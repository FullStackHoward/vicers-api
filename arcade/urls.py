"""
Arcade routes.

Mounted under `api/arcade/` to match the existing `api/gaming/` and
`api/creators/` apps. The arcade page at arcade.vicers.net calls these
directly, which is why that origin is in CORS_ALLOWED_ORIGINS.
"""

from django.urls import path

from . import views

urlpatterns = [
    # The arcade page
    path('token', views.TokenExchangeView.as_view(), name='arcade-token'),
    path('appearance', views.AppearanceView.as_view(), name='arcade-appearance'),
    path('sessions', views.SessionView.as_view(), name='arcade-sessions'),
    path('scores', views.ScoreSubmitView.as_view(), name='arcade-scores'),
    path('leaderboards/<slug:game_slug>', views.LeaderboardView.as_view(), name='arcade-leaderboards'),

    # The bot
    path('events/pending', views.PendingEventsView.as_view(), name='arcade-events-pending'),
    path('events/<int:event_id>/ack', views.AckEventView.as_view(), name='arcade-events-ack'),
    path('scores/<int:score_id>/remove', views.RemoveScoreView.as_view(), name='arcade-score-remove'),
    path('podium/current', views.CurrentPodiumView.as_view(), name='arcade-podium-current'),
    path('standings/live', views.LiveStandingsView.as_view(), name='arcade-standings-live'),
    path('seasons/closing', views.ClosingSeasonsView.as_view(), name='arcade-seasons-closing'),
    path('seasons/<int:season_id>/preview', views.SeasonPreviewView.as_view(), name='arcade-season-preview'),
    path('seasons/<int:season_id>/finalize', views.SeasonFinalizeView.as_view(), name='arcade-season-finalize'),
    # This one is listed first deliberately: the pattern below matches
    # "daily/5/posted" as game "5" on day "posted" and answers 404.
    path('daily/<int:board_id>/posted', views.DailyBoardPostedView.as_view(), name='arcade-daily-posted'),
    path('daily/<slug:game_slug>/<str:day>', views.DailyBoardView.as_view(), name='arcade-daily'),
    path('members/departed', views.DepartedMembersView.as_view(), name='arcade-members-departed'),
    path('players/active', views.ActivePlayersView.as_view(), name='arcade-players-active'),
    path('profile/<str:discord_user_id>', views.ProfileView.as_view(), name='arcade-profile'),
    path('alerts/<str:discord_user_id>', views.AlertsView.as_view(), name='arcade-alerts'),
]
