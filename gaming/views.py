from rest_framework import generics
from .models import Squad, Game, GamingProject
from .serializers import SquadSerializer, GameSerializer, GamingProjectSerializer

class SquadListView(generics.ListAPIView):
    serializer_class = SquadSerializer

    def get_queryset(self):
        return Squad.objects.filter(is_active=True)


class GameListView(generics.ListAPIView):
    serializer_class = GameSerializer

    def get_queryset(self):
        return Game.objects.filter(is_active=True)


class GamingProjectListView(generics.ListAPIView):
    serializer_class = GamingProjectSerializer

    def get_queryset(self):
        return GamingProject.objects.filter(is_active=True)