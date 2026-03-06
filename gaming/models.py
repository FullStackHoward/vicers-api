from django.db import models

class Squad(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    game = models.CharField(max_length=100)
    logo = models.ImageField(upload_to='squads/', blank=True, null=True)
    cover_image = models.ImageField(upload_to='squads/', blank=True, null=True)
    platform = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['-is_active', 'name']


class Game(models.Model):
    name = models.CharField(max_length=100)
    cover_image = models.ImageField(upload_to='games/', blank=True, null=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name

    class Meta:
        ordering = ['name']


class GamingProject(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='gaming_projects/', blank=True, null=True)
    link = models.URLField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['title']