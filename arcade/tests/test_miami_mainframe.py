"""
Miami Mainframe: its cabinet row and its dots_eaten limits.
"""

from datetime import timedelta

from django.test import TestCase

from arcade import plausibility, podium
from arcade.models import ArcadeGame
from arcade.tests.factories import make_season

DOTS = {'stat': 'dots_eaten', 'label': 'dots', 'max_score_each': 300, 'min_ms_each': 100}


class SeededRowTests(TestCase):
    def setUp(self):
        self.game = ArcadeGame.objects.get(slug='miami-mainframe')

    def test_it_is_seeded_live_with_its_limits(self):
        self.assertEqual(self.game.display_name, 'Miami Mainframe')
        self.assertTrue(self.game.is_active)
        self.assertEqual(self.game.plausibility, {'max_level': 99, 'unit_limits': [DOTS]})

    def test_it_sets_none_of_the_other_games_keys(self):
        for key in ('max_score_per_kill', 'min_ms_per_kill', 'max_score_per_piece', 'max_score_per_brick'):
            self.assertNotIn(key, self.game.plausibility)

        entries = self.game.plausibility['unit_limits']
        self.assertEqual(len(entries), 1)
        self.assertNotEqual(entries[0]['stat'], 'foods_eaten')

    def test_it_counts_for_the_podium_only_from_a_season_that_starts_after_it(self):
        running = make_season(self.game.active_from - timedelta(days=2))
        later = make_season(self.game.active_from + timedelta(days=5))

        self.assertFalse(self.game.counts_for_season(running))
        self.assertTrue(self.game.counts_for_season(later))
        self.assertNotIn(self.game, podium.counted_games(running))
        self.assertIn(self.game, podium.counted_games(later))


class DotLimitTests(TestCase):
    def setUp(self):
        self.game = ArcadeGame.objects.get(slug='miami-mainframe')

    def test_a_plausible_run_passes(self):
        self.assertEqual(plausibility.check(self.game, 30_000, {'dots_eaten': 100, 'duration_ms': 10_000}), [])

    def test_an_impossible_run_is_refused_for_score_and_pace_in_that_order(self):
        problems = plausibility.check(self.game, 30_001, {'dots_eaten': 100, 'duration_ms': 9_000})

        self.assertEqual(
            problems,
            [
                'score 30001 is more than 300 per dots over 100 dots',
                '100 dots in 9000 ms is faster than 100 ms per dots',
            ],
        )

    def test_a_run_with_no_dot_count_is_refused_with_the_missing_stat_message(self):
        problems = plausibility.check(self.game, 10, {'duration_ms': 60_000})

        self.assertEqual(
            problems,
            [
                'dots_eaten is missing, so the score cannot be checked',
                'dots_eaten or duration_ms is missing, so the pace cannot be checked',
            ],
        )
