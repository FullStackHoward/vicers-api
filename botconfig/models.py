"""
Config the Vice Verify Bot reads at runtime instead of from its `.env`.

What belongs here: values an admin changes while the bot runs (channel ids,
links, copy, schedules). What never belongs here: secrets and credentials, the
guild ids and API address the bot needs in order to ask for the rest, and
anything the bot must know at boot to register commands.

One model per feature, keyed by community, and one section per feature in the
`/api/bot-config/` response. The next value moved adds a model and a section,
not a new pipeline.
"""

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from community.models import Community

# Discord ids are stored as strings so nothing between here and the bot (JSON,
# JavaScript) can round them.
snowflake = RegexValidator(
    regex=r'^\d{17,20}$',
    message='A Discord id is 17 to 20 digits and nothing else.',
)


def https_only(value):
    if urlsplit(value).scheme.lower() != 'https':
        raise ValidationError('The link has to start with https://.')


class MinecraftRealm(models.Model):
    """The pinned "join our Realm" post in a community's Minecraft channel."""

    community = models.CharField(max_length=20, choices=Community.choices, unique=True)
    channel_id = models.CharField(
        'channel id',
        max_length=20,
        blank=True,
        validators=[snowflake],
        help_text=(
            'In Discord, turn on Developer Mode, right-click the channel, Copy Channel ID. '
            'Blank turns the post off and leaves any existing post alone.'
        ),
    )
    invite_url = models.URLField(
        'invite link',
        max_length=300,
        blank=True,
        validators=[https_only],
        help_text='Paste the exact link Realms gave you. Blank leaves the existing post as it is.',
    )
    link_unavailable = models.BooleanField(
        'link not working',
        default=False,
        help_text=(
            'Tick this when the invite link stops working. The post swaps the link for a note '
            'that admins will update it soon. Untick after pasting the new link.'
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Minecraft Realm post'
        verbose_name_plural = 'Minecraft Realm posts'

    def __str__(self):
        return f'Minecraft Realm post ({self.get_community_display()})'
