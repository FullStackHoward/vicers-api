"""
Who may call what.

Two audiences, two mechanisms.

The bot authenticates with the shared `X-Bot-Secret` header, matching the pattern
already used by `community.views.CreateAnnouncementView`. That check is written
once here as a permission class rather than repeated inline across the dozen
bot-only arcade endpoints.

The arcade page authenticates with the signed identity it was given at sign-in. That
identity originates from Discord, never from a claim the browser made about
itself.
"""

from rest_framework import authentication, exceptions, permissions

from . import conf, play


class IsBot(permissions.BasePermission):
    """The shared-secret header the bot already uses for its other API calls."""

    message = 'This endpoint is for Vice Verify Bot.'

    def has_permission(self, request, view):
        sent = request.headers.get('X-Bot-Secret')
        if not sent:
            return False

        # Compared with a constant-time check so a wrong secret does not leak how
        # much of it was right.
        import hmac
        return hmac.compare_digest(str(sent), conf.bot_api_secret())


class ArcadeIdentity(authentication.BaseAuthentication):
    """
    Resolves the signed identity token into a player, with no database user.

    DRF wants a user object, and there isn't one: arcade players are Discord
    accounts, not Django accounts. The verified id is attached to the request
    instead, and views read `request.arcade_player`.
    """

    header = 'X-Arcade-Identity'

    def authenticate_header(self, request):
        # Without this DRF turns every 401 into a 403, which tells the page
        # nothing about whether to send the player to sign in again.
        return self.header

    def authenticate(self, request):
        token = request.headers.get(self.header)
        if not token:
            return None

        try:
            user_id, name = play.read_identity(token)
        except play.GateRefused as refused:
            raise exceptions.AuthenticationFailed(refused.detail or refused.reason)

        request.arcade_player = {'discord_user_id': user_id, 'display_name': name}
        return None


def require_player(request):
    """The verified player on this request, or a 401 for the view to return."""
    player = getattr(request, 'arcade_player', None)
    if not player:
        raise exceptions.NotAuthenticated(
            'Sign in with Discord to play.'
        )
    return player
