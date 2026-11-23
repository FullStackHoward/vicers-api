"""
Sandbar Sweep: its cabinet row and its cells and digs limits.
"""

from datetime import timedelta

from django.test import TestCase

from arcade import plausibility, podium
from arcade.models import ArcadeGame
from arcade.tests.factories import make_season

CELLS = {'stat': 'cells_dug', 'label': 'cells', 'max_score_each': 30}
DIGS = {'stat': 'digs', 'label': 'digs', 'min_ms_each': 60}


class SeededRowTests(TestCase):
    def setUp(self):
        self.game = ArcadeGame.objects.get(slug='sandbar-sweep')

    def test_it_is_seeded_live_with_its_limits(self):
        self.assertEqual(self.game.display_name, 'Sandbar Sweep')
        self.assertTrue(self.game.is_active)
        self.assertEqual(self.game.plausibility, {'max_level': 99, 'unit_limits': [CELLS, DIGS]})

    def test_it_sets_none_of_the_other_games_keys(self):
        self.assertNotIn('max_score_per_kill', self.game.plausibility)
        self.assertNotIn('min_ms_per_kill', self.game.plausibility)

        entries = self.game.plausibility['unit_limits']
        self.assertEqual(len(entries), 2)
        for entry in entries:
            self.assertNotIn(entry['stat'], ('foods_eaten', 'dots_eaten', 'hops', 'kills', 'shots'))

    def test_it_counts_for_the_podium_only_from_a_season_that_starts_after_it(self):
        running = make_season(self.game.active_from - timedelta(days=2))
        later = make_season(self.game.active_from + timedelta(days=5))

        self.assertFalse(self.game.counts_for_season(running))
        self.assertTrue(self.game.counts_for_season(later))
        self.assertNotIn(self.game, podium.counted_games(running))
        self.assertIn(self.game, podium.counted_games(later))


class CellAndDigLimitTests(TestCase):
    def setUp(self):
        self.game = ArcadeGame.objects.get(slug='sandbar-sweep')

    def test_a_plausible_run_passes(self):
        stats = {'cells_dug': 100, 'digs': 100, 'duration_ms': 6_000}
        self.assertEqual(plausibility.check(self.game, 3_000, stats), [])

    def test_an_impossible_run_is_refused_for_score_and_pace_in_that_order(self):
        stats = {'cells_dug': 100, 'digs': 100, 'duration_ms': 5_999}
        problems = plausibility.check(self.game, 3_001, stats)

        self.assertEqual(
            problems,
            [
                'score 3001 is more than 30 per cells over 100 cells',
                '100 digs in 5999 ms is faster than 60 ms per digs',
            ],
        )

    def test_a_run_with_no_counts_is_refused_with_the_missing_stat_messages(self):
        problems = plausibility.check(self.game, 10, {'duration_ms': 60_000})

        self.assertIn('cells_dug is missing, so the score cannot be checked', problems)
        self.assertIn('digs or duration_ms is missing, so the pace cannot be checked', problems)

    def test_a_run_that_opened_nothing_and_scored_nothing_passes(self):
        stats = {'cells_dug': 0, 'digs': 0, 'duration_ms': 0}
        self.assertEqual(plausibility.check(self.game, 0, stats), [])

    def test_a_score_with_no_cell_is_refused(self):
        stats = {'cells_dug': 0, 'digs': 0, 'duration_ms': 5_000}
        problems = plausibility.check(self.game, 10, stats)

        self.assertEqual(problems, ['score 10 is more than 30 per cells over 0 cells'])
