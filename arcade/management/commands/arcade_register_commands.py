"""
Registers the owner's /arcade command in Vice Gamers, as the Vice Arcade app.

Run once after deploying, and again whenever arcade/interactions.py changes the
command. It makes the app's commands in Vice Gamers exactly /arcade, so anything
else registered there by hand is replaced.

The old Activity added a global /arcade Entry Point command. It is only reported
here unless --remove-entry-point is passed, because deleting it is not undone by
running this again.
"""

from django.core.management.base import BaseCommand, CommandError

from arcade import conf, discord_api, interactions

ENTRY_POINT = 4


class Command(BaseCommand):
    help = 'Register the /arcade owner command in Vice Gamers.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--remove-entry-point',
            action='store_true',
            help='Also delete the global /arcade Entry Point command left by the old Activity.',
        )

    def handle(self, *args, **options):
        app = conf.client_id()
        guild = conf.guild_id()

        try:
            registered = discord_api.replace_guild_commands(app, guild, [interactions.COMMAND])
        except (discord_api.DiscordError, discord_api.DiscordUnavailable) as error:
            raise CommandError(f'Discord refused the command: {error}')

        names = ', '.join(f'/{command["name"]}' for command in registered)
        self.stdout.write(self.style.SUCCESS(f'Registered in Vice Gamers: {names}'))

        try:
            global_commands = discord_api.list_commands(app)
        except (discord_api.DiscordError, discord_api.DiscordUnavailable) as error:
            self.stdout.write(f'Could not check the global commands: {error}')
            global_commands = []

        for command in global_commands:
            if command.get('type') != ENTRY_POINT:
                continue
            if options['remove_entry_point']:
                try:
                    discord_api.delete_command(app, command['id'])
                    self.stdout.write(self.style.SUCCESS(f'Deleted the global /{command["name"]} Entry Point command.'))
                except (discord_api.DiscordError, discord_api.DiscordUnavailable) as error:
                    self.stdout.write(self.style.WARNING(f'Could not delete /{command["name"]}: {error}'))
            else:
                self.stdout.write(self.style.WARNING(
                    f'The old Activity\'s global /{command["name"]} Entry Point command is still '
                    f'registered. Run again with --remove-entry-point to delete it.'
                ))

        self.stdout.write(
            'In the Developer Portal, under General Information, the Interactions Endpoint '
            'URL has to be https://api.vicers.net/api/arcade/discord/interactions, and '
            'VICE_ARCADE_PUBLIC_KEY in the API .env has to be the Public Key on that page.'
        )
