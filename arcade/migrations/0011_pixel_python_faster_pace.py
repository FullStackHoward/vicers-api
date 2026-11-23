"""
Loosens Pixel Python's pace limit to match its faster top speed.

The game was retuned after migration 0010 seeded it: the fastest snake now moves
one cell per 3 ticks, not 4. Two foods are still never closer than three cells
apart, so three moves is 9 ticks, which is 150 ms. `min_ms_each` on
`foods_eaten` drops from 180 to 135 to leave slack.

Only an entry still at the seeded 180 is changed, so a limit already edited in
Django admin is left alone. Going back restores 180 the same way.
"""

from django.db import migrations

SEEDED = 180
RETUNED = 135


def _set_pace(apps, old, new):
    ArcadeGame = apps.get_model('arcade', 'ArcadeGame')
    game = ArcadeGame.objects.filter(slug='pixel-python').first()

    if game is None or not isinstance(game.plausibility, dict):
        return

    entries = game.plausibility.get('unit_limits')

    if not isinstance(entries, list):
        return

    changed = False

    for entry in entries:
        if isinstance(entry, dict) and entry.get('stat') == 'foods_eaten' and entry.get('min_ms_each') == old:
            entry['min_ms_each'] = new
            changed = True

    if changed:
        game.save(update_fields=['plausibility'])


def loosen(apps, schema_editor):
    _set_pace(apps, SEEDED, RETUNED)


def tighten(apps, schema_editor):
    _set_pace(apps, RETUNED, SEEDED)


class Migration(migrations.Migration):

    dependencies = [
        ('arcade', '0010_pixel_python_and_dewdrop_defense'),
    ]

    operations = [
        migrations.RunPython(loosen, tighten),
    ]
