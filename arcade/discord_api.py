"""
The calls this API makes to Discord.

Stdlib urllib, not requests: nothing in requirements.txt provides a synchronous
HTTP client, and adding one would mean a pip install on the server for a
handful of calls.

The distinction that matters here is between "Discord said no" and "Discord did
not answer". A gate check that cannot reach Discord must fail closed, while a
membership check that cannot reach Discord must not conclude that anybody left.
Callers get different exceptions so they can tell those apart.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from . import conf


class DiscordError(Exception):
    """Discord answered, and the answer was not usable."""

    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


class DiscordUnavailable(Exception):
    """Discord did not answer. Never treat this as a negative result."""


# Discord's JSON error codes this API acts on.
UNKNOWN_CHANNEL = 10003
UNKNOWN_MESSAGE = 10008
UNKNOWN_MEMBER = 10007
UNKNOWN_USER = 10013

# Discord asks bots to identify themselves this way, and Cloudflare in front of
# it can refuse urllib's default.
USER_AGENT = 'DiscordBot (https://api.vicers.net, 1.0)'


def _request(method, path, headers=None, body=None, form=False, timeout=None):
    url = f'{conf.DISCORD_API}{path}'
    data = None
    sent_headers = {'Accept': 'application/json', 'User-Agent': USER_AGENT}
    sent_headers.update(headers or {})

    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            sent_headers['Content-Type'] = 'application/x-www-form-urlencoded'
        else:
            data = json.dumps(body).encode()
            sent_headers['Content-Type'] = 'application/json'

    request = urllib.request.Request(url, data=data, headers=sent_headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=timeout or conf.REQUEST_TIMEOUT_SECONDS) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = {}
        raise DiscordError(
            parsed.get('message') or f'Discord returned {error.code}',
            status=error.code,
            code=parsed.get('code'),
        )
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise DiscordUnavailable(str(error))

    if not payload:
        return {}

    try:
        return json.loads(payload)
    except ValueError:
        raise DiscordError('Discord returned a body that is not JSON')


def exchange_code(code, redirect_uri):
    """
    Swap the sign-in page's OAuth code for an access token.

    `redirect_uri` has to be the exact one the browser was sent back to, or
    Discord refuses the exchange. The client secret is used here and nowhere
    else, and never leaves the server.
    """
    payload = _request(
        'POST',
        '/oauth2/token',
        body={
            'client_id': conf.client_id(),
            'client_secret': conf.client_secret(),
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': redirect_uri,
        },
        form=True,
    )

    token = payload.get('access_token')
    if not token:
        raise DiscordError('Discord did not return an access token')

    return payload


def current_user(access_token):
    """
    Who the player actually is.

    The client's claim about its own user id is never trusted; this is the only
    thing that establishes identity.
    """
    return _request(
        'GET',
        '/users/@me',
        headers={'Authorization': f'Bearer {access_token}'},
    )


def guild_member(guild_id, discord_user_id):
    """
    The player's membership of a server, or DiscordError with UNKNOWN_MEMBER.

    Asked with the Vice Arcade bot's token rather than the player's, so sign-in
    needs only the `identify` scope. Looking one member up over REST needs no
    gateway intent, only that the bot is in the server.
    """
    return _request(
        'GET',
        f'/guilds/{urllib.parse.quote(str(guild_id))}/members/{urllib.parse.quote(str(discord_user_id))}',
        headers={'Authorization': f'Bot {conf.bot_token()}'},
    )


# ---------------------------------------------------------------------------
# Posting in the arcade channel, as the Vice Arcade bot (arcade/announce.py)
# ---------------------------------------------------------------------------

# Shorter than the membership check's, because these run inside the bot's event
# poll, which the bot gives up on after fifteen seconds.
POST_TIMEOUT_SECONDS = 5


def _bot():
    return {'Authorization': f'Bot {conf.bot_token()}'}


def _channel(channel_id):
    return f'/channels/{urllib.parse.quote(str(channel_id))}'


def get_channel(channel_id):
    """The channel, for its `last_message_id`: whether a post is still the newest."""
    return _request('GET', _channel(channel_id), headers=_bot(), timeout=POST_TIMEOUT_SECONDS)


def get_message(channel_id, message_id):
    return _request(
        'GET',
        f'{_channel(channel_id)}/messages/{urllib.parse.quote(str(message_id))}',
        headers=_bot(),
        timeout=POST_TIMEOUT_SECONDS,
    )


def create_message(channel_id, payload):
    return _request('POST', f'{_channel(channel_id)}/messages', headers=_bot(), body=payload, timeout=POST_TIMEOUT_SECONDS)


def edit_message(channel_id, message_id, payload):
    return _request(
        'PATCH',
        f'{_channel(channel_id)}/messages/{urllib.parse.quote(str(message_id))}',
        headers=_bot(),
        body=payload,
        timeout=POST_TIMEOUT_SECONDS,
    )


def delete_message(channel_id, message_id):
    return _request(
        'DELETE',
        f'{_channel(channel_id)}/messages/{urllib.parse.quote(str(message_id))}',
        headers=_bot(),
        timeout=POST_TIMEOUT_SECONDS,
    )


def pin_message(channel_id, message_id):
    """Needs Pin Messages in the channel."""
    return _request(
        'PUT',
        f'{_channel(channel_id)}/messages/pins/{urllib.parse.quote(str(message_id))}',
        headers=_bot(),
        timeout=POST_TIMEOUT_SECONDS,
    )


def guild_emojis(guild_id):
    return _request(
        'GET',
        f'/guilds/{urllib.parse.quote(str(guild_id))}/emojis',
        headers=_bot(),
        timeout=POST_TIMEOUT_SECONDS,
    )
