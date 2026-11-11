"""
Opening, closing and finalizing seasons, lazily.

There is no cron job. Every entry point that could care about the clock calls
`ensure_current_season`, and the bot polls `GET /arcade/events/pending` every
thirty seconds, which makes that poll the heartbeat. The usual objection to lazy
rollover is that an idle API never advances; it cancels itself here, because when
the bot is down nothing needs advancing, since nothing can be posted.

Finalization is deliberately a two-party handshake. The API cannot see Discord
membership, so it closes the season and says so, and the bot confirms the winners
are still in the server before anything is written or announced. A cron job could
not do that half, which is the real reason lazy wins rather than mere taste.

Every operation here is idempotent. Concurrent callers collide on the unique
constraint over `ArcadeSeason.starts_at` and the loser re-reads.
"""

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import podium, seasons
from .models import (
    ArcadeEvent,
    ArcadeSeason,
    ArcadeSettings,
    EventType,
    PodiumResult,
    SeasonStatus,
)

# How many consecutive missed seasons the chain will build in one pass. A bot
# offline for months should still catch up, but an unbounded loop on a bad clock
# would hang a request, so the work is capped and resumed on the next call.
MAX_CATCH_UP = 400


def first_season(now):
    """
    The opening round ends at the first Sunday 5 PM Eastern after go-live, so a
    partial first week is still awarded, as already decided.
    """
    return ArcadeSeason.objects.create(
        starts_at=now,
        ends_at=seasons.first_season_end(now),
        status=SeasonStatus.OPEN,
    )


def _close(season):
    """
    Mark a finished season as awaiting its winner check and say so once.

    Only an open season produces an event, so a repeated pass over an already
    closing or finalized season is silent.
    """
    if season.status != SeasonStatus.OPEN:
        return None

    season.status = SeasonStatus.CLOSING
    season.save(update_fields=['status'])

    return ArcadeEvent.objects.create(
        type=EventType.SEASON_CLOSING,
        payload={
            'season_id': season.id,
            'starts_at': season.starts_at.isoformat(),
            'ends_at': season.ends_at.isoformat(),
        },
    )


def ensure_current_season(now=None):
    """
    The open season containing `now`, creating and closing whatever it takes to
    get there.

    Boards therefore switch on time whether or not the bot is running, which is
    what the amendment requires: the new board is empty, the podium endpoint
    returns nobody, and the bot clears the roles when it comes back.
    """
    now = now or timezone.now()

    with transaction.atomic():
        season = ArcadeSeason.objects.order_by('-starts_at').first()

        if season is None:
            return first_season(now)

        interval = ArcadeSettings.load().reset_interval_days

        for _ in range(MAX_CATCH_UP):
            if season.ends_at > now:
                return season

            _close(season)

            starts_at = season.ends_at
            try:
                with transaction.atomic():
                    season = ArcadeSeason.objects.create(
                        starts_at=starts_at,
                        ends_at=seasons.season_end_for(starts_at, interval),
                        status=SeasonStatus.OPEN,
                    )
            except IntegrityError:
                # Another request built this one first. Take theirs and carry on.
                season = ArcadeSeason.objects.get(starts_at=starts_at)

        return season


def closing_seasons():
    """Finished seasons still waiting on their winner check, oldest first."""
    return ArcadeSeason.objects.filter(status=SeasonStatus.CLOSING).order_by('starts_at')


def preview(season):
    """
    The standings a season finished on, for the bot's winner check.

    Read before finalizing so the bot can look each player up in Vice Gamers and
    report anyone who has left, then ask again.
    """
    return podium.top_three(season)


def finalize(season, winners):
    """
    Store a finished season's podium and announce it.

    `winners` comes from the bot, which has already confirmed each player is still
    in Vice Gamers. Repeat calls are safe and write no second event, because the
    bot may well retry after a timeout it never saw the result of.
    """
    if season.status == SeasonStatus.FINALIZED:
        return None

    with transaction.atomic():
        locked = ArcadeSeason.objects.select_for_update().get(pk=season.pk)
        if locked.status == SeasonStatus.FINALIZED:
            return None

        PodiumResult.objects.filter(season=locked).delete()

        for place, winner in enumerate(winners[:podium.PODIUM_PLACES], start=1):
            PodiumResult.objects.create(
                season=locked,
                place=place,
                discord_user_id=winner['discord_user_id'],
                display_name=winner['display_name'],
                average_placement=winner['average_placement'],
                breakdown=winner.get('breakdown', {}),
            )

        locked.status = SeasonStatus.FINALIZED
        locked.finalized_at = timezone.now()
        locked.save(update_fields=['status', 'finalized_at'])

        if not winners:
            # A round nobody played gets no callout.
            return None

        return ArcadeEvent.objects.create(
            type=EventType.SEASON_AWARDED,
            payload={
                'season_id': locked.id,
                'revision': locked.revision,
                'starts_at': locked.starts_at.isoformat(),
                'ends_at': locked.ends_at.isoformat(),
                # How many games the placings were averaged over, so the callout can
                # show the average when it means something and leave it out when
                # there was only one game.
                'games_counted': len(podium.counted_games(locked)),
                'winners': [
                    {
                        'place': index,
                        'discord_user_id': winner['discord_user_id'],
                        'display_name': winner['display_name'],
                        'average_placement': winner['average_placement'],
                    }
                    for index, winner in enumerate(winners[:podium.PODIUM_PLACES], start=1)
                ],
            },
        )


def recompute_finalized(season):
    """
    Correct an already finalized season after a late approval or removal.

    The stored history changes and a corrected callout goes out, but live roles are
    untouched, because they now belong to the season that is running.
    """
    if season.status != SeasonStatus.FINALIZED:
        return None

    fresh = podium.top_three(season)
    existing = [
        {'place': row.place, 'discord_user_id': row.discord_user_id}
        for row in PodiumResult.objects.filter(season=season, hidden=False).order_by('place')
    ]
    proposed = [
        {'place': index, 'discord_user_id': row['discord_user_id']}
        for index, row in enumerate(fresh, start=1)
    ]

    if existing == proposed:
        return None

    with transaction.atomic():
        PodiumResult.objects.filter(season=season).delete()

        for place, winner in enumerate(fresh, start=1):
            PodiumResult.objects.create(
                season=season,
                place=place,
                discord_user_id=winner['discord_user_id'],
                display_name=winner['display_name'],
                average_placement=winner['average_placement'],
                breakdown=winner.get('breakdown', {}),
            )

        season.revision = season.revision + 1
        season.save(update_fields=['revision'])

        return ArcadeEvent.objects.create(
            type=EventType.SEASON_AWARDED,
            payload={
                'season_id': season.id,
                'revision': season.revision,
                'corrected': True,
                'games_counted': len(podium.counted_games(season)),
                'winners': [
                    {
                        'place': index,
                        'discord_user_id': winner['discord_user_id'],
                        'display_name': winner['display_name'],
                        'average_placement': winner['average_placement'],
                    }
                    for index, winner in enumerate(fresh, start=1)
                ],
            },
        )


def apply_interval_change(now=None):
    """
    Move the open season's end after the interval setting changes.

    Recomputed from the season's start, so raising 7 to 14 on day three pushes the
    reset out by exactly seven days. Lowering it can land in the past, which must
    never fire a reset nobody saw coming, so `seasons.revised_season_end` pushes
    those to the next 5 PM Eastern at least an hour out.

    Returns the new end, for the admin confirmation message.
    """
    now = now or timezone.now()
    season = ensure_current_season(now)
    interval = ArcadeSettings.load().reset_interval_days

    revised = seasons.revised_season_end(season.starts_at, interval, now)

    if revised != season.ends_at:
        season.ends_at = revised
        season.save(update_fields=['ends_at'])

    return revised
