"""
main.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Entry point. Imports all modules, registers command groups,
starts background tasks, and runs the bot.

Run with:  python main.py
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks
import asyncio

from config import TOKEN, GUILD, GUILD_ID, LOG_BATCH_MINUTES, BOT_LOGS_ID, GAME_ROOM_PREFIX, CREW_ROLE_ID
from database import init_db, db_flush_logs, db_get_active_events, db_get_current_round
from state import get_thread_reg
from threads import restore_thread_registry
from services import refresh_spectator_dashboard, _refresh_judges_on_duty, log_immediate, refresh_standings_card

# ── Command modules ───────────────────────────────────────────────────────────
import commands_event
import commands_round
import commands_round_teams
import commands_teams
import ritual

# ══════════════════════════════════════════════════════════════════════════════
# BOT SETUP
# ══════════════════════════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.message_content = True
intents.reactions       = True
intents.members         = True
intents.presences       = True

bot  = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ── Register command groups ───────────────────────────────────────────────────
tree.add_command(commands_event.event_grp)
tree.add_command(commands_event.reg_grp)
tree.add_command(commands_round.round_grp)
tree.add_command(commands_round.result_grp)
tree.add_command(commands_round_teams.result_team_grp)
tree.add_command(commands_teams.team_grp)

# ── Register top-level commands ───────────────────────────────────────────────
tree.add_command(commands_round.event_finish)
tree.add_command(commands_round.standings_cmd)
tree.add_command(commands_round.my_list_cmd)
tree.add_command(commands_round_teams.team_standings_cmd)
tree.add_command(ritual.roll_dice, guild=GUILD)

# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════════════

@tasks.loop(minutes=LOG_BATCH_MINUTES)
async def flush_batch_logs():
    logs = db_flush_logs()
    if not logs or not any(bot.guilds): return
    ch = bot.get_channel(BOT_LOGS_ID)
    if not ch: return
    for log in logs:
        colour = discord.Color.red() if log["level"] == "error" else discord.Color.blue()
        em = discord.Embed(description=log["message"], color=colour,
                           timestamp=log["logged_at"])
        em.set_footer(text=f"[{log['level'].upper()}]  {log.get('event_id','')}")
        try:
            await ch.send(embed=em)
        except Exception:
            pass

@tasks.loop(minutes=1)
async def check_round_deadlines():
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    for event in db_get_active_events():
        rnd = db_get_current_round(event["event_id"])
        if not rnd or rnd["state"] != "in_progress": continue
        deadline = rnd.get("deadline_at")
        if not deadline: continue
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        remaining = (deadline - now).total_seconds()
        if 0 < remaining < 600:
            reg = get_thread_reg(event["event_id"])
            rn  = rnd["round_number"]
            tid = reg.get("rounds", {}).get(rn)
            if tid:
                ch = bot.get_channel(tid)
                if ch:
                    try:
                        await ch.send(
                            f"⏰ **10 minutes remaining** for Round {rn}! "
                            f"Please submit results promptly."
                        )
                    except Exception:
                        pass

@tasks.loop(minutes=5)
async def refresh_dashboards():
    guild = bot.get_guild(GUILD_ID)
    for event in db_get_active_events():
        eid = event["event_id"]
        await refresh_spectator_dashboard(bot, eid)
        if guild:
            await _refresh_judges_on_duty(bot, eid, guild)
            await refresh_standings_card(bot, eid)

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
    init_db()
    # Wire bot reference into command modules that need it
    commands_event.init(bot)
    guild = bot.get_guild(GUILD_ID)
    if guild:
        await restore_thread_registry(bot, guild)
    try:
        synced = await tree.sync(guild=GUILD)
        print(f"✅ Synced {len(synced)} commands to guild {GUILD_ID}")
    except Exception as e:
        print(f"❌ Sync error: {e}")
    flush_batch_logs.start()
    check_round_deadlines.start()
    refresh_dashboards.start()
    print(f"✅ {bot.user} ready")

@bot.event
async def on_member_join(member: discord.Member):
    pass   # extend as needed

@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after:  discord.VoiceState,
):
    """
    Refresh the Judges on Duty card whenever a crew member or admin
    moves into or out of a Game Room voice channel.
    """
    # Only care about crew / admins
    is_crew = (
        member.guild_permissions.administrator
        or (CREW_ROLE_ID and any(r.id == CREW_ROLE_ID for r in member.roles))
    )
    if not is_crew:
        return

    # Only care if the channel change involves a Game Room
    def _is_game_room(vc: discord.VoiceState | None) -> bool:
        return bool(vc and vc.channel and vc.channel.name.startswith(GAME_ROOM_PREFIX))

    if not (_is_game_room(before) or _is_game_room(after)):
        return

    # Refresh for every active event (typically just one at a time)
    for event in db_get_active_events():
        await _refresh_judges_on_duty(bot, event["event_id"], member.guild)

@tree.error
async def on_error(interaction: discord.Interaction,
                   error: app_commands.AppCommandError):
    msg = f"❌ {error}"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════════════════════
# RUN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    bot.run(TOKEN)
