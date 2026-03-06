from django.urls import path
from .views import SquadListView, GameListView, GamingProjectListView

urlpatterns = [
    path('squads/', SquadListView.as_view(), name='squad-list'),
    path('games/', GameListView.as_view(), name='game-list'),
    path('projects/', GamingProjectListView.as_view(), name='gaming-project-list'),

]