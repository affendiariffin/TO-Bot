"""
commands_event.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Slash commands for event lifecycle management (TO only).

Singles registration flow (spec):
  1.  TO creates event with name / points / max_players / date.
      Bot auto-determines round count (<=16 -> 3 rounds, 32 -> 5 rounds).
      Bot builds full KL-time schedule and posts event card in #event-noticeboard.
      Card shows three sections: Confirmed / Chop / Reserve.
      Card shows Rules Cutoff (event_date - 7 days) and Reg Deadline (event_date - 2 days).

  2.  Player clicks "Chop" on the event card and uploads their list via a modal.
      Bot creates a PRIVATE thread (player + bot + TO only) for back-and-forth list review.
      Event card updated: player name appears in the Chop section.

  3.  In the private thread the TO may:
        /reg approve   -> player moves to Confirmed; thread updated; event card updated.
        /reg relegate  -> player moves to Reserve; thread updated; event card updated.
        /reg reject    -> player removed entirely; thread closed; DM sent.
      The player can resubmit/edit their list at any time inside the private thread.
      If a Chop player withdraws, the oldest Reserve is auto-promoted to Chop.

  4.  At registration deadline call /event lock-lists:
        All private threads archived and locked.
        Chop and Reserve sections removed from event card.
        PUBLIC thread created showing all confirmed players' lists.
        Confirmed Players card posted in noticeboard.
        Ping goes out reminding confirmed players of the briefing.

2v2 registration flow (spec):
  - Points: 2000 fixed. Max teams: 2 or 4. 3 rounds, 1 day.
  - Captain clicks Chop -> modal (team name + teammate Discord ID + both lists).
  - Team row stored in tournament_teams; thread ID in teams.captains_thread_id.
  - /reg approve/relegate/reject target the captain (who represents the team).
  - lock-lists publishes 2 lists per team.

Teams (3s/5s/8s) registration flow (spec):
  - Points: 2000 fixed. Max teams: 2-5. Team size: 3, 5, or 8.
  - Rounds: 1 if 2 teams, 3 if 3 teams, 5 if 4-5 teams.
  - Captain gets the Captains role on approval.
  - lock-lists publishes N lists per team.

Command groups registered in main.py via tree.add_command().
Call init(bot_instance) from main.py's on_ready to wire up the bot reference.
"""

import discord
from discord import app_commands
import asyncio
from datetime import datetime, timedelta, timezone, date
from typing import Optional
from zoneinfo import ZoneInfo

from config import (GUILD_ID, GUILD, EVENT_NOTICEBOARD_ID, WHATS_PLAYING_ID,
                    COLOUR_GOLD, COLOUR_CRIMSON, COLOUR_AMBER, COLOUR_SLATE,
                    fe, faction_colour, CREW_ROLE_ID, CAPTAINS_ROLE_ID)
from state import ES, RS, TS, FMT, is_to, get_thread_reg
from database import *
from threads import (ensure_lists_thread, add_player_to_event_threads,
                     event_round_count, ensure_all_round_threads,
                     create_private_thread)
from embeds import (build_list_review_header, build_player_list_embed,
                    build_spectator_dashboard_embed, build_event_main_embed,
                    build_schedule_embed, build_missions_embed,
                    build_judges_on_duty_embed, build_standings_embed,
                    build_singles_event_card, build_team_event_card)
from views import ChopRegistrationView, TeamChopRegistrationView
from services import (ac_active_events, ac_all_events, log_immediate)

KL_TZ = ZoneInfo("Asia/Kuala_Lumpur")

# ── Bot reference (set via init()) ────────────────────────────────────────────
bot = None


def init(bot_instance):
    """Called from main.py after bot is created."""
    global bot
    bot = bot_instance


# ══════════════════════════════════════════════════════════════════════════════
# SCHEDULE BUILDER  —  KL time (UTC+8), fixed slots per spec
# ══════════════════════════════════════════════════════════════════════════════

def build_kl_schedule(event_date: date, round_count: int) -> list[dict]:
    """
    Return a list of {label, start_dt, end_dt} (timezone-aware KL datetimes).

    3-round (1-day) schedule:
      08:30       Briefing
      09:00-12:00 Round 1
      12:00-13:00 Lunch Break
      13:00-16:00 Round 2
      16:00-16:15 Toilet Break
      16:15-19:15 Round 3
      19:15-19:30 Results

    5-round (2-day) — same as above then Day 2:
      08:30       Briefing (Day 2)
      09:00-12:00 Round 4
      12:00-13:00 Lunch Break
      13:00-16:00 Round 5
      16:00-16:15 Results
    """
    def kl(d: date, h: int, m: int = 0) -> datetime:
        return datetime(d.year, d.month, d.day, h, m, tzinfo=KL_TZ)

    d1 = event_date

    # 1-round schedule (2-team events): Briefing, Round 1, Results at 12pm
    if round_count == 1:
        return [
            {"label": "📢 Briefing",  "start_dt": kl(d1, 8, 30), "end_dt": kl(d1, 9, 0)},
            {"label": "⚔️ Round 1",   "start_dt": kl(d1, 9, 0),  "end_dt": kl(d1, 12, 0)},
            {"label": "🏆 Results",   "start_dt": kl(d1, 12, 0), "end_dt": kl(d1, 12, 30)},
        ]

    slots = [
        {"label": "📢 Briefing",     "start_dt": kl(d1, 8, 30), "end_dt": kl(d1, 9, 0)},
        {"label": "⚔️ Round 1",      "start_dt": kl(d1, 9, 0),  "end_dt": kl(d1, 12, 0)},
        {"label": "🍱 Lunch Break",  "start_dt": kl(d1, 12, 0), "end_dt": kl(d1, 13, 0)},
        {"label": "⚔️ Round 2",      "start_dt": kl(d1, 13, 0), "end_dt": kl(d1, 16, 0)},
        {"label": "🚻 Toilet Break", "start_dt": kl(d1, 16, 0), "end_dt": kl(d1, 16, 15)},
        {"label": "⚔️ Round 3",      "start_dt": kl(d1, 16, 15),"end_dt": kl(d1, 19, 15)},
    ]
    if round_count == 3:
        slots.append({"label": "🏆 Results", "start_dt": kl(d1, 19, 15), "end_dt": kl(d1, 19, 30)})
    else:
        d2 = d1 + timedelta(days=1)
        slots += [
            {"label": "📢 Briefing (Day 2)", "start_dt": kl(d2, 8, 30), "end_dt": kl(d2, 9, 0)},
            {"label": "⚔️ Round 4",          "start_dt": kl(d2, 9, 0),  "end_dt": kl(d2, 12, 0)},
            {"label": "🍱 Lunch Break",      "start_dt": kl(d2, 12, 0), "end_dt": kl(d2, 13, 0)},
            {"label": "⚔️ Round 5",          "start_dt": kl(d2, 13, 0), "end_dt": kl(d2, 16, 0)},
            {"label": "🏆 Results",          "start_dt": kl(d2, 16, 0), "end_dt": kl(d2, 16, 15)},
        ]
    return slots


# ══════════════════════════════════════════════════════════════════════════════
# PRIVATE THREAD HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def get_or_create_chop_thread(
    bot_ref,
    event_id: str,
    player_id: str,
    player_name: str,
    guild: discord.Guild,
) -> Optional[discord.Thread]:
    """
    Get or create the private review thread for a specific chop/reserve player.
    Members: the player + all Crew role members.
    Thread ID is stored in registration.chop_thread_id.
    """
    reg = db_get_registration(event_id, str(player_id))
    if reg and reg.get("chop_thread_id"):
        t = guild.get_thread(int(reg["chop_thread_id"]))
        if t:
            return t

    ch = bot_ref.get_channel(EVENT_NOTICEBOARD_ID)
    if not ch:
        return None

    thread = await create_private_thread(ch, f"📋 {player_name} — List Review")
    if not thread:
        return None

    # Add the player
    member = guild.get_member(int(player_id))
    if member:
        try:
            await thread.add_user(member)
        except Exception:
            pass

    # Add all Crew members
    if CREW_ROLE_ID:
        crew_role = guild.get_role(CREW_ROLE_ID)
        if crew_role:
            for m in crew_role.members:
                try:
                    await thread.add_user(m)
                except Exception:
                    pass

    # Persist
    db_update_registration(event_id, str(player_id), {"chop_thread_id": str(thread.id)})
    return thread


async def refresh_event_card(bot_ref, event_id: str, guild: discord.Guild):
    """Re-render the singles event card in #event-noticeboard."""
    event = db_get_event(event_id)
    if not event:
        return
    msg_id = event.get("noticeboard_msg_id")
    if not msg_id:
        return
    ch = bot_ref.get_channel(EVENT_NOTICEBOARD_ID)
    if not ch:
        return
    try:
        msg  = await ch.fetch_message(int(msg_id))
        regs = db_get_registrations(event_id)
        embed = build_singles_event_card(event, regs)
        view  = ChopRegistrationView(event_id)
        await msg.edit(embed=embed, view=view)
    except Exception as e:
        print(f"⚠️ refresh_event_card failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# TEAM EVENT HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def get_or_create_team_chop_thread(
    bot_ref,
    event_id: str,
    team_id: str,
    captain_id: str,
    captain_name: str,
    team_name: str,
    guild: discord.Guild,
) -> Optional[discord.Thread]:
    """
    Get or create the private review thread for a team captain.
    Members: the captain + all Crew role members.
    Thread ID stored in tournament_teams.captains_thread_id.
    """
    team = db_get_team(team_id)
    if team and team.get("captains_thread_id"):
        t = guild.get_thread(int(team["captains_thread_id"]))
        if t:
            return t

    ch = bot_ref.get_channel(EVENT_NOTICEBOARD_ID)
    if not ch:
        return None

    thread = await create_private_thread(ch, f"\U0001f4cb {team_name} \u2014 Team Review")
    if not thread:
        return None

    captain_member = guild.get_member(int(captain_id))
    if captain_member:
        try:
            await thread.add_user(captain_member)
        except Exception:
            pass

    if CREW_ROLE_ID:
        crew_role = guild.get_role(CREW_ROLE_ID)
        if crew_role:
            for m in crew_role.members:
                try:
                    await thread.add_user(m)
                except Exception:
                    pass

    db_update_team(team_id, {"captains_thread_id": str(thread.id)})
    return thread


async def refresh_team_event_card(bot_ref, event_id: str, guild: discord.Guild):
    """Re-render the team event card in #event-noticeboard."""
    event = db_get_event(event_id)
    if not event:
        return
    msg_id = event.get("noticeboard_msg_id")
    if not msg_id:
        return
    nb_ch = bot_ref.get_channel(EVENT_NOTICEBOARD_ID)
    if not nb_ch:
        return
    teams = db_get_teams(event_id)
    try:
        msg   = await nb_ch.fetch_message(int(msg_id))
        embed = build_team_event_card(event, teams)
        await msg.edit(embed=embed)
    except Exception as e:
        print(f"\u26a0\ufe0f refresh_team_event_card failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS  \u2014 EVENT MANAGEMENT  (TO only)
# ══════════════════════════════════════════════════════════════════════════════

event_grp = app_commands.Group(
    name="event",
    description="Tournament event management",
    guild_ids=[GUILD_ID],
    default_permissions=discord.Permissions(use_application_commands=True),
)


@event_grp.command(name="create", description="[TO] Create a new tournament event")
@app_commands.describe(
    name       = "Event name (type in)",
    event_date = "Event start date (YYYY-MM-DD)",
    format     = "Event format",
    max_players= "Singles: max players (8/16/32)",
    max_teams  = "2v2 / Teams: max teams (2-5)",
    team_size  = "Teams: players per team (3, 5, or 8)",
    scoring_mode = "Teams 3/5/8 scoring system (default: NTL)",
)
@app_commands.choices(
    format=[
        app_commands.Choice(name="Singles",   value="singles"),
        app_commands.Choice(name="2v2",       value="2v2"),
        app_commands.Choice(name="Teams 3s",  value="teams_3"),
        app_commands.Choice(name="Teams 5s",  value="teams_5"),
        app_commands.Choice(name="Teams 8s",  value="teams_8"),
    ],
    max_players=[
        app_commands.Choice(name="8  (3 rounds, 1 day)",  value=8),
        app_commands.Choice(name="16 (3 rounds, 1 day)",  value=16),
        app_commands.Choice(name="32 (5 rounds, 2 days)", value=32),
    ],
    max_teams=[
        app_commands.Choice(name="2 teams", value=2),
        app_commands.Choice(name="3 teams", value=3),
        app_commands.Choice(name="4 teams", value=4),
        app_commands.Choice(name="5 teams", value=5),
    ],
    team_size=[
        app_commands.Choice(name="3 players per team", value=3),
        app_commands.Choice(name="5 players per team", value=5),
        app_commands.Choice(name="8 players per team", value=8),
    ],
    scoring_mode=[
        app_commands.Choice(name="NTL (ratio-scaled thresholds)",   value="ntl"),
        app_commands.Choice(name="WTC (fixed 75/85 GP thresholds)", value="wtc"),
    ],
)
async def event_create(
    interaction: discord.Interaction,
    name: str,
    event_date: str,
    format: str = "singles",
    max_players: Optional[int] = None,
    max_teams:   Optional[int] = None,
    team_size:   Optional[int] = None,
    scoring_mode: str = "ntl",
):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    # Parse date
    try:
        sd = datetime.strptime(event_date, "%Y-%m-%d").date()
    except ValueError:
        await interaction.followup.send("❌ Date must be YYYY-MM-DD (e.g. 2026-04-12).", ephemeral=True)
        return

    is_team_format = format in ("2v2", "teams_3", "teams_5", "teams_8")

    # ── SINGLES ───────────────────────────────────────────────────────────────
    if format == "singles":
        if not max_players:
            await interaction.followup.send(
                "❌ `max_players` is required for Singles format (8 / 16 / 32).", ephemeral=True)
            return
        if max_players not in (8, 16, 32):
            await interaction.followup.send("❌ max_players must be 8, 16, or 32.", ephemeral=True)
            return

        points      = 2000  # spec always 2000; change choice below if 1000 needed
        round_count = 5 if max_players == 32 else 3
        end_date    = sd + timedelta(days=1) if round_count == 5 else sd
        n_teams     = 1
        t_size      = 1
        fmt_label   = "Singles"
        day_label   = "1 day" if round_count == 3 else "2 days"

    # ── 2v2 ───────────────────────────────────────────────────────────────────
    elif format == "2v2":
        if not max_teams or max_teams not in (2, 4):
            await interaction.followup.send(
                "❌ `max_teams` is required for 2v2 and must be 2 or 4.", ephemeral=True)
            return

        points      = 2000
        round_count = 3
        end_date    = sd
        n_teams     = max_teams
        t_size      = 2
        max_players = max_teams * t_size
        fmt_label   = "2v2"
        day_label   = "1 day"

    # ── TEAMS ─────────────────────────────────────────────────────────────────
    else:   # teams_3 / teams_5 / teams_8
        t_size_map = {"teams_3": 3, "teams_5": 5, "teams_8": 8}
        t_size = team_size or t_size_map.get(format, 3)
        if not max_teams or max_teams not in (2, 3, 4, 5):
            await interaction.followup.send(
                "❌ `max_teams` is required for Teams format and must be 2–5.", ephemeral=True)
            return

        # Spec: 1 round if 2 teams, 3 rounds if 3 teams, 5 rounds if 4-5 teams
        if max_teams == 2:
            round_count = 1
        elif max_teams == 3:
            round_count = 3
        else:
            round_count = 5

        points      = 2000
        end_date    = sd + timedelta(days=1) if round_count == 5 else sd
        n_teams     = max_teams
        max_players = max_teams * t_size
        fmt_label   = f"Teams ({t_size}s)"
        day_label   = "1 day" if round_count <= 3 else "2 days"

    rules_cutoff   = sd - timedelta(days=7)
    reg_deadline   = sd - timedelta(days=2)
    schedule_slots = build_kl_schedule(sd, round_count)

    # Persist base event row
    eid = db_create_event({
        "name":           name,
        "mission_code":   "TBD",
        "points_limit":   points,
        "start_date":     sd,
        "end_date":       end_date,
        "max_players":    max_players,
        "round_count":    round_count,
        "rounds_per_day": min(round_count, 3),
        "terrain_layout": "",
        "created_by":     str(interaction.user.id),
    })
    db_update_event(eid, {
        "format":            format,
        "team_size":         t_size,
        "individual_points": points,
        "event_pairings":    [],
        "event_layouts":     [],
        "event_missions":    [],
        "state":             ES.INTEREST,
        "rules_cutoff":      str(rules_cutoff),
        "reg_deadline":      str(reg_deadline),
        # WTC scoring mode only applies to team formats; singles/2v2 always use NTL
        "scoring_mode":      scoring_mode if format in ("teams_3", "teams_5", "teams_8") else "ntl",
    })

    event = db_get_event(eid)
    event["_rules_cutoff"]   = rules_cutoff
    event["_reg_deadline"]   = reg_deadline
    event["_schedule_slots"] = schedule_slots

    # Build card + view based on format
    nb_ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    if nb_ch:
        if is_team_format:
            embed = build_team_event_card(event, [])
            view  = TeamChopRegistrationView(eid)
        else:
            embed = build_singles_event_card(event, [])
            view  = ChopRegistrationView(eid)
        msg = await nb_ch.send(embed=embed, view=view)
        db_update_event(eid, {"noticeboard_msg_id": str(msg.id)})
        try:
            await msg.pin()
        except Exception:
            pass

    # Discord scheduled event
    try:
        start_kl = datetime(sd.year, sd.month, sd.day, 8, 30, tzinfo=KL_TZ)
        disc_evt = await interaction.guild.create_scheduled_event(
            name=name,
            description=(
                f"⚔️ Warhammer 40k TTS — {points}pts {fmt_label} Tournament\n"
                f"Register in #event-noticeboard"
            ),
            start_time=start_kl.astimezone(timezone.utc),
            end_time=(start_kl + timedelta(hours=11)).astimezone(timezone.utc),
            entity_type=discord.EntityType.external,
            location="Tabletop Simulator",
            privacy_level=discord.PrivacyLevel.guild_only,
        )
        db_update_event(eid, {"discord_event_id": str(disc_evt.id)})
    except Exception as e:
        print(f"⚠️ Discord event creation failed: {e}")

    await interaction.followup.send(
        f"✅ **{name}** created — `{eid}`\n"
        f"**{fmt_label}** · {points}pts · **{round_count} round{'s' if round_count > 1 else ''}** ({day_label})\n"
        + (f"👥 Teams: **{n_teams}** × {t_size} players = {max_players} total\n" if is_team_format else f"👥 Max players: **{max_players}**\n")
        + f"📅 Event date:    **{sd.strftime('%a %d %b %Y')}**\n"
        f"📋 Rules cutoff:  **{rules_cutoff.strftime('%a %d %b %Y')}** *(7 days before)*\n"
        f"⏰ Reg deadline:  **{reg_deadline.strftime('%a %d %b %Y')}** *(2 days before)*\n\n"
        f"Card posted in #event-noticeboard — teams/players can Chop now.",
        ephemeral=True,
    )
    await log_immediate(
        interaction.client,
        "Event Created",
        f"🏆 **{name}** by {interaction.user.display_name}\n"
        f"{fmt_label} · {points}pts · {round_count} rounds · {sd}",
        COLOUR_GOLD,
    )


# ── open-interest (legacy) ────────────────────────────────────────────────────

@event_grp.command(name="open-interest", description="[TO] Open interest phase (legacy override)")
@app_commands.autocomplete(event_id=ac_active_events)
async def event_open_interest(interaction: discord.Interaction, event_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True)
        return
    event = db_get_event(event_id)
    if not event:
        await interaction.response.send_message("❌ Not found.", ephemeral=True)
        return
    db_update_event(event_id, {"state": ES.INTEREST})
    await interaction.response.send_message("✅ Interest phase opened.", ephemeral=True)


# ── open-registration (manual override) ──────────────────────────────────────

@event_grp.command(name="open-registration", description="[TO] Manually open registration phase")
@app_commands.autocomplete(event_id=ac_active_events)
async def event_open_registration(interaction: discord.Interaction, event_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Not found.", ephemeral=True)
        return
    db_update_event(event_id, {"state": ES.REGISTRATION})
    await interaction.followup.send("✅ Registration phase opened.", ephemeral=True)


# ── lock-lists — closes private threads, publishes lists at deadline ──────────

@event_grp.command(name="lock-lists",
                   description="[TO] Close registration at deadline: lock threads, publish lists")
@app_commands.autocomplete(event_id=ac_active_events)
async def event_lock_lists(interaction: discord.Interaction, event_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Not found.", ephemeral=True)
        return

    is_team = event.get("format") in ("2v2", "teams_3", "teams_5", "teams_8")
    nb_ch   = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    msg_id  = event.get("noticeboard_msg_id")

    # ── TEAM EVENT ────────────────────────────────────────────────────────────
    if is_team:
        all_teams      = db_get_teams(event_id)
        confirmed_teams = [t for t in all_teams if t["state"] == TS.READY]

        # 1. Archive all team captain threads
        for team in all_teams:
            tid = team.get("captains_thread_id")
            if not tid:
                continue
            thread = interaction.guild.get_thread(int(tid))
            if thread:
                try:
                    await thread.send(
                        "🔒 **Registration deadline has passed. This thread is now closed.**\n"
                        "Check the Army Lists thread in #event-noticeboard for all confirmed lists."
                    )
                    await thread.edit(archived=True, locked=True)
                except Exception:
                    pass

        # 2. Update event card — Confirmed teams only
        if nb_ch and msg_id:
            try:
                msg   = await nb_ch.fetch_message(int(msg_id))
                embed = build_team_event_card(event, confirmed_teams, deadline_passed=True)
                await msg.edit(embed=embed, view=None)
            except Exception as e:
                print(f"⚠️ lock-lists team card edit failed: {e}")

        # 3. Public Army Lists thread — N lists per team
        lists_thread = await ensure_lists_thread(
            interaction.client, event_id, interaction.guild, event["name"]
        )
        target = lists_thread or nb_ch
        team_num = 0
        if target and confirmed_teams:
            for team in confirmed_teams:
                team_num += 1
                members = db_get_team_members(team["team_id"])
                # Post team header
                t_size = event.get("team_size", 2)
                header_embed = discord.Embed(
                    title=f"⚔️ Team {team_num}: {team['team_name']}",
                    description="\n".join(
                        f"{'👑 Captain' if m['role'] == 'captain' else '🧑 Player'}: **{m['player_username']}** — {m.get('army','?')} · *{m.get('detachment','?')}*"
                        for m in members
                    ) or "*No members.*",
                    color=COLOUR_GOLD,
                )
                await target.send(embed=header_embed)
                # Post each member's list
                for j, member in enumerate(members, 1):
                    reg_like = {
                        "army":            member.get("army", "Unknown"),
                        "detachment":      member.get("detachment", "Unknown"),
                        "player_username": member["player_username"],
                        "list_text":       member.get("list_text"),
                        "submitted_at":    member.get("joined_at"),
                    }
                    await target.send(embed=build_player_list_embed(reg_like, j))
                    await asyncio.sleep(0.3)
                await asyncio.sleep(0.4)

        # 4. Post Confirmed Teams card
        if nb_ch:
            await nb_ch.send(embed=_build_confirmed_teams_card(event, confirmed_teams))

        # 5. Ping all confirmed members
        if nb_ch and confirmed_teams:
            all_pids = []
            for team in confirmed_teams:
                members = db_get_team_members(team["team_id"])
                all_pids.extend(m["player_id"] for m in members)
            mentions = " ".join(f"<@{pid}>" for pid in all_pids)
            await nb_ch.send(
                f"📣 **Registration closed for {event['name']}!**\n"
                f"{mentions}\n\n"
                f"✅ You're confirmed! Please be in the **Event Briefing Room** at **8:30am KL time** on the event day.\n"
                f"🗂️ Army lists are now public in the thread above — study your opponents!"
            )

        # 6. DM all confirmed members
        for team in confirmed_teams:
            members = db_get_team_members(team["team_id"])
            for member in members:
                try:
                    u = await interaction.client.fetch_user(int(member["player_id"]))
                    await u.send(
                        f"📋 **Army lists are now public for {event['name']}!**\n"
                        f"Check #event-noticeboard → Army Lists thread.\n"
                        f"🕗 Be in the **Event Briefing Room** at **8:30am KL time** on the day!"
                    )
                except Exception:
                    pass

        await interaction.followup.send(
            f"✅ Registration closed for **{event['name']}** (Teams).\n"
            f"• {len(confirmed_teams)} confirmed teams\n"
            f"• Captain threads archived\n"
            f"• Army lists published\n"
            f"• All members notified",
            ephemeral=True,
        )
        return

    # ── SINGLES EVENT ─────────────────────────────────────────────────────────
    all_regs       = db_get_registrations(event_id)
    confirmed_regs = [r for r in all_regs if r["state"] == RS.APPROVED]

    # 1. Archive all private chop/reserve threads
    for reg in all_regs:
        tid = reg.get("chop_thread_id")
        if not tid:
            continue
        thread = interaction.guild.get_thread(int(tid))
        if thread:
            try:
                await thread.send(
                    "🔒 **Registration deadline has passed. This thread is now closed.**\n"
                    "Check the Army Lists thread in #event-noticeboard for all confirmed lists."
                )
                await thread.edit(archived=True, locked=True)
            except Exception:
                pass

    # 2. Update event card — Confirmed only, Chop/Reserve removed
    if nb_ch and msg_id:
        try:
            msg   = await nb_ch.fetch_message(int(msg_id))
            embed = build_singles_event_card(event, confirmed_regs, deadline_passed=True)
            await msg.edit(embed=embed, view=None)
        except Exception as e:
            print(f"⚠️ lock-lists card edit failed: {e}")

    # 3. Create public Army Lists thread and post all lists
    lists_thread = await ensure_lists_thread(
        interaction.client, event_id, interaction.guild, event["name"]
    )
    target = lists_thread or nb_ch
    if target and confirmed_regs:
        await target.send(embed=build_list_review_header(event, confirmed_regs))
        for i, reg in enumerate(confirmed_regs, 1):
            await target.send(embed=build_player_list_embed(reg, i))
            await asyncio.sleep(0.4)

    # 4. Post Confirmed Players card in noticeboard
    if nb_ch:
        await nb_ch.send(embed=_build_confirmed_players_card(event, confirmed_regs))

    # 5. Ping confirmed players with briefing reminder
    if nb_ch and confirmed_regs:
        mentions = " ".join(f"<@{r['player_id']}>" for r in confirmed_regs)
        await nb_ch.send(
            f"📣 **Registration closed for {event['name']}!**\n"
            f"{mentions}\n\n"
            f"✅ You're confirmed! Please be in the **Event Briefing Room** at **8:30am KL time** on the event day.\n"
            f"🗂️ Army lists are now public in the thread above — study your opponents!"
        )

    # 6. DM each confirmed player
    for reg in confirmed_regs:
        try:
            user = await interaction.client.fetch_user(int(reg["player_id"]))
            await user.send(
                f"📋 **Army lists are now public for {event['name']}!**\n"
                f"Check #event-noticeboard → Army Lists thread.\n"
                f"🕗 Be in the **Event Briefing Room** at **8:30am KL time** on the day!"
            )
        except Exception:
            pass

    await interaction.followup.send(
        f"✅ Registration closed for **{event['name']}**.\n"
        f"• {len(confirmed_regs)} confirmed players\n"
        f"• Private threads archived\n"
        f"• Army lists published\n"
        f"• Players notified",
        ephemeral=True,
    )


# ── start event ───────────────────────────────────────────────────────────────

@event_grp.command(name="start", description="[TO] Start the event — post pinned cards and create threads")
@app_commands.autocomplete(event_id=ac_active_events)
async def event_start(interaction: discord.Interaction, event_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Not found.", ephemeral=True)
        return

    db_update_event(event_id, {"state": ES.IN_PROGRESS})

    nb_ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
    regs  = db_get_registrations(event_id, RS.APPROVED)
    total_rounds = event_round_count(event)

    pinned_msgs = []
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

        reg_meta = get_thread_reg(event_id)
        if len(pinned_msgs) >= 5:
            reg_meta["judge_msg_id"]     = pinned_msgs[3].id
            reg_meta["standings_msg_id"] = pinned_msgs[4].id
            db_update_event(event_id, {
                "noticeboard_msg_id": str(pinned_msgs[0].id),
                "standings_msg_id":   str(pinned_msgs[4].id),
            })

    round_threads = await ensure_all_round_threads(
        interaction.client, event_id, interaction.guild, event["name"], total_rounds,
    )
    lists_thread = await ensure_lists_thread(
        interaction.client, event_id, interaction.guild, event["name"]
    )

    wpc = interaction.guild.get_channel(WHATS_PLAYING_ID)
    if wpc:
        standings  = db_get_standings(event_id)
        dash_embed = build_spectator_dashboard_embed(event, None, [], standings, interaction.guild)
        dash_msg   = await wpc.send(embed=dash_embed)
        try:
            await dash_msg.pin()
        except discord.HTTPException:
            pass
        db_update_event(event_id, {"spectator_msg_id": str(dash_msg.id)})

    thread_list = "  ·  ".join(f"Round {rn}" for rn in sorted(round_threads.keys()))
    await interaction.followup.send(
        f"✅ **{event['name']}** started!\n"
        f"5 cards pinned · Round threads: {thread_list}\n"
        f"Army Lists thread: {lists_thread.mention if lists_thread else '—'}\n"
        f"Use `/round briefing` to begin Day 1.",
        ephemeral=True,
    )
    await log_immediate(
        interaction.client, "Event Started",
        f"🏆 **{event['name']}** is LIVE\n{total_rounds} round threads · 5 cards pinned",
        COLOUR_CRIMSON,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SLASH COMMANDS  —  REGISTRATION  (/reg group)
# ══════════════════════════════════════════════════════════════════════════════

reg_grp = app_commands.Group(
    name="reg",
    description="Player registration management",
    guild_ids=[GUILD_ID],
)


@reg_grp.command(name="approve", description="[TO] Approve a registration — move to Confirmed")
@app_commands.describe(event_id="Event", player="Captain / player to confirm")
@app_commands.autocomplete(event_id=ac_active_events)
async def reg_approve(interaction: discord.Interaction, event_id: str, player: discord.Member):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True)
        return

    is_team = event.get("format") in ("2v2", "teams_3", "teams_5", "teams_8")

    # ── TEAM EVENT ────────────────────────────────────────────────────────────
    if is_team:
        team = db_get_team_by_captain(event_id, str(player.id))
        if not team:
            await interaction.followup.send("❌ No team found for this captain.", ephemeral=True)
            return
        if team["state"] == TS.READY:
            await interaction.followup.send("ℹ️ Team is already confirmed.", ephemeral=True)
            return

        db_update_team(team["team_id"], {"state": TS.READY})

        # Grant Captain role to the captain
        if CAPTAINS_ROLE_ID:
            cap_role = interaction.guild.get_role(CAPTAINS_ROLE_ID)
            if cap_role:
                try:
                    await player.add_roles(cap_role, reason=f"Team confirmed for {event['name']}")
                except Exception:
                    pass

        # Notify in private thread
        tid = team.get("captains_thread_id")
        if tid:
            t = interaction.guild.get_thread(int(tid))
            if t:
                try:
                    members = db_get_team_members(team["team_id"])
                    member_pings = " ".join(f"<@{m['player_id']}>" for m in members)
                    await t.send(
                        f"✅ **Team '{team['team_name']}' is now CONFIRMED!**\n"
                        f"{member_pings}\n"
                        f"All lists accepted for **{event['name']}**. See you at the event! ⚔️"
                    )
                except Exception:
                    pass

        # DM captain
        try:
            await player.send(
                f"✅ **Team '{team['team_name']}' confirmed for {event['name']}!**\n"
                f"Watch #event-noticeboard for the schedule. For the Emperor! ⚔️"
            )
        except Exception:
            pass

        await refresh_team_event_card(interaction.client, event_id, interaction.guild)
        await interaction.followup.send(f"✅ Team **{team['team_name']}** confirmed.", ephemeral=True)
        await log_immediate(interaction.client, "Team Confirmed",
                            f"✅ {team['team_name']} confirmed for {event['name']}", COLOUR_GOLD)
        return

    # ── SINGLES EVENT ─────────────────────────────────────────────────────────
    reg = db_get_registration(event_id, str(player.id))
    if not reg:
        await interaction.followup.send("❌ Player not registered for this event.", ephemeral=True)
        return
    if reg["state"] == RS.APPROVED:
        await interaction.followup.send("ℹ️ Already confirmed.", ephemeral=True)
        return

    db_update_registration(event_id, str(player.id), {
        "state":       RS.APPROVED,
        "approved_at": datetime.utcnow(),
    })
    db_upsert_standing(event_id, str(player.id), reg["player_username"],
                       reg["army"], reg["detachment"])
    await add_player_to_event_threads(interaction.client, event_id, interaction.guild, str(player.id))

    # Notify in private thread
    tid = reg.get("chop_thread_id")
    if tid:
        t = interaction.guild.get_thread(int(tid))
        if t:
            try:
                await t.send(
                    f"✅ **{reg['player_username']}, you are now CONFIRMED!**\n"
                    f"Your list has been approved for **{event['name']}**. See you at the event! ⚔️"
                )
            except Exception:
                pass

    # DM player
    try:
        await player.send(
            f"✅ **Registration confirmed for {event['name']}!**\n"
            f"{fe(reg['army'])} {reg['army']} · *{reg['detachment']}*\n"
            f"Watch #event-noticeboard for pairings. For the Emperor! ⚔️"
        )
    except Exception:
        pass

    await refresh_event_card(interaction.client, event_id, interaction.guild)
    await interaction.followup.send(f"✅ **{reg['player_username']}** confirmed.", ephemeral=True)
    await log_immediate(interaction.client, "Registration Approved",
                        f"✅ {reg['player_username']} confirmed for {event['name']}", COLOUR_GOLD)


@reg_grp.command(name="relegate", description="[TO] Move a Chop team/player to Reserve")
@app_commands.describe(event_id="Event", player="Captain / player to relegate to Reserve")
@app_commands.autocomplete(event_id=ac_active_events)
async def reg_relegate(interaction: discord.Interaction, event_id: str, player: discord.Member):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True)
        return

    is_team = event.get("format") in ("2v2", "teams_3", "teams_5", "teams_8")

    if is_team:
        team = db_get_team_by_captain(event_id, str(player.id))
        if not team:
            await interaction.followup.send("❌ No team found for this captain.", ephemeral=True)
            return
        # Use FORMING as "Chop", use a custom state "reserve" stored directly on team
        # Since TS only has forming/ready/dropped, we store a string flag via update
        db_update_team(team["team_id"], {"state": "reserve"})

        tid = team.get("captains_thread_id")
        if tid:
            t = interaction.guild.get_thread(int(tid))
            if t:
                try:
                    await t.send(
                        f"ℹ️ **{team['team_name']}**, you've been moved to **Reserve** by the TO.\n"
                        f"You can still edit and resubmit your lists here. "
                        f"You'll be promoted if a Chop spot opens up."
                    )
                except Exception:
                    pass
        try:
            await player.send(
                f"ℹ️ Your team **{team['team_name']}** has been moved to **Reserve** for **{event['name']}**.\n"
                f"Continue editing lists in your private thread. You'll be notified if a spot opens."
            )
        except Exception:
            pass

        await refresh_team_event_card(interaction.client, event_id, interaction.guild)
        await interaction.followup.send(f"✅ Team {team['team_name']} moved to Reserve.", ephemeral=True)
        return

    # Singles
    reg = db_get_registration(event_id, str(player.id))
    if not reg:
        await interaction.followup.send("❌ Player not registered.", ephemeral=True)
        return

    db_update_registration(event_id, str(player.id), {"state": RS.INTERESTED})

    tid = reg.get("chop_thread_id")
    if tid:
        t = interaction.guild.get_thread(int(tid))
        if t:
            try:
                await t.send(
                    f"ℹ️ **{reg['player_username']}**, you've been moved to **Reserve** by the TO.\n"
                    f"You can still edit and resubmit your list here. "
                    f"You'll be promoted if a Chop spot opens up."
                )
            except Exception:
                pass

    try:
        await player.send(
            f"ℹ️ You've been moved to **Reserve** for **{event['name']}**.\n"
            f"Continue editing your list in your private thread. You'll be notified if a spot opens."
        )
    except Exception:
        pass

    await refresh_event_card(interaction.client, event_id, interaction.guild)
    await interaction.followup.send(f"✅ {reg['player_username']} moved to Reserve.", ephemeral=True)


@reg_grp.command(name="reject", description="[TO] Reject a team/player registration entirely")
@app_commands.describe(event_id="Event", player="Captain / player to reject", reason="Reason (shown to them)")
@app_commands.autocomplete(event_id=ac_active_events)
async def reg_reject(interaction: discord.Interaction, event_id: str, player: discord.Member,
                     reason: str = ""):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)

    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True)
        return

    is_team = event.get("format") in ("2v2", "teams_3", "teams_5", "teams_8")

    if is_team:
        team = db_get_team_by_captain(event_id, str(player.id))
        if not team:
            await interaction.followup.send("❌ No team found for this captain.", ephemeral=True)
            return

        db_update_team(team["team_id"], {"state": TS.DROPPED})

        tid = team.get("captains_thread_id")
        if tid:
            t = interaction.guild.get_thread(int(tid))
            if t:
                try:
                    await t.send(
                        "❌ **Team registration rejected.**\n"
                        + (f"Reason: *{reason}*\n" if reason else "")
                        + "This thread is now closed. Contact the TO if you have questions."
                    )
                    await t.edit(archived=True, locked=True)
                except Exception:
                    pass

        try:
            await player.send(
                f"❌ **Team '{team['team_name']}' rejected for {event['name']}**\n"
                + (f"Reason: {reason}\n" if reason else "")
                + "Contact the TO if you have questions."
            )
        except Exception:
            pass

        # Promote oldest reserve team if there is one
        reserve_teams = [t for t in db_get_teams(event_id) if t["state"] == "reserve"
                         and t["team_id"] != team["team_id"]]
        if reserve_teams:
            promoted_team = reserve_teams[0]
            db_update_team(promoted_team["team_id"], {"state": TS.FORMING})
            try:
                cap = await interaction.client.fetch_user(int(promoted_team["captain_id"]))
                await cap.send(
                    f"🎉 **Team '{promoted_team['team_name']}' promoted from Reserve to Chop for {event['name']}!**\n"
                    f"A spot opened up. The TO will review your lists shortly."
                )
            except Exception:
                pass
            ptid = promoted_team.get("captains_thread_id")
            if ptid:
                pt = interaction.guild.get_thread(int(ptid))
                if pt:
                    try:
                        await pt.send(
                            f"🎉 **Promoted from Reserve → Chop!** A spot opened up. TO will review shortly."
                        )
                    except Exception:
                        pass

        await refresh_team_event_card(interaction.client, event_id, interaction.guild)
        await interaction.followup.send(f"✅ Team {team['team_name']} rejected.", ephemeral=True)
        await log_immediate(interaction.client, "Team Rejected",
                            f"❌ Team {team['team_name']} rejected from {event['name']}"
                            + (f"\nReason: {reason}" if reason else ""), COLOUR_CRIMSON)
        return

    # Singles
    reg = db_get_registration(event_id, str(player.id))
    if not reg:
        await interaction.followup.send("❌ Not registered.", ephemeral=True)
        return

    db_update_registration(event_id, str(player.id), {
        "state":            RS.REJECTED,
        "rejection_reason": reason,
    })

    tid = reg.get("chop_thread_id")
    if tid:
        t = interaction.guild.get_thread(int(tid))
        if t:
            try:
                await t.send(
                    "❌ **Registration rejected.**\n"
                    + (f"Reason: *{reason}*\n" if reason else "")
                    + "This thread is now closed. Contact the TO if you have questions."
                )
                await t.edit(archived=True, locked=True)
            except Exception:
                pass

    try:
        await player.send(
            f"❌ **Registration rejected for {event['name']}**\n"
            + (f"Reason: {reason}\n" if reason else "")
            + "Contact the TO if you have questions."
        )
    except Exception:
        pass

    await refresh_event_card(interaction.client, event_id, interaction.guild)
    await interaction.followup.send(f"✅ {reg['player_username']} rejected.", ephemeral=True)
    await log_immediate(interaction.client, "Registration Rejected",
                        f"❌ {reg['player_username']} rejected from {event['name']}"
                        + (f"\nReason: {reason}" if reason else ""), COLOUR_CRIMSON)


@reg_grp.command(name="drop", description="Withdraw your team/yourself from a tournament")
@app_commands.describe(event_id="Event to drop from")
@app_commands.autocomplete(event_id=ac_active_events)
async def reg_drop(interaction: discord.Interaction, event_id: str):
    event = db_get_event(event_id)
    if not event:
        await interaction.response.send_message("❌ Event not found.", ephemeral=True)
        return

    is_team = event.get("format") in ("2v2", "teams_3", "teams_5", "teams_8")

    # ── TEAM EVENT ────────────────────────────────────────────────────────────
    if is_team:
        team = db_get_team_by_captain(event_id, str(interaction.user.id))
        if not team or team["state"] == TS.DROPPED:
            await interaction.response.send_message(
                "❌ No active team registration found for you on this event.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        was_chop_or_confirmed = team["state"] in (TS.FORMING, TS.READY)
        db_update_team(team["team_id"], {"state": TS.DROPPED})

        tid = team.get("captains_thread_id")
        if tid:
            t = interaction.guild.get_thread(int(tid))
            if t:
                try:
                    await t.send("👋 Team has withdrawn. This thread is now closed.")
                    await t.edit(archived=True, locked=True)
                except Exception:
                    pass

        # Promote oldest reserve team
        if was_chop_or_confirmed:
            reserve_teams = sorted(
                [t for t in db_get_teams(event_id)
                 if t["state"] == "reserve" and t["team_id"] != team["team_id"]],
                key=lambda t: t.get("created_at") or datetime.min,
            )
            if reserve_teams:
                promoted = reserve_teams[0]
                db_update_team(promoted["team_id"], {"state": TS.FORMING})
                try:
                    cap = await interaction.client.fetch_user(int(promoted["captain_id"]))
                    await cap.send(
                        f"🎉 **Team '{promoted['team_name']}' promoted from Reserve to Chop for {event['name']}!**\n"
                        f"A spot opened up. The TO will review your lists shortly."
                    )
                except Exception:
                    pass
                ptid = promoted.get("captains_thread_id")
                if ptid:
                    pt = interaction.guild.get_thread(int(ptid))
                    if pt:
                        try:
                            await pt.send(
                                "🎉 **Promoted from Reserve → Chop!** A spot opened up. TO will review shortly."
                            )
                        except Exception:
                            pass
                nb_ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
                if nb_ch:
                    await nb_ch.send(
                        f"🔄 **{event['name']}**: Team **{team['team_name']}** withdrew — "
                        f"Team **{promoted['team_name']}** promoted from Reserve to Chop."
                    )

        await refresh_team_event_card(interaction.client, event_id, interaction.guild)
        await log_immediate(
            interaction.client, "Team Withdrew",
            f"⚠️ Team **{team['team_name']}** withdrew from **{event['name']}**",
            COLOUR_AMBER,
        )
        await interaction.followup.send(
            f"Your team **{team['team_name']}** has been withdrawn from **{event['name']}**.",
            ephemeral=True,
        )
        return

    # ── SINGLES EVENT ─────────────────────────────────────────────────────────
    reg = db_get_registration(event_id, str(interaction.user.id))
    if not reg or reg["state"] == RS.DROPPED:
        await interaction.response.send_message("❌ You're not registered for this event.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    was_confirmed = reg["state"] == RS.APPROVED
    was_chop      = reg["state"] == RS.PENDING

    db_update_registration(event_id, str(interaction.user.id), {
        "state":      RS.DROPPED,
        "dropped_at": datetime.utcnow(),
    })
    if was_confirmed:
        db_update_standing(event_id, str(interaction.user.id), {"active": False})

    tid = reg.get("chop_thread_id")
    if tid:
        t = interaction.guild.get_thread(int(tid))
        if t:
            try:
                await t.send("👋 Player has withdrawn. This thread is now closed.")
                await t.edit(archived=True, locked=True)
            except Exception:
                pass

    if was_chop or was_confirmed:
        all_regs = db_get_registrations(event_id)
        reserves = sorted(
            [r for r in all_regs if r["state"] == RS.INTERESTED
             and r["player_id"] != str(interaction.user.id)],
            key=lambda r: r.get("submitted_at") or datetime.min,
        )
        if reserves:
            promoted = reserves[0]
            db_update_registration(event_id, promoted["player_id"], {"state": RS.PENDING})
            try:
                promoted_user = await interaction.client.fetch_user(int(promoted["player_id"]))
                await promoted_user.send(
                    f"🎉 **Promoted from Reserve to Chop for {event['name']}!**\n"
                    f"A spot has opened up. Check your private thread — the TO will review your list shortly."
                )
            except Exception:
                pass
            p_tid = promoted.get("chop_thread_id")
            if p_tid:
                p_thread = interaction.guild.get_thread(int(p_tid))
                if p_thread:
                    try:
                        await p_thread.send(
                            "🎉 **Promotion:** You've moved from Reserve → **Chop**!\n"
                            "A Chop spot opened up. The TO will review and confirm shortly."
                        )
                    except Exception:
                        pass
            nb_ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
            if nb_ch:
                await nb_ch.send(
                    f"🔄 **{event['name']}**: {interaction.user.display_name} withdrew — "
                    f"**{promoted['player_username']}** promoted from Reserve to Chop."
                )

    await refresh_event_card(interaction.client, event_id, interaction.guild)
    await log_immediate(
        interaction.client, "Player Withdrew",
        f"⚠️ **{interaction.user.display_name}** withdrew from **{event['name']}**",
        COLOUR_AMBER,
    )
    await interaction.followup.send(
        f"You've been withdrawn from **{event['name']}**. Your existing results are preserved.",
        ephemeral=True,
    )


@reg_grp.command(name="list", description="[TO] View all registrations for an event")
@app_commands.describe(event_id="Event")
@app_commands.autocomplete(event_id=ac_all_events)
async def reg_list(interaction: discord.Interaction, event_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ TO only.", ephemeral=True)
        return
    event = db_get_event(event_id)
    if not event:
        await interaction.response.send_message("❌ Not found.", ephemeral=True)
        return
    regs = db_get_registrations(event_id)
    if not regs:
        await interaction.response.send_message("No registrations yet.", ephemeral=True)
        return

    sections = [
        (RS.APPROVED,   "✅  Confirmed"),
        (RS.PENDING,    "✊  Chop"),
        (RS.INTERESTED, "🖐️  Reserve"),
        (RS.DROPPED,    "🚪  Withdrawn"),
        (RS.REJECTED,   "❌  Rejected"),
    ]
    embed = discord.Embed(title=f"📋  Registrations — {event['name']}", color=COLOUR_SLATE)
    for state, label in sections:
        group = [r for r in regs if r["state"] == state]
        if group:
            lines = [f"{fe(r['army'])} **{r['player_username']}** — *{r['army']}*" for r in group]
            embed.add_field(name=label, value="\n".join(lines), inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════════
# PRIVATE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _build_confirmed_players_card(event: dict, confirmed_regs: list) -> discord.Embed:
    """Final confirmed roster card posted at registration deadline."""
    roster = "\n".join(
        f"{fe(r['army'])}  **{r['player_username']}**  ·  *{r['army']}*"
        for r in confirmed_regs
    ) or "*No confirmed players.*"

    embed = discord.Embed(
        title=f"✅  Confirmed Players — {event['name']}",
        description="Registration is now **closed**. The following players are confirmed.\n"
                    "Army lists are visible in the thread above.",
        color=COLOUR_GOLD,
    )
    embed.add_field(
        name=f"👥  Confirmed ({len(confirmed_regs)}/{event['max_players']})",
        value=roster,
        inline=False,
    )
    embed.add_field(
        name="📢  Day-of Reminder",
        value="Please gather in the **Event Briefing Room** at **8:30am KL time** on event day.",
        inline=False,
    )
    embed.set_footer(text="Registration closed · Lists are now public")
    return embed


def _build_confirmed_teams_card(event: dict, confirmed_teams: list) -> discord.Embed:
    """Final confirmed teams card posted at registration deadline for team events."""
    t_size     = event.get("team_size", 2)
    fmt_label  = {
        "2v2":     "2v2",
        "teams_3": "Teams 3s",
        "teams_5": "Teams 5s",
        "teams_8": "Teams 8s",
    }.get(event.get("format",""), "Teams")

    embed = discord.Embed(
        title=f"✅  Confirmed Teams — {event['name']}",
        description=(
            f"Registration is now **closed**. The following teams are confirmed.\n"
            f"Army lists are visible in the thread above."
        ),
        color=COLOUR_GOLD,
    )

    max_t = event.get("max_players", 0) // t_size if t_size else "?"
    embed.add_field(
        name=f"👥  Format: {fmt_label} · {len(confirmed_teams)}/{max_t} teams confirmed",
        value="​",
        inline=False,
    )

    for i, team in enumerate(confirmed_teams, 1):
        members = db_get_team_members(team["team_id"])
        member_lines = "\n".join(
            f"{'👑' if m['role'] == 'captain' else '🧑'} {m['player_username']} — *{m.get('army','?')}*"
            for m in members
        ) or "*No members*"
        embed.add_field(
            name=f"{'✅' if team['state'] == TS.READY else '?'} Team {i}: **{team['team_name']}**",
            value=member_lines,
            inline=False,
        )

    embed.add_field(
        name="📢  Day-of Reminder",
        value="Please gather in the **Event Briefing Room** at **8:30am KL time** on event day.",
        inline=False,
    )
    embed.set_footer(text="Registration closed · Lists are now public")
    return embed
