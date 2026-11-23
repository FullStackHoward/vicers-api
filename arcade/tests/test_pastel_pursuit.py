"""
Pastel Pursuit: its cabinet row and its hops limits.
"""

from datetime import timedelta

from django.test import TestCase

from arcade import plausibility, podium
from arcade.models import ArcadeGame
from arcade.tests.factories import make_season

HOPS = {'stat': 'hops', 'label': 'hops', 'max_score_each': 70, 'min_ms_each': 90}


class SeededRowTests(TestCase):
    def setUp(self):
        self.game = ArcadeGame.objects.get(slug='pastel-pursuit')

    def test_it_is_seeded_live_with_its_limits(self):
        self.assertEqual(self.game.display_name, 'Pastel Pursuit')
        self.assertTrue(self.game.is_active)
        self.assertEqual(self.game.plausibility, {'max_level': 99, 'unit_limits': [HOPS]})

    def test_it_sets_none_of_the_other_games_keys(self):
        self.assertNotIn('max_score_per_kill', self.game.plausibility)
        self.assertNotIn('min_ms_per_kill', self.game.plausibility)

        entries = self.game.plausibility['unit_limits']
        self.assertEqual(len(entries), 1)
        self.assertNotIn(entries[0]['stat'], ('foods_eaten', 'dots_eaten'))

    def test_it_counts_for_the_podium_only_from_a_season_that_starts_after_it(self):
        running = make_season(self.game.active_from - timedelta(days=2))
        later = make_season(self.game.active_from + timedelta(days=5))

        self.assertFalse(self.game.counts_for_season(running))
        self.assertTrue(self.game.counts_for_season(later))
        self.assertNotIn(self.game, podium.counted_games(running))
        self.assertIn(self.game, podium.counted_games(later))


class HopLimitTests(TestCase):
    def setUp(self):
        self.game = ArcadeGame.objects.get(slug='pastel-pursuit')

    def test_a_plausible_run_passes(self):
        self.assertEqual(plausibility.check(self.game, 7_000, {'hops': 100, 'duration_ms': 10_000}), [])

    def test_an_impossible_run_is_refused_for_score_and_pace_in_that_order(self):
        problems = plausibility.check(self.game, 7_001, {'hops': 100, 'duration_ms': 8_999})

        self.assertEqual(
            problems,
            [
                'score 7001 is more than 70 per hops over 100 hops',
                '100 hops in 8999 ms is faster than 90 ms per hops',
            ],
        )

    def test_a_run_with_no_hops_count_is_refused_with_the_missing_stat_message(self):
        problems = plausibility.check(self.game, 10, {'duration_ms': 60_000})

        self.assertIn('hops is missing, so the score cannot be checked', problems)

    def test_a_run_that_moved_nothing_and_scored_nothing_passes(self):
        self.assertEqual(plausibility.check(self.game, 0, {'hops': 0, 'duration_ms': 0}), [])

    def test_a_run_that_scored_without_hopping_is_refused(self):
        problems = plausibility.check(self.game, 10, {'hops': 0, 'duration_ms': 5_000})

        self.assertEqual(problems, ['score 10 is more than 70 per hops over 0 hops'])
