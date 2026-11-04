"""
The owner's /arcade command and the opt-in first-place DMs.

What matters most: a wipe clears only the boards it names and deletes nothing; a
reset clears everything but the players' settings; the channel is emptied before
anything new is posted in it; only the server owner can do any of it, and only
with a request Discord really signed; and a DM goes only to a player who asked
for one, and not over and over.
"""

import json
import os
import time
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from nacl.signing import SigningKey

from arcade import alerts, announce, discord_api, interactions, leaderboards, play, wipes
from arcade.models import (
    ArcadeAlertOptIn,
    ArcadeCallout,
    ArcadeEvent,
    ArcadeSeason,
    BoardWipe,
    ChannelPurge,
    DailyBoard,
    EventType,
    PersonalBest,
    PlayerAlert,
    Score,
)
from arcade.tests.factories import eastern, make_game, make_score, make_season, only_games
from arcade.tests.test_play import ARCADE_ENV, GUILD

CHANNEL = '800000000000000011'
OWNER = '100000000000000001'
ADMIN = '100000000000000002'
APP = '1567002859785031690'

SIGNING_KEY = SigningKey.generate()
PUBLIC_KEY = SIGNING_KEY.verify_key.encode().hex()


class CommandMixin:
    channel_id = ''

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {
            **ARCADE_ENV,
            'VG_ARCADE_CHANNEL_ID': self.channel_id,
            'VICE_ARCADE_PUBLIC_KEY': PUBLIC_KEY,
        })
        env.start()
        self.addCleanup(env.stop)

        self.game = make_game()
        only_games(self.game)
        # Noon Eastern on a Tuesday, inside a season that started on Sunday.
        self.now = eastern(2026, 11, 10, 12, 0)
        self.season = make_season(eastern(2026, 11, 8, 17, 0))
        interactions._owner_cache.update(guild=None, owner=None, fetched=0.0)

    def runs(self):
        """Yesterday's champion, and two runs today."""
        make_score(self.game, 'ace', 900, self.now - timedelta(days=1), name='Ace')
        make_score(self.game, 'bea', 500, self.now - timedelta(hours=2), name='Bea')
        make_score(self.game, 'cal', 300, self.now - timedelta(hours=1), name='Cal')

    def board(self, which):
        today = self.now.date()
        rows = {
            'daily': lambda: leaderboards.daily_rows(self.game, today),
            'season': lambda: leaderboards.season_rows(self.game, self.season),
            'alltime': lambda: leaderboards.alltime_rows(self.game),
        }[which]()
        return [row['discord_user_id'] for row in rows]


# ---------------------------------------------------------------------------
# Wipes and the reset
# ---------------------------------------------------------------------------

class WipeTests(CommandMixin, TestCase):
    def test_wiping_today_clears_today_only_and_deletes_nothing(self):
        self.runs()

        result = wipes.wipe_today(by=OWNER, now=self.now)

        self.assertEqual(result['players'], 2)
        self.assertEqual(self.board('daily'), [])
        self.assertEqual(self.board('season'), ['ace', 'bea', 'cal'])
        self.assertEqual(self.board('alltime'), ['ace', 'bea', 'cal'])
        self.assertEqual(Score.objects.count(), 3)
        self.assertEqual(BoardWipe.objects.get().wiped_by, OWNER)

    def test_a_run_after_the_wipe_is_on_today_again(self):
        self.runs()
        wipes.wipe_today(now=self.now)

        make_score(self.game, 'dee', 50, self.now + timedelta(minutes=5))

        self.assertEqual(self.board('daily'), ['dee'])

    def test_a_wipe_only_lasts_the_day_it_was_made(self):
        self.runs()
        wipes.wipe_today(now=self.now)

        yesterday = (self.now - timedelta(days=1)).date()
        self.assertEqual(
            [row['discord_user_id'] for row in leaderboards.daily_rows(self.game, yesterday)],
            ['ace'],
        )

    def test_wiping_the_season_clears_today_too_but_not_all_time(self):
        self.runs()

        result = wipes.wipe_season(by=OWNER, now=self.now)

        self.assertEqual(result['players'], 3)
        self.assertEqual(self.board('season'), [])
        self.assertEqual(self.board('daily'), [])
        self.assertEqual(self.board('alltime'), ['ace', 'bea', 'cal'])
        self.assertEqual(PersonalBest.objects.count(), 0, 'personal bests are not touched (none were stored)')

    def test_wiping_the_season_takes_the_roles_off(self):
        self.runs()
        from arcade import podium
        podium.refresh(self.season)
        ArcadeEvent.objects.all().delete()

        wipes.wipe_season(now=self.now)

        event = ArcadeEvent.objects.get(type=EventType.PODIUM_CHANGED)
        self.assertEqual(event.payload['top'], [])

    def test_the_midnight_board_for_a_wiped_day_only_has_later_runs(self):
        self.runs()
        wipes.wipe_today(now=self.now)
        make_score(self.game, 'dee', 50, self.now + timedelta(minutes=5))

        from arcade import dailyboards
        dailyboards.finalize_due_boards(self.now + timedelta(days=1))

        board = DailyBoard.objects.get(day=self.now.date())
        self.assertEqual([entry['discord_user_id'] for entry in board.entries], ['dee'])


class ResetTests(CommandMixin, TestCase):
    channel_id = CHANNEL

    def test_reset_deletes_every_score_board_and_season(self):
        self.runs()
        for user in ('ace', 'bea', 'cal'):
            leaderboards.recompute_personal_best(user, self.game)
        DailyBoard.objects.create(game=self.game, day=self.now.date() - timedelta(days=1), entries=[])
        ArcadeAlertOptIn.objects.create(discord_user_id='ace', enabled=True)

        result = wipes.reset_everything(by=OWNER, now=self.now)

        self.assertEqual(result['runs'], 3)
        self.assertEqual(Score.objects.count(), 0)
        self.assertEqual(PersonalBest.objects.count(), 0)
        self.assertEqual(DailyBoard.objects.count(), 0)
        self.assertEqual(ArcadeSeason.objects.get(), result['season'])
        self.assertTrue(ArcadeAlertOptIn.objects.get(discord_user_id='ace').enabled, 'settings are kept')

    def test_the_fresh_season_starts_now_and_runs_a_full_interval_to_5_pm(self):
        result = wipes.reset_everything(now=self.now)

        self.assertEqual(result['season'].starts_at, self.now)
        self.assertEqual(result['season'].ends_at, eastern(2026, 11, 17, 17, 0))

    def test_reset_drops_what_the_bot_was_about_to_post_and_takes_the_roles_off(self):
        ArcadeEvent.objects.create(type=EventType.SEASON_CLOSING, payload={'season_id': self.season.id})

        wipes.reset_everything(now=self.now)

        pending = ArcadeEvent.objects.filter(handled_at__isnull=True)
        self.assertEqual([event.type for event in pending], [EventType.PODIUM_CHANGED])
        self.assertEqual(pending[0].payload['top'], [])

    def test_reset_queues_the_channel_to_be_emptied_and_forgets_the_callout(self):
        callout = ArcadeCallout.load()
        callout.message_id = 'm1'
        callout.save()

        result = wipes.reset_everything(now=self.now, application_id=APP, interaction_token='tok')

        purge = result['purge']
        self.assertEqual(purge.before_message_id, wipes.snowflake_at(self.now))
        self.assertEqual(purge.interaction_token, 'tok')
        self.assertEqual(ArcadeCallout.load().message_id, '')

    def test_without_an_arcade_channel_nothing_is_queued_to_empty(self):
        with mock.patch.dict(os.environ, {'VG_ARCADE_CHANNEL_ID': ''}):
            result = wipes.reset_everything(now=self.now)

        self.assertIsNone(result['purge'])
        self.assertEqual(ChannelPurge.objects.count(), 0)


# ---------------------------------------------------------------------------
# Emptying the channel
# ---------------------------------------------------------------------------

class FakeHistory:
    """A channel's history by real snowflake, and the reply to the owner."""

    def __init__(self, now):
        self.now = now
        self.messages = {}
        self.calls = []
        self.reply = None
        self.refuse_with = None
        self.posted = []

    def add(self, age):
        message_id = str(int(wipes.snowflake_at(self.now - age)) + len(self.messages) + 1)
        self.messages[message_id] = True
        return message_id

    def _refuse(self):
        if self.refuse_with:
            raise self.refuse_with

    def list_messages(self, channel_id, before, limit=100):
        self._refuse()
        older = sorted((i for i in self.messages if int(i) < int(before)), key=int, reverse=True)
        return [{'id': i} for i in older[:limit]]

    def bulk_delete(self, channel_id, ids):
        self._refuse()
        self.calls.append(('bulk', len(ids)))
        for message_id in ids:
            self.messages.pop(message_id)

    def delete_message(self, channel_id, message_id):
        self._refuse()
        self.calls.append(('delete', message_id))
        self.messages.pop(message_id, None)

    def edit_interaction_reply(self, application_id, token, payload):
        self.reply = payload['content']

    def create_message(self, channel_id, payload):
        message_id = str(10 ** 19 + len(self.posted))
        self.posted.append(payload)
        return {'id': message_id, 'pinned': True}


class PurgeTests(CommandMixin, TestCase):
    channel_id = CHANNEL

    def setUp(self):
        super().setUp()
        self.history = FakeHistory(self.now)
        patcher = mock.patch.multiple(
            discord_api,
            list_messages=self.history.list_messages,
            bulk_delete=self.history.bulk_delete,
            delete_message=self.history.delete_message,
            edit_interaction_reply=self.history.edit_interaction_reply,
            create_message=self.history.create_message,
            get_channel=lambda channel_id: {'last_message_id': None},
            pin_message=lambda channel_id, message_id: None,
            guild_emojis=lambda guild_id: [],
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        announce._icon_cache.update(url=None, fetched=0.0)

    def test_recent_messages_go_in_bulk_and_old_ones_one_by_one(self):
        recent = [self.history.add(timedelta(hours=hours)) for hours in (1, 2, 3)]
        old = self.history.add(timedelta(days=20))
        wipes.reset_everything(now=self.now, application_id=APP, interaction_token='tok')

        done = announce.purge_channel(CHANNEL, self.now)

        self.assertTrue(done)
        self.assertEqual(self.history.messages, {})
        self.assertIn(('bulk', len(recent)), self.history.calls)
        self.assertIn(('delete', old), self.history.calls)
        purge = ChannelPurge.objects.get()
        self.assertEqual(purge.deleted, 4)
        self.assertIsNotNone(purge.finished_at)
        self.assertEqual(purge.interaction_token, '', 'the reply token is forgotten once used')
        self.assertIn('Deleted 4 messages', self.history.reply)

    def test_messages_after_the_reset_are_left_alone(self):
        self.history.add(timedelta(hours=1))
        wipes.reset_everything(now=self.now)
        after = str(int(wipes.snowflake_at(self.now + timedelta(seconds=5))))
        self.history.messages[after] = True

        announce.purge_channel(CHANNEL, self.now)

        self.assertEqual(list(self.history.messages), [after])

    def test_nothing_is_posted_until_the_channel_is_clear(self):
        self.history.add(timedelta(hours=1))
        wipes.reset_everything(now=self.now)
        self.history.refuse_with = discord_api.DiscordError('rate limited', status=429)

        announce.tick(now=self.now)

        self.assertEqual(self.history.posted, [])
        self.assertIsNone(ChannelPurge.objects.get().finished_at, 'tried again next tick')

        self.history.refuse_with = None
        announce.tick(now=self.now)

        self.assertEqual(self.history.messages, {})
        self.assertEqual(len(self.history.posted), 1, 'a fresh pinned post goes up')
        self.assertEqual(ArcadeCallout.load().message_id, str(10 ** 19))

    def test_a_missing_permission_stops_the_purge_and_tells_the_owner(self):
        self.history.add(timedelta(hours=1))
        wipes.reset_everything(now=self.now, application_id=APP, interaction_token='tok')
        self.history.refuse_with = discord_api.DiscordError('Missing Permissions', status=403, code=50013)

        done = announce.purge_channel(CHANNEL, self.now)

        self.assertTrue(done, 'posting carries on rather than waiting forever')
        purge = ChannelPurge.objects.get()
        self.assertIn('Manage Messages', purge.last_error)
        self.assertIn('could not be cleared', self.history.reply)


# ---------------------------------------------------------------------------
# First-place alerts
# ---------------------------------------------------------------------------

class AlertQueueTests(CommandMixin, TestCase):
    def bank(self, user, value, name=None):
        leaders = alerts.leaders_before(self.game, self.season)
        saved = make_score(self.game, user, value, self.now, name=name)
        return alerts.queue(saved, leaders)

    def test_taking_first_place_alerts_a_player_who_opted_in(self):
        make_score(self.game, 'ace', 500, self.now - timedelta(hours=1))
        ArcadeAlertOptIn.objects.create(discord_user_id='ace', enabled=True)

        queued = self.bank('bea', 600, name='Bea')

        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0].discord_user_id, 'ace')
        self.assertEqual(queued[0].payload, {
            'boards': ['season', 'alltime'], 'their_score': 500, 'by': 'Bea', 'score': 600,
        })

    def test_nobody_is_alerted_who_did_not_opt_in(self):
        make_score(self.game, 'ace', 500, self.now - timedelta(hours=1))
        ArcadeAlertOptIn.objects.create(discord_user_id='ace', enabled=False)

        self.assertEqual(self.bank('bea', 600), [])

    def test_equalling_first_place_does_not_take_it(self):
        make_score(self.game, 'ace', 500, self.now - timedelta(hours=1))
        ArcadeAlertOptIn.objects.create(discord_user_id='ace', enabled=True)

        self.assertEqual(self.bank('bea', 500), [])

    def test_beating_your_own_first_place_alerts_nobody(self):
        make_score(self.game, 'ace', 500, self.now - timedelta(hours=1))
        ArcadeAlertOptIn.objects.create(discord_user_id='ace', enabled=True)

        self.assertEqual(self.bank('ace', 600), [])

    def test_the_season_and_all_time_leaders_can_be_different_players(self):
        make_score(self.game, 'old', 900, eastern(2026, 10, 1, 12, 0))
        make_score(self.game, 'ace', 500, self.now - timedelta(hours=1))
        ArcadeAlertOptIn.objects.create(discord_user_id='old', enabled=True)
        ArcadeAlertOptIn.objects.create(discord_user_id='ace', enabled=True)

        queued = self.bank('bea', 600)

        self.assertEqual([(alert.discord_user_id, alert.payload['boards']) for alert in queued], [('ace', ['season'])])

    def test_a_banked_score_queues_the_alert(self):
        make_score(self.game, 'ace', 500, self.now - timedelta(hours=1))
        ArcadeAlertOptIn.objects.create(discord_user_id='ace', enabled=True)
        with mock.patch.object(discord_api, 'guild_member', return_value={'pending': False}):
            location = play.confirm_member('bea')
        session, token = play.issue_session('bea', self.game, location, now=self.now)

        play.submit_score(token, 'bea', 'Bea', 600, {'seed': session.seed}, now=self.now)

        self.assertEqual(PlayerAlert.objects.get().discord_user_id, 'ace')


class AlertSendTests(CommandMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.sent = []
        self.closed = set()

        def open_dm(user):
            if user in self.closed:
                raise discord_api.DiscordError('Cannot send messages to this user', status=403, code=discord_api.CANNOT_DM)
            return {'id': f'dm-{user}'}

        patcher = mock.patch.multiple(
            discord_api,
            open_dm=open_dm,
            create_message=lambda channel_id, payload: self.sent.append((channel_id, payload)) or {'id': 'x'},
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def alert(self, user='ace', minutes_ago=1):
        ArcadeAlertOptIn.objects.update_or_create(discord_user_id=user, defaults={'enabled': True})
        alert = PlayerAlert.objects.create(
            discord_user_id=user,
            game=self.game,
            payload={'boards': ['season'], 'their_score': 500, 'by': 'Bea', 'score': 600},
        )
        PlayerAlert.objects.filter(pk=alert.pk).update(created_at=self.now - timedelta(minutes=minutes_ago))
        return alert

    def test_an_alert_is_sent_as_a_dm(self):
        self.alert()

        alerts.send_waiting(self.now)

        channel, payload = self.sent[0]
        self.assertEqual(channel, 'dm-ace')
        text = payload['embeds'][0]['description']
        self.assertIn('Your first place on Vetrix was just taken', text)
        self.assertIn('**Bea** scored **600** on This Season, passing your 500', text)
        self.assertEqual(payload['allowed_mentions'], {'parse': []})

    def test_one_dm_per_game_per_cooldown(self):
        self.alert()
        alerts.send_waiting(self.now)
        second = self.alert()

        alerts.send_waiting(self.now)

        second.refresh_from_db()
        self.assertEqual(second.skipped, 'cooldown')
        self.assertEqual(len(self.sent), 1)

    def test_turning_alerts_off_stops_one_already_queued(self):
        alert = self.alert()
        alerts.set_alerts('ace', False)

        alerts.send_waiting(self.now)

        alert.refresh_from_db()
        self.assertEqual(alert.skipped, 'alerts_off')
        self.assertEqual(self.sent, [])

    def test_closed_dms_are_noted_and_not_retried(self):
        alert = self.alert()
        self.closed.add('ace')

        alerts.send_waiting(self.now)

        alert.refresh_from_db()
        self.assertEqual(alert.skipped, 'dms_closed')

    def test_an_old_alert_is_dropped_rather_than_sent_late(self):
        alert = self.alert(minutes_ago=90)

        alerts.send_waiting(self.now)

        alert.refresh_from_db()
        self.assertEqual(alert.skipped, 'stale')


# ---------------------------------------------------------------------------
# The /arcade command over HTTP
# ---------------------------------------------------------------------------

class InteractionTests(CommandMixin, TestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(discord_api, 'get_guild', return_value={'id': GUILD, 'owner_id': OWNER})
        self.get_guild = patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, body, signed_at=None, key=SIGNING_KEY):
        raw = json.dumps(body).encode()
        timestamp = str(int(signed_at if signed_at is not None else time.time()))
        signature = key.sign(timestamp.encode() + raw).signature.hex()
        return self.client.post(
            reverse('arcade-discord-interactions'),
            data=raw,
            content_type='application/json',
            HTTP_X_SIGNATURE_ED25519=signature,
            HTTP_X_SIGNATURE_TIMESTAMP=timestamp,
        )

    def command(self, action, user=OWNER):
        return {
            'type': interactions.APPLICATION_COMMAND,
            'application_id': APP,
            'token': 'tok',
            'guild_id': GUILD,
            'member': {'user': {'id': user}},
            'data': {'name': 'arcade', 'options': [{'type': 1, 'name': action}]},
        }

    def press(self, custom_id, user=OWNER):
        return {
            'type': interactions.MESSAGE_COMPONENT,
            'application_id': APP,
            'token': 'tok',
            'guild_id': GUILD,
            'member': {'user': {'id': user}},
            'data': {'custom_id': custom_id},
        }

    def test_discords_ping_is_answered(self):
        response = self.send({'type': interactions.PING})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'type': interactions.PONG})

    def test_a_request_discord_did_not_sign_is_refused(self):
        response = self.send({'type': interactions.PING}, key=SigningKey.generate())

        self.assertEqual(response.status_code, 401)

    def test_an_old_signed_request_is_refused(self):
        response = self.send({'type': interactions.PING}, signed_at=time.time() - 3600)

        self.assertEqual(response.status_code, 401)

    def test_an_unsigned_request_is_refused(self):
        response = self.client.post(
            reverse('arcade-discord-interactions'),
            data=json.dumps({'type': interactions.PING}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 401)

    def test_an_admin_who_is_not_the_owner_is_turned_away(self):
        response = self.send(self.command('reset', user=ADMIN)).json()

        self.assertEqual(response['data']['content'], 'Only the server owner can use this.')
        self.assertEqual(response['data']['flags'], interactions.EPHEMERAL)
        self.assertNotIn('components', response['data'])

    def test_the_owner_is_asked_to_confirm_privately(self):
        response = self.send(self.command('wipe-today')).json()

        self.assertEqual(response['type'], interactions.CHANNEL_MESSAGE)
        self.assertEqual(response['data']['flags'], interactions.EPHEMERAL)
        self.assertIn('Wipe today\'s boards?', response['data']['content'])
        buttons = response['data']['components'][0]['components']
        self.assertTrue(buttons[0]['custom_id'].startswith('arcade:wipe-today:'))
        self.assertEqual(buttons[1]['custom_id'], 'arcade:cancel')
        self.assertEqual(BoardWipe.objects.count(), 0, 'nothing happens before Confirm')

    def test_confirming_runs_the_wipe(self):
        # Pressed now, on the real clock, so the runs are today's on the real clock.
        from django.utils import timezone
        make_score(self.game, 'bea', 500, timezone.now() - timedelta(seconds=2))
        make_score(self.game, 'cal', 300, timezone.now() - timedelta(seconds=1))
        issued = int(time.time())

        response = self.send(self.press(f'arcade:wipe-today:{issued}')).json()

        self.assertEqual(response['type'], interactions.UPDATE_MESSAGE)
        self.assertIn('Today\'s boards are wiped (2 leaderboard entries cleared)', response['data']['content'])
        self.assertEqual(response['data']['components'], [])
        self.assertEqual(BoardWipe.objects.get().wiped_by, OWNER)

    def test_confirming_a_reset_keeps_the_reply_token_for_the_purge(self):
        with mock.patch.dict(os.environ, {'VG_ARCADE_CHANNEL_ID': CHANNEL}):
            response = self.send(self.press(f'arcade:reset:{int(time.time())}')).json()

        self.assertIn('Everything is reset', response['data']['content'])
        self.assertIn(f'Clearing <#{CHANNEL}> now', response['data']['content'])
        purge = ChannelPurge.objects.get()
        self.assertEqual((purge.application_id, purge.interaction_token), (APP, 'tok'))

    def test_a_confirm_pressed_by_someone_else_does_nothing(self):
        response = self.send(self.press(f'arcade:reset:{int(time.time())}', user=ADMIN)).json()

        self.assertEqual(response['data']['content'], 'Only the server owner can use this.')
        self.assertEqual(ChannelPurge.objects.count(), 0)

    def test_an_old_confirmation_has_expired(self):
        issued = int(time.time()) - 3600

        response = self.send(self.press(f'arcade:wipe-season:{issued}')).json()

        self.assertIn('expired', response['data']['content'])
        self.assertEqual(BoardWipe.objects.count(), 0)

    def test_cancel_changes_nothing(self):
        response = self.send(self.press('arcade:cancel')).json()

        self.assertEqual(response['data']['content'], 'Cancelled. Nothing was changed.')

    def test_another_server_is_turned_away(self):
        body = self.command('reset')
        body['guild_id'] = '999'

        response = self.send(body).json()

        self.assertEqual(response['data']['content'], 'This only works in Vice Gamers.')

    def test_discord_not_answering_the_owner_check_refuses(self):
        self.get_guild.side_effect = discord_api.DiscordUnavailable('timed out')

        response = self.send(self.command('reset')).json()

        self.assertIn('Could not check with Discord', response['data']['content'])


# ---------------------------------------------------------------------------
# The toggle on the arcade page
# ---------------------------------------------------------------------------

class MyAlertsTests(CommandMixin, TestCase):
    def identity(self, user='player'):
        return {'HTTP_X_ARCADE_IDENTITY': play.sign_identity(user, 'Player')}

    def test_alerts_start_off(self):
        response = self.client.get(reverse('arcade-my-alerts'), **self.identity())

        self.assertEqual(response.json(), {'enabled': False})

    def test_a_player_turns_their_own_alerts_on(self):
        response = self.client.put(
            reverse('arcade-my-alerts'),
            data=json.dumps({'enabled': True}),
            content_type='application/json',
            **self.identity(),
        )

        self.assertEqual(response.json(), {'enabled': True})
        self.assertTrue(ArcadeAlertOptIn.objects.get(discord_user_id='player').enabled)

    def test_signing_in_is_required(self):
        response = self.client.get(reverse('arcade-my-alerts'))

        self.assertEqual(response.status_code, 401)


class RegisterCommandTests(CommandMixin, TestCase):
    def run_it(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with mock.patch.multiple(
            discord_api,
            replace_guild_commands=mock.DEFAULT,
            list_commands=mock.DEFAULT,
            delete_command=mock.DEFAULT,
        ) as calls:
            calls['replace_guild_commands'].return_value = [{'name': 'arcade'}]
            calls['list_commands'].return_value = [{'id': '42', 'name': 'arcade', 'type': 4}]
            call_command('arcade_register_commands', *args, stdout=out)
        return out.getvalue(), calls

    def test_registers_arcade_in_vice_gamers_and_only_reports_the_entry_point(self):
        out, calls = self.run_it()

        calls['replace_guild_commands'].assert_called_once_with(
            ARCADE_ENV['VICE_ARCADE_CLIENT_ID'], GUILD, [interactions.COMMAND]
        )
        calls['delete_command'].assert_not_called()
        self.assertIn('--remove-entry-point', out)

    def test_the_entry_point_is_deleted_only_when_asked(self):
        out, calls = self.run_it('--remove-entry-point')

        calls['delete_command'].assert_called_once_with(ARCADE_ENV['VICE_ARCADE_CLIENT_ID'], '42')
        self.assertIn('Deleted the global /arcade Entry Point command', out)
