from rest_framework import serializers
from .models import Squad, Game, GamingProject

class SquadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Squad
        fields = [
            'id',
            'name',
            'description',
            'game',
            'logo',
            'cover_image',
            'platform',
            'is_active',
            'created_at',
        ]

class GameSerializer(serializers.ModelSerializer):
    class Meta:
        model = Game
        fields = [
            'id',
            'name',
            'cover_image',
            'is_active',
        ]

class GamingProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = GamingProject
        fields = [
            'id',
            'title',
            'description',
            'image',
            'link',
            'is_active',
            'created_at',
        ]