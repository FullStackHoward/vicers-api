from rest_framework import serializers

from .models import MinecraftRealm


class MinecraftRealmSerializer(serializers.ModelSerializer):
    class Meta:
        model = MinecraftRealm
        fields = ['channel_id', 'invite_url', 'link_unavailable', 'updated_at']
