from datetime import date, timedelta

from django.test import TestCase

from arcade import rollover, seasons
from arcade.models import (
    ArcadeEvent,
    ArcadeSeason,
    ArcadeSettings,
    EventType,
    PodiumResult,
    SeasonStatus,
)
from arcade.tests.factories import eastern, make_season


def pending(event_type):
    return ArcadeEvent.objects.filter(type=event_type).count()


class FirstSeasonTests(TestCase):
    def test_the_first_call_opens_a_season_ending_the_next_sunday_at_five(self):
        now = eastern(2026, 11, 3, 9, 30)
        season = rollover.ensure_current_season(now)

        self.assertEqual(season.starts_at, now)
        self.assertEqual(seasons.eastern_date(season.ends_at), date(2026, 11, 8))
        self.assertEqual(seasons.to_eastern(season.ends_at).hour, 17)
        self.assertEqual(season.status, SeasonStatus.OPEN)

    def test_a_partial_first_round_is_still_a_round(self):
        now = eastern(2026, 11, 7, 23, 0)
        season = rollover.ensure_current_season(now)

        self.assertLess(season.ends_at - season.starts_at, timedelta(days=1))

    def test_opening_a_season_announces_nothing(self):
        rollover.ensure_current_season(eastern(2026, 11, 3, 9))
        self.assertEqual(ArcadeEvent.objects.count(), 0)


class SteadyStateTests(TestCase):
    def test_a_season_still_running_is_returned_untouched(self):
        now = eastern(2026, 11, 9, 12)
        first = rollover.ensure_current_season(now)
        second = rollover.ensure_current_season(now + timedelta(hours=1))

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ArcadeSeason.objects.count(), 1)

    def test_repeat_calls_write_no_duplicate_events(self):
        start = eastern(2026, 11, 8, 17)
        make_season(start)
        after = start + timedelta(days=8)

        rollover.ensure_current_season(after)
        rollover.ensure_current_season(after)
        rollover.ensure_current_season(after)

        self.assertEqual(pending(EventType.SEASON_CLOSING), 1)


class RolloverTests(TestCase):
    def test_a_finished_season_closes_and_the_next_starts_exactly_where_it_ended(self):
        start = eastern(2026, 11, 8, 17)
        old = make_season(start)
        now = old.ends_at + timedelta(minutes=1)

        new = rollover.ensure_current_season(now)
        old.refresh_from_db()

        self.assertEqual(old.status, SeasonStatus.CLOSING)
        self.assertEqual(new.starts_at, old.ends_at)
        self.assertEqual(new.status, SeasonStatus.OPEN)
        self.assertEqual(pending(EventType.SEASON_CLOSING), 1)

    def test_the_boards_switch_even_though_the_bot_never_ran(self):
        start = eastern(2026, 11, 8, 17)
        make_season(start)
        now = start + timedelta(days=10)

        season = rollover.ensure_current_season(now)

        self.assertTrue(season.contains(now))
        self.assertEqual(season.live_top, [])

    def test_several_missed_seasons_are_built_in_order_and_each_announces_once(self):
        start = eastern(2026, 11, 8, 17)
        make_season(start)
        now = start + timedelta(days=25)

        current = rollover.ensure_current_season(now)

        self.assertTrue(current.contains(now))
        self.assertEqual(ArcadeSeason.objects.count(), 4)
        self.assertEqual(pending(EventType.SEASON_CLOSING), 3)

        boundaries = list(ArcadeSeason.objects.order_by('starts_at'))
        for earlier, later in zip(boundaries, boundaries[1:]):
            self.assertEqual(earlier.ends_at, later.starts_at)

    def test_a_rollover_across_the_autumn_clock_change_still_lands_at_five_pm(self):
        start = eastern(2026, 10, 25, 17)
        make_season(start)

        season = rollover.ensure_current_season(start + timedelta(days=8))

        self.assertEqual(seasons.to_eastern(season.starts_at).hour, 17)
        self.assertEqual(seasons.to_eastern(season.ends_at).hour, 17)


class IntervalChangeTests(TestCase):
    def test_raising_the_interval_mid_season_pushes_the_end_out_by_the_difference(self):
        start = eastern(2026, 11, 8, 17)
        season = make_season(start, interval_days=7)
        original_end = season.ends_at
        now = start + timedelta(days=3)

        settings_row = ArcadeSettings.load()
        settings_row.reset_interval_days = 14
        settings_row.save()

        revised = rollover.apply_interval_change(now)
        season.refresh_from_db()

        self.assertEqual(season.ends_at, revised)
        self.assertEqual((revised - original_end), timedelta(days=7))

    def test_lowering_the_interval_past_today_never_resets_on_the_spot(self):
        start = eastern(2026, 11, 8, 17)
        season = make_season(start, interval_days=7)
        now = start + timedelta(days=5)

        settings_row = ArcadeSettings.load()
        settings_row.reset_interval_days = 2
        settings_row.save()

        revised = rollover.apply_interval_change(now)
        season.refresh_from_db()

        self.assertGreater(revised, now + seasons.MIN_NOTICE)
        self.assertEqual(seasons.to_eastern(revised).hour, 17)
        self.assertEqual(season.ends_at, revised)

    def test_a_fresh_install_resets_every_seven_days(self):
        self.assertEqual(
            ArcadeSettings.load().reset_interval_days,
            ArcadeSettings.DEFAULT_RESET_INTERVAL_DAYS,
        )
        self.assertEqual(ArcadeSettings.DEFAULT_RESET_INTERVAL_DAYS, 7)

    def test_the_settings_row_is_a_singleton(self):
        first = ArcadeSettings.load()
        first.reset_interval_days = 21
        first.save()

        second = ArcadeSettings.load()

        self.assertEqual(ArcadeSettings.objects.count(), 1)
        self.assertEqual(second.pk, ArcadeSettings.SINGLETON_PK)
        self.assertEqual(second.reset_interval_days, 21)

    def test_the_settings_row_refuses_to_be_deleted(self):
        with self.assertRaises(RuntimeError):
            ArcadeSettings.load().delete()


class FinalizeTests(TestCase):
    def setUp(self):
        self.season = make_season(eastern(2026, 11, 8, 17), status=SeasonStatus.CLOSING)
        self.winners = [
            {'discord_user_id': '1', 'display_name': 'one', 'average_placement': 1.0, 'breakdown': {}},
            {'discord_user_id': '2', 'display_name': 'two', 'average_placement': 2.0, 'breakdown': {}},
            {'discord_user_id': '3', 'display_name': 'three', 'average_placement': 3.0, 'breakdown': {}},
        ]

    def test_finalizing_stores_the_podium_and_announces_once(self):
        event = rollover.finalize(self.season, self.winners)
        self.season.refresh_from_db()

        self.assertEqual(self.season.status, SeasonStatus.FINALIZED)
        self.assertIsNotNone(self.season.finalized_at)
        self.assertEqual(PodiumResult.objects.filter(season=self.season).count(), 3)
        self.assertEqual(event.type, EventType.SEASON_AWARDED)
        self.assertEqual([w['place'] for w in event.payload['winners']], [1, 2, 3])

    def test_a_retry_after_a_timeout_writes_no_second_callout(self):
        rollover.finalize(self.season, self.winners)
        again = rollover.finalize(self.season, self.winners)

        self.assertIsNone(again)
        self.assertEqual(pending(EventType.SEASON_AWARDED), 1)

    def test_fewer_than_three_players_fills_only_the_places_there_are(self):
        rollover.finalize(self.season, self.winners[:2])

        places = list(
            PodiumResult.objects.filter(season=self.season).order_by('place')
            .values_list('place', flat=True)
        )
        self.assertEqual(places, [1, 2])

    def test_a_round_nobody_played_gets_no_callout(self):
        event = rollover.finalize(self.season, [])
        self.season.refresh_from_db()

        self.assertIsNone(event)
        self.assertEqual(self.season.status, SeasonStatus.FINALIZED)
        self.assertEqual(ArcadeEvent.objects.count(), 0)

    def test_only_three_places_are_ever_stored(self):
        crowded = self.winners + [
            {'discord_user_id': '4', 'display_name': 'four', 'average_placement': 4.0, 'breakdown': {}},
        ]
        rollover.finalize(self.season, crowded)

        self.assertEqual(PodiumResult.objects.filter(season=self.season).count(), 3)


class ClosingQueueTests(TestCase):
    def test_closed_seasons_come_back_oldest_first(self):
        later = make_season(eastern(2026, 11, 15, 17), status=SeasonStatus.CLOSING)
        earlier = make_season(eastern(2026, 11, 8, 17), status=SeasonStatus.CLOSING)
        make_season(eastern(2026, 11, 22, 17), status=SeasonStatus.OPEN)

        queue = list(rollover.closing_seasons())

        self.assertEqual([s.pk for s in queue], [earlier.pk, later.pk])
