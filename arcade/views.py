"""
The arcade's HTTP surface.

Two audiences: the arcade web page, which presents the signed identity it was
given at sign-in, and the bot, which presents the shared secret. They never share an
endpoint.

Lazy finalization hangs off `tick`, called by the endpoints that are reached
often enough to serve as a clock: the bot's event poll and any leaderboard read.
There is no cron job. See arcade/rollover.py for why that is a structural choice
rather than a preference.
"""

import json
import logging

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import (
    alerts,
    announce,
    conf,
    dailyboards,
    departures,
    discord_api,
    interactions,
    leaderboards,
    play,
    podium,
    rollover,
    seasons,
    serializers as arcade_serializers,
)
from .models import (
    ArcadeAlertOptIn,
    ArcadeAppearance,
    ArcadeEvent,
    ArcadeGame,
    ArcadeSeason,
    DailyBoard,
    PersonalBest,
    PodiumResult,
    Score,
)
from .permissions import ArcadeIdentity, IsBot, require_player

# The refusal copy the spec specifies, keyed by the gate's reason.
GATE_COPY = {
    'wrong_guild': 'Vice Arcade is for Vice Gamers members. Apply at discord.vicers.net.',
    'member_pending': 'Finish joining Vice Gamers in Discord first, then sign in again.',
}


def tick(now=None):
    """
    Advance anything the clock has made due: seasons, then Daily boards.

    Seasons first, because a board closing is scoped to the days a game was
    active and nothing about a day depends on a season existing, while the
    standings a season finalizes do depend on its window being settled.
    """
    now = now or timezone.now()
    season = rollover.ensure_current_season(now)
    dailyboards.finalize_due_boards(now)
    return season


def gate_response(refused):
    return Response(
        {
            'error': refused.reason,
            'detail': GATE_COPY.get(refused.reason, refused.detail or 'Refused.'),
        },
        status=status.HTTP_403_FORBIDDEN,
    )


# ---------------------------------------------------------------------------
# The arcade web page
# ---------------------------------------------------------------------------

def discord_unavailable(detail):
    # Fail closed. A gate that cannot reach Discord must not let anyone in.
    return Response(
        {'error': 'discord_unavailable', 'detail': detail},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


class TokenExchangeView(APIView):
    """
    Sign-in: swap the OAuth code for a signed identity.

    Discord sends the player back to the arcade with a code; the page posts it
    here with the redirect it was sent to. This is the only place the client
    secret is used. Non-members are turned away here, at the door, rather than
    after they have picked a game.

    Discord's access token stays on the server. The page never needs it: every
    later call presents the identity token instead.
    """

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        payload = arcade_serializers.TokenExchangeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        redirect_uri = payload.validated_data['redirect_uri']

        if redirect_uri not in conf.redirect_uris():
            return Response(
                {'error': 'bad_redirect', 'detail': 'Sign in from arcade.vicers.net.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            exchanged = discord_api.exchange_code(payload.validated_data['code'], redirect_uri)
            user = discord_api.current_user(exchanged['access_token'])
            play.confirm_member(user['id'])
        except discord_api.DiscordUnavailable:
            return discord_unavailable('Discord did not answer. Try again.')
        except play.GateRefused as refused:
            return gate_response(refused)
        except discord_api.DiscordError as error:
            return Response(
                {'error': 'discord_refused', 'detail': str(error)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        display_name = user.get('global_name') or user.get('username') or 'Player'

        return Response({
            'identity': play.sign_identity(user['id'], display_name),
            'player': {
                'discord_user_id': user['id'],
                'display_name': display_name,
                'avatar_url': avatar_url(user),
            },
        })


def avatar_url(user):
    avatar = user.get('avatar')
    if not avatar:
        return None
    extension = 'gif' if avatar.startswith('a_') else 'png'
    return f'https://cdn.discordapp.com/avatars/{user["id"]}/{avatar}.{extension}?size=64'


class SessionView(APIView):
    """
    The membership gate, then a signed single-use play session.

    The gate runs here rather than at submit so a player who cannot play is told
    before they spend a run, not after. Membership is asked of Discord fresh each
    time, because the identity token can be days old.
    """

    authentication_classes = [ArcadeIdentity]
    permission_classes = []

    def post(self, request):
        player = require_player(request)
        payload = arcade_serializers.SessionRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        game = get_object_or_404(
            ArcadeGame, slug=payload.validated_data['game'], is_active=True
        )

        try:
            location = play.confirm_member(player['discord_user_id'])
        except discord_api.DiscordUnavailable:
            return discord_unavailable('Could not confirm you are in Vice Gamers. Try again.')
        except play.GateRefused as refused:
            return gate_response(refused)
        except discord_api.DiscordError as error:
            return Response(
                {'error': 'discord_refused', 'detail': str(error)},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        session, token = play.issue_session(player['discord_user_id'], game, location)

        return Response({
            'session_token': token,
            'seed': session.seed,
            'game': game.slug,
            'expires_at': session.expires_at.isoformat(),
        }, status=status.HTTP_201_CREATED)


class AppearanceView(APIView):
    """
    The page's uploaded images and wording, from the Arcade appearance and Arcade
    game screens in admin. `background_url` and `logo_url` at the top level are the
    cabinet's (the lobby); each game carries its own. Public: it is read before anyone signs in, and it holds
    nothing that is not already on the page.

    A blank field comes back as null, and the page keeps its built-in look for it.
    """

    authentication_classes = []
    permission_classes = []

    def get(self, request):
        look = ArcadeAppearance.load()

        def url(image):
            return request.build_absolute_uri(image.url) if image else None

        return Response({
            'background_url': url(look.background_image),
            'logo_url': url(look.logo_image),
            'tagline': look.tagline,
            'games': {
                game.slug: {
                    'display_name': game.display_name,
                    'thumbnail_url': url(game.thumbnail),
                    'background_url': url(game.background_image),
                    'logo_url': url(game.logo_image),
                }
                for game in ArcadeGame.objects.filter(is_active=True)
            },
            'updated_at': look.updated_at.isoformat() if look.updated_at else None,
        })


class ScoreSubmitView(APIView):
    authentication_classes = [ArcadeIdentity]
    permission_classes = []

    def post(self, request):
        player = require_player(request)
        payload = arcade_serializers.ScoreSubmitSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        try:
            saved = play.submit_score(
                data['session_token'],
                player['discord_user_id'],
                player['display_name'] or 'Player',
                data['score'],
                data.get('run_stats') or {},
                input_log=data.get('input_log'),
            )
        except play.SubmissionRefused as refused:
            return Response(
                {'error': refused.reason, 'detail': refused.detail},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            'score_id': saved.id,
            'score': saved.score,
        }, status=status.HTTP_201_CREATED)


class LeaderboardView(APIView):
    """
    All three boards for one game, plus when this round resets.

    Identity is optional: without it the boards still render, just without the
    "you" row.
    """

    authentication_classes = [ArcadeIdentity]
    permission_classes = []

    def get(self, request, game_slug):
        season = tick()
        game = get_object_or_404(ArcadeGame, slug=game_slug)
        player = getattr(request, 'arcade_player', None)
        user_id = player['discord_user_id'] if player else None
        today = seasons.eastern_date(timezone.now())

        return Response({
            'game': {'slug': game.slug, 'display_name': game.display_name},
            'season': {
                'id': season.id,
                'starts_at': season.starts_at.isoformat(),
                'ends_at': season.ends_at.isoformat(),
                'resets_in_words': seasons.describe(season.ends_at),
            },
            'daily': leaderboards.board_view(leaderboards.daily_rows(game, today), user_id),
            'round': leaderboards.board_view(leaderboards.season_rows(game, season), user_id),
            'alltime': leaderboards.board_view(leaderboards.alltime_rows(game), user_id),
        })


class MyAlertsView(APIView):
    """
    The signed-in player's first-place alert setting, for the toggle on the arcade
    page. Off until they turn it on.
    """

    authentication_classes = [ArcadeIdentity]
    permission_classes = []

    def get(self, request):
        player = require_player(request)
        return Response({'enabled': alerts.alerts_on(player['discord_user_id'])})

    def put(self, request):
        player = require_player(request)
        payload = arcade_serializers.AlertSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        enabled = alerts.set_alerts(player['discord_user_id'], payload.validated_data['enabled'])

        return Response({'enabled': enabled})


# ---------------------------------------------------------------------------
# Discord: the owner's /arcade command (arcade/interactions.py)
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


@method_decorator(csrf_exempt, name='dispatch')
class DiscordInteractionsView(View):
    """
    The Interactions Endpoint URL in the Developer Portal. A plain Django view
    rather than DRF, because the signature covers the exact bytes Discord sent.
    """

    def post(self, request):
        try:
            interactions.verify(
                request.body,
                request.headers.get('X-Signature-Ed25519'),
                request.headers.get('X-Signature-Timestamp'),
            )
        except interactions.Unverified:
            return HttpResponse('invalid request signature', status=401)

        try:
            interaction = json.loads(request.body)
        except ValueError:
            return HttpResponse('not json', status=400)

        try:
            return JsonResponse(interactions.handle(interaction))
        except Exception:
            logger.exception('Vice Arcade: the /arcade command failed')
            return JsonResponse({
                'type': interactions.CHANNEL_MESSAGE,
                'data': {
                    'content': 'Something went wrong on the arcade\'s side. Nothing was changed.',
                    'flags': interactions.EPHEMERAL,
                },
            })


# ---------------------------------------------------------------------------
# The bot
# ---------------------------------------------------------------------------

class BotView(APIView):
    authentication_classes = []
    permission_classes = [IsBot]


class PendingEventsView(BotView):
    """
    The outbox drain, and the arcade's heartbeat.

    Because the bot polls this every thirty seconds, it is also what makes lazy
    season and Daily board finalization reliable without a cron job, and it is
    when the arcade's own channel posts go out (arcade/announce.py), so a run is
    posted within about thirty seconds of being banked.
    """

    def get(self, request):
        tick()
        announce.tick()

        events = ArcadeEvent.objects.filter(handled_at__isnull=True).order_by('created_at', 'id')[:100]

        return Response({
            'events': [
                {
                    'id': event.id,
                    'type': event.type,
                    'payload': event.payload,
                    'created_at': event.created_at.isoformat(),
                }
                for event in events
            ],
        })


class AckEventView(BotView):
    def post(self, request, event_id):
        event = get_object_or_404(ArcadeEvent, pk=event_id)

        if event.handled_at is None:
            event.handled_at = timezone.now()
            event.save(update_fields=['handled_at'])

        return Response({'id': event.id, 'handled_at': event.handled_at.isoformat()})


class RemoveScoreView(BotView):
    def post(self, request, score_id):
        score = get_object_or_404(Score, pk=score_id)
        play.remove_score(score)

        return Response({'score_id': score.id, 'removed': True})


class CurrentPodiumView(BotView):
    """
    The live top three, which is what the roles are made to match.

    Read from the standings rather than the stored snapshot, so a reconciliation
    pass corrects a missed event instead of trusting it.
    """

    def get(self, request):
        season = tick()
        holders = podium.current_holders(season)

        return Response({
            'season': {
                'id': season.id,
                'starts_at': season.starts_at.isoformat(),
                'ends_at': season.ends_at.isoformat(),
            },
            'holders': [
                {
                    'place': row['place'],
                    'discord_user_id': row['discord_user_id'],
                    'display_name': row['display_name'],
                    'average_placement': row['average_placement'],
                    'games_played': row['games_played'],
                }
                for row in holders
            ],
        })


class LiveStandingsView(BotView):
    def get(self, request):
        season = tick()
        rows = podium.standings(season)

        return Response({
            'season': {'id': season.id, 'ends_at': season.ends_at.isoformat()},
            'standings': [
                {
                    'place': row['place'],
                    'discord_user_id': row['discord_user_id'],
                    'display_name': row['display_name'],
                    'average_placement': row['average_placement'],
                    'breakdown': row['breakdown'],
                    'games_played': row['games_played'],
                }
                for row in rows
            ],
        })


class SeasonPreviewView(BotView):
    """
    A closed season's standings, for the bot's winner check before any callout.

    The bot looks each player up in Vice Gamers, reports anyone who has left, and
    asks again. Nothing is finalized until it does.
    """

    def get(self, request, season_id):
        season = get_object_or_404(ArcadeSeason, pk=season_id)

        return Response({
            'season': {
                'id': season.id,
                'status': season.status,
                'starts_at': season.starts_at.isoformat(),
                'ends_at': season.ends_at.isoformat(),
            },
            'top': [
                {
                    'place': row['place'],
                    'discord_user_id': row['discord_user_id'],
                    'display_name': row['display_name'],
                    'average_placement': row['average_placement'],
                    'breakdown': row['breakdown'],
                }
                for row in rollover.preview(season)
            ],
        })


class SeasonFinalizeView(BotView):
    def post(self, request, season_id):
        season = get_object_or_404(ArcadeSeason, pk=season_id)
        payload = arcade_serializers.FinalizeSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        event = rollover.finalize(season, payload.validated_data['winners'])
        season.refresh_from_db()

        return Response({
            'season_id': season.id,
            'status': season.status,
            'announced': event is not None,
        })


class ClosingSeasonsView(BotView):
    def get(self, request):
        tick()

        return Response({
            'seasons': [
                {
                    'id': season.id,
                    'starts_at': season.starts_at.isoformat(),
                    'ends_at': season.ends_at.isoformat(),
                }
                for season in rollover.closing_seasons()
            ],
        })


class DailyBoardView(BotView):
    def get(self, request, game_slug, day):
        game = get_object_or_404(ArcadeGame, slug=game_slug)
        board = get_object_or_404(DailyBoard, game=game, day=day)
        season = rollover.ensure_current_season()

        return Response({
            'board_id': board.id,
            'game': {'slug': game.slug, 'display_name': game.display_name},
            'day': board.day.isoformat(),
            'entries': board.entries,
            'message_ids': board.message_ids,
            'resets_at': season.ends_at.isoformat(),
        })


class DailyBoardPostedView(BotView):
    def post(self, request, board_id):
        board = get_object_or_404(DailyBoard, pk=board_id)
        payload = arcade_serializers.PostedMessagesSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        dailyboards.record_posted_messages(board, payload.validated_data['message_ids'])

        return Response({'board_id': board.id, 'message_ids': board.message_ids})


class DepartedMembersView(BotView):
    """
    Players the bot has confirmed are no longer in Vice Gamers.

    The confirmation is the bot's job, deliberately. The API cannot see Discord
    membership, and this endpoint removes data, so it trusts a caller that has
    already checked and whose sweep abandons itself when the member list looks
    short.
    """

    def post(self, request):
        payload = arcade_serializers.DepartureBatchSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        results = departures.record_departures(payload.validated_data['departures'])

        return Response({
            'processed': len(results),
            'scores_removed': sum(result['scores_removed'] for result in results),
            'boards_changed': sum(len(result['boards_changed']) for result in results),
        })


class ActivePlayersView(BotView):
    def get(self, request):
        return Response({'discord_user_ids': departures.active_player_ids()})


class ProfileView(BotView):
    def get(self, request, discord_user_id):
        bests = PersonalBest.objects.filter(discord_user_id=discord_user_id).select_related('game')
        trophies = (
            PodiumResult.objects
            .filter(discord_user_id=discord_user_id, hidden=False)
            .select_related('season')
            .order_by('-season__starts_at')
        )

        return Response({
            'discord_user_id': discord_user_id,
            'personal_bests': [
                {
                    'game_slug': best.game.slug,
                    'game_display_name': best.game.display_name,
                    'best_score': best.best_score,
                    'achieved_at': best.achieved_at.isoformat(),
                }
                for best in bests
            ],
            'podium_history': [
                {
                    'season_id': trophy.season_id,
                    'place': trophy.place,
                    'round_ended': trophy.season.ends_at.isoformat(),
                }
                for trophy in trophies
            ],
        })


class AlertsView(BotView):
    def put(self, request, discord_user_id):
        payload = arcade_serializers.AlertSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        row, _ = ArcadeAlertOptIn.objects.update_or_create(
            discord_user_id=discord_user_id,
            defaults={'enabled': payload.validated_data['enabled']},
        )

        return Response({'discord_user_id': discord_user_id, 'enabled': row.enabled})
