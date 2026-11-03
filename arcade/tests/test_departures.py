from datetime import timedelta

from django.test import TestCase

from arcade import departures, leaderboards, podium
from arcade.models import (
    ArcadeAlertOptIn,
    ArcadeEvent,
    DailyBoard,
    EventType,
    PersonalBest,
    PlaySession,
    PodiumResult,
    RemovalReason,
    Score,
    SeasonStatus,
)
from arcade.tests.factories import eastern, make_game, make_score, make_season


class RemovalTests(TestCase):
    def setUp(self):
        self.season = make_season(eastern(2026, 11, 8, 17))
        self.game = make_game()
        self.moment = self.season.starts_at + timedelta(hours=1)
        self.left_at = self.moment + timedelta(hours=2)

    def test_the_leader_leaving_hands_every_role_down_one(self):
        for name, value in (('first', 400), ('second', 300), ('third', 200), ('fourth', 100)):
            make_score(self.game, name, value, self.moment)
        podium.refresh(self.season)

        departures.record_departure('first', self.left_at)

        self.assertEqual(
            [row['discord_user_id'] for row in podium.current_holders(self.season)],
            ['second', 'third', 'fourth'],
        )

    def test_a_departure_writes_no_record_post_because_nobody_beat_a_score(self):
        make_score(self.game, 'first', 400, self.moment)
        make_score(self.game, 'second', 300, self.moment)

        departures.record_departure('first', self.left_at)

        self.assertEqual(
            ArcadeEvent.objects.filter(type=EventType.NEW_ALLTIME_RECORD).count(),
            0,
        )

    def test_the_next_player_simply_becomes_all_time_number_one(self):
        make_score(self.game, 'first', 400, self.moment)
        make_score(self.game, 'second', 300, self.moment)

        departures.record_departure('first', self.left_at)
        rows = leaderboards.alltime_rows(self.game)

        self.assertEqual([r['discord_user_id'] for r in rows], ['second'])
        self.assertEqual(rows[0]['place'], 1)

    def test_they_vanish_from_every_board_at_once(self):
        make_score(self.game, 'gone', 400, self.moment)
        make_score(self.game, 'stays', 300, self.moment)

        departures.record_departure('gone', self.left_at)

        day = eastern(2026, 11, 8, 17)
        for rows in (
            leaderboards.alltime_rows(self.game),
            leaderboards.season_rows(self.game, self.season),
            leaderboards.daily_rows(self.game, self.moment.date()),
        ):
            self.assertNotIn('gone', [r['discord_user_id'] for r in rows])

    def test_their_scores_are_marked_rather_than_deleted_so_a_mistake_is_recoverable(self):
        make_score(self.game, 'gone', 400, self.moment)

        departures.record_departure('gone', self.left_at)
        score = Score.objects.get(discord_user_id='gone')

        self.assertTrue(score.removed)
        self.assertEqual(score.removal_reason, RemovalReason.MEMBER_LEFT)

    def test_a_score_the_owner_removed_keeps_its_own_reason(self):
        make_score(
            self.game, 'gone', 400, self.moment,
            removed=True, removal_reason=RemovalReason.OWNER_REMOVED,
        )
        make_score(self.game, 'gone', 100, self.moment + timedelta(minutes=1))

        departures.record_departure('gone', self.left_at)
        reasons = set(
            Score.objects.filter(discord_user_id='gone').values_list('removal_reason', flat=True)
        )

        self.assertEqual(reasons, {RemovalReason.OWNER_REMOVED, RemovalReason.MEMBER_LEFT})

    def test_personal_bests_and_alert_opt_ins_go_too(self):
        make_score(self.game, 'gone', 400, self.moment)
        leaderboards.recompute_personal_best('gone', self.game)
        ArcadeAlertOptIn.objects.create(discord_user_id='gone', enabled=True)

        departures.record_departure('gone', self.left_at)

        self.assertFalse(PersonalBest.objects.filter(discord_user_id='gone').exists())
        self.assertFalse(ArcadeAlertOptIn.objects.filter(discord_user_id='gone').exists())

    def test_a_run_in_progress_can_no_longer_be_submitted(self):
        session = PlaySession.objects.create(
            discord_user_id='gone',
            game=self.game,
            seed=1234,
            guild_id='g',
            channel_id='c',
            expires_at=self.moment + timedelta(hours=2),
        )

        departures.record_departure('gone', self.left_at)
        session.refresh_from_db()

        self.assertTrue(session.used)

    def test_repeating_the_same_departure_changes_nothing_further(self):
        make_score(self.game, 'gone', 400, self.moment)

        first = departures.record_departure('gone', self.left_at)
        second = departures.record_departure('gone', self.left_at)

        self.assertEqual(first['scores_removed'], 1)
        self.assertEqual(second['scores_removed'], 0)
        self.assertEqual(
            Score.objects.filter(discord_user_id='gone', removed=True).count(), 1
        )


class RejoinTests(TestCase):
    def setUp(self):
        self.season = make_season(eastern(2026, 11, 8, 17))
        self.game = make_game()
        self.moment = self.season.starts_at + timedelta(hours=1)
        self.left_at = self.moment + timedelta(hours=2)

    def test_a_session_issued_before_they_left_is_rejected(self):
        departures.record_departure('gone', self.left_at)

        self.assertTrue(
            departures.session_invalidated_by_departure('gone', self.left_at - timedelta(minutes=5))
        )

    def test_a_session_issued_after_they_rejoined_is_valid(self):
        departures.record_departure('gone', self.left_at)

        self.assertFalse(
            departures.session_invalidated_by_departure('gone', self.left_at + timedelta(hours=1))
        )

    def test_a_player_who_never_left_is_never_rejected(self):
        self.assertFalse(
            departures.session_invalidated_by_departure('present', self.moment)
        )

    def test_old_scores_stay_gone_after_rejoining(self):
        make_score(self.game, 'gone', 400, self.moment)
        departures.record_departure('gone', self.left_at)

        make_score(self.game, 'gone', 50, self.left_at + timedelta(hours=1))
        rows = leaderboards.alltime_rows(self.game)

        self.assertEqual([(r['discord_user_id'], r['score']) for r in rows], [('gone', 50)])

    def test_leaving_twice_takes_the_later_departure(self):
        first_exit = self.left_at
        second_exit = self.left_at + timedelta(days=1)

        departures.record_departure('gone', first_exit)
        departures.record_departure('gone', second_exit)

        self.assertTrue(
            departures.session_invalidated_by_departure('gone', first_exit + timedelta(hours=1))
        )
        self.assertFalse(
            departures.session_invalidated_by_departure('gone', second_exit + timedelta(hours=1))
        )


class PostedBoardTests(TestCase):
    def setUp(self):
        self.season = make_season(eastern(2026, 11, 8, 17))
        self.game = make_game()
        self.moment = self.season.starts_at + timedelta(hours=1)
        self.left_at = self.moment + timedelta(hours=5)
        self.day = self.moment.date()

    def make_board(self, *users):
        make_score(self.game, 'ignored', 1, self.moment)
        return DailyBoard.objects.create(
            game=self.game,
            day=self.day,
            entries=[
                {
                    'place': index,
                    'discord_user_id': user,
                    'display_name': user,
                    'score': 100 - index,
                    'pending': False,
                }
                for index, user in enumerate(users, start=1)
            ],
            message_ids=['111'],
        )

    def test_a_posted_board_is_renumbered_without_the_departed_player(self):
        board = self.make_board('a', 'gone', 'c')
        make_score(self.game, 'gone', 400, self.moment)

        departures.record_departure('gone', self.left_at)
        board.refresh_from_db()

        self.assertEqual(
            [(e['place'], e['discord_user_id']) for e in board.entries],
            [(1, 'a'), (2, 'c')],
        )

    def test_the_bot_is_told_which_messages_to_edit(self):
        self.make_board('gone', 'b')
        make_score(self.game, 'gone', 400, self.moment)

        departures.record_departure('gone', self.left_at)
        event = ArcadeEvent.objects.get(type=EventType.DAILY_BOARDS_CHANGED)

        self.assertEqual(event.payload['reason'], 'member_left')
        self.assertEqual(event.payload['boards'][0]['message_ids'], ['111'])

    def test_a_board_that_ends_up_empty_is_reported_as_empty(self):
        board = self.make_board('gone')
        make_score(self.game, 'gone', 400, self.moment)

        departures.record_departure('gone', self.left_at)
        board.refresh_from_db()

        self.assertEqual(board.entries, [])

    def test_a_board_the_player_was_never_on_is_left_alone(self):
        board = self.make_board('a', 'b')
        make_score(self.game, 'gone', 400, self.moment)

        result = departures.record_departure('gone', self.left_at)
        board.refresh_from_db()

        self.assertEqual(len(board.entries), 2)
        self.assertEqual(result['boards_changed'], [])
        self.assertIsNone(result['boards_event'])


class FinalizedHistoryTests(TestCase):
    def setUp(self):
        self.season = make_season(eastern(2026, 11, 1, 17), status=SeasonStatus.FINALIZED)
        self.game = make_game()
        for place, user in enumerate(['gold', 'silver', 'bronze'], start=1):
            PodiumResult.objects.create(
                season=self.season,
                place=place,
                discord_user_id=user,
                display_name=user,
                average_placement=float(place),
            )

    def test_a_past_winner_who_leaves_is_hidden_and_nobody_moves_up(self):
        departures.record_departure('silver', eastern(2026, 11, 10, 12))

        rows = PodiumResult.objects.filter(season=self.season).order_by('place')
        visible = [(r.place, r.discord_user_id) for r in rows if not r.hidden]
        hidden = [(r.place, r.discord_user_id) for r in rows if r.hidden]

        self.assertEqual(visible, [(1, 'gold'), (3, 'bronze')])
        self.assertEqual(hidden, [(2, 'silver')])

    def test_a_hidden_winner_drops_out_of_the_active_player_list(self):
        self.assertIn('gold', departures.active_player_ids())

        departures.record_departure('gold', eastern(2026, 11, 10, 12))

        self.assertNotIn('gold', departures.active_player_ids())


class BatchTests(TestCase):
    def setUp(self):
        self.season = make_season(eastern(2026, 11, 8, 17))
        self.game = make_game()
        self.moment = self.season.starts_at + timedelta(hours=1)
        self.left_at = self.moment + timedelta(hours=2)

    def test_a_purge_of_many_members_leaves_one_correct_final_state(self):
        for index in range(6):
            make_score(self.game, f'gone{index}', 500 - index, self.moment)
        make_score(self.game, 'survivor', 10, self.moment)
        podium.refresh(self.season)

        departures.record_departures([
            {'discord_user_id': f'gone{index}', 'left_at': self.left_at}
            for index in range(6)
        ])

        holders = podium.current_holders(self.season)
        self.assertEqual([row['discord_user_id'] for row in holders], ['survivor'])
        self.assertEqual(
            leaderboards.visible_scores(self.game).count(), 1
        )

    def test_the_active_list_covers_players_with_visible_scores(self):
        make_score(self.game, 'here', 100, self.moment)
        make_score(self.game, 'hidden', 100, self.moment, removed=True,
                   removal_reason=RemovalReason.OWNER_REMOVED)

        active = departures.active_player_ids()

        self.assertIn('here', active)
        self.assertNotIn('hidden', active)
