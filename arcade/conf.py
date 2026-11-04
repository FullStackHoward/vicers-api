"""
Every environment value the arcade reads, in one place.

Naming follows the `.env` Howard already created for the Discord snowflakes, so
the values can be copied across without being retyped under a second spelling.
The three Vice Arcade credentials keep their spec names instead of the plain
`BOT_TOKEN` in that file, because this API also talks to the Vice Verify Bot and
two different tokens called `BOT_TOKEN` is a mistake waiting to happen.

Nothing here is read at import time. A missing value raises when it is first
needed, so `manage.py check`, migrations and the tests all run without a full
production environment.
"""

from decouple import Csv, UndefinedValueError, config

DISCORD_API = 'https://discord.com/api/v10'

# A Discord call that has not answered in this long is treated as unknown rather
# than as a failure, which matters for the departure check: an outage must never
# be read as "this player has left".
REQUEST_TIMEOUT_SECONDS = 10

# How long a play session stays valid. Decided: two hours.
SESSION_TTL_HOURS = 2

# Where Discord may send a player back to after sign-in. Each one also has to be
# listed under OAuth2 > Redirects in the Developer Portal; Discord enforces that
# side, and this list stops the API exchanging a code for any page but the arcade.
DEFAULT_REDIRECT_URIS = 'https://arcade.vicers.net/,http://localhost:5173/'


def _required(name):
    try:
        value = config(name)
    except UndefinedValueError:
        raise RuntimeError(
            f'{name} is not set in the API environment. The arcade needs it; see '
            f'arcade/conf.py for the full list.'
        )
    if not str(value).strip():
        raise RuntimeError(f'{name} is set but empty in the API environment.')
    return str(value).strip()


def client_id():
    return _required('VICE_ARCADE_CLIENT_ID')


def client_secret():
    return _required('VICE_ARCADE_CLIENT_SECRET')


def bot_token():
    return _required('VICE_ARCADE_BOT_TOKEN')


def guild_id():
    return _required('VICE_GAMERS_GUILD_ID')


def session_signing_key():
    return _required('ARCADE_SESSION_SIGNING_KEY')


def bot_api_secret():
    return _required('BOT_API_SECRET')


def redirect_uris():
    return config('ARCADE_REDIRECT_URIS', default=DEFAULT_REDIRECT_URIS, cast=Csv())
