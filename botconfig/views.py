from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from community.models import Community

from .models import MinecraftRealm
from .permissions import IsVerifyBot
from .serializers import MinecraftRealmSerializer


class BotConfigView(APIView):
    """
    Every runtime config section the Vice Verify Bot reads, for one community.

    Contract: changes are additive. The bot ignores keys it does not know, and
    reads a missing or null key as "no config for that feature".
    """

    # Set explicitly: production has no default permission class, so a view that
    # leaves these unset is open to anyone.
    authentication_classes = []
    permission_classes = [IsVerifyBot]

    def get(self, request):
        community = request.query_params.get('community', '')
        if community not in Community.values:
            return Response(
                {'error': f'community must be one of: {", ".join(Community.values)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        realm = MinecraftRealm.objects.filter(community=community).first()

        response = Response({
            'community': community,
            'minecraft': MinecraftRealmSerializer(realm).data if realm else None,
        })
        response['Cache-Control'] = 'no-store'
        return response
