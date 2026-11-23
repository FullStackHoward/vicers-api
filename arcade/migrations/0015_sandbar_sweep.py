"""
Seeds the Sandbar Sweep cabinet, a mine-sweeping puzzle that counts `cells_dug` and
`digs`.

`active_from` is the moment this migration runs, so the game is on the boards at
once but only counts toward the Arcade Champion standings from the next season. A
cabinet that appears mid-season must not reshuffle a podium that is nearly decided
(`ArcadeGame.counts_for_season`).

Every limit below is derived from the game's starting tuning, which nobody has
played, so it has to be re-derived if that tuning changes.

The page counts `cells_dug` as safe cells opened by any means (a dig, a chord, a
fill or a ping), and `digs` as accepted actions that opened at least one cell or
ended the run. The game has two `unit_limits` entries: a score ceiling on
`cells_dug` and a pace floor on `digs`.

The score unit is cells because it is above zero whenever the score is. Every point
comes from an opened cell, or from clearing a board, which needs every safe cell
opened.

Sandbar Sweep's plausibility limits:

- `max_score_each` at 30, on `cells_dug`. Each safe cell opened pays 10. Clearing a
  board pays 100 * min(b, 10) + 10 * (correctly flagged mines) + floor(5 * safe *
  left / tideTicks), and clearing needs every safe cell on the board opened, so the
  bonus can be shared out over that board's safe cells. The best a board can pay per
  cell is 10 + (100 * min(b, 10) + 10 * mines + 5 * safe) / safe, taking every mine
  flagged and a clear at the first tick of the tide. The boards are 8 x 8 with 8
  mines, 9 x 9 with 11, 10 x 10 with 15, 10 x 12 with 19, 11 x 13 with 24, then 11 x
  14 with min(32, 27 + (b - 6)). Enumerating boards 1 to 99 gives 25.82, at board 11
  and on (122 safe cells, 32 mines). 30 is at least that and under 20 over it. A run
  that dies mid-board gets 10 a cell at most. The page's test recomputes it from
  `config.js`, and if it ever differs, the limit is what should move.
- `min_ms_each` at 60, on `digs`. The page waits 4 ticks after any accepted dig,
  flag or ping, which is 66.7 ms at 60 ticks a second, so two accepted actions are
  never closer than that, and `digs` counts only some of them. 60 leaves slack. This
  is a weak bound, because one dig can open a large area, so there is no useful pace
  limit per cell. It exists to catch a run faster than any player or the game could
  produce.
- `max_level` at 99 is a typo guard, as for the other games. `final_level` is the
  board number.
"""

from django.db import migrations
from django.utils import timezone


def seed(apps, schema_editor):
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')

    ArcadeGame.objects.get_or_create(
        slug='sandbar-sweep',
        defaults={
            'display_name': 'Sandbar Sweep',
            'is_active': True,
            'active_from': timezone.now(),
            'plausibility': {
                'max_level': 99,
                'unit_limits': [
                    {
                        'stat': 'cells_dug',
                        'label': 'cells',
                        'max_score_each': 30,
                    },
                    {
                        'stat': 'digs',
                        'label': 'digs',
                        'min_ms_each': 60,
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
    ArcadeGame.objects.filter(slug='sandbar-sweep').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('arcade', '0014_boulevard_barrage'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
