"""
Seeds the Miami Mainframe cabinet, a maze game that counts `dots_eaten`.

`active_from` is the moment this migration runs, so the game is on the boards at
once but only counts toward the Arcade Champion standings from the next season. A
cabinet that appears mid-season must not reshuffle a podium that is nearly decided
(`ArcadeGame.counts_for_season`).

Every limit below is derived from the game's starting tuning, which nobody has
played, so it has to be re-derived if that tuning changes.

The page counts `dots_eaten` as bits and power cells together.

Miami Mainframe's plausibility limits (a `unit_limits` entry on `dots_eaten`):

- `max_score_each` at 300. The best ratio of score to dots eaten comes early in a
  run: eat `K` dots to reach a power cell, eat it (50), then catch all four
  glitches under it (200 + 400 + 800 + 1600 = 3,000). That is `10 * K + 50 + 3,000`
  points for `K + 1` dots. The page's maze rule puts at least 10 dot cells on every
  route from the player's start to a power cell and between power cells, so `K` is
  at least 10 and the ceiling is `3,150 / 11 = 286.4`. 300 is at least that and
  under 20 over it. Later power cells, bonus items and clear bonuses each need more
  dots eaten first, so none beats the early ratio. The page's bot test checks the
  claim, and if it ever shows a run over 300 a dot the limit is what should move.
- `min_ms_each` at 100. Dots are one cell apart at best and the player's fastest
  move is 7 ticks at 60 ticks a second, which is 116.7 ms a cell. 100 leaves slack.
- `max_level` at 99 is a typo guard, as for the other games. `final_level` is the
  board number.
"""

from django.db import migrations
from django.utils import timezone


def seed(apps, schema_editor):
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')

    ArcadeGame.objects.get_or_create(
        slug='miami-mainframe',
        defaults={
            'display_name': 'Miami Mainframe',
            'is_active': True,
            'active_from': timezone.now(),
            'plausibility': {
                'max_level': 99,
                'unit_limits': [
                    {
                        'stat': 'dots_eaten',
                        'label': 'dots',
                        'max_score_each': 300,
                        'min_ms_each': 100,
                    },
                ],
            },
        },
    )


def unseed(apps, schema_editor):
    # Warning: deleting a game row cascades to its scores (`Score.game` is
    # `on_delete=CASCADE`), so reversing this migration also deletes every score
    # banked on this game.
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')
    ArcadeGame.objects.filter(slug='miami-mainframe').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('arcade', '0011_pixel_python_faster_pace'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
