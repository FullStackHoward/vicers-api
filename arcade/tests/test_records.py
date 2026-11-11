"""
Neon Breaker's cabinet row, its plausibility limits, and the per-game record roles.
"""

import os
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from arcade import plausibility, play, podium, records, rollover
from arcade.models import ArcadeEvent, ArcadeGame, EventType, SeasonStatus
from arcade.tests.factories import eastern, make_game, make_score, make_season, only_games
from arcade.tests.test_api import ApiMixin, bot_headers
from arcade.tests.test_play import ARCADE_ENV, HERE


class NeonBreakerRowTests(TestCase):
    def test_the_migration_seeds_neon_breaker_live_with_its_limits(self):
        game = ArcadeGame.objects.get(slug='neon-breaker')

        self.assertEqual(game.display_name, 'Neon Breaker')
        self.assertTrue(game.is_active)
        self.assertEqual(game.plausibility['max_score_per_brick'], 1400)
        self.assertNotIn('min_ms_per_brick', game.plausibility)

    def test_it_counts_for_the_champion_roles_only_from_a_season_that_starts_after_it(self):
        game = ArcadeGame.objects.get(slug='neon-breaker')

        running = make_season(game.active_from - timedelta(days=2))
        later = make_season(game.active_from + timedelta(days=5))

        self.assertNotIn(game, podium.counted_games(running))
        self.assertIn(game, podium.counted_games(later))


class BrickPlausibilityTests(TestCase):
    def setUp(self):
        self.game = make_game('neon-breaker', 'Neon Breaker')
        self.game.plausibility = {'max_score_per_brick': 1400, 'min_ms_per_brick': 40}
        self.game.save()

    def test_a_score_the_rules_can_produce_passes(self):
        stats = {'bricks_broken': 50, 'duration_ms': 90_000}
        self.assertEqual(plausibility.check(self.game, 50 * 1400, stats), [])

    def test_more_per_brick_than_the_game_can_pay_is_refused(self):
        problems = plausibility.check(self.game, 50 * 1400 + 1, {'bricks_broken': 50, 'duration_ms': 90_000})
        self.assertEqual(len(problems), 1)
        self.assertIn('per brick', problems[0])

    def test_a_run_with_no_brick_count_cannot_be_checked_and_is_refused(self):
        problems = plausibility.check(self.game, 500, {'duration_ms': 90_000})
        self.assertTrue(any('bricks_broken is missing' in problem for problem in problems))

    def test_bricks_broken_faster_than_the_pace_limit_are_refused(self):
        problems = plausibility.check(self.game, 100, {'bricks_broken': 100, 'duration_ms': 3_000})
        self.assertTrue(any('ms per brick' in problem for problem in problems))

    def test_vetrix_limits_never_ask_for_bricks(self):
        vetrix = make_game()
        vetrix.plausibility = {'min_ms_per_piece': 150}
        vetrix.save()

        self.assertEqual(plausibility.check(vetrix, 500, {'pieces_placed': 10, 'duration_ms': 10_000}), [])


class CurrentRecordsTests(TestCase):
    def setUp(self):
        self.vetrix = make_game()
        self.breaker = make_game('neon-breaker', 'Neon Breaker')
        only_games(self.vetrix, self.breaker)
        self.vetrix.record_role_id = '111'
        self.vetrix.save()

    def test_each_game_has_its_own_holder_and_role(self):
        now = timezone.now()
        make_score(self.vetrix, 'a', 900, now - timedelta(days=3), name='Ace')
        make_score(self.vetrix, 'b', 400, now - timedelta(hours=1))
        make_score(self.breaker, 'b', 7000, now - timedelta(hours=2), name='Bee')

        by_slug = {row['game_slug']: row for row in records.current_records()}

        self.assertEqual(by_slug['vetrix']['role_id'], '111')
        self.assertEqual(by_slug['vetrix']['holder']['discord_user_id'], 'a')
        self.assertEqual(by_slug['vetrix']['holder']['score'], 900)
        self.assertIsNone(by_slug['neon-breaker']['role_id'], 'no role set yet')
        self.assertEqual(by_slug['neon-breaker']['holder']['display_name'], 'Bee')

    def test_a_removed_record_hands_the_role_to_the_next_best(self):
        now = timezone.now()
        top = make_score(self.vetrix, 'a', 900, now - timedelta(days=3))
        make_score(self.vetrix, 'b', 400, now - timedelta(hours=1))

        play.remove_score(top)

        vetrix = next(row for row in records.current_records() if row['game_slug'] == 'vetrix')
        self.assertEqual(vetrix['holder']['discord_user_id'], 'b')

    def test_a_game_nobody_has_played_has_no_holder(self):
        breaker = next(row for row in records.current_records() if row['game_slug'] == 'neon-breaker')
        self.assertIsNone(breaker['holder'])

    def test_a_switched_off_game_has_no_holder_so_its_role_comes_off(self):
        make_score(self.vetrix, 'a', 900, timezone.now() - timedelta(days=1))
        self.vetrix.is_active = False
        self.vetrix.save()

        vetrix = next(row for row in records.current_records() if row['game_slug'] == 'vetrix')
        self.assertEqual(vetrix['role_id'], '111')
        self.assertIsNone(vetrix['holder'])


class RecordsEndpointTests(ApiMixin, TestCase):
    def test_the_bot_reads_every_game_record(self):
        response = self.client.get('/api/arcade/records/current', **bot_headers())

        self.assertEqual(response.status_code, 200)
        slugs = [row['game_slug'] for row in response.json()['records']]
        self.assertIn('vetrix', slugs)
        self.assertIn('neon-breaker', slugs)

    def test_it_is_for_the_bot_only(self):
        response = self.client.get('/api/arcade/records/current')
        self.assertIn(response.status_code, (401, 403))


class RecordEventTests(TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, ARCADE_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.now = timezone.now()
        make_season(self.now - timedelta(hours=3))
        self.game = make_game()
        self.game.record_role_id = '555'
        self.game.save()

    def test_a_new_record_names_the_game_record_role(self):
        make_score(self.game, 'holder', 400, self.now - timedelta(hours=1))
        session, token = play.issue_session('challenger', self.game, HERE, 'inst', self.now)

        play.submit_score(token, 'challenger', 'Challenger', 900, {'seed': session.seed}, now=self.now)

        event = ArcadeEvent.objects.get(type=EventType.NEW_ALLTIME_RECORD)
        self.assertEqual(event.payload['record_role_id'], '555')
        self.assertEqual(event.payload['discord_user_id'], 'challenger')


class SeasonAwardGameCountTests(TestCase):
    def test_the_callout_says_how_many_games_the_placings_were_averaged_over(self):
        vetrix = make_game()
        breaker = make_game('neon-breaker', 'Neon Breaker', active_from=eastern(2020, 1, 1))
        only_games(vetrix, breaker)
        season = make_season(eastern(2026, 11, 8, 17), status=SeasonStatus.CLOSING)

        event = rollover.finalize(season, [
            {'discord_user_id': '1', 'display_name': 'one', 'average_placement': 1.5, 'breakdown': {}},
        ])

        self.assertEqual(event.payload['games_counted'], 2)
