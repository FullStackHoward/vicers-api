"""
Eastern calendar arithmetic, including every daylight saving case.

Reference dates used throughout, for America/New_York:
  2026-03-08  clocks go forward, 2 AM to 3 AM, EST to EDT
  2026-11-01  clocks go back, 2 AM to 1 AM, EDT to EST
  2027-03-14  clocks go forward
"""

from datetime import date, datetime, timedelta, timezone

from django.test import SimpleTestCase

from arcade import seasons


def eastern(year, month, day, hour=0, minute=0):
    return seasons.localize(date(year, month, day), hour, minute)


def hours_between(start, end):
    # Both are UTC, because that is all seasons.localize returns. Comparing two
    # Eastern-tagged values here would silently measure wall clocks and miss the
    # daylight saving hour, which is the whole point of these tests.
    return (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds() / 3600


class WallClockArithmeticTests(SimpleTestCase):
    def test_seven_days_inside_one_offset_is_168_hours(self):
        start = eastern(2026, 11, 8, 17)
        end = seasons.add_days_same_wall_clock(start, 7)

        self.assertEqual(hours_between(start, end), 168)
        self.assertEqual(seasons.to_eastern(end).hour, 17)

    def test_seven_days_across_the_autumn_change_is_169_hours_at_the_same_clock(self):
        start = eastern(2026, 10, 25, 17)
        end = seasons.add_days_same_wall_clock(start, 7)

        self.assertEqual(seasons.eastern_date(end), date(2026, 11, 1))
        self.assertEqual(seasons.to_eastern(end).hour, 17)
        # The span really is an hour longer. A fixed 168 hours would land at 4 PM.
        self.assertEqual(hours_between(start, end), 169)

    def test_seven_days_across_the_spring_change_is_167_hours_at_the_same_clock(self):
        start = eastern(2026, 3, 1, 17)
        end = seasons.add_days_same_wall_clock(start, 7)

        self.assertEqual(seasons.eastern_date(end), date(2026, 3, 8))
        self.assertEqual(seasons.to_eastern(end).hour, 17)
        self.assertEqual(hours_between(start, end), 167)

    def test_fourteen_days_keeps_the_weekday_and_the_clock(self):
        start = eastern(2026, 10, 25, 17)
        end = seasons.add_days_same_wall_clock(start, 14)

        self.assertEqual(seasons.eastern_date(end), date(2026, 11, 8))
        self.assertEqual(seasons.to_eastern(end).weekday(), seasons.SUNDAY)
        self.assertEqual(seasons.to_eastern(end).hour, 17)

    def test_a_non_multiple_of_seven_moves_the_weekday(self):
        start = eastern(2026, 11, 8, 17)
        end = seasons.add_days_same_wall_clock(start, 10)

        self.assertEqual(seasons.eastern_date(end), date(2026, 11, 18))
        self.assertEqual(seasons.to_eastern(end).hour, 17)

    def test_five_pm_is_never_a_nonexistent_local_time(self):
        for day in (date(2026, 3, 8), date(2026, 11, 1), date(2027, 3, 14)):
            self.assertFalse(seasons.is_nonexistent(day))


class AwkwardLocalTimeTests(SimpleTestCase):
    def test_the_skipped_hour_resolves_to_a_real_instant(self):
        self.assertTrue(seasons.is_nonexistent(date(2026, 3, 8), 2, 30))

        moment = seasons.localize(date(2026, 3, 8), 2, 30)
        shown = seasons.to_eastern(moment)

        # The wall clock never reads 2:30 that morning, so it lands an hour later.
        self.assertEqual((shown.hour, shown.minute), (3, 30))

    def test_the_repeated_hour_takes_the_first_pass(self):
        moment = seasons.localize(date(2026, 11, 1), 1, 30)

        # Still on daylight time, four hours behind UTC rather than five.
        self.assertEqual(seasons.to_eastern(moment).utcoffset(), timedelta(hours=-4))
        self.assertEqual(seasons.to_eastern(moment).tzname(), 'EDT')

    def test_a_season_starting_in_the_skipped_hour_still_advances_cleanly(self):
        start = seasons.localize(date(2026, 3, 7), 2, 30)
        end = seasons.add_days_same_wall_clock(start, 1)

        self.assertEqual(seasons.eastern_date(end), date(2026, 3, 8))
        self.assertEqual(seasons.to_eastern(end).hour, 3)


class DayBoundsTests(SimpleTestCase):
    def test_an_ordinary_day_is_24_hours(self):
        start, end = seasons.eastern_day_bounds(date(2026, 11, 10))
        self.assertEqual(hours_between(start, end), 24)

    def test_the_autumn_change_day_is_25_hours(self):
        start, end = seasons.eastern_day_bounds(date(2026, 11, 1))
        self.assertEqual(hours_between(start, end), 25)

    def test_the_spring_change_day_is_23_hours(self):
        start, end = seasons.eastern_day_bounds(date(2026, 3, 8))
        self.assertEqual(hours_between(start, end), 23)

    def test_bounds_are_half_open_and_meet_the_next_day(self):
        _, first_end = seasons.eastern_day_bounds(date(2026, 11, 1))
        second_start, _ = seasons.eastern_day_bounds(date(2026, 11, 2))
        self.assertEqual(first_end, second_start)

    def test_an_instant_just_before_midnight_belongs_to_the_earlier_day(self):
        _, end = seasons.eastern_day_bounds(date(2026, 11, 1))
        self.assertEqual(seasons.eastern_date(end - timedelta(seconds=1)), date(2026, 11, 1))
        self.assertEqual(seasons.eastern_date(end), date(2026, 11, 2))


class OccurrenceTests(SimpleTestCase):
    def test_the_first_season_ends_on_the_next_sunday_five_pm(self):
        go_live = eastern(2026, 11, 3, 9, 30)
        end = seasons.first_season_end(go_live)

        self.assertEqual(seasons.eastern_date(end), date(2026, 11, 8))
        self.assertEqual(seasons.to_eastern(end).hour, 17)

    def test_going_live_on_a_sunday_morning_ends_that_same_afternoon(self):
        go_live = eastern(2026, 11, 8, 9, 0)
        end = seasons.first_season_end(go_live)

        self.assertEqual(seasons.eastern_date(end), date(2026, 11, 8))

    def test_going_live_after_five_pm_sunday_waits_a_full_week(self):
        go_live = eastern(2026, 11, 8, 17, 1)
        end = seasons.first_season_end(go_live)

        self.assertEqual(seasons.eastern_date(end), date(2026, 11, 15))

    def test_an_occurrence_is_strictly_after_the_given_instant(self):
        exactly_five = eastern(2026, 11, 8, 17)
        self.assertEqual(
            seasons.eastern_date(seasons.next_weekly_occurrence(exactly_five)),
            date(2026, 11, 15),
        )

    def test_the_next_daily_five_pm_skips_to_tomorrow_once_today_has_passed(self):
        after = eastern(2026, 11, 10, 18)
        self.assertEqual(
            seasons.eastern_date(seasons.next_daily_occurrence(after)),
            date(2026, 11, 11),
        )


class IntervalChangeTests(SimpleTestCase):
    def test_raising_seven_to_fourteen_mid_season_moves_the_end_out_by_seven_days(self):
        starts_at = eastern(2026, 11, 8, 17)
        now = starts_at + timedelta(days=3)

        original = seasons.season_end_for(starts_at, 7)
        revised = seasons.revised_season_end(starts_at, 14, now)

        self.assertEqual(hours_between(original, revised), 168)
        self.assertEqual(seasons.eastern_date(revised), date(2026, 11, 22))

    def test_lowering_the_interval_below_the_current_day_never_resets_immediately(self):
        starts_at = eastern(2026, 11, 8, 17)
        now = starts_at + timedelta(days=5)

        revised = seasons.revised_season_end(starts_at, 2, now)

        self.assertGreater(revised, now + seasons.MIN_NOTICE)
        self.assertEqual(seasons.to_eastern(revised).hour, 17)
        self.assertEqual(seasons.eastern_date(revised), date(2026, 11, 14))

    def test_an_end_landing_inside_the_notice_window_is_pushed_out(self):
        starts_at = eastern(2026, 11, 8, 17)
        # A one-day interval whose end is only half an hour away.
        now = seasons.season_end_for(starts_at, 1) - timedelta(minutes=30)

        revised = seasons.revised_season_end(starts_at, 1, now)

        self.assertGreater(revised, now + seasons.MIN_NOTICE)

    def test_an_end_comfortably_in_the_future_is_left_exactly_where_it_falls(self):
        starts_at = eastern(2026, 11, 8, 17)
        now = starts_at + timedelta(days=1)

        self.assertEqual(
            seasons.revised_season_end(starts_at, 7, now),
            seasons.season_end_for(starts_at, 7),
        )

    def test_a_revised_end_across_the_autumn_change_keeps_five_pm(self):
        starts_at = eastern(2026, 10, 25, 17)
        now = starts_at + timedelta(days=1)

        revised = seasons.revised_season_end(starts_at, 7, now)

        self.assertEqual(seasons.to_eastern(revised).hour, 17)
        self.assertEqual(seasons.eastern_date(revised), date(2026, 11, 1))


class ClosedDaysTests(SimpleTestCase):
    def test_today_is_never_closed(self):
        now = eastern(2026, 11, 10, 12)
        days = seasons.closed_days_between(date(2026, 11, 8), now)

        self.assertEqual(days, [date(2026, 11, 8), date(2026, 11, 9)])

    def test_a_single_day_gap_returns_nothing(self):
        now = eastern(2026, 11, 8, 12)
        self.assertEqual(seasons.closed_days_between(date(2026, 11, 8), now), [])

    def test_the_run_includes_the_clock_change_day(self):
        now = eastern(2026, 11, 3, 1)
        days = seasons.closed_days_between(date(2026, 10, 31), now)

        self.assertIn(date(2026, 11, 1), days)
        self.assertEqual(days[-1], date(2026, 11, 2))


class DescribeTests(SimpleTestCase):
    def test_the_admin_label_names_the_day_the_time_and_the_zone(self):
        label = seasons.describe(eastern(2026, 11, 8, 17))

        self.assertIn('Sunday', label)
        self.assertIn('5:00 PM', label)
        self.assertIn('EST', label)
