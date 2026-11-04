"""
Daily boards: one snapshot per game per closed Eastern day.

Finalized lazily and idempotently, like seasons, and for the same reason: the
bot's thirty-second poll is a reliable heartbeat, and a unique constraint over
(game, day) makes a repeated or concurrent pass harmless.

A board lists everyone who played that day, not a top ten, because that is what
gets posted in the channel. A day nobody played produces no row and therefore no
post.
"""

from django.db import IntegrityError, transaction

from . import leaderboards, seasons
from .models import ArcadeEvent, ArcadeGame, DailyBoard, EventType, Score

# A guard against a bad clock or a very old first score turning one request into a
# multi-year backfill. Older days are picked up on later calls.
MAX_DAYS_PER_PASS = 60


def _entries_for(game, day):
    rows = leaderboards.daily_rows(game, day)
    return [
        {
            'place': row['place'],
            'discord_user_id': row['discord_user_id'],
            'display_name': row['display_name'],
            'score': row['score'],
        }
        for row in rows
    ]


def _first_day_needing_a_board(game):
    """
    Where to start looking: the day after the most recent stored board, or the day
    of this game's earliest visible score when there is no board yet.
    """
    latest = DailyBoard.objects.filter(game=game).order_by('-day').first()
    if latest is not None:
        return latest.day + seasons.DAY

    earliest = (
        leaderboards.visible_scores(game)
        .order_by('created_at')
        .values_list('created_at', flat=True)
        .first()
    )
    if earliest is None:
        return None

    return seasons.eastern_date(earliest)


def finalize_due_boards(now):
    """
    Snapshot every closed day that has no board yet, oldest first.

    Returns the events written, so a caller can see what the bot will be asked to
    post. A day with no scores is skipped silently and never revisited, because
    the start point walks forward from the latest stored board.
    """
    events = []

    for game in ArcadeGame.objects.all():
        start = _first_day_needing_a_board(game)
        if start is None:
            continue

        for day in seasons.closed_days_between(start, now)[:MAX_DAYS_PER_PASS]:
            day_start, day_end = seasons.eastern_day_bounds(day)

            # A cabinet that was not live that day has nothing to report.
            if day_end <= game.active_from:
                continue

            entries = _entries_for(game, day)
            if not entries:
                continue

            try:
                with transaction.atomic():
                    board = DailyBoard.objects.create(game=game, day=day, entries=entries)
            except IntegrityError:
                # Another request finalized this day first.
                continue

            events.append(ArcadeEvent.objects.create(
                type=EventType.DAILY_BOARD_CLOSED,
                payload={
                    'board_id': board.id,
                    'game_slug': game.slug,
                    'game_display_name': game.display_name,
                    'day': day.isoformat(),
                },
            ))

    return events


def record_posted_messages(board, message_ids):
    """
    Remember which Discord messages a board was posted as, so a departure can edit
    them later rather than leaving a stale leaderboard in the channel.
    """
    board.message_ids = list(message_ids)
    board.save(update_fields=['message_ids'])
    return board
