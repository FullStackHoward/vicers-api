from datetime import date, timedelta

from django.test import TestCase

from arcade import dailyboards
from arcade.models import ArcadeEvent, DailyBoard, EventType
from arcade.tests.factories import eastern, make_game, make_score


class FinalizationTests(TestCase):
    def setUp(self):
        self.game = make_game()
        self.day = date(2026, 11, 10)
        self.noon = eastern(2026, 11, 10, 12)
        self.tomorrow = eastern(2026, 11, 11, 9)

    def test_a_closed_day_with_scores_is_snapshotted_and_announced(self):
        make_score(self.game, 'a', 300, self.noon)
        make_score(self.game, 'b', 200, self.noon)

        events = dailyboards.finalize_due_boards(self.tomorrow)
        board = DailyBoard.objects.get(game=self.game, day=self.day)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].type, EventType.DAILY_BOARD_CLOSED)
        self.assertEqual(events[0].payload['day'], self.day.isoformat())
        self.assertEqual(
            [(e['place'], e['discord_user_id']) for e in board.entries],
            [(1, 'a'), (2, 'b')],
        )

    def test_today_is_never_finalized(self):
        make_score(self.game, 'a', 300, self.noon)

        events = dailyboards.finalize_due_boards(self.noon + timedelta(hours=1))

        self.assertEqual(events, [])
        self.assertFalse(DailyBoard.objects.exists())

    def test_a_board_lists_everyone_who_played_not_a_top_ten(self):
        for index in range(14):
            make_score(self.game, f'p{index}', 500 - index, self.noon)

        dailyboards.finalize_due_boards(self.tomorrow)
        board = DailyBoard.objects.get(game=self.game, day=self.day)

        self.assertEqual(len(board.entries), 14)
        self.assertEqual(board.entries[-1]['place'], 14)

    def test_one_row_per_player_using_their_best_of_the_day(self):
        make_score(self.game, 'a', 100, self.noon)
        make_score(self.game, 'a', 400, self.noon + timedelta(hours=1))

        dailyboards.finalize_due_boards(self.tomorrow)
        board = DailyBoard.objects.get(game=self.game, day=self.day)

        self.assertEqual(len(board.entries), 1)
        self.assertEqual(board.entries[0]['score'], 400)

    def test_a_day_with_no_scores_produces_no_row_and_no_post(self):
        make_score(self.game, 'a', 300, self.noon)
        # Two days later: the day in between was empty.
        dailyboards.finalize_due_boards(eastern(2026, 11, 12, 9))

        days = list(DailyBoard.objects.values_list('day', flat=True))

        self.assertEqual(days, [self.day])
        self.assertEqual(ArcadeEvent.objects.count(), 1)

    def test_a_second_pass_finalizes_nothing_twice(self):
        make_score(self.game, 'a', 300, self.noon)

        dailyboards.finalize_due_boards(self.tomorrow)
        again = dailyboards.finalize_due_boards(self.tomorrow)

        self.assertEqual(again, [])
        self.assertEqual(DailyBoard.objects.count(), 1)
        self.assertEqual(ArcadeEvent.objects.count(), 1)

    def test_several_closed_days_are_finalized_oldest_first(self):
        make_score(self.game, 'a', 300, eastern(2026, 11, 10, 12))
        make_score(self.game, 'b', 300, eastern(2026, 11, 11, 12))

        events = dailyboards.finalize_due_boards(eastern(2026, 11, 12, 9))

        self.assertEqual(
            [event.payload['day'] for event in events],
            ['2026-11-10', '2026-11-11'],
        )

    def test_a_removed_score_keeps_its_player_off_the_board(self):
        make_score(self.game, 'a', 300, self.noon)
        make_score(self.game, 'gone', 900, self.noon, removed=True,
                   removal_reason='member_left')

        dailyboards.finalize_due_boards(self.tomorrow)
        board = DailyBoard.objects.get(game=self.game, day=self.day)

        self.assertEqual([e['discord_user_id'] for e in board.entries], ['a'])


class ActiveWindowTests(TestCase):
    def test_a_cabinet_that_was_not_live_that_day_is_skipped(self):
        game = make_game(active_from=eastern(2026, 11, 11, 0))
        make_score(game, 'a', 300, eastern(2026, 11, 10, 12))

        events = dailyboards.finalize_due_boards(eastern(2026, 11, 12, 9))

        self.assertEqual(events, [])

    def test_a_cabinet_live_from_that_morning_is_included(self):
        game = make_game(active_from=eastern(2026, 11, 10, 0))
        make_score(game, 'a', 300, eastern(2026, 11, 10, 12))

        events = dailyboards.finalize_due_boards(eastern(2026, 11, 11, 9))

        self.assertEqual(len(events), 1)

    def test_each_game_gets_its_own_board_for_the_same_day(self):
        first = make_game(slug='vetrix')
        second = make_game(slug='other', display_name='Other')
        make_score(first, 'a', 300, eastern(2026, 11, 10, 12))
        make_score(second, 'a', 200, eastern(2026, 11, 10, 12))

        events = dailyboards.finalize_due_boards(eastern(2026, 11, 11, 9))

        self.assertEqual(len(events), 2)
        self.assertEqual(DailyBoard.objects.count(), 2)


class ClockChangeTests(TestCase):
    def test_the_twenty_five_hour_day_captures_the_whole_day(self):
        game = make_game()
        # 1:30 AM happens twice on Nov 1 2026. Both passes belong to that day.
        make_score(game, 'early', 100, eastern(2026, 11, 1, 1, 30))
        make_score(game, 'late', 200, eastern(2026, 11, 1, 23, 30))

        dailyboards.finalize_due_boards(eastern(2026, 11, 2, 9))
        board = DailyBoard.objects.get(game=game, day=date(2026, 11, 1))

        self.assertEqual(len(board.entries), 2)

    def test_a_score_just_after_midnight_lands_on_the_new_day(self):
        game = make_game()
        make_score(game, 'a', 100, eastern(2026, 11, 10, 23, 59))
        make_score(game, 'b', 100, eastern(2026, 11, 11, 0, 1))

        dailyboards.finalize_due_boards(eastern(2026, 11, 12, 9))

        first = DailyBoard.objects.get(day=date(2026, 11, 10))
        second = DailyBoard.objects.get(day=date(2026, 11, 11))

        self.assertEqual([e['discord_user_id'] for e in first.entries], ['a'])
        self.assertEqual([e['discord_user_id'] for e in second.entries], ['b'])


class PostedMessageTests(TestCase):
    def test_the_message_ids_a_board_was_posted_as_are_remembered(self):
        game = make_game()
        make_score(game, 'a', 100, eastern(2026, 11, 10, 12))
        dailyboards.finalize_due_boards(eastern(2026, 11, 11, 9))
        board = DailyBoard.objects.get()

        dailyboards.record_posted_messages(board, ['991', '992'])
        board.refresh_from_db()

        self.assertEqual(board.message_ids, ['991', '992'])
