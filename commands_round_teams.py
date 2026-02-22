"""
commands_round_teams.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Team-event round management commands.

Commands:
  • /round pair-teams     — team Swiss pairings (added to round_grp from commands_round)
  • /result-team submit   — captain submits team round scores
  • /team-standings       — view team standings

Also contains:
  • TeamScoreModal
"""
import discord
from discord import app_commands, ui
import asyncio
import math
import re
from datetime import datetime, timedelta, timezone
from config import (GUILD, GUILD_ID, EVENT_NOTICEBOARD_ID, COLOUR_GOLD,
                    COLOUR_CRIMSON, COLOUR_AMBER, COLOUR_SLATE,
                    fe, ts, GAME_ROOM_PREFIX)
from state import ES, RndS, TRS, FMT, TS, GS, is_to
from database import *
from threads import (ensure_round_thread, team_swiss_pair,
                     get_previous_team_pairings, ntl_team_result,
                     event_round_count, db_get_team_standings,
                     db_upsert_team_standing, db_apply_team_result)
from embeds import build_team_standings_embed, build_standings_embed
from views import PairingActionView
from services import (refresh_spectator_dashboard, ac_active_events, log_immediate)

# Import round_grp so we can add /round pair-teams as a subcommand
from commands_round import round_grp

# ══════════════════════════════════════════════════════════════════════════════
# /round pair-teams  — added to the shared round_grp
# ══════════════════════════════════════════════════════════════════════════════

@round_grp.command(name="pair-teams", description="[TO] Generate team Swiss pairings and start the round")
@app_commands.describe(event_id="Select event", duration_minutes="Round duration in minutes (default 120)")
@app_commands.autocomplete(event_id=ac_active_events)
async def round_pair_teams(interaction: discord.Interaction, event_id: str, duration_minutes: int = 120):
    if not is_to(interaction):
        await interaction.response.send_message("❌ Crew only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)

    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True); return
    fmt = event.get("format", "singles")
    if not FMT.is_team(fmt):
        await interaction.followup.send("❌ This is a Singles event. Use `/round start` instead.", ephemeral=True); return

    ready_teams = [t for t in db_get_teams(event_id) if t["state"] == TS.READY]
    if len(ready_teams) < 2:
        await interaction.followup.send("❌ Need at least 2 ready teams.", ephemeral=True); return

    existing   = db_get_rounds(event_id)
    round_num  = len(existing) + 1
    max_rounds = event_round_count(event)
    if round_num > max_rounds:
        await interaction.followup.send(f"❌ All {max_rounds} rounds complete.", ephemeral=True); return

    day_num  = math.ceil(round_num / event["rounds_per_day"])
    deadline = datetime.utcnow() + timedelta(minutes=duration_minutes)
    round_id = db_create_round(event_id, round_num, day_num, deadline)
    db_update_round(round_id, {"state": RndS.IN_PROGRESS, "started_at": datetime.utcnow()})

    team_standings = db_get_team_standings(event_id)
    if not team_standings:
        for t in ready_teams:
            db_upsert_team_standing(event_id, t["team_id"], t["team_name"])
        team_standings = db_get_team_standings(event_id)

    previous       = get_previous_team_pairings(event_id)
    pairings, bye_team = team_swiss_pair(team_standings, previous)

    ch            = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    summary_lines = []

    for team_a_data, team_b_data in pairings:
        team_a = db_get_team(team_a_data["team_id"])
        team_b = db_get_team(team_b_data["team_id"])
        trid   = db_create_team_round(round_id, event_id, team_a["team_id"], team_b["team_id"])

        if fmt == FMT.TWO_V_TWO:
            members_a    = db_get_team_members(team_a["team_id"])
            members_b    = db_get_team_members(team_b["team_id"])
            non_subs_a   = [m for m in members_a if m["role"] != "substitute"]
            non_subs_b   = [m for m in members_b if m["role"] != "substitute"]

            room_nums = sorted([
                n for n in [
                    (lambda m: int(m.group(1)) if m else None)(re.search(r"(\d+)\s*$", c.name))
                    for c in interaction.guild.voice_channels if c.name.startswith(GAME_ROOM_PREFIX)
                ] if n is not None
            ])
            room_offset = len(summary_lines)

            for slot_idx, (ma, mb) in enumerate(zip(non_subs_a, non_subs_b)):
                room = room_nums[room_offset * 2 + slot_idx] if (room_offset * 2 + slot_idx) < len(room_nums) else None
                gid  = db_create_game({
                    "round_id": round_id, "event_id": event_id, "room_number": room,
                    "player1_id": ma["player_id"], "player1_username": ma["player_username"],
                    "player1_army": ma["army"] or "Unknown", "player1_detachment": ma["detachment"] or "Unknown",
                    "player2_id": mb["player_id"], "player2_username": mb["player_username"],
                    "player2_army": mb["army"] or "Unknown", "player2_detachment": mb["detachment"] or "Unknown",
                })
                pid_row = db_create_team_pairing(trid, slot_idx + 1)
                db_update_team_pairing(pid_row, {
                    "game_id": gid,
                    "defender_player_id": ma["player_id"], "defender_team_id": team_a["team_id"],
                    "attacker_player_id": mb["player_id"], "attacker_team_id": team_b["team_id"],
                })
                for m in (ma, mb):
                    db_upsert_standing(event_id, m["player_id"], m["player_username"],
                                        m["army"] or "Unknown", m["detachment"] or "Unknown")

        summary_lines.append(
            f"⚔️ **{team_a['team_name']}**  vs  **{team_b['team_name']}**"
            + ("  *(2v2 auto-assigned)*" if fmt == FMT.TWO_V_TWO else "  *(pairing ritual required)*")
        )

    bye_note = ""
    if bye_team:
        bye_tid    = bye_team["team_id"]
        bye_obj    = db_get_team(bye_tid)
        db_create_team_round(round_id, event_id, bye_tid, None)
        bye_gp = round(80 * (FMT.team_size(fmt) * 20) / 160)
        db_apply_team_result(event_id, bye_tid, 2, bye_gp, 0, True, False)
        bye_note = f"\n\n🎲 **{bye_obj['team_name']}** has a bye this round and receives a walkover win ({bye_gp} GP)."

    round_thread = await ensure_round_thread(interaction.client, event_id, round_num, interaction.guild, event["name"])

    if ch:
        embed = discord.Embed(
            title=f"⚔️  Round {round_num} Pairings — {event['name']}",
            description="\n".join(summary_lines) + bye_note,
            color=COLOUR_CRIMSON,
        )
        embed.add_field(name="⏰ Deadline", value=ts(deadline), inline=True)
        embed.add_field(name="Format", value=fmt.replace("_", " ").title(), inline=True)
        msg = await ch.send(embed=embed)
        db_update_round(round_id, {"pairings_msg_id": str(msg.id)})

        if fmt == FMT.TWO_V_TWO:
            btn_target = round_thread or ch
            games = db_get_games(round_id)
            for g in games:
                if g["is_bye"]: continue
                view = PairingActionView(g["game_id"], event_id, g["room_number"])
                await btn_target.send(
                    f"**Room {g['room_number']}  ·  {g['player1_username']}** {fe(g['player1_army'])} **vs** "
                    f"{fe(g['player2_army'])} **{g['player2_username']}**",
                    view=view,
                )
                await asyncio.sleep(0.3)

    await interaction.followup.send(
        f"✅ Round {round_num} pairings generated — {len(pairings)} matchup(s).\n"
        + "\n".join(summary_lines) + bye_note,
        ephemeral=True,
    )
    await log_immediate(interaction.client, f"Team Round {round_num} Started",
        f"Event: **{event['name']}** · {len(pairings)} team matchups",
        COLOUR_CRIMSON)

# ══════════════════════════════════════════════════════════════════════════════
# RESULT-TEAM GROUP
# ══════════════════════════════════════════════════════════════════════════════

result_team_grp = app_commands.Group(
    name="result-team",
    description="Submit and manage team round results",
    guild_ids=[GUILD_ID],
)

class TeamScoreModal(ui.Modal, title="Submit Team Round Scores"):
    score_a = ui.TextInput(label="Your team's total GP", placeholder="e.g. 90", required=True, max_length=3)
    score_b = ui.TextInput(label="Opponent team's total GP", placeholder="e.g. 70", required=True, max_length=3)

    def __init__(self, team_round_id: str, event_id: str, team_id: str,
                  team_name: str, opp_name: str, fmt: str):
        super().__init__(title=f"Scores: {team_name} vs {opp_name}")
        self.team_round_id = team_round_id
        self.event_id      = event_id
        self.team_id       = team_id
        self.team_name     = team_name
        self.opp_name      = opp_name
        self.fmt           = fmt
        self.score_a.label = f"{team_name} total GP"
        self.score_b.label = f"{opp_name} total GP"

    async def on_submit(self, interaction: discord.Interaction):
        try:
            gp_a = int(self.score_a.value.strip())
            gp_b = int(self.score_b.value.strip())
            if gp_a < 0 or gp_b < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Please enter valid non-negative integers.", ephemeral=True)
            return

        tr = db_get_team_round(self.team_round_id)
        if not tr or tr["state"] == TRS.COMPLETE:
            await interaction.response.send_message("❌ This team round is already complete.", ephemeral=True)
            return

        team_sz = FMT.team_size(self.fmt)
        max_gp  = team_sz * 20

        tp_a, result_a = ntl_team_result(gp_a, max_gp)
        tp_b, result_b = ntl_team_result(gp_b, max_gp)
        if tp_a == tp_b == 2:
            if gp_a >= gp_b: tp_b = 0
            else: tp_a = 0

        is_win_a  = tp_a == 2
        is_draw_a = tp_a == 1

        db_update_team_round(self.team_round_id, {
            "team_a_score": gp_a,
            "team_b_score": gp_b,
            "team_a_win":   is_win_a,
            "state":        TRS.COMPLETE,
        })

        db_apply_team_result(self.event_id, tr["team_a_id"], tp_a, gp_a, gp_a - gp_b, is_win_a, is_draw_a)
        db_apply_team_result(self.event_id, tr["team_b_id"], tp_b, gp_b, gp_b - gp_a,
                              tp_b == 2, tp_b == 1)

        icon_a = "🏆" if is_win_a else ("🤝" if is_draw_a else "❌")
        icon_b = "🏆" if tp_b == 2 else ("🤝" if tp_b == 1 else "❌")

        ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID) if interaction.guild else None
        if ch:
            await ch.send(
                embed=discord.Embed(
                    title="📊 Team Round Result",
                    description=(
                        f"{icon_a} **{self.team_name}** — {gp_a} GP  ({result_a}, {tp_a} TP)\n"
                        f"{icon_b} **{self.opp_name}** — {gp_b} GP  ({result_b}, {tp_b} TP)"
                    ),
                    color=COLOUR_GOLD if not is_draw_a else COLOUR_AMBER,
                )
            )

        await interaction.response.send_message(
            f"✅ Result recorded!\n"
            f"{icon_a} **{self.team_name}**: {gp_a} GP → {result_a} ({tp_a} TP)\n"
            f"{icon_b} **{self.opp_name}**: {gp_b} GP → {result_b} ({tp_b} TP)",
            ephemeral=True,
        )
        db_queue_log(f"Team result: {self.team_name} {gp_a} GP vs {self.opp_name} {gp_b} GP", self.event_id)


@result_team_grp.command(name="submit", description="[Captain/TO] Submit team round scores")
@app_commands.describe(event_id="The event")
@app_commands.autocomplete(event_id=ac_active_events)
async def result_team_submit(interaction: discord.Interaction, event_id: str):
    event = db_get_event(event_id)
    if not event:
        await interaction.response.send_message("❌ Event not found.", ephemeral=True); return

    pid  = str(interaction.user.id)
    team = db_get_team_by_player(event_id, pid)
    if not team and not is_to(interaction):
        await interaction.response.send_message("❌ You are not on a team for this event.", ephemeral=True); return
    if team and team["captain_id"] != pid and not is_to(interaction):
        await interaction.response.send_message("❌ Only the team captain can submit results.", ephemeral=True); return

    round_obj = db_get_current_round(event_id)
    if not round_obj:
        await interaction.response.send_message("❌ No active round.", ephemeral=True); return

    team_rounds = db_get_team_rounds(round_obj["round_id"])
    tr = None
    if team:
        tr = next((r for r in team_rounds
                   if r["team_a_id"] == team["team_id"] or r["team_b_id"] == team["team_id"]), None)
    elif is_to(interaction):
        tr = next((r for r in team_rounds if r["state"] != TRS.COMPLETE and r["team_b_id"]), None)

    if not tr:
        await interaction.response.send_message("❌ No active team matchup found.", ephemeral=True); return
    if tr["state"] == TRS.COMPLETE:
        await interaction.response.send_message("❌ This matchup is already complete.", ephemeral=True); return

    team_a = db_get_team(tr["team_a_id"])
    team_b = db_get_team(tr["team_b_id"])
    modal  = TeamScoreModal(
        team_round_id=tr["team_round_id"],
        event_id=event_id,
        team_id=team["team_id"] if team else tr["team_a_id"],
        team_name=team_a["team_name"],
        opp_name=team_b["team_name"],
        fmt=event.get("format", "teams_8"),
    )
    await interaction.response.send_modal(modal)

# ══════════════════════════════════════════════════════════════════════════════
# TEAM STANDINGS  (top-level command, registered in main.py)
# ══════════════════════════════════════════════════════════════════════════════

async def _team_standings_callback(interaction: discord.Interaction, event_id: str):
    await interaction.response.defer(ephemeral=False)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found."); return
    if not FMT.is_team(event.get("format", "singles")):
        await interaction.followup.send("❌ This is a Singles event. Use `/standings` instead."); return
    standings = db_get_team_standings(event_id)
    embed = build_team_standings_embed(event, standings)
    await interaction.followup.send(embed=embed)

team_standings_cmd = app_commands.Command(
    name="team-standings",
    description="View current team standings",
    callback=_team_standings_callback,
    guild_ids=[GUILD_ID],
)
app_commands.autocomplete(event_id=ac_active_events)(team_standings_cmd)
