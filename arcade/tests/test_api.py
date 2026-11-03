import os
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from arcade import discord_api, play
from arcade.models import ArcadeEvent, DailyBoard, EventType, PlaySession, Score, SeasonStatus
from arcade.tests.factories import eastern, make_game, make_score, make_season
from arcade.tests.test_play import ARCADE_ENV, CHANNEL, GUILD, HERE

BOT_SECRET = ARCADE_ENV['BOT_API_SECRET']


def bot_headers():
    return {'HTTP_X_BOT_SECRET': BOT_SECRET}


class ApiMixin:
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, ARCADE_ENV)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.game = make_game()

    def identity(self, user='player', name='Player'):
        return {'HTTP_X_ARCADE_IDENTITY': play.sign_identity(user, name)}


class BotAuthTests(ApiMixin, TestCase):
    BOT_ONLY = [
        '/api/arcade/events/pending',
        '/api/arcade/podium/current',
        '/api/arcade/standings/live',
        '/api/arcade/players/active',
        '/api/arcade/seasons/closing',
    ]

    def test_every_bot_endpoint_refuses_an_anonymous_caller(self):
        for url in self.BOT_ONLY:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_a_wrong_secret_is_refused(self):
        response = self.client.get(
            '/api/arcade/events/pending', HTTP_X_BOT_SECRET='not-the-secret'
        )
        self.assertEqual(response.status_code, 403)

    def test_the_right_secret_is_accepted(self):
        for url in self.BOT_ONLY:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url, **bot_headers()).status_code, 200)

    def test_a_near_miss_secret_is_still_refused(self):
        response = self.client.get(
            '/api/arcade/events/pending', HTTP_X_BOT_SECRET=BOT_SECRET[:-1]
        )
        self.assertEqual(response.status_code, 403)


class TokenExchangeTests(ApiMixin, TestCase):
    def test_a_launch_returns_an_identity_the_player_can_reuse(self):
        with mock.patch.object(discord_api, 'exchange_code', return_value={'access_token': 'at'}), \
             mock.patch.object(discord_api, 'current_user',
                               return_value={'id': '555', 'global_name': 'Howard'}):
            response = self.client.post('/api/arcade/token', {'code': 'abc'}, content_type='application/json')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['player'], {'discord_user_id': '555', 'display_name': 'Howard'})
        self.assertEqual(play.read_identity(body['identity']), ('555', 'Howard'))

    def test_discord_being_down_is_a_503_not_a_rejection(self):
        with mock.patch.object(discord_api, 'exchange_code',
                               side_effect=discord_api.DiscordUnavailable('timeout')):
            response = self.client.post('/api/arcade/token', {'code': 'abc'}, content_type='application/json')

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'discord_unavailable')

    def test_a_bad_code_is_a_400(self):
        with mock.patch.object(discord_api, 'exchange_code',
                               side_effect=discord_api.DiscordError('invalid_grant', status=400)):
            response = self.client.post('/api/arcade/token', {'code': 'nope'}, content_type='application/json')

        self.assertEqual(response.status_code, 400)

    def test_a_missing_code_is_rejected_before_discord_is_called(self):
        with mock.patch.object(discord_api, 'exchange_code') as called:
            response = self.client.post('/api/arcade/token', {}, content_type='application/json')

        self.assertEqual(response.status_code, 400)
        called.assert_not_called()


class GateEndpointTests(ApiMixin, TestCase):
    def post_session(self, instance, user='player'):
        with mock.patch.object(discord_api, 'activity_instance', return_value=instance):
            return self.client.post(
                '/api/arcade/sessions',
                {'game': self.game.slug, 'instance_id': 'inst-1'},
                content_type='application/json',
                **self.identity(user),
            )

    def test_playing_in_the_arcade_channel_gets_a_session(self):
        response = self.post_session({'location': HERE, 'users': [{'id': 'player'}]})

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn('session_token', body)
        self.assertTrue(0 <= body['seed'] < 2 ** 32)
        self.assertEqual(PlaySession.objects.count(), 1)

    def test_another_server_gets_the_apply_message(self):
        response = self.post_session(
            {'location': {'guild_id': '999', 'channel_id': CHANNEL}, 'users': [{'id': 'player'}]}
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'wrong_guild')
        self.assertIn('discord.vicers.net', response.json()['detail'])

    def test_another_channel_gets_pointed_at_the_arcade_channel(self):
        response = self.post_session(
            {'location': {'guild_id': GUILD, 'channel_id': '123'}, 'users': [{'id': 'player'}]}
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'wrong_channel')
        self.assertIn('#vice-arcade', response.json()['detail'])

    def test_no_identity_means_no_session(self):
        response = self.client.post(
            '/api/arcade/sessions',
            {'game': self.game.slug, 'instance_id': 'inst-1'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_an_expired_identity_is_refused(self):
        token = play.sign_identity('player', 'Player')
        with mock.patch.object(play, 'IDENTITY_TTL_SECONDS', -1):
            response = self.client.post(
                '/api/arcade/sessions',
                {'game': self.game.slug, 'instance_id': 'inst-1'},
                content_type='application/json',
                HTTP_X_ARCADE_IDENTITY=token,
            )
        self.assertEqual(response.status_code, 401)

    def test_the_gate_fails_closed_when_discord_cannot_be_reached(self):
        with mock.patch.object(discord_api, 'activity_instance',
                               side_effect=discord_api.DiscordUnavailable('timeout')):
            response = self.client.post(
                '/api/arcade/sessions',
                {'game': self.game.slug, 'instance_id': 'inst-1'},
                content_type='application/json',
                **self.identity(),
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(PlaySession.objects.count(), 0)

    def test_a_launch_with_no_instance_id_is_refused(self):
        response = self.client.post(
            '/api/arcade/sessions',
            {'game': self.game.slug},
            content_type='application/json',
            **self.identity(),
        )
        self.assertEqual(response.status_code, 403)

    def test_a_locked_cabinet_cannot_be_started(self):
        locked = make_game(slug='locked', display_name='Locked', is_active=False)
        with mock.patch.object(discord_api, 'activity_instance',
                               return_value={'location': HERE, 'users': [{'id': 'player'}]}):
            response = self.client.post(
                '/api/arcade/sessions',
                {'game': locked.slug, 'instance_id': 'inst-1'},
                content_type='application/json',
                **self.identity(),
            )
        self.assertEqual(response.status_code, 404)


class SubmitEndpointTests(ApiMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.season = make_season(timezone.now() - timedelta(hours=2))

    def start(self, user='player'):
        with mock.patch.object(discord_api, 'activity_instance',
                               return_value={'location': HERE, 'users': [{'id': user}]}):
            response = self.client.post(
                '/api/arcade/sessions',
                {'game': self.game.slug, 'instance_id': 'inst-1'},
                content_type='application/json',
                **self.identity(user),
            )
        return response.json()

    def test_a_run_is_banked(self):
        started = self.start()

        response = self.client.post(
            '/api/arcade/scores',
            {
                'session_token': started['session_token'],
                'score': 1200,
                'run_stats': {'pieces_placed': 80, 'duration_ms': 90000, 'seed': started['seed']},
                'input_log': [{'tick': 1, 'input': 'left'}],
            },
            content_type='application/json',
            **self.identity(),
        )

        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json()['pending_review'])
        self.assertEqual(Score.objects.count(), 1)

    def test_a_record_comes_back_marked_pending(self):
        make_score(self.game, 'holder', 400, timezone.now() - timedelta(hours=1))
        started = self.start(user='challenger')

        response = self.client.post(
            '/api/arcade/scores',
            {
                'session_token': started['session_token'],
                'score': 9000,
                'run_stats': {'pieces_placed': 800, 'duration_ms': 900000, 'seed': started['seed']},
            },
            content_type='application/json',
            **self.identity('challenger', 'Challenger'),
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()['pending_review'])
        self.assertEqual(ArcadeEvent.objects.filter(type=EventType.RECORD_PENDING_REVIEW).count(), 1)

    def test_a_refused_submission_explains_itself(self):
        started = self.start()

        response = self.client.post(
            '/api/arcade/scores',
            {
                'session_token': started['session_token'],
                'score': 100,
                'run_stats': {'seed': started['seed'] + 1},
            },
            content_type='application/json',
            **self.identity(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'seed_mismatch')

    def test_a_negative_score_never_reaches_the_engine(self):
        started = self.start()

        response = self.client.post(
            '/api/arcade/scores',
            {'session_token': started['session_token'], 'score': -5},
            content_type='application/json',
            **self.identity(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Score.objects.count(), 0)


class LeaderboardEndpointTests(ApiMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.season = make_season(self.now - timedelta(hours=2))

    def test_all_three_boards_come_back_with_the_reset_time(self):
        make_score(self.game, 'a', 300, self.now - timedelta(minutes=10))
        make_score(self.game, 'b', 200, self.now - timedelta(minutes=5))

        response = self.client.get(f'/api/arcade/leaderboards/{self.game.slug}')
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(body['daily']['leader']['discord_user_id'], 'a')
        self.assertEqual(body['round']['leader']['discord_user_id'], 'a')
        self.assertEqual(body['alltime']['leader']['discord_user_id'], 'a')
        self.assertIn('ends_at', body['season'])
        self.assertIn('resets_in_words', body['season'])

    def test_your_own_rank_comes_back_even_from_outside_the_top_ten(self):
        for index in range(12):
            make_score(self.game, f'p{index}', 500 - index, self.now - timedelta(minutes=10))

        response = self.client.get(
            f'/api/arcade/leaderboards/{self.game.slug}', **self.identity('p11', 'P11')
        )
        body = response.json()

        self.assertEqual(len(body['alltime']['top']), 10)
        self.assertEqual(body['alltime']['you']['place'], 12)

    def test_the_boards_render_without_an_identity(self):
        make_score(self.game, 'a', 300, self.now - timedelta(minutes=10))

        body = self.client.get(f'/api/arcade/leaderboards/{self.game.slug}').json()

        self.assertIsNone(body['alltime']['you'])

    def test_a_pending_score_is_marked_on_the_board(self):
        make_score(self.game, 'cheat', 900, self.now - timedelta(minutes=10),
                   approved=False, flagged=True)

        body = self.client.get(f'/api/arcade/leaderboards/{self.game.slug}').json()

        self.assertTrue(body['alltime']['leader']['pending'])

    def test_an_unknown_game_is_a_404(self):
        self.assertEqual(self.client.get('/api/arcade/leaderboards/nope').status_code, 404)


class EventDrainTests(ApiMixin, TestCase):
    def test_pending_events_come_back_oldest_first_and_ack_once(self):
        first = ArcadeEvent.objects.create(type=EventType.PODIUM_CHANGED, payload={'n': 1})
        second = ArcadeEvent.objects.create(type=EventType.SEASON_CLOSING, payload={'n': 2})

        listed = self.client.get('/api/arcade/events/pending', **bot_headers()).json()['events']
        self.assertEqual([e['id'] for e in listed], [first.id, second.id])

        self.client.post(f'/api/arcade/events/{first.id}/ack', **bot_headers())

        remaining = self.client.get('/api/arcade/events/pending', **bot_headers()).json()['events']
        self.assertEqual([e['id'] for e in remaining], [second.id])

    def test_acking_twice_keeps_the_first_handled_time(self):
        event = ArcadeEvent.objects.create(type=EventType.PODIUM_CHANGED, payload={})

        first = self.client.post(f'/api/arcade/events/{event.id}/ack', **bot_headers()).json()
        second = self.client.post(f'/api/arcade/events/{event.id}/ack', **bot_headers()).json()

        self.assertEqual(first['handled_at'], second['handled_at'])

    def test_the_poll_is_what_advances_the_clock(self):
        from arcade.models import ArcadeSeason

        self.assertEqual(ArcadeSeason.objects.count(), 0)
        self.client.get('/api/arcade/events/pending', **bot_headers())
        self.assertEqual(ArcadeSeason.objects.count(), 1)


class SeasonEndpointTests(ApiMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.season = make_season(self.now - timedelta(days=9), status=SeasonStatus.CLOSING)
        make_score(self.game, 'a', 300, self.now - timedelta(days=8))
        make_score(self.game, 'b', 200, self.now - timedelta(days=8))

    def test_the_bot_can_preview_the_winners_before_anything_is_announced(self):
        body = self.client.get(
            f'/api/arcade/seasons/{self.season.id}/preview', **bot_headers()
        ).json()

        self.assertEqual([row['discord_user_id'] for row in body['top']], ['a', 'b'])
        self.assertEqual(body['season']['status'], 'closing')
        self.assertEqual(ArcadeEvent.objects.filter(type=EventType.SEASON_AWARDED).count(), 0)

    def test_finalizing_stores_the_podium_and_announces(self):
        response = self.client.post(
            f'/api/arcade/seasons/{self.season.id}/finalize',
            {'winners': [
                {'discord_user_id': 'a', 'display_name': 'A', 'average_placement': 1.0},
                {'discord_user_id': 'b', 'display_name': 'B', 'average_placement': 2.0},
            ]},
            content_type='application/json',
            **bot_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['announced'])
        self.assertEqual(response.json()['status'], 'finalized')

    def test_a_closing_round_shows_up_in_the_queue(self):
        body = self.client.get('/api/arcade/seasons/closing', **bot_headers()).json()
        self.assertIn(self.season.id, [s['id'] for s in body['seasons']])


class DepartureEndpointTests(ApiMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.season = make_season(self.now - timedelta(hours=2))

    def test_a_reported_departure_clears_their_scores(self):
        make_score(self.game, 'gone', 500, self.now - timedelta(hours=1))
        make_score(self.game, 'stays', 100, self.now - timedelta(hours=1))

        response = self.client.post(
            '/api/arcade/members/departed',
            {'departures': [{'discord_user_id': 'gone', 'left_at': self.now.isoformat()}]},
            content_type='application/json',
            **bot_headers(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['scores_removed'], 1)
        self.assertEqual(
            self.client.get('/api/arcade/players/active', **bot_headers()).json()['discord_user_ids'],
            ['stays'],
        )

    def test_a_purge_sized_batch_is_one_call(self):
        for index in range(5):
            make_score(self.game, f'gone{index}', 100 + index, self.now - timedelta(hours=1))

        response = self.client.post(
            '/api/arcade/members/departed',
            {'departures': [
                {'discord_user_id': f'gone{index}', 'left_at': self.now.isoformat()}
                for index in range(5)
            ]},
            content_type='application/json',
            **bot_headers(),
        )

        self.assertEqual(response.json()['processed'], 5)
        self.assertEqual(response.json()['scores_removed'], 5)

    def test_a_malformed_batch_removes_nothing(self):
        make_score(self.game, 'gone', 500, self.now - timedelta(hours=1))

        response = self.client.post(
            '/api/arcade/members/departed',
            {'departures': [{'discord_user_id': 'gone'}]},
            content_type='application/json',
            **bot_headers(),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Score.objects.filter(removed=True).count(), 0)


class ReviewEndpointTests(ApiMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.now = timezone.now()
        self.season = make_season(self.now - timedelta(hours=2))

    def test_approving_a_record_announces_it(self):
        make_score(self.game, 'holder', 400, self.now - timedelta(hours=1))
        record = make_score(self.game, 'new', 900, self.now - timedelta(minutes=30),
                            approved=False, flagged=True)

        response = self.client.post(
            f'/api/arcade/scores/{record.id}/approve', **bot_headers()
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['announced'])
        self.assertEqual(ArcadeEvent.objects.filter(type=EventType.NEW_ALLTIME_RECORD).count(), 1)

    def test_removing_a_score_takes_it_off_the_board(self):
        keep = make_score(self.game, 'keeper', 100, self.now - timedelta(hours=1))
        drop = make_score(self.game, 'cheat', 900, self.now - timedelta(hours=1))

        self.client.post(f'/api/arcade/scores/{drop.id}/remove', **bot_headers())

        body = self.client.get(f'/api/arcade/leaderboards/{self.game.slug}').json()
        self.assertEqual(body['alltime']['leader']['discord_user_id'], 'keeper')


class DailyBoardEndpointTests(ApiMixin, TestCase):
    def test_a_closed_board_can_be_fetched_and_its_messages_recorded(self):
        from arcade import dailyboards
        from arcade import seasons as season_utils

        yesterday = timezone.now() - timedelta(days=1)
        make_score(self.game, 'a', 300, yesterday)
        dailyboards.finalize_due_boards(timezone.now())
        board = DailyBoard.objects.get()

        day = board.day.isoformat()
        response = self.client.get(
            f'/api/arcade/daily/{self.game.slug}/{day}', **bot_headers()
        )
        self.assertEqual(response.status_code, 200)
        fetched = response.json()

        self.assertEqual(fetched['entries'][0]['discord_user_id'], 'a')
        self.assertIn('resets_at', fetched)

        recorded = self.client.post(
            f'/api/arcade/daily/{board.id}/posted',
            {'message_ids': ['123', '124']},
            content_type='application/json',
            **bot_headers(),
        )
        self.assertEqual(recorded.status_code, 200)

        board.refresh_from_db()
        self.assertEqual(board.message_ids, ['123', '124'])

    def test_the_posted_route_is_not_shadowed_by_the_fetch_route(self):
        # Guards the url ordering: a numeric board id plus "posted" also matches
        # the game-and-day pattern, which would answer 404 forever.
        from django.urls import resolve

        self.assertEqual(resolve('/api/arcade/daily/5/posted').url_name, 'arcade-daily-posted')
        self.assertEqual(resolve('/api/arcade/daily/vetrix/2026-11-10').url_name, 'arcade-daily')
