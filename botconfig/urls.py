from django.urls import path

from . import views

urlpatterns = [
    path('', views.BotConfigView.as_view(), name='bot-config'),
]
