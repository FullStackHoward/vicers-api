"""
Seeds the Vetrix cabinet, because nothing in the arcade works without a game row.

The plausibility limits are deliberately sparse. A limit that is too tight
rejects a legitimate run, which is worse than no limit at all, so only the ones
that can be justified without a tuned scoring table are set here:

- `min_ms_per_piece` at 150 means a sustained rate of more than about six and a
  half placements per second, every second, for the whole run. Hard drops are
  fast, but not that fast for that long.
- `max_chain` and `max_level` are typo guards rather than real ceilings.

`max_score_per_piece` is left unset on purpose. It is the most useful check of
the three, and it needs real numbers from a playtest rather than a guess. Set it
in Django admin once a few genuine runs are on the board.
"""

from datetime import datetime, timezone as dt_timezone

from django.db import migrations

# Early enough that Vetrix counts toward every round, including the first.
COUNTS_FROM = datetime(2020, 1, 1, tzinfo=dt_timezone.utc)


def seed(apps, schema_editor):
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')

    ArcadeGame.objects.update_or_create(
        slug='vetrix',
        defaults={
            'display_name': 'Vetrix',
            'is_active': True,
            'active_from': COUNTS_FROM,
            'plausibility': {
                'min_ms_per_piece': 150,
                'max_chain': 50,
                'max_level': 99,
            },
        },
    )


def unseed(apps, schema_editor):
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')
    ArcadeGame.objects.filter(slug='vetrix').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('arcade', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
