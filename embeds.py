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
  • build_list_review_header
  • build_player_list_embed
  • build_judges_on_duty_embed
  • build_spectator_dashboard_embed
  • build_event_main_embed
  • build_schedule_embed
  • build_missions_embed

Imported by: services.py, views.py, commands_*.py
"""
import discord
import math
from datetime import datetime, timedelta
from typing import List, Optional
from config import (COLOUR_GOLD, COLOUR_CRIMSON, COLOUR_AMBER, COLOUR_SLATE,
                    SEP, GAME_ROOM_PREFIX,
                    fe, faction_colour, room_colour, ts, ts_full)
from state import GS, RndS, JCS, FMT, get_judges_for_guild
from database import db_get_rounds, db_get_mission

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def vp_bar(vp: int, max_vp: int = 120, width: int = 10) -> str:
    """Unicode VP progress bar.
    e.g. vp_bar(85) → ▓▓▓▓▓▓▓░░░  85 VP"""
    if not max_vp:
        return ""
    filled = round((vp / max_vp) * width)
    filled = max(0, min(width, filled))
    return f"{'▓' * filled}{'░' * (width - filled)}  {vp}"

def _round_slot(round_data: dict | None) -> str:
    """
    Raw slot string before right-justification.
    Format: "<vp><result>" e.g. "85W", "42L", "60D".
    Max 4 chars ("100W"). Returns "-" when game is unconfirmed or missing.

    Expects round_data = {"vp": int | None, "result": "W"|"L"|"D"|None}
    as produced by db_get_results_by_player.
    """
    if not round_data or round_data.get("vp") is None:
        return "-"
    vp     = round_data["vp"]
    result = round_data.get("result") or ""
    suffix = result if result in ("W", "L", "D") else ""
    return f"{vp}{suffix}"

def _standings_table(
    standings: List[dict],
    done_rounds: int,
    player_results: dict,
    include_totals: bool = False,
) -> str:
    """
    Monospace standings table in a code block.
    Renders exactly done_rounds slot columns — never more.
    """
    if not standings:
        return "*No results yet.*"

    # Pre-event or between Round 1 — just show roster order
    if done_rounds == 0:
        return "```\n" + "\n".join(
            f"{(str(i) + '.').rjust(3)} {s['player_username'][:18]}"
            for i, s in enumerate(standings, 1)
        ) + "\n```"

    SLOT_W   = 4   # "100W" is the widest possible value
    max_name = max((len(s["player_username"]) for s in standings), default=8)
    name_w   = min(max_name, 18)

    lines = []
    for i, s in enumerate(standings, 1):
        pid  = s["player_id"]
        pos  = f"{i}.".rjust(3)
        name = s["player_username"][:name_w].ljust(name_w)

        pr    = player_results.get(pid, {})
        slots = [
            _round_slot(pr.get(rn)).rjust(SLOT_W)
            for rn in range(1, done_rounds + 1)
        ]
        score_str = " / ".join(slots)

        if include_totals:
            vp_tot = s.get("vp_total", 0)
            vdiff  = s.get("vp_diff", 0)
            totals = f"  {vp_tot:>3}VP ({vdiff:>+4})"
        else:
            totals = ""

        lines.append(f"{pos} {name}  {score_str}{totals}")

    return "```\n" + "\n".join(lines) + "\n```"

# ══════════════════════════════════════════════════════════════════════════════
# EMBED BUILDERS  —  TV-bot design language
# ══════════════════════════════════════════════════════════════════════════════

def build_event_main_embed(event: dict, regs: list) -> discord.Embed:
    """
    Card 1 — Event Main Details.
    Pinned by admin in #event-noticeboard.
    Shows core event info + current player roster.
    """
    m           = db_get_mission(event["mission_code"])
    sd, ed      = event["start_date"], event["end_date"]
    multi       = sd != ed
    dstr        = (f"{sd.strftime('%a %d %b')} — {ed.strftime('%a %d %b %Y')}"
                   if multi else sd.strftime("%A %d %B %Y"))
    total_rounds = event.get("round_count", 3)
    fmt_label   = {
        "singles": "Singles", "2v2": "2v2",
        "teams_3": "Teams 3s", "teams_5": "Teams 5s", "teams_8": "Teams 8s",
    }.get(event.get("format", "singles"), "Singles")

    approved = [r for r in regs if r["state"] == "approved"]
    roster   = "\n".join(
        f"{fe(r['army'])}  **{r['player_username']}**  ·  *{r['army']}*"
        for r in approved
    ) or "*No players confirmed yet.*"

    embed = discord.Embed(
        title=f"🏆  {event['name']}",
        description=f"**Warhammer 40,000  ·  Tabletop Simulator  ·  {fmt_label}**\n{SEP}",
        color=COLOUR_GOLD,
    )
    embed.add_field(name="📅  Date",    value=dstr,                               inline=True)
    embed.add_field(name="⚔️  Points",  value=f"**{event['points_limit']} pts**", inline=True)
    embed.add_field(name="🎲  Format",  value=f"Swiss · **{total_rounds} rounds** · {event['rounds_per_day']}/day", inline=True)
    embed.add_field(name=f"👥  Players  ({len(approved)}/{event['max_players']})",
                    value=roster, inline=False)
    if event.get("terrain_layout"):
        embed.add_field(name="🏗️  Terrain", value=event["terrain_layout"], inline=False)
    embed.set_footer(text="Pinned  ·  Updated by crew as registrations are confirmed")
    return embed

def build_schedule_embed(event: dict) -> discord.Embed:
    """
    Card 2 — Schedule.
    Pinned by admin. Shows day/round breakdown and key times.
    Admin edits the message manually if times change.
    """
    total_rounds = event.get("round_count", 3)
    rpd          = event["rounds_per_day"]
    days         = math.ceil(total_rounds / rpd)
    sd           = event["start_date"]

    lines = []
    round_counter = 1
    for d in range(1, days + 1):
        rounds_today = []
        for _ in range(rpd):
            if round_counter > total_rounds:
                break
            rounds_today.append(f"Round {round_counter}")
            round_counter += 1
        day_label = (sd + timedelta(days=d - 1)).strftime("%a %d %b") if hasattr(sd, "strftime") else f"Day {d}"
        lines.append(f"**Day {d}  ·  {day_label}**\n" + "  ·  ".join(rounds_today))

    embed = discord.Embed(
        title=f"📅  Schedule  —  {event['name']}",
        description="\n\n".join(lines) or "*Schedule not yet set.*",
        color=COLOUR_GOLD,
    )
    embed.set_footer(text="Pinned  ·  All times in UTC  ·  Check #event-noticeboard for round updates")
    return embed

def build_missions_embed(event: dict) -> discord.Embed:
    """
    Card 3 — Missions.
    Singles/2v2: shows the full round-by-round pairings table set at creation.
    Team formats: shows the layout + mission pools captains draw from each ritual.
    """
    fmt = event.get("format", "singles")

    embed = discord.Embed(
        title=f"🗺️  Mission Pack  —  {event['name']}",
        color=COLOUR_GOLD,
    )

    if fmt in ("singles", "2v2"):
        pairings = event.get("event_pairings") or []
        if pairings:
            lines = []
            for i, p in enumerate(pairings):
                m = db_get_mission(p["mission"])
                lines.append(
                    f"**Round {i+1}**  ·  Layout **{p['layout']}**  ·  "
                    f"{m.get('name','?')}  (*{m.get('deployment','?')}*)"
                )
            embed.add_field(name="📋  Round Pairings", value="\n".join(lines), inline=False)
        else:
            # Legacy fallback: single mission_code
            m = db_get_mission(event["mission_code"])
            embed.add_field(name="Mission",    value=f"**{m.get('name', '—')}**",           inline=True)
            embed.add_field(name="Deployment", value=f"*{m.get('deployment', '—')}*",        inline=True)
            embed.add_field(name="Layouts",    value=", ".join(m.get("layouts", [])) or "—", inline=False)
    else:
        layouts  = event.get("event_layouts")  or []
        missions = event.get("event_missions") or []
        layout_str  = ", ".join(f"Layout {l}" for l in layouts)  if layouts  else "⚠️ not configured"
        mission_str = ", ".join(missions)                          if missions else "⚠️ not configured"
        embed.add_field(name="🗺️  Layout pool",  value=layout_str,  inline=False)
        embed.add_field(name="🎯  Mission pool", value=mission_str, inline=False)
        embed.add_field(
            name="ℹ️  Selection",
            value="Captains choose layout and mission each round via the ritual.",
            inline=False,
        )

    scoring = (
        "**Primary:** Secure objectives as per mission rules\n"
        "**Secondary:** Chosen from your detachment's secondary options\n"
        "**Max VP:** 100 per game"
    )
    embed.add_field(name="⚔️  Scoring", value=scoring, inline=False)
    embed.set_footer(text="Pinned  ·  Refer to the current matched play rules pack for full details")
    return embed

def build_event_announcement_embed(event: dict) -> discord.Embed:
    m = db_get_mission(event["mission_code"])
    sd, ed = event["start_date"], event["end_date"]
    multi  = sd != ed
    dstr   = (f"{sd.strftime('%a %d %b')} — {ed.strftime('%a %d %b %Y')}"
              if multi else sd.strftime("%A %d %B %Y"))
    total_rounds = event.get("round_count", 3)
    fmt = event.get("format", "singles")
    fmt_label = {
        "singles": "Singles", "2v2": "2v2",
        "teams_3": "Teams 3s", "teams_5": "Teams 5s", "teams_8": "Teams 8s",
    }.get(fmt, "Singles")

    embed = discord.Embed(
        title=f"🏆  {event['name']}",
        description=(
            f"**Warhammer 40,000 · Tabletop Simulator · Swiss Tournament**\n"
            f"**{fmt_label}  ·  {event['points_limit']} pts  ·  {total_rounds} rounds**\n"
            f"{SEP}"
        ),
        color=COLOUR_GOLD,
    )
    embed.add_field(name="📅  Date",     value=dstr,                              inline=True)
    embed.add_field(name="👥  Players",  value=f"Max **{event['max_players']}**", inline=True)
    embed.add_field(name="🎲  Schedule", value=f"{total_rounds} rounds · {event['rounds_per_day']}/day", inline=True)

    # Mission / layout block — format-dependent
    if fmt in ("singles", "2v2"):
        pairings = event.get("event_pairings") or []
        if pairings:
            lines = [f"R{i+1}  ·  Layout **{p['layout']}**  ·  {p['mission']}" for i, p in enumerate(pairings)]
            embed.add_field(
                name=f"🗺️  Round Pairings ({len(pairings)} rounds)",
                value="\n".join(lines),
                inline=False,
            )
        else:
            # Fallback: show single mission as before
            embed.add_field(
                name="🗺️  Mission",
                value=f"**{m.get('name','—')}**\n*{m.get('deployment','—')}*\nLayouts: {', '.join(m.get('layouts',[]))}",
                inline=False,
            )
    else:
        # Team formats: show pools
        layouts  = event.get("event_layouts") or []
        missions = event.get("event_missions") or []
        layout_str  = ", ".join(f"Layout {l}" for l in layouts)  if layouts  else "⚠️ not set"
        mission_str = ", ".join(missions)                          if missions else "⚠️ not set"
        embed.add_field(name="🗺️  Layout pool",  value=layout_str,  inline=True)
        embed.add_field(name="🎯  Mission pool", value=mission_str, inline=True)

    if event.get("terrain_layout"):
        embed.add_field(name="🏗️  Terrain", value=event["terrain_layout"], inline=False)
    embed.set_thumbnail(url="https://emojicdn.elk.sh/🏆?style=twitter")
    embed.set_footer(text="Express interest below  ·  List submission required to confirm your spot")
    return embed

def build_briefing_embed(event: dict, round_number: int, day_number: int,
                          players: List[dict]) -> discord.Embed:
    m = db_get_mission(event["mission_code"])
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
        vc = discord.utils.find(
            lambda c: isinstance(c, discord.VoiceChannel) and c.name.startswith(GAME_ROOM_PREFIX)
                      and c.name.endswith(str(room)),
            guild.channels
        )
        room_link = f"[🔊 Join Room](<https://discord.com/channels/{guild.id}/{vc.id}>)" if vc else ""

        status_icon = {GS.PENDING: "⏳", GS.SUBMITTED: "📋", GS.COMPLETE: "✅", GS.DISPUTED: "⚠️"}.get(g["state"], "⏳")

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
        embed.add_field(name="​", value="**VS**", inline=True)
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

def build_spectator_dashboard_embed(
    event: dict,
    round_obj: Optional[dict],
    games: List[dict],
    standings: List[dict],
    guild: discord.Guild,
) -> discord.Embed:
    """
    Pinned card in #what's-playing-now.
    Shows round status + VP standings for completed rounds only.
    """
    from database import db_get_results_by_player

    live   = bool(round_obj and round_obj["state"] == RndS.IN_PROGRESS)
    colour = COLOUR_CRIMSON if live else COLOUR_GOLD

    rounds       = db_get_rounds(event["event_id"])
    done_rounds  = sum(1 for r in rounds if r["state"] == RndS.COMPLETE)
    total_rounds = event.get("round_count", 3)

    # ── Title + status ────────────────────────────────────────────────────
    if round_obj:
        rnum = round_obj["round_number"]
        if live:
            deadline = round_obj.get("deadline_at")
            status   = f"⏱️  Round ends {ts(deadline)}" if deadline else "🔴  In progress"
        else:
            status = "⏸️  Between rounds"
        title = f"{'🔴 LIVE' if live else '📊'}  {event['name']}  —  Round {rnum}"
    else:
        status = "*Waiting for first round*"
        title  = f"🏆  {event['name']}"

    embed = discord.Embed(title=title, description=status, color=colour)

    # ── Standings ─────────────────────────────────────────────────────────
    field_name = (
        f"📊  Standings  (after Round {done_rounds} of {total_rounds})"
        if done_rounds else "📊  Standings"
    )
    if standings:
        player_results = db_get_results_by_player(event["event_id"])
        table = _standings_table(standings, done_rounds, player_results)
        embed.add_field(name=field_name, value=table, inline=False)
    else:
        embed.add_field(name=field_name, value="*No results yet.*", inline=False)

    # ── Footer ────────────────────────────────────────────────────────────
    parts = []
    if done_rounds > 0:
        parts.append(f"After Round {done_rounds}")
    parts.append(f"Last updated {datetime.utcnow().strftime('%H:%M')} UTC")
    embed.set_footer(text="  ·  ".join(parts))

    return embed

def build_standings_embed(event: dict, standings: List[dict], final: bool = False) -> discord.Embed:
    """
    /standings command embed — completed rounds only, with total VP and VP diff.
    """
    from database import db_get_results_by_player

    title  = f"🏆  Final Standings — {event['name']}" if final else f"📊  Standings — {event['name']}"
    colour = COLOUR_GOLD if final else COLOUR_SLATE

    if not standings:
        return discord.Embed(title=title, description="No results yet.", color=colour)

    rounds       = db_get_rounds(event["event_id"])
    done_rounds  = sum(1 for r in rounds if r["state"] == RndS.COMPLETE)
    total_rounds = event.get("round_count", 3)
    p_results    = db_get_results_by_player(event["event_id"])

    table = _standings_table(standings, done_rounds, p_results, include_totals=True)
    embed = discord.Embed(title=title, description=table, color=colour)

    if not final:
        embed.set_footer(
            text=(
                f"Round {done_rounds}/{total_rounds}  ·  "
                f"Format: VP + W/L/D per completed round  ·  "
                f"Last updated {datetime.utcnow().strftime('%H:%M')} UTC"
            )
        )
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
        total  = event.get("round_count", 3)
        embed.set_footer(text=f"Round {done}/{total}  ·  Primary: Tournament Points  ·  Secondary: Game Points")
    else:
        embed.set_footer(text="Tournament complete")
    return embed

def build_list_review_header(event: dict, regs: list) -> discord.Embed:
    """
    Header card for the Army Lists thread.
    Submission checklist — submitted vs missing.
    """
    submitted = [r for r in regs if r.get("list_text")]
    missing   = [r for r in regs if not r.get("list_text")]

    lines = []
    for r in submitted:
        lines.append(f"✅  {fe(r['army'])} **{r['player_username']}**")
    for r in missing:
        lines.append(f"⏳  **{r['player_username']}** — *list not submitted*")

    embed = discord.Embed(
        title=f"📋  Army Lists  —  {event['name']}",
        description="\n".join(lines) or "*No registrations found.*",
        color=COLOUR_GOLD,
    )
    embed.add_field(name="✅  Submitted", value=str(len(submitted)), inline=True)
    embed.add_field(name="⏳  Missing",   value=str(len(missing)),   inline=True)
    embed.set_footer(text="Individual lists follow below  ·  Scroll down to review")
    return embed

def build_player_list_embed(reg: dict, index: int) -> discord.Embed:
    """
    Individual army list card in the Army Lists thread.
    Long names are handled — embed title truncates gracefully,
    full name shown inside the description.
    """
    army      = reg["army"]
    det       = reg["detachment"]
    emoji     = fe(army)
    colour    = faction_colour(army)
    full_name = reg["player_username"]
    # Truncate only the embed title (Discord limit 256), keep full name in body
    title_name = full_name[:50] + ("…" if len(full_name) > 50 else "")

    list_text = reg.get("list_text") or "*No list submitted*"
    # Discord field value limit is 1024 chars
    if len(list_text) > 950:
        list_text = list_text[:950] + "\n*[truncated — contact player for full list]*"

    embed = discord.Embed(
        title=f"{emoji}  {title_name}",
        description=f"**{full_name}**\n{army}  ·  *{det}*",
        color=colour,
    )
    embed.add_field(name="📜  Army List", value=f"```\n{list_text}\n```", inline=False)
    if reg.get("submitted_at"):
        embed.set_footer(text=f"Submitted {reg['submitted_at'].strftime('%d %b %Y  %H:%M UTC')}")
    return embed

def build_judges_on_duty_embed(
    guild: discord.Guild,
    round_obj: Optional[dict] = None,
) -> discord.Embed:
    """
    Read-only Judges on Duty card.

    Availability is derived purely from voice channel presence:
      🟢 available  — not in any Game Room (safe to DM)
      🔵 in-game    — currently in a Game Room (judging a match)

    Players should DM a 🟢 available judge directly.
    VP adjustments applied by crew via /result adjust after the round.
    """
    judges    = get_judges_for_guild(guild)
    available = [j for j in judges if j["available"]]
    in_game   = [j for j in judges if not j["available"]]

    # Card colour reflects whether anyone is free
    if not judges:
        colour = COLOUR_SLATE
    elif available:
        colour = discord.Color.green()
    else:
        colour = COLOUR_AMBER

    title = "⚖️  Judges on Duty"

    # Build roster lines
    lines: list[str] = []
    for j in available:
        lines.append(f"🟢  **{j['name']}** — available  ·  {j['mention']}")
    for j in in_game:
        lines.append(f"🔵  **{j['name']}** — {j['room']}")
    if not judges:
        lines = ["*No judges currently assigned.*"]

    embed = discord.Embed(
        title=title,
        description="\n".join(lines),
        color=colour,
    )

    if available:
        embed.add_field(
            name="📩  Need a ruling?",
            value=(
                "DM a 🟢 available judge directly.\n"
                "They will come to your Game Room."
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="📩  All judges are in a game",
            value=(
                "Wait for a judge to finish their current room — "
                "this card updates automatically."
            ),
            inline=False,
        )

    # Footer — round info + last updated timestamp
    parts = []
    if round_obj:
        parts.append(f"Round {round_obj['round_number']}")
    parts.append(f"Last updated {datetime.utcnow().strftime('%H:%M')} UTC")
    embed.set_footer(text="  ·  ".join(parts))

    return embed
