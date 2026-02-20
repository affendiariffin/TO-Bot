"""
commands_round_teams.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Team-event round management commands.

Commands:
  • /round pair-teams     — team Swiss pairings
  • /result-team submit   — captain submits team round scores
  • /team-standings       — view team standings

Also contains:
  • TeamScoreModal

Bot events (on_ready, check_round_deadlines, flush_batch_logs):
  see commands_round.py — these tasks are registered in main.py.
"""
import discord
from discord import app_commands, ui
from discord.ext import tasks
import asyncio
from datetime import datetime, timedelta, timezone
from config import (GUILD, GUILD_ID, EVENT_NOTICEBOARD_ID, COLOUR_GOLD,
                    COLOUR_AMBER, COLOUR_SLATE)
from state import ES, RndS, TRS, FMT, TS, is_to
from database import *
from threads import (ensure_round_thread, team_swiss_pair,
                     get_previous_team_pairings, ntl_team_result,
                     twovtwo_team_result, db_get_team_standings,
                     db_upsert_team_standing, db_apply_team_result)
from embeds import build_team_standings_embed, build_pairings_embed
from services import (refresh_spectator_dashboard, ac_active_events,
                      ac_approved_regs, log_immediate)

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS  —  TEAM ROUND MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@round_grp.command(name="pair-teams", description="[TO] Generate team Swiss pairings and start the round (team events)")
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

    existing  = db_get_rounds(event_id)
    round_num = len(existing) + 1
    max_rounds = calculate_rounds(len(ready_teams))
    if round_num > max_rounds:
        await interaction.followup.send(f"❌ All {max_rounds} rounds complete.", ephemeral=True); return

    day_num  = math.ceil(round_num / event["rounds_per_day"])
    deadline = datetime.utcnow() + timedelta(minutes=duration_minutes)
    round_id = db_create_round(event_id, round_num, day_num, deadline)
    db_update_round(round_id, {"state": RndS.IN_PROGRESS, "started_at": datetime.utcnow()})

    # Build team standings pool (or seed from teams if round 1)
    team_standings = db_get_team_standings(event_id)
    if not team_standings:
        # First round — seed standings rows from ready teams
        for t in ready_teams:
            db_upsert_team_standing(event_id, t["team_id"], t["team_name"])
        team_standings = db_get_team_standings(event_id)

    previous  = get_previous_team_pairings(event_id)
    pairings, bye_team = team_swiss_pair(team_standings, previous)

    # Create team_round records and individual game records for 2v2
    team_size = FMT.team_size(fmt)
    ch        = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    summary_lines = []

    for team_a_data, team_b_data in pairings:
        team_a = db_get_team(team_a_data["team_id"])
        team_b = db_get_team(team_b_data["team_id"])
        trid   = db_create_team_round(round_id, event_id, team_a["team_id"], team_b["team_id"])

        if fmt == FMT.TWO_V_TWO:
            # Auto-assign individual games: A1vsB1, A2vsB2
            members_a = db_get_team_members(team_a["team_id"])
            members_b = db_get_team_members(team_b["team_id"])
            non_subs_a = [m for m in members_a if m["role"] != "substitute"]
            non_subs_b = [m for m in members_b if m["role"] != "substitute"]

            room_nums = sorted([
                n for n in [
                    (lambda m: int(m.group(1)) if m else None)(re.search(r"(\d+)\s*$", c.name))
                    for c in interaction.guild.voice_channels if c.name.startswith(GAME_ROOM_PREFIX)
                ] if n is not None
            ])
            room_offset = len(summary_lines)  # crude room assignment

            for slot_idx, (ma, mb) in enumerate(zip(non_subs_a, non_subs_b)):
                room = room_nums[room_offset * 2 + slot_idx] if (room_offset * 2 + slot_idx) < len(room_nums) else None
                gid = db_create_game({
                    "round_id": round_id, "event_id": event_id, "room_number": room,
                    "player1_id": ma["player_id"], "player1_username": ma["player_username"],
                    "player1_army": ma["army"] or "Unknown", "player1_detachment": ma["detachment"] or "Unknown",
                    "player2_id": mb["player_id"], "player2_username": mb["player_username"],
                    "player2_army": mb["army"] or "Unknown", "player2_detachment": mb["detachment"] or "Unknown",
                })
                # Record individual pairing slot
                pid = db_create_team_pairing(trid, slot_idx + 1)
                db_update_team_pairing(pid, {
                    "game_id": gid,
                    "defender_player_id": ma["player_id"], "defender_team_id": team_a["team_id"],
                    "attacker_player_id": mb["player_id"], "attacker_team_id": team_b["team_id"],
                })
                # Ensure individual standings rows
                for m in (ma, mb):
                    db_upsert_standing(event_id, m["player_id"], m["player_username"],
                                        m["army"] or "Unknown", m["detachment"] or "Unknown")

        summary_lines.append(
            f"⚔️ **{team_a['team_name']}**  vs  **{team_b['team_name']}**"
            + (f"  *(2v2 auto-assigned)*" if fmt == FMT.TWO_V_TWO else "  *(pairing ritual required)*")
        )

    # Bye team
    bye_note = ""
    if bye_team:
        bye_tid = bye_team["team_id"]
        team_b_obj = db_get_team(bye_tid)
        db_create_team_round(round_id, event_id, bye_tid, None)
        # Award bye: 2 TP + fixed GP (80 for 8s scales to team size)
        bye_gp = round(80 * (FMT.team_size(fmt) * 20) / 160)
        db_apply_team_result(event_id, bye_tid, 2, bye_gp, 0, True, False)
        bye_note = f"\n\n🎲 **{team_b_obj['team_name']}** has a bye this round and receives a walkover win ({bye_gp} GP)."

    round_thread = await ensure_round_thread(bot, event_id, round_num, interaction.guild, event["name"])

    if ch:
        embed = discord.Embed(
            title=f"⚔️  Round {round_num} Pairings — {event['name']}",
            description="\n".join(summary_lines) + bye_note,
            color=COLOUR_CRIMSON,
        )
        embed.add_field(name="⏰ Deadline", value=ts(deadline), inline=True)
        embed.add_field(name="Format", value=fmt.replace("_"," ").title(), inline=True)
        msg = await ch.send(embed=embed)
        db_update_round(round_id, {"pairings_msg_id": str(msg.id)})

        if fmt == FMT.TWO_V_TWO:
            # Post individual game action buttons for 2v2 in the round thread
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
    await log_immediate(bot, f"Team Round {round_num} Started",
        f"Event: **{event['name']}** · {len(pairings)} team matchups",
        COLOUR_CRIMSON)

# ── Team result submission ─────────────────────────────────────────────────────

result_team_grp = app_commands.Group(
    name="result-team", description="Submit and manage team round results",
    guild_ids=[GUILD_ID]
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
        max_gp  = team_sz * 20  # max per game is 20 GP

        # Determine winners
        tp_a, result_a = ntl_team_result(gp_a, max_gp)
        tp_b, result_b = ntl_team_result(gp_b, max_gp)
        # Ensure TP sum = 2 (win+loss) or 2 (draw+draw)
        if tp_a == tp_b == 2:  # Both can't win — give higher GP the win
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

        # Apply to team standings
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

    # Find this team's team_round
    team_rounds = db_get_team_rounds(round_obj["round_id"])
    tr = None
    if team:
        tr = next((r for r in team_rounds
                   if r["team_a_id"] == team["team_id"] or r["team_b_id"] == team["team_id"]), None)
    elif is_to(interaction):
        # TO: show all pending team rounds as choices — simplified: take first pending
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


tree.add_command(result_team_grp)

# ── Team standings command ─────────────────────────────────────────────────────

@tree.command(name="team-standings", description="View current team standings", guild=GUILD)
@app_commands.describe(event_id="The event")
@app_commands.autocomplete(event_id=ac_active_events)
async def team_standings_cmd(interaction: discord.Interaction, event_id: str):
    await interaction.response.defer(ephemeral=False)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found."); return
    if not FMT.is_team(event.get("format","singles")):
        await interaction.followup.send("❌ This is a Singles event. Use `/standings` instead."); return

    standings = db_get_team_standings(event_id)
    embed = build_team_standings_embed(event, standings)
    await interaction.followup.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS  —  RESULTS (TO use only — players use buttons)
# ══════════════════════════════════════════════════════════════════════════════

result_grp = app_commands.Group(name="result", description="Result management (TO)",
                                 guild_ids=[GUILD_ID],
                                 default_permissions=discord.Permissions(use_application_commands=True))

@result_grp.command(name="override", description="Force a game result")
@app_commands.describe(event_id="Select event", game_id="Select game", p1_vp="Player 1 VP", p2_vp="Player 2 VP")
@app_commands.autocomplete(event_id=ac_active_events, game_id=ac_active_games)
async def result_override(interaction: discord.Interaction, event_id: str,
                           game_id: str, p1_vp: int, p2_vp: int):
    await interaction.response.defer(ephemeral=True)
    game = db_get_game(game_id)
    if not game: await interaction.followup.send("❌ Game not found.", ephemeral=True); return

    winner_id = game["player1_id"] if p1_vp >= p2_vp else game["player2_id"]
    loser_id  = game["player2_id"] if winner_id == game["player1_id"] else game["player1_id"]
    db_update_game(game_id, {"player1_vp": p1_vp, "player2_vp": p2_vp,
                               "winner_id": winner_id, "state": GS.COMPLETE,
                               "confirmed_at": datetime.utcnow()})
    db_apply_result_to_standings(event_id, winner_id, loser_id, p1_vp, p2_vp)

    winner_name = game["player1_username"] if winner_id == game["player1_id"] else game["player2_username"]
    await log_immediate(bot, "Result Override",
        f"⚡ Room {game['room_number']}: **{game['player1_username']}** {p1_vp}—{p2_vp} **{game['player2_username']}**\n"
        f"Winner: **{winner_name}**  ·  Overridden by {interaction.user.display_name}",
        COLOUR_AMBER)
    await refresh_spectator_dashboard(bot, event_id)
    await interaction.followup.send(f"✅ Result forced — **{p1_vp}–{p2_vp}**  Winner: **{winner_name}**", ephemeral=True)

@result_grp.command(name="adjust", description="Adjust VPs on a confirmed result (e.g. after judge ruling)")
@app_commands.describe(event_id="Select event", game_id="Select completed game")
@app_commands.autocomplete(event_id=ac_active_events, game_id=ac_complete_games)
async def result_adjust(interaction: discord.Interaction, event_id: str, game_id: str):
    game = db_get_game(game_id)
    if not game: await interaction.response.send_message("❌ Game not found.", ephemeral=True); return
    if game["state"] != GS.COMPLETE:
        await interaction.response.send_message("❌ Game is not yet confirmed.", ephemeral=True); return
    await interaction.response.send_modal(VPAdjustModal(game_id, event_id, bot, interaction.guild))

tree.add_command(result_grp)

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMAND  —  EVENT FINISH  (bulk Scorebot submit)
# ══════════════════════════════════════════════════════════════════════════════

@tree.command(name="event-finish", description="[TO] Close event and submit results to Scorebot", guild=GUILD)
@app_commands.describe(event_id="Select event")
@app_commands.autocomplete(event_id=ac_active_events)
async def event_finish(interaction: discord.Interaction, event_id: str):
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event: await interaction.followup.send("❌ Not found.", ephemeral=True); return

    db_update_event(event_id, {"state": ES.COMPLETE})

    # Final standings
    standings = db_get_standings(event_id, active_only=False)
    embed     = build_standings_embed(event, standings, final=True)
    ch        = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        await ch.send(embed=embed)

    # #announcements winner card
    ann_ch = interaction.guild.get_channel(ANNOUNCEMENTS_ID)
    if ann_ch and standings:
        winner = standings[0]
        w_embed = discord.Embed(
            title=f"🏆  {event['name']}  —  Results!",
            description=(
                f"🥇 **{winner['player_username']}**\n"
                f"{fe(winner['army'])} {winner['army']} · *{winner['detachment']}*\n\n"
                f"{winner['wins']}W {winner['losses']}L  ·  {winner['vp_total']} VP total\n\n"
                f"*Full standings in #event-noticeboard*"
            ),
            color=COLOUR_GOLD,
        )
        w_embed.set_thumbnail(url="https://emojicdn.elk.sh/🥇?style=twitter")
        await ann_ch.send(embed=w_embed)

    # Bulk submit to Scorebot
    all_games  = db_get_event_games(event_id)
    submitted  = scorebot_bulk_submit(event_id, all_games)

    # Close Discord Scheduled Event
    if event.get("discord_event_id"):
        try:
            de = await interaction.guild.fetch_scheduled_event(int(event["discord_event_id"]))
            await de.end()
        except: pass

    # Archive and lock all private threads
    await archive_event_threads(bot, event_id, interaction.guild)

    await log_immediate(bot, "Event Complete",
        f"🏆 **{event['name']}** finished\n"
        f"Winner: {standings[0]['player_username'] if standings else 'N/A'}\n"
        f"Results submitted to Scorebot: {submitted} games",
        COLOUR_GOLD)

    await interaction.followup.send(
        f"✅ **{event['name']}** closed!\n"
        f"Final standings posted  ·  {submitted}/{len([g for g in all_games if g['state']==GS.COMPLETE and not g['is_bye']])} results submitted to Scorebot",
        ephemeral=True)

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS  —  SPECTATOR / PUBLIC
# ══════════════════════════════════════════════════════════════════════════════

@tree.command(name="standings", description="View current event standings", guild=GUILD)
@app_commands.autocomplete(event_id=ac_all_events)
async def standings_cmd(interaction: discord.Interaction, event_id: str):
    event = db_get_event(event_id)
    if not event: await interaction.response.send_message("❌ Not found.", ephemeral=True); return
    embed = build_standings_embed(event, db_get_standings(event_id))
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="my-list", description="View your submitted army list", guild=GUILD)
@app_commands.autocomplete(event_id=ac_active_events)
async def my_list(interaction: discord.Interaction, event_id: str):
    reg = db_get_registration(event_id, str(interaction.user.id))
    if not reg: await interaction.response.send_message("❌ Not registered.", ephemeral=True); return
    await interaction.response.send_message(embed=build_player_list_embed(reg, 0), ephemeral=True)

# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════════════

@tasks.loop(minutes=LOG_BATCH_MINUTES)
async def flush_batch_logs():
    if not BOT_LOGS_ID: return
    ch   = bot.get_channel(BOT_LOGS_ID)
    if not ch: return
    logs = db_flush_logs()
    if not logs: return
    by_event: Dict[str, list] = {}
    for log in logs:
        by_event.setdefault(log["event_id"] or "general", []).append(log)
    for eid, entries in by_event.items():
        event = db_get_event(eid) if eid != "general" else None
        title = f"📋  Log — {event['name']}" if event else "📋  Tournament Log"
        lines = [f"`{l['logged_at'].strftime('%H:%M')}` {l['message']}" for l in entries]
        embed = discord.Embed(
            title=title,
            description="\n".join(lines[:20]),
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow(),
        )
        embed.set_footer(text=f"{len(entries)} events in last {LOG_BATCH_MINUTES}m")
        try: await ch.send(embed=embed)
        except Exception as e: print(f"⚠️ Batch log failed: {e}")

@tasks.loop(minutes=1)
async def check_round_deadlines():
    for event in db_get_active_events():
        if event["state"] != ES.IN_PROGRESS: continue
        rnd = db_get_current_round(event["event_id"])
        if not rnd or not rnd.get("deadline_at"): continue
        if rnd.get("clock_paused"): continue
        deadline = rnd["deadline_at"]
        if deadline.tzinfo is None: deadline = deadline.replace(tzinfo=timezone.utc)
        diff = (deadline - datetime.now(timezone.utc)).total_seconds()
        # 15-minute warning
        if 840 <= diff <= 900:
            games = db_get_games(rnd["round_id"])
            incomplete = [g for g in games if g["state"] in (GS.PENDING,) and not g["is_bye"]]
            if incomplete:
                ch = bot.get_channel(EVENT_NOTICEBOARD_ID)
                if ch:
                    room_list = ", ".join(f"Room {g['room_number']}" for g in incomplete)
                    mentions  = " ".join(
                        f"<@{g['player1_id']}> <@{g['player2_id']}>"
                        for g in incomplete if g.get("player2_id")
                    )
                    await ch.send(
                        f"⏰ **15 minutes remaining — Round {rnd['round_number']}**\n"
                        f"Results outstanding: {room_list}\n{mentions}"
                    )

@tasks.loop(minutes=3)
async def refresh_dashboards():
    for event in db_get_active_events():
        if event["state"] == ES.IN_PROGRESS:
            try: await refresh_spectator_dashboard(bot, event["event_id"])
            except: pass

@flush_batch_logs.before_loop
@check_round_deadlines.before_loop
@refresh_dashboards.before_loop
async def before_loops():
    await bot.wait_until_ready()

# ══════════════════════════════════════════════════════════════════════════════
# BOT EVENTS
# ══════════════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    print(f"✅ FND Tournament Bot online as {bot.user}")
    try:
        init_db()
        print("✅ DB ready")
    except Exception as e:
        print(f"❌ DB init failed: {e}")
    try:
        synced = await tree.sync(guild=GUILD)
        print(f"✅ {len(synced)} commands synced")
    except Exception as e:
        print(f"❌ Sync failed: {e}")
    # Restore thread registry from DB so existing threads survive restarts
    try:
        guild = bot.get_guild(GUILD_ID)
        if guild:
            await restore_thread_registry(bot, guild)
    except Exception as e:
        print(f"⚠️ Thread registry restore failed: {e}")
    for task in (flush_batch_logs, check_round_deadlines, refresh_dashboards):
        if not task.is_running(): task.start()
    print("✅ FND Tournament Bot ready. For the Emperor! ⚔️")

@bot.event
async def on_member_join(member: discord.Member):
    live = [e for e in db_get_active_events() if e["state"] == ES.IN_PROGRESS]
    if not live: return
    event = live[0]
    try:
        wpc = bot.get_channel(WHATS_PLAYING_ID)
        await member.send(
            f"👋 Welcome! A **Warhammer 40k TTS Tournament** is live:\n"
            f"**{event['name']}**\n\n"
            f"Head to {wpc.mention if wpc else '#whats-playing-now'} to spectate.\n"
            f"Players cannot see you in the Game Rooms. ⚔️"
        )
    except: pass

@tree.error
async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    msg = "❌ Admin permission required." if isinstance(error, app_commands.MissingPermissions) \
          else f"❌ Error: {error}"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except: pass
    print(f"Command error: {error}")

# ══════════════════════════════════════════════════════════════════════════════
