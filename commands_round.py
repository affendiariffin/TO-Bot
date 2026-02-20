"""
commands_round.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Round management slash commands (singles + shared TO tools).

Commands (/round group):
  • /round briefing
  • /round start
  • /round complete
  • /round repair

Result commands (/result group):
  • /result override
  • /result adjust

Top-level commands (registered in main.py):
  • /event-finish
  • /standings
  • /my-list
"""
import discord
from discord import app_commands
import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Dict
from config import (GUILD, GUILD_ID, EVENT_NOTICEBOARD_ID, ANNOUNCEMENTS_ID,
                    BOT_LOGS_ID, LOG_BATCH_MINUTES, COLOUR_GOLD, COLOUR_CRIMSON,
                    COLOUR_AMBER, COLOUR_SLATE, fe, ts, ts_full)
from state import ES, RndS, GS, RS, is_to, get_thread_reg
from database import *
from threads import (ensure_round_thread, swiss_pair, assign_rooms,
                     get_previous_pairings, calculate_rounds, get_avg_vp,
                     archive_event_threads)
from embeds import (build_briefing_embed, build_pairings_embed,
                    build_standings_embed, build_spectator_dashboard_embed,
                    build_player_list_embed)
from views import PairingActionView, VPAdjustModal
from services import (refresh_spectator_dashboard, _refresh_judge_queue,
                      ac_active_events, ac_all_events, ac_active_games,
                      ac_complete_games, ac_approved_regs, log_immediate)

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS  —  ROUNDS
# ══════════════════════════════════════════════════════════════════════════════

round_grp = app_commands.Group(
    name="round", description="Round management",
    guild_ids=[GUILD_ID],
    default_permissions=discord.Permissions(use_application_commands=True),
)

@round_grp.command(name="briefing", description="Post day briefing and ping all players")
@app_commands.describe(event_id="Select event", day_number="Day number", round_number="First round of this day")
@app_commands.autocomplete(event_id=ac_active_events)
async def round_briefing(interaction: discord.Interaction, event_id: str,
                          day_number: int, round_number: int):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    event   = db_get_event(event_id)
    players = db_get_registrations(event_id, RS.APPROVED)
    if not event or not players:
        await interaction.followup.send("❌ Event not found or no approved players.", ephemeral=True); return
    embed = build_briefing_embed(event, round_number, day_number, players)
    ch    = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        mentions = " ".join(f"<@{p['player_id']}>" for p in players)
        await ch.send(content=mentions, embed=embed)
    await interaction.followup.send(f"✅ Day {day_number} briefing posted. {len(players)} players pinged.", ephemeral=True)

@round_grp.command(name="start", description="Generate Swiss pairings and start the round")
@app_commands.describe(event_id="Select event", duration_minutes="Round duration in minutes (default 120)")
@app_commands.autocomplete(event_id=ac_active_events)
async def round_start(interaction: discord.Interaction, event_id: str, duration_minutes: int = 120):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Not found.", ephemeral=True); return

    existing   = db_get_rounds(event_id)
    round_num  = len(existing) + 1
    max_rounds = calculate_rounds(event["max_players"])
    if round_num > max_rounds:
        await interaction.followup.send(
            f"❌ All {max_rounds} rounds complete. Use `/event-finish` to close.", ephemeral=True); return

    day_num  = math.ceil(round_num / event["rounds_per_day"])
    deadline = datetime.utcnow() + timedelta(minutes=duration_minutes)
    round_id = db_create_round(event_id, round_num, day_num, deadline)
    db_update_round(round_id, {"state": RndS.IN_PROGRESS, "started_at": datetime.utcnow()})

    standings = db_get_standings(event_id, active_only=True)
    if not standings:
        regs = db_get_registrations(event_id, RS.APPROVED)
        standings = [{"player_id": r["player_id"], "player_username": r["player_username"],
                      "army": r["army"], "detachment": r["detachment"],
                      "wins": 0, "losses": 0, "vp_diff": 0, "had_bye": False} for r in regs]

    previous         = get_previous_pairings(event_id)
    pairings, bye_player = swiss_pair(standings, previous)
    assigned         = assign_rooms(pairings, interaction.guild)

    games_data = []
    for item in assigned:
        p1, p2, room = item["p1"], item["p2"], item["room"]
        gid = db_create_game({
            "round_id": round_id, "event_id": event_id, "room_number": room,
            "player1_id": p1["player_id"], "player1_username": p1["player_username"],
            "player1_army": p1["army"],    "player1_detachment": p1["detachment"],
            "player2_id": p2["player_id"], "player2_username": p2["player_username"],
            "player2_army": p2["army"],    "player2_detachment": p2["detachment"],
        })
        games_data.append((gid, room, p1, p2))

    if bye_player:
        db_create_game({
            "round_id": round_id, "event_id": event_id, "room_number": None,
            "player1_id": bye_player["player_id"], "player1_username": bye_player["player_username"],
            "player1_army": bye_player["army"],     "player1_detachment": bye_player["detachment"],
            "is_bye": True,
        })
        db_update_standing(event_id, bye_player["player_id"], {"had_bye": True})

    round_obj    = db_get_round(round_id)
    all_games    = db_get_games(round_id)
    ch           = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    round_thread = None

    if ch:
        embed        = build_pairings_embed(event, round_obj, all_games, interaction.guild)
        pairings_msg = await ch.send(embed=embed)
        db_update_round(round_id, {"pairings_msg_id": str(pairings_msg.id)})

        round_thread = await ensure_round_thread(
            interaction.client, event_id, round_num, interaction.guild, event["name"], pairings_msg
        )
        btn_target = round_thread or ch

        for gid, room, p1, p2 in games_data:
            view = PairingActionView(gid, event_id, room)
            await btn_target.send(
                f"**Room {room}  ·  {p1['player_username']}** {fe(p1['army'])} **vs** "
                f"{fe(p2['army'])} **{p2['player_username']}**",
                view=view,
            )
            await asyncio.sleep(0.3)

        if round_thread:
            await ch.send(
                f"⚔️ **Round {round_num} is LIVE!**  Deadline {ts(deadline)}\n"
                f"Players: submit results and raise judge calls in {round_thread.mention}",
                silent=True,
            )

    await _refresh_judge_queue(interaction.client, event_id, interaction.guild)
    await refresh_spectator_dashboard(interaction.client, event_id)
    await interaction.followup.send(
        f"✅ Round {round_num} started!  {len(assigned)} games  ·  Deadline {ts(deadline)}\n"
        f"Round thread: {round_thread.mention if round_thread else '#event-noticeboard'}"
        + (f"\n⚠️ Bye: **{bye_player['player_username']}** — VP awarded at round close" if bye_player else ""),
        ephemeral=True,
    )
    await log_immediate(interaction.client, f"Round {round_num} Started",
        f"Event: **{event['name']}**  ·  Day {day_num}  ·  {len(assigned)} games\n"
        f"Deadline {ts_full(deadline)}"
        + (f"\nThread: {round_thread.mention}" if round_thread else ""),
        COLOUR_CRIMSON)

@round_grp.command(name="complete", description="Close current round and award bye VPs")
@app_commands.describe(event_id="Select event")
@app_commands.autocomplete(event_id=ac_active_events)
async def round_complete(interaction: discord.Interaction, event_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    event     = db_get_event(event_id)
    round_obj = db_get_current_round(event_id)
    if not event or not round_obj:
        await interaction.followup.send("❌ No active round.", ephemeral=True); return

    games      = db_get_games(round_obj["round_id"])
    incomplete = [g for g in games if g["state"] in (GS.PENDING, GS.SUBMITTED, GS.DISPUTED)
                  and not g["is_bye"]]
    if incomplete:
        await interaction.followup.send(
            f"⚠️ {len(incomplete)} game(s) still incomplete:\n"
            + "\n".join(f"Room {g['room_number']}: {g['player1_username']} vs {g['player2_username']}"
                        for g in incomplete)
            + "\n\nUse `/result override` to force results before closing.",
            ephemeral=True)
        return

    bye_vp  = 0
    bye_games = [g for g in games if g["is_bye"]]
    if bye_games:
        avg_vp = get_avg_vp(event_id, round_obj["round_id"])
        bye_vp = round(avg_vp)
        for bg in bye_games:
            db_update_game(bg["game_id"], {
                "state":        GS.COMPLETE,
                "player1_vp":   bye_vp,
                "player2_vp":   0,
                "winner_id":    bg["player1_id"],
                "confirmed_at": datetime.utcnow(),
            })
            db_apply_result_to_standings(event_id, bg["player1_id"], "bye", bye_vp, 0)
            db_queue_log(f"Bye awarded to {bg['player1_username']}: {bye_vp} VP (round avg)", event_id)

    db_update_round(round_obj["round_id"], {"state": RndS.COMPLETE, "completed_at": datetime.utcnow()})

    standings = db_get_standings(event_id)
    embed     = build_standings_embed(event, standings)
    ch        = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        await ch.send(embed=embed)

    await refresh_spectator_dashboard(interaction.client, event_id)
    await interaction.followup.send(
        f"✅ Round {round_obj['round_number']} complete."
        + (f"\n🎲 Bye VP awarded: **{bye_vp}** (round average)" if bye_games else ""),
        ephemeral=True,
    )

@round_grp.command(name="repair", description="Regenerate pairings for the current round (after drops)")
@app_commands.describe(event_id="Select event", duration_minutes="New round duration (0 = keep original)")
@app_commands.autocomplete(event_id=ac_active_events)
async def round_repair(interaction: discord.Interaction, event_id: str, duration_minutes: int = 0):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    event     = db_get_event(event_id)
    round_obj = db_get_current_round(event_id)
    if not event or not round_obj:
        await interaction.followup.send("❌ No active round to repair.", ephemeral=True); return

    games = db_get_games(round_obj["round_id"])
    if any(g["state"] == GS.COMPLETE for g in games):
        await interaction.followup.send(
            "❌ Cannot repair — some games already have confirmed results.\n"
            "Use `/result adjust` for score corrections instead.", ephemeral=True); return

    db_delete_games_for_round(round_obj["round_id"])

    if duration_minutes:
        new_deadline = datetime.utcnow() + timedelta(minutes=duration_minutes)
        db_update_round(round_obj["round_id"], {"deadline_at": new_deadline})
    round_obj = db_get_round(round_obj["round_id"])

    standings = db_get_standings(event_id, active_only=True)
    if len(standings) < 2:
        await interaction.followup.send("❌ Not enough active players to pair.", ephemeral=True); return

    previous         = get_previous_pairings(event_id)
    pairings, bye_player = swiss_pair(standings, previous)
    assigned         = assign_rooms(pairings, interaction.guild)

    games_data = []
    for item in assigned:
        p1, p2, room = item["p1"], item["p2"], item["room"]
        gid = db_create_game({
            "round_id": round_obj["round_id"], "event_id": event_id, "room_number": room,
            "player1_id": p1["player_id"], "player1_username": p1["player_username"],
            "player1_army": p1["army"],    "player1_detachment": p1["detachment"],
            "player2_id": p2["player_id"], "player2_username": p2["player_username"],
            "player2_army": p2["army"],    "player2_detachment": p2["detachment"],
        })
        games_data.append((gid, room, p1, p2))

    if bye_player:
        db_create_game({
            "round_id": round_obj["round_id"], "event_id": event_id, "room_number": None,
            "player1_id": bye_player["player_id"], "player1_username": bye_player["player_username"],
            "player1_army": bye_player["army"],     "player1_detachment": bye_player["detachment"],
            "is_bye": True,
        })
        db_update_standing(event_id, bye_player["player_id"], {"had_bye": True})

    all_games    = db_get_games(round_obj["round_id"])
    ch           = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    round_thread = None
    reg_t        = get_thread_reg(event_id)
    tid          = reg_t["rounds"].get(round_obj["round_number"])
    if tid:
        round_thread = interaction.guild.get_thread(tid)

    if ch:
        embed = build_pairings_embed(event, round_obj, all_games, interaction.guild)
        await ch.send(f"🔄 **Pairings updated — Round {round_obj['round_number']}**", embed=embed)
        btn_target = round_thread or ch
        for gid, room, p1, p2 in games_data:
            view = PairingActionView(gid, event_id, room)
            await btn_target.send(
                f"**Room {room}  ·  {p1['player_username']}** {fe(p1['army'])} **vs** "
                f"{fe(p2['army'])} **{p2['player_username']}**",
                view=view,
            )
            await asyncio.sleep(0.3)

    await refresh_spectator_dashboard(interaction.client, event_id)
    await interaction.followup.send(
        f"✅ Round {round_obj['round_number']} pairings repaired.\n"
        f"{len(assigned)} games regenerated"
        + (f"\n⚠️ Bye: **{bye_player['player_username']}**" if bye_player else ""),
        ephemeral=True,
    )
    await log_immediate(interaction.client, f"Round {round_obj['round_number']} Repaired",
        f"⚠️ Pairings rebuilt for **{event['name']}** — {len(standings)} active players",
        COLOUR_AMBER)

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS  —  RESULTS
# ══════════════════════════════════════════════════════════════════════════════

result_grp = app_commands.Group(
    name="result", description="Result management (TO)",
    guild_ids=[GUILD_ID],
    default_permissions=discord.Permissions(use_application_commands=True),
)

@result_grp.command(name="override", description="Force a game result")
@app_commands.describe(event_id="Select event", game_id="Select game", p1_vp="Player 1 VP", p2_vp="Player 2 VP")
@app_commands.autocomplete(event_id=ac_active_events, game_id=ac_active_games)
async def result_override(interaction: discord.Interaction, event_id: str,
                           game_id: str, p1_vp: int, p2_vp: int):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    game = db_get_game(game_id)
    if not game:
        await interaction.followup.send("❌ Game not found.", ephemeral=True); return

    winner_id   = game["player1_id"] if p1_vp >= p2_vp else game["player2_id"]
    loser_id    = game["player2_id"] if winner_id == game["player1_id"] else game["player1_id"]
    db_update_game(game_id, {"player1_vp": p1_vp, "player2_vp": p2_vp,
                               "winner_id": winner_id, "state": GS.COMPLETE,
                               "confirmed_at": datetime.utcnow()})
    db_apply_result_to_standings(event_id, winner_id, loser_id, p1_vp, p2_vp)

    winner_name = game["player1_username"] if winner_id == game["player1_id"] else game["player2_username"]
    await log_immediate(interaction.client, "Result Override",
        f"⚡ Room {game['room_number']}: **{game['player1_username']}** {p1_vp}—{p2_vp} **{game['player2_username']}**\n"
        f"Winner: **{winner_name}**  ·  Overridden by {interaction.user.display_name}",
        COLOUR_AMBER)
    await refresh_spectator_dashboard(interaction.client, event_id)
    await interaction.followup.send(f"✅ Result forced — **{p1_vp}–{p2_vp}**  Winner: **{winner_name}**", ephemeral=True)

@result_grp.command(name="adjust", description="Adjust VPs on a confirmed result (e.g. after judge ruling)")
@app_commands.describe(event_id="Select event", game_id="Select completed game")
@app_commands.autocomplete(event_id=ac_active_events, game_id=ac_complete_games)
async def result_adjust(interaction: discord.Interaction, event_id: str, game_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    game = db_get_game(game_id)
    if not game:
        await interaction.response.send_message("❌ Game not found.", ephemeral=True); return
    if game["state"] != GS.COMPLETE:
        await interaction.response.send_message("❌ Game is not yet confirmed.", ephemeral=True); return
    await interaction.response.send_modal(VPAdjustModal(game_id, event_id, interaction.client, interaction.guild))

# ══════════════════════════════════════════════════════════════════════════════
# TOP-LEVEL COMMANDS  (registered individually in main.py)
# ══════════════════════════════════════════════════════════════════════════════

@app_commands.guild_only()
async def event_finish(interaction: discord.Interaction, event_id: str):
    """[TO] Close event and submit results to Scorebot"""
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Not found.", ephemeral=True); return

    db_update_event(event_id, {"state": ES.COMPLETE})

    standings = db_get_standings(event_id, active_only=False)
    embed     = build_standings_embed(event, standings, final=True)
    ch        = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        await ch.send(embed=embed)

    ann_ch = interaction.guild.get_channel(ANNOUNCEMENTS_ID)
    if ann_ch and standings:
        winner  = standings[0]
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

    all_games = db_get_event_games(event_id)
    submitted = scorebot_bulk_submit(event_id, all_games)

    if event.get("discord_event_id"):
        try:
            de = await interaction.guild.fetch_scheduled_event(int(event["discord_event_id"]))
            await de.end()
        except: pass

    await archive_event_threads(interaction.client, event_id, interaction.guild)

    await log_immediate(interaction.client, "Event Complete",
        f"🏆 **{event['name']}** finished\n"
        f"Winner: {standings[0]['player_username'] if standings else 'N/A'}\n"
        f"Results submitted to Scorebot: {submitted} games",
        COLOUR_GOLD)

    await interaction.followup.send(
        f"✅ **{event['name']}** closed!\n"
        f"Final standings posted  ·  {submitted}/"
        f"{len([g for g in all_games if g['state']==GS.COMPLETE and not g['is_bye']])} "
        f"results submitted to Scorebot",
        ephemeral=True)

event_finish = app_commands.Command(
    name="event-finish",
    description="[TO] Close event and submit results to Scorebot",
    callback=event_finish,
    guild_ids=[GUILD_ID],
)
app_commands.describe(event_id="Select event")(event_finish)
app_commands.autocomplete(event_id=ac_active_events)(event_finish)


async def _standings_callback(interaction: discord.Interaction, event_id: str):
    event = db_get_event(event_id)
    if not event:
        await interaction.response.send_message("❌ Not found.", ephemeral=True); return
    embed = build_standings_embed(event, db_get_standings(event_id))
    await interaction.response.send_message(embed=embed)

standings_cmd = app_commands.Command(
    name="standings",
    description="View current event standings",
    callback=_standings_callback,
    guild_ids=[GUILD_ID],
)
app_commands.autocomplete(event_id=ac_all_events)(standings_cmd)


async def _my_list_callback(interaction: discord.Interaction, event_id: str):
    reg = db_get_registration(event_id, str(interaction.user.id))
    if not reg:
        await interaction.response.send_message("❌ Not registered.", ephemeral=True); return
    await interaction.response.send_message(embed=build_player_list_embed(reg, 0), ephemeral=True)

my_list_cmd = app_commands.Command(
    name="my-list",
    description="View your submitted army list",
    callback=_my_list_callback,
    guild_ids=[GUILD_ID],
)
app_commands.autocomplete(event_id=ac_active_events)(my_list_cmd)
