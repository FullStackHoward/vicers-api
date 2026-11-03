"""
The three calls this API makes to Discord.

Stdlib urllib, not requests: nothing in requirements.txt provides a synchronous
HTTP client, and adding one would mean a pip install on the server for three
calls.

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


# Discord's JSON error code for a member or user that is not there.
UNKNOWN_MEMBER = 10007
UNKNOWN_USER = 10013


def _request(method, path, headers=None, body=None, form=False):
    url = f'{conf.DISCORD_API}{path}'
    data = None
    sent_headers = {'Accept': 'application/json'}
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
        with urllib.request.urlopen(request, timeout=conf.REQUEST_TIMEOUT_SECONDS) as response:
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


def exchange_code(code):
    """
    Swap the Activity's OAuth code for an access token.

    The client secret is used here and nowhere else, and never leaves the server.
    """
    payload = _request(
        'POST',
        '/oauth2/token',
        body={
            'client_id': conf.client_id(),
            'client_secret': conf.client_secret(),
            'grant_type': 'authorization_code',
            'code': code,
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


def activity_instance(instance_id):
    """
    Where a running Activity instance is: which guild, which channel, and who is
    in it.

    Discord's docs require the application's bot token for this route, which is
    why the Vice Arcade app has a bot at all even though it never runs as one.
    """
    return _request(
        'GET',
        f'/applications/{conf.client_id()}/activity-instances/{urllib.parse.quote(instance_id)}',
        headers={'Authorization': f'Bot {conf.bot_token()}'},
    )
