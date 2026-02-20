"""
state.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
State-machine enums (ES, FMT, TS, TRS, PS, RS, RndS, GS, JCS),
permission helper is_to(), and in-memory registries:
  • judge_roster  — which judges are on-duty / handling which call
  • thread_registry — cached Discord thread IDs per event

Imported by: database.py, threads.py, views.py, commands_*.py, ritual.py
"""
import discord
from typing import Dict, List
from config import CREW_ROLE_ID

# ══════════════════════════════════════════════════════════════════════════════
# STATE ENUMS
# ══════════════════════════════════════════════════════════════════════════════

class ES:   # EventState
    ANNOUNCED    = "announced"
    INTEREST     = "interest"
    REGISTRATION = "registration"
    IN_PROGRESS  = "in_progress"
    COMPLETE     = "complete"

class FMT:  # Format
    SINGLES  = "singles"
    TWO_V_TWO = "2v2"
    TEAMS_3  = "teams_3"
    TEAMS_5  = "teams_5"
    TEAMS_8  = "teams_8"

    @staticmethod
    def team_size(fmt: str) -> int:
        return {FMT.TWO_V_TWO: 2, FMT.TEAMS_3: 3, FMT.TEAMS_5: 5, FMT.TEAMS_8: 8}.get(fmt, 1)

    @staticmethod
    def is_team(fmt: str) -> bool:
        return fmt != FMT.SINGLES

    @staticmethod
    def phase_count(fmt: str) -> int:
        """Number of defender/attacker phases in the pairing ritual."""
        return {FMT.TEAMS_3: 1, FMT.TEAMS_5: 2, FMT.TEAMS_8: 3}.get(fmt, 0)

    @staticmethod
    def individual_points(fmt: str) -> int:
        return 1000 if fmt == FMT.TWO_V_TWO else 2000

class TS:   # TeamState
    FORMING = "forming"
    READY   = "ready"
    DROPPED = "dropped"

class TRS:  # TeamRoundState
    PAIRING  = "pairing"
    PLAYING  = "playing"
    COMPLETE = "complete"

class PS:   # PairingStep (ritual state machine)
    AWAIT_ROLLOFF   = "await_rolloff"
    AWAIT_DEFENDERS = "await_defenders"
    AWAIT_ATTACKERS = "await_attackers"
    AWAIT_CHOICE    = "await_choice"
    AWAIT_LAYOUT_A  = "await_layout_a"
    AWAIT_MISSION_A = "await_mission_a"
    AWAIT_LAYOUT_B  = "await_layout_b"
    AWAIT_MISSION_B = "await_mission_b"
    COMPLETE        = "complete"

class RS:   # RegistrationState
    INTERESTED = "interested"
    PENDING    = "pending"
    APPROVED   = "approved"
    REJECTED   = "rejected"
    DROPPED    = "dropped"

class RndS: # RoundState
    PENDING     = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETE    = "complete"

class GS:   # GameState
    PENDING   = "pending"
    SUBMITTED = "submitted"
    COMPLETE  = "complete"
    DISPUTED  = "disputed"
    BYE       = "bye"

class JCS:  # JudgeCallState
    OPEN         = "open"
    ACKNOWLEDGED = "acknowledged"
    CLOSED       = "closed"

# ══════════════════════════════════════════════════════════════════════════════
# PERMISSION HELPER
# ══════════════════════════════════════════════════════════════════════════════

def is_to(interaction: discord.Interaction) -> bool:
    """True if the user is an admin or has the Crew role."""
    if interaction.user.guild_permissions.administrator:
        return True
    if CREW_ROLE_ID:
        return any(r.id == CREW_ROLE_ID for r in interaction.user.roles)
    return False

# ══════════════════════════════════════════════════════════════════════════════
# IN-MEMORY MULTI-JUDGE STATUS  (ephemeral — resets on restart safely)
# ══════════════════════════════════════════════════════════════════════════════
#
# Structure: judge_roster[event_id][judge_id] = {"name": str, "call_id": str|None, "room": int|None}
# A judge is "busy" when call_id is not None.
# The roster is populated whenever a judge acknowledges a call or is looked up.

judge_roster: Dict[str, Dict[str, dict]] = {}

def get_judge_roster(event_id: str) -> Dict[str, dict]:
    return judge_roster.setdefault(event_id, {})

def register_judge(event_id: str, judge_id: str, judge_name: str):
    roster = get_judge_roster(event_id)
    if judge_id not in roster:
        roster[judge_id] = {"name": judge_name, "call_id": None, "room": None}

def judge_take_call(event_id: str, judge_id: str, judge_name: str, call_id: str, room: int):
    roster = get_judge_roster(event_id)
    roster[judge_id] = {"name": judge_name, "call_id": call_id, "room": room}

def judge_release_call(event_id: str, judge_id: str):
    roster = get_judge_roster(event_id)
    if judge_id in roster:
        roster[judge_id]["call_id"] = None
        roster[judge_id]["room"]    = None

def get_judges_for_guild(guild: discord.Guild, event_id: str) -> List[dict]:
    """
    Return list of dicts describing every judge on duty for this event.
    Includes all Crew role members + admins visible in the guild.
    Merges with in-memory call status.
    """
    roster = get_judge_roster(event_id)
    judges = []
    seen   = set()
    # Crew role members
    if CREW_ROLE_ID:
        crew_role = guild.get_role(CREW_ROLE_ID)
        if crew_role:
            for m in crew_role.members:
                sid = str(m.id)
                seen.add(sid)
                status = roster.get(sid, {})
                judges.append({
                    "id":      sid,
                    "name":    m.display_name,
                    "call_id": status.get("call_id"),
                    "room":    status.get("room"),
                    "online":  m.status != discord.Status.offline if hasattr(m, "status") else True,
                })
    # Admins not already listed
    for m in guild.members:
        sid = str(m.id)
        if sid in seen: continue
        if m.guild_permissions.administrator and not m.bot:
            status = roster.get(sid, {})
            judges.append({
                "id":      sid,
                "name":    m.display_name,
                "call_id": status.get("call_id"),
                "room":    status.get("room"),
                "online":  True,
            })
    return judges

# ══════════════════════════════════════════════════════════════════════════════
# IN-MEMORY THREAD REGISTRY  (ephemeral — repopulated from DB on restart)
# ══════════════════════════════════════════════════════════════════════════════
#
# thread_registry[event_id] = {
#   "submissions":  thread_id,
#   "lists":        thread_id,
#   "rounds":       {round_number: thread_id},
#   "queue_msg_id": message_id,   # judge queue embed in noticeboard
# }

thread_registry: Dict[str, dict] = {}

def get_thread_reg(event_id: str) -> dict:
    return thread_registry.setdefault(event_id, {
        "submissions":  None,
        "lists":        None,
        "rounds":       {},
        "queue_msg_id": None,
    })

# ══════════════════════════════════════════════════════════════════════════════
# DATABASE  —  CONNECTION + INIT
# ══════════════════════════════════════════════════════════════════════════════
