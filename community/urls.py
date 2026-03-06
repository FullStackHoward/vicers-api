from django.urls import path
from .views import EventListView, AnnouncementView, CreateAnnouncementView

urlpatterns = [
    path('events/', EventListView.as_view(), name='event-list'),
    path('announcement/', AnnouncementView.as_view(), name='announcement'),
path('announcement/create/', CreateAnnouncementView.as_view(), name='create-announcement'),
]