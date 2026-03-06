import aiohttp
import asyncio
from django.db.models.signals import post_save
from django.dispatch import receiver
from asgiref.sync import sync_to_async
from decouple import config
from .models import Event, Community
from django.db import transaction

DISCORD_API_BASE = 'https://discord.com/api/v10'
BOT_TOKEN = config('DISCORD_BOT_TOKEN')

COMMUNITY_GUILD_MAP = {
    Community.VICE_GAMERS: config('VG_GUILD_ID'),
    Community.VICE_CREATORS: config('VC_GUILD_ID'),
}

async def create_discord_event(guild_id, title, description, start_time, end_time):
    url = f'{DISCORD_API_BASE}/guilds/{guild_id}/scheduled-events'
    headers = {
        'Authorization': f'Bot {BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    payload = {
        'name': title,
        'description': description,
        'scheduled_start_time': start_time.isoformat(),
        'privacy_level': 2,
        'entity_type': 3,
        'entity_metadata': {'location': 'Discord'},
    }
    if end_time:
        payload['scheduled_end_time'] = end_time.isoformat()

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as response:
            if response.status == 200:
                return await response.json()
            else:
                text = await response.text()
                print(f'Failed to create Discord event: {response.status} - {text}')
                return None

@sync_to_async
def update_event_discord_id(event_id, discord_event_id):
    Event.objects.filter(id=event_id).update(discord_event_id=discord_event_id)

async def handle_event_post_save(event):
    guild_id = COMMUNITY_GUILD_MAP.get(event.community)
    if not guild_id:
        print(f'No guild found for community {event.community}')
        return

    discord_event = await create_discord_event(
        guild_id=guild_id,
        title=event.title,
        description=event.description,
        start_time=event.start_time,
        end_time=event.end_time,
    )

    if discord_event:
        discord_event_id = str(discord_event['id'])
        await update_event_discord_id(event.id, discord_event_id)
        print(f'Created Discord event: {event.title} in guild {guild_id}')

from django.db import transaction

@receiver(post_save, sender=Event)
def event_post_save(sender, instance, created, **kwargs):
    if created and not instance.discord_event_id:
        transaction.on_commit(lambda: asyncio.run(handle_event_post_save(instance)))