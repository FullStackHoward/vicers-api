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
    """
    Idempotent on slug, because migration 0002 seeds the real Vetrix row and the
    test database carries it. Reusing that row rather than adding a second one
    also keeps the game counts these tests assert on honest.
    """
    game, _ = ArcadeGame.objects.update_or_create(
        slug=slug,
        defaults={
            'display_name': display_name,
            'is_active': is_active,
            'active_from': active_from or eastern(2020, 1, 1),
            'plausibility': {},
        },
    )
    return game


def only_games(*games):
    """
    Drop every cabinet except these, for the handful of tests that assert on the
    whole set of counted games rather than on one of them.
    """
    keep = [game.pk for game in games]
    ArcadeGame.objects.exclude(pk__in=keep).delete()


def make_season(starts_at, ends_at=None, interval_days=7, status=SeasonStatus.OPEN):
    return ArcadeSeason.objects.create(
        starts_at=starts_at,
        ends_at=ends_at or seasons.season_end_for(starts_at, interval_days),
        status=status,
    )


def make_score(game, user, value, created_at, name=None, removed=False, removal_reason=''):
    score = Score.objects.create(
        discord_user_id=user,
        display_name=name or f'player-{user}',
        game=game,
        score=value,
        removed=removed,
        removal_reason=removal_reason,
    )
    Score.objects.filter(pk=score.pk).update(created_at=created_at)
    score.refresh_from_db()
    return score
