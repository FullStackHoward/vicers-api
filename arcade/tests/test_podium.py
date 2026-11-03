from datetime import timedelta

from django.test import TestCase

from arcade import leaderboards, podium
from arcade.models import ArcadeEvent, EventType
from arcade.tests.factories import eastern, make_game, make_score, make_season, only_games


class SingleGameTests(TestCase):
    def setUp(self):
        self.season = make_season(eastern(2026, 11, 8, 17))
        self.game = make_game()
        self.moment = self.season.starts_at + timedelta(hours=1)

    def test_with_one_game_live_the_standings_are_just_that_game_rank(self):
        make_score(self.game, 'a', 300, self.moment)
        make_score(self.game, 'b', 200, self.moment)
        make_score(self.game, 'c', 100, self.moment)

        rows = podium.standings(self.season)

        self.assertEqual([r['discord_user_id'] for r in rows], ['a', 'b', 'c'])
        self.assertEqual([r['place'] for r in rows], [1, 2, 3])
        self.assertEqual([r['average_placement'] for r in rows], [1.0, 2.0, 3.0])

    def test_a_player_is_ranked_on_their_best_score_not_their_latest(self):
        make_score(self.game, 'a', 500, self.moment)
        make_score(self.game, 'a', 100, self.moment + timedelta(minutes=5))
        make_score(self.game, 'b', 300, self.moment)

        rows = podium.standings(self.season)

        self.assertEqual([r['discord_user_id'] for r in rows], ['a', 'b'])

    def test_equal_scores_go_to_whoever_got_there_first(self):
        make_score(self.game, 'late', 400, self.moment + timedelta(minutes=10))
        make_score(self.game, 'early', 400, self.moment)

        rows = podium.standings(self.season)

        self.assertEqual([r['discord_user_id'] for r in rows], ['early', 'late'])

    def test_a_score_pending_review_shows_on_the_board_but_moves_no_role(self):
        make_score(self.game, 'a', 100, self.moment)
        make_score(self.game, 'cheat', 999, self.moment, approved=False, flagged=True)

        board = leaderboards.season_rows(self.game, self.season)
        standings = podium.standings(self.season)

        self.assertEqual(board[0]['discord_user_id'], 'cheat')
        self.assertTrue(board[0]['pending'])
        self.assertEqual([r['discord_user_id'] for r in standings], ['a'])

    def test_approving_a_pending_record_then_moves_the_role(self):
        make_score(self.game, 'a', 100, self.moment)
        record = make_score(self.game, 'record', 999, self.moment, approved=False, flagged=True)

        record.approved = True
        record.save(update_fields=['approved'])

        self.assertEqual(
            [r['discord_user_id'] for r in podium.standings(self.season)],
            ['record', 'a'],
        )

    def test_a_removed_score_counts_for_nothing(self):
        make_score(self.game, 'a', 100, self.moment)
        make_score(self.game, 'gone', 999, self.moment, removed=True, removal_reason='member_left')

        self.assertEqual(
            [r['discord_user_id'] for r in podium.standings(self.season)],
            ['a'],
        )

    def test_a_score_from_another_season_does_not_count(self):
        make_score(self.game, 'a', 100, self.moment)
        make_score(self.game, 'past', 999, self.season.starts_at - timedelta(days=1))

        self.assertEqual(
            [r['discord_user_id'] for r in podium.standings(self.season)],
            ['a'],
        )

    def test_fewer_than_three_players_gives_fewer_than_three_places(self):
        make_score(self.game, 'a', 100, self.moment)
        self.assertEqual(len(podium.top_three(self.season)), 1)

    def test_a_season_nobody_played_has_no_standings(self):
        self.assertEqual(podium.standings(self.season), [])


class CountedGameTests(TestCase):
    def setUp(self):
        self.season = make_season(eastern(2026, 11, 8, 17))
        self.moment = self.season.starts_at + timedelta(hours=1)

    def test_a_cabinet_added_mid_season_does_not_count_until_the_next_one(self):
        old = make_game(slug='vetrix')
        new = make_game(
            slug='newcomer',
            display_name='Newcomer',
            active_from=self.season.starts_at + timedelta(days=1),
        )

        counted = podium.counted_games(self.season)

        self.assertIn(old, counted)
        self.assertNotIn(new, counted)

    def test_a_locked_cabinet_never_counts(self):
        make_game(slug='vetrix')
        locked = make_game(slug='locked', display_name='Locked', is_active=False)

        self.assertNotIn(locked, podium.counted_games(self.season))

    def test_with_no_counted_games_there_are_no_standings(self):
        # only_games drops the seeded Vetrix row, so this really does run with
        # nothing counted rather than merely with nothing scored.
        later = make_game(slug='later', active_from=self.season.starts_at + timedelta(days=1))
        only_games(later)
        make_score(later, 'a', 500, self.moment)

        self.assertEqual(podium.counted_games(self.season), [])
        self.assertEqual(podium.standings(self.season), [])


class AveragePlacementTests(TestCase):
    def setUp(self):
        self.season = make_season(eastern(2026, 11, 8, 17))
        self.first = make_game(slug='vetrix', display_name='Vetrix')
        self.second = make_game(slug='other', display_name='Other')
        self.moment = self.season.starts_at + timedelta(hours=1)

    def test_a_game_not_played_is_scored_at_the_cap(self):
        make_score(self.first, 'a', 500, self.moment)

        rows = podium.standings(self.season)
        row = rows[0]

        self.assertEqual(row['breakdown'], {'vetrix': 1, 'other': leaderboards.PLACEMENT_CAP})
        self.assertEqual(row['average_placement'], (1 + leaderboards.PLACEMENT_CAP) / 2)

    def test_playing_everything_moderately_beats_winning_one_and_skipping_the_other(self):
        # Spec's own example: with all games counted, the all-rounder wins.
        make_score(self.first, 'specialist', 900, self.moment)
        make_score(self.first, 'allrounder', 100, self.moment)
        make_score(self.second, 'allrounder', 100, self.moment)

        rows = podium.standings(self.season)

        self.assertEqual(rows[0]['discord_user_id'], 'allrounder')
        self.assertEqual(rows[0]['average_placement'], 1.5)
        self.assertEqual(rows[1]['average_placement'], (1 + leaderboards.PLACEMENT_CAP) / 2)

    def test_a_tie_on_average_goes_to_more_games_played(self):
        # Both average 6.0, by different routes:
        #   one_game  is 1st on Vetrix and absent from Other, so 1 and the cap.
        #   two_games is 1st on Other and 11th on Vetrix, so the cap and 1.
        # Nine fillers sit between them on Vetrix to push two_games past the top ten.
        make_score(self.first, 'one_game', 10000, self.moment)
        for index in range(9):
            make_score(self.first, f'filler{index}', 1000 + index, self.moment)
        make_score(self.first, 'two_games', 1, self.moment)
        make_score(self.second, 'two_games', 500, self.moment)

        rows = {r['discord_user_id']: r for r in podium.standings(self.season)}
        cap = leaderboards.PLACEMENT_CAP

        self.assertEqual(rows['one_game']['breakdown'], {'vetrix': 1, 'other': cap})
        self.assertEqual(rows['two_games']['breakdown'], {'vetrix': cap, 'other': 1})
        self.assertEqual(rows['one_game']['average_placement'], 6.0)
        self.assertEqual(rows['two_games']['average_placement'], 6.0)

        self.assertEqual(rows['two_games']['games_played'], 2)
        self.assertEqual(rows['one_game']['games_played'], 1)
        self.assertLess(rows['two_games']['place'], rows['one_game']['place'])

    def test_rank_past_the_top_ten_is_capped(self):
        for index in range(12):
            make_score(self.first, f'p{index}', 1000 - index, self.moment)

        rows = {r['discord_user_id']: r for r in podium.standings(self.season)}

        self.assertEqual(rows['p10']['breakdown']['vetrix'], leaderboards.PLACEMENT_CAP)
        self.assertEqual(rows['p11']['breakdown']['vetrix'], leaderboards.PLACEMENT_CAP)
        self.assertEqual(rows['p9']['breakdown']['vetrix'], 10)


class RefreshTests(TestCase):
    def setUp(self):
        self.season = make_season(eastern(2026, 11, 8, 17))
        self.game = make_game()
        self.moment = self.season.starts_at + timedelta(hours=1)

    def changed_events(self):
        return ArcadeEvent.objects.filter(type=EventType.PODIUM_CHANGED).count()

    def test_a_new_score_entering_the_top_three_writes_one_event(self):
        make_score(self.game, 'a', 100, self.moment)

        event = podium.refresh(self.season)

        self.assertIsNotNone(event)
        self.assertEqual(event.type, EventType.PODIUM_CHANGED)
        self.assertEqual(event.payload['top'][0]['discord_user_id'], 'a')

    def test_nothing_changing_writes_nothing(self):
        make_score(self.game, 'a', 100, self.moment)
        podium.refresh(self.season)

        self.assertIsNone(podium.refresh(self.season))
        self.assertEqual(self.changed_events(), 1)

    def test_a_score_that_does_not_disturb_the_top_three_is_silent(self):
        for name, value in (('a', 500), ('b', 400), ('c', 300)):
            make_score(self.game, name, value, self.moment)
        podium.refresh(self.season)

        make_score(self.game, 'd', 10, self.moment + timedelta(minutes=1))

        self.assertIsNone(podium.refresh(self.season))
        self.assertEqual(self.changed_events(), 1)

    def test_a_better_score_further_down_the_top_three_does_write_an_event(self):
        for name, value in (('a', 500), ('b', 400), ('c', 300)):
            make_score(self.game, name, value, self.moment)
        podium.refresh(self.season)

        make_score(self.game, 'd', 450, self.moment + timedelta(minutes=1))
        event = podium.refresh(self.season)

        self.assertIsNotNone(event)
        self.assertEqual(
            [w['discord_user_id'] for w in event.payload['top']],
            ['a', 'd', 'b'],
        )

    def test_a_burst_of_scores_produces_one_role_call_not_one_each(self):
        for index in range(8):
            make_score(self.game, f'p{index}', 100 + index, self.moment + timedelta(seconds=index))

        podium.refresh(self.season)

        self.assertEqual(self.changed_events(), 1)

    def test_an_average_shifting_without_reordering_is_not_a_change(self):
        second = make_game(slug='other', display_name='Other')
        make_score(self.game, 'a', 500, self.moment)
        make_score(self.game, 'b', 100, self.moment)
        podium.refresh(self.season)
        before = self.changed_events()

        # b now plays the second game too. Averages move, the order does not.
        make_score(second, 'b', 50, self.moment + timedelta(minutes=1))
        make_score(second, 'a', 900, self.moment + timedelta(minutes=2))

        podium.refresh(self.season)

        self.assertEqual(self.changed_events(), before)

    def test_the_snapshot_lives_on_the_season_so_a_reset_clears_it(self):
        make_score(self.game, 'a', 100, self.moment)
        podium.refresh(self.season)
        self.season.refresh_from_db()

        self.assertEqual(
            self.season.live_top,
            [{'place': 1, 'discord_user_id': 'a'}],
        )

        fresh = make_season(self.season.ends_at)
        self.assertEqual(fresh.live_top, [])
        self.assertEqual(podium.current_holders(fresh), [])
