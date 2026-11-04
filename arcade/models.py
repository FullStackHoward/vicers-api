"""
Vice Arcade: leaderboards, seasons, and the event outbox the bot drains.

Two rules govern this module and are repeated where they bite:

1. A score belongs to the season whose window contains its `created_at`. Season
   membership is always a range query against ArcadeSeason, never a stored foreign
   key on Score. Changing `reset_interval_days` moves an open season's `ends_at`,
   which would turn a stored key into a lie for scores near the old boundary.
2. Anyone who is not in Vice Gamers appears on no leaderboard. Their scores keep
   their rows with `removed=True` so a false departure signal can be undone, and
   every query that feeds a board or a response filters them out.
"""

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class SeasonStatus(models.TextChoices):
    OPEN = 'open', 'Open'
    CLOSING = 'closing', 'Closing'
    FINALIZED = 'finalized', 'Finalized'


class RemovalReason(models.TextChoices):
    MEMBER_LEFT = 'member_left', 'Left Vice Gamers'
    OWNER_REMOVED = 'owner_removed', 'Removed by owner review'


class EventType(models.TextChoices):
    NEW_ALLTIME_RECORD = 'new_alltime_record', 'New all-time record'
    RECORD_PENDING_REVIEW = 'record_pending_review', 'Record pending review'
    PB_PASSED = 'pb_passed', 'Personal best passed'
    PODIUM_CHANGED = 'podium_changed', 'Podium changed'
    SEASON_CLOSING = 'season_closing', 'Season closing'
    SEASON_AWARDED = 'season_awarded', 'Season awarded'
    DAILY_BOARD_CLOSED = 'daily_board_closed', 'Daily board closed'
    DAILY_BOARDS_CHANGED = 'daily_boards_changed', 'Daily boards changed'


class ArcadeSettings(models.Model):
    """
    One row, always pk=1. Edited in Django admin, which is where every other app in
    this project keeps its configuration.

    The singleton is enforced here as well as in the admin, so application code can
    call `load()` without depending on an admin screen to hold the invariant.
    """

    SINGLETON_PK = 1

    MIN_RESET_INTERVAL_DAYS = 1
    MAX_RESET_INTERVAL_DAYS = 90
    DEFAULT_RESET_INTERVAL_DAYS = 7

    reset_interval_days = models.PositiveSmallIntegerField(
        default=DEFAULT_RESET_INTERVAL_DAYS,
        validators=[
            MinValueValidator(MIN_RESET_INTERVAL_DAYS),
            MaxValueValidator(MAX_RESET_INTERVAL_DAYS),
        ],
        verbose_name='Leaderboard reset interval (days)',
        help_text='How many days a season runs before the Daily and season boards '
                  'reset. All-time scores and personal bests are never reset. One '
                  'value covers every game. Changing it moves the open season to '
                  'its start date plus this many days.',
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        verbose_name='Last changed in admin by',
        help_text='Only recorded for changes made through this admin page. A change '
                  'made in a shell or a migration leaves this as it was.',
    )

    class Meta:
        verbose_name = 'Arcade settings'
        verbose_name_plural = 'Arcade settings'

    def __str__(self):
        return f'Arcade settings (reset every {self.reset_interval_days} days)'

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError('The arcade settings row cannot be deleted.')

    @classmethod
    def load(cls):
        row, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return row


# Uploads are served straight from MEDIA_ROOT to every visitor, so an image of
# tens of megabytes is a page that never finishes loading on a phone.
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def limit_image_size(image):
    if image and image.size > MAX_IMAGE_BYTES:
        raise ValidationError(
            f'Images have to be {MAX_IMAGE_BYTES // (1024 * 1024)} MB or smaller. '
            f'Export it smaller, or as a JPG or WebP instead of a PNG.'
        )


class ArcadeAppearance(models.Model):
    """
    How the arcade page looks: the images and wording that change without a
    deploy. One row, always pk=1, edited in Django admin like ArcadeSettings.

    Every field is optional. Left blank, the page uses its built-in look, so the
    arcade never shows a broken image while this is being filled in.
    """

    SINGLETON_PK = 1
    DEFAULT_TAGLINE = 'Pick a cabinet.'

    background_image = models.ImageField(
        upload_to='arcade/appearance/',
        blank=True,
        validators=[limit_image_size],
        help_text='Behind the whole page. It is cropped to fill the screen, so keep '
                  'the important part near the middle. At least 1920x1080, '
                  'as a JPG or WebP, 5 MB or smaller.',
    )
    logo_image = models.ImageField(
        upload_to='arcade/appearance/',
        blank=True,
        validators=[limit_image_size],
        help_text='Optional. Shown instead of the VICE ARCADE heading. A PNG or WebP '
                  'with a transparent background, around 1200 pixels wide.',
    )
    tagline = models.CharField(
        max_length=120,
        blank=True,
        default=DEFAULT_TAGLINE,
        help_text='The line under the heading.',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Arcade appearance'
        verbose_name_plural = 'Arcade appearance'

    def __str__(self):
        return 'Arcade appearance'

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError('The arcade appearance row cannot be deleted.')

    @classmethod
    def load(cls):
        row, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return row


class ArcadeGame(models.Model):
    slug = models.SlugField(max_length=40, unique=True)
    display_name = models.CharField(max_length=60)
    is_active = models.BooleanField(default=True)
    active_from = models.DateTimeField(
        help_text='A game counts toward a season only if it was active when that '
                  'season started, so a cabinet added mid-season starts counting at '
                  'the next reset.',
    )
    plausibility = models.JSONField(
        default=dict,
        blank=True,
        help_text='Per-game limits used to reject impossible runs. See '
                  'arcade/plausibility.py for the keys this reads.',
    )
    thumbnail = models.ImageField(
        upload_to='arcade/thumbnails/',
        blank=True,
        validators=[limit_image_size],
        help_text='The picture on this game\'s cabinet in the lobby. Shown at 16:9, '
                  'so 1280x720 is ideal.',
    )

    class Meta:
        ordering = ['display_name']

    def __str__(self):
        return self.display_name

    def counts_for_season(self, season):
        return self.is_active and self.active_from <= season.starts_at


class ArcadeSeason(models.Model):
    """
    One reset cycle. Player-facing text calls this a round, never a week, because
    its length is a setting.
    """

    starts_at = models.DateTimeField(unique=True)
    ends_at = models.DateTimeField()
    status = models.CharField(
        max_length=12,
        choices=SeasonStatus.choices,
        default=SeasonStatus.OPEN,
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveSmallIntegerField(default=1)
    live_top = models.JSONField(
        default=list,
        blank=True,
        help_text='The top three this season last told the bot about. Compared '
                  'against freshly computed standings so a podium_changed event is '
                  'written only when the top three actually move, not on every '
                  'score. Lives on the season so it clears itself at a reset.',
    )

    class Meta:
        ordering = ['-starts_at']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(ends_at__gt=models.F('starts_at')),
                name='arcade_season_ends_after_start',
            ),
        ]

    def __str__(self):
        return f'Season from {self.starts_at:%Y-%m-%d %H:%M} ({self.status})'

    def contains(self, moment):
        return self.starts_at <= moment < self.ends_at


class PlaySession(models.Model):
    """
    Issued only after the guild and channel gate passes. Single use, and the row is
    the authority: the signed token the client holds carries this id, never a score.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    discord_user_id = models.CharField(max_length=32, db_index=True)
    game = models.ForeignKey(ArcadeGame, on_delete=models.CASCADE, related_name='sessions')
    seed = models.PositiveBigIntegerField(
        help_text='The uint32 the game feeds its deterministic RNG, so a run can be '
                  'reproduced from this plus the input log.',
    )
    guild_id = models.CharField(max_length=32)
    channel_id = models.CharField(max_length=32)
    instance_id = models.CharField(max_length=128, blank=True)
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-issued_at']
        indexes = [
            models.Index(fields=['discord_user_id', 'used']),
        ]

    def __str__(self):
        return f'Session {self.id} for {self.discord_user_id}'


class Score(models.Model):
    discord_user_id = models.CharField(max_length=32, db_index=True)
    display_name = models.CharField(
        max_length=64,
        help_text='Stored at score time so boards and a future public leaderboard '
                  'render without any Discord call.',
    )
    game = models.ForeignKey(ArcadeGame, on_delete=models.CASCADE, related_name='scores')
    session = models.OneToOneField(
        PlaySession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='score',
    )
    score = models.PositiveIntegerField()
    run_stats = models.JSONField(default=dict, blank=True)
    input_log = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    flagged = models.BooleanField(
        default=False,
        help_text='A score that beat the all-time best. Shown with a pending marker '
                  'until it is approved.',
    )
    approved = models.BooleanField(default=True)
    removed = models.BooleanField(default=False)
    removal_reason = models.CharField(
        max_length=20,
        choices=RemovalReason.choices,
        blank=True,
    )

    class Meta:
        ordering = ['-score', 'created_at']
        indexes = [
            models.Index(fields=['game', 'removed', '-score']),
            models.Index(fields=['game', 'created_at']),
        ]

    def __str__(self):
        return f'{self.display_name} scored {self.score} on {self.game.slug}'

    @property
    def counts_for_podium(self):
        return self.approved and not self.removed and not self.flagged


class PersonalBest(models.Model):
    discord_user_id = models.CharField(max_length=32, db_index=True)
    game = models.ForeignKey(ArcadeGame, on_delete=models.CASCADE, related_name='personal_bests')
    best_score = models.PositiveIntegerField()
    score = models.ForeignKey(Score, on_delete=models.CASCADE, related_name='+')
    achieved_at = models.DateTimeField()

    class Meta:
        ordering = ['-best_score']
        constraints = [
            models.UniqueConstraint(
                fields=['discord_user_id', 'game'],
                name='arcade_one_personal_best_per_game',
            ),
        ]

    def __str__(self):
        return f'{self.discord_user_id} best {self.best_score} on {self.game.slug}'


class PodiumResult(models.Model):
    """
    A finalized season's top three. Places are never renumbered: when a winner
    leaves the server their row is hidden and the others keep their places, which
    is deliberately different from Daily boards.
    """

    season = models.ForeignKey(ArcadeSeason, on_delete=models.CASCADE, related_name='podium')
    place = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(3)])
    discord_user_id = models.CharField(max_length=32, db_index=True)
    display_name = models.CharField(max_length=64)
    average_placement = models.FloatField()
    breakdown = models.JSONField(default=dict, blank=True)
    hidden = models.BooleanField(
        default=False,
        help_text='Set when this player left Vice Gamers. The row is kept for '
                  'history and shown to nobody.',
    )

    class Meta:
        ordering = ['season', 'place']
        constraints = [
            models.UniqueConstraint(
                fields=['season', 'place'],
                name='arcade_one_result_per_place',
            ),
        ]

    def __str__(self):
        return f'Place {self.place}: {self.display_name}'


class DailyBoard(models.Model):
    """
    A closed Eastern day, snapshotted. Unlike a finalized podium, a Daily board is
    renumbered when a player leaves, and the posted Discord messages are edited to
    match.
    """

    game = models.ForeignKey(ArcadeGame, on_delete=models.CASCADE, related_name='daily_boards')
    day = models.DateField(help_text='The Eastern calendar day this board covers.')
    finalized_at = models.DateTimeField(auto_now_add=True)
    entries = models.JSONField(default=list, blank=True)
    message_ids = models.JSONField(
        default=list,
        blank=True,
        help_text='The Discord message ids this board was posted as, in order, so a '
                  'departure can edit them.',
    )

    class Meta:
        ordering = ['-day', 'game']
        constraints = [
            models.UniqueConstraint(
                fields=['game', 'day'],
                name='arcade_one_board_per_game_per_day',
            ),
        ]

    def __str__(self):
        return f'{self.game.slug} daily board for {self.day}'


class ArcadeAlertOptIn(models.Model):
    discord_user_id = models.CharField(max_length=32, unique=True)
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        state = 'on' if self.enabled else 'off'
        return f'Alerts {state} for {self.discord_user_id}'


class ArcadeEvent(models.Model):
    """
    The outbox. The bot polls `GET /arcade/events/pending`, acts, then acks. Chosen
    over a webhook because the bot has no HTTP listener and an outbox survives a
    restart.
    """

    type = models.CharField(max_length=24, choices=EventType.choices, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['handled_at', 'created_at']),
        ]

    def __str__(self):
        state = 'handled' if self.handled_at else 'pending'
        return f'{self.type} ({state})'


class MemberDeparture(models.Model):
    """
    Every time a player leaves Vice Gamers. Kept as history rather than a flag,
    because a score is rejected when its session was issued before the player's
    most recent departure, and a rejoin has to make later sessions valid again.
    """

    discord_user_id = models.CharField(max_length=32, db_index=True)
    left_at = models.DateTimeField()
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-left_at']

    def __str__(self):
        return f'{self.discord_user_id} left at {self.left_at:%Y-%m-%d %H:%M}'
