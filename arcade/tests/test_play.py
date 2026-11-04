import os
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from arcade import departures, discord_api, leaderboards, play, podium
from arcade.models import (
    ArcadeEvent,
    EventType,
    PersonalBest,
    PlaySession,
    RemovalReason,
    RunPost,
    Score,
)
from arcade.tests.factories import eastern, make_game, make_score, make_season

GUILD = '427913782895837195'

ARCADE_ENV = {
    'VICE_ARCADE_CLIENT_ID': '1567002859785031690',
    'VICE_ARCADE_CLIENT_SECRET': 'not-a-real-secret',
    'VICE_ARCADE_BOT_TOKEN': 'not-a-real-token',
    'VICE_GAMERS_GUILD_ID': GUILD,
    'ARCADE_SESSION_SIGNING_KEY': 'test-signing-key',
    'BOT_API_SECRET': 'test-bot-secret',
    # Blank, so no test ever posts to a real channel whatever a local .env says.
    'VG_ARCADE_CHANNEL_ID': '',
}

HERE = {'guild_id': GUILD, 'channel_id': ''}


class ArcadeEnvMixin:
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, ARCADE_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)


class MembershipGateTests(ArcadeEnvMixin, TestCase):
    def confirm(self, **lookup):
        with mock.patch.object(discord_api, 'guild_member', **lookup) as called:
            location = play.confirm_member('player')
        return location, called

    def test_a_member_passes_and_is_stamped_with_vice_gamers(self):
        location, called = self.confirm(return_value={'user': {'id': 'player'}, 'pending': False})

        self.assertEqual(location, HERE)
        called.assert_called_once_with(GUILD, 'player')

    def test_someone_who_is_not_in_vice_gamers_is_refused(self):
        with self.assertRaises(play.GateRefused) as caught:
            self.confirm(side_effect=discord_api.DiscordError(
                'Unknown Member', status=404, code=discord_api.UNKNOWN_MEMBER,
            ))
        self.assertEqual(caught.exception.reason, 'wrong_guild')

    def test_a_member_still_on_the_rules_screen_is_refused(self):
        with self.assertRaises(play.GateRefused) as caught:
            self.confirm(return_value={'user': {'id': 'player'}, 'pending': True})
        self.assertEqual(caught.exception.reason, 'member_pending')

    def test_discord_being_down_is_not_read_as_a_refusal(self):
        # The caller fails closed on this; the gate must not turn it into "not a
        # member", which would tell a member to go and apply.
        with self.assertRaises(discord_api.DiscordUnavailable):
            self.confirm(side_effect=discord_api.DiscordUnavailable('timeout'))

    def test_any_other_discord_error_is_not_read_as_a_refusal_either(self):
        with self.assertRaises(discord_api.DiscordError):
            self.confirm(side_effect=discord_api.DiscordError('Unauthorized', status=401, code=0))


class SessionTests(ArcadeEnvMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.game = make_game()
        self.now = eastern(2026, 11, 10, 12)

    def test_a_session_is_signed_single_use_and_expires_in_two_hours(self):
        session, token = play.issue_session('player', self.game, HERE, 'inst-1', self.now)

        self.assertFalse(session.used)
        self.assertEqual(session.expires_at - self.now, timedelta(hours=2))
        self.assertEqual(play.read_session_token(token), str(session.id))

    def test_the_seed_is_a_uint32(self):
        session, _ = play.issue_session('player', self.game, HERE, 'inst-1', self.now)
        self.assertTrue(0 <= session.seed < 2 ** 32)

    def test_two_sessions_do_not_share_a_seed_by_accident(self):
        seeds = {
            play.issue_session('p', self.game, HERE, 'i', self.now)[0].seed
            for _ in range(20)
        }
        self.assertGreater(len(seeds), 1)

    def test_a_tampered_token_is_refused(self):
        _, token = play.issue_session('player', self.game, HERE, 'inst-1', self.now)

        with self.assertRaises(play.SubmissionRefused) as caught:
            play.read_session_token(token[:-3] + 'xxx')
        self.assertEqual(caught.exception.reason, 'bad_session_token')

    def test_a_token_signed_with_another_key_is_refused(self):
        _, token = play.issue_session('player', self.game, HERE, 'inst-1', self.now)

        with mock.patch.dict(os.environ, {'ARCADE_SESSION_SIGNING_KEY': 'a-different-key'}):
            with self.assertRaises(play.SubmissionRefused):
                play.read_session_token(token)


class SubmitTests(ArcadeEnvMixin, TestCase):
    def setUp(self):
        super().setUp()
        # submit_score stamps created_at through auto_now_add, so these tests have
        # to run inside a season that really contains the present moment. Tests
        # that build scores by hand can use frozen instants instead.
        self.now = timezone.now()
        self.season = make_season(self.now - timedelta(hours=3))
        self.game = make_game()

    def start(self, user='player'):
        return play.issue_session(user, self.game, HERE, 'inst-1', self.now)

    def stats(self, **overrides):
        base = {'pieces_placed': 100, 'duration_ms': 120000, 'max_chain': 3, 'final_level': 4}
        base.update(overrides)
        return base

    def test_the_first_score_onto_an_empty_board_posts_no_record(self):
        session, token = self.start()
        play.submit_score(
            token, 'player', 'Player', 500,
            self.stats(seed=session.seed), now=self.now,
        )

        self.assertEqual(ArcadeEvent.objects.filter(type=EventType.NEW_ALLTIME_RECORD).count(), 0)

    def test_the_session_is_spent(self):
        session, token = self.start()
        play.submit_score(token, 'player', 'Player', 500, self.stats(seed=session.seed), now=self.now)
        session.refresh_from_db()

        self.assertTrue(session.used)

    def test_the_same_session_cannot_be_submitted_twice(self):
        session, token = self.start()
        play.submit_score(token, 'player', 'Player', 500, self.stats(seed=session.seed), now=self.now)

        with self.assertRaises(play.SubmissionRefused) as caught:
            play.submit_score(token, 'player', 'Player', 900, self.stats(seed=session.seed), now=self.now)
        self.assertEqual(caught.exception.reason, 'session_used')

    def test_an_expired_session_is_refused(self):
        session, token = self.start()

        with self.assertRaises(play.SubmissionRefused) as caught:
            play.submit_score(
                token, 'player', 'Player', 500, self.stats(seed=session.seed),
                now=self.now + timedelta(hours=3),
            )
        self.assertEqual(caught.exception.reason, 'session_expired')

    def test_someone_else_cannot_submit_your_session(self):
        session, token = self.start(user='owner')

        with self.assertRaises(play.SubmissionRefused) as caught:
            play.submit_score(token, 'thief', 'Thief', 500, self.stats(seed=session.seed), now=self.now)
        self.assertEqual(caught.exception.reason, 'session_not_yours')

    def test_an_unknown_session_is_refused(self):
        import uuid
        from django.core import signing
        from arcade import conf

        token = signing.dumps(
            {'session_id': str(uuid.uuid4()), 'user': 'player'},
            key=conf.session_signing_key(),
            salt=play.SIGNING_SALT,
        )

        with self.assertRaises(play.SubmissionRefused) as caught:
            play.submit_score(token, 'player', 'Player', 500, self.stats(), now=self.now)
        self.assertEqual(caught.exception.reason, 'unknown_session')

    def test_a_run_on_the_wrong_seed_is_refused(self):
        session, token = self.start()

        with self.assertRaises(play.SubmissionRefused) as caught:
            play.submit_score(
                token, 'player', 'Player', 500,
                self.stats(seed=session.seed + 1), now=self.now,
            )
        self.assertEqual(caught.exception.reason, 'seed_mismatch')

    def test_a_run_banked_after_the_player_left_is_refused(self):
        session, token = self.start()
        departures.record_departure('player', self.now + timedelta(minutes=1))

        with self.assertRaises(play.SubmissionRefused) as caught:
            play.submit_score(
                token, 'player', 'Player', 500, self.stats(seed=session.seed),
                now=self.now + timedelta(minutes=2),
            )
        self.assertEqual(caught.exception.reason, 'member_left')

    def play(self, user, name, score):
        session, token = self.start(user=user)
        return play.submit_score(token, user, name, score, self.stats(seed=session.seed), now=self.now)

    def queued(self):
        return list(RunPost.objects.select_related('score').order_by('id'))

    def test_every_banked_run_is_queued_for_its_channel_post(self):
        saved = self.play('player', 'Player', 500)

        [post] = self.queued()
        self.assertEqual(post.score, saved)
        self.assertIsNone(post.previous_best, 'a first run has nothing before it')
        self.assertIsNone(post.handled_at)

    def test_the_queued_post_knows_the_best_the_run_was_up_against(self):
        self.play('holder', 'Holder', 1000)
        self.play('player', 'Player', 300)
        self.play('player', 'Player', 450)
        self.play('player', 'Player', 200)

        _, first, better, worse = self.queued()
        self.assertIsNone(first.previous_best)
        self.assertEqual(better.previous_best, 300)
        self.assertEqual(worse.previous_best, 450)

    def test_a_record_run_is_queued_as_played_like_any_other(self):
        self.play('holder', 'Holder', 400)
        self.play('challenger', 'Challenger', 900)

        self.assertEqual(
            [post.score.display_name for post in self.queued()],
            ['Holder', 'Challenger'],
        )

    def test_a_score_above_the_record_is_posted_as_a_record_at_once(self):
        make_score(self.game, 'holder', 400, self.now - timedelta(hours=1))
        session, token = self.start(user='challenger')

        play.submit_score(
            token, 'challenger', 'Challenger', 900,
            self.stats(seed=session.seed), now=self.now,
        )

        event = ArcadeEvent.objects.get(type=EventType.NEW_ALLTIME_RECORD)
        self.assertEqual(event.payload['score'], 900)
        self.assertEqual(event.payload['previous_best'], 400)
        self.assertEqual(event.payload['previous_holder'], 'holder')
        self.assertFalse(ArcadeEvent.objects.filter(type='record_pending_review').exists())

    def test_a_score_below_the_record_posts_no_record(self):
        make_score(self.game, 'holder', 400, self.now - timedelta(hours=1))
        session, token = self.start(user='challenger')

        play.submit_score(
            token, 'challenger', 'Challenger', 300,
            self.stats(seed=session.seed), now=self.now,
        )

        self.assertFalse(ArcadeEvent.objects.filter(type=EventType.NEW_ALLTIME_RECORD).exists())

    def test_a_record_counts_on_the_board_and_for_the_role_at_once(self):
        make_score(self.game, 'holder', 400, self.now - timedelta(hours=1))
        session, token = self.start(user='challenger')
        play.submit_score(token, 'challenger', 'Challenger', 900, self.stats(seed=session.seed), now=self.now)

        board = leaderboards.season_rows(self.game, self.season)

        self.assertEqual(board[0]['discord_user_id'], 'challenger')
        self.assertNotIn('pending', board[0])
        self.assertEqual(
            [r['discord_user_id'] for r in podium.current_holders(self.season)],
            ['challenger', 'holder'],
        )

    def test_a_personal_best_is_recorded(self):
        session, token = self.start()
        play.submit_score(token, 'player', 'Player', 500, self.stats(seed=session.seed), now=self.now)

        best = PersonalBest.objects.get(discord_user_id='player', game=self.game)
        self.assertEqual(best.best_score, 500)

    def test_the_run_stats_and_input_log_are_kept_for_a_replay(self):
        session, token = self.start()
        log = [{'tick': 1, 'input': 'left'}]

        saved = play.submit_score(
            token, 'player', 'Player', 500, self.stats(seed=session.seed),
            input_log=log, now=self.now,
        )

        self.assertEqual(saved.input_log, log)
        self.assertEqual(saved.run_stats['seed'], session.seed)


class PlausibilityTests(ArcadeEnvMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.season = make_season(self.now - timedelta(hours=3))
        self.game = make_game()
        self.game.plausibility = {
            'max_score': 100000,
            'max_score_per_piece': 50,
            'min_ms_per_piece': 200,
            'max_chain': 10,
            'max_level': 20,
        }
        self.game.save()

    def submit(self, score, **stats):
        session, token = play.issue_session('player', self.game, HERE, 'inst', self.now)
        payload = {'pieces_placed': 100, 'duration_ms': 60000, 'seed': session.seed}
        payload.update(stats)
        return play.submit_score(token, 'player', 'Player', score, payload, now=self.now)

    def test_a_believable_run_is_accepted(self):
        self.assertIsNotNone(self.submit(3000))

    def test_more_points_than_the_pieces_could_have_produced_is_refused(self):
        with self.assertRaises(play.SubmissionRefused) as caught:
            self.submit(9000, pieces_placed=100)
        self.assertEqual(caught.exception.reason, 'implausible')
        self.assertIn('per piece', caught.exception.detail)

    def test_a_run_faster_than_the_pieces_allow_is_refused(self):
        with self.assertRaises(play.SubmissionRefused) as caught:
            self.submit(1000, pieces_placed=100, duration_ms=5000)
        self.assertIn('faster than', caught.exception.detail)

    def test_an_impossible_chain_is_refused(self):
        with self.assertRaises(play.SubmissionRefused) as caught:
            self.submit(1000, max_chain=99)
        self.assertIn('max chain', caught.exception.detail)

    def test_the_absolute_ceiling_is_enforced(self):
        with self.assertRaises(play.SubmissionRefused) as caught:
            self.submit(999999, pieces_placed=100000, duration_ms=99999999)
        self.assertIn('ceiling', caught.exception.detail)

    def test_missing_stats_cannot_sneak_past_a_ratio_check(self):
        with self.assertRaises(play.SubmissionRefused) as caught:
            self.submit(9000, pieces_placed=None)
        self.assertIn('missing', caught.exception.detail)

    def test_every_broken_limit_is_reported_not_just_the_first(self):
        with self.assertRaises(play.SubmissionRefused) as caught:
            self.submit(9000, pieces_placed=100, duration_ms=1000, max_chain=50)
        self.assertEqual(caught.exception.detail.count(';'), 2)

    def test_a_cabinet_with_no_limits_set_accepts_anything(self):
        self.game.plausibility = {}
        self.game.save()
        self.assertIsNotNone(self.submit(10 ** 9, pieces_placed=1, duration_ms=1))


class OwnerRemovalTests(ArcadeEnvMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.season = make_season(eastern(2026, 11, 8, 17))
        self.game = make_game()
        self.moment = self.season.starts_at + timedelta(hours=1)

    def test_removing_a_score_takes_it_off_every_board(self):
        keep = make_score(self.game, 'keeper', 100, self.moment)
        drop = make_score(self.game, 'cheat', 900, self.moment)

        play.remove_score(drop, now=self.moment)

        drop.refresh_from_db()
        self.assertTrue(drop.removed)
        self.assertEqual(drop.removal_reason, RemovalReason.OWNER_REMOVED)
        self.assertEqual(
            [r['discord_user_id'] for r in leaderboards.alltime_rows(self.game)],
            ['keeper'],
        )

    def test_removing_a_players_best_falls_back_to_their_next_best(self):
        make_score(self.game, 'player', 100, self.moment)
        top = make_score(self.game, 'player', 900, self.moment + timedelta(minutes=1))
        leaderboards.recompute_personal_best('player', self.game)

        play.remove_score(top, now=self.moment)

        self.assertEqual(
            PersonalBest.objects.get(discord_user_id='player', game=self.game).best_score,
            100,
        )

    def test_removing_a_players_only_score_drops_their_personal_best_row(self):
        only = make_score(self.game, 'player', 100, self.moment)
        leaderboards.recompute_personal_best('player', self.game)

        play.remove_score(only, now=self.moment)

        self.assertFalse(PersonalBest.objects.filter(discord_user_id='player').exists())
