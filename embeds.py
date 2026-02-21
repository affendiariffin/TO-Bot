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
  • bbuild_standings_embed


Imported by: services.py, views.py, commands_*.py
"""
import discord
from datetime import datetime
from typing import List, Optional
from config import (COLOUR_GOLD, COLOUR_CRIMSON, COLOUR_AMBER, COLOUR_SLATE,
                    SEP, GAME_ROOM_PREFIX, TOURNAMENT_MISSIONS,
                    fe, faction_colour, room_colour, ts, ts_full)
from state import GS, RndS, JCS, FMT, get_judges_for_guild
from database import db_get_rounds
from threads import calculate_rounds

# ══════════════════════════════════════════════════════════════════════════════
# EMBED BUILDERS  —  TV-bot design language
# ══════════════════════════════════════════════════════════════════════════════

def vp_bar(vp: int, max_vp: int = 120, width: int = 10) -> str:
    """Unicode VP progress bar.
    e.g. vp_bar(85) → ▓▓▓▓▓▓▓░░░  85 VP"""
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
    from state import get_judges_for_guild
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
def _round_slot(round_data: dict | None) -> str:
    """
    Raw slot string before right-justification.
    Max 4 chars: "100W", "100L", "100D".
    "-" only for a game that existed in a completed round but has no confirmed result.
    """
    if not round_data or round_data["vp"] is None:
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
    from threads  import calculate_rounds

    live   = bool(round_obj and round_obj["state"] == RndS.IN_PROGRESS)
    colour = COLOUR_CRIMSON if live else COLOUR_GOLD

    rounds       = db_get_rounds(event["event_id"])
    done_rounds  = sum(1 for r in rounds if r["state"] == RndS.COMPLETE)
    total_rounds = calculate_rounds(event["max_players"])

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
    Standalone /standings command embed — same VP-per-round format as the
    dashboard, with total VP and VP diff appended for the full detailed view.
    """
    from database import db_get_results_by_player
    from threads  import calculate_rounds

    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    title  = f"🏆  Final Standings — {event['name']}" if final else f"📊  Standings — {event['name']}"
    colour = COLOUR_GOLD if final else COLOUR_SLATE

    if not standings:
        return discord.Embed(title=title, description="No results yet.", color=colour)

    rounds       = db_get_rounds(event["event_id"])
    done_rounds  = sum(1 for r in rounds if r["state"] == RndS.COMPLETE)
    total_rounds = calculate_rounds(event["max_players"])
    p_results    = db_get_results_by_player(event["event_id"])

    max_name = max((len(s["player_username"]) for s in standings), default=8)
    name_w   = min(max_name, 18)
    SLOT_W   = 4

    lines = []
    for i, s in enumerate(standings, 1):
        pid    = s["player_id"]
        name   = s["player_username"][:name_w].ljust(name_w)
        medal  = medals.get(i, f"{i:>2}.")
        vp_tot = s.get("vp_total", 0)
        vdiff  = s.get("vp_diff", 0)

        pr    = p_results.get(pid, {})
        slots = [_round_slot(pr.get(rn)).rjust(SLOT_W) for rn in range(1, total_rounds + 1)]
        score_str = " / ".join(slots)

        lines.append(f"`{medal} {name}  {score_str}   {vp_tot}VP ({vdiff:+})`")

    embed = discord.Embed(title=title, description="\n".join(lines), color=colour)

    if not final:
        embed.set_footer(
            text=(
                f"Round {done_rounds}/{total_rounds}  ·  "
                f"Format: VP scored + W/L/D per round  ·  "
                f"Last updated {datetime.utcnow().strftime('%H:%M')} UTC"
            )
        )
    else:
        embed.set_footer(text="Tournament complete  ·  Results submitted to Scorebot for ELO calculation")

    return embed

def build_standings_embed(event: dict, standings: List[dict], final: bool = False) -> discord.Embed:
    """
    /standings command embed — completed rounds only, with total VP and VP diff.
    """
    from database import db_get_results_by_player
    from threads  import calculate_rounds

    title  = f"🏆  Final Standings — {event['name']}" if final else f"📊  Standings — {event['name']}"
    colour = COLOUR_GOLD if final else COLOUR_SLATE

    if not standings:
        return discord.Embed(title=title, description="No results yet.", color=colour)

    rounds       = db_get_rounds(event["event_id"])
    done_rounds  = sum(1 for r in rounds if r["state"] == RndS.COMPLETE)
    total_rounds = calculate_rounds(event["max_players"])
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
