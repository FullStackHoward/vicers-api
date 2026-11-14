"""
Seeds the Dolphin Dream and Sunset Shatter cabinets.

Each `active_from` is the moment this migration runs, so the games are on the
boards at once but only count toward the Arcade Champion standings from the next
season. A cabinet that appears mid-season must not reshuffle a podium that is
nearly decided (`ArcadeGame.counts_for_season`).

Dolphin Dream's plausibility limits:

- `max_score_per_column` at 2. A column pays 1, or 2 for a clean pass, so no
  honest run exceeds 2 per column.
- `min_ms_per_column` at 700. The columns scroll no faster than 2.0 units per
  tick and sit 100 units apart, which is 833 ms per column at the speed cap. 700
  leaves room. It is derived from the game's starting tuning, so it has to be
  re-derived if that tuning changes.
- `max_level` is a typo guard, as for the other games.

Sunset Shatter's plausibility limits:

- `max_score_per_kill` at 5100. The best single kill is a small saucer at 1000
  points times the heat multiplier cap of 5, which is 5000, plus about 32 points
  of level-clear bonus share.
- `min_ms_per_kill` at 100. Bullets fire at most every 8 ticks (133 ms), so a
  kill every 100 ms is not reachable. Derived from the game's starting tuning.
- `max_level` is a typo guard, as for the other games.
"""

from django.db import migrations
from django.utils import timezone


def seed(apps, schema_editor):
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')

    ArcadeGame.objects.get_or_create(
        slug='dolphin-dream',
        defaults={
            'display_name': 'Dolphin Dream',
            'is_active': True,
            'active_from': timezone.now(),
            'plausibility': {
                'max_score_per_column': 2,
                'min_ms_per_column': 700,
                'max_level': 99,
            },
        },
    )
    ArcadeGame.objects.get_or_create(
        slug='sunset-shatter',
        defaults={
            'display_name': 'Sunset Shatter',
            'is_active': True,
            'active_from': timezone.now(),
            'plausibility': {
                'max_score_per_kill': 5100,
                'min_ms_per_kill': 100,
                'max_level': 99,
            },
        },
    )


def unseed(apps, schema_editor):
    # Warning: deleting a game row cascades to its scores (`Score.game` is
    # `on_delete=CASCADE`), so reversing this migration also deletes every score
    # banked on these two games.
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')
    ArcadeGame.objects.filter(slug__in=['dolphin-dream', 'sunset-shatter']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('arcade', '0008_neon_breaker_and_record_roles'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
