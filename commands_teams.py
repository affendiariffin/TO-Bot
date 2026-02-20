"""
commands_teams.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Team registration & roster management slash commands.

Commands (/team group):
  • /team register
  • /team invite
  • /team kick
  • /team submit-list
  • /team substitute
  • /team drop
  • /team drop-player
  • /team info
  • /team list
  • /team approve-list

Also contains:
  • TeamListSubmitModal
  • ensure_captains_thread / ensure_pairing_room_thread helpers
  • post_captains_council_roster
  • team_is_full / check_army_uniqueness validators
"""
import discord
from discord import app_commands, ui
import asyncio
from datetime import datetime, timezone
from config import (GUILD, GUILD_ID, EVENT_NOTICEBOARD_ID, COLOUR_GOLD,
                    COLOUR_AMBER, COLOUR_SLATE, CAPTAINS_ROLE_ID,
                    CREW_ROLE_ID, PLAYER_ROLE_ID, WARHAMMER_ARMIES)
from state import ES, TS, RS, FMT, is_to, get_thread_reg
from database import *
from threads import (add_player_to_event_threads, ensure_round_thread,
                     create_private_thread, _add_thread_members)
from embeds import build_player_list_embed
from services import (ac_active_events, ac_armies, ac_detachments, log_immediate)

# ══════════════════════════════════════════════════════════════════════════════
# THREAD HELPERS  —  TEAMS (Captains Council + Pairing Room)
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_captains_thread(bot, event_id: str, guild: discord.Guild,
                                  event_name: str) -> Optional[discord.Thread]:
    """Get or create the Captains Council private thread."""
    event = db_get_event(event_id)
    if event and event.get("captains_thread_id"):
        t = guild.get_thread(int(event["captains_thread_id"]))
        if t: return t
    ch = bot.get_channel(EVENT_NOTICEBOARD_ID)
    if not ch: return None
    thread = await create_private_thread(ch, f"⚔️ Captains Council — {event_name}")
    if thread:
        db_update_event(event_id, {"captains_thread_id": str(thread.id)})
        # Add crew
        await _add_thread_members(thread, guild)
        # Add captains by role if configured
        if CAPTAINS_ROLE_ID:
            role = guild.get_role(CAPTAINS_ROLE_ID)
            if role:
                for m in role.members:
                    try: await thread.add_user(m)
                    except: pass
        await thread.send(
            embed=discord.Embed(
                title=f"⚔️ Captains Council — {event_name}",
                description=(
                    "Welcome, Captains. This private channel is for:\n"
                    "• **List review** — all team lists published here at list lock\n"
                    "• **Pre-round briefings** — matchups posted before each round\n"
                    "• **Roll-off results** and judge rulings\n\n"
                    "*Only Captains and Crew can see this thread.*"
                ),
                color=COLOUR_GOLD,
            )
        )
    return thread

async def ensure_pairing_room_thread(bot, event_id: str, guild: discord.Guild,
                                      event_name: str) -> Optional[discord.Thread]:
    """Get or create the Pairing Room thread for live ritual dashboard."""
    event = db_get_event(event_id)
    if event and event.get("pairing_room_thread_id"):
        t = guild.get_thread(int(event["pairing_room_thread_id"]))
        if t: return t
    ch = bot.get_channel(EVENT_NOTICEBOARD_ID)
    if not ch: return None
    thread = await create_private_thread(ch, f"🎲 Pairing Room — {event_name}")
    if thread:
        db_update_event(event_id, {"pairing_room_thread_id": str(thread.id)})
        # Add everyone — all players watch the pairing ritual live
        teams = db_get_teams(event_id)
        player_ids = []
        for team in teams:
            for m in db_get_team_members(team["team_id"]):
                player_ids.append(m["player_id"])
        await _add_thread_members(thread, guild, player_ids if player_ids else None)
        await thread.send(
            embed=discord.Embed(
                title=f"🎲 Pairing Room — {event_name}",
                description=(
                    "This is the **live pairing ritual hub**.\n"
                    "All players are added here to watch pairings unfold in real time.\n\n"
                    "The pairing dashboard will be pinned and updated live each step.\n"
                    "Captains interact privately — results are revealed simultaneously. ⚡"
                ),
                color=COLOUR_AMBER,
            )
        )
    return thread

async def post_captains_council_roster(bot, event_id: str, guild: discord.Guild):
    """Post the current team roster to the Captains Council thread."""
    event = db_get_event(event_id)
    if not event or not event.get("captains_thread_id"): return
    thread = guild.get_thread(int(event["captains_thread_id"]))
    if not thread: return
    teams = db_get_teams(event_id)
    if not teams: return
    lines = []
    for team in teams:
        members = db_get_team_members(team["team_id"])
        captain_name = team["captain_username"]
        member_names = [f"{m['player_username']} ({m['army'] or '?'})" for m in members]
        lines.append(f"**{team['team_name']}** — Captain: {captain_name}\n  " + ", ".join(member_names))
    embed = discord.Embed(
        title="📋 Registered Teams",
        description="\n\n".join(lines) or "No teams registered yet.",
        color=COLOUR_SLATE,
    )
    await thread.send(embed=embed)

# ══════════════════════════════════════════════════════════════════════════════
# TEAM VALIDATION HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def team_is_full(team: dict, members: List[dict]) -> bool:
    team_size = FMT.team_size(team.get("format", "singles")) if "format" in team else None
    # team_size comes from the event
    return False  # evaluated at call site with event context

def check_army_uniqueness(members: List[dict], new_army: str) -> bool:
    """Return True if the army is already on the team (advisory check)."""
    return any(m.get("army") == new_army and m["active"] for m in members)

# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS  —  TEAM MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

team_grp = app_commands.Group(name="team", description="Team registration and management",
                               guild_ids=[GUILD_ID])

@team_grp.command(name="register", description="Register a new team for a team-format event (you become captain)")
@app_commands.describe(event_id="The event to register for", team_name="Your team name")
@app_commands.autocomplete(event_id=ac_active_events)
async def team_register(interaction: discord.Interaction, event_id: str, team_name: str):
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True); return
    if not FMT.is_team(event.get("format", "singles")):
        await interaction.followup.send("❌ This event is Singles format. Use `/register submit` instead.", ephemeral=True); return
    if event["state"] not in (ES.REGISTRATION, ES.INTEREST):
        await interaction.followup.send("❌ Registration is not open for this event.", ephemeral=True); return

    pid = str(interaction.user.id)
    # Check already on a team
    existing = db_get_team_by_player(event_id, pid)
    if existing:
        await interaction.followup.send(f"❌ You're already on team **{existing['team_name']}**.", ephemeral=True); return
    # Check name uniqueness
    all_teams = db_get_teams(event_id)
    if any(t["team_name"].lower() == team_name.lower() for t in all_teams):
        await interaction.followup.send(f"❌ A team named **{team_name}** already exists.", ephemeral=True); return

    tid = db_create_team(event_id, team_name, pid, interaction.user.display_name)
    # Add captain as first member
    db_add_team_member(tid, event_id, pid, interaction.user.display_name, role="captain")

    team_sz = FMT.team_size(event["format"])
    await interaction.followup.send(
        f"✅ Team **{team_name}** registered! You are the captain.\n"
        f"Use `/team invite` to add {team_sz - 1} more player(s).\n"
        f"Team ID: `{tid}`",
        ephemeral=True
    )

    ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        await ch.send(f"⚔️ **{team_name}** has registered for **{event['name']}**! Captain: {interaction.user.mention}")

    # Open/ensure Captains Council thread if not yet created
    asyncio.create_task(ensure_captains_thread(bot, event_id, interaction.guild, event["name"]))


@team_grp.command(name="invite", description="[Captain] Invite a player to your team")
@app_commands.describe(event_id="The event", member="The Discord member to invite")
@app_commands.autocomplete(event_id=ac_active_events)
async def team_invite(interaction: discord.Interaction, event_id: str, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True); return

    pid = str(interaction.user.id)
    team = db_get_team_by_captain(event_id, pid)
    if not team:
        await interaction.followup.send("❌ You are not a captain for this event.", ephemeral=True); return
    if team["state"] == TS.DROPPED:
        await interaction.followup.send("❌ Your team has been dropped.", ephemeral=True); return

    target_id = str(member.id)
    if target_id == pid:
        await interaction.followup.send("❌ You can't invite yourself.", ephemeral=True); return

    # Check target not already on a team
    existing = db_get_team_by_player(event_id, target_id)
    if existing:
        await interaction.followup.send(f"❌ {member.display_name} is already on team **{existing['team_name']}**.", ephemeral=True); return

    members = db_get_team_members(team["team_id"])
    team_sz = FMT.team_size(event["format"])
    if len(members) >= team_sz:
        await interaction.followup.send(f"❌ Your team is already full ({team_sz} players).", ephemeral=True); return

    # Army uniqueness check (advisory)
    reg = db_get_registration(event_id, target_id)
    army_warning = ""
    if reg and reg.get("army"):
        if check_army_uniqueness(members, reg["army"]):
            army_warning = f"\n⚠️ **Warning:** {member.display_name}'s army ({reg['army']}) is already on your team. Each army should be unique."

    db_add_team_member(team["team_id"], event_id, target_id, member.display_name,
                        role="player",
                        army=reg.get("army") if reg else None,
                        detachment=reg.get("detachment") if reg else None)

    # Check if team is now full → mark ready
    updated_members = db_get_team_members(team["team_id"])
    if len(updated_members) >= team_sz:
        db_update_team(team["team_id"], {"state": TS.READY})
        ready_msg = f"\n✅ Team **{team['team_name']}** is now full and ready!"
    else:
        remaining = team_sz - len(updated_members)
        ready_msg = f"\n{remaining} more player(s) needed."

    await interaction.followup.send(
        f"✅ **{member.display_name}** added to **{team['team_name']}**.{army_warning}{ready_msg}",
        ephemeral=True
    )
    # Notify the invited player
    try:
        await member.send(
            f"⚔️ You've been added to team **{team['team_name']}** for **{event['name']}** "
            f"by captain {interaction.user.display_name}."
        )
    except: pass


@team_grp.command(name="kick", description="[Captain] Remove a player from your team")
@app_commands.describe(event_id="The event", member="The Discord member to remove")
@app_commands.autocomplete(event_id=ac_active_events)
async def team_kick(interaction: discord.Interaction, event_id: str, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True); return

    pid = str(interaction.user.id)
    team = db_get_team_by_captain(event_id, pid)
    if not team:
        await interaction.followup.send("❌ You are not a captain for this event.", ephemeral=True); return

    target_id = str(member.id)
    if target_id == pid:
        await interaction.followup.send("❌ You can't kick yourself. Use `/team drop` to drop the whole team.", ephemeral=True); return

    tm = db_get_team_member(team["team_id"], target_id)
    if not tm or not tm["active"]:
        await interaction.followup.send(f"❌ {member.display_name} is not on your team.", ephemeral=True); return

    db_update_team_member(team["team_id"], target_id, {"active": False, "dropped_at": datetime.utcnow()})
    # Revert team to forming if it was ready
    if team["state"] == TS.READY:
        db_update_team(team["team_id"], {"state": TS.FORMING})

    await interaction.followup.send(
        f"✅ **{member.display_name}** removed from **{team['team_name']}**. Team is back to forming.",
        ephemeral=True
    )
    try:
        await member.send(f"ℹ️ You have been removed from team **{team['team_name']}** for **{event['name']}**.")
    except: pass


@team_grp.command(name="submit-list", description="[Captain] Submit or update an army list for a team member")
@app_commands.describe(
    event_id="The event",
    member="The team member whose list you're submitting",
    army="Their army/faction",
    detachment="Their detachment",
)
@app_commands.autocomplete(event_id=ac_active_events, army=ac_armies, detachment=ac_detachments)
async def team_submit_list(interaction: discord.Interaction, event_id: str,
                             member: discord.Member, army: str, detachment: str):
    """Opens a modal so the captain can paste the list."""
    event = db_get_event(event_id)
    if not event:
        await interaction.response.send_message("❌ Event not found.", ephemeral=True); return

    pid = str(interaction.user.id)
    team = db_get_team_by_captain(event_id, pid)
    if not team:
        await interaction.response.send_message("❌ You are not a captain for this event.", ephemeral=True); return

    target_id = str(member.id)
    tm = db_get_team_member(team["team_id"], target_id)
    if not tm or not tm["active"]:
        await interaction.response.send_message(f"❌ {member.display_name} is not on your team.", ephemeral=True); return

    modal = TeamListSubmitModal(team_id=team["team_id"], event_id=event_id,
                                 player_id=target_id, player_name=member.display_name,
                                 army=army, detachment=detachment,
                                 ind_pts=event.get("individual_points", 2000))
    await interaction.response.send_modal(modal)


class TeamListSubmitModal(ui.Modal, title="Submit Team Member List"):
    list_text = ui.TextInput(
        label="Army List",
        style=discord.TextStyle.paragraph,
        placeholder="Paste the full army list here...",
        required=True, max_length=4000,
    )

    def __init__(self, team_id: str, event_id: str, player_id: str, player_name: str,
                  army: str, detachment: str, ind_pts: int):
        super().__init__()
        self.team_id    = team_id
        self.event_id   = event_id
        self.player_id  = player_id
        self.player_name = player_name
        self.army       = army
        self.detachment = detachment
        self.ind_pts    = ind_pts
        self.list_text.label = f"Army List for {player_name} ({ind_pts}pts)"

    async def on_submit(self, interaction: discord.Interaction):
        db_update_team_member(self.team_id, self.player_id, {
            "army": self.army,
            "detachment": self.detachment,
            "list_text": self.list_text.value,
            "list_approved": False,
        })
        await interaction.response.send_message(
            f"✅ List submitted for **{self.player_name}** ({self.army} — {self.detachment}). "
            f"Awaiting TO approval.",
            ephemeral=True
        )
        db_queue_log(f"Team list submitted: {self.player_name} ({self.army}) in team {self.team_id}", self.event_id)


@team_grp.command(name="substitute", description="[Captain] Register a substitute player (replaces a dropped/unavailable member)")
@app_commands.describe(event_id="The event", member="The new substitute player")
@app_commands.autocomplete(event_id=ac_active_events)
async def team_substitute(interaction: discord.Interaction, event_id: str, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True); return

    pid = str(interaction.user.id)
    team = db_get_team_by_captain(event_id, pid)
    if not team:
        await interaction.followup.send("❌ You are not a captain for this event.", ephemeral=True); return

    target_id = str(member.id)
    existing = db_get_team_by_player(event_id, target_id)
    if existing:
        await interaction.followup.send(f"❌ {member.display_name} is already on team **{existing['team_name']}**.", ephemeral=True); return

    db_add_team_member(team["team_id"], event_id, target_id, member.display_name, role="substitute")
    await interaction.followup.send(
        f"✅ **{member.display_name}** registered as a substitute for **{team['team_name']}**.\n"
        f"⚠️ Substitutes cannot change the team's list submissions. TO approval required before they play.",
        ephemeral=True
    )
    ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        await ch.send(
            f"🔄 **{team['team_name']}** has registered a substitute: {member.mention}  *(TO approval required)*"
        )


@team_grp.command(name="drop", description="[Captain] Drop your entire team from the event")
@app_commands.describe(event_id="The event")
@app_commands.autocomplete(event_id=ac_active_events)
async def team_drop(interaction: discord.Interaction, event_id: str):
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True); return

    pid = str(interaction.user.id)
    team = db_get_team_by_captain(event_id, pid)
    if not team:
        # Allow TO to drop any team by providing team_id — handled in /team drop-player
        await interaction.followup.send("❌ You are not a captain for this event.", ephemeral=True); return
    if team["state"] == TS.DROPPED:
        await interaction.followup.send("❌ Team is already dropped.", ephemeral=True); return

    db_update_team(team["team_id"], {"state": TS.DROPPED})
    ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if ch:
        await ch.send(
            f"📤 **{team['team_name']}** has withdrawn from **{event['name']}**.\n"
            f"Their remaining fixtures will be forfeited. *(TO: re-run Swiss if before round start.)*"
        )
    await interaction.followup.send(
        f"✅ Team **{team['team_name']}** has been dropped. Thank you for letting us know.",
        ephemeral=True
    )
    await log_immediate(bot, "Team Dropped",
        f"📤 **{team['team_name']}** dropped from {event['name']} by captain {interaction.user.display_name}",
        COLOUR_CRIMSON)


@team_grp.command(name="drop-player", description="[Captain/TO] Drop a single player from a team (triggers sub/bye logic)")
@app_commands.describe(event_id="The event", member="The player to drop")
@app_commands.autocomplete(event_id=ac_active_events)
async def team_drop_player(interaction: discord.Interaction, event_id: str, member: discord.Member):
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True); return

    pid       = str(interaction.user.id)
    target_id = str(member.id)
    is_crew   = is_to(interaction)

    team = db_get_team_by_captain(event_id, pid)
    if not team and not is_crew:
        await interaction.followup.send("❌ Only the team captain or Crew can drop a player.", ephemeral=True); return
    if not team:
        # TO path — find the player's team
        team = db_get_team_by_player(event_id, target_id)
    if not team:
        await interaction.followup.send("❌ Player is not on any team for this event.", ephemeral=True); return

    tm = db_get_team_member(team["team_id"], target_id)
    if not tm or not tm["active"]:
        await interaction.followup.send(f"❌ {member.display_name} is not an active member of **{team['team_name']}**.", ephemeral=True); return
    if tm["role"] == "captain" and not is_crew:
        await interaction.followup.send("❌ Captains cannot self-drop via this command. Use `/team drop` to drop the whole team.", ephemeral=True); return

    db_update_team_member(team["team_id"], target_id, {"active": False, "dropped_at": datetime.utcnow()})

    team_sz      = FMT.team_size(event["format"])
    active_count = len(db_get_team_members(team["team_id"]))
    subs         = [m for m in db_get_team_members(team["team_id"]) if m["role"] == "substitute"]

    if active_count < team_sz and not subs:
        # Below minimum — team must forfeit
        db_update_team(team["team_id"], {"state": TS.DROPPED})
        ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
        if ch:
            await ch.send(
                f"⚠️ **{team['team_name']}** is below minimum roster after dropping {member.display_name} "
                f"and has no substitute registered. **Team forfeited.** Opponent receives walkover.\n"
                f"*TO: please re-run Swiss if before round start.*"
            )
        await interaction.followup.send(
            f"⚠️ {member.display_name} dropped. Team is below minimum and has been forfeited.",
            ephemeral=True
        )
    else:
        sub_note = f" A substitute ({subs[0]['player_username']}) is available — TO approval needed." if subs else ""
        ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
        if ch:
            await ch.send(
                f"⚠️ **{member.display_name}** has dropped from **{team['team_name']}**.{sub_note}\n"
                f"*TO: check if a substitute is needed before the next pairing ritual.*"
            )
        await interaction.followup.send(
            f"✅ {member.display_name} dropped from **{team['team_name']}**.{sub_note}",
            ephemeral=True
        )

    await log_immediate(bot, "Player Dropped from Team",
        f"📤 {member.display_name} dropped from **{team['team_name']}** ({event['name']})",
        COLOUR_CRIMSON)


@team_grp.command(name="info", description="View a team's roster and list status")
@app_commands.describe(event_id="The event", team_name="Team name (leave blank to see your own team)")
@app_commands.autocomplete(event_id=ac_active_events)
async def team_info(interaction: discord.Interaction, event_id: str, team_name: str = None):
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True); return

    pid = str(interaction.user.id)
    if team_name:
        all_teams = db_get_teams(event_id)
        team = next((t for t in all_teams if t["team_name"].lower() == team_name.lower()), None)
    else:
        team = db_get_team_by_player(event_id, pid)

    if not team:
        await interaction.followup.send("❌ Team not found. Are you registered for this event?", ephemeral=True); return

    members = db_get_team_members(team["team_id"])
    team_sz = FMT.team_size(event["format"])

    lines = []
    for m in members:
        role_icon = "👑" if m["role"] == "captain" else ("🔄" if m["role"] == "substitute" else "⚔️")
        army_str  = f"{fe(m['army'])} {m['army']}" if m.get("army") else "*(no army set)*"
        list_str  = "✅ Approved" if m["list_approved"] else ("📋 Submitted" if m.get("list_text") else "❌ No list")
        lines.append(f"{role_icon} **{m['player_username']}** — {army_str}  |  {list_str}")

    state_icon = {"forming": "🔧", "ready": "✅", "dropped": "💀"}.get(team["state"], "❓")
    embed = discord.Embed(
        title=f"{state_icon} {team['team_name']}",
        description="\n".join(lines) or "No members.",
        color=COLOUR_GOLD if team["state"] == TS.READY else COLOUR_SLATE,
    )
    embed.add_field(name="Event", value=event["name"], inline=True)
    embed.add_field(name="Format", value=event.get("format", "singles").replace("_"," ").title(), inline=True)
    embed.add_field(name="Players", value=f"{len(members)}/{team_sz}", inline=True)
    embed.set_footer(text=f"Team ID: {team['team_id']}")
    await interaction.followup.send(embed=embed, ephemeral=True)


@team_grp.command(name="list", description="[TO] List all teams registered for an event")
@app_commands.describe(event_id="The event")
@app_commands.autocomplete(event_id=ac_active_events)
async def team_list(interaction: discord.Interaction, event_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ Crew only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True); return

    teams = db_get_teams(event_id)
    if not teams:
        await interaction.followup.send("No teams registered yet.", ephemeral=True); return

    team_sz = FMT.team_size(event["format"])
    lines = []
    for t in teams:
        members = db_get_team_members(t["team_id"])
        all_listed = all(m.get("list_text") for m in members)
        all_approved = all(m.get("list_approved") for m in members)
        icon = "✅" if all_approved else ("📋" if all_listed else "⏳")
        state_tag = f" *(dropped)*" if t["state"] == TS.DROPPED else ""
        lines.append(f"{icon} **{t['team_name']}** ({len(members)}/{team_sz}){state_tag} — Capt: {t['captain_username']}")

    embed = discord.Embed(
        title=f"📋 Teams — {event['name']}",
        description="\n".join(lines),
        color=COLOUR_SLATE,
    )
    embed.set_footer(text=f"{len(teams)} teams · ✅=all lists approved · 📋=all submitted · ⏳=pending")
    await interaction.followup.send(embed=embed, ephemeral=True)


@team_grp.command(name="approve-list", description="[TO] Approve a team member's list")
@app_commands.describe(event_id="The event", member="The player whose list to approve")
@app_commands.autocomplete(event_id=ac_active_events)
async def team_approve_list(interaction: discord.Interaction, event_id: str, member: discord.Member):
    if not is_to(interaction):
        await interaction.response.send_message("❌ Crew only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True); return

    target_id = str(member.id)
    team = db_get_team_by_player(event_id, target_id)
    if not team:
        await interaction.followup.send(f"❌ {member.display_name} is not on any team for this event.", ephemeral=True); return

    tm = db_get_team_member(team["team_id"], target_id)
    if not tm or not tm.get("list_text"):
        await interaction.followup.send(f"❌ {member.display_name} has not submitted a list yet.", ephemeral=True); return

    db_update_team_member(team["team_id"], target_id, {"list_approved": True})

    # Check if all team lists are now approved
    members = db_get_team_members(team["team_id"])
    team_sz = FMT.team_size(event["format"])
    non_subs = [m for m in members if m["role"] != "substitute"]
    all_approved = all(m["list_approved"] for m in non_subs) and len(non_subs) >= team_sz

    if all_approved and team["state"] == TS.FORMING:
        db_update_team(team["team_id"], {"state": TS.READY})
        ready_note = f"\n✅ All lists approved — **{team['team_name']}** is now **ready**!"
    else:
        approved_count = sum(1 for m in non_subs if m["list_approved"])
        ready_note = f"\n{approved_count}/{len(non_subs)} lists approved."

    await interaction.followup.send(
        f"✅ List approved for **{member.display_name}** ({tm.get('army', '?')}).{ready_note}",
        ephemeral=True
    )
    db_queue_log(f"List approved: {member.display_name} ({tm.get('army','?')}) in {team['team_name']}", event_id)

    # Notify captain
    try:
        captain = interaction.guild.get_member(int(team["captain_id"]))
        if captain:
            await captain.send(
                f"✅ List approved for **{member.display_name}** ({tm.get('army','?')}) on team **{team['team_name']}**.{ready_note}"
            )
    except: pass


import random as _random

# ══════════════════════════════════════════════════════════════════════════════
# PAIRING RITUAL  —  TEAMS 3s / 5s  (Chunk 4)
# Simultaneous reveal via DM selection + DB polling
# Public live dashboard in the Pairing Room thread
# ══════════════════════════════════════════════════════════════════════════════
