"""
Adds the per-game all-time record role, and seeds the Neon Breaker cabinet.

Neon Breaker's `active_from` is the moment this migration runs, so it is on the
boards at once but only counts toward the Arcade Champion standings from the next
season. A cabinet that appears on day six must not reshuffle a podium that is
nearly decided (`ArcadeGame.counts_for_season`).

Its plausibility limits:

- `max_score_per_brick` at 1400. The best a brick can pay is its top row value
  (70), doubled for a tough brick, times the level multiplier, which stops at 8:
  1120. A wall bonus (500 x 8) spread over the smallest wall a level can have
  (20 bricks) adds 200. 1400 sits above that with room, so no honest run can hit
  it, and a submitted score the game's own rules cannot produce is turned away.
- `max_level` is a typo guard, as for Vetrix.

`min_ms_per_brick` is left unset on purpose: a ball rattling between the ceiling
and the top of the wall breaks bricks very quickly, and a real number for how
quickly needs real runs.
"""

from django.db import migrations, models
from django.utils import timezone


def seed(apps, schema_editor):
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')

    ArcadeGame.objects.get_or_create(
        slug='neon-breaker',
        defaults={
            'display_name': 'Neon Breaker',
            'is_active': True,
            'active_from': timezone.now(),
            'plausibility': {
                'max_score_per_brick': 1400,
                'max_level': 99,
            },
        },
    )


def unseed(apps, schema_editor):
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')
    ArcadeGame.objects.filter(slug='neon-breaker').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('arcade', '0007_board_wipes_purge_and_alerts'),
    ]

    operations = [
        migrations.AddField(
            model_name='arcadegame',
            name='record_role_id',
            field=models.CharField(blank=True, help_text="The Discord role id for whoever holds this game's all-time high score, such as Vetrix Master. The Vice Arcade bot gives it to the record holder, moves it when the record is beaten, and announces both in the arcade channel. Create the role in Discord first, below the bot's own role, then paste its id here (Developer Mode, right click the role, Copy Role ID). Blank: no role for this game.", max_length=32, verbose_name='All-time record role'),
        ),
        migrations.RunPython(seed, unseed),
    ]
