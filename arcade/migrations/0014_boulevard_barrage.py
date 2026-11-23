"""
Seeds the Boulevard Barrage cabinet, a missile defense game that counts `kills`
and `shots`.

`active_from` is the moment this migration runs, so the game is on the boards at
once but only counts toward the Arcade Champion standings from the next season. A
cabinet that appears mid-season must not reshuffle a podium that is nearly decided
(`ArcadeGame.counts_for_season`).

Every limit below is derived from the game's starting tuning, which nobody has
played, so it has to be re-derived if that tuning changes.

A kill is a rocket or a plane shot down. A shot is one counter-missile fired. The
game has two `unit_limits` entries: a score ceiling on `kills` and a pace floor on
`shots`.

The unit for the score is kills because a score above zero has to come with a count
above zero, or the score rule refuses the run. That is why the page pays the
end-of-wave bonus only for a wave in which at least one thing was shot down: a wave
with no kill pays nothing, so the score is above zero only when kills are.

Boulevard Barrage's plausibility limits:

- `max_score_each` at 360, on `kills`. Rockets pay 25 * m and planes pay 50 * m,
  where m = min(6, 1 + floor((n - 1) / 2)) for wave n. A wave in which at least one
  thing was shot down also pays, at its end, the standing buildings * 100 * m plus
  the unspent shots * 5 * m. That bonus has no kills of its own, so the bound uses
  the page's impact rule: every rocket that lands destroys exactly one standing slot
  of nine (six buildings and three towers), and slots never come back. After a wave
  with R parent rockets, of which k were shot down, at most 9 - (R - k) slots stand.
  With a buildings and at least one tower standing, a + 1 <= 9 - R + k, so the
  fewest rocket kills is kmin = max(1, R + a - 8). Children of splitters and rockets
  dropped by planes only add rockets, so R is the parent count, min(24, 8 + 2 * (n -
  1)). At most 29 shots can be unspent when a wave had a kill. The best score per
  kill is the maximum, over waves 1 to 99, a from 1 to 6, and plane kills p from 0
  to the wave's planes (none before wave 5, one up to wave 7, two after that), of
  (kmin * 25m + p * 50m + a * 100m + 29 * 5m) / (kmin + p). That comes to 353.2, at
  wave 11 and on with six buildings and 22 rocket kills. 360 is at least that and
  under 20 over it. The page's test recomputes it from `config.js`, and if it ever
  differs, the limit is what should move. A game that rebuilds slots or adds lives
  breaks the slot count above, so this bullet has to be re-derived for it.
- `min_ms_each` at 150, on `shots`. The page waits 10 ticks between shots, which is
  166.7 ms at 60 ticks a second, and cards do not clear the wait. 150 leaves slack.
  There is no pace limit on `kills`, because one blast can chain through many
  rockets in a single tick.
- `max_level` at 99 is a typo guard, as for the other games. `final_level` is the
  wave number.
"""

from django.db import migrations
from django.utils import timezone


def seed(apps, schema_editor):
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')

    ArcadeGame.objects.get_or_create(
        slug='boulevard-barrage',
        defaults={
            'display_name': 'Boulevard Barrage',
            'is_active': True,
            'active_from': timezone.now(),
            'plausibility': {
                'max_level': 99,
                'unit_limits': [
                    {
                        'stat': 'kills',
                        'label': 'kills',
                        'max_score_each': 360,
                    },
                    {
                        'stat': 'shots',
                        'label': 'shots',
                        'min_ms_each': 150,
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
    ArcadeGame.objects.filter(slug='boulevard-barrage').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('arcade', '0013_pastel_pursuit'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
