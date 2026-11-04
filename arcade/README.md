# Vice Arcade API

Leaderboards, rounds, page appearance and the event outbox behind Vice Arcade,
the web page at arcade.vicers.net. Mounted at `api/arcade/`, alongside
`api/gaming/` and `api/creators/`.

Players sign in with Discord (OAuth2, `identify` scope only) and have to be in
Vice Gamers to play. It started as a Discord Activity; it moved to a web page
because Discord only shows unverified Activities in servers under 25 members.

## Environment

Added to the API's server `.env`. Nothing here is read at import time, so
`manage.py check`, migrations and the tests all run without a full production
environment; a missing value raises when it is first needed.

| Variable | What it is |
|---|---|
| `VICE_ARCADE_CLIENT_ID` | The Vice Arcade application id. Public. |
| `VICE_ARCADE_CLIENT_SECRET` | Secret. Used only to exchange a sign-in code for an access token. |
| `VICE_ARCADE_BOT_TOKEN` | Secret. Looks up whether a player is in Vice Gamers, and sends the arcade's channel posts. |
| `VICE_GAMERS_GUILD_ID` | The server players have to be in. |
| `ARCADE_SESSION_SIGNING_KEY` | Secret. Signs play sessions and sign-in identities. |
| `BOT_API_SECRET` | Already present. Authenticates the bot's calls. |
| `ARCADE_REDIRECT_URIS` | Optional. Comma-separated pages sign-in may return to. Defaults to `https://arcade.vicers.net/,http://localhost:5173/`. |
| `VG_ARCADE_CHANNEL_ID` | Optional. The channel for the callout and run posts. Left unset, the arcade posts nothing there. |
| `ARCADE_URL` | Optional. Where the Play buttons go. Defaults to `https://arcade.vicers.net`. |
| `ARCADE_CALLOUT_POST_HOUR` | Optional. The hour, Eastern, the callout moves back to the bottom of the channel. Defaults to `18`. |

Each redirect also has to be added under OAuth2 > Redirects for the Vice Arcade
application in the Discord Developer Portal, exactly as written, trailing slash
included.

The three Vice Arcade credentials keep their spec names rather than the plain
`BOT_TOKEN` used in the client-side scratch `.env`, because this API also talks
to Vice Verify Bot and two different tokens called `BOT_TOKEN` is a mistake
waiting to happen.

## How a season works

A season (`ArcadeSeason`, shown to players as **This Season**) is one reset
cycle. Some older code and the bot's posts still call it a round. Its length is `reset_interval_days` in Django admin: one value for every
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

## Posts in the arcade channel

The API posts these itself, as the Vice Arcade bot over Discord REST
(`arcade/announce.py`). They go out from the bot's thirty-second poll of
`events/pending`, so a run is posted within about thirty seconds.
`manage.py arcade_announce` does the same by hand, or from cron.

**The callout.** One post saying the arcade is open, with the top three on
today's board and this season's board for each game, when each resets, and a
**Play Vice Arcade** button to the cabinet (the front page). It is pinned, edited when the
standings change, and once a day at `ARCADE_CALLOUT_POST_HOUR` it is posted again
at the bottom of the channel and the old copy deleted. If staff delete it, a new
one goes up within ten minutes.

**Run posts.** Every banked run: who scored what, whether it is a personal best,
where they stand on today's board, and a **Play <game>** button to
`?play=<slug>`, which opens that game directly. Runs by one player on one game
within thirty minutes are one session and share one post, which is edited (or
moved to the bottom, when others have posted since) instead of a new post per
run. A run that waited more than fifteen minutes, because the bot was down,
is skipped rather than posted late.

The icon is the `:vicearcade:` server emoji, found by name.

**Permissions.** The Vice Arcade bot needs View Channel, Send Messages, Embed
Links and Pin Messages in the arcade channel. When something is missing, the
reason is under **Arcade callout** in admin (`last_error`). **Run posts** in admin
shows each run's post, or why it was skipped.

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

### The arcade page

Authenticates with `X-Arcade-Identity`, the signed identity issued at sign-in and
good for a week. Identity comes from Discord, never from a claim the browser
makes about itself, and Discord's access token never leaves the server.

| Method | Path | Notes |
|---|---|---|
| POST | `token` | Sign-in: exchanges the OAuth code and turns away anyone not in Vice Gamers. The only use of the client secret. |
| GET | `appearance` | Public. The uploaded background, logo, tagline and game thumbnails. |
| POST | `sessions` | Asks Discord again whether the player is in Vice Gamers, then issues a signed single-use session. Fails closed if Discord cannot be reached. |
| POST | `scores` | Banks one run. |
| GET | `leaderboards/<game_slug>` | Daily, round and all-time, plus your own rank and the reset time. |

### The bot

Authenticates with `X-Bot-Secret`, the pattern `community.views` already uses,
compared in constant time.

| Method | Path |
|---|---|
| GET | `events/pending` |
| POST | `events/<id>/ack` |
| POST | `scores/<id>/remove` |
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
and nothing more. Replaying a suspicious run server side from its seed and input
log would be the next step up and is out of scope for v1. There is no review
step: a run that passes the limits is on the boards, counts for the podium, and
is posted as a record if it is one, the moment it is banked. The owner can still
take a score off every board afterwards: in Django admin, select it under
**Scores** and run **Take the selected scores off every board**. That rebuilds
personal bests, the podium and any finished round it was in, which is why the
`removed` box itself is read-only.

`max_score_per_piece` is intentionally unset on the seeded Vetrix row. It is the
most useful of the checks and needs real numbers from a playtest; a guess that is
too tight would reject genuine runs.

## Tests

```
python manage.py test arcade
```

## Changing how the page looks

Django admin, no deploy. Changes show on the page at its next load.

- **Arcade appearance:** the page background, an optional logo shown instead
  of the VICE ARCADE heading, and the tagline under it.
- **Arcade games:** each game's lobby thumbnail, shown at 16:9 (1280x720 is
  ideal).

Every image is optional; a blank field leaves the page's built-in look in place.
Uploads are capped at 5 MB, since they are served as they are to every visitor,
and are stored under `media/arcade/`, which the web server already serves.
