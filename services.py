"""
services.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Background service coroutines and autocomplete helpers.

Functions:
  • refresh_spectator_dashboard
  • log_immediate
  • refresh_standings_card
  • _refresh_judges_on_duty
  • Autocomplete: ac_active_events, ac_all_events, ac_missions,
                  ac_armies, ac_detachments, ac_pending_regs,
                  ac_approved_regs, ac_active_games, ac_complete_games

Imported by: all command modules and main.py
"""
import discord
from discord import app_commands
from datetime import datetime
from typing import List
from config import (WHATS_PLAYING_ID, EVENT_NOTICEBOARD_ID, BOT_LOGS_ID,
                    TOURNAMENT_MISSIONS)
# FIX: import live faction data from database cache instead of stale static config lists
from database import *
from state import get_thread_reg, GS, RS
from embeds import build_spectator_dashboard_embed, build_judges_on_duty_embed, build_standings_embed

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

async def refresh_standings_card(bot, event_id: str):
    """
    Refresh the pinned Standings card in #event-noticeboard.
    Called from the refresh_dashboards background task alongside
    refresh_spectator_dashboard and _refresh_judges_on_duty.
    """
    event = db_get_event(event_id)
    if not event:
        return

    ch = bot.get_channel(EVENT_NOTICEBOARD_ID)
    if not ch:
        return

    reg = get_thread_reg(event_id)
    msg_id = reg.get("standings_msg_id") or (
        int(event["standings_msg_id"]) if event.get("standings_msg_id") else None
    )
    if not msg_id:
        return

    standings = db_get_standings(event_id)
    embed     = build_standings_embed(event, standings)

    try:
        msg = await ch.fetch_message(int(msg_id))
        await msg.edit(embed=embed)
    except (discord.NotFound, discord.HTTPException):
        pass   # Card was deleted — admin will re-pin manually

async def _refresh_judges_on_duty(bot, event_id: str, guild: discord.Guild):
    """
    Refresh (or post) the Judges on Duty card in #event-noticeboard.

    Called:
      • Every 5 min by the refresh_dashboards background task
      • Immediately on on_voice_state_update when a crew member
        joins or leaves a Game Room (see main.py hook below)
    """
    ch = bot.get_channel(EVENT_NOTICEBOARD_ID)
    if not ch:
        return

    round_obj = db_get_current_round(event_id)
    embed     = build_judges_on_duty_embed(guild, round_obj)
    reg       = get_thread_reg(event_id)

    try:
        if reg.get("judge_msg_id"):
            msg = await ch.fetch_message(int(reg["judge_msg_id"]))
            await msg.edit(embed=embed)
        else:
            msg = await ch.send(embed=embed)
            reg["judge_msg_id"] = msg.id
    except (discord.NotFound, discord.HTTPException):
        # Message was deleted — repost
        msg = await ch.send(embed=embed)
        reg["judge_msg_id"] = msg.id

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
    # FIX: use live DB cache (db_get_army_names) instead of static WARHAMMER_ARMIES from config
    return [app_commands.Choice(name=a, value=a)
            for a in db_get_army_names() if current.lower() in a.lower()][:25]

async def ac_detachments(i: discord.Interaction, current: str):
    army = getattr(i.namespace, "army", "") or ""
    # FIX: use live DB cache (_detachments_cache via db_get_factions) instead of static config dict
    factions = db_get_factions()
    dets = _detachments_cache.get(army, ["Other"]) if army in factions else ["Other"]
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
