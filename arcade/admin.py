"""
Admin for the arcade.

Django admin is where every other app in this project keeps its configuration, so
the reset interval lives here too rather than in a settings file that would need
a deploy to change.

`ArcadeSettings` is a singleton: one row, no add, no delete. The model enforces
that as well, so nothing here is load-bearing for the invariant. What the admin
adds is the part only it can do: recording who made the change, and telling them
in plain Eastern time when the next reset now falls.
"""

from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html

from . import play, rollover, seasons
from .models import (
    ArcadeAlertOptIn,
    ArcadeAppearance,
    ArcadeCallout,
    ArcadeEvent,
    ArcadeGame,
    ArcadeSeason,
    ArcadeSettings,
    BoardWipe,
    ChannelPurge,
    DailyBoard,
    MemberDeparture,
    PersonalBest,
    PlayerAlert,
    PlaySession,
    PodiumResult,
    RunPost,
    Score,
)


@admin.register(ArcadeSettings)
class ArcadeSettingsAdmin(admin.ModelAdmin):
    readonly_fields = ['current_round', 'updated_at', 'updated_by']
    fields = ['reset_interval_days', 'current_round', 'updated_at', 'updated_by']

    def has_add_permission(self, request):
        # One row only, and it is created on demand by ArcadeSettings.load().
        return not ArcadeSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        """Go straight to the one row rather than showing a list of length one."""
        from django.shortcuts import redirect

        row = ArcadeSettings.load()
        return redirect(reverse('admin:arcade_arcadesettings_change', args=[row.pk]))

    @admin.display(description='Current round')
    def current_round(self, obj):
        season = ArcadeSeason.objects.order_by('-starts_at').first()
        if season is None:
            return 'No round has started yet. The first one opens when the arcade is first used.'

        return format_html(
            'Started {}<br>Resets {}<br>Status: {}',
            seasons.describe(season.starts_at),
            seasons.describe(season.ends_at),
            season.get_status_display(),
        )

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

        # The interval only means anything once it is applied to the round in
        # progress, and the person who just changed it should be told where the
        # reset landed rather than having to work it out.
        revised = rollover.apply_interval_change()
        messages.info(
            request,
            f'Leaderboards now reset every {obj.reset_interval_days} day(s). '
            f'The round in progress now resets {seasons.describe(revised)}.',
        )


def image_preview(image, height=120):
    """A small preview beside an upload field, so a wrong image is caught here."""
    if not image:
        return 'Nothing uploaded yet.'
    return format_html(
        '<img src="{}" style="max-height:{}px;max-width:100%;image-rendering:pixelated;'
        'border:1px solid #ccc;background:#111">',
        image.url,
        height,
    )


@admin.register(ArcadeAppearance)
class ArcadeAppearanceAdmin(admin.ModelAdmin):
    """
    The arcade page's images and wording. A singleton, handled like
    ArcadeSettings: no add once it exists, no delete, and the list goes straight
    to the one row. Changes show on the page at the next load, with no deploy.
    """

    fields = [
        'background_image', 'background_preview',
        'logo_image', 'logo_preview',
        'tagline',
        'updated_at',
    ]
    readonly_fields = ['background_preview', 'logo_preview', 'updated_at']

    def has_add_permission(self, request):
        return not ArcadeAppearance.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect

        row = ArcadeAppearance.load()
        return redirect(reverse('admin:arcade_arcadeappearance_change', args=[row.pk]))

    @admin.display(description='Background preview')
    def background_preview(self, obj):
        return image_preview(obj.background_image, height=180)

    @admin.display(description='Logo preview')
    def logo_preview(self, obj):
        return image_preview(obj.logo_image)


@admin.register(ArcadeCallout)
class ArcadeCalloutAdmin(admin.ModelAdmin):
    """
    The pinned post in the arcade channel, read-only: the API keeps it. Here to
    show `last_error`, which is where a missing permission turns up. Delete the
    message in Discord, not here, and the next poll posts a new one.
    """

    fields = ['message_id', 'last_repost_day', 'checked_at', 'last_error', 'last_error_at']
    readonly_fields = fields

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        from django.shortcuts import redirect

        row = ArcadeCallout.load()
        return redirect(reverse('admin:arcade_arcadecallout_change', args=[row.pk]))


@admin.register(RunPost)
class RunPostAdmin(admin.ModelAdmin):
    """Each banked run's channel post, for checking what went out and why not."""

    list_display = ['score', 'created_at', 'handled_at', 'skipped', 'session_runs', 'message_id']
    list_filter = ['skipped']
    readonly_fields = [field.name for field in RunPost._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(ArcadeGame)
class ArcadeGameAdmin(admin.ModelAdmin):
    list_display = ['display_name', 'slug', 'is_active', 'active_from',
                    'has_thumbnail', 'has_background', 'has_logo']
    list_filter = ['is_active']
    search_fields = ['slug', 'display_name']
    readonly_fields = ['thumbnail_preview', 'background_preview', 'logo_preview']
    fieldsets = [
        (None, {'fields': ['display_name', 'slug', 'is_active', 'active_from', 'plausibility']}),
        ('Look', {
            'description': 'This game\'s own images. The lobby\'s background and logo are '
                           'on the Arcade appearance screen.',
            'fields': [
                'thumbnail', 'thumbnail_preview',
                'background_image', 'background_preview',
                'logo_image', 'logo_preview',
            ],
        }),
    ]

    @admin.display(description='Thumbnail', boolean=True)
    def has_thumbnail(self, obj):
        return bool(obj.thumbnail)

    @admin.display(description='Background', boolean=True)
    def has_background(self, obj):
        return bool(obj.background_image)

    @admin.display(description='Logo', boolean=True)
    def has_logo(self, obj):
        return bool(obj.logo_image)

    @admin.display(description='Thumbnail preview')
    def thumbnail_preview(self, obj):
        return image_preview(obj.thumbnail)

    @admin.display(description='Background preview')
    def background_preview(self, obj):
        return image_preview(obj.background_image, height=180)

    @admin.display(description='Logo preview')
    def logo_preview(self, obj):
        return image_preview(obj.logo_image)


@admin.register(ArcadeSeason)
class ArcadeSeasonAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'starts_at', 'ends_at', 'status', 'revision']
    list_filter = ['status']
    readonly_fields = ['live_top']


@admin.register(Score)
class ScoreAdmin(admin.ModelAdmin):
    list_display = ['display_name', 'game', 'score', 'created_at', 'removed']
    list_filter = ['game', 'removed', 'removal_reason']
    search_fields = ['discord_user_id', 'display_name']
    # `removed` is read-only so it can only change through the action below, which
    # also rebuilds personal bests, the podium and any finished round it was in.
    # Ticking the box by hand would leave all of those stale.
    readonly_fields = ['run_stats', 'input_log', 'created_at', 'removed', 'removal_reason']
    date_hierarchy = 'created_at'
    actions = ['take_off_every_board']

    @admin.action(description='Take the selected scores off every board')
    def take_off_every_board(self, request, queryset):
        count = 0
        for score in queryset.filter(removed=False):
            play.remove_score(score)
            count += 1
        self.message_user(
            request,
            f'{count} score(s) taken off every board. Personal bests, the podium and any '
            'finished round they were in have been recomputed.',
        )


@admin.register(PersonalBest)
class PersonalBestAdmin(admin.ModelAdmin):
    list_display = ['discord_user_id', 'game', 'best_score', 'achieved_at']
    list_filter = ['game']
    search_fields = ['discord_user_id']


@admin.register(PodiumResult)
class PodiumResultAdmin(admin.ModelAdmin):
    list_display = ['season', 'place', 'display_name', 'average_placement', 'hidden']
    list_filter = ['place', 'hidden']
    search_fields = ['discord_user_id', 'display_name']


@admin.register(DailyBoard)
class DailyBoardAdmin(admin.ModelAdmin):
    list_display = ['day', 'game', 'player_count', 'finalized_at']
    list_filter = ['game']
    readonly_fields = ['entries', 'message_ids', 'finalized_at']
    date_hierarchy = 'day'

    @admin.display(description='Players')
    def player_count(self, obj):
        return len(obj.entries or [])


@admin.register(ArcadeEvent)
class ArcadeEventAdmin(admin.ModelAdmin):
    list_display = ['type', 'created_at', 'handled_at']
    list_filter = ['type', 'handled_at']
    readonly_fields = ['type', 'payload', 'created_at', 'handled_at']
    date_hierarchy = 'created_at'


@admin.register(MemberDeparture)
class MemberDepartureAdmin(admin.ModelAdmin):
    list_display = ['discord_user_id', 'left_at', 'recorded_at']
    search_fields = ['discord_user_id']
    date_hierarchy = 'left_at'


@admin.register(PlaySession)
class PlaySessionAdmin(admin.ModelAdmin):
    list_display = ['id', 'discord_user_id', 'game', 'issued_at', 'expires_at', 'used']
    list_filter = ['game', 'used']
    search_fields = ['discord_user_id']


@admin.register(ArcadeAlertOptIn)
class ArcadeAlertOptInAdmin(admin.ModelAdmin):
    list_display = ['discord_user_id', 'enabled', 'updated_at']
    list_filter = ['enabled']
    search_fields = ['discord_user_id']


class ReadOnlyAdmin(admin.ModelAdmin):
    """Written by the /arcade command and the posting tick, only read here."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(BoardWipe)
class BoardWipeAdmin(ReadOnlyAdmin):
    list_display = ['board', 'wiped_at', 'wiped_by']
    list_filter = ['board']


@admin.register(ChannelPurge)
class ChannelPurgeAdmin(ReadOnlyAdmin):
    list_display = ['requested_at', 'requested_by', 'deleted', 'finished_at', 'last_error']
    # The command's reply token is a short-lived credential, so it is never shown.
    exclude = ['interaction_token']


@admin.register(PlayerAlert)
class PlayerAlertAdmin(ReadOnlyAdmin):
    list_display = ['discord_user_id', 'game', 'created_at', 'handled_at', 'skipped']
    list_filter = ['game', 'skipped']
    search_fields = ['discord_user_id']
