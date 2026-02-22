"""
threads.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Discord thread management and tournament logic helpers.

Sections:
  • _add_thread_members / create_private_thread
  • ensure_submissions_thread / ensure_lists_thread / ensure_round_thread
  • add_player_to_event_threads / archive_event_threads
  • restore_thread_registry
  • Swiss pairing: calculate_rounds, get_previous_pairings, swiss_pair
  • assign_rooms, team Swiss pairing
  • Scoring formulas: ntl_gp, ntl_team_result, twovtwo_team_result
  • db_get_team_standings / db_upsert_team_standing / db_apply_team_result
  • ensure_all_round_threads

Imported by: services.py, commands_*.py, ritual.py
"""
import discord
import asyncio, math, re  # FIX: added `re` (used in assign_rooms)
import psycopg2.extras    # FIX: needed for RealDictCursor in db_get_team_standings
from datetime import datetime
from typing import List, Optional, Dict
from config import (EVENT_NOTICEBOARD_ID, CREW_ROLE_ID, PLAYER_ROLE_ID,
                    CAPTAINS_ROLE_ID, GUILD_ID, GAME_ROOM_PREFIX)
from state import get_thread_reg, thread_registry, RS  # FIX: added RS (used in ensure_submissions_thread)
from database import *

# ══════════════════════════════════════════════════════════════════════════════
# THREAD MANAGEMENT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _add_thread_members(thread: discord.Thread, guild: discord.Guild,
                               approved_player_ids: List[str] = None):
    """
    Add Crew role members, admins, and optionally approved players to a private thread.
    Batches additions with a small sleep to avoid rate limits.
    """
    to_add: List[discord.Member] = []
    seen = set()

    # Crew role
    if CREW_ROLE_ID:
        crew_role = guild.get_role(CREW_ROLE_ID)
        if crew_role:
            for m in crew_role.members:
                if m.id not in seen and not m.bot:
                    to_add.append(m); seen.add(m.id)

    # Player role
    if PLAYER_ROLE_ID and approved_player_ids is None:
        player_role = guild.get_role(PLAYER_ROLE_ID)
        if player_role:
            for m in player_role.members:
                if m.id not in seen and not m.bot:
                    to_add.append(m); seen.add(m.id)

    # Specific approved player IDs (more precise than role-based)
    if approved_player_ids:
        for pid in approved_player_ids:
            try:
                m = guild.get_member(int(pid))
                if m and m.id not in seen and not m.bot:
                    to_add.append(m); seen.add(m.id)
            except (ValueError, AttributeError):
                pass

    # Add in batches
    for i, member in enumerate(to_add):
        try:
            await thread.add_user(member)
        except (discord.HTTPException, discord.Forbidden):
            pass
        if i % 10 == 9:
            await asyncio.sleep(1.0)

async def create_private_thread(parent_ch: discord.TextChannel, name: str,
                                  anchor_msg: discord.Message = None) -> Optional[discord.Thread]:
    """Create a private thread, optionally anchored to a message."""
    try:
        if anchor_msg:
            thread = await anchor_msg.create_thread(name=name, auto_archive_duration=10080)
            # Threads created on messages are public by default — we need a standalone private thread
            # Discord does not support private threads on messages; create standalone instead
            await thread.delete()
        thread = await parent_ch.create_thread(
            name=name,
            type=discord.ChannelType.private_thread,
            auto_archive_duration=10080,  # 7 days
            invitable=False,              # Only the bot can add members
        )
        return thread
    except discord.HTTPException as e:
        print(f"⚠️ Thread creation failed ({name}): {e}")
        return None

async def ensure_submissions_thread(bot, event_id: str, guild: discord.Guild,
                                     event_name: str) -> Optional[discord.Thread]:
    """Get or create the Submissions private thread for an event."""
    reg = get_thread_reg(event_id)
    if reg["submissions"]:
        t = guild.get_thread(reg["submissions"])
        if t: return t
    ch = bot.get_channel(EVENT_NOTICEBOARD_ID)
    if not ch: return None
    thread = await create_private_thread(ch, f"📋 Submissions — {event_name}")
    if thread:
        reg["submissions"] = thread.id
        db_update_event(event_id, {"submissions_thread_id": str(thread.id)})
        regs = db_get_registrations(event_id, RS.APPROVED)
        player_ids = [r["player_id"] for r in regs]
        await _add_thread_members(thread, guild, player_ids)
        await thread.send(
            f"📋 **Result confirmations for {event_name}**\n"
            f"Pending results will appear here. Opponents confirm or dispute below each card.\n"
            f"*This thread is private — visible only to players, Crew, and admins.*"
        )
    return thread

async def ensure_lists_thread(
    bot, event_id: str, guild: discord.Guild, event_name: str,
) -> Optional[discord.Thread]:
    """
    Get or create the PUBLIC Army Lists thread in #event-noticeboard.
    Long player names are handled at the embed level (build_player_list_embed).
    """
    from state import get_thread_reg
    from database import db_update_event
    from config import EVENT_NOTICEBOARD_ID

    reg = get_thread_reg(event_id)
    if reg.get("lists"):
        t = guild.get_thread(reg["lists"])
        if t:
            return t

    ch = bot.get_channel(EVENT_NOTICEBOARD_ID)
    if not ch:
        return None

    try:
        thread = await ch.create_thread(
            name=f"📋  Army Lists  —  {event_name}",
            type=discord.ChannelType.public_thread,
            auto_archive_duration=10080,
        )
    except discord.HTTPException as e:
        print(f"⚠️ Army Lists thread creation failed: {e}")
        return None

    reg["lists"] = thread.id
    db_update_event(event_id, {"lists_thread_id": str(thread.id)})
    return thread

async def ensure_all_round_threads(
    bot, event_id: str, guild: discord.Guild,
    event_name: str, total_rounds: int,
) -> dict:
    """
    Pre-create ALL round pairing threads when the event starts.
    Threads are created empty — content is posted by /round start.
    Returns {round_number: thread} for all rounds.
    """
    threads = {}
    for rn in range(1, total_rounds + 1):
        t = await ensure_round_thread(bot, event_id, rn, guild, event_name)
        if t:
            threads[rn] = t
        await asyncio.sleep(0.5)  # rate limit safety
    return threads

async def ensure_round_thread(
    bot, event_id: str, round_number: int,
    guild: discord.Guild, event_name: str,
) -> Optional[discord.Thread]:
    """
    Get or create the PUBLIC thread for a round's pairings in #event-noticeboard.
    Created empty when the event starts; content posted when the round begins.
    """
    from state import get_thread_reg
    from database import db_update_round_thread, db_get_registrations, RS
    from config import EVENT_NOTICEBOARD_ID

    reg = get_thread_reg(event_id)
    existing_id = reg["rounds"].get(round_number)
    if existing_id:
        t = guild.get_thread(existing_id)
        if t:
            return t

    ch = bot.get_channel(EVENT_NOTICEBOARD_ID)
    if not ch:
        return None

    try:
        thread = await ch.create_thread(
            name=f"⚔️  Round {round_number} Pairings",
            type=discord.ChannelType.public_thread,
            auto_archive_duration=10080,  # 7 days
        )
    except discord.HTTPException as e:
        print(f"⚠️ Round thread creation failed (Round {round_number}): {e}")
        return None

    reg["rounds"][round_number] = thread.id
    db_update_round_thread(event_id, round_number, str(thread.id))
    return thread

async def add_player_to_event_threads(bot, event_id: str, guild: discord.Guild,
                                       player_id: str):
    """Add a newly approved player to all existing event threads."""
    member = guild.get_member(int(player_id))
    if not member: return
    reg = get_thread_reg(event_id)
    for tid in [reg.get("submissions"), reg.get("lists")] + list(reg.get("rounds", {}).values()):
        if not tid: continue
        t = guild.get_thread(tid)
        if t:
            try: await t.add_user(member)
            except: pass

async def archive_event_threads(bot, event_id: str, guild: discord.Guild):
    """Archive and lock all threads for a completed event."""
    reg = get_thread_reg(event_id)
    thread_ids = (
        [reg.get("submissions"), reg.get("lists")] +
        list(reg.get("rounds", {}).values())
    )
    for tid in thread_ids:
        if not tid: continue
        t = guild.get_thread(tid)
        if t:
            try:
                await t.send("🏁 *This thread has been archived. The event is complete.*")
                await t.edit(archived=True, locked=True)
            except: pass

def db_update_round_thread(event_id: str, round_number: int, thread_id: str):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""UPDATE tournament_rounds SET round_thread_id=%s
                           WHERE event_id=%s AND round_number=%s""",
                        (thread_id, event_id, round_number))
            conn.commit()

async def restore_thread_registry(bot, guild: discord.Guild):
    """On restart, repopulate thread_registry from DB so existing threads are reused."""
    for event in db_get_active_events():
        eid = event["event_id"]
        reg = get_thread_reg(eid)
        if event.get("submissions_thread_id"):
            reg["submissions"] = int(event["submissions_thread_id"])
        if event.get("lists_thread_id"):
            reg["lists"] = int(event["lists_thread_id"])
        for rnd in db_get_rounds(eid):
            if rnd.get("round_thread_id"):
                reg["rounds"][rnd["round_number"]] = int(rnd["round_thread_id"])
    print("✅ Thread registry restored")

def calculate_rounds(player_count: int) -> int:
    if player_count <= 4:  return 2
    if player_count <= 8:  return 3
    if player_count <= 16: return 4
    if player_count <= 32: return 5
    return 6

def get_previous_pairings(eid: str) -> set:
    pairs = set()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT player1_id,player2_id FROM tournament_games WHERE event_id=%s AND is_bye=FALSE",
                        (eid,))
            for r in cur.fetchall():
                if r[0] and r[1]:
                    pairs.add(frozenset([r[0], r[1]]))
    return pairs

def swiss_pair(players: List[dict], previous: set) -> tuple:
    """
    Returns (pairings, bye_player).
    pairings = list of (p1_dict, p2_dict).
    bye_player = dict or None.
    Players ranked by wins DESC, vp_diff DESC.
    """
    ranked = sorted(players, key=lambda p: (p["wins"], p["vp_diff"]), reverse=True)

    bye_player = None
    if len(ranked) % 2 != 0:
        # Give bye to lowest-ranked player who hasn't had one yet
        for p in reversed(ranked):
            if not p.get("had_bye", False):
                bye_player = p
                ranked = [x for x in ranked if x["player_id"] != p["player_id"]]
                break
        if bye_player is None:
            bye_player = ranked.pop()

    pairings = []
    unpaired = list(ranked)
    while len(unpaired) >= 2:
        p1 = unpaired.pop(0)
        paired = False
        for i, p2 in enumerate(unpaired):
            if frozenset([p1["player_id"], p2["player_id"]]) not in previous:
                pairings.append((p1, p2))
                unpaired.pop(i)
                paired = True
                break
        if not paired:
            # Forced rematch — all remaining opponents seen before
            pairings.append((p1, unpaired.pop(0)))

    return pairings, bye_player

def get_avg_vp(eid: str, round_id: str) -> float:
    """Average VP of all confirmed games in a round (for bye award)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT player1_vp, player2_vp FROM tournament_games
                WHERE event_id=%s AND round_id=%s AND state='complete' AND is_bye=FALSE
            """, (eid, round_id))
            rows = cur.fetchall()
    if not rows:
        return 0.0
    all_vp = [vp for r in rows for vp in r if vp is not None]
    return sum(all_vp) / len(all_vp) if all_vp else 0.0

def assign_rooms(pairings: List[tuple], guild: discord.Guild) -> List[dict]:
    room_nums = sorted([
        n for n in [
            (lambda m: int(m.group(1)) if m else None)(re.search(r"(\d+)\s*$", ch.name))
            for ch in guild.voice_channels if ch.name.startswith(GAME_ROOM_PREFIX)
        ] if n is not None
    ])
    result = []
    for i, (p1, p2) in enumerate(pairings):
        result.append({"p1": p1, "p2": p2, "room": room_nums[i] if i < len(room_nums) else None})
    return result

# ── Team Swiss pairing ────────────────────────────────────────────────────────

def get_previous_team_pairings(eid: str) -> set:
    """Return set of frozensets of team_id pairs that have already played."""
    pairs = set()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT team_a_id, team_b_id FROM tournament_team_rounds
                WHERE event_id=%s AND team_b_id IS NOT NULL
            """, (eid,))
            for r in cur.fetchall():
                pairs.add(frozenset([r[0], r[1]]))
    return pairs

def team_swiss_pair(teams: List[dict], previous: set) -> tuple:
    """
    Swiss-pair teams by team_points DESC, then total game_points DESC.
    Returns (pairings, bye_team).
    pairings = list of (team_a, team_b).
    bye_team = dict or None.
    """
    ranked = sorted(teams, key=lambda t: (t.get("team_points", 0), t.get("game_points", 0)), reverse=True)

    bye_team = None
    if len(ranked) % 2 != 0:
        for t in reversed(ranked):
            if not t.get("had_bye", False):
                bye_team = t
                ranked = [x for x in ranked if x["team_id"] != t["team_id"]]
                break
        if bye_team is None:
            bye_team = ranked.pop()

    pairings = []
    unpaired = list(ranked)
    while len(unpaired) >= 2:
        t1 = unpaired.pop(0)
        paired = False
        for i, t2 in enumerate(unpaired):
            if frozenset([t1["team_id"], t2["team_id"]]) not in previous:
                pairings.append((t1, t2))
                unpaired.pop(i)
                paired = True
                break
        if not paired:
            pairings.append((t1, unpaired.pop(0)))

    return pairings, bye_team

# ── NTL GP scoring table ──────────────────────────────────────────────────────

NTL_GP_TABLE = [
    (0,   5,  10, 10),
    (6,  10,  11,  9),
    (11, 15,  12,  8),
    (16, 20,  13,  7),
    (21, 25,  14,  6),
    (26, 30,  15,  5),
    (31, 35,  16,  4),
    (36, 40,  17,  3),
    (41, 45,  18,  2),
    (46, 50,  19,  1),
    (51, 999, 20,  0),
]

def ntl_gp(winner_vp: int, loser_vp: int) -> tuple:
    """Return (winner_gp, loser_gp) from the NTL differential table."""
    diff = abs(winner_vp - loser_vp)
    for lo, hi, wgp, lgp in NTL_GP_TABLE:
        if lo <= diff <= hi:
            return wgp, lgp
    return 20, 0

def ntl_team_result(total_gp: int, max_gp: int) -> tuple:
    """
    Given total GP scored by a team and max possible GP,
    return (tournament_points, result_str).
    Thresholds scale proportionally from the 8s baseline (max 160):
      win  >= 86/160  → scale to max_gp
      draw  75-85/160 → scale
      loss  < 75/160
    """
    if max_gp == 0:
        return 0, "Loss"
    ratio = total_gp / max_gp
    if ratio >= (86 / 160):
        return 2, "Win"
    elif ratio >= (75 / 160):
        return 1, "Draw"
    else:
        return 0, "Loss"

def twovtwo_team_result(wins_a: int, wins_b: int) -> tuple:
    """2v2: team with more individual game wins wins the round. (tp_a, tp_b)"""
    if wins_a > wins_b:   return 2, 0
    elif wins_b > wins_a: return 0, 2
    else:                 return 1, 1  # 1-1 draw

# ── Team standings DB helpers ─────────────────────────────────────────────────

def db_get_team_standings(eid: str) -> List[dict]:
    """Return team-level standings for a team-format event."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT ts.*, t.team_name, t.captain_username, t.state as team_state
                FROM tournament_standings ts
                JOIN tournament_teams t ON ts.team_id = t.team_id
                WHERE ts.event_id=%s AND t.state != 'dropped'
                ORDER BY ts.team_points DESC, ts.game_points DESC, ts.vp_diff DESC
            """, (eid,))
            return [dict(r) for r in cur.fetchall()]

def db_upsert_team_standing(eid: str, team_id: str, team_name: str):
    """Ensure a team has a standings row (uses a synthetic player_id for the team)."""
    sid = f"std_{eid[:8]}_team_{team_id[:8]}"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tournament_standings
                    (standing_id, event_id, player_id, player_username, army, detachment, team_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id, player_id) DO NOTHING
            """, (sid, eid, f"team_{team_id}", team_name, "—", "—", team_id))
            conn.commit()

def db_apply_team_result(eid: str, team_id: str, tp: int, gp: int, vp_diff: int, is_win: bool, is_draw: bool):
    """Apply a team round result to team standings."""
    wins   = 1 if is_win else 0
    losses = 0 if (is_win or is_draw) else 1
    draws  = 1 if is_draw else 0
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE tournament_standings SET
                    team_wins   = team_wins   + %s,
                    team_losses = team_losses + %s,
                    team_draws  = team_draws  + %s,
                    team_points = team_points + %s,
                    game_points = game_points + %s,
                    vp_diff     = vp_diff     + %s
                WHERE event_id=%s AND team_id=%s
            """, (wins, losses, draws, tp, gp, vp_diff, eid, team_id))
            conn.commit()
