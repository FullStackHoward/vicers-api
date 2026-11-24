import os
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase

from botconfig.models import MinecraftRealm

BOT_SECRET = 'test-bot-secret'
URL = '/api/bot-config/'
CHANNEL = '123456789012345678'
LINK = 'https://realms.gg/EXAMPLE'


def bot_headers(secret=BOT_SECRET):
    return {'HTTP_X_BOT_SECRET': secret}


class EnvMixin:
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {'BOT_API_SECRET': BOT_SECRET})
        patcher.start()
        self.addCleanup(patcher.stop)


class BotConfigAuthTests(EnvMixin, TestCase):
    # DRF answers 403 for a refused permission on a view with no authenticators, the
    # same as every bot-only arcade endpoint.
    def test_no_header_is_refused(self):
        self.assertEqual(self.client.get(URL, {'community': 'vicegamers'}).status_code, 403)

    def test_wrong_secret_is_refused(self):
        response = self.client.get(URL, {'community': 'vicegamers'}, **bot_headers('nope'))
        self.assertEqual(response.status_code, 403)

    def test_near_miss_secret_is_refused(self):
        response = self.client.get(URL, {'community': 'vicegamers'}, **bot_headers(BOT_SECRET + 'x'))
        self.assertEqual(response.status_code, 403)

    def test_unset_secret_never_matches(self):
        with mock.patch.dict(os.environ, {'BOT_API_SECRET': ''}):
            for sent in ['', 'anything']:
                with self.subTest(sent=sent):
                    response = self.client.get(URL, {'community': 'vicegamers'}, **bot_headers(sent))
                    self.assertEqual(response.status_code, 403)


class BotConfigEndpointTests(EnvMixin, TestCase):
    def get(self, **params):
        return self.client.get(URL, params, **bot_headers())

    def test_missing_community_is_a_400(self):
        self.assertEqual(self.get().status_code, 400)

    def test_unknown_community_is_a_400(self):
        self.assertEqual(self.get(community='nobody').status_code, 400)

    def test_no_row_gives_a_null_section(self):
        response = self.get(community='vicegamers')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'community': 'vicegamers', 'minecraft': None})

    def test_stored_values_are_returned(self):
        MinecraftRealm.objects.create(
            community='vicegamers', channel_id=CHANNEL, invite_url=LINK, link_unavailable=True,
        )

        response = self.get(community='vicegamers')
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body['community'], 'vicegamers')
        self.assertEqual(body['minecraft']['channel_id'], CHANNEL)
        self.assertIsInstance(body['minecraft']['channel_id'], str)
        self.assertEqual(body['minecraft']['invite_url'], LINK)
        self.assertIs(body['minecraft']['link_unavailable'], True)
        self.assertTrue(body['minecraft']['updated_at'])

    def test_another_communitys_row_is_not_returned(self):
        MinecraftRealm.objects.create(community='vicecreators', channel_id=CHANNEL, invite_url=LINK)
        self.assertIsNone(self.get(community='vicegamers').json()['minecraft'])

    def test_response_is_not_cached(self):
        self.assertEqual(self.get(community='vicegamers')['Cache-Control'], 'no-store')


class MinecraftRealmModelTests(TestCase):
    def build(self, **overrides):
        values = {'community': 'vicegamers', 'channel_id': CHANNEL, 'invite_url': LINK}
        values.update(overrides)
        return MinecraftRealm(**values)

    def test_valid_values_are_accepted(self):
        self.build().full_clean()
        self.build(channel_id='', invite_url='').full_clean()
        self.build(channel_id='1' * 17).full_clean()
        self.build(channel_id='1' * 20).full_clean()

    def test_http_link_is_rejected(self):
        with self.assertRaises(ValidationError) as caught:
            self.build(invite_url='http://realms.gg/EXAMPLE').full_clean()
        self.assertIn('invite_url', caught.exception.message_dict)

    def test_bad_channel_ids_are_rejected(self):
        for bad in ['abc123456789012345', '1' * 16, '1' * 21, '12345678901234567 ', '-12345678901234567']:
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError) as caught:
                    self.build(channel_id=bad).full_clean()
                self.assertIn('channel_id', caught.exception.message_dict)

    def test_one_row_per_community(self):
        self.build().save()
        with self.assertRaises(ValidationError) as caught:
            self.build().full_clean()
        self.assertIn('community', caught.exception.message_dict)

    def test_link_unavailable_defaults_to_false(self):
        self.assertIs(MinecraftRealm.objects.create(community='vicegamers').link_unavailable, False)
