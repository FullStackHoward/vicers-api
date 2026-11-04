"""
The arcade's own posts in Vice Gamers: the pinned callout and the "just played"
posts.

Sent from here, over Discord REST as the Vice Arcade bot, rather than by a bot
process: this API already holds that bot's token and is the first to know a run
finished. `tick` is called from the bot's thirty-second event poll, the same
clock that drives season and Daily board rollover (see arcade/views.py), and
`manage.py arcade_announce` runs it by hand or from cron.

The callout
    One post, kept the way the LFG board is: edited in place while it is
    current, pinned, and posted again at the bottom of the channel once a day at
    the posting hour, with the old copy deleted. Post first and delete second, so
    a failure leaves the existing callout rather than none. Its Play button opens
    the cabinet (the arcade's front page).

Run posts
    Every banked run is posted with a button straight into the game that was
    played. What keeps that from flooding the channel is the session: runs by one
    player on one game within RUN_SESSION_MINUTES of each other share one post.
    Each new run edits it when it is still the newest message, and otherwise
    reposts it at the bottom and deletes the old one. Twenty runs in a row are
    one post, not twenty.

Nothing here may break the poll that calls it. Discord being down leaves the
work queued for the next tick; a missing permission is written to the callout's
`last_error`, which admin shows.
"""

import hashlib
import json
import logging
import time
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

from . import conf, discord_api, leaderboards, rollover, seasons
from .models import ArcadeCallout, ArcadeGame, RunPost

logger = logging.getLogger(__name__)

EASTERN = ZoneInfo('America/New_York')
BRAND_NAME = 'Vice Arcade'
PLAY_EMOJI = '🕹️'

# The Vice Radar callout colours, one per run session so a post does not change
# colour under the reader on every run.
COLORS = [0xFFB570, 0x2ED9C3, 0xE0A9E8]
CALLOUT_COLOR = COLORS[0]

# Run posts handled per tick. The rest wait thirty seconds for the next one,
# which keeps a single poll from spending its time on Discord.
RUN_POSTS_PER_TICK = 10

# Seconds a tick may spend before it stops starting new Discord work. The bot
# gives up on its poll after fifteen, and each call can take five.
TICK_BUDGET_SECONDS = 6

# How long the emoji lookup is trusted before asking Discord again.
ICON_CACHE_SECONDS = 60 * 60

# Answers that mean "try again later" rather than "this post is broken".
RETRY_LATER_STATUSES = {429, 500, 502, 503, 504}

# Answers that mean the bot cannot post in the channel at all.
CHANNEL_CODES = {
    discord_api.UNKNOWN_CHANNEL,
    50001,  # Missing Access
    50013,  # Missing Permissions
}

_icon_cache = {'url': None, 'fetched': 0.0}


class StopTick(Exception):
    """Leave the rest of the work for the next tick."""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def tick(now=None):
    """
    Post queued runs, then bring the callout up to date. Never raises.

    Does nothing until VG_ARCADE_CHANNEL_ID is set, so the API can be deployed
    ahead of that.
    """
    channel_id = conf.arcade_channel_id()
    if not channel_id:
        return

    now = now or timezone.now()
    deadline = time.monotonic() + TICK_BUDGET_SECONDS

    try:
        icon = icon_url()
        post_runs(channel_id, icon, now, deadline)
        if time.monotonic() < deadline:
            refresh_callout(channel_id, icon, now)
    except StopTick:
        pass
    except Exception:
        logger.exception('Vice Arcade: posting in the arcade channel failed')


def _stop_for(error, what):
    """
    Turn a Discord failure into "try later": logged, and written to the callout
    row when it is the kind a person has to fix.
    """
    if isinstance(error, discord_api.DiscordError) and (
        error.code in CHANNEL_CODES or error.status in (401, 403)
    ):
        record_error(f'{what}: {error} (code {error.code}). The Vice Arcade bot needs '
                     f'View Channel, Send Messages, Embed Links and Pin Messages in '
                     f'the arcade channel.')
    else:
        logger.warning('Vice Arcade: %s: %s', what, error)

    raise StopTick() from error


def _retry_later(error):
    return isinstance(error, discord_api.DiscordUnavailable) or (
        isinstance(error, discord_api.DiscordError)
        and (error.status in RETRY_LATER_STATUSES or error.status in (401, 403)
             or error.code in CHANNEL_CODES)
    )


def record_error(message):
    logger.warning('Vice Arcade: %s', message)
    callout = ArcadeCallout.load()
    callout.last_error = message[:2000]
    callout.last_error_at = timezone.now()
    callout.save(update_fields=['last_error', 'last_error_at'])


# ---------------------------------------------------------------------------
# What the posts say
# ---------------------------------------------------------------------------

def icon_url():
    """
    The :vicearcade: server emoji as an image, or None. Cached, because it is
    looked up on every tick otherwise.
    """
    if _icon_cache['fetched'] and time.monotonic() - _icon_cache['fetched'] < ICON_CACHE_SECONDS:
        return _icon_cache['url']

    url = None
    try:
        for emoji in discord_api.guild_emojis(conf.guild_id()) or []:
            if emoji.get('name') == conf.ICON_EMOJI_NAME and emoji.get('id'):
                extension = 'gif' if emoji.get('animated') else 'png'
                url = f'https://cdn.discordapp.com/emojis/{emoji["id"]}.{extension}?size=128'
                break
    except (discord_api.DiscordError, discord_api.DiscordUnavailable) as error:
        logger.warning('Vice Arcade: could not look up the :%s: emoji: %s', conf.ICON_EMOJI_NAME, error)
        # Keep whatever was found before, and ask again next time.
        return _icon_cache['url']

    _icon_cache.update(url=url, fetched=time.monotonic())
    return url


def play_url(slug=None):
    base = conf.arcade_url()
    return f'{base}/?play={slug}' if slug else f'{base}/'


def _number(value):
    return f'{int(value):,}'


def _message(color, icon, lines, label, url):
    embed = {
        'color': color,
        'author': {'name': BRAND_NAME},
        'description': '\n'.join(lines),
    }
    if icon:
        embed['thumbnail'] = {'url': icon}

    return {
        'embeds': [embed],
        'components': [{
            'type': 1,
            'components': [{
                'type': 2,
                'style': 5,
                'label': label,
                'url': url,
                'emoji': {'name': PLAY_EMOJI},
            }],
        }],
        # Display names come from members. Nothing here can ping, whatever they say.
        'allowed_mentions': {'parse': []},
    }


MEDALS = ['🥇', '🥈', '🥉']

# Places shown per board in the pinned post. The full board is in the arcade.
CALLOUT_TOP = 3


def ordinal(place):
    if 10 <= place % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(place % 10, 'th')
    return f'{place}{suffix}'


def _top_lines(rows, empty):
    if not rows:
        return [empty]
    return [
        f'{MEDALS[index]} {row["display_name"]} · {_number(row["score"])}'
        for index, row in enumerate(rows[:CALLOUT_TOP])
    ]


def callout_message(icon, now):
    """
    The pinned post: the top three on today's board and this season's board for
    each game, and when each resets. Edited whenever those change, so it is the
    running leaderboard in the channel.
    """
    season = rollover.ensure_current_season(now)
    today = seasons.eastern_date(now)
    stamp = int(season.ends_at.timestamp())

    lines = [
        f'## {PLAY_EMOJI} The arcade is open',
        'Play right in your browser. Sign in with Discord and every run lands on '
        'the Vice Gamers leaderboards.',
    ]

    for game in ArcadeGame.objects.filter(is_active=True).order_by('display_name'):
        lines += ['', f'### {game.display_name}', '**Today**']
        lines += _top_lines(leaderboards.daily_rows(game, today), 'Nobody yet. Be the first.')
        lines += ['', '**This season**']
        lines += _top_lines(leaderboards.season_rows(game, season), 'Up for grabs.')

    lines += [
        '',
        'Today\'s board starts fresh every night at midnight Eastern.',
        f'This season ends <t:{stamp}:F> (<t:{stamp}:R>).',
    ]

    return _message(CALLOUT_COLOR, icon, lines, 'Play Vice Arcade', play_url())


def today_line(place, players, best=None):
    """
    Where a player stands on today's board, in words. `best` is their best score
    today, given only when it is not the run being posted.
    """
    if players == 1:
        text = 'The only player on today\'s board so far'
    elif place == 1:
        others = players - 1
        text = f'🥇 1st on today\'s board, ahead of {others} other player{"s" if others != 1 else ""}'
    else:
        text = f'{ordinal(place)} on today\'s board, out of {players} players'

    if best is not None:
        text += f' (best today: {_number(best)})'

    return text + '.'


def run_message(post, icon):
    """
    One player's session on one game: the latest run, and how the session has
    gone when it has been more than one. Today's place is their best run today,
    which is not always this one, so it is worded as a standing.
    """
    score = post.score
    game = score.game
    lines = [f'## {score.display_name} scored {_number(score.score)} on {game.display_name}']

    if post.previous_best is None:
        lines.append(f'First run on the {game.display_name} boards.')
    elif score.score > post.previous_best:
        lines.append(f'🔥 New personal best, up from {_number(post.previous_best)}.')

    today = leaderboards.daily_rows(game, seasons.eastern_date(score.created_at))
    mine = next((row for row in today if row['discord_user_id'] == score.discord_user_id), None)
    if mine:
        best = mine['score'] if mine['score'] != score.score else None
        lines.append(today_line(mine['place'], len(today), best))

    if post.session_runs > 1:
        lines.append(f'{post.session_runs} runs this session, best {_number(post.session_best)}.')

    lines.append('Think you can beat it?')

    return _message(post.color, icon, lines, f'Play {game.display_name}', play_url(game.slug))


# ---------------------------------------------------------------------------
# Run posts
# ---------------------------------------------------------------------------

def _session_before(post):
    """The player's post on this game that this run folds into, if it is recent."""
    return (
        RunPost.objects
        .filter(
            score__discord_user_id=post.score.discord_user_id,
            score__game_id=post.score.game_id,
            handled_at__isnull=False,
            handled_at__gte=post.created_at - timedelta(minutes=conf.RUN_SESSION_MINUTES),
        )
        .exclude(message_id='')
        .exclude(pk=post.pk)
        .order_by('-handled_at', '-id')
        .first()
    )


def _skip(post, reason, now):
    post.skipped = reason
    post.handled_at = now
    post.save(update_fields=['skipped', 'handled_at'])


def post_runs(channel_id, icon, now, deadline=None):
    waiting = list(
        RunPost.objects
        .filter(handled_at__isnull=True)
        .select_related('score', 'score__game')
        .order_by('created_at', 'id')[:RUN_POSTS_PER_TICK]
    )
    if not waiting:
        return

    newest = None  # the channel's last message id, read once and then tracked

    for post in waiting:
        if deadline is not None and time.monotonic() >= deadline:
            return

        score = post.score

        if score.removed:
            _skip(post, 'not_public', now)
            continue

        if now - post.created_at > timedelta(minutes=conf.RUN_POST_MAX_AGE_MINUTES):
            _skip(post, 'stale', now)
            continue

        previous = _session_before(post)
        if previous:
            post.session_runs = previous.session_runs + 1
            post.session_best = max(previous.session_best, score.score)
            post.color = previous.color
        else:
            post.session_runs = 1
            post.session_best = score.score
            post.color = COLORS[post.pk % len(COLORS)]

        payload = run_message(post, icon)
        message_id = None

        try:
            if previous:
                if newest is None:
                    newest = discord_api.get_channel(channel_id).get('last_message_id')

                if newest == previous.message_id:
                    try:
                        discord_api.edit_message(channel_id, previous.message_id, payload)
                        message_id = previous.message_id
                    except discord_api.DiscordError as error:
                        if error.code != discord_api.UNKNOWN_MESSAGE:
                            raise
                        # Deleted by staff in the meantime. Post a fresh one below.

            if not message_id:
                message_id = discord_api.create_message(channel_id, payload)['id']
                newest = message_id

                if previous and previous.message_id != message_id:
                    try:
                        discord_api.delete_message(channel_id, previous.message_id)
                    except (discord_api.DiscordError, discord_api.DiscordUnavailable):
                        pass  # Already gone. The new post is up either way.
        except (discord_api.DiscordError, discord_api.DiscordUnavailable) as error:
            if _retry_later(error):
                _stop_for(error, f'posting score {score.id}')
            # Discord refused this one post outright. Not worth retrying forever.
            logger.warning('Vice Arcade: run post for score %s refused: %s', score.id, error)
            _skip(post, 'failed', now)
            continue

        post.message_id = message_id
        post.handled_at = now
        post.save(update_fields=['message_id', 'handled_at', 'session_runs', 'session_best', 'color'])


# ---------------------------------------------------------------------------
# The callout
# ---------------------------------------------------------------------------

def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _pin(channel_id, message):
    if message.get('pinned'):
        return

    try:
        discord_api.pin_message(channel_id, message['id'])
    except (discord_api.DiscordError, discord_api.DiscordUnavailable) as error:
        # An unpinned callout is still a callout. Said once in admin, not raised.
        record_error(f'The callout is posted but could not be pinned: {error}. The '
                     f'Vice Arcade bot needs Pin Messages in the arcade channel.')


def repost_due(callout, now):
    local = timezone.localtime(now, EASTERN)
    hour = conf.callout_post_hour()

    # A three-hour window, so a quiet spell over the posting hour still reposts
    # that evening, but never at an unrelated time.
    return callout.last_repost_day != local.date() and hour <= local.hour <= hour + 3


def refresh_callout(channel_id, icon, now):
    # No transaction: each save follows the Discord call it records, and a
    # rollback would forget a message that is already posted. One bot polls at a
    # time, so there is nobody to race.
    callout = ArcadeCallout.load()
    payload = callout_message(icon, now)
    digest = _digest(payload)

    try:
        if callout.message_id and repost_due(callout, now):
            _repost(channel_id, callout, payload, digest, now)
        else:
            _keep_current(channel_id, callout, payload, digest, now)
    except (discord_api.DiscordError, discord_api.DiscordUnavailable) as error:
        _stop_for(error, 'updating the callout')


def _remember(callout, message_id, digest, now, **extra):
    callout.message_id = message_id
    callout.content_hash = digest
    callout.checked_at = now
    for field, value in extra.items():
        setattr(callout, field, value)
    callout.save()


def _keep_current(channel_id, callout, payload, digest, now):
    """Edit the callout when its standings changed, or post it if there is none."""
    unchanged = callout.message_id and callout.content_hash == digest
    checked_recently = callout.checked_at and now - callout.checked_at < timedelta(minutes=conf.CALLOUT_CHECK_MINUTES)

    if unchanged and checked_recently:
        return

    if callout.message_id:
        try:
            message = (
                discord_api.get_message(channel_id, callout.message_id)
                if unchanged
                else discord_api.edit_message(channel_id, callout.message_id, payload)
            )
            _pin(channel_id, message)
            _remember(callout, callout.message_id, digest, now)
            return
        except discord_api.DiscordError as error:
            # A callout staff deleted is a normal thing. Post a new one.
            if error.code != discord_api.UNKNOWN_MESSAGE:
                raise

    message = discord_api.create_message(channel_id, payload)
    _remember(callout, message['id'], digest, now)
    _pin(channel_id, message)


def _repost(channel_id, callout, payload, digest, now):
    """Once a day: move the callout back to the bottom of the channel."""
    today = timezone.localtime(now, EASTERN).date()
    newest = discord_api.get_channel(channel_id).get('last_message_id')

    # Already the newest message: nothing to resurface, just bring it up to date.
    if newest == callout.message_id:
        callout.last_repost_day = today
        callout.checked_at = None
        callout.save(update_fields=['last_repost_day', 'checked_at'])
        _keep_current(channel_id, callout, payload, digest, now)
        return

    old = callout.message_id
    message = discord_api.create_message(channel_id, payload)
    _remember(callout, message['id'], digest, now, last_repost_day=today)

    # Pinned before the old copy goes. Deleting a pinned message takes it out of
    # the pins, so there are never two pinned and never a need to unpin.
    _pin(channel_id, message)

    try:
        discord_api.delete_message(channel_id, old)
    except (discord_api.DiscordError, discord_api.DiscordUnavailable):
        pass  # Already gone, or left for staff. The new callout is up either way.
