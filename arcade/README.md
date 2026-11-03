# Vice Arcade API

Leaderboards, rounds and the event outbox behind the Vice Arcade Discord
Activity. Mounted at `api/arcade/`, alongside `api/gaming/` and `api/creators/`.

## Environment

Added to the API's server `.env`. Nothing here is read at import time, so
`manage.py check`, migrations and the tests all run without a full production
environment; a missing value raises when it is first needed.

| Variable | What it is |
|---|---|
| `VICE_ARCADE_CLIENT_ID` | The Vice Arcade application id. Public. Also builds the allowed CORS origin. |
| `VICE_ARCADE_CLIENT_SECRET` | Secret. Used only to exchange an OAuth code for an access token. |
| `VICE_ARCADE_BOT_TOKEN` | Secret. Used only to read an Activity instance's location. |
| `VICE_GAMERS_GUILD_ID` | The only guild play is allowed from. |
| `VG_ARCADE_CHANNEL_ID` | The only channel play is allowed from. |
| `ARCADE_SESSION_SIGNING_KEY` | Secret. Signs play sessions and launch identities. |
| `BOT_API_SECRET` | Already present. Authenticates the bot's calls. |

The three Vice Arcade credentials keep their spec names rather than the plain
`BOT_TOKEN` used in the client-side scratch `.env`, because this API also talks
to Vice Verify Bot and two different tokens called `BOT_TOKEN` is a mistake
waiting to happen.

## How a round works

A round, called a season internally and "this round" to players, is one reset
cycle. Its length is `reset_interval_days` in Django admin: one value for every
game, default 7, and a change applies to the round in progress by moving its end
to its start plus the new interval. Lowering it can put that end in the past,
which never fires a reset nobody saw coming; those are pushed to the next 5 PM
Eastern at least an hour out.

Rounds open and close lazily. There is no cron job, because finalizing a round
needs the bot to confirm the winners are still in the guild, which a cron job
cannot do. The bot's thirty-second poll of `events/pending` is the heartbeat, and
when the bot is down nothing needs advancing because nothing can be posted.
`manage.py arcade_roll_seasons` does the same work by hand if it is ever wanted
on a timer.

## Two rules worth knowing before changing anything

**Round membership is a range query, never a stored key.** A score belongs to the
round whose window contains its `created_at`. Changing the interval moves an open
round's end, so a key written at score time would become wrong for scores near
the old boundary.

**Everything `seasons.py` returns is UTC.** Eastern decides which instant is
meant and is never carried around. Subtracting or comparing two aware datetimes
that share a `tzinfo` object ignores the zone and compares wall clocks, so two
Eastern-tagged values an hour apart across a clock change measure as equal. That
is the daylight saving bug this module exists to prevent, and returning UTC makes
it unrepresentable rather than merely avoided.

## Endpoints

### The Activity

Authenticates with `X-Arcade-Identity`, the signed identity issued at launch.
Identity comes from Discord once per launch, never from a claim the browser makes
about itself.

| Method | Path | Notes |
|---|---|---|
| POST | `token` | Exchanges the OAuth code. The only use of the client secret. |
| POST | `sessions` | The guild and channel gate, then a signed single-use session. Fails closed if Discord cannot be reached. |
| POST | `scores` | Banks one run. |
| GET | `leaderboards/<game_slug>` | Daily, round and all-time, plus your own rank and the reset time. |

### The bot

Authenticates with `X-Bot-Secret`, the pattern `community.views` already uses,
compared in constant time.

| Method | Path |
|---|---|
| GET | `events/pending` |
| POST | `events/<id>/ack` |
| POST | `scores/<id>/approve`, `scores/<id>/remove` |
| GET | `podium/current`, `standings/live` |
| GET | `seasons/closing`, `seasons/<id>/preview` |
| POST | `seasons/<id>/finalize` |
| GET | `daily/<game_slug>/<day>` |
| POST | `daily/<board_id>/posted` |
| POST | `members/departed` |
| GET | `players/active` |
| GET | `profile/<discord_user_id>` |
| PUT | `alerts/<discord_user_id>` |

## Departures

Anyone who is not in Vice Gamers appears on no leaderboard. The bot confirms a
departure against Discord before calling `members/departed`, and its sweep
abandons itself when the member list comes back short, because treating current
members as departed would wipe real scores.

Scores are marked, not deleted: `removed=True` with `removal_reason`. A false
departure signal is the likeliest failure in this feature, and the reason keeps
the owner's own removals distinct so they are never relabelled.

Daily boards are renumbered and their posted messages edited. Finalized rounds
are not renumbered: the row is hidden and the other winners keep their places, so
a past podium can read 1st and 3rd with 2nd missing.

## Score integrity, stated plainly

The game runs on the player's device, so a determined cheater can fake inputs.
The plausibility limits in `ArcadeGame.plausibility` raise the effort required
and nothing more. Replaying a flagged run server side from its seed and input log
would be the next step up and is out of scope for v1. A score that beats the
approved all-time best is saved flagged and unapproved, so it shows with a
pending marker and moves no role until it is reviewed.

`max_score_per_piece` is intentionally unset on the seeded Vetrix row. It is the
most useful of the checks and needs real numbers from a playtest; a guess that is
too tight would reject genuine runs.

## Tests

```
python manage.py test arcade
```
