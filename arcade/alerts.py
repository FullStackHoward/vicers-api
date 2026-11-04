"""
First-place alerts: a DM to a player when someone takes their first place on
This Season or All Time.

Off unless the player turns them on from the arcade page (ArcadeAlertOptIn). Read
when a run is banked, so nothing is computed later from boards that have moved on,
and sent from the posting tick (arcade/announce.py), so a slow Discord never holds
up a score being saved.

One DM covers both boards when a run takes both. A player gets at most one per
game every ALERT_COOLDOWN_MINUTES, so two players trading first place all evening
do not fill each other's DMs.
"""

import logging
import time
from datetime import timedelta

from . import conf, discord_api, leaderboards
from .models import ArcadeAlertOptIn, PlayerAlert

logger = logging.getLogger(__name__)

ALERTS_PER_TICK = 10

BOARD_NAMES = {'season': 'This Season', 'alltime': 'All Time'}


def alerts_on(discord_user_id):
    return ArcadeAlertOptIn.objects.filter(discord_user_id=str(discord_user_id), enabled=True).exists()


def set_alerts(discord_user_id, enabled):
    row, _ = ArcadeAlertOptIn.objects.update_or_create(
        discord_user_id=str(discord_user_id),
        defaults={'enabled': bool(enabled)},
    )
    return row.enabled


def leaders_before(game, season):
    """Who is first on each board before a run is saved: {'season': Score, 'alltime': Score}."""
    return {
        'season': leaderboards.season_best(game, season),
        'alltime': leaderboards.alltime_best(game),
    }


def queue(saved, leaders):
    """
    After `saved` is banked: an alert for each player it took first place from who
    wants to know. Equal scores do not take first place, because the earlier run
    keeps it.
    """
    lost = {}

    for board, leader in leaders.items():
        if leader is None or leader.discord_user_id == saved.discord_user_id:
            continue
        if saved.score <= leader.score:
            continue
        entry = lost.setdefault(leader.discord_user_id, {'boards': [], 'their_score': leader.score})
        entry['boards'].append(board)
        entry['their_score'] = max(entry['their_score'], leader.score)

    queued = []

    for user, entry in lost.items():
        if not alerts_on(user):
            continue
        queued.append(PlayerAlert.objects.create(
            discord_user_id=user,
            game=saved.game,
            payload={
                'boards': entry['boards'],
                'their_score': entry['their_score'],
                'by': saved.display_name,
                'score': saved.score,
            },
        ))

    return queued


def message(alert):
    from .announce import BRAND_NAME, PLAY_EMOJI, play_url

    game = alert.game
    data = alert.payload
    boards = ' and '.join(BOARD_NAMES.get(board, board) for board in data.get('boards', []))

    return {
        'embeds': [{
            'color': 0xE0A9E8,
            'author': {'name': BRAND_NAME},
            'description': '\n'.join([
                f'## Your first place on {game.display_name} was just taken',
                f'**{data.get("by", "Someone")}** scored **{int(data.get("score", 0)):,}** on '
                f'{boards}, passing your {int(data.get("their_score", 0)):,}.',
                'Think you can take it back?',
            ]),
            'footer': {'text': 'You turned these on at the arcade. Turn them off there any time.'},
        }],
        'components': [{
            'type': 1,
            'components': [{
                'type': 2,
                'style': 5,
                'label': f'Play {game.display_name}',
                'url': play_url(game.slug),
                'emoji': {'name': PLAY_EMOJI},
            }],
        }],
        'allowed_mentions': {'parse': []},
    }


def _skip(alert, reason, now):
    alert.skipped = reason
    alert.handled_at = now
    alert.save(update_fields=['skipped', 'handled_at'])


def _sent_recently(alert, now):
    return (
        PlayerAlert.objects
        .filter(
            discord_user_id=alert.discord_user_id,
            game_id=alert.game_id,
            skipped='',
            handled_at__gte=now - timedelta(minutes=conf.ALERT_COOLDOWN_MINUTES),
        )
        .exclude(pk=alert.pk)
        .exists()
    )


def send_waiting(now, deadline=None):
    """
    Send queued alerts. Returns False when Discord asked to wait, so the caller
    leaves the rest for the next tick.
    """
    waiting = list(
        PlayerAlert.objects
        .filter(handled_at__isnull=True)
        .select_related('game')
        .order_by('created_at', 'id')[:ALERTS_PER_TICK]
    )

    for alert in waiting:
        if deadline is not None and time.monotonic() >= deadline:
            return False

        # Read again at send time: a player may have turned them off since.
        if not alerts_on(alert.discord_user_id):
            _skip(alert, 'alerts_off', now)
            continue

        if now - alert.created_at > timedelta(minutes=conf.ALERT_MAX_AGE_MINUTES):
            _skip(alert, 'stale', now)
            continue

        if _sent_recently(alert, now):
            _skip(alert, 'cooldown', now)
            continue

        try:
            channel = discord_api.open_dm(alert.discord_user_id)
            discord_api.create_message(channel['id'], message(alert))
        except discord_api.DiscordUnavailable as error:
            logger.warning('Vice Arcade: first place alert waiting on Discord: %s', error)
            return False
        except discord_api.DiscordError as error:
            if error.status == 429 or (error.status or 0) >= 500:
                logger.warning('Vice Arcade: first place alert waiting on Discord: %s', error)
                return False
            reason = 'dms_closed' if error.code == discord_api.CANNOT_DM else 'failed'
            logger.info('Vice Arcade: first place alert to %s not sent: %s', alert.discord_user_id, error)
            _skip(alert, reason, now)
            continue

        alert.handled_at = now
        alert.save(update_fields=['handled_at'])

    return True
