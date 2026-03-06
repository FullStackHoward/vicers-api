from rest_framework import serializers
from .models import Event, Announcement

class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = [
            'id',
            'title',
            'description',
            'start_time',
            'end_time',
            'community',
            'discord_event_id',
            'created_at',
        ]

class AnnouncementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Announcement
        fields = [
            'id',
            'title',
            'content',
            'link',
            'created_at',
        ]