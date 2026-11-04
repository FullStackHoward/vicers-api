"""
The /arcade slash command, for the server owner: wipe-today, wipe-season and
reset.

Discord sends these to the API over HTTP (the Interactions Endpoint URL in the
Developer Portal) rather than to a bot process, so nothing in Vice Verify Bot is
involved. Every request is signed by Discord with the app's key and checked here
before anything is read from it.

Each command answers with a private confirmation and two buttons, and nothing
happens until Confirm is pressed. The owner is checked again on the button,
against the server's actual owner as Discord reports it, so the command being
visible to an admin is never enough. `default_member_permissions` of "0" keeps the
command out of everyone else's list in the first place.

Discord wants an answer within three seconds. The wipes are a few rows and fit
easily. The reset deletes the scores straight away and leaves emptying the channel
to the posting tick (arcade/announce.py), which edits this reply when it is done.
"""

import logging
import time

from django.utils import timezone

from . import conf, discord_api, wipes

logger = logging.getLogger(__name__)

# Interaction types.
PING = 1
APPLICATION_COMMAND = 2
MESSAGE_COMPONENT = 3

# Response types.
PONG = 1
CHANNEL_MESSAGE = 4
UPDATE_MESSAGE = 7

EPHEMERAL = 1 << 6

# A confirmation older than this is refused, so a forgotten prompt cannot be
# pressed days later.
CONFIRM_MINUTES = 5

# The server's owner, asked of Discord at most this often.
OWNER_CACHE_SECONDS = 10 * 60

COMMAND = {
    'name': 'arcade',
    'type': 1,
    'description': 'Vice Arcade tools for the server owner',
    # Nobody but administrators sees it in the list. The owner check below is the
    # real gate.
    'default_member_permissions': '0',
    'options': [
        {
            'type': 1,
            'name': 'wipe-today',
            'description': 'Clear today\'s boards for every game. This Season and All Time keep the runs.',
        },
        {
            'type': 1,
            'name': 'wipe-season',
            'description': 'Clear this season\'s and today\'s boards for every game. All Time keeps the runs.',
        },
        {
            'type': 1,
            'name': 'reset',
            'description': 'Delete every score, including All Time, start a fresh season and empty the arcade channel.',
        },
    ],
}

ACTIONS = {
    'wipe-today': {
        'prompt': (
            '**Wipe today\'s boards?**\n'
            'Today\'s board for every game starts again from empty. The runs stay on '
            'This Season and All Time, and nothing is posted in the channel.'
        ),
        'confirm': 'Wipe today',
    },
    'wipe-season': {
        'prompt': (
            '**Wipe this season\'s boards?**\n'
            'This Season and Today start again from empty for every game, and the '
            'podium roles come off until someone plays. The runs stay on All Time, and '
            'the season still ends when it was going to.'
        ),
        'confirm': 'Wipe the season',
    },
    'reset': {
        'prompt': (
            '**Reset the whole arcade?**\n'
            'Every score is deleted, All Time included, along with personal bests, '
            'past seasons and podium history. A fresh season starts now, the podium '
            'roles come off, and every message in the arcade channel is deleted. '
            'This cannot be undone.'
        ),
        'confirm': 'Reset everything',
    },
}

_owner_cache = {'guild': None, 'owner': None, 'fetched': 0.0}


class Unverified(Exception):
    pass


def verify(body, signature, timestamp, now=None):
    """
    Raise Unverified unless Discord signed this exact body at about this time.
    Discord sends deliberately bad signatures when the URL is saved, and stops
    using an endpoint that accepts them.
    """
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    if not signature or not timestamp:
        raise Unverified('missing signature')

    try:
        sent_at = int(timestamp)
    except ValueError:
        raise Unverified('bad timestamp')

    now = time.time() if now is None else now
    if abs(now - sent_at) > conf.INTERACTION_MAX_AGE_SECONDS:
        raise Unverified('stale timestamp')

    try:
        VerifyKey(bytes.fromhex(conf.public_key())).verify(timestamp.encode() + body, bytes.fromhex(signature))
    except (BadSignatureError, ValueError):
        raise Unverified('bad signature')


def server_owner(guild_id):
    if (
        _owner_cache['guild'] == guild_id
        and _owner_cache['owner']
        and time.monotonic() - _owner_cache['fetched'] < OWNER_CACHE_SECONDS
    ):
        return _owner_cache['owner']

    owner = str(discord_api.get_guild(guild_id).get('owner_id') or '')
    _owner_cache.update(guild=guild_id, owner=owner, fetched=time.monotonic())
    return owner


def _private(text, components=None):
    data = {'content': text, 'flags': EPHEMERAL, 'allowed_mentions': {'parse': []}}
    if components is not None:
        data['components'] = components
    return {'type': CHANNEL_MESSAGE, 'data': data}


def _update(text):
    return {
        'type': UPDATE_MESSAGE,
        'data': {'content': text, 'components': [], 'allowed_mentions': {'parse': []}},
    }


def _owner_refusal(interaction):
    """None when the person is the server owner, otherwise the reply to send."""
    guild_id = str(interaction.get('guild_id') or '')
    user_id = str(((interaction.get('member') or {}).get('user') or {}).get('id') or '')

    if not guild_id or guild_id != conf.guild_id() or not user_id:
        return 'This only works in Vice Gamers.'

    try:
        owner = server_owner(guild_id)
    except (discord_api.DiscordError, discord_api.DiscordUnavailable) as error:
        logger.warning('Vice Arcade: could not look up the server owner: %s', error)
        return 'Could not check with Discord who owns the server. Try again in a moment.'

    if user_id != owner:
        return 'Only the server owner can use this.'

    return None


def handle(interaction, now=None):
    kind = interaction.get('type')

    if kind == PING:
        return {'type': PONG}

    if kind == APPLICATION_COMMAND:
        return on_command(interaction, now)

    if kind == MESSAGE_COMPONENT:
        return on_button(interaction, now)

    return _private('That is not something the arcade knows how to do.')


def on_command(interaction, now=None):
    data = interaction.get('data') or {}
    options = data.get('options') or []
    action = options[0].get('name') if options else None

    if data.get('name') != COMMAND['name'] or action not in ACTIONS:
        return _private('That is not something the arcade knows how to do.')

    refusal = _owner_refusal(interaction)
    if refusal:
        return _private(refusal)

    issued = int((now or timezone.now()).timestamp())

    return _private(ACTIONS[action]['prompt'], components=[{
        'type': 1,
        'components': [
            {
                'type': 2,
                'style': 4,  # red
                'label': ACTIONS[action]['confirm'],
                'custom_id': f'arcade:{action}:{issued}',
            },
            {
                'type': 2,
                'style': 2,  # grey
                'label': 'Cancel',
                'custom_id': 'arcade:cancel',
            },
        ],
    }])


def on_button(interaction, now=None):
    now = now or timezone.now()
    parts = str((interaction.get('data') or {}).get('custom_id') or '').split(':')

    if parts[:2] == ['arcade', 'cancel']:
        return _update('Cancelled. Nothing was changed.')

    if len(parts) != 3 or parts[0] != 'arcade' or parts[1] not in ACTIONS:
        return _update('That button is not one the arcade knows.')

    action = parts[1]

    try:
        issued = int(parts[2])
    except ValueError:
        return _update('That button is not one the arcade knows.')

    if now.timestamp() - issued > CONFIRM_MINUTES * 60:
        return _update(f'That confirmation expired. Run `/arcade {action}` again.')

    refusal = _owner_refusal(interaction)
    if refusal:
        return _update(refusal)

    by = str(interaction['member']['user']['id'])

    if action == 'wipe-today':
        result = wipes.wipe_today(by=by, now=now)
        return _update(
            f'Today\'s boards are wiped ({_entries(result["players"])} cleared). Those runs '
            f'still count on This Season and All Time. The pinned post catches up within '
            f'a minute.'
        )

    if action == 'wipe-season':
        result = wipes.wipe_season(by=by, now=now)
        ends = int(result['season'].ends_at.timestamp())
        return _update(
            f'This season\'s boards are wiped, and today\'s with them '
            f'({_entries(result["players"])} cleared). Those runs still count on All Time. '
            f'The season still ends <t:{ends}:F>. The pinned post and podium roles catch '
            f'up within a minute.'
        )

    result = wipes.reset_everything(
        by=by,
        now=now,
        application_id=str(interaction.get('application_id') or ''),
        interaction_token=str(interaction.get('token') or ''),
    )
    ends = int(result['season'].ends_at.timestamp())
    channel = conf.arcade_channel_id()
    runs = result['runs']
    text = (
        f'Everything is reset: {runs:,} run{"s" if runs != 1 else ""} deleted, and a fresh '
        f'season runs until <t:{ends}:F>.'
    )

    if result['purge'] is None:
        text += ' No arcade channel is set (VG_ARCADE_CHANNEL_ID), so no messages were deleted.'
    else:
        text += f' Clearing <#{channel}> now. This message updates when that is done.'

    return _update(text)


def _entries(count):
    return f'{count} leaderboard entr{"y" if count == 1 else "ies"}'
