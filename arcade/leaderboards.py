"""
The three boards: Daily, season, and All-Time.

A score is on the boards, and counts towards the podium, as soon as it is banked
and for as long as it is not removed. There is no review step.

Departed players need no special case here. A departure marks their scores
removed, so every query below stops showing them and everyone underneath moves up.

Ranking is deduped in Python rather than with a window function. One row per
player per board means picking each player's best score in the window, and the
candidate set for a Discord community is small enough that the clearer code wins.
"""

from .models import DailyBoard, PersonalBest, Score

TOP_N = 10

# A player who did not play a game, or finished outside the top 10 of it, is
# treated as this placing when averaging across games. It stops a busy game from
# outweighing a quiet one.
PLACEMENT_CAP = 11


def visible_scores(game=None):
    """Everything a board shows and the podium counts: anything not removed."""
    queryset = Score.objects.filter(removed=False)
    if game is not None:
        queryset = queryset.filter(game=game)
    return queryset


def _rank(queryset):
    """
    One row per player, best score first, and for equal scores the one reached
    first ranks higher.

    The ordering does the tie-break: sorting by score descending then `created_at`
    ascending means the first row seen for a player is their best, and the first
    row overall is the rightful leader.
    """
    rows = []
    seen = set()

    for score in queryset.order_by('-score', 'created_at', 'id'):
        if score.discord_user_id in seen:
            continue
        seen.add(score.discord_user_id)
        rows.append({
            'place': len(rows) + 1,
            'discord_user_id': score.discord_user_id,
            'display_name': score.display_name,
            'score': score.score,
            'score_id': score.id,
            'achieved_at': score.created_at,
        })

    return rows


def window_rows(game, start, end):
    """Ranked rows for one game inside a half-open instant range."""
    return _rank(visible_scores(game).filter(created_at__gte=start, created_at__lt=end))


def alltime_rows(game):
    """
    Ranked rows with no window: each player's best score ever.

    Computed from Score rather than from PersonalBest so that removing a score,
    whether by review or by a departure, corrects this board with no extra step.
    """
    return _rank(visible_scores(game))


def daily_rows(game, day):
    from . import seasons

    start, end = seasons.eastern_day_bounds(day)
    return window_rows(game, start, end)


def season_rows(game, season):
    return window_rows(game, season.starts_at, season.ends_at)


def board_view(rows, discord_user_id=None):
    """
    The shape the Activity and the bot both consume: the top ten, the current
    leader, and the requesting player's own rank even when it is below the cut.
    """
    top = rows[:TOP_N]
    view = {
        'leader': rows[0] if rows else None,
        'top': top,
        'total_players': len(rows),
        'you': None,
    }

    if discord_user_id is not None:
        for row in rows:
            if row['discord_user_id'] == discord_user_id:
                view['you'] = row
                break

    return view


def placements(rows, cap=PLACEMENT_CAP):
    """
    Player id to placing, for the podium average. Anyone past the top ten is
    capped, and anyone absent is given the cap by the caller.
    """
    result = {}

    for row in rows:
        if row['place'] <= TOP_N:
            result[row['discord_user_id']] = row['place']
        else:
            result[row['discord_user_id']] = cap

    return result


def recompute_personal_best(discord_user_id, game):
    """
    Rebuild one player's stored best from their visible scores.

    Called after anything that can invalidate it: an owner removal or a departure. Returns the PersonalBest row, or None when the player has no
    visible scores left, in which case the row is deleted.
    """
    best = (
        visible_scores(game)
        .filter(discord_user_id=discord_user_id)
        .order_by('-score', 'created_at', 'id')
        .first()
    )

    if best is None:
        PersonalBest.objects.filter(discord_user_id=discord_user_id, game=game).delete()
        return None

    row, _ = PersonalBest.objects.update_or_create(
        discord_user_id=discord_user_id,
        game=game,
        defaults={
            'best_score': best.score,
            'score': best,
            'achieved_at': best.created_at,
        },
    )
    return row


def alltime_best(game):
    """The score to beat for the record check."""
    return visible_scores(game).order_by('-score', 'created_at', 'id').first()


def strip_player_from_daily_boards(discord_user_id):
    """
    Remove a departed player from every stored Daily board and renumber the rest.

    Daily boards are renumbered, unlike a finalized podium, because a posted board
    is a leaderboard and has to read correctly after the edit.

    Returns the boards that actually changed, so only those messages are edited.
    """
    from . import seasons

    # Narrowed to the Eastern days this player actually scored on, which is a small
    # set, rather than scanning every board ever stored. Their score rows are still
    # present at this point, only marked removed, so the days are still knowable.
    days = {
        seasons.eastern_date(created_at)
        for created_at in Score.objects
        .filter(discord_user_id=discord_user_id)
        .values_list('created_at', flat=True)
    }

    changed = []

    for board in DailyBoard.objects.filter(day__in=days):
        kept = [e for e in board.entries if e.get('discord_user_id') != discord_user_id]
        if len(kept) == len(board.entries):
            continue

        for index, entry in enumerate(kept, start=1):
            entry['place'] = index

        board.entries = kept
        board.save(update_fields=['entries'])
        changed.append(board)

    return changed
