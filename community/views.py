from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import Event, Announcement
from .serializers import EventSerializer, AnnouncementSerializer
from decouple import config

class EventListView(generics.ListAPIView):
    serializer_class = EventSerializer

    def get_queryset(self):
        community = self.request.query_params.get('community', None)
        if community:
            return Event.objects.filter(community=community)
        return Event.objects.all()

class AnnouncementView(generics.ListAPIView):
    serializer_class = AnnouncementSerializer

    def get_queryset(self):
        return Announcement.objects.all()[:1]


class CreateAnnouncementView(APIView):
    def post(self, request):
        bot_secret = request.headers.get('X-Bot-Secret')
        if bot_secret != config('BOT_API_SECRET'):
            return Response(
                {'error': 'Unauthorized'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        serializer = AnnouncementSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)