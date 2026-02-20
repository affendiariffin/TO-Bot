"""
services.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Background service coroutines and autocomplete helpers.

Functions:
  • refresh_spectator_dashboard
  • _refresh_judge_queue
  • log_immediate
  • Autocomplete: ac_active_events, ac_all_events, ac_missions,
                  ac_armies, ac_detachments, ac_pending_regs,
                  ac_approved_regs, ac_active_games, ac_complete_games

Imported by: all command modules and tasks.py
"""
import discord
from discord import app_commands
from typing import List
from config import WHATS_PLAYING_ID, EVENT_NOTICEBOARD_ID, WARHAMMER_ARMIES, TOURNAMENT_MISSIONS
from state import get_thread_reg, GS, RS
from database import *
from embeds import build_spectator_dashboard_embed, build_judge_queue_embed
from views import JudgeQueueView

# ══════════════════════════════════════════════════════════════════════════════
# DASHBOARD REFRESH HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def refresh_spectator_dashboard(bot, event_id: str):
    event = db_get_event(event_id)
    if not event or not event.get("spectator_msg_id"):
        return
    ch = bot.get_channel(WHATS_PLAYING_ID)
    if not ch:
        return
    try:
        msg       = await ch.fetch_message(int(event["spectator_msg_id"]))
        round_obj = db_get_current_round(event_id)
        games     = db_get_games(round_obj["round_id"]) if round_obj else []
        standings = db_get_standings(event_id)
        embed     = build_spectator_dashboard_embed(event, round_obj, games, standings, ch.guild)
        await msg.edit(embed=embed)
    except Exception as e:
        print(f"⚠️ Spectator dashboard refresh failed: {e}")

async def _refresh_judge_queue(bot, event_id: str, guild: discord.Guild):
    """Refresh or post the judge queue embed in #event-noticeboard."""
    ch = bot.get_channel(EVENT_NOTICEBOARD_ID)
    if not ch:
        return
    event     = db_get_event(event_id)
    calls     = db_get_open_calls(event_id)
    round_obj = db_get_current_round(event_id)
    judges    = get_judges_for_guild(guild, event_id)
    embed     = build_judge_queue_embed(event, calls, round_obj, judges)
    view      = JudgeQueueView(event_id, calls)
    reg       = get_thread_reg(event_id)
    try:
        if reg.get("queue_msg_id"):
            msg = await ch.fetch_message(int(reg["queue_msg_id"]))
            await msg.edit(embed=embed, view=view)
        else:
            msg = await ch.send(embed=embed, view=view)
            reg["queue_msg_id"] = msg.id
    except (discord.NotFound, discord.HTTPException):
        msg = await ch.send(embed=embed, view=view)
        reg["queue_msg_id"] = msg.id

async def log_immediate(bot, title: str, description: str, color: discord.Color = discord.Color.blue()):
    if not BOT_LOGS_ID:
        return
    ch = bot.get_channel(BOT_LOGS_ID)
    if not ch:
        return
    embed = discord.Embed(title=f"🔔  {title}", description=description,
                           color=color, timestamp=datetime.utcnow())
    try:
        await ch.send(embed=embed)
    except Exception as e:
        print(f"⚠️ Immediate log failed: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# AUTOCOMPLETE FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

async def ac_active_events(i: discord.Interaction, current: str):
    return [app_commands.Choice(name=f"{e['name']} [{e['state']}]", value=e["event_id"])
            for e in db_get_active_events() if current.lower() in e["name"].lower()][:25]

async def ac_all_events(i: discord.Interaction, current: str):
    return [app_commands.Choice(name=f"{e['name']} [{e['state']}]", value=e["event_id"])
            for e in db_get_all_events() if current.lower() in e["name"].lower()][:25]

async def ac_missions(i: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=f"{code}: {m['name']} [{m['deployment']}]", value=code)
        for code, m in TOURNAMENT_MISSIONS.items()
        if current.lower() in f"{code} {m['name']} {m['deployment']}".lower()
    ][:25]

async def ac_armies(i: discord.Interaction, current: str):
    return [app_commands.Choice(name=a, value=a)
            for a in WARHAMMER_ARMIES if current.lower() in a.lower()][:25]

async def ac_detachments(i: discord.Interaction, current: str):
    army = i.namespace.army or ""
    dets = WARHAMMER_DETACHMENTS.get(army, ["Other"])
    return [app_commands.Choice(name=d, value=d)
            for d in dets if current.lower() in d.lower()][:25]

async def ac_pending_regs(i: discord.Interaction, current: str):
    eid = getattr(i.namespace, "event_id", "")
    if not eid: return []
    return [
        app_commands.Choice(name=f"{r['player_username']} — {r['army']}", value=r["player_id"])
        for r in db_get_registrations(eid, RS.PENDING)
        if current.lower() in r["player_username"].lower()
    ][:25]

async def ac_approved_regs(i: discord.Interaction, current: str):
    eid = getattr(i.namespace, "event_id", "")
    if not eid: return []
    return [
        app_commands.Choice(name=f"{r['player_username']} — {r['army']}", value=r["player_id"])
        for r in db_get_registrations(eid, RS.APPROVED)
        if current.lower() in r["player_username"].lower()
    ][:25]

async def ac_active_games(i: discord.Interaction, current: str):
    eid = getattr(i.namespace, "event_id", "")
    if not eid: return []
    rnd = db_get_current_round(eid)
    if not rnd: return []
    return [
        app_commands.Choice(
            name=f"Room {g['room_number']}: {g['player1_username']} vs {g['player2_username']}",
            value=g["game_id"],
        )
        for g in db_get_games(rnd["round_id"])
        if not g["is_bye"] and g["state"] not in (GS.BYE,)
        and current.lower() in f"room {g['room_number']} {g['player1_username']} {g['player2_username']}".lower()
    ][:25]

async def ac_complete_games(i: discord.Interaction, current: str):
    """All completed games in the event (for adjust command)."""
    eid = getattr(i.namespace, "event_id", "")
    if not eid: return []
    return [
        app_commands.Choice(
            name=f"R{g['room_number'] or '?'}: {g['player1_username']} {g.get('player1_vp','?')}–{g.get('player2_vp','?')} {g['player2_username']}",
            value=g["game_id"],
        )
        for g in db_get_event_games(eid)
        if g["state"] == GS.COMPLETE and not g["is_bye"]
        and current.lower() in f"{g['player1_username']} {g['player2_username']}".lower()
    ][:25]

