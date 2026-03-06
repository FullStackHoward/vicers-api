from django.db import models

class Community(models.TextChoices):
    VICE_GAMERS = 'vicegamers', 'Vice Gamers'
    VICE_CREATORS = 'vicecreators', 'Vice Creators'

class Event(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField(blank=True, null=True)
    community = models.CharField(
        max_length=20,
        choices=Community.choices,
    )
    discord_event_id = models.CharField(max_length=100, blank=True, null=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.community})"

    class Meta:
        ordering = ['start_time']

class Announcement(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    link = models.URLField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-created_at']