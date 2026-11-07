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


def public_key():
    """
    The Vice Arcade app's public key, from General Information in the Developer
    Portal. Not a secret: it checks that a slash command really came from Discord.
    """
    return _required('VICE_ARCADE_PUBLIC_KEY')


def guild_id():
    return _required('VICE_GAMERS_GUILD_ID')


def session_signing_key():
    return _required('ARCADE_SESSION_SIGNING_KEY')


def bot_api_secret():
    return _required('BOT_API_SECRET')


def redirect_uris():
    return config('ARCADE_REDIRECT_URIS', default=DEFAULT_REDIRECT_URIS, cast=Csv())


# ---------------------------------------------------------------------------
# The arcade's own posts in Vice Gamers (arcade/announce.py)
# ---------------------------------------------------------------------------

# Where a finished run's post folds into the player's previous one: runs on one
# game within this long of each other are one session and share one post.
RUN_SESSION_MINUTES = 30

# A run older than this when its post comes up is not posted at all, so runs
# that waited out an outage do not land in a burst as if they had just happened.
RUN_POST_MAX_AGE_MINUTES = 15

# The callout's standings are checked against Discord at least this often, so a
# callout staff deleted comes back without waiting for the next day's repost.
CALLOUT_CHECK_MINUTES = 10

# The server emoji used as the posts' icon, looked up by name so a re-upload
# needs no change here.
ICON_EMOJI_NAME = 'vicearcade'


def arcade_channel_id():
    """
    The channel the callout and run posts go in. Optional: left unset, the arcade
    posts nothing and everything else carries on.
    """
    return str(config('VG_ARCADE_CHANNEL_ID', default='')).strip()


def arcade_channel_url():
    """
    A link to the arcade channel, for the page's Back to Discord button. None when
    either ID is unset, so the page leaves the button out rather than linking
    nowhere. Read softly: this is served on a public endpoint, which must not fail
    over a missing optional value.
    """
    guild = str(config('VICE_GAMERS_GUILD_ID', default='')).strip()
    channel = arcade_channel_id()
    if not guild or not channel:
        return None
    return f'https://discord.com/channels/{guild}/{channel}'


def arcade_url():
    return str(config('ARCADE_URL', default='https://arcade.vicers.net')).strip().rstrip('/')


def callout_post_hour():
    """The hour, Eastern, the callout is moved back to the bottom of the channel."""
    return config('ARCADE_CALLOUT_POST_HOUR', default=18, cast=int)


# ---------------------------------------------------------------------------
# First-place alerts by DM (arcade/alerts.py)
# ---------------------------------------------------------------------------

# One DM per player per game in this long, so two players trading first place
# all evening do not fill each other's DMs.
ALERT_COOLDOWN_MINUTES = 30

# An alert that could not be sent within this long is dropped rather than sent late.
ALERT_MAX_AGE_MINUTES = 30

# A slash command or button press signed longer ago than this is refused, so a
# captured request cannot be replayed later.
INTERACTION_MAX_AGE_SECONDS = 5 * 60
