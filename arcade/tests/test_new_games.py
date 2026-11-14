"""
Dolphin Dream and Sunset Shatter: their cabinet rows and the column and kill limits.
"""

from datetime import timedelta

from django.test import TestCase

from arcade import plausibility, podium
from arcade.models import ArcadeGame
from arcade.tests.factories import make_game, make_season


class SeededRowTests(TestCase):
    def test_dolphin_dream_is_seeded_live_with_its_limits(self):
        game = ArcadeGame.objects.get(slug='dolphin-dream')

        self.assertEqual(game.display_name, 'Dolphin Dream')
        self.assertTrue(game.is_active)
        self.assertEqual(
            game.plausibility,
            {'max_score_per_column': 2, 'min_ms_per_column': 700, 'max_level': 99},
        )

    def test_sunset_shatter_is_seeded_live_with_its_limits(self):
        game = ArcadeGame.objects.get(slug='sunset-shatter')

        self.assertEqual(game.display_name, 'Sunset Shatter')
        self.assertTrue(game.is_active)
        self.assertEqual(
            game.plausibility,
            {'max_score_per_kill': 5100, 'min_ms_per_kill': 100, 'max_level': 99},
        )

    def test_neither_row_sets_the_other_games_keys(self):
        dolphin = ArcadeGame.objects.get(slug='dolphin-dream')
        shatter = ArcadeGame.objects.get(slug='sunset-shatter')

        self.assertNotIn('max_score_per_kill', dolphin.plausibility)
        self.assertNotIn('max_score_per_column', shatter.plausibility)

    def test_each_counts_for_the_podium_only_from_a_season_that_starts_after_it(self):
        for slug in ('dolphin-dream', 'sunset-shatter'):
            with self.subTest(slug=slug):
                game = ArcadeGame.objects.get(slug=slug)
                running = make_season(game.active_from - timedelta(days=2))
                later = make_season(game.active_from + timedelta(days=5))

                self.assertFalse(game.counts_for_season(running))
                self.assertTrue(game.counts_for_season(later))
                self.assertNotIn(game, podium.counted_games(running))
                self.assertIn(game, podium.counted_games(later))


class ColumnPlausibilityTests(TestCase):
    def setUp(self):
        self.game = make_game('dolphin-dream', 'Dolphin Dream')
        self.game.plausibility = {'max_score_per_column': 2, 'min_ms_per_column': 700}
        self.game.save()

    def test_a_score_the_rules_can_produce_passes(self):
        stats = {'columns_passed': 40, 'duration_ms': 40 * 833}
        self.assertEqual(plausibility.check(self.game, 80, stats), [])

    def test_more_per_column_than_the_game_can_pay_is_refused(self):
        problems = plausibility.check(self.game, 81, {'columns_passed': 40, 'duration_ms': 60_000})
        self.assertEqual(len(problems), 1)
        self.assertIn('per column', problems[0])

    def test_a_run_with_no_column_count_cannot_be_checked_and_is_refused(self):
        problems = plausibility.check(self.game, 10, {'duration_ms': 60_000})
        self.assertIn('columns_passed is missing, so the score cannot be checked', problems)
        self.assertTrue(any('columns_passed or duration_ms is missing' in p for p in problems))

    def test_a_run_with_no_duration_cannot_have_its_pace_checked(self):
        problems = plausibility.check(self.game, 10, {'columns_passed': 10})
        self.assertEqual(
            problems, ['columns_passed or duration_ms is missing, so the pace cannot be checked']
        )

    def test_columns_passed_faster_than_the_pace_limit_are_refused(self):
        problems = plausibility.check(self.game, 20, {'columns_passed': 20, 'duration_ms': 10_000})
        self.assertEqual(len(problems), 1)
        self.assertIn('ms per column', problems[0])

    def test_every_problem_is_reported_together(self):
        problems = plausibility.check(self.game, 500, {'columns_passed': 20, 'duration_ms': 10_000})
        self.assertEqual(len(problems), 2)

    def test_a_run_with_no_columns_and_no_score_passes(self):
        self.assertEqual(plausibility.check(self.game, 0, {'columns_passed': 0, 'duration_ms': 0}), [])


class KillPlausibilityTests(TestCase):
    def setUp(self):
        self.game = make_game('sunset-shatter', 'Sunset Shatter')
        self.game.plausibility = {'max_score_per_kill': 5100, 'min_ms_per_kill': 100}
        self.game.save()

    def test_a_score_the_rules_can_produce_passes(self):
        stats = {'kills': 30, 'duration_ms': 60_000}
        self.assertEqual(plausibility.check(self.game, 30 * 5100, stats), [])

    def test_more_per_kill_than_the_game_can_pay_is_refused(self):
        problems = plausibility.check(self.game, 30 * 5100 + 1, {'kills': 30, 'duration_ms': 60_000})
        self.assertEqual(len(problems), 1)
        self.assertIn('per kill', problems[0])

    def test_a_run_with_no_kill_count_cannot_be_checked_and_is_refused(self):
        problems = plausibility.check(self.game, 100, {'duration_ms': 60_000})
        self.assertIn('kills is missing, so the score cannot be checked', problems)
        self.assertTrue(any('kills or duration_ms is missing' in p for p in problems))

    def test_a_run_with_no_duration_cannot_have_its_pace_checked(self):
        problems = plausibility.check(self.game, 100, {'kills': 10})
        self.assertEqual(problems, ['kills or duration_ms is missing, so the pace cannot be checked'])

    def test_kills_faster_than_the_pace_limit_are_refused(self):
        problems = plausibility.check(self.game, 100, {'kills': 100, 'duration_ms': 5_000})
        self.assertEqual(len(problems), 1)
        self.assertIn('ms per kill', problems[0])

    def test_a_run_with_no_kills_and_no_score_passes(self):
        self.assertEqual(plausibility.check(self.game, 0, {'kills': 0, 'duration_ms': 0}), [])


class UnsetLimitsTests(TestCase):
    def test_a_game_with_none_of_the_new_keys_accepts_anything(self):
        game = make_game('dolphin-dream', 'Dolphin Dream')
        game.plausibility = {}
        game.save()

        self.assertEqual(plausibility.check(game, 10**9, {}), [])

    def test_the_new_keys_are_listed(self):
        for key in ('max_score_per_column', 'min_ms_per_column', 'max_score_per_kill', 'min_ms_per_kill'):
            self.assertIn(key, plausibility.LIMIT_KEYS)
