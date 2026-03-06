from django.contrib import admin
from .models import Squad, Game, GamingProject

@admin.register(Squad)
class SquadAdmin(admin.ModelAdmin):
    list_display = ['name', 'game', 'platform', 'is_active']
    list_filter = ['is_active', 'platform']
    search_fields = ['name', 'game']

@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name']

@admin.register(GamingProject)
class GamingProjectAdmin(admin.ModelAdmin):
    list_display = ['title', 'is_active']
    list_filter = ['is_active']
    search_fields = ['title', 'description']
