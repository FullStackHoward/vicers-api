"""
Pixel Python and Dewdrop Defense: their cabinet rows and the generic unit_limits key.
"""

import logging
from datetime import timedelta

from django.test import TestCase

from arcade import plausibility, podium
from arcade.models import ArcadeGame
from arcade.tests.factories import make_game, make_season

FOODS = {'stat': 'foods_eaten', 'label': 'foods', 'max_score_each': 210, 'min_ms_each': 180}


class SeededRowTests(TestCase):
    def test_pixel_python_is_seeded_live_with_its_limits(self):
        game = ArcadeGame.objects.get(slug='pixel-python')

        self.assertEqual(game.display_name, 'Pixel Python')
        self.assertTrue(game.is_active)
        self.assertEqual(game.plausibility, {'max_level': 99, 'unit_limits': [FOODS]})

    def test_dewdrop_defense_is_seeded_live_with_its_limits(self):
        game = ArcadeGame.objects.get(slug='dewdrop-defense')

        self.assertEqual(game.display_name, 'Dewdrop Defense')
        self.assertTrue(game.is_active)
        self.assertEqual(
            game.plausibility,
            {'max_level': 99, 'max_score_per_kill': 1300, 'min_ms_per_kill': 120},
        )

    def test_neither_row_sets_the_other_games_keys(self):
        python = ArcadeGame.objects.get(slug='pixel-python')
        dewdrop = ArcadeGame.objects.get(slug='dewdrop-defense')

        self.assertNotIn('max_score_per_kill', python.plausibility)
        self.assertNotIn('min_ms_per_kill', python.plausibility)
        self.assertNotIn('unit_limits', dewdrop.plausibility)

    def test_each_counts_for_the_podium_only_from_a_season_that_starts_after_it(self):
        for slug in ('pixel-python', 'dewdrop-defense'):
            with self.subTest(slug=slug):
                game = ArcadeGame.objects.get(slug=slug)
                running = make_season(game.active_from - timedelta(days=2))
                later = make_season(game.active_from + timedelta(days=5))

                self.assertFalse(game.counts_for_season(running))
                self.assertTrue(game.counts_for_season(later))
                self.assertNotIn(game, podium.counted_games(running))
                self.assertIn(game, podium.counted_games(later))


class DewdropKillLimitTests(TestCase):
    def test_the_seeded_limits_accept_a_plausible_run_and_refuse_an_impossible_one(self):
        game = ArcadeGame.objects.get(slug='dewdrop-defense')

        self.assertEqual(plausibility.check(game, 30 * 1300, {'kills': 30, 'duration_ms': 60_000}), [])
        problems = plausibility.check(game, 30 * 1300 + 1, {'kills': 30, 'duration_ms': 3_000})
        self.assertEqual(len(problems), 2)


class UnitLimitTests(TestCase):
    def setUp(self):
        self.game = make_game('pixel-python', 'Pixel Python')
        self.game.plausibility = {'unit_limits': [dict(FOODS)]}
        self.game.save()

    def test_a_legitimate_run_passes(self):
        self.assertEqual(plausibility.check(self.game, 40 * 210, {'foods_eaten': 40, 'duration_ms': 40 * 500}), [])

    def test_a_score_over_the_per_unit_limit_is_refused(self):
        problems = plausibility.check(self.game, 40 * 210 + 1, {'foods_eaten': 40, 'duration_ms': 60_000})
        self.assertEqual(problems, ['score 8401 is more than 210 per foods over 40 foods'])

    def test_a_run_with_no_count_is_refused_with_the_missing_stat_message(self):
        problems = plausibility.check(self.game, 10, {'duration_ms': 60_000})
        self.assertIn('foods_eaten is missing, so the score cannot be checked', problems)

    def test_a_run_with_no_duration_cannot_have_its_pace_checked(self):
        problems = plausibility.check(self.game, 10, {'foods_eaten': 10})
        self.assertEqual(problems, ['foods_eaten or duration_ms is missing, so the pace cannot be checked'])

    def test_a_run_faster_than_the_pace_limit_is_refused(self):
        problems = plausibility.check(self.game, 100, {'foods_eaten': 100, 'duration_ms': 10_000})
        self.assertEqual(problems, ['100 foods in 10000 ms is faster than 180 ms per foods'])

    def test_every_problem_is_reported_together(self):
        problems = plausibility.check(self.game, 10_000, {'foods_eaten': 20, 'duration_ms': 1_000})
        self.assertEqual(len(problems), 2)

    def test_missing_count_and_duration_report_both_problems(self):
        self.assertEqual(len(plausibility.check(self.game, 10, {})), 2)

    def test_a_zero_food_run_with_no_score_passes(self):
        self.assertEqual(plausibility.check(self.game, 0, {'foods_eaten': 0, 'duration_ms': 0}), [])

    def test_the_label_defaults_to_the_stat(self):
        self.game.plausibility = {'unit_limits': [{'stat': 'gems', 'max_score_each': 5}]}
        problems = plausibility.check(self.game, 100, {'gems': 10})
        self.assertEqual(problems, ['score 100 is more than 5 per gems over 10 gems'])

    def test_a_limit_that_is_absent_is_not_checked(self):
        self.game.plausibility = {'unit_limits': [{'stat': 'gems', 'max_score_each': 5}]}
        self.assertEqual(plausibility.check(self.game, 10, {'gems': 10}), [])


class MalformedEntryTests(TestCase):
    def setUp(self):
        self.game = make_game('pixel-python', 'Pixel Python')

    def check(self, entries, score, stats):
        self.game.plausibility = {'unit_limits': entries}
        self.game.save()
        return plausibility.check(self.game, score, stats)

    def test_malformed_entries_are_skipped_and_valid_ones_still_judge_the_run(self):
        entries = [
            'foods_eaten',
            {'label': 'no stat', 'max_score_each': 1},
            {'stat': 'foods_eaten', 'max_score_each': 'lots'},
            {'stat': 'foods_eaten', 'min_ms_each': True},
            {'stat': 7, 'max_score_each': 1},
            dict(FOODS),
        ]
        with self.assertLogs('arcade.plausibility', level=logging.WARNING) as logs:
            problems = self.check(entries, 40 * 210 + 1, {'foods_eaten': 40, 'duration_ms': 60_000})

        self.assertEqual(len(problems), 1)
        self.assertIn('per foods', problems[0])
        self.assertEqual(len(logs.records), 1, 'one warning per call')
        self.assertIn('pixel-python', logs.output[0])

    def test_a_run_that_is_fine_passes_despite_malformed_entries(self):
        with self.assertLogs('arcade.plausibility', level=logging.WARNING):
            self.assertEqual(self.check(['junk', 3, None], 10, {}), [])

    def test_a_value_that_is_not_a_list_is_skipped_not_raised(self):
        with self.assertLogs('arcade.plausibility', level=logging.WARNING):
            self.assertEqual(self.check('foods', 10, {}), [])

    def test_well_formed_entries_log_nothing(self):
        with self.assertNoLogs('arcade.plausibility', level=logging.WARNING):
            self.check([dict(FOODS)], 100, {'foods_eaten': 10, 'duration_ms': 60_000})


class TwoEntryTests(TestCase):
    def test_both_entries_are_enforced(self):
        game = make_game('pixel-python', 'Pixel Python')
        game.plausibility = {
            'unit_limits': [
                {'stat': 'foods_eaten', 'label': 'foods', 'max_score_each': 10},
                {'stat': 'gems', 'label': 'gems', 'max_score_each': 100},
            ]
        }
        game.save()

        problems = plausibility.check(game, 5000, {'foods_eaten': 10, 'gems': 10})
        self.assertEqual(len(problems), 2)
        self.assertTrue(any('per foods' in p for p in problems))
        self.assertTrue(any('per gems' in p for p in problems))

        self.assertEqual(plausibility.check(game, 100, {'foods_eaten': 10, 'gems': 10}), [])


class UnsetLimitsTests(TestCase):
    def test_a_game_with_no_unit_limits_key_accepts_anything(self):
        game = make_game('pixel-python', 'Pixel Python')
        game.plausibility = {}
        game.save()

        self.assertEqual(plausibility.check(game, 10**9, {}), [])

    def test_unit_limits_is_listed(self):
        self.assertIn('unit_limits', plausibility.LIMIT_KEYS)
