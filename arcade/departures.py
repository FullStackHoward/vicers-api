"""
Section 5: anyone who is not in Vice Gamers appears on no leaderboard.

This is the only destructive path in the arcade, so two choices are deliberate.

Scores are marked, not deleted. `removed=True` with `removal_reason='member_left'`
hides them from every board while leaving a way back if a departure signal turns
out to be false, which is the failure this feature is most likely to produce. A
score the owner removed by hand keeps its own reason and is never relabelled.

Departures are history, not a flag. A score is rejected when its session was
issued before the player's most recent departure, so a player who rejoins has to
have their later sessions count again, and a single boolean could not express
that.

The caller is responsible for being sure. The bot confirms a departure against
Discord before calling in, and its daily sweep abandons itself when the member
list comes back short, because treating current members as departed would wipe
real scores.
"""

from django.db import transaction

from . import leaderboards, podium, rollover
from .models import (
    ArcadeAlertOptIn,
    ArcadeEvent,
    EventType,
    MemberDeparture,
    PersonalBest,
    PlaySession,
    PodiumResult,
    RemovalReason,
    Score,
)


def session_invalidated_by_departure(discord_user_id, issued_at):
    """
    True when a session predates the player's latest departure.

    This is what rejects a run that was in flight when someone left, and what lets
    a rejoiner's new sessions count while their old ones stay dead.
    """
    latest = (
        MemberDeparture.objects
        .filter(discord_user_id=discord_user_id)
        .order_by('-left_at')
        .values_list('left_at', flat=True)
        .first()
    )
    return latest is not None and issued_at < latest


def active_player_ids():
    """
    Every player the boards or stored history still show, for the bot's daily sweep
    to compare against the real member list.
    """
    from_scores = set(
        leaderboards.visible_scores()
        .values_list('discord_user_id', flat=True)
        .distinct()
    )
    from_history = set(
        PodiumResult.objects
        .filter(hidden=False)
        .values_list('discord_user_id', flat=True)
        .distinct()
    )
    return sorted(from_scores | from_history)


@transaction.atomic
def record_departure(discord_user_id, left_at):
    """
    Remove one player from every board, every stored snapshot, and every pending
    run, then let the podium and the posted boards catch up.

    Safe to repeat: the departure row is keyed on the pair, and each removal step
    is already a no-op the second time.
    """
    departure, _ = MemberDeparture.objects.get_or_create(
        discord_user_id=discord_user_id,
        left_at=left_at,
    )

    # Scores the owner removed by hand keep their own reason.
    removed_scores = Score.objects.filter(
        discord_user_id=discord_user_id,
        removed=False,
    ).update(removed=True, removal_reason=RemovalReason.MEMBER_LEFT)

    PersonalBest.objects.filter(discord_user_id=discord_user_id).delete()
    ArcadeAlertOptIn.objects.filter(discord_user_id=discord_user_id).delete()

    # A run in progress cannot be submitted. Marked rather than deleted so the row
    # still explains itself if anyone goes looking.
    open_sessions = PlaySession.objects.filter(
        discord_user_id=discord_user_id,
        used=False,
    ).update(used=True)

    # Finalized rounds keep their places. The row is hidden and nobody moves up,
    # which is deliberately unlike a Daily board.
    hidden_results = PodiumResult.objects.filter(
        discord_user_id=discord_user_id,
        hidden=False,
    ).update(hidden=True)

    # Daily boards are leaderboards, so these are renumbered and the posted
    # messages get edited to match.
    changed_boards = leaderboards.strip_player_from_daily_boards(discord_user_id)

    boards_event = None
    if changed_boards:
        boards_event = ArcadeEvent.objects.create(
            type=EventType.DAILY_BOARDS_CHANGED,
            payload={
                'reason': 'member_left',
                'discord_user_id': discord_user_id,
                'boards': [
                    {
                        'board_id': board.id,
                        'game_slug': board.game.slug,
                        'game_display_name': board.game.display_name,
                        'day': board.day.isoformat(),
                        'message_ids': list(board.message_ids or []),
                    }
                    for board in changed_boards
                ],
            },
        )

    # Everyone below moves up. If that disturbs the top three, one event goes out
    # and the next player takes the role. No record post: nobody beat a score.
    season = rollover.ensure_current_season(left_at)
    podium_event = podium.refresh(season)

    return {
        'departure': departure,
        'scores_removed': removed_scores,
        'sessions_closed': open_sessions,
        'results_hidden': hidden_results,
        'boards_changed': changed_boards,
        'boards_event': boards_event,
        'podium_event': podium_event,
    }


def record_departures(entries):
    """
    A batch from the bot, which sends `{discord_user_id, left_at}` pairs. Used by
    the immediate listener, the startup sweep and the daily sweep alike.
    """
    return [record_departure(entry['discord_user_id'], entry['left_at']) for entry in entries]
