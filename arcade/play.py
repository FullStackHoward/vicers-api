"""
Issuing play sessions and accepting scores.

The gate runs at session issue, not at submit, so a player is told they cannot
play before they spend a run rather than after. Submit then re-checks
everything that could have changed in between: the session being spent, expiring,
or the player having left the server mid-run.

Identity never comes from the client. The access token is exchanged for a real
user id through Discord, and the user id the browser sends is ignored.
"""

import secrets
from datetime import timedelta

from django.core import signing
from django.db import transaction
from django.utils import timezone

from . import alerts, conf, departures, discord_api, leaderboards, plausibility, podium, rollover
from .models import (
    ArcadeEvent,
    ArcadeSeason,
    EventType,
    PersonalBest,
    PlaySession,
    RemovalReason,
    RunPost,
    Score,
    SeasonStatus,
)

SIGNING_SALT = 'arcade.play-session'
IDENTITY_SALT = 'arcade.identity'

# How long the signed identity issued at sign-in stays good for. A week, so a
# member is not sent through Discord every visit. It carries no privileges beyond
# naming the player, and membership is checked again with Discord before every
# run, so a long life does not let anyone who has left keep playing.
IDENTITY_TTL_SECONDS = 7 * 24 * 60 * 60

# The seed the game feeds its deterministic RNG. A uint32, because that is what
# mulberry32 consumes in the client engine.
SEED_BITS = 32


class GateRefused(Exception):
    """The player may not start a run here. `reason` is a key, not prose."""

    def __init__(self, reason, detail=''):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class SubmissionRefused(Exception):
    def __init__(self, reason, detail=''):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def new_seed():
    return secrets.randbelow(2 ** SEED_BITS)


def sign_identity(discord_user_id, display_name):
    """
    Proof that we asked Discord who this is.

    Identity is established once, at sign-in, by exchanging the OAuth code and
    calling `/users/@me`. Everything after that presents this token, so reading a
    leaderboard does not cost a Discord round trip. It is signed with the same
    key as a play session and carries no privileges beyond naming the player.
    """
    return signing.dumps(
        {'user': str(discord_user_id), 'name': display_name},
        key=conf.session_signing_key(),
        salt=IDENTITY_SALT,
    )


def read_identity(token):
    """The verified player behind an identity token, or a refusal."""
    try:
        payload = signing.loads(
            token,
            key=conf.session_signing_key(),
            salt=IDENTITY_SALT,
            max_age=IDENTITY_TTL_SECONDS,
        )
    except signing.SignatureExpired:
        raise GateRefused('identity_expired', 'Sign in with Discord again.')
    except signing.BadSignature:
        raise GateRefused('bad_identity', 'That identity token is not valid.')

    user = payload.get('user')
    if not user:
        raise GateRefused('bad_identity', 'That identity token is incomplete.')

    return str(user), payload.get('name') or ''


def sign_session(session):
    return signing.dumps(
        {'session_id': str(session.id), 'user': session.discord_user_id},
        key=conf.session_signing_key(),
        salt=SIGNING_SALT,
    )


def read_session_token(token):
    """
    The session id a token carries, or a refusal.

    The signature proves the token came from us. It does not prove the session is
    still usable, which is the database's job.
    """
    try:
        payload = signing.loads(
            token,
            key=conf.session_signing_key(),
            salt=SIGNING_SALT,
        )
    except signing.BadSignature:
        raise SubmissionRefused('bad_session_token', 'The session token is not valid.')

    session_id = payload.get('session_id')
    if not session_id:
        raise SubmissionRefused('bad_session_token', 'The session token is incomplete.')

    return session_id


def confirm_member(discord_user_id):
    """
    The membership gate: the player has to be in Vice Gamers right now.

    Asked of Discord at sign-in and again before every run, so somebody who has
    left cannot keep playing on an identity issued while they were still in.
    DiscordUnavailable is left to the caller, which must fail closed.

    Returns the location a session is stamped with. There is no channel any more:
    the arcade is a web page, and membership is the whole gate.
    """
    try:
        member = discord_api.guild_member(conf.guild_id(), discord_user_id)
    except discord_api.DiscordError as error:
        if error.code in (discord_api.UNKNOWN_MEMBER, discord_api.UNKNOWN_USER) or error.status == 404:
            raise GateRefused('wrong_guild')
        raise

    # A member who has joined but not yet accepted the rules screen. Discord itself
    # will not let them talk yet, so neither does the arcade.
    if member.get('pending'):
        raise GateRefused('member_pending')

    return {'guild_id': conf.guild_id(), 'channel_id': ''}


@transaction.atomic
def issue_session(discord_user_id, game, location, instance_id='', now=None):
    """
    A signed, single-use session. The row is the authority; the token only names it.
    """
    now = now or timezone.now()

    session = PlaySession.objects.create(
        discord_user_id=str(discord_user_id),
        game=game,
        seed=new_seed(),
        guild_id=str(location.get('guild_id') or ''),
        channel_id=str(location.get('channel_id') or ''),
        instance_id=str(instance_id or ''),
        expires_at=now + timedelta(hours=conf.SESSION_TTL_HOURS),
    )

    return session, sign_session(session)


def _load_usable_session(session_id, discord_user_id, now):
    try:
        session = PlaySession.objects.select_for_update().get(pk=session_id)
    except (PlaySession.DoesNotExist, ValueError, TypeError):
        raise SubmissionRefused('unknown_session', 'That session is not known.')

    if session.discord_user_id != str(discord_user_id):
        raise SubmissionRefused('session_not_yours', 'That session belongs to someone else.')

    # Checked before the spent and expired cases because a departure spends the
    # player's open sessions, which would otherwise report the symptom instead of
    # the cause.
    if departures.session_invalidated_by_departure(session.discord_user_id, session.issued_at):
        raise SubmissionRefused('member_left', 'That session predates the player leaving.')

    if session.used:
        raise SubmissionRefused('session_used', 'That session has already been submitted.')

    if session.expires_at <= now:
        raise SubmissionRefused('session_expired', 'That session has expired.')

    if session.guild_id != conf.guild_id():
        raise SubmissionRefused('wrong_guild', 'That session was not issued in Vice Gamers.')

    return session


@transaction.atomic
def submit_score(token, discord_user_id, display_name, score, run_stats, input_log=None, now=None):
    """
    Bank one run.

    A run that passes the plausibility limits is on every board, and counts
    towards the podium, the moment it is saved. There is no review step: the
    limits are the only gate, and the owner can still take a score off the
    boards afterwards with `remove_score`.
    """
    now = now or timezone.now()
    session_id = read_session_token(token)
    session = _load_usable_session(session_id, discord_user_id, now)

    stats = dict(run_stats or {})

    claimed_seed = stats.get('seed')
    if claimed_seed is not None and int(claimed_seed) != session.seed:
        raise SubmissionRefused('seed_mismatch', 'The run was not played on the issued seed.')

    problems = plausibility.check(session.game, score, stats)
    if problems:
        raise SubmissionRefused('implausible', '; '.join(problems))

    # Read before this run is saved, so the post can say whether it beat it.
    previous_best = (
        PersonalBest.objects
        .filter(discord_user_id=str(discord_user_id), game=session.game)
        .values_list('best_score', flat=True)
        .first()
    )

    # Read before this run is saved: the best of everybody else, which is the
    # record this run has to pass to be posted as a new one. The first score onto
    # an empty board is nobody's record to lose, so it posts nothing.
    previous_record = (
        leaderboards.visible_scores(session.game)
        .exclude(discord_user_id=str(discord_user_id))
        .order_by('-score', 'created_at', 'id')
        .first()
    )
    record_to_beat = leaderboards.alltime_best(session.game)
    is_new_record = (
        previous_record is not None
        and score > record_to_beat.score
    )

    # Who is first before this run, for the opt-in "your first place was taken" DM.
    season = rollover.ensure_current_season(now)
    leaders = alerts.leaders_before(session.game, season)

    saved = Score.objects.create(
        discord_user_id=str(discord_user_id),
        display_name=display_name[:64],
        game=session.game,
        session=session,
        score=score,
        run_stats=stats,
        input_log=input_log,
    )

    session.used = True
    session.save(update_fields=['used'])

    leaderboards.recompute_personal_best(saved.discord_user_id, session.game)

    podium.refresh(season)
    alerts.queue(saved, leaders)

    if is_new_record:
        ArcadeEvent.objects.create(
            type=EventType.NEW_ALLTIME_RECORD,
            payload={
                'score_id': saved.id,
                'game_slug': session.game.slug,
                'game_display_name': session.game.display_name,
                'discord_user_id': saved.discord_user_id,
                'display_name': saved.display_name,
                'score': saved.score,
                'previous_best': previous_record.score,
                'previous_holder': previous_record.discord_user_id,
            },
        )

    # Queued for the "just played" post in the arcade channel (arcade/announce.py).
    RunPost.objects.create(score=saved, previous_best=previous_best)

    return saved


@transaction.atomic
def remove_score(score, now=None):
    """Owner review: take a score off every board."""
    now = now or timezone.now()

    if not score.removed:
        score.removed = True
        score.removal_reason = RemovalReason.OWNER_REMOVED
        score.save(update_fields=['removed', 'removal_reason'])

    leaderboards.recompute_personal_best(score.discord_user_id, score.game)

    season = rollover.ensure_current_season(now)
    podium.refresh(season)

    # A removal inside an already finalized round corrects the stored history and
    # posts a short correction. Live roles belong to the running season and are
    # left alone.
    for finalized in ArcadeSeason.objects.filter(
        status=SeasonStatus.FINALIZED,
        starts_at__lte=score.created_at,
        ends_at__gt=score.created_at,
    ):
        rollover.recompute_finalized(finalized)

    return score
