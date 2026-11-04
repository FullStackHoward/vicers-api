"""
Does by hand what the bot's event poll already does: post queued runs and bring
the pinned callout up to date. For a shell while debugging, or a cron line if
the posts should ever stop depending on the bot polling.
"""

from django.core.management.base import BaseCommand

from arcade import announce, conf
from arcade.models import ArcadeCallout, RunPost


class Command(BaseCommand):
    help = 'Post queued arcade runs and refresh the pinned callout in the arcade channel.'

    def handle(self, *args, **options):
        if not conf.arcade_channel_id():
            self.stdout.write('VG_ARCADE_CHANNEL_ID is not set, so the arcade posts nothing.')
            return

        announce.tick()

        callout = ArcadeCallout.load()
        self.stdout.write(f'Callout message: {callout.message_id or "none yet"}')
        self.stdout.write(f'Runs waiting to post: {RunPost.objects.filter(handled_at__isnull=True).count()}')
        if callout.last_error:
            self.stdout.write(f'Last problem ({callout.last_error_at:%Y-%m-%d %H:%M}): {callout.last_error}')
