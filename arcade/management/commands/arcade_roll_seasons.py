"""
Does by hand what every request already does lazily.

Finalization is lazy, driven by the bot's thirty-second poll, and that is the
design. This command exists so cron can be switched on with one CRONJOBS line if
belt and braces are ever wanted, and so the same work can be run from a shell
while debugging. It is idempotent, so running it alongside live traffic is safe.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from arcade import dailyboards, rollover, seasons


class Command(BaseCommand):
    help = 'Open and close arcade seasons and daily boards that the clock has made due.'

    def handle(self, *args, **options):
        now = timezone.now()

        before = rollover.ArcadeSeason.objects.count()
        season = rollover.ensure_current_season(now)
        after = rollover.ArcadeSeason.objects.count()

        boards = dailyboards.finalize_due_boards(now)

        self.stdout.write(f'Current round resets {seasons.describe(season.ends_at)}')
        self.stdout.write(f'Rounds opened: {after - before}')
        self.stdout.write(f'Daily boards closed: {len(boards)}')

        closing = list(rollover.closing_seasons())
        if closing:
            self.stdout.write(
                f'Rounds waiting on the bot to confirm winners: '
                f'{", ".join(str(s.id) for s in closing)}'
            )
