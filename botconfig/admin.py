from django.contrib import admin

from .models import MinecraftRealm

REACH_NOTE = 'Changes reach Discord within about 10 minutes. No bot restart is needed.'


@admin.register(MinecraftRealm)
class MinecraftRealmAdmin(admin.ModelAdmin):
    list_display = ['community', 'channel_id', 'invite_url', 'link_unavailable', 'updated_at']
    readonly_fields = ['updated_at']
    fieldsets = [
        (None, {
            'description': REACH_NOTE,
            'fields': ['community', 'channel_id', 'invite_url', 'link_unavailable', 'updated_at'],
        }),
    ]
