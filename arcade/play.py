"""
Issuing play sessions and accepting scores.

The gate runs at session issue, not at submit, so a player is told they are in
the wrong place before they spend a run rather than after. Submit then re-checks
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

from . import conf, departures, leaderboards, plausibility, podium, rollover
from .models import (
    ArcadeEvent,
    ArcadeSeason,
    EventType,
    PlaySession,
    RemovalReason,
    Score,
    SeasonStatus,
)

SIGNING_SALT = 'arcade.play-session'
IDENTITY_SALT = 'arcade.identity'

# How long the signed identity issued at launch stays good for. One Activity
# session is far shorter than this; the point is to call Discord once per launch
# rather than once per board view, not to keep anyone signed in.
IDENTITY_TTL_SECONDS = 12 * 60 * 60

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

    Identity is established once, at launch, by exchanging the OAuth code and
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
        raise GateRefused('identity_expired', 'Reload the Activity to sign in again.')
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


def check_location(instance):
    """
    The guild and channel gate.

    Play is limited to one channel, enforced here rather than by Discord's
    integration settings, so the restriction holds whatever the portal is set to.
    """
    location = (instance or {}).get('location') or {}

    if str(location.get('guild_id') or '') != conf.guild_id():
        raise GateRefused('wrong_guild')

    if str(location.get('channel_id') or '') != conf.arcade_channel_id():
        raise GateRefused('wrong_channel')

    return location


def check_membership_of_instance(instance, discord_user_id):
    """The player has to actually be in the instance they claim to be playing in."""
    users = (instance or {}).get('users') or []
    ids = {str(entry.get('id') if isinstance(entry, dict) else entry) for entry in users}

    if ids and str(discord_user_id) not in ids:
        raise GateRefused('not_in_instance')


@transaction.atomic
def issue_session(discord_user_id, game, location, instance_id, now=None):
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

    if session.channel_id != conf.arcade_channel_id():
        raise SubmissionRefused('wrong_channel', 'That session was not issued in the arcade channel.')

    return session


@transaction.atomic
def submit_score(token, discord_user_id, display_name, score, run_stats, input_log=None, now=None):
    """
    Bank one run.

    A score that beats the approved all-time best is saved flagged and unapproved:
    it shows on boards with a pending marker, and it moves no role until the owner
    approves it. Everything else that passes the limits is approved on the spot,
    which the spec states plainly is what makes the plausibility checks the only
    gate on the podium.
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

    record_to_beat = leaderboards.alltime_best(session.game)
    beats_record = record_to_beat is None or score > record_to_beat.score
    # The first score onto an empty board is not a displacement, so it is not
    # treated as a record awaiting review.
    is_pending_record = beats_record and record_to_beat is not None

    saved = Score.objects.create(
        discord_user_id=str(discord_user_id),
        display_name=display_name[:64],
        game=session.game,
        session=session,
        score=score,
        run_stats=stats,
        input_log=input_log,
        flagged=is_pending_record,
        approved=not is_pending_record,
    )

    session.used = True
    session.save(update_fields=['used'])

    if is_pending_record:
        ArcadeEvent.objects.create(
            type=EventType.RECORD_PENDING_REVIEW,
            payload={
                'score_id': saved.id,
                'game_slug': session.game.slug,
                'game_display_name': session.game.display_name,
                'discord_user_id': saved.discord_user_id,
                'display_name': saved.display_name,
                'score': saved.score,
                'previous_best': record_to_beat.score,
                'previous_holder': record_to_beat.discord_user_id,
                'run_stats': stats,
            },
        )

    leaderboards.recompute_personal_best(saved.discord_user_id, session.game)

    season = rollover.ensure_current_season(now)
    podium.refresh(season)

    return saved


@transaction.atomic
def approve_score(score, now=None):
    """
    Owner review: let a flagged record count.

    The public record post is written here rather than at submit, so nothing is
    announced before the owner has looked at it. It is written only when a
    different player is displaced, because the first score onto an empty board is
    nobody's record to lose.
    """
    now = now or timezone.now()

    # Guarded on `approved` alone. `flagged` is history that stays set after review,
    # so including it here would let a second approval announce the same record twice.
    if score.approved:
        return None

    previous = (
        leaderboards.counting_scores(score.game)
        .exclude(discord_user_id=score.discord_user_id)
        .order_by('-score', 'created_at', 'id')
        .first()
    )

    score.approved = True
    score.save(update_fields=['approved'])

    leaderboards.recompute_personal_best(score.discord_user_id, score.game)

    season = rollover.ensure_current_season(now)
    podium.refresh(season)

    displaced_someone = previous is not None and score.score > previous.score
    if not displaced_someone:
        return None

    return ArcadeEvent.objects.create(
        type=EventType.NEW_ALLTIME_RECORD,
        payload={
            'score_id': score.id,
            'game_slug': score.game.slug,
            'game_display_name': score.game.display_name,
            'discord_user_id': score.discord_user_id,
            'display_name': score.display_name,
            'score': score.score,
            'previous_best': previous.score,
            'previous_holder': previous.discord_user_id,
        },
    )


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
