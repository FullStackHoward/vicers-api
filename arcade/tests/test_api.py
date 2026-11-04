import os
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from arcade import discord_api, play
from arcade.models import ArcadeAppearance, ArcadeEvent, DailyBoard, EventType, PlaySession, Score, SeasonStatus
from arcade.tests.factories import eastern, make_game, make_score, make_season
from arcade.tests.test_play import ARCADE_ENV, GUILD

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


REDIRECT = 'https://arcade.vicers.net/'
MEMBER = {'user': {'id': 'player'}, 'pending': False}
NOT_A_MEMBER = discord_api.DiscordError('Unknown Member', status=404, code=discord_api.UNKNOWN_MEMBER)


class TokenExchangeTests(ApiMixin, TestCase):
    def sign_in(self, body=None, member=MEMBER, user=None, **exchange):
        exchange = exchange or {'return_value': {'access_token': 'at'}}
        lookup = {'side_effect': member} if isinstance(member, Exception) else {'return_value': member}

        with mock.patch.object(discord_api, 'exchange_code', **exchange) as exchanged, \
             mock.patch.object(discord_api, 'current_user',
                               return_value=user or {'id': '555', 'global_name': 'Howard', 'avatar': 'abc'}), \
             mock.patch.object(discord_api, 'guild_member', **lookup):
            response = self.client.post(
                '/api/arcade/token',
                body if body is not None else {'code': 'abc', 'redirect_uri': REDIRECT},
                content_type='application/json',
            )
        return response, exchanged

    def test_a_member_gets_an_identity_they_can_reuse(self):
        response, exchanged = self.sign_in()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['player']['discord_user_id'], '555')
        self.assertEqual(body['player']['display_name'], 'Howard')
        self.assertEqual(body['player']['avatar_url'], 'https://cdn.discordapp.com/avatars/555/abc.png?size=64')
        self.assertEqual(play.read_identity(body['identity']), ('555', 'Howard'))
        exchanged.assert_called_once_with('abc', REDIRECT)

    def test_discords_access_token_never_reaches_the_browser(self):
        response, _ = self.sign_in()

        self.assertNotIn('access_token', response.json())
        self.assertNotIn('"at"', response.content.decode())

    def test_someone_outside_vice_gamers_is_turned_away_at_sign_in(self):
        response, _ = self.sign_in(member=NOT_A_MEMBER)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'wrong_guild')
        self.assertIn('discord.vicers.net', response.json()['detail'])

    def test_a_redirect_that_is_not_the_arcade_is_refused_before_discord_is_called(self):
        response, exchanged = self.sign_in({'code': 'abc', 'redirect_uri': 'https://evil.example/'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'bad_redirect')
        exchanged.assert_not_called()

    def test_discord_being_down_is_a_503_not_a_rejection(self):
        response, _ = self.sign_in(side_effect=discord_api.DiscordUnavailable('timeout'))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'discord_unavailable')

    def test_a_bad_code_is_a_400(self):
        response, _ = self.sign_in(side_effect=discord_api.DiscordError('invalid_grant', status=400))

        self.assertEqual(response.status_code, 400)

    def test_a_missing_code_is_rejected_before_discord_is_called(self):
        response, exchanged = self.sign_in({'redirect_uri': REDIRECT})

        self.assertEqual(response.status_code, 400)
        exchanged.assert_not_called()


class GateEndpointTests(ApiMixin, TestCase):
    def post_session(self, member=MEMBER, game=None, headers=None):
        lookup = {'side_effect': member} if isinstance(member, Exception) else {'return_value': member}

        with mock.patch.object(discord_api, 'guild_member', **lookup):
            return self.client.post(
                '/api/arcade/sessions',
                {'game': game or self.game.slug},
                content_type='application/json',
                **(self.identity() if headers is None else headers),
            )

    def test_a_member_gets_a_session(self):
        response = self.post_session()

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn('session_token', body)
        self.assertTrue(0 <= body['seed'] < 2 ** 32)
        self.assertEqual(PlaySession.objects.get().guild_id, GUILD)

    def test_someone_who_has_left_since_signing_in_gets_the_apply_message(self):
        response = self.post_session(member=NOT_A_MEMBER)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'wrong_guild')
        self.assertEqual(PlaySession.objects.count(), 0)

    def test_a_member_still_on_the_rules_screen_is_told_to_finish_joining(self):
        response = self.post_session(member={'user': {'id': 'player'}, 'pending': True})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['error'], 'member_pending')

    def test_no_identity_means_no_session(self):
        response = self.post_session(headers={})
        self.assertEqual(response.status_code, 401)

    def test_an_expired_identity_is_refused(self):
        token = play.sign_identity('player', 'Player')
        with mock.patch.object(play, 'IDENTITY_TTL_SECONDS', -1):
            response = self.post_session(headers={'HTTP_X_ARCADE_IDENTITY': token})
        self.assertEqual(response.status_code, 401)

    def test_the_gate_fails_closed_when_discord_cannot_be_reached(self):
        response = self.post_session(member=discord_api.DiscordUnavailable('timeout'))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(PlaySession.objects.count(), 0)

    def test_a_locked_cabinet_cannot_be_started(self):
        locked = make_game(slug='locked', display_name='Locked', is_active=False)
        response = self.post_session(game=locked.slug)
        self.assertEqual(response.status_code, 404)


class SubmitEndpointTests(ApiMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.season = make_season(timezone.now() - timedelta(hours=2))

    def start(self, user='player'):
        with mock.patch.object(discord_api, 'guild_member',
                               return_value={'user': {'id': user}, 'pending': False}):
            response = self.client.post(
                '/api/arcade/sessions',
                {'game': self.game.slug},
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
        self.assertNotIn('pending_review', response.json())
        self.assertEqual(Score.objects.count(), 1)

    def test_a_record_is_posted_straight_away_with_no_review(self):
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
        self.assertEqual(ArcadeEvent.objects.filter(type=EventType.NEW_ALLTIME_RECORD).count(), 1)
        self.assertEqual(ArcadeEvent.objects.filter(type='record_pending_review').count(), 0)

        body = self.client.get(f'/api/arcade/leaderboards/{self.game.slug}').json()
        self.assertEqual(body['alltime']['leader']['discord_user_id'], 'challenger')
        self.assertNotIn('pending', body['alltime']['leader'])

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

    def test_there_is_no_approve_endpoint_any_more(self):
        record = make_score(self.game, 'new', 900, self.now - timedelta(minutes=30))

        response = self.client.post(
            f'/api/arcade/scores/{record.id}/approve', **bot_headers()
        )

        self.assertEqual(response.status_code, 404)

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


# One pixel of PNG, enough for Pillow to accept the upload as an image.
PIXEL_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06'
    b'\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00'
    b'\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
)


class AppearanceEndpointTests(ApiMixin, TestCase):
    def setUp(self):
        super().setUp()
        media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)

    def test_with_nothing_uploaded_every_image_is_null_and_the_page_keeps_its_own_look(self):
        response = self.client.get('/api/arcade/appearance')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNone(body['background_url'])
        self.assertIsNone(body['logo_url'])
        self.assertEqual(body['tagline'], ArcadeAppearance.DEFAULT_TAGLINE)
        self.assertEqual(body['games'][self.game.slug]['thumbnail_url'], None)

    def test_uploaded_images_come_back_as_absolute_urls(self):
        look = ArcadeAppearance.load()
        look.background_image = SimpleUploadedFile('night.png', PIXEL_PNG, content_type='image/png')
        look.save()
        self.game.thumbnail = SimpleUploadedFile('vetrix.png', PIXEL_PNG, content_type='image/png')
        self.game.save()

        body = self.client.get('/api/arcade/appearance').json()

        self.assertTrue(body['background_url'].startswith('http://testserver/media/arcade/appearance/night'))
        self.assertTrue(body['games'][self.game.slug]['thumbnail_url'].startswith(
            'http://testserver/media/arcade/thumbnails/vetrix'))

    def test_it_needs_no_sign_in(self):
        self.assertEqual(self.client.get('/api/arcade/appearance').status_code, 200)

    def test_an_inactive_game_is_left_out(self):
        make_game(slug='hidden', display_name='Hidden', is_active=False)

        self.assertNotIn('hidden', self.client.get('/api/arcade/appearance').json()['games'])

    def test_the_appearance_row_is_a_singleton(self):
        ArcadeAppearance(tagline='One').save()
        ArcadeAppearance(tagline='Two').save()

        self.assertEqual(ArcadeAppearance.objects.count(), 1)
        self.assertEqual(ArcadeAppearance.load().tagline, 'Two')

    def test_an_oversized_image_is_refused_with_a_reason(self):
        from django.core.exceptions import ValidationError
        from arcade.models import MAX_IMAGE_BYTES, limit_image_size

        big = SimpleUploadedFile('huge.png', b'0' * (MAX_IMAGE_BYTES + 1), content_type='image/png')

        with self.assertRaises(ValidationError):
            limit_image_size(big)


class CorsTests(ApiMixin, TestCase):
    def test_the_browser_may_send_the_identity_header_from_the_arcade_page(self):
        # Without this, the preflight refuses the header, the browser never sends
        # the request, and the page can sign in but never start a run.
        response = self.client.options(
            '/api/arcade/sessions',
            HTTP_ORIGIN='http://localhost:5173',
            HTTP_ACCESS_CONTROL_REQUEST_METHOD='POST',
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS='content-type,x-arcade-identity',
        )

        allowed = response.headers.get('access-control-allow-headers', '')
        self.assertIn('x-arcade-identity', allowed)
        self.assertIn('content-type', allowed)
