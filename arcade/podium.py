"""
Live standings and the three podium roles.

The roles follow the board continuously, so there is no rollover ceremony here.
Every change that could move the top three recomputes it, compares against what
the bot was last told, and writes a single `podium_changed` event if and only if
the top three actually moved. A burst of scores therefore produces one role call,
not one per score.

Ranking is average placement across the games that count for the season, capped,
so adding a cabinet never adds a role. With one game live this reduces to that
game's season rank, which is the case in v1.
"""

from django.db import transaction

from . import leaderboards
from .models import ArcadeEvent, ArcadeGame, EventType

PODIUM_PLACES = 3


def counted_games(season):
    """
    The games whose boards feed this season's standings.

    A cabinet added mid-season does not count until the next one, so a quiet game
    launched on day six cannot reshuffle a round that is nearly over.
    """
    return [
        game for game in ArcadeGame.objects.all()
        if game.counts_for_season(season)
    ]


def standings(season, games=None):
    """
    Every player with a counting score this season, best average placement first.

    Only approved scores count, so a record still awaiting review shows on the
    board with its marker but cannot hand anyone a role.
    """
    games = counted_games(season) if games is None else games
    if not games:
        return []

    cap = leaderboards.PLACEMENT_CAP
    per_game = {}
    names = {}
    games_played = {}
    last_score_at = {}

    for game in games:
        rows = leaderboards.season_rows(game, season, counting_only=True)
        per_game[game.slug] = leaderboards.placements(rows, cap=cap)

        for row in rows:
            user = row['discord_user_id']
            names[user] = row['display_name']
            games_played[user] = games_played.get(user, 0) + 1
            achieved = row['achieved_at']
            if user not in last_score_at or achieved > last_score_at[user]:
                last_score_at[user] = achieved

    rows = []

    for user, display_name in names.items():
        breakdown = {
            slug: placings.get(user, cap)
            for slug, placings in per_game.items()
        }
        average = sum(breakdown.values()) / len(breakdown)

        rows.append({
            'discord_user_id': user,
            'display_name': display_name,
            'average_placement': average,
            'breakdown': breakdown,
            'games_played': games_played[user],
            'last_score_at': last_score_at[user],
        })

    # Lower average wins. Ties go to more games played, then to the earlier last
    # counting score, so the player who got there and stopped beats the one who
    # matched them later.
    rows.sort(key=lambda row: (
        row['average_placement'],
        -row['games_played'],
        row['last_score_at'],
    ))

    for index, row in enumerate(rows, start=1):
        row['place'] = index

    return rows


def top_three(season, games=None):
    return standings(season, games=games)[:PODIUM_PLACES]


def _identity(rows):
    """
    What makes two podiums the same for the purpose of calling Discord: who holds
    which place. A player's average shifting without changing the order is not a
    role change and must not produce an event.
    """
    return [
        {'place': row['place'], 'discord_user_id': row['discord_user_id']}
        for row in rows
    ]


def refresh(season, games=None):
    """
    Recompute the live top three and write one `podium_changed` event if it moved.

    Returns the event when one was written, otherwise None. Safe to call after
    every score, approval, removal and departure, which is exactly how it is used.
    """
    current = top_three(season, games=games)
    identity = _identity(current)

    if identity == list(season.live_top or []):
        return None

    with transaction.atomic():
        season.live_top = identity
        season.save(update_fields=['live_top'])

        return ArcadeEvent.objects.create(
            type=EventType.PODIUM_CHANGED,
            payload={
                'season_id': season.id,
                'top': [
                    {
                        'place': row['place'],
                        'discord_user_id': row['discord_user_id'],
                        'display_name': row['display_name'],
                        'average_placement': row['average_placement'],
                        'games_played': row['games_played'],
                    }
                    for row in current
                ],
            },
        )


def current_holders(season):
    """
    What the bot makes the roles match. Reads the live standings rather than the
    stored snapshot, so a reconciliation pass corrects a missed event.
    """
    return top_three(season)
