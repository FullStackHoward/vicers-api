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
| `VICE_ARCADE_PUBLIC_KEY` | The Public Key from General Information in the Developer Portal. Not a secret. Checks that an `/arcade` command really came from Discord. |

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
| GET | `appearance` | Public. The cabinet background, logo and tagline, each game's thumbnail, background and logo, and `discord_channel_url` (the arcade channel, null when `VG_ARCADE_CHANNEL_ID` is unset) for the Back to Discord button. |
| POST | `sessions` | Asks Discord again whether the player is in Vice Gamers, then issues a signed single-use session. Fails closed if Discord cannot be reached. |
| POST | `scores` | Banks one run. |
| GET | `leaderboards/<game_slug>` | Daily, round and all-time, plus your own rank and the reset time. |
| GET, PUT | `me/alerts` | The player's first-place DM setting, `{"enabled": true}`. Off until they turn it on. |

### Discord

| Method | Path | Notes |
|---|---|---|
| POST | `discord/interactions` | The Interactions Endpoint URL for the `/arcade` command. Refuses anything Discord did not sign, or signed more than five minutes ago. |

### The bot

Authenticates with `X-Bot-Secret`, the pattern `community.views` already uses,
compared in constant time.

| Method | Path |
|---|---|
| GET | `events/pending` |
| POST | `events/<id>/ack` |
| POST | `scores/<id>/remove` |
| GET | `podium/current`, `standings/live`, `records/current` |
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

Neon Breaker counts bricks rather than pieces, so it has its own keys:
`max_score_per_brick` (seeded at 1400, the most a brick can pay under the game's own
scoring plus a wall bonus share; see migration 0008) and `min_ms_per_brick` (unset until
there are real runs).

Dolphin Dream counts columns passed (stat `columns_passed`) and Sunset Shatter counts
kills (stat `kills`), so they have their own keys: `max_score_per_column` and
`min_ms_per_column` (seeded at 2 and 700), and `max_score_per_kill` and `min_ms_per_kill`
(seeded at 5100 and 100). The pace limits are derived from each game's starting tuning
and must be re-derived if it changes; see migration 0009.

`max_score_per_piece` is intentionally unset on the seeded Vetrix row. It is the
most useful of the checks and needs real numbers from a playtest; a guess that is
too tight would reject genuine runs.

## The owner's /arcade command

`/arcade wipe-today`, `/arcade wipe-season` and `/arcade reset`, for the server
owner only (checked against Discord on every use, not just hidden from the list).
Each answers privately with Confirm and Cancel, and nothing happens until Confirm.

- **wipe-today** clears Today for every game. The runs stay on This Season and All Time.
- **wipe-season** clears This Season and Today, and the podium roles come off. The
  runs stay on All Time, and the season still ends when it was going to.
- **reset** deletes every score, personal best, board, season and podium result,
  starts a fresh season ending at 5 PM Eastern one interval from today, takes the
  roles off, and deletes every message in the arcade channel from before the reset.
  Players' alert settings are kept.

The wipes delete nothing: each is a `BoardWipe` row, and the board's window starts
there from then on (`leaderboards.board_start`). The reset empties the channel from
the posting tick, a little at a time, and posts nothing until it is clear; it needs
Manage Messages and Read Message History in the channel.

Setting it up, once:

1. Put the Public Key from the Developer Portal in `.env` as `VICE_ARCADE_PUBLIC_KEY`,
   `pip install -r requirements.txt` (PyNaCl checks the signatures), migrate and restart.
2. In the Developer Portal, set the Interactions Endpoint URL to
   `https://api.vicers.net/api/arcade/discord/interactions`. Discord tests it on save.
3. `python manage.py arcade_register_commands`. It also reports the old Activity's
   global `/arcade` Entry Point command; `--remove-entry-point` deletes it.

## First-place alerts

A DM when someone takes your first place on This Season or All Time, for players
who turn it on from the arcade page. One DM covers both boards, a player gets at
most one per game every 30 minutes, and an alert that could not go out within 30
minutes is dropped. Queued when the score is banked (`PlayerAlert`) and sent from
the posting tick. A player whose DMs are closed is noted in admin, not retried.

## Tests

```
python manage.py test arcade
```

## The games

Each cabinet is an **Arcade game** row. Vetrix is seeded by migration 0002, Neon
Breaker by migration 0008, and Dolphin Dream and Sunset Shatter by migration 0009. A game is on the boards and in the callout as soon as it is
active, but only counts toward the Arcade Champion standings from the first season that
starts after its `active_from`, so Neon Breaker, Dolphin Dream and Sunset Shatter join
the podium average at the next reset rather than reshuffling the season in progress.

The page has to know the game too: deploy the API (and migrate) before the page that adds
a cabinet, or starting a run on it is refused as an unknown game.

## Record roles

Each game can have an **All-time record role** (Arcade games > the game > Record role),
the Discord role id for whoever holds that game's top All Time score. `records/current`
gives the bot every game's role and holder; the bot moves the role and announces each
change in the arcade channel. A `new_alltime_record` event carries `record_role_id` so
the record post can name the role. A switched-off game reports no holder, so its role
comes off. Blank leaves the game without a role.

The podium roles are unaffected: they are average placing across games (`podium.py`).

## Changing how the page looks

Django admin, no deploy. Changes show on the page at its next load.

- **Arcade appearance:** the cabinet (lobby) background, an optional logo
  shown instead of the VICE ARCADE heading, and the tagline under it.
- **Arcade games:** each game's own look, under **Look**: its lobby thumbnail
  (16:9, 1280x720 is ideal), the background behind it while it is played, and
  the logo shown top centre above it. A game with no background uses the
  built-in backdrop, not the cabinet's; a game with no logo shows its name as a
  placeholder.

Every image is optional; a blank field leaves the page's built-in look in place.
Uploads are capped at 5 MB, since they are served as they are to every visitor,
and are stored under `media/arcade/`, which the web server already serves.
