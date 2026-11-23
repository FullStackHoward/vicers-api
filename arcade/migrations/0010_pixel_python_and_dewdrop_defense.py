"""
Seeds the Pixel Python and Dewdrop Defense cabinets.

Each `active_from` is the moment this migration runs, so the games are on the
boards at once but only count toward the Arcade Champion standings from the next
season. A cabinet that appears mid-season must not reshuffle a podium that is
nearly decided (`ArcadeGame.counts_for_season`).

Every limit below is derived from the game's starting tuning, which nobody has
playtested, so it has to be re-derived if that tuning changes.

Pixel Python's plausibility limits (a `unit_limits` entry on `foods_eaten`):

- `max_score_each` at 210. The best single food is a golden fruit worth 50 at
  the streak multiplier cap of 4, which is 200. An apple at the cap is 40, plus
  at most 50 of stage-clear bonus share, which is 90. 210 leaves 10 of slack.
- `min_ms_each` at 135. Two foods are never closer than three cells apart and
  the fastest snake moves one cell per 3 ticks, so three moves is 9 ticks, which
  is 150 ms. 135 leaves slack.
- `max_level` is a typo guard, as for the other games.

Dewdrop Defense's plausibility limits (it counts `kills`, as Sunset Shatter does):

- `max_score_per_kill` at 1300. The best single kill is the mystery globe at 300
  points times the streak cap of 4, which is 1,200, plus about 37 of wave-clear
  bonus share (2,000 over the 55 pods a wave needs). 1,300 leaves slack.
- `min_ms_per_kill` at 120. The fastest fire cooldown is 8 ticks, which is 133
  ms, and a shot kills at most one target. 120 leaves slack.
- `max_level` is a typo guard, as for the other games.
"""

from django.db import migrations
from django.utils import timezone


def seed(apps, schema_editor):
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')

    ArcadeGame.objects.get_or_create(
        slug='pixel-python',
        defaults={
            'display_name': 'Pixel Python',
            'is_active': True,
            'active_from': timezone.now(),
            'plausibility': {
                'max_level': 99,
                'unit_limits': [
                    {
                        'stat': 'foods_eaten',
                        'label': 'foods',
                        'max_score_each': 210,
                        'min_ms_each': 135,
                    },
                ],
            },
        },
    )
    ArcadeGame.objects.get_or_create(
        slug='dewdrop-defense',
        defaults={
            'display_name': 'Dewdrop Defense',
            'is_active': True,
            'active_from': timezone.now(),
            'plausibility': {
                'max_level': 99,
                'max_score_per_kill': 1300,
                'min_ms_per_kill': 120,
            },
        },
    )


def unseed(apps, schema_editor):
    # Warning: deleting a game row cascades to its scores (`Score.game` is
    # `on_delete=CASCADE`), so reversing this migration also deletes every score
    # banked on these two games.
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')
    ArcadeGame.objects.filter(slug__in=['pixel-python', 'dewdrop-defense']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('arcade', '0009_dolphin_dream_and_sunset_shatter'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
