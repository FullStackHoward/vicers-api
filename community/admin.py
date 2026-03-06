from django.contrib import admin
from .models import Event, Announcement

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ['title', 'community', 'start_time', 'end_time']
    list_filter = ['community']
    search_fields = ['title', 'description']

@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ['title', 'created_at']
    search_fields = ['title', 'content']