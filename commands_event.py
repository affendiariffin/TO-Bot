"""
commands_event.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Slash commands for event lifecycle management (TO only).

Commands (all under /event group):
  • /event create
  • /event open-interest
  • /event open-registration
  • /event lock-lists
  • /event start

Registration commands (/reg group):
  • /reg submit
  • /reg drop
  • /reg list

Registered into the bot in main.py via bot.add_listener / tree.add_command.
"""
import discord
from discord import app_commands
import uuid
from datetime import datetime, timezone
from config import (GUILD_ID, GUILD, EVENT_NOTICEBOARD_ID, COLOUR_GOLD,
                    COLOUR_CRIMSON, WARHAMMER_ARMIES)
from state import ES, RS, FMT, is_to
from database import *
from threads import (ensure_submissions_thread, ensure_lists_thread,
                     add_player_to_event_threads)
from embeds import (build_event_announcement_embed, build_list_review_header,
                    build_player_list_embed)
from views import EventAnnouncementView, RegistrationApprovalView
from services import (refresh_spectator_dashboard, ac_active_events,
                      ac_all_events, ac_missions, ac_armies, ac_detachments,
                      ac_pending_regs, log_immediate)

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS  —  EVENT MANAGEMENT  (TO only)
# ══════════════════════════════════════════════════════════════════════════════

event_grp = app_commands.Group(name="event", description="Tournament event management",
                                guild_ids=[GUILD_ID],
                                default_permissions=discord.Permissions(use_application_commands=True))

@event_grp.command(name="create", description="Create a new TTS tournament event")
@app_commands.describe(
    name="Event name", mission="Mission code", points_limit="Points limit (e.g. 2000)",
    start_date="Start date DD/MM/YYYY", end_date="End date DD/MM/YYYY (same for 1 day)",
    max_players="Player cap (default 16)", rounds_per_day="Rounds per day (default 3)",
    terrain_layout="Optional terrain notes",
    format="Format: singles (default), 2v2, teams_3, teams_5, teams_8",
)
@app_commands.autocomplete(mission=ac_missions)
@app_commands.choices(format=[
    app_commands.Choice(name="Singles (default)", value="singles"),
    app_commands.Choice(name="2v2 (1000pts each)",  value="2v2"),
    app_commands.Choice(name="Teams 3s (2000pts each)", value="teams_3"),
    app_commands.Choice(name="Teams 5s (2000pts each)", value="teams_5"),
    app_commands.Choice(name="Teams 8s — NTL ritual (2000pts each)", value="teams_8"),
])
async def event_create(interaction: discord.Interaction,
                        name: str, mission: str, points_limit: int,
                        start_date: str, end_date: str,
                        max_players: int = 16, rounds_per_day: int = 3,
                        terrain_layout: str = None,
                        format: str = "singles"):
    await interaction.response.defer(ephemeral=True)
    try:
        sd = datetime.strptime(start_date, "%d/%m/%Y").date()
        ed = datetime.strptime(end_date,   "%d/%m/%Y").date()
    except ValueError:
        await interaction.followup.send("❌ Date format must be DD/MM/YYYY", ephemeral=True); return
    if mission not in TOURNAMENT_MISSIONS:
        await interaction.followup.send("❌ Invalid mission code.", ephemeral=True); return

    team_sz   = FMT.team_size(format)
    ind_pts   = FMT.individual_points(format)

    eid = db_create_event({"name": name, "mission_code": mission, "points_limit": points_limit,
                            "start_date": sd, "end_date": ed, "max_players": max_players,
                            "rounds_per_day": rounds_per_day, "terrain_layout": terrain_layout,
                            "created_by": str(interaction.user.id)})
    db_update_event(eid, {"format": format, "team_size": team_sz, "individual_points": ind_pts})

    event  = db_get_event(eid)
    embed  = build_event_announcement_embed(event)
    view   = EventAnnouncementView(eid)

    ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        msg = await ch.send(embed=embed, view=view)
        db_update_event(eid, {"noticeboard_msg_id": str(msg.id)})
        try: await msg.pin()
        except: pass

    # Discord Scheduled Event
    try:
        start_dt = datetime.combine(sd, datetime.min.time()).replace(hour=9, tzinfo=timezone.utc)
        m = TOURNAMENT_MISSIONS[mission]
        fmt_label = {"singles":"Singles","2v2":"2v2","teams_3":"Teams 3s","teams_5":"Teams 5s","teams_8":"Teams 8s"}.get(format,"Singles")
        disc_evt = await interaction.guild.create_scheduled_event(
            name=name,
            description=(f"⚔️ Warhammer 40k TTS Tournament — {ind_pts}pts  [{fmt_label}]\n"
                         f"🗺️ {m['name']} [{m['deployment']}]\n"
                         f"Register interest in #event-noticeboard"),
            start_time=start_dt,
            end_time=start_dt + timedelta(hours=8),
            entity_type=discord.EntityType.external,
            location="Tabletop Simulator",
            privacy_level=discord.PrivacyLevel.guild_only,
        )
        db_update_event(eid, {"discord_event_id": str(disc_evt.id)})
    except Exception as e:
        print(f"⚠️ Discord event creation failed: {e}")

    fmt_label = {"singles":"Singles","2v2":"2v2","teams_3":"Teams 3s","teams_5":"Teams 5s","teams_8":"Teams 8s"}.get(format,"Singles")
    await interaction.followup.send(
        f"✅ **{name}** created — `{eid}`\n"
        f"Format: **{fmt_label}** · {ind_pts}pts per player\n"
        f"{calculate_rounds(max_players)} rounds suggested  ·  Announcement posted to #event-noticeboard",
        ephemeral=True)
    await log_immediate(bot, "Event Created",
        f"🏆 **{name}** by {interaction.user.display_name}\n"
        f"Format: {fmt_label} · Mission {mission}: {TOURNAMENT_MISSIONS[mission]['name']} · {ind_pts}pts · {sd}→{ed}",
        COLOUR_GOLD)

@event_grp.command(name="open-interest", description="Open interest registration")
@app_commands.autocomplete(event_id=ac_active_events)
async def event_open_interest(interaction: discord.Interaction, event_id: str):
    event = db_get_event(event_id)
    if not event: await interaction.response.send_message("❌ Not found.", ephemeral=True); return
    db_update_event(event_id, {"state": ES.INTEREST})
    ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        await ch.send(f"📣 **Interest registration open for {event['name']}!**\n"
                      f"Click **Register Interest** on the event card above.")
    await interaction.response.send_message("✅ Interest phase opened.", ephemeral=True)

@event_grp.command(name="open-registration", description="Open full list registration")
@app_commands.autocomplete(event_id=ac_active_events)
async def event_open_registration(interaction: discord.Interaction, event_id: str):
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event: await interaction.followup.send("❌ Not found.", ephemeral=True); return
    db_update_event(event_id, {"state": ES.REGISTRATION})
    regs = db_get_registrations(event_id, RS.INTERESTED)
    ch   = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch and regs:
        mentions = " ".join(f"<@{r['player_id']}>" for r in regs)
        await ch.send(
            f"🚨 **Registration is now OPEN for {event['name']}!** {mentions}\n\n"
            f"Use `/register submit` to confirm your spot. **List submission and TO approval required.**"
        )
    await interaction.followup.send(f"✅ Registration opened. {len(regs)} players notified.", ephemeral=True)

@event_grp.command(name="lock-lists", description="Lock lists and publish list review dashboard")
@app_commands.autocomplete(event_id=ac_active_events)
async def event_lock_lists(interaction: discord.Interaction, event_id: str):
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event: await interaction.followup.send("❌ Not found.", ephemeral=True); return
    regs = db_get_registrations(event_id, RS.APPROVED)

    # Create/get private lists thread
    lists_thread = await ensure_lists_thread(bot, event_id, interaction.guild, event["name"])
    target = lists_thread or interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)

    if target:
        await target.send(embed=build_list_review_header(event, regs))
        for i, reg in enumerate(regs, 1):
            await target.send(embed=build_player_list_embed(reg, i))
            await asyncio.sleep(0.4)

    # Also post a brief notice in the main noticeboard channel
    ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch and lists_thread:
        await ch.send(
            f"📋 **Army lists published for {event['name']}**\n"
            f"Approved players: check {lists_thread.mention} to review your opponents."
        )

    # DM approved players
    for reg in regs:
        try:
            user = await bot.fetch_user(int(reg["player_id"]))
            await user.send(
                f"📋 **Army lists published for {event['name']}!**\n"
                f"Check the Army Lists thread in #event-noticeboard to review your opponents."
            )
        except: pass
    await interaction.followup.send(f"✅ {len(regs)} lists published to {lists_thread.mention if lists_thread else '#event-noticeboard'}.", ephemeral=True)

@event_grp.command(name="start", description="Start the event and post spectator dashboard")
@app_commands.autocomplete(event_id=ac_active_events)
async def event_start(interaction: discord.Interaction, event_id: str):
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event: await interaction.followup.send("❌ Not found.", ephemeral=True); return
    db_update_event(event_id, {"state": ES.IN_PROGRESS})
    wpc = interaction.guild.get_channel(WHATS_PLAYING_ID)
    if wpc:
        embed = build_spectator_dashboard_embed(event, None, [], [], interaction.guild)
        msg   = await wpc.send(embed=embed)
        try: await msg.pin()
        except: pass
        db_update_event(event_id, {"spectator_msg_id": str(msg.id)})

    # Create private submissions thread
    sub_thread = await ensure_submissions_thread(bot, event_id, interaction.guild, event["name"])

    await interaction.followup.send(
        f"✅ **{event['name']}** started!\n"
        f"Spectator dashboard → {wpc.mention if wpc else '#whats-playing-now'}\n"
        f"Submissions thread → {sub_thread.mention if sub_thread else '#event-noticeboard'}\n"
        f"Use `/round briefing` to begin Day 1.",
        ephemeral=True)
    await log_immediate(bot, "Event Started", f"🏆 **{event['name']}** is LIVE", COLOUR_CRIMSON)

tree.add_command(event_grp)

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS  —  REGISTRATION
# ══════════════════════════════════════════════════════════════════════════════

reg_grp = app_commands.Group(name="register", description="Player registration",
                              guild_ids=[GUILD_ID])

@reg_grp.command(name="submit", description="Register and submit your army list for an event")
@app_commands.describe(event_id="Select event", army="Your faction", detachment="Your detachment")
@app_commands.autocomplete(event_id=ac_active_events, army=ac_armies, detachment=ac_detachments)
async def reg_submit(interaction: discord.Interaction, event_id: str, army: str, detachment: str):
    event = db_get_event(event_id)
    if not event:
        await interaction.response.send_message("❌ Event not found.", ephemeral=True); return
    if event["state"] != ES.REGISTRATION:
        await interaction.response.send_message("❌ Registration not currently open.", ephemeral=True); return
    approved = len(db_get_registrations(event_id, RS.APPROVED))
    pending  = len(db_get_registrations(event_id, RS.PENDING))
    if approved + pending >= event["max_players"]:
        await interaction.response.send_message("❌ Event is full.", ephemeral=True); return
    # Ensure they have at minimum an interest record
    db_upsert_registration(event_id, str(interaction.user.id),
                            interaction.user.display_name, RS.INTERESTED)
    await interaction.response.send_modal(ListSubmissionModal(event_id, army, detachment))

@reg_grp.command(name="drop", description="Drop out of a tournament")
@app_commands.describe(event_id="Select event")
@app_commands.autocomplete(event_id=ac_active_events)
async def reg_drop(interaction: discord.Interaction, event_id: str):
    reg = db_get_registration(event_id, str(interaction.user.id))
    if not reg or reg["state"] == RS.DROPPED:
        await interaction.response.send_message("❌ You're not registered for this event.", ephemeral=True); return
    event = db_get_event(event_id)
    db_update_registration(event_id, str(interaction.user.id),
                            {"state": RS.DROPPED, "dropped_at": datetime.utcnow()})
    # Mark inactive in standings
    db_update_standing(event_id, str(interaction.user.id), {"active": False})
    db_queue_log(f"{interaction.user.display_name} dropped from {event['name']}", event_id, level="drop")
    # Notify TO
    ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        await ch.send(
            f"⚠️ **{interaction.user.display_name}** has dropped from **{event['name']}**.\n"
            f"Use `/round repair` before the next round if pairings need updating."
        )
    await log_immediate(bot, "Player Dropped",
        f"⚠️ **{interaction.user.display_name}** dropped from **{event['name']}**",
        COLOUR_AMBER)
    await interaction.response.send_message(
        f"You've been withdrawn from **{event['name']}**. Your existing results are preserved.",
        ephemeral=True)

@reg_grp.command(name="list", description="[TO] View all registrations")
@app_commands.describe(event_id="Select event")
@app_commands.autocomplete(event_id=ac_all_events)
async def reg_list(interaction: discord.Interaction, event_id: str):
    event = db_get_event(event_id)
    if not event: await interaction.response.send_message("❌ Not found.", ephemeral=True); return
    regs  = db_get_registrations(event_id)
    if not regs: await interaction.response.send_message("No registrations yet.", ephemeral=True); return
    icons = {RS.INTERESTED:"✋", RS.PENDING:"⏳", RS.APPROVED:"✅", RS.REJECTED:"❌", RS.DROPPED:"🚫"}
    embed = discord.Embed(title=f"📋  Registrations — {event['name']}", color=COLOUR_SLATE)
    for state in [RS.APPROVED, RS.PENDING, RS.INTERESTED, RS.DROPPED, RS.REJECTED]:
        group = [r for r in regs if r["state"] == state]
        if group:
            lines = [f"{icons[state]} {fe(r['army'])} **{r['player_username']}** — *{r['army']}*" for r in group]
            embed.add_field(name=state.capitalize(), value="\n".join(lines), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

tree.add_command(reg_grp)

# ══════════════════════════════════════════════════════════════════════════════
