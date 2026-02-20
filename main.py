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
import asyncio, random as _random

from config import TOKEN, GUILD, GUILD_ID, LOG_BATCH_MINUTES
from database import init_db, db_flush_logs, db_get_active_events, db_get_current_round
from state import get_thread_reg
from threads import restore_thread_registry
from services import refresh_spectator_dashboard, _refresh_judge_queue, log_immediate

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
# These group objects are defined in each command module; we import and add them.
# (Each module defines its group at module level so autocomplete decorators bind.)

tree.add_command(commands_event.event_grp)
tree.add_command(commands_event.reg_grp)
tree.add_command(commands_round.round_grp)
tree.add_command(commands_round.result_grp)
tree.add_command(commands_round_teams.result_team_grp)
tree.add_command(commands_teams.team_grp)

# Top-level guild commands (registered with guild= in their module decorators)
# tree.add_command(commands_round.event_finish)       # already decorated
# tree.add_command(commands_round.standings_cmd)
# tree.add_command(commands_round.my_list)
# tree.add_command(commands_round_teams.team_standings_cmd)
# tree.add_command(ritual.roll_dice)

# ══════════════════════════════════════════════════════════════════════════════
# BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════════════

@tasks.loop(minutes=LOG_BATCH_MINUTES)
async def flush_batch_logs():
    logs = db_flush_logs()
    if not logs or not any(bot.guilds): return
    ch = bot.get_channel(commands_event.BOT_LOGS_ID)   # re-use config constant
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
    for event in db_get_active_events():
        await refresh_spectator_dashboard(bot, event["event_id"])

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
