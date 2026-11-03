"""
Season windows and Eastern calendar arithmetic.

Pure by design: every function takes the clock as an argument, so the daylight
saving cases are tested with frozen instants instead of waited for. This mirrors
the discipline already used in the bot's weekly digest, which documents the same
trap: a cached UTC offset would have fired the first digest an hour early once the
clocks went back.

Two rules govern this module.

1. Never add a fixed number of hours to cross a day boundary. Add days to the
   Eastern calendar date and recombine with the same wall-clock time, because a
   season is "every N days at 5 PM Eastern", not "every N times 24 hours".

2. Every instant returned from here is UTC-aware. Eastern is used to decide which
   instant is meant, never to carry it around. The reason is a sharp edge in
   Python: subtracting or comparing two aware datetimes that share the same
   `tzinfo` object ignores the zone entirely and compares wall clocks, so two
   Eastern-tagged values an hour apart across a clock change look equal in
   elapsed time. Returning UTC makes that unrepresentable rather than merely
   avoided, and matches what Django stores.
"""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

RESET_ZONE = ZoneInfo('America/New_York')

# Python's weekday(): Monday is 0, so Sunday is 6.
SUNDAY = 6

# The first season ends at the first Sunday 5 PM Eastern after go-live, and 5 PM is
# also the fallback used when a shortened interval would otherwise fire immediately.
RESET_HOUR = 17
RESET_MINUTE = 0

# A shortened interval never causes a surprise reset: the new end is pushed out to
# the next 5 PM Eastern that is at least this far away.
MIN_NOTICE = timedelta(hours=1)

DAY = timedelta(days=1)


def to_eastern(moment):
    """For reading a wall clock or formatting. Never for arithmetic between results."""
    return moment.astimezone(RESET_ZONE)


def eastern_date(moment):
    """The Eastern calendar day an instant falls on. This is what a Daily board keys on."""
    return to_eastern(moment).date()


def localize(day, hour=RESET_HOUR, minute=RESET_MINUTE):
    """
    The instant, as UTC, at which the Eastern wall clock reads `day` at `hour:minute`.

    Local times are not one-to-one with instants, and both awkward cases are
    resolved the conventional way rather than left to chance:

    - Nonexistent (the hour skipped each spring): zoneinfo resolves it with the
      pre-transition offset, which lands on a real instant one hour after the gap
      opens, so 2:30 AM becomes 3:30 AM EDT.
    - Ambiguous (the hour repeated each autumn): fold=0 picks the first pass, the
      one still on daylight time.

    Neither applies at 5 PM. They are handled because the reset interval is a
    setting, so a season can start at any wall-clock time.
    """
    naive = datetime.combine(day, time(hour, minute))
    return naive.replace(tzinfo=RESET_ZONE).astimezone(timezone.utc)


def is_nonexistent(day, hour=RESET_HOUR, minute=RESET_MINUTE):
    """True when that wall clock never happens locally, because of the spring gap."""
    naive = datetime.combine(day, time(hour, minute))
    return to_eastern(localize(day, hour, minute)).replace(tzinfo=None) != naive


def add_days_same_wall_clock(moment, days):
    """
    `days` Eastern calendar days later, at the same Eastern wall-clock time.

    Across a clock change this is deliberately not a fixed number of hours: seven
    days from 5 PM is 5 PM, whether that span contains 167, 168 or 169 hours.
    """
    local = to_eastern(moment)
    return localize(local.date() + timedelta(days=days), local.hour, local.minute)


def next_daily_occurrence(after, hour=RESET_HOUR, minute=RESET_MINUTE):
    """The first instant strictly after `after` when the Eastern clock reads hour:minute."""
    day = eastern_date(after)

    for offset in range(0, 3):
        candidate = localize(day + timedelta(days=offset), hour, minute)
        if candidate > after:
            return candidate

    raise RuntimeError('No daily occurrence found within three days, which cannot happen.')


def next_weekly_occurrence(after, weekday=SUNDAY, hour=RESET_HOUR, minute=RESET_MINUTE):
    """The first instant strictly after `after` on that Eastern weekday at hour:minute."""
    day = eastern_date(after)

    for offset in range(0, 9):
        candidate_day = day + timedelta(days=offset)
        if candidate_day.weekday() != weekday:
            continue
        candidate = localize(candidate_day, hour, minute)
        if candidate > after:
            return candidate

    raise RuntimeError('No weekly occurrence found within nine days, which cannot happen.')


def first_season_end(starts_at):
    """
    The first season ends at the first Sunday 5 PM Eastern after it starts, so a
    partial opening round is still awarded, as already decided.
    """
    return next_weekly_occurrence(starts_at)


def season_end_for(starts_at, interval_days):
    """A normal season end: the start date plus N days, same Eastern wall clock."""
    return add_days_same_wall_clock(starts_at, interval_days)


def revised_season_end(starts_at, interval_days, now):
    """
    Where an open season's end moves to when the interval setting changes.

    The end is recomputed from the season's start, not from now, so raising 7 to 14
    on day 3 moves the reset out by exactly 7 days. Lowering the interval can put
    the new end in the past or moments away, and that must never fire a reset
    nobody saw coming, so the end is pushed to the next 5 PM Eastern at least an
    hour out.
    """
    candidate = season_end_for(starts_at, interval_days)

    if candidate > now + MIN_NOTICE:
        return candidate

    return next_daily_occurrence(now + MIN_NOTICE)


def eastern_day_bounds(day):
    """
    The half-open instant range covering one Eastern calendar day.

    Found by localizing both midnights rather than adding 24 hours, because the day
    a clock change lands on is 23 or 25 hours long.
    """
    return localize(day, 0, 0), localize(day + DAY, 0, 0)


def closed_days_between(first_day, now):
    """
    Every Eastern day from `first_day` up to but excluding today, oldest first.

    A Daily board is only ever built for a day that has finished.
    """
    today = eastern_date(now)
    days = []
    cursor = first_day

    while cursor < today:
        days.append(cursor)
        cursor = cursor + DAY

    return days


def describe(moment):
    """A short Eastern label for admin confirmation messages and Discord copy."""
    local = to_eastern(moment)
    hour12 = local.hour % 12 or 12
    meridiem = 'AM' if local.hour < 12 else 'PM'
    return (
        f'{local:%A}, {local:%b} {local.day} at '
        f'{hour12}:{local:%M} {meridiem} {local:%Z}'
    )
