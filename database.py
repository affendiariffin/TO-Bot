"""
database.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PostgreSQL connection, schema init (init_db()), and all db_* /
scorebot_* helper functions.

Sections:
  • get_conn() / init_db()
  • Events
  • Registrations
  • Rounds & Games
  • Judge calls
  • Standings
  • Log queue
  • Teams, team members, team rounds, team pairings, pairing state
  • Scorebot integration
  • Standings_msg_id column

Imported by: threads.py, views.py, services.py, commands_*.py, ritual.py
"""
import psycopg2, psycopg2.extras
import uuid
import json as _json
from datetime import datetime, date, timezone
from typing import List, Optional, Dict
from config import DATABASE_URL
from state import ES, RndS, GS, JCS, TS, TRS, PS  # FIX: added missing import (NameError at runtime without this)

def get_conn():
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def _parse_event_row(r) -> dict:
    """Deserialise JSON list columns on event rows."""
    d = dict(r)
    for col in ("event_layouts", "event_missions", "event_pairings"):
        raw = d.get(col)
        if isinstance(raw, str):
            try:
                d[col] = _json.loads(raw)
            except Exception:
                d[col] = []
        elif raw is None:
            d[col] = []
    return d

def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            # ── Events ──────────────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_events (
                    event_id             TEXT PRIMARY KEY,
                    name                 TEXT NOT NULL,
                    state                TEXT NOT NULL DEFAULT 'announced',
                    points_limit         INTEGER NOT NULL,
                    mission_code         TEXT NOT NULL,
                    terrain_layout       TEXT,
                    max_players          INTEGER NOT NULL DEFAULT 16,
                    rounds_per_day       INTEGER NOT NULL DEFAULT 3,
                    start_date           DATE NOT NULL,
                    end_date             DATE NOT NULL,
                    discord_event_id     TEXT,
                    noticeboard_msg_id   TEXT,
                    spectator_msg_id     TEXT,
                    submissions_thread_id TEXT,
                    lists_thread_id      TEXT,
                    created_by           TEXT NOT NULL,
                    created_at           TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)
            # Add thread columns if upgrading from v2
            for col in ("submissions_thread_id", "lists_thread_id"):
                cur.execute(f"""
                    ALTER TABLE tournament_events ADD COLUMN IF NOT EXISTS {col} TEXT
                """)
            # ── Registrations ────────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_registrations (
                    reg_id           TEXT PRIMARY KEY,
                    event_id         TEXT NOT NULL REFERENCES tournament_events(event_id),
                    player_id        TEXT NOT NULL,
                    player_username  TEXT NOT NULL,
                    army             TEXT NOT NULL DEFAULT 'Unknown',
                    detachment       TEXT NOT NULL DEFAULT 'Unknown',
                    list_text        TEXT,
                    state            TEXT NOT NULL DEFAULT 'interested',
                    rejection_reason TEXT,
                    submitted_at     TIMESTAMP NOT NULL DEFAULT NOW(),
                    approved_at      TIMESTAMP,
                    dropped_at       TIMESTAMP,
                    UNIQUE(event_id, player_id)
                )
            """)
            # ── Rounds ───────────────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_rounds (
                    round_id         TEXT PRIMARY KEY,
                    event_id         TEXT NOT NULL REFERENCES tournament_events(event_id),
                    round_number     INTEGER NOT NULL,
                    day_number       INTEGER NOT NULL DEFAULT 1,
                    state            TEXT NOT NULL DEFAULT 'pending',
                    started_at       TIMESTAMP,
                    deadline_at      TIMESTAMP,
                    completed_at     TIMESTAMP,
                    clock_paused     BOOLEAN NOT NULL DEFAULT FALSE,
                    pause_started_at TIMESTAMP,
                    extra_seconds    INTEGER NOT NULL DEFAULT 0,
                    pairings_msg_id  TEXT,
                    round_thread_id  TEXT,
                    UNIQUE(event_id, round_number)
                )
            """)
            cur.execute("ALTER TABLE tournament_rounds ADD COLUMN IF NOT EXISTS round_thread_id TEXT")
            # ── Games (pairings) ─────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_games (
                    game_id              TEXT PRIMARY KEY,
                    round_id             TEXT NOT NULL REFERENCES tournament_rounds(round_id),
                    event_id             TEXT NOT NULL,
                    room_number          INTEGER,
                    player1_id           TEXT NOT NULL,
                    player1_username     TEXT NOT NULL,
                    player1_army         TEXT NOT NULL,
                    player1_detachment   TEXT NOT NULL,
                    player2_id           TEXT,
                    player2_username     TEXT,
                    player2_army         TEXT,
                    player2_detachment   TEXT,
                    is_bye               BOOLEAN NOT NULL DEFAULT FALSE,
                    player1_vp           INTEGER,
                    player2_vp           INTEGER,
                    winner_id            TEXT,
                    state                TEXT NOT NULL DEFAULT 'pending',
                    result_msg_id        TEXT,
                    result_channel_id    TEXT,
                    submitted_at         TIMESTAMP,
                    confirmed_at         TIMESTAMP,
                    adj_note             TEXT
                )
            """)
            # ── Judge calls ───────────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_judge_calls (
                    call_id              TEXT PRIMARY KEY,
                    event_id             TEXT NOT NULL,
                    round_id             TEXT NOT NULL,
                    game_id              TEXT,
                    raised_by_id         TEXT NOT NULL,
                    raised_by_name       TEXT NOT NULL,
                    room_number          INTEGER,
                    state                TEXT NOT NULL DEFAULT 'open',
                    raised_at            TIMESTAMP NOT NULL DEFAULT NOW(),
                    acknowledged_at      TIMESTAMP,
                    acknowledged_by_id   TEXT,
                    acknowledged_by_name TEXT,
                    closed_at            TIMESTAMP,
                    closed_by_id         TEXT,
                    closed_by_name       TEXT,
                    vp_adjustment        TEXT
                )
            """)
            # Add new columns if upgrading from v2
            for col in ("acknowledged_by_id TEXT", "acknowledged_by_name TEXT",
                        "closed_by_id TEXT", "closed_by_name TEXT"):
                cur.execute(f"ALTER TABLE tournament_judge_calls ADD COLUMN IF NOT EXISTS {col}")
            # ── Standings (live cache) ────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_standings (
                    standing_id      TEXT PRIMARY KEY,
                    event_id         TEXT NOT NULL,
                    player_id        TEXT NOT NULL,
                    player_username  TEXT NOT NULL,
                    army             TEXT NOT NULL,
                    detachment       TEXT NOT NULL,
                    wins             INTEGER NOT NULL DEFAULT 0,
                    losses           INTEGER NOT NULL DEFAULT 0,
                    draws            INTEGER NOT NULL DEFAULT 0,
                    vp_total         INTEGER NOT NULL DEFAULT 0,
                    vp_against       INTEGER NOT NULL DEFAULT 0,
                    vp_diff          INTEGER NOT NULL DEFAULT 0,
                    had_bye          BOOLEAN NOT NULL DEFAULT FALSE,
                    active           BOOLEAN NOT NULL DEFAULT TRUE,
                    UNIQUE(event_id, player_id)
                )
            """)
            # ── Batch log queue ───────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_log_queue (
                    log_id      SERIAL PRIMARY KEY,
                    event_id    TEXT,
                    level       TEXT NOT NULL DEFAULT 'info',
                    message     TEXT NOT NULL,
                    logged_at   TIMESTAMP NOT NULL DEFAULT NOW(),
                    flushed     BOOLEAN NOT NULL DEFAULT FALSE
                )
            """)

            # ── v4 additions: format columns on existing tables ───────────────
            for col_def in (
                "format TEXT DEFAULT 'singles'",
                "team_size INTEGER DEFAULT 1",
                "individual_points INTEGER DEFAULT 2000",
                "captains_thread_id TEXT",
                "pairing_room_thread_id TEXT",
                "standings_msg_id TEXT",   # FIX: added missing column used by refresh_standings_card
                # round_count: 3 or 5 — set explicitly by TO at creation, stored here as the source of truth
                "round_count INTEGER DEFAULT 3",
                # singles/2v2: ordered list of {layout, mission} combos for all rounds of the event
                # validated at creation — no duplicates, no illegal combos
                "event_pairings TEXT",
                # team formats: pool of layouts/missions captains pick from during each ritual
                "event_layouts TEXT",
                "event_missions TEXT",
                "rules_cutoff TEXT",
                "reg_deadline TEXT",
                # WTC: scoring mode for team events ('ntl' or 'wtc')
                "scoring_mode TEXT DEFAULT 'ntl'",
            ):
                cur.execute(f"ALTER TABLE tournament_events ADD COLUMN IF NOT EXISTS {col_def}")
            cur.execute(
                "ALTER TABLE tournament_registrations ADD COLUMN IF NOT EXISTS chop_thread_id TEXT"
            )

            for col_def in (
                "team_id TEXT",
                "team_wins INTEGER DEFAULT 0",
                "team_losses INTEGER DEFAULT 0",
                "team_draws INTEGER DEFAULT 0",
                "team_points INTEGER DEFAULT 0",
                "game_points INTEGER DEFAULT 0",
                # WTC: per-player WTC GP accumulated across rounds (individual standings)
                "wtc_gp INTEGER DEFAULT 0",
            ):
                cur.execute(f"ALTER TABLE tournament_standings ADD COLUMN IF NOT EXISTS {col_def}")

            # WTC: per-game WTC GP values stored on the game record
            for col_def in (
                "player1_wtc_gp INTEGER",
                "player2_wtc_gp INTEGER",
            ):
                cur.execute(f"ALTER TABLE tournament_games ADD COLUMN IF NOT EXISTS {col_def}")

            cur.execute("ALTER TABLE tournament_rounds ADD COLUMN IF NOT EXISTS active_team_round_id TEXT")

            # ── Teams ─────────────────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_teams (
                    team_id          TEXT PRIMARY KEY,
                    event_id         TEXT NOT NULL REFERENCES tournament_events(event_id),
                    team_name        TEXT NOT NULL,
                    captain_id       TEXT NOT NULL,
                    captain_username TEXT NOT NULL,
                    state            TEXT NOT NULL DEFAULT 'forming',
                    created_at       TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)

            # ── Team members ──────────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_team_members (
                    member_id        TEXT PRIMARY KEY,
                    team_id          TEXT NOT NULL REFERENCES tournament_teams(team_id),
                    event_id         TEXT NOT NULL,
                    player_id        TEXT NOT NULL,
                    player_username  TEXT NOT NULL,
                    role             TEXT NOT NULL DEFAULT 'player',
                    army             TEXT,
                    detachment       TEXT,
                    list_text        TEXT,
                    list_approved    BOOLEAN NOT NULL DEFAULT FALSE,
                    joined_at        TIMESTAMP NOT NULL DEFAULT NOW(),
                    dropped_at       TIMESTAMP,
                    active           BOOLEAN NOT NULL DEFAULT TRUE,
                    UNIQUE(team_id, player_id)
                )
            """)

            # ── Team rounds ───────────────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_team_rounds (
                    team_round_id     TEXT PRIMARY KEY,
                    round_id          TEXT NOT NULL REFERENCES tournament_rounds(round_id),
                    event_id          TEXT NOT NULL,
                    team_a_id         TEXT NOT NULL REFERENCES tournament_teams(team_id),
                    team_b_id         TEXT,
                    state             TEXT NOT NULL DEFAULT 'pairing',
                    team_a_score      INTEGER DEFAULT 0,
                    team_b_score      INTEGER DEFAULT 0,
                    team_a_win        BOOLEAN,
                    pairing_phase     INTEGER DEFAULT 0,
                    pairing_thread_id TEXT,
                    layout_picker     TEXT,
                    created_at        TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)

            # ── Team pairings (individual game slots within a team round) ─────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_team_pairings (
                    pairing_id           TEXT PRIMARY KEY,
                    team_round_id        TEXT NOT NULL REFERENCES tournament_team_rounds(team_round_id),
                    game_id              TEXT REFERENCES tournament_games(game_id),
                    pairing_slot         INTEGER NOT NULL,
                    defender_player_id   TEXT,
                    defender_team_id     TEXT,
                    attacker_player_id   TEXT,
                    attacker_team_id     TEXT,
                    refused_player_id    TEXT,
                    layout_number        INTEGER,
                    mission_code         TEXT,
                    layout_picker_team   TEXT,
                    mission_picker_team  TEXT,
                    created_at           TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)

            # ── Pairing ritual state machine ──────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_pairing_state (
                    state_id         TEXT PRIMARY KEY,
                    team_round_id    TEXT NOT NULL UNIQUE REFERENCES tournament_team_rounds(team_round_id),
                    current_phase    INTEGER NOT NULL DEFAULT 1,
                    current_step     TEXT NOT NULL DEFAULT 'await_rolloff',
                    defender_a       TEXT,
                    defender_b       TEXT,
                    attackers_a      TEXT[],
                    attackers_b      TEXT[],
                    choice_a         TEXT,
                    choice_b         TEXT,
                    scrum_a          TEXT,
                    scrum_b          TEXT,
                    updated_at       TIMESTAMP NOT NULL DEFAULT NOW()
                )
            """)

            # ── Missions & layouts ───────────────────────────────────────────
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_missions (
                    code         TEXT PRIMARY KEY,
                    name         TEXT NOT NULL,
                    deployment   TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS tournament_layouts (
                    id           SERIAL PRIMARY KEY,
                    mission_code TEXT NOT NULL REFERENCES tournament_missions(code) ON DELETE CASCADE,
                    layout       TEXT NOT NULL,
                    UNIQUE (mission_code, layout)
                )
            """)

            # Seed default missions (idempotent — skips existing rows)
            _MISSION_SEED = [
                ("A", "Take and Hold",   "Tipping Point",       ["1","2","4","6","7","8"]),
                ("B", "Supply Drop",     "Tipping Point",       ["1","2","4","6","7","8"]),
                ("C", "Linchpin",        "Tipping Point",       ["1","2","4","6","7","8"]),
                ("D", "Scorched Earth",  "Tipping Point",       ["1","2","4","6","7","8"]),
                ("E", "Take and Hold",   "Hammer and Anvil",    ["1","7","8"]),
                ("F", "Hidden Supplies", "Hammer and Anvil",    ["1","7","8"]),
                ("G", "Purge the Foe",   "Hammer and Anvil",    ["1","7","8"]),
                ("H", "Supply Drop",     "Hammer and Anvil",    ["1","7","8"]),
                ("I", "Hidden Supplies", "Search and Destroy",  ["1","2","3","4","6"]),
                ("J", "Linchpin",        "Search and Destroy",  ["1","2","3","4","6"]),
                ("K", "Scorched Earth",  "Search and Destroy",  ["1","2","3","4","6"]),
                ("L", "Take and Hold",   "Search and Destroy",  ["1","2","3","4","6"]),
                ("M", "Purge the Foe",   "Crucible of Battle",  ["1","2","4","6","8"]),
                ("N", "Hidden Supplies", "Crucible of Battle",  ["1","2","4","6","8"]),
                ("O", "Terraform",       "Crucible of Battle",  ["1","2","4","6","8"]),
                ("P", "Scorched Earth",  "Crucible of Battle",  ["1","2","4","6","8"]),
                ("Q", "Supply Drop",     "Sweeping Engagement", ["3","5"]),
                ("R", "Terraform",       "Sweeping Engagement", ["3","5"]),
                ("S", "Linchpin",        "Dawn of War",         ["5"]),
                ("T", "Purge the Foe",   "Dawn of War",         ["5"]),
            ]
            for code, name, deployment, layouts in _MISSION_SEED:
                cur.execute("""
                    INSERT INTO tournament_missions (code, name, deployment)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (code) DO NOTHING
                """, (code, name, deployment))
                for layout in layouts:
                    cur.execute("""
                        INSERT INTO tournament_layouts (mission_code, layout)
                        VALUES (%s, %s)
                        ON CONFLICT (mission_code, layout) DO NOTHING
                    """, (code, layout))

            conn.commit()
    print("✅ Tournament DB ready")  # FIX: corrected indentation (was 2 spaces, causing IndentationError)

# ══════════════════════════════════════════════════════════════════════════════
# DATABASE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

# ── Events ────────────────────────────────────────────────────────────────────

def db_create_event(d: dict) -> str:
    eid = f"evt_{uuid.uuid4().hex[:8]}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_events
                    (event_id,name,state,points_limit,mission_code,terrain_layout,
                     max_players,round_count,rounds_per_day,start_date,end_date,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (eid, d["name"], ES.ANNOUNCED, d["points_limit"], d["mission_code"],
                  d.get("terrain_layout"), d["max_players"], d.get("round_count", 3),
                  d.get("rounds_per_day", 3), d["start_date"], d["end_date"], d["created_by"]))
            conn.commit()
    return eid

def db_get_event(eid: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_events WHERE event_id=%s", (eid,))
            r = cur.fetchone()
    return _parse_event_row(r) if r else None

def db_get_active_events() -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_events WHERE state!='complete' ORDER BY start_date")
            return [_parse_event_row(r) for r in cur.fetchall()]

def db_get_all_events() -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_events ORDER BY start_date DESC LIMIT 25")
            return [_parse_event_row(r) for r in cur.fetchall()]

def db_update_event(eid: str, updates: dict):
    if not updates: return
    serialised = dict(updates)
    for col in ("event_layouts", "event_missions", "event_pairings"):
        if col in serialised and isinstance(serialised[col], list):
            serialised[col] = _json.dumps(serialised[col])
    fields = ", ".join(f"{k}=%s" for k in serialised)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tournament_events SET {fields} WHERE event_id=%s",
                        list(serialised.values()) + [eid])
            conn.commit()

# ── Registrations ─────────────────────────────────────────────────────────────

def db_upsert_registration(eid: str, pid: str, username: str, state: str,
                             army="Unknown", det="Unknown", list_text=None) -> str:
    rid = f"reg_{uuid.uuid4().hex[:8]}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_registrations
                    (reg_id,event_id,player_id,player_username,army,detachment,list_text,state)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (event_id,player_id) DO UPDATE SET
                    player_username = EXCLUDED.player_username,
                    army       = CASE WHEN EXCLUDED.army!='Unknown' THEN EXCLUDED.army
                                      ELSE tournament_registrations.army END,
                    detachment = CASE WHEN EXCLUDED.detachment!='Unknown' THEN EXCLUDED.detachment
                                      ELSE tournament_registrations.detachment END,
                    list_text  = CASE WHEN EXCLUDED.list_text IS NOT NULL THEN EXCLUDED.list_text
                                      ELSE tournament_registrations.list_text END,
                    state      = CASE WHEN EXCLUDED.state IN ('pending','approved','rejected','dropped')
                                      THEN EXCLUDED.state
                                      ELSE tournament_registrations.state END
                RETURNING reg_id
            """, (rid, eid, pid, username, army, det, list_text, state))
            row = cur.fetchone()
            conn.commit()
    return row[0] if row else rid

def db_get_registration(eid: str, pid: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_registrations WHERE event_id=%s AND player_id=%s", (eid, pid))
            r = cur.fetchone()
    return dict(r) if r else None

def db_get_registrations(eid: str, state: str = None) -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if state:
                cur.execute("SELECT * FROM tournament_registrations WHERE event_id=%s AND state=%s ORDER BY submitted_at", (eid, state))
            else:
                cur.execute("SELECT * FROM tournament_registrations WHERE event_id=%s ORDER BY submitted_at", (eid,))
            return [dict(r) for r in cur.fetchall()]

def db_update_registration(eid: str, pid: str, updates: dict):
    if not updates: return
    fields = ", ".join(f"{k}=%s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tournament_registrations SET {fields} WHERE event_id=%s AND player_id=%s",
                        list(updates.values()) + [eid, pid])
            conn.commit()

# ── Rounds ────────────────────────────────────────────────────────────────────

def db_create_round(eid: str, round_num: int, day_num: int, deadline: datetime) -> str:
    rid = f"rnd_{uuid.uuid4().hex[:8]}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_rounds (round_id,event_id,round_number,day_number,state,deadline_at)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (rid, eid, round_num, day_num, RndS.PENDING, deadline))
            conn.commit()
    return rid

def db_get_round(rid: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_rounds WHERE round_id=%s", (rid,))
            r = cur.fetchone()
    return dict(r) if r else None

def db_get_current_round(eid: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT * FROM tournament_rounds WHERE event_id=%s
                           AND state IN ('pending','in_progress') ORDER BY round_number LIMIT 1""", (eid,))
            r = cur.fetchone()
    return dict(r) if r else None

def db_get_rounds(eid: str) -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_rounds WHERE event_id=%s ORDER BY round_number", (eid,))
            return [dict(r) for r in cur.fetchall()]

def db_update_round(rid: str, updates: dict):
    if not updates: return
    fields = ", ".join(f"{k}=%s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tournament_rounds SET {fields} WHERE round_id=%s",
                        list(updates.values()) + [rid])
            conn.commit()

# ── Games ─────────────────────────────────────────────────────────────────────

def db_create_game(d: dict) -> str:
    gid = f"g_{uuid.uuid4().hex[:10]}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_games
                    (game_id,round_id,event_id,room_number,
                     player1_id,player1_username,player1_army,player1_detachment,
                     player2_id,player2_username,player2_army,player2_detachment,
                     is_bye,state)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (gid, d["round_id"], d["event_id"], d.get("room_number"),
                  d["player1_id"], d["player1_username"], d["player1_army"], d["player1_detachment"],
                  d.get("player2_id"), d.get("player2_username"), d.get("player2_army"), d.get("player2_detachment"),
                  d.get("is_bye", False), GS.BYE if d.get("is_bye") else GS.PENDING))
            conn.commit()
    return gid

def db_get_game(gid: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_games WHERE game_id=%s", (gid,))
            r = cur.fetchone()
    return dict(r) if r else None

def db_get_games(round_id: str) -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_games WHERE round_id=%s ORDER BY room_number NULLS LAST", (round_id,))
            return [dict(r) for r in cur.fetchall()]

def db_get_event_games(eid: str) -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_games WHERE event_id=%s ORDER BY game_id", (eid,))
            return [dict(r) for r in cur.fetchall()]

def db_update_game(gid: str, updates: dict):
    if not updates: return
    fields = ", ".join(f"{k}=%s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tournament_games SET {fields} WHERE game_id=%s",
                        list(updates.values()) + [gid])
            conn.commit()

def db_delete_games_for_round(round_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tournament_games WHERE round_id=%s AND state='pending'", (round_id,))
            conn.commit()

# ── Standings ─────────────────────────────────────────────────────────────────

def db_upsert_standing(eid: str, pid: str, username: str, army: str, det: str):
    sid = f"std_{eid[:8]}_{pid}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_standings (standing_id,event_id,player_id,player_username,army,detachment)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (event_id,player_id) DO NOTHING
            """, (sid, eid, pid, username, army, det))
            conn.commit()

def db_get_standings(eid: str, active_only=True) -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if active_only:
                cur.execute("""SELECT * FROM tournament_standings WHERE event_id=%s AND active=TRUE
                               ORDER BY wins DESC, vp_diff DESC, vp_total DESC""", (eid,))
            else:
                cur.execute("""SELECT * FROM tournament_standings WHERE event_id=%s
                               ORDER BY wins DESC, vp_diff DESC, vp_total DESC""", (eid,))
            return [dict(r) for r in cur.fetchall()]

def db_update_standing(eid: str, pid: str, updates: dict):
    if not updates: return
    fields = ", ".join(f"{k}=%s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tournament_standings SET {fields} WHERE event_id=%s AND player_id=%s",
                        list(updates.values()) + [eid, pid])
            conn.commit()

def db_apply_wtc_gp_to_standing(eid: str, player_id: str, wtc_gp_delta: int):
    """Accumulate WTC GP for an individual player's standing row (WTC-mode team events)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE tournament_standings
                SET wtc_gp = COALESCE(wtc_gp, 0) + %s
                WHERE event_id=%s AND player_id=%s
            """, (wtc_gp_delta, eid, player_id))
            conn.commit()

def db_get_team_round_by_game(game_id: str) -> Optional[dict]:
    """Find the team_round that contains a given game via tournament_team_pairings."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT tr.* FROM tournament_team_rounds tr
                JOIN tournament_team_pairings tp ON tp.team_round_id = tr.team_round_id
                WHERE tp.game_id = %s
                LIMIT 1
            """, (game_id,))
            r = cur.fetchone()
    return dict(r) if r else None

def db_get_team_id_for_player_in_round(game_id: str, player_id: str) -> Optional[str]:
    """Return the team_id for a given player within a specific game's pairing."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT defender_team_id, attacker_team_id,
                       defender_player_id, attacker_player_id
                FROM tournament_team_pairings
                WHERE game_id = %s
                LIMIT 1
            """, (game_id,))
            r = cur.fetchone()
    if not r:
        return None
    if r[2] == player_id:
        return r[0]  # defender_team_id
    if r[3] == player_id:
        return r[1]  # attacker_team_id
    return None

def db_accumulate_wtc_team_score(team_round_id: str, team_a_id: str,
                                  team_a_delta: int, team_b_delta: int):
    """Add WTC GP increments to both teams' running totals in a team_round."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE tournament_team_rounds
                SET team_a_score = COALESCE(team_a_score, 0) + %s,
                    team_b_score = COALESCE(team_b_score, 0) + %s
                WHERE team_round_id = %s AND team_a_id = %s
            """, (team_a_delta, team_b_delta, team_round_id, team_a_id))
            # Also handle the case where caller passed teams in reversed order
            cur.execute("""
                UPDATE tournament_team_rounds
                SET team_a_score = COALESCE(team_a_score, 0) + %s,
                    team_b_score = COALESCE(team_b_score, 0) + %s
                WHERE team_round_id = %s AND team_a_id != %s
                  AND team_b_id = %s
            """, (team_b_delta, team_a_delta, team_round_id, team_a_id, team_a_id))
            conn.commit()

def db_apply_result_to_standings(eid: str, winner_id: str, loser_id: str, winner_vp: int, loser_vp: int):
    # FIX: detect draws (equal VP) and increment draws column instead of wins/losses
    is_draw = (winner_vp == loser_vp)
    with get_conn() as conn:
        with conn.cursor() as cur:
            if winner_id and winner_id != "bye":
                if is_draw:
                    cur.execute("""UPDATE tournament_standings SET
                        draws=draws+1, vp_total=vp_total+%s, vp_against=vp_against+%s, vp_diff=vp_diff+%s
                        WHERE event_id=%s AND player_id=%s""",
                        (winner_vp, loser_vp, 0, eid, winner_id))
                else:
                    cur.execute("""UPDATE tournament_standings SET
                        wins=wins+1, vp_total=vp_total+%s, vp_against=vp_against+%s, vp_diff=vp_diff+%s
                        WHERE event_id=%s AND player_id=%s""",
                        (winner_vp, loser_vp, winner_vp - loser_vp, eid, winner_id))
            if loser_id and loser_id != "bye":
                if is_draw:
                    cur.execute("""UPDATE tournament_standings SET
                        draws=draws+1, vp_total=vp_total+%s, vp_against=vp_against+%s, vp_diff=vp_diff+%s
                        WHERE event_id=%s AND player_id=%s""",
                        (loser_vp, winner_vp, 0, eid, loser_id))
                else:
                    cur.execute("""UPDATE tournament_standings SET
                        losses=losses+1, vp_total=vp_total+%s, vp_against=vp_against+%s, vp_diff=vp_diff+%s
                        WHERE event_id=%s AND player_id=%s""",
                        (loser_vp, winner_vp, loser_vp - winner_vp, eid, loser_id))
            conn.commit()

def db_reverse_result_from_standings(eid: str, winner_id: str, loser_id: str, winner_vp: int, loser_vp: int):
    """Undo a previously applied result (used by adjust command)."""
    # FIX: detect draws so we reverse the correct column (draws vs wins/losses)
    is_draw = (winner_vp == loser_vp)
    with get_conn() as conn:
        with conn.cursor() as cur:
            if winner_id and winner_id != "bye":
                if is_draw:
                    cur.execute("""UPDATE tournament_standings SET
                        draws=GREATEST(0,draws-1), vp_total=GREATEST(0,vp_total-%s),
                        vp_against=GREATEST(0,vp_against-%s), vp_diff=vp_diff-%s
                        WHERE event_id=%s AND player_id=%s""",
                        (winner_vp, loser_vp, 0, eid, winner_id))
                else:
                    cur.execute("""UPDATE tournament_standings SET
                        wins=GREATEST(0,wins-1), vp_total=GREATEST(0,vp_total-%s),
                        vp_against=GREATEST(0,vp_against-%s), vp_diff=vp_diff-%s
                        WHERE event_id=%s AND player_id=%s""",
                        (winner_vp, loser_vp, winner_vp - loser_vp, eid, winner_id))
            if loser_id and loser_id != "bye":
                if is_draw:
                    cur.execute("""UPDATE tournament_standings SET
                        draws=GREATEST(0,draws-1), vp_total=GREATEST(0,vp_total-%s),
                        vp_against=GREATEST(0,vp_against-%s), vp_diff=vp_diff-%s
                        WHERE event_id=%s AND player_id=%s""",
                        (loser_vp, winner_vp, 0, eid, loser_id))
                else:
                    cur.execute("""UPDATE tournament_standings SET
                        losses=GREATEST(0,losses-1), vp_total=GREATEST(0,vp_total-%s),
                        vp_against=GREATEST(0,vp_against-%s), vp_diff=vp_diff-%s
                        WHERE event_id=%s AND player_id=%s""",
                        (loser_vp, winner_vp, loser_vp - winner_vp, eid, loser_id))
            conn.commit()

# ── Judge calls ───────────────────────────────────────────────────────────────

def db_create_judge_call(eid: str, round_id: str, game_id: Optional[str],
                          raised_by_id: str, raised_by_name: str, room: int) -> str:
    cid = f"jdg_{uuid.uuid4().hex[:8]}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_judge_calls
                    (call_id,event_id,round_id,game_id,raised_by_id,raised_by_name,room_number,state)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (cid, eid, round_id, game_id, raised_by_id, raised_by_name, room, JCS.OPEN))
            conn.commit()
    return cid

def db_get_open_calls(eid: str) -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT * FROM tournament_judge_calls
                           WHERE event_id=%s AND state IN ('open','acknowledged')
                           ORDER BY raised_at""", (eid,))
            return [dict(r) for r in cur.fetchall()]

def db_update_judge_call(cid: str, updates: dict):
    if not updates: return
    fields = ", ".join(f"{k}=%s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tournament_judge_calls SET {fields} WHERE call_id=%s",
                        list(updates.values()) + [cid])
            conn.commit()

# ── Logging ───────────────────────────────────────────────────────────────────

def db_queue_log(msg: str, eid: str = None, level: str = "info"):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO tournament_log_queue (event_id,level,message) VALUES (%s,%s,%s)",
                        (eid, level, msg))
            conn.commit()

def db_flush_logs() -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_log_queue WHERE flushed=FALSE ORDER BY logged_at")
            rows = [dict(r) for r in cur.fetchall()]
            if rows:
                ids = [r["log_id"] for r in rows]
                cur.execute("UPDATE tournament_log_queue SET flushed=TRUE WHERE log_id=ANY(%s)", (ids,))
            conn.commit()
    return rows

# ── Teams ─────────────────────────────────────────────────────────────────────

def db_create_team(eid: str, team_name: str, captain_id: str, captain_username: str) -> str:
    tid = f"team_{uuid.uuid4().hex[:8]}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_teams
                    (team_id, event_id, team_name, captain_id, captain_username, state)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (tid, eid, team_name, captain_id, captain_username, TS.FORMING))
            conn.commit()
    return tid

def db_get_team(team_id: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_teams WHERE team_id=%s", (team_id,))
            r = cur.fetchone()
    return dict(r) if r else None

def db_get_teams(eid: str, state: str = None) -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if state:
                cur.execute("SELECT * FROM tournament_teams WHERE event_id=%s AND state=%s ORDER BY created_at", (eid, state))
            else:
                cur.execute("SELECT * FROM tournament_teams WHERE event_id=%s ORDER BY created_at", (eid,))
            return [dict(r) for r in cur.fetchall()]

def db_get_team_by_captain(eid: str, captain_id: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_teams WHERE event_id=%s AND captain_id=%s AND state!='dropped'", (eid, captain_id))
            r = cur.fetchone()
    return dict(r) if r else None

def db_get_team_by_player(eid: str, player_id: str) -> Optional[dict]:
    """Find a player's team in an event (via team_members)."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT t.* FROM tournament_teams t
                JOIN tournament_team_members m ON t.team_id = m.team_id
                WHERE t.event_id=%s AND m.player_id=%s AND m.active=TRUE AND t.state!='dropped'
            """, (eid, player_id))
            r = cur.fetchone()
    return dict(r) if r else None

def db_update_team(team_id: str, updates: dict):
    if not updates: return
    fields = ", ".join(f"{k}=%s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tournament_teams SET {fields} WHERE team_id=%s",
                        list(updates.values()) + [team_id])
            conn.commit()

# ── Team members ──────────────────────────────────────────────────────────────

def db_add_team_member(team_id: str, eid: str, player_id: str, player_username: str,
                        role: str = "player", army: str = None, detachment: str = None) -> str:
    mid = f"tm_{uuid.uuid4().hex[:8]}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_team_members
                    (member_id, team_id, event_id, player_id, player_username, role, army, detachment)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (team_id, player_id) DO UPDATE SET
                    active=TRUE, dropped_at=NULL,
                    player_username=EXCLUDED.player_username,
                    role=EXCLUDED.role
                RETURNING member_id
            """, (mid, team_id, eid, player_id, player_username, role, army, detachment))
            row = cur.fetchone()
            conn.commit()
    return row[0] if row else mid

def db_get_team_members(team_id: str, active_only: bool = True) -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if active_only:
                cur.execute("SELECT * FROM tournament_team_members WHERE team_id=%s AND active=TRUE ORDER BY joined_at", (team_id,))
            else:
                cur.execute("SELECT * FROM tournament_team_members WHERE team_id=%s ORDER BY joined_at", (team_id,))
            return [dict(r) for r in cur.fetchall()]

def db_get_team_member(team_id: str, player_id: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_team_members WHERE team_id=%s AND player_id=%s", (team_id, player_id))
            r = cur.fetchone()
    return dict(r) if r else None

def db_update_team_member(team_id: str, player_id: str, updates: dict):
    if not updates: return
    fields = ", ".join(f"{k}=%s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tournament_team_members SET {fields} WHERE team_id=%s AND player_id=%s",
                        list(updates.values()) + [team_id, player_id])
            conn.commit()

# ── Team rounds ───────────────────────────────────────────────────────────────

def db_create_team_round(round_id: str, eid: str, team_a_id: str, team_b_id: Optional[str]) -> str:
    trid = f"tr_{uuid.uuid4().hex[:8]}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_team_rounds
                    (team_round_id, round_id, event_id, team_a_id, team_b_id, state)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (trid, round_id, eid, team_a_id, team_b_id, TRS.PAIRING))
            conn.commit()
    return trid

def db_get_team_round(team_round_id: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_team_rounds WHERE team_round_id=%s", (team_round_id,))
            r = cur.fetchone()
    return dict(r) if r else None

def db_get_team_rounds(round_id: str) -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_team_rounds WHERE round_id=%s", (round_id,))
            return [dict(r) for r in cur.fetchall()]

def db_update_team_round(team_round_id: str, updates: dict):
    if not updates: return
    fields = ", ".join(f"{k}=%s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tournament_team_rounds SET {fields} WHERE team_round_id=%s",
                        list(updates.values()) + [team_round_id])
            conn.commit()

# ── Team pairings ─────────────────────────────────────────────────────────────

def db_create_team_pairing(team_round_id: str, slot: int) -> str:
    pid = f"tp_{uuid.uuid4().hex[:8]}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_team_pairings (pairing_id, team_round_id, pairing_slot)
                VALUES (%s, %s, %s)
            """, (pid, team_round_id, slot))
            conn.commit()
    return pid

def db_get_team_pairings(team_round_id: str) -> List[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_team_pairings WHERE team_round_id=%s ORDER BY pairing_slot", (team_round_id,))
            return [dict(r) for r in cur.fetchall()]

def db_update_team_pairing(pairing_id: str, updates: dict):
    if not updates: return
    fields = ", ".join(f"{k}=%s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tournament_team_pairings SET {fields} WHERE pairing_id=%s",
                        list(updates.values()) + [pairing_id])
            conn.commit()

# ── Pairing ritual state ──────────────────────────────────────────────────────

def db_create_pairing_state(team_round_id: str) -> str:
    sid = f"ps_{uuid.uuid4().hex[:8]}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_pairing_state
                    (state_id, team_round_id, current_phase, current_step)
                VALUES (%s, %s, 1, %s)
                ON CONFLICT (team_round_id) DO NOTHING
                RETURNING state_id
            """, (sid, team_round_id, PS.AWAIT_ROLLOFF))
            row = cur.fetchone()
            conn.commit()
    return row[0] if row else sid

def db_get_pairing_state(team_round_id: str) -> Optional[dict]:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM tournament_pairing_state WHERE team_round_id=%s", (team_round_id,))
            r = cur.fetchone()
    return dict(r) if r else None

def db_update_pairing_state(team_round_id: str, updates: dict):
    if not updates: return
    updates["updated_at"] = datetime.now(timezone.utc)  # FIX: use timezone-aware datetime
    fields = ", ".join(f"{k}=%s" for k in updates)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE tournament_pairing_state SET {fields} WHERE team_round_id=%s",
                        list(updates.values()) + [team_round_id])
            conn.commit()

# ── Scorebot integration (bulk submit at event end) ───────────────────────────

def scorebot_ensure_player(cur, pid: str, name: str):
    cur.execute("SELECT player_id FROM players WHERE player_id=%s", (pid,))
    if not cur.fetchone():
        cur.execute("INSERT INTO players (player_id,username,elo,wins,losses) VALUES (%s,%s,1000,0,0) ON CONFLICT DO NOTHING",
                    (pid, name))

def scorebot_get_season_id() -> Optional[int]:
    today = date.today()  # FIX: `date` now imported correctly
    q = (today.month - 1) // 3 + 1
    name = f"Season Q{q} {today.year}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT season_id FROM seasons WHERE name=%s", (name,))
            r = cur.fetchone()
    return r[0] if r else None

def scorebot_bulk_submit(eid: str, games: List[dict]) -> int:
    """
    Submit all confirmed tournament games to Scorebot's pending_matches at event end.
    Returns count of games submitted.
    """
    season_id = scorebot_get_season_id()
    submitted = 0
    now = datetime.utcnow()
    with get_conn() as conn:
        with conn.cursor() as cur:
            for g in games:
                if g["state"] != GS.COMPLETE or g["is_bye"]:
                    continue
                if not g.get("player2_id"):
                    continue
                match_id = f"tts_{g['game_id']}"
                scorebot_ensure_player(cur, g["player1_id"], g["player1_username"])
                scorebot_ensure_player(cur, g["player2_id"], g["player2_username"])
                try:
                    cur.execute("""
                        INSERT INTO pending_matches (
                            match_id,player1_id,player1_username,player1_army,player1_detachment,
                            player2_id,player2_username,player2_army,player2_detachment,
                            player1_score,player2_score,game_date,game_time,submitted_at,
                            screenshot_url,message_id,channel_id
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT DO NOTHING
                    """, (match_id,
                          g["player1_id"], g["player1_username"], g["player1_army"], g["player1_detachment"],
                          g["player2_id"], g["player2_username"], g["player2_army"], g["player2_detachment"],
                          g["player1_vp"], g["player2_vp"],
                          now.strftime("%Y-%m-%d"), now.strftime("%H:%M"),
                          int(now.timestamp() * 1000),
                          None, None, None))  # screenshot_url, message_id, channel_id — nullable
                    submitted += 1
                except Exception as e:
                    print(f"⚠️ Scorebot submit failed for game {g['game_id']}: {e}")
            conn.commit()
    return submitted

def db_get_results_by_player(event_id: str) -> dict:
    """
    Returns {player_id: {round_number: {"vp": int | None, "result": "W"|"L"|"D"|None}}}
    for every player in the event, across every round.

    "result" values:
      "W" = win (including bye)
      "L" = loss
      "D" = draw
      None = game exists but not yet confirmed (pending/submitted state)

    "vp" is the player's own VP for that game, or None if not yet confirmed.

    Used by the standings table to render per-round VP + result columns.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    g.player1_id,
                    g.player2_id,
                    g.player1_vp,
                    g.player2_vp,
                    g.state,
                    g.is_bye,
                    r.round_number
                FROM tournament_games g
                JOIN tournament_rounds r ON g.round_id = r.round_id
                WHERE g.event_id = %s
                ORDER BY r.round_number
            """, (event_id,))
            rows = cur.fetchall()

    # {player_id: {round_num: {"vp": int|None, "result": "W"|"L"|"D"|None}}}
    results: dict = {}

    for row in rows:
        rn  = row["round_number"]
        p1  = row["player1_id"]
        p2  = row["player2_id"]
        vp1 = row["player1_vp"]
        vp2 = row["player2_vp"]
        st  = row["state"]

        # Bye round — player 1 always wins; vp awarded at round close
        if row["is_bye"]:
            if p1:
                results.setdefault(p1, {})[rn] = {"vp": vp1, "result": "W"}
            continue

        # Confirmed result
        if st == "complete" and vp1 is not None and vp2 is not None:
            if vp1 > vp2:
                res1, res2 = "W", "L"
            elif vp2 > vp1:
                res1, res2 = "L", "W"
            else:
                res1 = res2 = "D"
        else:
            # Game exists but not yet confirmed
            res1 = res2 = None

        if p1:
            results.setdefault(p1, {})[rn] = {"vp": vp1, "result": res1}
        if p2:
            results.setdefault(p2, {})[rn] = {"vp": vp2, "result": res2}

    return results

# ══════════════════════════════════════════════════════════════════════════════
# FACTIONS CACHE
# ══════════════════════════════════════════════════════════════════════════════

_factions_cache:    dict[str, dict] = {}
_detachments_cache: dict[str, list] = {}


def init_factions_cache():
    """
    Load all factions and their detachments from DB into memory.
    Uses the existing 'armies' and 'detachments' tables.
    Call once at startup. Re-call after DB edits via /faction reload.
    """
    global _factions_cache, _detachments_cache
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT a.id, a.name, a.emoji,
                       d.name AS detachment_name
                FROM armies a
                LEFT JOIN detachments d ON d.army_id = a.id
                ORDER BY a.id, d.name
            """)
            rows = cur.fetchall()

    _factions_cache = {}
    _detachments_cache = {}
    for r in rows:
        army = r["name"]
        if army not in _factions_cache:
            _factions_cache[army] = {
                "emoji":      r["emoji"] or "⚔️",
                "colour":     (100, 100, 100),
                "sort_order": r["id"],
            }
        if r["detachment_name"]:
            _detachments_cache.setdefault(army, []).append(r["detachment_name"])

    print(f"✅ Factions cache loaded ({len(_factions_cache)} factions, "
          f"{sum(len(v) for v in _detachments_cache.values())} detachments)")
    return _factions_cache, _detachments_cache


def db_get_faction(army_name: str) -> dict:
    """Return {emoji, colour, sort_order} for a faction, or {} if not found."""
    return _factions_cache.get(army_name, {})


def db_get_factions() -> dict[str, dict]:
    """Return full {army_name: {emoji, colour, sort_order}} mapping (sorted)."""
    return _factions_cache


def db_get_army_names() -> list[str]:
    """Return sorted list of army names. Replaces WARHAMMER_ARMIES."""
    return list(_factions_cache.keys())   # already sorted by sort_order from query


# ── Missions & Layouts ────────────────────────────────────────────────────────

def db_get_missions() -> dict:
    """
    Return all missions as a dict keyed by code:
      { "A": {"name": "Take and Hold", "deployment": "Tipping Point", "layouts": ["1","2",...]}, ... }
    Replaces the static TOURNAMENT_MISSIONS dict in config.py.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT code, name, deployment FROM tournament_missions ORDER BY code")
            missions = {r["code"]: {"name": r["name"], "deployment": r["deployment"], "layouts": []}
                        for r in cur.fetchall()}
            cur.execute("SELECT mission_code, layout FROM tournament_layouts ORDER BY mission_code, layout")
            for r in cur.fetchall():
                if r["mission_code"] in missions:
                    missions[r["mission_code"]]["layouts"].append(r["layout"])
    return missions


def db_get_mission(code: str) -> dict:
    """
    Return a single mission dict {"name", "deployment", "layouts"} for the given code,
    or {} if not found.
    """
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT code, name, deployment FROM tournament_missions WHERE code = %s", (code,))
            row = cur.fetchone()
            if not row:
                return {}
            cur.execute("SELECT layout FROM tournament_layouts WHERE mission_code = %s ORDER BY layout", (code,))
            layouts = [r["layout"] for r in cur.fetchall()]
    return {"name": row["name"], "deployment": row["deployment"], "layouts": layouts}


def db_upsert_mission(code: str, name: str, deployment: str, layouts: list) -> None:
    """Insert or replace a mission and its layouts."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_missions (code, name, deployment)
                VALUES (%s, %s, %s)
                ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, deployment = EXCLUDED.deployment
            """, (code, name, deployment))
            cur.execute("DELETE FROM tournament_layouts WHERE mission_code = %s", (code,))
            for layout in layouts:
                cur.execute("""
                    INSERT INTO tournament_layouts (mission_code, layout) VALUES (%s, %s)
                    ON CONFLICT (mission_code, layout) DO NOTHING
                """, (code, layout))
            conn.commit()


def db_delete_mission(code: str) -> None:
    """Delete a mission and all its layouts (cascade)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM tournament_missions WHERE code = %s", (code,))
            conn.commit()
