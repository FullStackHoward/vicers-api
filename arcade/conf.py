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

from decouple import UndefinedValueError, config

DISCORD_API = 'https://discord.com/api/v10'

# A Discord call that has not answered in this long is treated as unknown rather
# than as a failure, which matters for the departure check: an outage must never
# be read as "this player has left".
REQUEST_TIMEOUT_SECONDS = 10

# How long a play session stays valid. Decided: two hours.
SESSION_TTL_HOURS = 2


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


def arcade_channel_id():
    return _required('VG_ARCADE_CHANNEL_ID')


def session_signing_key():
    return _required('ARCADE_SESSION_SIGNING_KEY')


def bot_api_secret():
    return _required('BOT_API_SECRET')
