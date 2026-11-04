"""
The owner's /arcade commands: wipe today's boards, wipe the season's, or reset
everything.

The two wipes delete nothing. They record a BoardWipe, and the board's window
starts there from then on (arcade/leaderboards.py), so the runs stay on All Time
and the wipe can be read back in admin. Wiping the season also wipes today,
because today is part of the season and a run that is gone from This Season but
still first on Today would make no sense.

A reset is the opposite: every score, personal best, board and season is deleted,
a fresh season starts, and the arcade channel is emptied a little at a time by the
posting tick (ChannelPurge, arcade/announce.py). Player alert settings and the
record of who has left the server are kept.
"""

from django.db import transaction
from django.utils import timezone

from . import conf, leaderboards, podium, rollover, seasons
from .models import (
    ArcadeCallout,
    ArcadeEvent,
    ArcadeGame,
    ArcadeSeason,
    ArcadeSettings,
    BoardWipe,
    ChannelPurge,
    DailyBoard,
    EventType,
    PersonalBest,
    PlayerAlert,
    PlaySession,
    Score,
    SeasonStatus,
    WipedBoard,
)

# Discord's epoch, for turning the reset's time into a message id to delete before.
DISCORD_EPOCH_MS = 1420070400000


def snowflake_at(moment):
    """The smallest Discord id that could have been made at this instant."""
    return str((int(moment.timestamp() * 1000) - DISCORD_EPOCH_MS) << 22)


def _runs_on(board_rows):
    return sum(len(rows) for rows in board_rows)


@transaction.atomic
def wipe_today(by='', now=None):
    """Clear Today for every game. Returns how many players were on it."""
    now = now or timezone.now()
    today = seasons.eastern_date(now)
    players = _runs_on(
        leaderboards.daily_rows(game, today) for game in ArcadeGame.objects.filter(is_active=True)
    )

    BoardWipe.objects.create(board=WipedBoard.DAILY, wiped_at=now, wiped_by=str(by))

    return {'players': players}


@transaction.atomic
def wipe_season(by='', now=None):
    """
    Clear This Season and Today for every game. Returns how many players were on
    the season's boards, and the podium change the bot turns into roles.
    """
    now = now or timezone.now()
    season = rollover.ensure_current_season(now)
    players = _runs_on(
        leaderboards.season_rows(game, season) for game in ArcadeGame.objects.filter(is_active=True)
    )

    BoardWipe.objects.create(board=WipedBoard.SEASON, wiped_at=now, wiped_by=str(by))
    BoardWipe.objects.create(board=WipedBoard.DAILY, wiped_at=now, wiped_by=str(by))

    # Nobody is on the board any more, so the roles come off.
    podium.refresh(season)

    return {'players': players, 'season': season}


@transaction.atomic
def reset_everything(by='', now=None, application_id='', interaction_token=''):
    """
    Delete every score and everything built from them, start a fresh season, and
    queue the arcade channel to be emptied. Returns the counts and the new season.
    """
    now = now or timezone.now()
    runs = Score.objects.count()

    # Nothing the bot was about to post still means anything, and a season it was
    # about to finalize is about to stop existing.
    ArcadeEvent.objects.filter(handled_at__isnull=True).update(handled_at=now)
    PlayerAlert.objects.filter(handled_at__isnull=True).update(handled_at=now, skipped='reset')

    # Scores take their run posts and personal bests with them. Seasons take their
    # podium results. Sessions go too, so a run started before the reset cannot land
    # on the fresh boards afterwards.
    Score.objects.all().delete()
    PersonalBest.objects.all().delete()
    PlaySession.objects.all().delete()
    DailyBoard.objects.all().delete()
    ArcadeSeason.objects.all().delete()
    BoardWipe.objects.all().delete()

    interval = ArcadeSettings.load().reset_interval_days
    season = ArcadeSeason.objects.create(
        starts_at=now,
        ends_at=seasons.fresh_season_end(now, interval),
        status=SeasonStatus.OPEN,
    )

    # The fresh board is empty, so this tells the bot to take the podium roles off.
    ArcadeEvent.objects.create(
        type=EventType.PODIUM_CHANGED,
        payload={'season_id': season.id, 'top': [], 'reset': True},
    )

    # Any purge still running from an earlier reset is folded into this one. With
    # no arcade channel set there is nothing to empty.
    ChannelPurge.objects.filter(finished_at__isnull=True).update(finished_at=now, interaction_token='')
    purge = None
    if conf.arcade_channel_id():
        purge = ChannelPurge.objects.create(
            requested_at=now,
            requested_by=str(by),
            before_message_id=snowflake_at(now),
            application_id=str(application_id or ''),
            interaction_token=str(interaction_token or ''),
        )

    # The pinned post is about to be deleted with everything else. Forgetting it
    # here means a fresh one is posted once the channel is clear.
    callout = ArcadeCallout.load()
    callout.message_id = ''
    callout.content_hash = ''
    callout.checked_at = None
    callout.save()

    return {'runs': runs, 'season': season, 'purge': purge}
