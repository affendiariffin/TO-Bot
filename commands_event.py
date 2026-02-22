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

Command groups are registered in main.py via tree.add_command().
Call init(bot_instance) from main.py's on_ready to wire up the bot reference.
"""
import discord
from discord import app_commands
import asyncio
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict
from config import (GUILD_ID, GUILD, EVENT_NOTICEBOARD_ID, WHATS_PLAYING_ID,
                    COLOUR_GOLD, COLOUR_CRIMSON, COLOUR_AMBER, COLOUR_SLATE,
                    fe, faction_colour)
from state import ES, RS, FMT, is_to, get_thread_reg
from database import *
from threads import (ensure_submissions_thread, ensure_lists_thread,
                     add_player_to_event_threads, event_round_count,
                     ensure_all_round_threads)
from embeds import (build_event_announcement_embed, build_list_review_header,
                    build_player_list_embed, build_spectator_dashboard_embed,
                    build_event_main_embed, build_schedule_embed,
                    build_missions_embed, build_judges_on_duty_embed,
                    build_standings_embed)
from views import EventAnnouncementView, RegistrationApprovalView
from services import (refresh_spectator_dashboard, ac_active_events,
                      ac_all_events, ac_missions, ac_armies, ac_detachments,
                      ac_pending_regs, log_immediate)

# ── Bot reference (set via init()) ───────────────────────────────────────────
bot = None

def init(bot_instance):
    """Called from main.py after bot is created to wire up the bot reference."""
    global bot
    bot = bot_instance

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS  —  EVENT MANAGEMENT  (TO only)
# ══════════════════════════════════════════════════════════════════════════════

event_grp = app_commands.Group(
    name="event",
    description="Tournament event management",
    guild_ids=[GUILD_ID],
    default_permissions=discord.Permissions(use_application_commands=True),
)

@event_grp.command(name="create", description="Create a new TTS tournament event")
@app_commands.describe(
    name="Event name",
    mission="Default mission code (used as fallback; singles/2v2 must also set pairings)",
    points_limit="Points limit (e.g. 2000)",
    max_players="Maximum number of players (or teams for team formats)",
    start_date="Start date (YYYY-MM-DD)",
    end_date="End date (YYYY-MM-DD)",
    round_count="Number of rounds: 3 (up to 8 players/teams) or 5 (up to 32)",
    rounds_per_day="Rounds per day",
    terrain_layout="Optional terrain layout notes",
    format="Event format",
    pairings="Singles/2v2: comma-separated Layout:MissionCode pairs for every round e.g. 1:A,4:C,8:M",
    layouts="Team formats: comma-separated layout numbers for ritual pool, max 3 (e.g. 1,4,8)",
    missions="Team formats: comma-separated mission codes for ritual pool, max 3 (e.g. A,C,M)",
)
@app_commands.autocomplete(mission=ac_missions)
@app_commands.choices(
    format=[
        app_commands.Choice(name="Singles",   value="singles"),
        app_commands.Choice(name="2v2",       value="2v2"),
        app_commands.Choice(name="Teams 3s",  value="teams_3"),
        app_commands.Choice(name="Teams 5s",  value="teams_5"),
        app_commands.Choice(name="Teams 8s",  value="teams_8"),
    ],
    round_count=[
        app_commands.Choice(name="3 rounds (≤8 players/teams)",  value=3),
        app_commands.Choice(name="5 rounds (≤32 players/teams)", value=5),
    ],
)
async def event_create(
    interaction: discord.Interaction,
    name: str,
    mission: str,
    points_limit: int,
    max_players: int,
    start_date: str,
    end_date: str,
    round_count: int = 3,
    rounds_per_day: int = 3,
    terrain_layout: str = "",
    format: str = "singles",
    pairings: str = "",
    layouts: str = "",
    missions: str = "",
):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)

    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date,   "%Y-%m-%d").date()
    except ValueError:
        await interaction.followup.send("❌ Date format must be YYYY-MM-DD.", ephemeral=True); return

    mission_obj = db_get_mission(mission)
    if not mission_obj:
        await interaction.followup.send("❌ Invalid mission code.", ephemeral=True); return

    # ── Validate round count ──────────────────────────────────────────────
    if round_count not in (3, 5):
        await interaction.followup.send("❌ round_count must be 3 or 5.", ephemeral=True); return

    team_sz  = FMT.team_size(format)
    ind_pts  = FMT.individual_points(format)

    # ── Format-specific mission/layout validation ─────────────────────────
    event_pairings  = []
    event_layouts   = []
    event_missions  = []

    if format in ("singles", "2v2"):
        # Require explicit pairings: one per round, e.g. "1:A,4:C,8:M"
        if not pairings.strip():
            await interaction.followup.send(
                f"❌ Singles/2v2 events require `pairings` — one Layout:Mission pair per round.\n"
                f"Example for {round_count} rounds: `1:A,4:C,8:M`" + (",6:B,4:D" if round_count == 5 else ""),
                ephemeral=True,
            ); return

        raw_pairs = [p.strip() for p in pairings.split(",") if p.strip()]
        if len(raw_pairs) != round_count:
            await interaction.followup.send(
                f"❌ Expected exactly {round_count} Layout:Mission pairs (one per round), "
                f"got {len(raw_pairs)}.",
                ephemeral=True,
            ); return

        all_missions = db_get_missions()
        seen = set()
        for raw in raw_pairs:
            parts = raw.split(":")
            if len(parts) != 2:
                await interaction.followup.send(
                    f"❌ Bad pair format `{raw}` — use Layout:MissionCode, e.g. `1:A`.",
                    ephemeral=True,
                ); return
            layout_num, mission_code = parts[0].strip(), parts[1].strip().upper()

            # Check mission exists
            m_obj = all_missions.get(mission_code)
            if not m_obj:
                await interaction.followup.send(
                    f"❌ Unknown mission code `{mission_code}`. Check `/mission list`.",
                    ephemeral=True,
                ); return

            # Check layout is valid for this mission
            if layout_num not in m_obj["layouts"]:
                await interaction.followup.send(
                    f"❌ Layout **{layout_num}** is not valid for mission **{mission_code}** "
                    f"({m_obj['name']}). Valid layouts: {', '.join(m_obj['layouts'])}.",
                    ephemeral=True,
                ); return

            # Check for duplicate combinations
            combo_key = (layout_num, mission_code)
            if combo_key in seen:
                await interaction.followup.send(
                    f"❌ Duplicate pairing: Layout {layout_num} + Mission {mission_code} "
                    f"appears more than once.",
                    ephemeral=True,
                ); return
            seen.add(combo_key)
            event_pairings.append({"layout": layout_num, "mission": mission_code})

    else:
        # Team formats: optional pools, warn if not set
        event_layouts  = [l.strip() for l in layouts.split(",")  if l.strip()][:3]
        event_missions = [m.strip() for m in missions.split(",") if m.strip()][:3]
        for code in event_missions:
            if not db_get_mission(code):
                await interaction.followup.send(
                    f"❌ Unknown mission code `{code}`. Check `/mission list`.",
                    ephemeral=True,
                ); return

    eid = db_create_event({
        "name": name, "mission_code": mission, "points_limit": points_limit,
        "start_date": sd, "end_date": ed, "max_players": max_players,
        "round_count": round_count, "rounds_per_day": rounds_per_day,
        "terrain_layout": terrain_layout, "created_by": str(interaction.user.id),
    })
    db_update_event(eid, {
        "format": format, "team_size": team_sz, "individual_points": ind_pts,
        "event_pairings": event_pairings,
        "event_layouts": event_layouts, "event_missions": event_missions,
    })

    event = db_get_event(eid)
    embed = build_event_announcement_embed(event)
    view  = EventAnnouncementView(eid)

    ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        msg = await ch.send(embed=embed, view=view)
        db_update_event(eid, {"noticeboard_msg_id": str(msg.id)})
        try: await msg.pin()
        except: pass

    # Discord Scheduled Event
    try:
        start_dt = datetime.combine(sd, datetime.min.time()).replace(hour=9, tzinfo=timezone.utc)
        m = mission_obj
        fmt_label = {
            "singles": "Singles", "2v2": "2v2",
            "teams_3": "Teams 3s", "teams_5": "Teams 5s", "teams_8": "Teams 8s",
        }.get(format, "Singles")
        disc_evt = await interaction.guild.create_scheduled_event(
            name=name,
            description=(
                f"⚔️ Warhammer 40k TTS Tournament — {ind_pts}pts  [{fmt_label}]\n"
                f"🗺️ {m['name']} [{m['deployment']}]\n"
                f"Register interest in #event-noticeboard"
            ),
            start_time=start_dt,
            end_time=start_dt + timedelta(hours=8),
            entity_type=discord.EntityType.external,
            location="Tabletop Simulator",
            privacy_level=discord.PrivacyLevel.guild_only,
        )
        db_update_event(eid, {"discord_event_id": str(disc_evt.id)})
    except Exception as e:
        print(f"⚠️ Discord event creation failed: {e}")

    fmt_label = {
        "singles": "Singles", "2v2": "2v2",
        "teams_3": "Teams 3s", "teams_5": "Teams 5s", "teams_8": "Teams 8s",
    }.get(format, "Singles")

    # Confirmation summary
    confirm = (
        f"✅ **{name}** created — `{eid}`\n"
        f"Format: **{fmt_label}** · {ind_pts}pts per player · **{round_count} rounds** · {rounds_per_day}/day\n"
    )
    if format in ("singles", "2v2"):
        pair_lines = [f"  R{i+1}: Layout {p['layout']} + Mission {p['mission']}" for i, p in enumerate(event_pairings)]
        confirm += "Round pairings:\n" + "\n".join(pair_lines)
    else:
        layout_str  = ", ".join(f"Layout {l}" for l in event_layouts)  if event_layouts  else "⚠️ none (set with /event set-layouts)"
        mission_str = ", ".join(event_missions)                          if event_missions else "⚠️ none (set with /event set-missions)"
        confirm += f"Layout pool: {layout_str}\nMission pool: {mission_str}"

    await interaction.followup.send(confirm, ephemeral=True)
    mission_name = mission_obj["name"]
    await log_immediate(
        interaction.client,
        "Event Created",
        f"🏆 **{name}** by {interaction.user.display_name}\n"
        f"Format: {fmt_label} · {ind_pts}pts · {round_count} rounds · {sd}→{ed}",
        COLOUR_GOLD,
    )

@event_grp.command(name="open-interest", description="Open interest registration")
@app_commands.autocomplete(event_id=ac_active_events)
async def event_open_interest(interaction: discord.Interaction, event_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    event = db_get_event(event_id)
    if not event:
        await interaction.response.send_message("❌ Not found.", ephemeral=True); return
    db_update_event(event_id, {"state": ES.INTEREST})
    ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        await ch.send(
            f"📣 **Interest registration open for {event['name']}!**\n"
            f"Click **Register Interest** on the event card above."
        )
    await interaction.response.send_message("✅ Interest phase opened.", ephemeral=True)

@event_grp.command(name="open-registration", description="Open full list registration")
@app_commands.autocomplete(event_id=ac_active_events)
async def event_open_registration(interaction: discord.Interaction, event_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Not found.", ephemeral=True); return
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
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Not found.", ephemeral=True); return
    regs = db_get_registrations(event_id, RS.APPROVED)

    lists_thread = await ensure_lists_thread(interaction.client, event_id, interaction.guild, event["name"])
    target = lists_thread or interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)

    if target:
        await target.send(embed=build_list_review_header(event, regs))
        for i, reg in enumerate(regs, 1):
            await target.send(embed=build_player_list_embed(reg, i))
            await asyncio.sleep(0.4)

    ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch and lists_thread:
        await ch.send(
            f"📋 **Army lists published for {event['name']}**\n"
            f"Approved players: check {lists_thread.mention} to review your opponents."
        )

    # DM approved players
    for reg in regs:
        try:
            user = await interaction.client.fetch_user(int(reg["player_id"]))
            await user.send(
                f"📋 **Army lists published for {event['name']}!**\n"
                f"Check the Army Lists thread in #event-noticeboard to review your opponents."
            )
        except: pass

    await interaction.followup.send(
        f"✅ {len(regs)} lists published to {lists_thread.mention if lists_thread else '#event-noticeboard'}.",
        ephemeral=True,
    )

# FIX: removed duplicate @event_grp.command(name="start") decorator that was causing
# Discord.py to raise an error at startup (duplicate command registration).
@event_grp.command(name="start", description="Start the event — post pinned cards and create threads")
@app_commands.autocomplete(event_id=ac_active_events)
async def event_start(interaction: discord.Interaction, event_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)

    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Not found.", ephemeral=True); return

    db_update_event(event_id, {"state": ES.IN_PROGRESS})

    nb_ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    regs  = db_get_registrations(event_id, RS.APPROVED)
    total_rounds = event_round_count(event)

    pinned_msgs = []

    # ── 5 pinned cards ────────────────────────────────────────────────────
    if nb_ch:
        for embed in [
            build_event_main_embed(event, regs),
            build_schedule_embed(event),
            build_missions_embed(event),
            build_judges_on_duty_embed(interaction.guild),
            build_standings_embed(event, []),
        ]:
            msg = await nb_ch.send(embed=embed)
            pinned_msgs.append(msg)
            try:
                await msg.pin()
            except discord.HTTPException:
                pass
            await asyncio.sleep(0.5)

        # Store judge card msg_id and standings msg_id for refresh
        reg = get_thread_reg(event_id)
        reg["judge_msg_id"]     = pinned_msgs[3].id
        reg["standings_msg_id"] = pinned_msgs[4].id
        db_update_event(event_id, {
            "noticeboard_msg_id": str(pinned_msgs[0].id),
            "standings_msg_id":   str(pinned_msgs[4].id),
        })

    # ── Pre-create all round pairing threads (empty) ──────────────────────
    round_threads = await ensure_all_round_threads(
        interaction.client, event_id, interaction.guild,
        event["name"], total_rounds,
    )

    # ── Army Lists thread ─────────────────────────────────────────────────
    lists_thread = await ensure_lists_thread(
        interaction.client, event_id, interaction.guild, event["name"]
    )

    # ── Spectator dashboard in #what's-playing-now ────────────────────────
    wpc = interaction.guild.get_channel(WHATS_PLAYING_ID)
    if wpc:
        standings = db_get_standings(event_id)
        dash_embed = build_spectator_dashboard_embed(event, None, [], standings, interaction.guild)
        dash_msg   = await wpc.send(embed=dash_embed)
        try:
            await dash_msg.pin()
        except discord.HTTPException:
            pass
        db_update_event(event_id, {"spectator_msg_id": str(dash_msg.id)})

    thread_list = "  ·  ".join(
        f"Round {rn}" for rn in sorted(round_threads.keys())
    )
    await interaction.followup.send(
        f"✅ **{event['name']}** started!\n"
        f"5 cards pinned in {nb_ch.mention if nb_ch else '#event-noticeboard'}\n"
        f"Round threads created: {thread_list}\n"
        f"Army Lists thread: {lists_thread.mention if lists_thread else '—'}\n"
        f"Use `/round briefing` to begin Day 1.",
        ephemeral=True,
    )
    # FIX: removed duplicate log_immediate call that was sending two identical log messages
    await log_immediate(
        interaction.client, "Event Started",
        f"🏆 **{event['name']}** is LIVE\n"
        f"{total_rounds} round threads created  ·  5 cards pinned",
        COLOUR_CRIMSON,
    )

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS  —  REGISTRATION
# ══════════════════════════════════════════════════════════════════════════════

reg_grp = app_commands.Group(
    name="register",
    description="Player registration",
    guild_ids=[GUILD_ID],
)

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
    db_update_standing(event_id, str(interaction.user.id), {"active": False})
    db_queue_log(f"{interaction.user.display_name} dropped from {event['name']}", event_id, level="drop")
    ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        await ch.send(
            f"⚠️ **{interaction.user.display_name}** has dropped from **{event['name']}**.\n"
            f"Use `/round repair` before the next round if pairings need updating."
        )
    await log_immediate(
        interaction.client,
        "Player Dropped",
        f"⚠️ **{interaction.user.display_name}** dropped from **{event['name']}**",
        COLOUR_AMBER,
    )
    await interaction.response.send_message(
        f"You've been withdrawn from **{event['name']}**. Your existing results are preserved.",
        ephemeral=True,
    )

@reg_grp.command(name="list", description="[TO] View all registrations")
@app_commands.describe(event_id="Select event")
@app_commands.autocomplete(event_id=ac_all_events)
async def reg_list(interaction: discord.Interaction, event_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True); return
    event = db_get_event(event_id)
    if not event:
        await interaction.response.send_message("❌ Not found.", ephemeral=True); return
    regs = db_get_registrations(event_id)
    if not regs:
        await interaction.response.send_message("No registrations yet.", ephemeral=True); return
    icons = {RS.INTERESTED:"✋", RS.PENDING:"⏳", RS.APPROVED:"✅", RS.REJECTED:"❌", RS.DROPPED:"🚫"}
    embed = discord.Embed(title=f"📋  Registrations — {event['name']}", color=COLOUR_SLATE)
    for state in [RS.APPROVED, RS.PENDING, RS.INTERESTED, RS.DROPPED, RS.REJECTED]:
        group = [r for r in regs if r["state"] == state]
        if group:
            lines = [f"{icons[state]} {fe(r['army'])} **{r['player_username']}** — *{r['army']}*" for r in group]
            embed.add_field(name=state.capitalize(), value="\n".join(lines), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# MODAL  —  list submission (used by reg_submit and RegistrationApprovalView)
# ══════════════════════════════════════════════════════════════════════════════

class ListSubmissionModal(discord.ui.Modal, title="Submit Army List"):
    list_text = discord.ui.TextInput(
        label="Paste your army list here",
        style=discord.TextStyle.paragraph,
        placeholder="Copy-paste your list from Wahapedia / BattleScribe / New Recruit…",
        required=True,
        max_length=4000,
    )

    def __init__(self, event_id: str, army: str, detachment: str):
        super().__init__()
        self.event_id   = event_id
        self.army       = army
        self.detachment = detachment

    async def on_submit(self, interaction: discord.Interaction):
        event = db_get_event(self.event_id)
        if not event:
            await interaction.response.send_message("❌ Event not found.", ephemeral=True); return

        db_upsert_registration(
            self.event_id, str(interaction.user.id),
            interaction.user.display_name, RS.PENDING,
            army=self.army, det=self.detachment,
            list_text=self.list_text.value,
          )

        # Post to submissions thread for TO review
        sub_thread = await ensure_submissions_thread(
            interaction.client, self.event_id, interaction.guild, event["name"]
        )
        if sub_thread:
            view  = RegistrationApprovalView(self.event_id, str(interaction.user.id))
            embed = discord.Embed(
                title=f"📋  List Submitted  —  {interaction.user.display_name}",
                description=(
                    f"{fe(self.army)} **{self.army}**  ·  *{self.detachment}*\n\n"
                    f"```\n{self.list_text.value[:1800]}\n```"
                ),
                color=COLOUR_AMBER,
            )
            await sub_thread.send(embed=embed, view=view)

        await interaction.response.send_message(
            f"✅ List submitted for **{event['name']}**!\n"
            f"Army: {fe(self.army)} **{self.army}**  ·  *{self.detachment}*\n"
            f"A TO will review and approve your registration shortly.",
            ephemeral=True,
        )
