"""
Seeds the Pastel Pursuit cabinet, a road and canal crossing game that counts `hops`.

`active_from` is the moment this migration runs, so the game is on the boards at
once but only counts toward the Arcade Champion standings from the next season. A
cabinet that appears mid-season must not reshuffle a podium that is nearly decided
(`ArcadeGame.counts_for_season`).

Every limit below is derived from the game's starting tuning, which nobody has
played, so it has to be re-derived if that tuning changes.

The page counts `hops` as hops that moved the hero one cell. A refused hop (a bump
on a wall, a filled door or the board edge) is not counted. That is why the pace
bound below holds.

The unit is hops and not doors because a run that scores progress points and dies
before reaching a door would have doors at 0 and be refused by the score rule. Hops
are above zero whenever the score is.

Pastel Pursuit's plausibility limits (a `unit_limits` entry on `hops`):

- `max_score_each` at 70. Points come from three places: 10 for each new highest
  row in a crossing (rows 10 to 1, so 100 a crossing), a door worth 200 plus a time
  bonus of at most 150, and a stage-clear bonus of `100 * min(stage, 10)`, so up to
  1,000. A crossing needs at least 11 hops (start row 11 to door row 0), and a stage
  is five crossings, so at least 55 hops. The best a crossing can pay is 450, and
  the best a stage can pay is `5 * 450 + 1,000 = 3,250`, so the best ratio is
  `3,250 / 55 = 59.1` points a hop. 70 is at least that and under 20 over it. A run
  that ends mid-crossing, or one that hops sideways or back, pays less per hop, and
  a run over several stages is an average of per-stage ratios that are each at or
  under this one, so none beats it. The page's tests recompute the derivation from
  `config.js` and run a bot over 30 seeds against it.
- `min_ms_each` at 90. The hop wait is 6 ticks at 60 ticks a second, which is 100
  ms, and it is not cleared by a door or a stage load, so any two hops in a run are
  at least 100 ms apart. 90 leaves slack.
- `max_level` at 99 is a typo guard, as for the other games. `final_level` is the
  stage number.
"""

from django.db import migrations
from django.utils import timezone


def seed(apps, schema_editor):
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')

    ArcadeGame.objects.get_or_create(
        slug='pastel-pursuit',
        defaults={
            'display_name': 'Pastel Pursuit',
            'is_active': True,
            'active_from': timezone.now(),
            'plausibility': {
                'max_level': 99,
                'unit_limits': [
                    {
                        'stat': 'hops',
                        'label': 'hops',
                        'max_score_each': 70,
                        'min_ms_each': 90,
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
    ArcadeGame.objects.filter(slug='pastel-pursuit').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('arcade', '0012_miami_mainframe'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
