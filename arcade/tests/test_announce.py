"""
The arcade's own channel posts, against a fake Discord channel.

Two things are worth pinning down. The run posts must not flood the channel: a
player's back-to-back runs are one post that moves to the bottom, never a pile.
And the callout must behave like the LFG board: one copy, pinned, resurfaced once
a day, and a failure to pin or to reach Discord never takes it down or breaks
the poll that drives it.
"""

import os
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from arcade import announce, discord_api
from arcade.models import ArcadeCallout, RunPost
from arcade.tests.factories import eastern, make_game, make_score, only_games
from arcade.tests.test_play import ARCADE_ENV

CHANNEL = '800000000000000011'
EMOJI_ID = '1567397328015327314'
ICON = f'https://cdn.discordapp.com/emojis/{EMOJI_ID}.png?size=128'

# 6:30 PM and 2 PM Eastern on Nov 10, 2026 (EST, UTC-5).
EVENING = datetime(2026, 11, 10, 23, 30, tzinfo=dt_timezone.utc)
AFTERNOON = datetime(2026, 11, 10, 19, 0, tzinfo=dt_timezone.utc)


class FakeChannel:
    """A channel that remembers what happened to it."""

    def __init__(self, pin_fails=False, down=False):
        self.messages = {}
        self.last_message_id = None
        self.log = []
        self.pin_fails = pin_fails
        self.down = down
        self.next_id = 1

    def _check(self):
        if self.down:
            raise discord_api.DiscordUnavailable('timed out')

    def _unknown(self):
        return discord_api.DiscordError('Unknown Message', status=404, code=discord_api.UNKNOWN_MESSAGE)

    def get_channel(self, channel_id):
        self._check()
        return {'id': channel_id, 'last_message_id': self.last_message_id}

    def get_message(self, channel_id, message_id):
        self._check()
        if message_id not in self.messages:
            raise self._unknown()
        return self.messages[message_id]

    def create_message(self, channel_id, payload):
        self._check()
        message_id = f'm{self.next_id}'
        self.next_id += 1
        self.messages[message_id] = {'id': message_id, 'pinned': False, 'payload': payload}
        self.last_message_id = message_id
        self.log.append(('send', message_id))
        return self.messages[message_id]

    def edit_message(self, channel_id, message_id, payload):
        self._check()
        if message_id not in self.messages:
            raise self._unknown()
        self.messages[message_id]['payload'] = payload
        self.log.append(('edit', message_id))
        return self.messages[message_id]

    def delete_message(self, channel_id, message_id):
        self._check()
        self.messages.pop(message_id, None)
        self.log.append(('delete', message_id))

    def pin_message(self, channel_id, message_id):
        self._check()
        if self.pin_fails:
            raise discord_api.DiscordError('Missing Permissions', status=403, code=50013)
        self.messages[message_id]['pinned'] = True
        self.log.append(('pin', message_id))

    def guild_emojis(self, guild_id):
        self._check()
        return [{'name': 'vicearcade', 'id': EMOJI_ID, 'animated': False}]

    def chat(self, text='someone chatting'):
        """A member's message, which is all it takes to push a post up."""
        message_id = f'm{self.next_id}'
        self.next_id += 1
        self.messages[message_id] = {'id': message_id, 'pinned': False, 'payload': {'content': text}}
        self.last_message_id = message_id

    def kinds(self):
        return [kind for kind, _ in self.log]

    def posts(self):
        return [message for message in self.messages.values() if 'embeds' in message['payload']]


def description(message):
    return message['payload']['embeds'][0]['description']


def button(message):
    return message['payload']['components'][0]['components'][0]


class AnnounceMixin:
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {**ARCADE_ENV, 'VG_ARCADE_CHANNEL_ID': CHANNEL})
        env.start()
        self.addCleanup(env.stop)

        announce._icon_cache.update(url=None, fetched=0.0)
        self.game = make_game()
        only_games(self.game)
        # A fixed noon Eastern, never the real clock: a test that puts a score an
        # hour earlier would otherwise land it on yesterday's board just after
        # midnight.
        self.now = eastern(2026, 11, 10, 12, 0)
        self.use(FakeChannel())

    def use(self, channel):
        self.channel = channel
        patcher = mock.patch.multiple(
            discord_api,
            get_channel=channel.get_channel,
            get_message=channel.get_message,
            create_message=channel.create_message,
            edit_message=channel.edit_message,
            delete_message=channel.delete_message,
            pin_message=channel.pin_message,
            guild_emojis=channel.guild_emojis,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_run(self, score=5000, user='u1', name='Howard', previous_best=6000, at=None, **extra):
        at = at or self.now
        saved = make_score(self.game, user, score, at, name=name, **extra)
        post = RunPost.objects.create(score=saved, previous_best=previous_best)
        RunPost.objects.filter(pk=post.pk).update(created_at=at)
        return post

    def post_runs(self, now=None):
        announce.post_runs(CHANNEL, ICON, now or self.now)


# ---------------------------------------------------------------------------
# Run posts
# ---------------------------------------------------------------------------

class TodayLineTests(TestCase):
    def test_today_s_standing_reads_as_words(self):
        self.assertEqual(announce.today_line(1, 1), "The only player on today's board so far.")
        self.assertEqual(announce.today_line(1, 2), "🥇 1st on today's board, ahead of 1 other player.")
        self.assertEqual(announce.today_line(1, 4), "🥇 1st on today's board, ahead of 3 other players.")
        self.assertEqual(announce.today_line(3, 4), "3rd on today's board, out of 4 players.")
        self.assertEqual(announce.today_line(12, 20, best=900), "12th on today's board, out of 20 players (best today: 900).")
        self.assertEqual([announce.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 101, 111)],
                         ['1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd', '101st', '111th'])


class RunPostTests(AnnounceMixin, TestCase):
    def test_a_run_post_says_who_scored_what_and_links_straight_into_that_game(self):
        self.add_run(previous_best=None)
        self.post_runs()

        [post] = self.channel.posts()
        self.assertRegex(description(post), r'^## Howard scored 5,000 on Vetrix')
        self.assertIn('First run on the Vetrix boards.', description(post))
        self.assertIn("The only player on today's board so far.", description(post))
        self.assertIn('Think you can beat it?', description(post))
        self.assertEqual(button(post)['label'], 'Play Vetrix')
        self.assertEqual(button(post)['url'], 'https://arcade.vicers.net/?play=vetrix')
        self.assertEqual(button(post)['style'], 5, 'a link button, so nothing has to handle the click')
        self.assertEqual(post['payload']['embeds'][0]['thumbnail'], {'url': ICON})
        self.assertEqual(post['payload']['allowed_mentions'], {'parse': []}, 'a display name can never ping')

    def test_a_personal_best_says_so_and_today_s_place_counts_everyone(self):
        make_score(self.game, 'u2', 9000, self.now - timedelta(minutes=5), name='Sam')
        self.add_run(score=5000, previous_best=4000)
        self.post_runs()

        text = description(self.channel.posts()[-1])
        self.assertIn('🔥 New personal best, up from 4,000.', text)
        self.assertIn("2nd on today's board, out of 2 players.", text)

    def test_a_run_below_the_player_s_best_today_shows_the_standing_it_came_from(self):
        make_score(self.game, 'u1', 6000, self.now - timedelta(minutes=50), name='Howard')
        self.add_run(score=5000)
        self.post_runs()

        text = description(self.channel.posts()[-1])
        self.assertIn("The only player on today's board so far (best today: 6,000).", text)
        self.assertNotIn('personal best', text)

    def test_a_player_s_next_run_edits_their_post_when_it_is_still_the_newest(self):
        self.add_run(score=3000)
        self.post_runs()
        self.add_run(score=7000)
        self.post_runs()

        self.assertEqual(self.channel.kinds(), ['send', 'edit'])
        [post] = self.channel.posts()
        self.assertIn('2 runs this session, best 7,000.', description(post))

    def test_when_others_have_posted_since_the_post_moves_down_instead_of_piling_up(self):
        first = self.add_run()
        self.post_runs()
        self.channel.chat()
        self.add_run(score=8000)
        self.post_runs()

        first.refresh_from_db()
        self.assertIn(('delete', first.message_id), self.channel.log)
        self.assertEqual(len(self.channel.posts()), 1, 'still one post for this player')
        self.assertEqual(self.channel.last_message_id, self.channel.posts()[0]['id'])

    def test_two_players_each_get_their_own_post(self):
        self.add_run(user='u1')
        self.add_run(user='u2', name='Sam')
        self.post_runs()

        self.assertEqual(len(self.channel.posts()), 2)

    def test_a_run_after_the_session_lapsed_starts_a_new_post_and_leaves_the_old(self):
        self.add_run()
        self.post_runs()
        self.channel.chat()

        later = self.now + timedelta(minutes=31)
        self.add_run(at=later)
        self.post_runs(now=later)

        self.assertNotIn('delete', self.channel.kinds(), 'the earlier session stays as history')
        self.assertEqual(len(self.channel.posts()), 2)
        self.assertNotIn('runs this session', description(self.channel.posts()[-1]))

    def test_runs_that_waited_out_an_outage_are_not_posted_as_if_they_just_happened(self):
        post = self.add_run(at=self.now - timedelta(hours=1))
        self.post_runs()

        post.refresh_from_db()
        self.assertEqual(post.skipped, 'stale')
        self.assertEqual(self.channel.posts(), [])

    def test_a_score_removed_before_its_post_went_out_is_not_posted(self):
        post = self.add_run(removed=True)
        self.post_runs()

        post.refresh_from_db()
        self.assertEqual(post.skipped, 'not_public')
        self.assertEqual(self.channel.posts(), [])

    def test_with_discord_down_runs_wait_for_the_next_poll(self):
        post = self.add_run()
        self.channel.down = True

        announce.tick(self.now)

        post.refresh_from_db()
        self.assertIsNone(post.handled_at)

        self.channel.down = False
        announce._icon_cache.update(url=None, fetched=0.0)
        announce.tick(self.now + timedelta(seconds=30))

        post.refresh_from_db()
        self.assertTrue(post.message_id)

    def test_without_a_channel_configured_nothing_is_posted(self):
        self.add_run()

        with mock.patch.dict(os.environ, {'VG_ARCADE_CHANNEL_ID': ''}):
            announce.tick(self.now)

        self.assertEqual(self.channel.log, [])


# ---------------------------------------------------------------------------
# The callout
# ---------------------------------------------------------------------------

class CalloutTests(AnnounceMixin, TestCase):
    def refresh(self, now=None):
        announce.refresh_callout(CHANNEL, ICON, now or self.now)

    def test_the_callout_links_to_the_cabinet_and_shows_today_s_leader(self):
        make_score(self.game, 'u1', 12400, self.now, name='Howard')
        self.refresh()

        [post] = self.channel.posts()
        self.assertEqual(button(post)['url'], 'https://arcade.vicers.net/')
        self.assertEqual(button(post)['label'], 'Play Vice Arcade')
        self.assertRegex(description(post), r'^## 🕹️ The arcade is open')
        self.assertIn('**Today**\n🥇 Howard · 12,400', description(post))
        self.assertIn('**This season**\n🥇 Howard · 12,400', description(post))
        self.assertIn('midnight Eastern', description(post))
        self.assertRegex(description(post), r'This season ends <t:\d+:F> \(<t:\d+:R>\)')
        self.assertEqual(post['payload']['embeds'][0]['thumbnail'], {'url': ICON})

    def test_the_callout_shows_the_top_three_on_each_board(self):
        for user, value, name in [('a', 400, 'Ana'), ('b', 300, 'Bo'), ('c', 200, 'Cy'), ('d', 100, 'Di')]:
            make_score(self.game, user, value, self.now, name=name)
        self.refresh()

        text = description(self.channel.posts()[0])
        self.assertIn('**Today**\n🥇 Ana · 400\n🥈 Bo · 300\n🥉 Cy · 200\n', text)
        self.assertNotIn('Di ·', text, 'only the top three')

    def test_an_empty_board_invites_the_first_run(self):
        self.refresh()

        text = description(self.channel.posts()[0])
        self.assertIn('**Today**\nNobody yet. Be the first.', text)
        self.assertIn('**This season**\nUp for grabs.', text)

    def test_it_is_posted_and_pinned_once_and_only_edited_when_the_standings_change(self):
        self.refresh()
        self.refresh(self.now + timedelta(seconds=30))
        self.assertEqual(self.channel.kinds(), ['send', 'pin'], 'nothing changed, nothing sent')

        make_score(self.game, 'u1', 12400, self.now, name='Howard')
        self.refresh(self.now + timedelta(seconds=60))

        self.assertEqual(self.channel.kinds(), ['send', 'pin', 'edit'])
        self.assertEqual(ArcadeCallout.load().message_id, self.channel.posts()[0]['id'])
        self.assertIn('Howard', description(self.channel.posts()[0]))

    def test_a_callout_staff_deleted_comes_back(self):
        self.refresh()
        original = ArcadeCallout.load().message_id
        self.channel.delete_message(CHANNEL, original)

        self.refresh(self.now + timedelta(minutes=11))

        [post] = self.channel.posts()
        self.assertNotEqual(post['id'], original)
        self.assertTrue(post['pinned'])

    def test_a_pin_the_bot_may_not_make_is_recorded_and_the_callout_stays_up(self):
        self.use(FakeChannel(pin_fails=True))
        self.refresh()

        self.assertEqual(len(self.channel.posts()), 1)
        self.assertIn('Pin Messages', ArcadeCallout.load().last_error)

    def test_once_a_day_at_the_posting_hour_it_moves_to_the_bottom_and_the_old_copy_goes(self):
        self.refresh(EVENING - timedelta(hours=6))
        original = ArcadeCallout.load().message_id
        self.channel.chat('a day of chat')

        self.refresh(EVENING)

        current = ArcadeCallout.load().message_id
        self.assertNotEqual(current, original)
        self.assertEqual(self.channel.last_message_id, current)
        self.assertIn(('delete', original), self.channel.log)
        self.assertTrue(self.channel.messages[current]['pinned'])

        sends = self.channel.kinds().count('send')
        self.channel.chat('more chat')
        self.refresh(EVENING + timedelta(hours=1))
        self.assertEqual(self.channel.kinds().count('send'), sends, 'not twice in a day')

    def test_outside_the_posting_hours_it_is_left_where_it_is(self):
        self.refresh(AFTERNOON - timedelta(hours=1))
        self.channel.chat()
        self.refresh(AFTERNOON)

        self.assertNotIn('delete', self.channel.kinds())

    def test_a_callout_already_at_the_bottom_is_not_reposted_for_nothing(self):
        self.refresh(EVENING - timedelta(hours=6))
        self.refresh(EVENING)

        self.assertEqual(self.channel.kinds().count('send'), 1)
        self.assertEqual(str(ArcadeCallout.load().last_repost_day), '2026-11-10')

    def test_the_bot_s_poll_is_what_sends_the_posts(self):
        from arcade.tests.test_api import bot_headers

        with mock.patch.object(announce, 'tick') as ticked:
            self.client.get('/api/arcade/events/pending', **bot_headers())

        ticked.assert_called_once()
