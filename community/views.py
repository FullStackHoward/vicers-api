from django.utils import timezone
from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import Event, Announcement
from .serializers import EventSerializer, AnnouncementSerializer
from decouple import config

class EventListView(generics.ListAPIView):
    serializer_class = EventSerializer

    def get_queryset(self):
        today = timezone.now().date()
        community = self.request.query_params.get('community', None)
        qs = Event.objects.filter(start_time__date__gte=today).order_by('start_time')[:5]
        if community:
            qs = Event.objects.filter(
                start_time__date__gte=today,
                community=community,
            ).order_by('start_time')[:5]
        return qs

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        if not queryset:
            return Response(
                {
                    'message': 'No events are scheduled at this time',
                    'results': [],
                },
                status=status.HTTP_200_OK,
            )
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

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