"""
Test helpers.

`created_at` is `auto_now_add`, so a score cannot be born at a chosen instant.
Every test that cares about a window therefore writes the timestamp back with an
UPDATE, which is also what keeps these tests free of sleeping or clock patching.
"""

from datetime import date, timedelta

from arcade import seasons
from arcade.models import ArcadeGame, ArcadeSeason, Score, SeasonStatus


def eastern(year, month, day, hour=0, minute=0):
    return seasons.localize(date(year, month, day), hour, minute)


def make_game(slug='vetrix', display_name='Vetrix', active_from=None, is_active=True):
    return ArcadeGame.objects.create(
        slug=slug,
        display_name=display_name,
        is_active=is_active,
        active_from=active_from or eastern(2020, 1, 1),
        plausibility={},
    )


def make_season(starts_at, ends_at=None, interval_days=7, status=SeasonStatus.OPEN):
    return ArcadeSeason.objects.create(
        starts_at=starts_at,
        ends_at=ends_at or seasons.season_end_for(starts_at, interval_days),
        status=status,
    )


def make_score(game, user, value, created_at, name=None, approved=True,
               flagged=False, removed=False, removal_reason=''):
    score = Score.objects.create(
        discord_user_id=user,
        display_name=name or f'player-{user}',
        game=game,
        score=value,
        approved=approved,
        flagged=flagged,
        removed=removed,
        removal_reason=removal_reason,
    )
    Score.objects.filter(pk=score.pk).update(created_at=created_at)
    score.refresh_from_db()
    return score
