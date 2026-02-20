"""
embeds.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All discord.Embed builder functions.

Functions:
  • vp_bar
  • build_event_announcement_embed
  • build_briefing_embed
  • build_pairings_embed
  • build_standings_embed
  • build_team_standings_embed
  • build_spectator_dashboard_embed
  • build_list_review_header
  • build_player_list_embed
  • build_judge_queue_embed

Imported by: services.py, views.py, commands_*.py
"""
import discord
from datetime import datetime
from typing import List, Optional
from config import (COLOUR_GOLD, COLOUR_CRIMSON, COLOUR_AMBER, COLOUR_SLATE,
                    SEP, GAME_ROOM_PREFIX, TOURNAMENT_MISSIONS,
                    fe, faction_colour, room_colour, ts, ts_full)
from state import GS, RndS, JCS, FMT
from database import get_judges_for_guild  # only used in judge queue embed

# ══════════════════════════════════════════════════════════════════════════════
# EMBED BUILDERS  —  TV-bot design language
# ══════════════════════════════════════════════════════════════════════════════

def vp_bar(vp: int, max_vp: int = 120, width: int = 10) -> str:
    """Unicode VP progress bar. ▓▓▓▓░░░░  85 VP"""
    if not max_vp:
        return ""
    filled = round((vp / max_vp) * width)
    filled = max(0, min(width, filled))
    return f"{'▓' * filled}{'░' * (width - filled)}  {vp}"

def build_event_announcement_embed(event: dict) -> discord.Embed:
    m = TOURNAMENT_MISSIONS.get(event["mission_code"], {})
    sd, ed = event["start_date"], event["end_date"]
    multi  = sd != ed
    dstr   = (f"{sd.strftime('%a %d %b')} — {ed.strftime('%a %d %b %Y')}"
              if multi else sd.strftime("%A %d %B %Y"))
    total_rounds = calculate_rounds(event["max_players"])

    embed = discord.Embed(
        title=f"🏆  {event['name']}",
        description=(
            f"**Warhammer 40,000 · Tabletop Simulator · Swiss Tournament**\n"
            f"{SEP}"
        ),
        color=COLOUR_GOLD,
    )
    embed.add_field(name="📅  Date",     value=dstr,                                 inline=True)
    embed.add_field(name="⚔️  Points",   value=f"**{event['points_limit']} pts**",   inline=True)
    embed.add_field(name="👥  Players",  value=f"Max **{event['max_players']}**",     inline=True)
    embed.add_field(
        name="🗺️  Mission",
        value=f"**{m.get('name','—')}**\n*{m.get('deployment','—')}*\nLayouts: {', '.join(m.get('layouts',[]))}",
        inline=False,
    )
    embed.add_field(
        name="🎲  Format",
        value=f"Swiss · **{total_rounds} rounds** · {event['rounds_per_day']}/day",
        inline=True,
    )
    if event.get("terrain_layout"):
        embed.add_field(name="🏗️  Terrain", value=event["terrain_layout"], inline=True)
    embed.set_thumbnail(url="https://emojicdn.elk.sh/🏆?style=twitter")
    embed.set_footer(text="Express interest below  ·  List submission required to confirm your spot")
    return embed

def build_briefing_embed(event: dict, round_number: int, day_number: int,
                          players: List[dict]) -> discord.Embed:
    m = TOURNAMENT_MISSIONS.get(event["mission_code"], {})
    roster = "\n".join(
        f"{fe(p['army'])}  **{p['player_username']}**  ·  *{p['army']}*"
        for p in players
    )
    embed = discord.Embed(
        title=f"📢  Day {day_number} Briefing  —  {event['name']}",
        description=f"**Round {round_number} pairings incoming — all players to the Briefing Room!**\n{SEP}",
        color=COLOUR_GOLD,
    )
    embed.add_field(name="🗺️  Mission",    value=f"**{m.get('name','—')}**\n*{m.get('deployment','—')}*", inline=True)
    embed.add_field(name="📐  Layouts",    value=", ".join(m.get("layouts", [])),                          inline=True)
    embed.add_field(name="⚔️  Points",     value=f"**{event['points_limit']}** pts",                      inline=True)
    embed.add_field(name=f"👥  Players  ({len(players)})", value=roster or "—",                           inline=False)
    embed.set_footer(text="🔊 Join the Event Briefing Room voice channel")
    return embed

def build_pairings_embed(event: dict, round_obj: dict, games: List[dict], guild: discord.Guild) -> discord.Embed:
    rnum     = round_obj["round_number"]
    deadline = round_obj.get("deadline_at")

    embed = discord.Embed(
        title=f"⚔️  Round {rnum} Pairings  —  Day {round_obj['day_number']}",
        description=(
            f"**{event['name']}**\n"
            f"Round closes {ts(deadline)}\n"
            f"{SEP}"
        ),
        color=COLOUR_CRIMSON,
    )

    for g in games:
        if g["is_bye"]:
            embed.add_field(
                name="🎲  BYE",
                value=f"**{g['player1_username']}** — rest round\n*VP awarded at round close*",
                inline=False,
            )
            continue

        room = g.get("room_number")
        colour_bar = "█" * 3  # visual room indicator
        vc = discord.utils.find(
            lambda c: isinstance(c, discord.VoiceChannel) and c.name.startswith(GAME_ROOM_PREFIX)
                      and c.name.endswith(str(room)),
            guild.channels
        )
        room_link = f"[🔊 Join Room](<https://discord.com/channels/{guild.id}/{vc.id}>)" if vc else ""

        status_icon = {GS.PENDING: "⏳", GS.SUBMITTED: "📋", GS.COMPLETE: "✅", GS.DISPUTED: "⚠️"}.get(g["state"], "⏳")

        # Three-column VS layout — TV-bot pattern
        e1 = fe(g["player1_army"])
        e2 = fe(g["player2_army"])
        embed.add_field(
            name=f"{status_icon}  Room {room}  {room_link}",
            value=(
                f"{e1} **{g['player1_username']}**\n"
                f"*{g['player1_army']}*\n"
                f"*{g['player1_detachment']}*"
            ),
            inline=True,
        )
        embed.add_field(name="​", value="**VS**", inline=True)   # zero-width space col
        embed.add_field(
            name="​",
            value=(
                f"{e2} **{g['player2_username']}**\n"
                f"*{g['player2_army']}*\n"
                f"*{g['player2_detachment']}*"
            ),
            inline=True,
        )

    embed.set_footer(text="Use the buttons below to submit results or call a judge  ·  Buttons are per-game")
    return embed

def build_standings_embed(event: dict, standings: List[dict], final: bool = False) -> discord.Embed:
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    title  = f"🏆  Final Standings — {event['name']}" if final else f"📊  Standings — {event['name']}"
    colour = COLOUR_GOLD if final else COLOUR_SLATE

    if not standings:
        return discord.Embed(title=title, description="No results yet.", color=colour)

    header = f"{'':3} {'Player':<20} {'Army':<18} {'W':>2} {'L':>2} {'VP+':>5} {'Δ':>5}"
    sep_   = "─" * len(header)
    lines  = [f"```", header, sep_]
    for i, s in enumerate(standings, 1):
        m    = medals.get(i, f"  {i}.")
        name = s["player_username"][:18]
        army = s["army"][:16]
        lines.append(f"{m:<3} {name:<20} {army:<18} {s['wins']:>2} {s['losses']:>2} {s['vp_total']:>5} {s['vp_diff']:>+5}")
    lines.append("```")

    embed = discord.Embed(title=title, description="\n".join(lines), color=colour)
    if not final:
        rounds = db_get_rounds(event["event_id"])
        done   = sum(1 for r in rounds if r["state"] == RndS.COMPLETE)
        total  = calculate_rounds(event["max_players"])
        embed.set_footer(text=f"Round {done}/{total}  ·  Tiebreaker: VP differential  ·  Updated {datetime.utcnow().strftime('%H:%M')} UTC")
    else:
        embed.set_footer(text="Tournament complete  ·  Results submitted to Scorebot for ELO calculation")
    return embed

def build_team_standings_embed(event: dict, standings: List[dict], final: bool = False) -> discord.Embed:
    """Team-format standings table: team_points → game_points → vp_diff."""
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    fmt_label = event.get("format", "singles").replace("_", " ").title()
    title  = (f"🏆  Final Standings — {event['name']}" if final
              else f"📊  Team Standings — {event['name']}  [{fmt_label}]")
    colour = COLOUR_GOLD if final else COLOUR_SLATE

    if not standings:
        return discord.Embed(title=title, description="No results yet.", color=colour)

    header = f"{'':3} {'Team':<22} {'TP':>2} {'W':>2} {'L':>2} {'D':>2} {'GP':>4} {'VPΔ':>5}"
    sep_   = "─" * len(header)
    lines  = ["```", header, sep_]
    for i, s in enumerate(standings, 1):
        medal = medals.get(i, f"  {i}.")
        name  = s["team_name"][:20]
        lines.append(
            f"{medal:<3} {name:<22} {s.get('team_points',0):>2} "
            f"{s.get('team_wins',0):>2} {s.get('team_losses',0):>2} {s.get('team_draws',0):>2} "
            f"{s.get('game_points',0):>4} {s.get('vp_diff',0):>+5}"
        )
    lines.append("```")
    lines.append("*TP=Tournament Points  GP=Game Points  VPΔ=VP differential*")

    embed = discord.Embed(title=title, description="\n".join(lines), color=colour)
    if not final:
        rounds = db_get_rounds(event["event_id"])
        done   = sum(1 for r in rounds if r["state"] == RndS.COMPLETE)
        total  = calculate_rounds(event["max_players"])
        embed.set_footer(text=f"Round {done}/{total}  ·  Primary: Tournament Points  ·  Secondary: Game Points")
    else:
        embed.set_footer(text="Tournament complete")
    return embed

def build_spectator_dashboard_embed(event: dict, round_obj: Optional[dict],
                                     games: List[dict], standings: List[dict],
                                     guild: discord.Guild) -> discord.Embed:
    m    = TOURNAMENT_MISSIONS.get(event["mission_code"], {})
    live = round_obj and round_obj["state"] == RndS.IN_PROGRESS
    colour = COLOUR_CRIMSON if live else COLOUR_GOLD

    if round_obj:
        rnum     = round_obj["round_number"]
        deadline = round_obj.get("deadline_at")
        desc = (
            f"**Round {rnum}  ·  {m.get('name','—')}  ·  {m.get('deployment','—')}**\n"
            f"{'⏱️  Ends ' + ts(deadline) if live else '⏸️  Between rounds'}\n"
            f"{SEP}"
        )
    else:
        desc = (
            f"**{m.get('name','—')}  ·  {m.get('deployment','—')}**\n"
            f"*Event not yet started*\n{SEP}"
        )

    embed = discord.Embed(
        title=f"{'🔴 LIVE' if live else '🏆'}  {event['name']}",
        description=desc,
        color=colour,
    )

    # Match cards — TV-bot VS layout
    status_icon = {GS.PENDING:"⏳", GS.SUBMITTED:"📋", GS.COMPLETE:"✅", GS.DISPUTED:"⚠️"}
    for g in [g for g in games if not g["is_bye"]]:
        si  = status_icon.get(g["state"], "⏳")
        e1  = fe(g["player1_army"])
        e2  = fe(g["player2_army"])
        score = ""
        if g["state"] == GS.COMPLETE and g.get("player1_vp") is not None:
            score = f"\n`{g['player1_vp']} — {g['player2_vp']}`"
        embed.add_field(
            name=f"{si}  Room {g['room_number']}",
            value=f"{e1} **{g['player1_username']}**\n*{g['player1_army']}*",
            inline=True,
        )
        embed.add_field(name="​", value=f"**VS**{score}", inline=True)
        embed.add_field(
            name="​",
            value=f"{e2} **{g['player2_username']}**\n*{g['player2_army']}*",
            inline=True,
        )

    # Compact top-8 standings
    if standings:
        medals = {1:"🥇", 2:"🥈", 3:"🥉"}
        slines = []
        for i, s in enumerate(standings[:8], 1):
            medal = medals.get(i, f"`{i}.`")
            slines.append(
                f"{medal} {fe(s['army'])} **{s['player_username']}**"
                f"  {s['wins']}W {s['losses']}L  ({s['vp_diff']:+} VP)"
            )
        embed.add_field(name=f"📊  Standings  (Top {min(8,len(standings))})",
                        value="\n".join(slines), inline=False)

    embed.set_thumbnail(url="https://emojicdn.elk.sh/⚔️?style=twitter")
    embed.set_footer(text=f"🔴 LIVE  ·  Players cannot see you  ·  {datetime.utcnow().strftime('%H:%M')} UTC")
    return embed

def build_list_review_header(event: dict, regs: List[dict]) -> discord.Embed:
    submitted = [r for r in regs if r["list_text"]]
    missing   = [r for r in regs if not r["list_text"]]
    checklist = (
        "\n".join(f"✅  {fe(r['army'])} **{r['player_username']}** — *{r['army']}*" for r in submitted) +
        ("\n" if missing else "") +
        "\n".join(f"⏳  {r['player_username']}" for r in missing)
    )
    embed = discord.Embed(
        title=f"📋  Army Lists  —  {event['name']}",
        description=f"{SEP}\n{checklist}\n{SEP}",
        color=COLOUR_GOLD,
    )
    embed.add_field(name="✅  Submitted", value=str(len(submitted)), inline=True)
    embed.add_field(name="⏳  Missing",   value=str(len(missing)),   inline=True)
    embed.set_footer(text="Lists published 24h before the event  ·  Scroll down for individual lists")
    return embed

def build_player_list_embed(reg: dict, index: int) -> discord.Embed:
    army  = reg["army"]
    det   = reg["detachment"]
    emoji = fe(army)
    colour = faction_colour(army)
    list_text = reg.get("list_text") or "*No list submitted*"
    if len(list_text) > 950:
        list_text = list_text[:950] + "\n*[Truncated — contact player for full list]*"
    embed = discord.Embed(
        title=f"{emoji}  {reg['player_username']}",
        description=f"**{army}**  ·  *{det}*",
        color=colour,
    )
    embed.add_field(name="📜  Army List", value=f"```\n{list_text}\n```", inline=False)
    if reg.get("submitted_at"):
        embed.set_footer(text=f"Submitted {reg['submitted_at'].strftime('%d %b %Y  %H:%M UTC')}")
    return embed

def build_judge_queue_embed(event: dict, calls: List[dict], round_obj: Optional[dict],
                             judges: List[dict]) -> discord.Embed:
    open_calls = [c for c in calls if c["state"] == JCS.OPEN]
    ack_calls  = [c for c in calls if c["state"] == JCS.ACKNOWLEDGED]

    if not calls:
        colour = discord.Color.green()
        title  = "⚖️  Judge Queue  —  All Clear"
        desc   = "*No active judge calls.*"
    elif len(calls) <= 2:
        colour = COLOUR_AMBER
        title  = f"⚖️  Judge Queue  —  {len(calls)} Call{'s' if len(calls)!=1 else ''}"
        desc   = ""
    else:
        colour = COLOUR_CRIMSON
        title  = f"⚖️  Judge Queue  —  {len(calls)} Calls ⚠️"
        desc   = f"**{len(open_calls)} waiting · {len(ack_calls)} in progress**"

    embed = discord.Embed(title=title, description=desc, color=colour)

    # ── Judge roster ───────────────────────────────────────────────────────
    if judges:
        roster_lines = []
        for j in judges:
            if j.get("call_id"):
                roster_lines.append(f"🔴  **{j['name']}** — Room {j['room'] or '?'}")
            else:
                roster_lines.append(f"🟢  **{j['name']}** — Available")
        embed.add_field(
            name=f"👥  Judges on Duty  ({len(judges)})",
            value="\n".join(roster_lines) or "—",
            inline=False,
        )

    # ── Individual calls ───────────────────────────────────────────────────
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    for i, c in enumerate(calls, 1):
        raised = c["raised_at"]
        if raised.tzinfo is None:
            raised = raised.replace(tzinfo=timezone.utc)
        waiting_secs = int((now - raised).total_seconds())
        mins, secs   = divmod(waiting_secs, 60)
        wait_str     = f"{mins}m {secs}s"

        if c["state"] == JCS.OPEN:
            icon   = "🔔"
            status = "*Waiting for judge...*"
        else:
            icon   = "🚶"
            judge_name = c.get("acknowledged_by_name") or "Judge"
            status = f"**{judge_name}** en route"

        embed.add_field(
            name=f"{icon}  #{i}  Room {c['room_number']}  —  {wait_str}",
            value=f"Raised by **{c['raised_by_name']}**\n{status}",
            inline=True,
        )
        # zero-width spacer to keep pairs on same row for ≤2 calls
        if i % 2 == 0 and i < len(calls):
            embed.add_field(name="​", value="​", inline=True)

    if round_obj and round_obj.get("deadline_at"):
        embed.set_footer(
            text=f"Round {round_obj['round_number']} · "
                 f"closes {round_obj['deadline_at'].strftime('%H:%M UTC')}"
        )
    return embed

