# FND TTS Tournament Bot

A Discord bot for running **Warhammer 40,000 tabletop simulator (TTS) tournaments** — supporting both singles and 2v2 team formats with Swiss pairing, army list submission, spectator dashboards, and automated round management.

---

## Features

- **Full event lifecycle** — from announcement through registration, list review, rounds, and final standings
- **Swiss pairing** with room assignment and repeat-opponent avoidance
- **Singles & 2v2 team formats** — separate command groups for each
- **Army list submission & approval** — players submit lists via modal; TOs approve or reject them
- **Spectator dashboard** — a live-updating embed showing pairings, scores, and standings
- **Judges on Duty card** — auto-refreshes when crew members enter or leave Game Room voice channels
- **Round deadline warnings** — posts a 10-minute warning in round threads automatically
- **Batch log flushing** — bot events are queued and periodically posted to a designated log channel
- **All 40K factions, detachments, and tournament missions** — fully configured out of the box

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Discord library | discord.py 2.4.0 |
| Database | PostgreSQL (via psycopg2) |
| Hosting | Railway (Railpack) |

---

## Project Structure

```
TO-Bot-main/
├── main.py                  # Entry point — registers commands, starts background tasks, runs bot
├── config.py                # Environment config, all Warhammer data, colour/timestamp helpers
├── database.py              # PostgreSQL schema, init, and all db_* query functions
├── state.py                 # In-memory state caches (events, rounds, games, teams, etc.)
├── threads.py               # Discord thread management and Swiss pairing logic
├── services.py              # Dashboard refresh, autocomplete helpers, logging
├── embeds.py                # All discord.Embed builders
├── views.py                 # discord.ui Views and Modals
├── commands_event.py        # /event and /reg slash command groups
├── commands_round.py        # /round, /result, /standings, /event-finish slash commands
├── commands_round_teams.py  # /result-team, /team-standings slash commands
├── commands_teams.py        # /team slash command group
├── ritual.py                # /roll-dice command
├── ritual_8s.py             # Eight-sided ritual dice helper
├── requirements.txt         # Python dependencies
└── runtime.txt              # Python version pin (3.11.9)
```

---

## Setup

### Prerequisites

- Python 3.11+
- A PostgreSQL database
- A Discord application with a bot token and slash command permissions

### Environment Variables

Set the following in your Railway dashboard (or `.env` locally):

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Your bot token |
| `GUILD` | ✅ | Discord server (guild) ID |
| `DATABASE_URL` | ✅ | PostgreSQL connection string |
| `EVENT_NOTICEBOARD` | ✗ | Channel ID for event announcements |
| `WHATS_PLAYING` | ✗ | Channel ID for "what's playing" posts |
| `ANNOUNCEMENTS_CHANNEL` | ✗ | Channel ID for round announcements |
| `BOT_LOGS_CHANNEL` | ✗ | Channel ID for bot log output |
| `CREW_ROLE` | ✗ | Role ID for Tournament Organiser crew |
| `PLAYER_ROLE` | ✗ | Role ID assigned to registered players |
| `CAPTAINS_ROLE` | ✗ | Role ID for team captains |
| `LOG_BATCH_MINUTES` | ✗ | Log flush interval in minutes (default: 15) |

> **Note:** Variable names intentionally omit the `_ID` suffix to avoid a Railpack 0.17.2 secret-detection bug.

### Installation

```bash
pip install -r requirements.txt
python main.py
```

On first run, `init_db()` will create all required PostgreSQL tables automatically.

---

## Slash Commands

### Event Management (`/event` — TO only)

| Command | Description |
|---|---|
| `/event create` | Create a new tournament event |
| `/event open-interest` | Open the event for interest sign-ups |
| `/event open-registration` | Open player registration |
| `/event lock-lists` | Lock army list submissions |
| `/event start` | Start the event |

### Player Registration (`/reg`)

| Command | Description |
|---|---|
| `/reg submit` | Submit your army list for an event |
| `/reg drop` | Drop from an event |
| `/reg list` | View registered players |

### Round Management (`/round` — TO only)

| Command | Description |
|---|---|
| `/round briefing` | Post the day briefing and ping all players |
| `/round start` | Generate Swiss pairings and start a round |
| `/round complete` | Mark a round as complete |
| `/round repair` | Repair/re-run pairing for a round |

### Results (`/result` — TO only)

| Command | Description |
|---|---|
| `/result override` | Override a game result |
| `/result adjust` | Adjust VP for a player |

### Top-Level Commands

| Command | Description |
|---|---|
| `/event-finish` | Finalise the event and post final standings |
| `/standings` | Display current standings |
| `/my-list` | View your submitted army list |
| `/roll-dice` | Roll ritual dice |

### Team Commands (`/team`)

| Command | Description |
|---|---|
| `/team register` | Register a new team |
| `/team invite` | Invite a player to your team |
| `/team kick` | Kick a player from your team |
| `/team submit-list` | Submit an army list for a team slot |
| `/team substitute` | Substitute a player |
| `/team drop` | Drop a team from the event |
| `/team drop-player` | Drop an individual player from a team |
| `/team info` | View team details |
| `/team list` | List all teams |
| `/team approve-list` | Approve a team's army list (TO only) |

---

## Background Tasks

The bot runs three background loops automatically:

- **`flush_batch_logs`** — flushes queued log messages to the bot logs channel (default every 15 minutes)
- **`check_round_deadlines`** — checks every minute for rounds approaching their deadline and posts a 10-minute warning
- **`refresh_dashboards`** — refreshes the spectator dashboard, standings card, and Judges on Duty embed every 5 minutes

---

## Warhammer Configuration

`config.py` ships with complete data for:

- All 29 Warhammer 40K factions with emoji and colour theming
- All detachments per faction
- 20 standard tournament missions (A–T) with deployment zones and terrain layout codes
- 10 colour-coded game rooms

---

## Database

The PostgreSQL schema is created automatically on first run and includes tables for: events, registrations, rounds, games, judge calls, standings messages, teams, team members, team rounds, team pairings, and pairing state.

---

## Deployment on Railway

1. Create a new Railway project and link this repository.
2. Add a PostgreSQL plugin.
3. Set all required environment variables in the Railway dashboard.
4. Railway will use `runtime.txt` to pin Python 3.11.9 and `requirements.txt` to install dependencies.
5. Set the start command to `python main.py`.
