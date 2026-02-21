"""
state.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
State-machine enums, permission helper, and in-memory thread registry.

Judge availability is derived live from voice channel presence —
no in-memory roster or call tracking needed here.

Imported by: database.py, threads.py, views.py, commands_*.py, ritual.py
"""
import discord
from typing import Dict, List
from config import CREW_ROLE_ID, GAME_ROOM_PREFIX

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
    SINGLES   = "singles"
    TWO_V_TWO = "2v2"
    TEAMS_3   = "teams_3"
    TEAMS_5   = "teams_5"
    TEAMS_8   = "teams_8"

    @staticmethod
    def team_size(fmt: str) -> int:
        return {FMT.TWO_V_TWO: 2, FMT.TEAMS_3: 3, FMT.TEAMS_5: 5, FMT.TEAMS_8: 8}.get(fmt, 1)

    @staticmethod
    def is_team(fmt: str) -> bool:
        return fmt != FMT.SINGLES

    @staticmethod
    def phase_count(fmt: str) -> int:
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

# JCS kept for DB schema compatibility — existing rows still reference these values
class JCS:
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
# JUDGE AVAILABILITY  —  derived from voice channel presence
# ══════════════════════════════════════════════════════════════════════════════
#
# A judge is AVAILABLE if they are NOT in a voice channel whose name starts
# with GAME_ROOM_PREFIX (e.g. "Game Room").
# A judge is IN-GAME (busy) if they ARE in such a channel.
# No in-memory tracking required — Discord's member.voice is the source of truth.

def _judge_voice_status(member: discord.Member) -> tuple[bool, str | None]:
    """
    Returns (in_game_room, channel_name_or_None).
    Checks if the member is currently in a Game Room voice channel.
    """
    vc = member.voice
    if vc and vc.channel and vc.channel.name.startswith(GAME_ROOM_PREFIX):
        return True, vc.channel.name
    return False, None

def get_judges_for_guild(guild: discord.Guild) -> List[dict]:
    """
    Return all on-duty judges (Crew role members + admins) with live
    availability status based on their current voice channel.

    Each entry:
      {
        "id":        str,
        "name":      str,
        "available": bool,      # True = not in a Game Room (free to DM)
        "room":      str|None,  # e.g. "Game Room 3" if busy
        "mention":   str,       # discord mention string for DM instructions
      }
    """
    seen: set[str] = set()
    judges: List[dict] = []

    def _entry(m: discord.Member) -> dict:
        in_room, room_name = _judge_voice_status(m)
        return {
            "id":        str(m.id),
            "name":      m.display_name,
            "available": not in_room,
            "room":      room_name,
            "mention":   m.mention,
        }

    # Crew role members first
    if CREW_ROLE_ID:
        crew_role = guild.get_role(CREW_ROLE_ID)
        if crew_role:
            for m in crew_role.members:
                sid = str(m.id)
                seen.add(sid)
                judges.append(_entry(m))

    # Admins not already listed
    for m in guild.members:
        sid = str(m.id)
        if sid in seen or m.bot:
            continue
        if m.guild_permissions.administrator:
            seen.add(sid)
            judges.append(_entry(m))

    return judges

# ══════════════════════════════════════════════════════════════════════════════
# IN-MEMORY THREAD REGISTRY  (ephemeral — repopulated from DB on restart)
# ══════════════════════════════════════════════════════════════════════════════
#
# thread_registry[event_id] = {
#   "submissions":  thread_id,
#   "lists":        thread_id,
#   "rounds":       {round_number: thread_id},
#   "judge_msg_id": message_id,   # Judges on Duty card in #event-noticeboard
# }

thread_registry: Dict[str, dict] = {}

def get_thread_reg(event_id: str) -> dict:
    return thread_registry.setdefault(event_id, {
        "submissions":      None,   
        "lists":            None,   
        "rounds":           {},     
        "judge_msg_id":     None,   
        "standings_msg_id": None,   
