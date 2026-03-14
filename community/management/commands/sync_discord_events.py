import aiohttp
import asyncio
from django.core.management.base import BaseCommand
from asgiref.sync import sync_to_async
from datetime import datetime
from community.models import Event, Community
from decouple import config

DISCORD_API_BASE = 'https://discord.com/api/v10'
BOT_TOKEN = config('DISCORD_BOT_TOKEN')

GUILD_COMMUNITY_MAP = {
    config('VG_GUILD_ID'): Community.VICE_GAMERS,
    config('VC_GUILD_ID'): Community.VICE_CREATORS,
}

async def fetch_guild_events(session, guild_id):
    url = f'{DISCORD_API_BASE}/guilds/{guild_id}/scheduled-events'
    headers = {'Authorization': f'Bot {BOT_TOKEN}'}
    async with session.get(url, headers=headers) as response:
        if response.status == 200:
            return await response.json()
        else:
            print(f'Failed to fetch events for guild {guild_id}: {response.status}')
            return []

@sync_to_async
def save_event(discord_event_id, title, description, start_time, end_time, community):
    event, created = Event.objects.update_or_create(
        discord_event_id=discord_event_id,
        defaults={
            'title': title,
            'description': description,
            'start_time': start_time,
            'end_time': end_time,
            'community': community,
        }
    )
    return event, created

@sync_to_async
def delete_removed_events(community, active_discord_ids):
    deleted, _ = Event.objects.filter(
        community=community
    ).exclude(
        discord_event_id__in=active_discord_ids
    ).delete()
    if deleted:
        print(f'Deleted {deleted} removed event(s) for {community}')

async def sync_events():
    async with aiohttp.ClientSession() as session:
        for guild_id, community in GUILD_COMMUNITY_MAP.items():
            print(f'Syncing events for {community}...')
            events = await fetch_guild_events(session, guild_id)

            active_discord_ids = []

            for discord_event in events:
                discord_event_id = str(discord_event['id'])
                active_discord_ids.append(discord_event_id)
                title = discord_event['name']
                description = discord_event.get('description', '')
                start_time = datetime.fromisoformat(
                    discord_event['scheduled_start_time'].replace('Z', '+00:00')
                )
                end_time = None
                if discord_event.get('scheduled_end_time'):
                    end_time = datetime.fromisoformat(
                        discord_event['scheduled_end_time'].replace('Z', '+00:00')
                    )

                event, created = await save_event(
                    discord_event_id,
                    title,
                    description,
                    start_time,
                    end_time,
                    community,
                )

                if created:
                    print(f'Created event: {title} ({community})')
                else:
                    print(f'Updated event: {title} ({community})')

            await delete_removed_events(community, active_discord_ids)

class Command(BaseCommand):
    help = 'Syncs scheduled events from Discord servers into the database'

    def handle(self, *args, **options):
        asyncio.run(sync_events())
        self.stdout.write(self.style.SUCCESS('Discord event sync complete'))