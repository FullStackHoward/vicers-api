from django.contrib import admin
from .models import Project

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['title', 'is_active', 'image']
    list_filter = ['is_active']
    search_fields = ['title', 'description']