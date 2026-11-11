"""
Who holds each game's all-time record, for the record roles.

One role per game (Vetrix Master, Neon Breaker Master, ...), set on the game in
Django admin, held by whoever is top of that game's All Time board. The bot makes
the role match this, the same way it makes the podium roles match the standings:
read fresh every pass, so a missed event, a removed score or a holder who left
the server is corrected without anything special.

The Arcade Champion, Silver and Bronze roles are separate and unchanged. They go
by average placing across every game (arcade/podium.py), which is what keeps them
fair as cabinets are added; a record role is about one game only.
"""

from . import leaderboards
from .models import ArcadeGame


def current_records():
    """
    Every game, with its record role and its current record holder.

    A game that is switched off still appears, with no holder, so its role comes
    off rather than being left on whoever held it last.
    """
    records = []

    for game in ArcadeGame.objects.order_by('display_name'):
        best = leaderboards.alltime_best(game) if game.is_active else None

        records.append({
            'game_slug': game.slug,
            'game_display_name': game.display_name,
            'role_id': game.record_role_id or None,
            'holder': None if best is None else {
                'discord_user_id': best.discord_user_id,
                'display_name': best.display_name,
                'score': best.score,
                'score_id': best.id,
                'achieved_at': best.created_at.isoformat(),
            },
        })

    return records
