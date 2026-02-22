"""
views.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All persistent discord.ui.View and discord.ui.Modal classes,
plus the game-confirm helper coroutines.

Classes:
  • EventAnnouncementView
  • RegistrationApprovalView
  • PairingActionView
  • JudgeQueueView
  • ResultConfirmationView
  • ListSubmissionModal
  • ResultModal
  • RejectionReasonModal
  • VPAdjustModal
  • JudgeCloseModal

Helpers:
  • _confirm_game
  • _auto_confirm_after_24h

Imported by: services.py, commands_*.py
"""
import discord
from discord import ui
import asyncio
import psycopg2.extras
from datetime import datetime, timezone
from typing import Optional
from config import (COLOUR_GOLD, COLOUR_CRIMSON, COLOUR_AMBER, COLOUR_SLATE,
                    CREW_ROLE_ID, EVENT_NOTICEBOARD_ID,
                    fe, room_colour)
from state import GS, RS, JCS, ES, is_to, get_thread_reg, get_judges_for_guild
from database import *
from threads import ensure_submissions_thread, calculate_rounds

# Lazy imports to avoid circular dependencies (services imports from views)
def _get_refresh_spectator_dashboard():
    from services import refresh_spectator_dashboard
    return refresh_spectator_dashboard

def _get_refresh_judges_on_duty():
    from services import _refresh_judges_on_duty
    return _refresh_judges_on_duty

def _get_log_immediate():
    from services import log_immediate
    return log_immediate

async def refresh_spectator_dashboard(bot, event_id):
    return await _get_refresh_spectator_dashboard()(bot, event_id)

async def _refresh_judges_on_duty(bot, event_id, guild):
    return await _get_refresh_judges_on_duty()(bot, event_id, guild)

async def log_immediate(bot, title, description, colour=None):
    return await _get_log_immediate()(bot, title, description, colour)

# ══════════════════════════════════════════════════════════════════════════════
# VIEWS & BUTTONS
# ══════════════════════════════════════════════════════════════════════════════

class EventAnnouncementView(ui.View):
    """Pinned on the event announcement card."""
    def __init__(self, event_id: str):
        super().__init__(timeout=None)
        self.event_id = event_id

    @ui.button(label="✋  Register Interest", style=discord.ButtonStyle.success,
               custom_id="btn_interest")
    async def btn_interest(self, interaction: discord.Interaction, button: ui.Button):
        event = db_get_event(self.event_id)
        if not event or event["state"] not in (ES.ANNOUNCED, ES.INTEREST):
            await interaction.response.send_message("❌ Interest registration is not open.", ephemeral=True)
            return
        existing = db_get_registration(self.event_id, str(interaction.user.id))
        if existing:
            await interaction.response.send_message(
                f"ℹ️ You're already registered as **{existing['state']}**.", ephemeral=True)
            return
        db_upsert_registration(self.event_id, str(interaction.user.id),
                                interaction.user.display_name, RS.INTERESTED)
        db_queue_log(f"{interaction.user.display_name} registered interest", self.event_id)
        await interaction.response.send_message(
            "✅ Interest noted! You'll be pinged when registration opens.", ephemeral=True)

    @ui.button(label="📋  View Event Details", style=discord.ButtonStyle.secondary,
               custom_id="btn_event_details")
    async def btn_details(self, interaction: discord.Interaction, button: ui.Button):
        event = db_get_event(self.event_id)
        m = db_get_mission(event["mission_code"])
        embed = discord.Embed(
            title=f"📋  {event['name']}  —  Details",
            color=COLOUR_GOLD,
        )
        embed.add_field(name="Mission",    value=f"{m.get('name','—')} — *{m.get('deployment','—')}*", inline=False)
        embed.add_field(name="Layouts",    value=", ".join(m.get("layouts", [])),                       inline=True)
        embed.add_field(name="Points",     value=f"{event['points_limit']} pts",                         inline=True)
        embed.add_field(name="Max Players",value=str(event["max_players"]),                              inline=True)
        embed.add_field(name="Rounds",     value=f"{calculate_rounds(event['max_players'])} total · {event['rounds_per_day']}/day", inline=True)
        if event.get("terrain_layout"):
            embed.add_field(name="Terrain Layout", value=event["terrain_layout"], inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RegistrationApprovalView(ui.View):
    """Appears on TO notification when a player submits their list."""
    def __init__(self, event_id: str, player_id: str):
        super().__init__(timeout=None)
        self.event_id  = event_id
        self.player_id = player_id

    @ui.button(label="✅  Approve", style=discord.ButtonStyle.success)
    async def btn_approve(self, interaction: discord.Interaction, button: ui.Button):
        if not is_to(interaction):
            await interaction.response.send_message("❌ TO only.", ephemeral=True); return
        reg = db_get_registration(self.event_id, self.player_id)
        if not reg or reg["state"] == RS.APPROVED:
            await interaction.response.send_message("ℹ️ Already approved or not found.", ephemeral=True); return
        event = db_get_event(self.event_id)
        db_update_registration(self.event_id, self.player_id,
                                {"state": RS.APPROVED, "approved_at": datetime.utcnow()})
        db_upsert_standing(self.event_id, self.player_id, reg["player_username"],
                            reg["army"], reg["detachment"])
        # Add player to all existing event threads
        await add_player_to_event_threads(interaction.client, self.event_id, interaction.guild, self.player_id)
        try:
            user = await interaction.client.fetch_user(int(self.player_id))
            await user.send(
                f"✅ **You're in! Registration approved for {event['name']}**\n"
                f"{fe(reg['army'])} {reg['army']} · *{reg['detachment']}*\n"
                f"Watch #event-noticeboard for pairings. For the Emperor! ⚔️"
            )
        except: pass
        db_queue_log(f"Registration approved: {reg['player_username']} ({reg['army']})", self.event_id)
        await interaction.response.edit_message(
            content=f"✅ **{reg['player_username']}** approved.",
            embed=interaction.message.embeds[0] if interaction.message.embeds else None,
            view=None,
        )

    @ui.button(label="❌  Reject", style=discord.ButtonStyle.danger)
    async def btn_reject(self, interaction: discord.Interaction, button: ui.Button):
        if not is_to(interaction):
            await interaction.response.send_message("❌ TO only.", ephemeral=True); return
        await interaction.response.send_modal(RejectionReasonModal(self.event_id, self.player_id,
                                                                    interaction.message))


class PairingActionView(ui.View):
    """
    Buttons attached to each pairing row in the pairings message.
    One view per game — Submit Result + Judge Call.
    Uses persistent custom_ids encoding the game_id.
    """
    def __init__(self, game_id: str, event_id: str, room_number: int):
        super().__init__(timeout=None)
        self.game_id     = game_id
        self.event_id    = event_id
        self.room_number = room_number

    @ui.button(label="📊  Submit Result", style=discord.ButtonStyle.success)
    async def btn_submit_result(self, interaction: discord.Interaction, button: ui.Button):
        game = db_get_game(self.game_id)
        if not game:
            await interaction.response.send_message("❌ Game not found.", ephemeral=True); return
        if game["state"] in (GS.COMPLETE,):
            await interaction.response.send_message("❌ Result already confirmed.", ephemeral=True); return

        uid = str(interaction.user.id)
        if uid not in (game["player1_id"], game.get("player2_id", "")):
            await interaction.response.send_message(
                "❌ Only players in this game can submit results.", ephemeral=True); return

        is_p1 = uid == game["player1_id"]
        await interaction.response.send_modal(
            ResultModal(self.game_id, self.event_id, is_p1, interaction.client)
        )

    @ui.button(label="⚖️  Judge Call", style=discord.ButtonStyle.danger)
    async def btn_judge_call(self, interaction: discord.Interaction, button: ui.Button):
        game = db_get_game(self.game_id)
        if not game:
            await interaction.response.send_message("❌ Game not found.", ephemeral=True); return

        uid = str(interaction.user.id)
        if uid not in (game["player1_id"], game.get("player2_id", "")):
            await interaction.response.send_message(
                "❌ Only players in this game can raise a judge call.", ephemeral=True); return

        round_obj = db_get_current_round(self.event_id)
        if not round_obj:
            await interaction.response.send_message("❌ No active round.", ephemeral=True); return

        call_id = db_create_judge_call(
            self.event_id, round_obj["round_id"], self.game_id,
            uid, interaction.user.display_name, self.room_number
        )
        db_queue_log(f"Judge call raised: Room {self.room_number} by {interaction.user.display_name}",
                     self.event_id, level="judge")

        # Update judge queue embed
        await _refresh_judges_on_duty(interaction.client, self.event_id, interaction.guild)

        # Ping judges in noticeboard
        open_calls = db_get_open_calls(self.event_id)
        queue_pos  = len(open_calls)
        ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
        if ch:
            # FIX: get_judges_for_guild takes only guild; availability derived from voice state
            all_judges  = get_judges_for_guild(interaction.guild)
            busy_judges = [j for j in all_judges if not j.get("available")]
            available   = [j for j in all_judges if j.get("available")]
            busy_note = (
                f"\n⚠️ All judges currently busy — you are #{queue_pos} in queue."
                if busy_judges and not available else ""
            )
            # Mention Crew role if configured, otherwise @here
            role_mention = f"<@&{CREW_ROLE_ID}>" if CREW_ROLE_ID else "@here"
            await ch.send(
                f"⚖️ **JUDGE CALL — Room {self.room_number}** {role_mention}\n"
                f"**{interaction.user.display_name}** · Round {round_obj['round_number']}"
                f"{busy_note}",
                silent=False,
            )
        await log_immediate(interaction.client, "Judge Call",
            f"⚖️ Room {self.room_number} — **{interaction.user.display_name}**\n"
            f"Call ID: `{call_id}`  ·  Queue position: #{queue_pos}",
            COLOUR_CRIMSON)

        await interaction.response.send_message(
            f"✅ Judge call raised.\n"
            f"A judge will be with you shortly."
            + (f"\n⚠️ All judges are currently busy — you are #{queue_pos} in queue." if queue_pos > 1 else ""),
            ephemeral=True,
        )


class JudgeQueueView(ui.View):
    """
    Persistent view on the judge queue embed in #event-noticeboard.
    Any judge (Crew/admin) can acknowledge any open call — first click wins.
    Each acknowledged call shows a Close button only to the claiming judge or any admin.
    """
    def __init__(self, event_id: str, calls: list):
        super().__init__(timeout=None)
        self.event_id = event_id

        # One Acknowledge button per OPEN call (up to 4 on row 0+1)
        open_calls = [c for c in calls if c["state"] == JCS.OPEN]
        for i, call in enumerate(open_calls[:4]):
            btn = ui.Button(
                label=f"🔔  Ack Room {call['room_number']}",
                style=discord.ButtonStyle.primary,
                custom_id=f"jq_ack_{call['call_id']}",
                row=i // 2,
            )
            btn.callback = self._make_ack_cb(call["call_id"])
            self.add_item(btn)

        # One Close button per ACKNOWLEDGED call (up to 4 on row 2+3)
        ack_calls = [c for c in calls if c["state"] == JCS.ACKNOWLEDGED]
        for i, call in enumerate(ack_calls[:4]):
            judge_name = call.get("acknowledged_by_name") or "Judge"
            btn = ui.Button(
                label=f"✅  Close Room {call['room_number']} ({judge_name})",
                style=discord.ButtonStyle.secondary,
                custom_id=f"jq_close_{call['call_id']}",
                row=2 + i // 2,
            )
            btn.callback = self._make_close_cb(call["call_id"])
            self.add_item(btn)

    def _make_ack_cb(self, call_id: str):
        async def callback(interaction: discord.Interaction):
            if not is_to(interaction):
                await interaction.response.send_message("❌ Judges only.", ephemeral=True); return

            # Fetch call — check it's still open (race condition guard)
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM tournament_judge_calls WHERE call_id=%s", (call_id,))
                    row = cur.fetchone()
            if not row:
                await interaction.response.send_message("❌ Call not found.", ephemeral=True); return
            call = dict(row)
            if call["state"] != JCS.OPEN:
                await interaction.response.send_message(
                    f"ℹ️ Call already acknowledged by **{call.get('acknowledged_by_name','another judge')}**.",
                    ephemeral=True); return

            judge_id   = str(interaction.user.id)
            judge_name = interaction.user.display_name
            db_update_judge_call(call_id, {
                "state":               JCS.ACKNOWLEDGED,
                "acknowledged_at":     datetime.utcnow(),
                "acknowledged_by_id":  judge_id,
                "acknowledged_by_name": judge_name,
            })
            # NOTE: judge_take_call() removed — availability derived from voice channel state,
            # not tracked in memory. DB update above is the source of truth.

            db_queue_log(
                f"Judge {judge_name} acknowledged call {call_id} — Room {call['room_number']}",
                self.event_id
            )
            await _refresh_judges_on_duty(interaction.client, self.event_id, interaction.guild)

            # Notify the room
            ch = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
            if ch:
                await ch.send(
                    f"⚖️ **{judge_name}** is heading to **Room {call['room_number']}** — on the way!",
                    silent=True,
                )
            await interaction.response.send_message(
                f"✅ You've taken Room {call['room_number']}. Close the call when you're done.",
                ephemeral=True,
            )
        return callback

    def _make_close_cb(self, call_id: str):
        async def callback(interaction: discord.Interaction):
            if not is_to(interaction):
                await interaction.response.send_message("❌ Judges only.", ephemeral=True); return

            # Only the claiming judge or any admin can close
            with get_conn() as conn:
                with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    cur.execute("SELECT * FROM tournament_judge_calls WHERE call_id=%s", (call_id,))
                    row = cur.fetchone()
            if not row:
                await interaction.response.send_message("❌ Call not found.", ephemeral=True); return
            call = dict(row)
            uid  = str(interaction.user.id)
            is_admin = interaction.user.guild_permissions.administrator or is_to(interaction)
            is_claimer = uid == call.get("acknowledged_by_id")
            if not is_admin and not is_claimer:
                await interaction.response.send_message(
                    f"❌ Only **{call.get('acknowledged_by_name','the claiming judge')}** or an admin can close this call.",
                    ephemeral=True); return

            await interaction.response.send_modal(
                JudgeCloseModal(call_id, self.event_id, interaction.client, interaction.guild)
            )
        return callback


class ResultConfirmationView(ui.View):
    """Buttons on the pending result card in #event-submissions."""
    def __init__(self, game_id: str, event_id: str, opponent_id: str):
        super().__init__(timeout=None)
        self.game_id     = game_id
        self.event_id    = event_id
        self.opponent_id = opponent_id

    @ui.button(label="✅  Confirm", style=discord.ButtonStyle.success)
    async def btn_confirm(self, interaction: discord.Interaction, button: ui.Button):
        uid = str(interaction.user.id)
        if uid != self.opponent_id and not is_to(interaction):
            await interaction.response.send_message("❌ Only the opponent or a TO can confirm.", ephemeral=True); return
        await interaction.response.defer()
        await _confirm_game(interaction.client, self.game_id, interaction.message, interaction.guild)

    @ui.button(label="⚠️  Dispute", style=discord.ButtonStyle.danger)
    async def btn_dispute(self, interaction: discord.Interaction, button: ui.Button):
        uid = str(interaction.user.id)
        if uid != self.opponent_id and not is_to(interaction):
            await interaction.response.send_message("❌ Only the opponent can dispute.", ephemeral=True); return
        db_update_game(self.game_id, {"state": GS.DISPUTED})
        game = db_get_game(self.game_id)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title=f"⚠️  Result Disputed — Room {game['room_number']}",
                description="This result has been flagged for TO review.",
                color=COLOUR_AMBER,
            ),
            view=None,
        )
        await log_immediate(interaction.client, "Result Disputed",
            f"⚠️ Room {game['room_number']}: **{game['player1_username']}** vs **{game['player2_username']}**\n"
            f"Disputed by {interaction.user.display_name}\nUse `/result override` to resolve.",
            COLOUR_AMBER)
        db_queue_log(f"Result disputed: Room {game['room_number']}", self.event_id, level="dispute")

    @ui.button(label="⚡  TO Override", style=discord.ButtonStyle.secondary)
    async def btn_override(self, interaction: discord.Interaction, button: ui.Button):
        if not is_to(interaction):
            await interaction.response.send_message("❌ TO only.", ephemeral=True); return
        await interaction.response.defer()
        await _confirm_game(interaction.client, self.game_id, interaction.message, interaction.guild)


# ══════════════════════════════════════════════════════════════════════════════
# MODALS
# ══════════════════════════════════════════════════════════════════════════════

class ListSubmissionModal(ui.Modal, title="Submit Army List"):
    list_text = ui.TextInput(
        label="Paste your army list",
        style=discord.TextStyle.paragraph,
        placeholder="Include all units, points, detachment etc.",
        max_length=2000,
    )
    def __init__(self, event_id: str, army: str, detachment: str):
        super().__init__()
        self.event_id   = event_id
        self.army       = army
        self.detachment = detachment

    async def on_submit(self, interaction: discord.Interaction):
        reg = db_get_registration(self.event_id, str(interaction.user.id))
        if not reg:
            await interaction.response.send_message("❌ You're not registered for this event.", ephemeral=True); return
        db_upsert_registration(self.event_id, str(interaction.user.id),
                                interaction.user.display_name,
                                RS.PENDING, army=self.army,
                                det=self.detachment, list_text=self.list_text.value)
        event = db_get_event(self.event_id)
        db_queue_log(f"{interaction.user.display_name} submitted list ({self.army} / {self.detachment})", self.event_id)

        # Notify TO in submissions thread with Approve/Reject buttons
        sub_thread = None
        reg_obj = get_thread_reg(self.event_id)
        if reg_obj.get("submissions"):
            sub_thread = interaction.guild.get_thread(reg_obj["submissions"])
        # Fallback: noticeboard channel
        target = sub_thread or interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
        if target:
            from config import faction_colour
            embed = discord.Embed(
                title=f"📋  List Submitted — {interaction.user.display_name}",
                description=(
                    f"{fe(self.army)} **{self.army}**  ·  *{self.detachment}*\n\n"
                    f"```\n{self.list_text.value[:400]}{'...' if len(self.list_text.value) > 400 else ''}\n```"
                ),
                color=faction_colour(self.army),
            )
            embed.add_field(name="Event", value=event["name"], inline=True)
            embed.set_footer(text=f"TO: use buttons to approve or reject")
            await target.send(
                embed=embed,
                view=RegistrationApprovalView(self.event_id, str(interaction.user.id))
            )

        await interaction.response.send_message(
            f"✅ List submitted!\n"
            f"{fe(self.army)} **{self.army}** · *{self.detachment}*\n"
            f"Pending TO approval — you'll receive a DM when confirmed.",
            ephemeral=True,
        )


class ResultModal(ui.Modal, title="Submit Game Result"):
    p1_vp = ui.TextInput(label="Your VP score",       placeholder="e.g. 78", max_length=4)
    p2_vp = ui.TextInput(label="Opponent's VP score", placeholder="e.g. 55", max_length=4)

    def __init__(self, game_id: str, event_id: str, is_player1: bool, bot_ref):
        super().__init__()
        self.game_id    = game_id
        self.event_id   = event_id
        self.is_player1 = is_player1
        self.bot_ref    = bot_ref

    async def on_submit(self, interaction: discord.Interaction):
        try:
            vp_me  = int(self.p1_vp.value.strip())
            vp_opp = int(self.p2_vp.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ VP scores must be numbers.", ephemeral=True); return
        if not (0 <= vp_me <= 200 and 0 <= vp_opp <= 200):
            await interaction.response.send_message("❌ VP must be between 0 and 200.", ephemeral=True); return

        game = db_get_game(self.game_id)
        if not game:
            await interaction.response.send_message("❌ Game not found.", ephemeral=True); return
        if game["state"] == GS.COMPLETE:
            await interaction.response.send_message("❌ Result already confirmed.", ephemeral=True); return

        p1_vp = vp_me  if self.is_player1 else vp_opp
        p2_vp = vp_opp if self.is_player1 else vp_me
        winner_id = game["player1_id"] if p1_vp >= p2_vp else game["player2_id"]

        db_update_game(self.game_id, {
            "player1_vp":   p1_vp,
            "player2_vp":   p2_vp,
            "winner_id":    winner_id,
            "state":        GS.SUBMITTED,
            "submitted_at": datetime.utcnow(),
        })

        winner_name = game["player1_username"] if winner_id == game["player1_id"] else game["player2_username"]
        opponent_id = game["player2_id"] if self.is_player1 else game["player1_id"]

        # Post pending result card to submissions thread (fallback: noticeboard)
        reg_obj    = get_thread_reg(self.event_id)
        sub_target = None
        if reg_obj.get("submissions"):
            sub_target = interaction.guild.get_thread(reg_obj["submissions"])
        if not sub_target:
            sub_target = interaction.guild.get_channel(EVENT_NOTICEBOARD_ID)
        if sub_target:
            embed = discord.Embed(
                title=f"⏳  Pending Result  —  Room {game['room_number']}",
                color=COLOUR_AMBER,
            )
            embed.add_field(
                name=f"🔵 {game['player1_username']}",
                value=f"{fe(game['player1_army'])} *{game['player1_army']}*",
                inline=True,
            )
            embed.add_field(name="\u200b", value=f"**{p1_vp} — {p2_vp}**", inline=True)
            embed.add_field(
                name=f"🔴 {game['player2_username']}",
                value=f"{fe(game['player2_army'])} *{game['player2_army']}*",
                inline=True,
            )
            embed.add_field(name="🏆  Winner", value=f"**{winner_name}**", inline=False)
            embed.set_footer(text="Opponent: confirm below  ·  Dispute if incorrect  ·  Auto-confirms in 24h")
            msg = await sub_target.send(
                embed=embed,
                view=ResultConfirmationView(self.game_id, self.event_id, opponent_id),
            )
            db_update_game(self.game_id, {
                "result_msg_id":     str(msg.id),
                "result_channel_id": str(sub_target.id),
            })
            self.bot_ref.loop.create_task(
                _auto_confirm_after_24h(self.bot_ref, self.game_id, msg, interaction.guild)
            )

        db_queue_log(
            f"Result submitted: Room {game['room_number']} — "
            f"{game['player1_username']} {p1_vp}:{p2_vp} {game['player2_username']}",
            self.event_id,
        )
        await interaction.response.send_message(
            f"✅ Result submitted!\n**{p1_vp} — {p2_vp}**\nWaiting for opponent confirmation.",
            ephemeral=True,
        )


class RejectionReasonModal(ui.Modal, title="Rejection Reason"):
    reason = ui.TextInput(label="Reason (shown to player)", max_length=200,
                           placeholder="e.g. List over points limit, missing required units")

    def __init__(self, event_id: str, player_id: str, original_message):
        super().__init__()
        self.event_id        = event_id
        self.player_id       = player_id
        self.original_message = original_message

    async def on_submit(self, interaction: discord.Interaction):
        reg = db_get_registration(self.event_id, self.player_id)
        if not reg:
            await interaction.response.send_message("❌ Not found.", ephemeral=True); return
        db_update_registration(self.event_id, self.player_id,
                                {"state": RS.REJECTED, "rejection_reason": self.reason.value})
        event = db_get_event(self.event_id)
        try:
            user = await interaction.client.fetch_user(int(self.player_id))
            await user.send(
                f"❌ **Registration rejected for {event['name']}**\n"
                f"Reason: {self.reason.value}\n"
                f"Contact the TO if you have questions."
            )
        except: pass
        await log_immediate(interaction.client, "Registration Rejected",
            f"❌ {reg['player_username']} rejected from {event['name']}\nReason: {self.reason.value}",
            COLOUR_CRIMSON)
        await interaction.response.edit_message(
            content=f"❌ **{reg['player_username']}** rejected. Player notified.",
            embed=None, view=None,
        )


class VPAdjustModal(ui.Modal, title="Adjust Result VPs"):
    new_p1_vp = ui.TextInput(label="Player 1 VP (corrected)", max_length=4)
    new_p2_vp = ui.TextInput(label="Player 2 VP (corrected)", max_length=4)
    note      = ui.TextInput(label="Reason for adjustment", max_length=200,
                              placeholder="e.g. Judge ruling — illegal unit removed")

    def __init__(self, game_id: str, event_id: str, bot_ref, guild):
        super().__init__()
        self.game_id  = game_id
        self.event_id = event_id
        self.bot_ref  = bot_ref
        self.guild    = guild

    async def on_submit(self, interaction: discord.Interaction):
        try:
            new_p1 = int(self.new_p1_vp.value.strip())
            new_p2 = int(self.new_p2_vp.value.strip())
        except ValueError:
            await interaction.response.send_message("❌ VP must be numbers.", ephemeral=True); return

        game = db_get_game(self.game_id)
        if not game:
            await interaction.response.send_message("❌ Game not found.", ephemeral=True); return

        old_p1 = game.get("player1_vp", 0) or 0
        old_p2 = game.get("player2_vp", 0) or 0
        old_winner = game.get("winner_id")
        old_loser  = game["player2_id"] if old_winner == game["player1_id"] else game["player1_id"]

        # Reverse old result from standings
        if old_winner:
            db_reverse_result_from_standings(self.event_id, old_winner, old_loser, old_p1, old_p2)

        # Apply new result
        new_winner_id = game["player1_id"] if new_p1 >= new_p2 else game["player2_id"]
        new_loser_id  = game["player2_id"] if new_winner_id == game["player1_id"] else game["player1_id"]
        db_apply_result_to_standings(self.event_id, new_winner_id, new_loser_id, new_p1, new_p2)

        db_update_game(self.game_id, {
            "player1_vp": new_p1,
            "player2_vp": new_p2,
            "winner_id":  new_winner_id,
            "adj_note":   self.note.value,
        })

        new_winner_name = game["player1_username"] if new_winner_id == game["player1_id"] else game["player2_username"]
        await log_immediate(self.bot_ref, "VP Adjustment",
            f"🔧 Room {game['room_number']}: **{game['player1_username']}** {new_p1}—{new_p2} **{game['player2_username']}**\n"
            f"*(was {old_p1}—{old_p2})*\nWinner: **{new_winner_name}**\nReason: {self.note.value}\n"
            f"Adjusted by {interaction.user.display_name}",
            COLOUR_AMBER)
        db_queue_log(
            f"VP adjusted: Room {game['room_number']} — {old_p1}:{old_p2} → {new_p1}:{new_p2}  ({self.note.value})",
            self.event_id, level="adjust"
        )
        await refresh_spectator_dashboard(self.bot_ref, self.event_id)
        await interaction.response.send_message(
            f"✅ Result adjusted.\n**{new_p1} — {new_p2}**  Winner: **{new_winner_name}**",
            ephemeral=True,
        )


class JudgeCloseModal(ui.Modal, title="Close Judge Call"):
    vp_adj = ui.TextInput(
        label="VP adjustment (leave blank if none)",
        required=False,
        max_length=100,
        placeholder="e.g. Room 3 P1 -5 VP — illegal model"
    )

    def __init__(self, call_id: str, event_id: str, bot_ref, guild):
        super().__init__()
        self.call_id  = call_id
        self.event_id = event_id
        self.bot_ref  = bot_ref
        self.guild    = guild

    async def on_submit(self, interaction: discord.Interaction):
        adj        = self.vp_adj.value.strip() if self.vp_adj.value else None
        judge_id   = str(interaction.user.id)
        judge_name = interaction.user.display_name
        db_update_judge_call(self.call_id, {
            "state":          JCS.CLOSED,
            "closed_at":      datetime.utcnow(),
            "closed_by_id":   judge_id,
            "closed_by_name": judge_name,
            "vp_adjustment":  adj,
        })
        # NOTE: judge_release_call() removed — availability derived from voice channel state,
        # not tracked in memory. DB update above is the source of truth.

        db_queue_log(
            f"Judge {judge_name} closed call {self.call_id}"
            + (f" — VP adj: {adj}" if adj else ""),
            self.event_id
        )

        if adj:
            await log_immediate(self.bot_ref, "Judge Ruling — VP Adjustment Noted",
                f"⚖️ Call `{self.call_id}` closed by **{judge_name}**\n"
                f"Noted adjustment: *{adj}*\n"
                f"Use `/result adjust` to apply the VP change to standings.",
                COLOUR_AMBER)

        await _refresh_judges_on_duty(self.bot_ref, self.event_id, self.guild)
        await interaction.response.send_message(
            f"✅ Call `{self.call_id}` closed."
            + (f"\n📝 Adjustment noted: *{adj}*\nApply with `/result adjust` if needed." if adj else ""),
            ephemeral=True,
        )


# ══════════════════════════════════════════════════════════════════════════════
# GAME RESULT LIFECYCLE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

async def _confirm_game(bot, game_id: str, message: discord.Message, guild: discord.Guild):
    game = db_get_game(game_id)
    if not game or game["state"] == GS.COMPLETE:
        return

    p1_vp     = game["player1_vp"] or 0
    p2_vp     = game["player2_vp"] or 0
    winner_id = game["winner_id"]
    loser_id  = game["player2_id"] if winner_id == game["player1_id"] else game["player1_id"]

    db_update_game(game_id, {"state": GS.COMPLETE, "confirmed_at": datetime.utcnow()})
    db_apply_result_to_standings(game["event_id"], winner_id, loser_id, p1_vp, p2_vp)

    winner_name = game["player1_username"] if winner_id == game["player1_id"] else game["player2_username"]
    col  = room_colour(game.get("room_number"))
    embed = discord.Embed(
        title=f"✅  Result Confirmed  —  Room {game['room_number']}",
        color=col,
    )
    embed.add_field(name=f"🔵 {game['player1_username']}", value=f"{fe(game['player1_army'])}", inline=True)
    embed.add_field(name="\u200b", value=f"**{p1_vp} — {p2_vp}**", inline=True)
    embed.add_field(name=f"🔴 {game['player2_username']}", value=f"{fe(game['player2_army'])}", inline=True)
    embed.add_field(name="🏆  Winner", value=f"**{winner_name}**", inline=False)
    embed.set_footer(text="Confirmed  ·  Results held until event closes, then submitted to Scorebot")

    await message.edit(embed=embed, view=None)
    db_queue_log(
        f"Result confirmed: Room {game['room_number']} — "
        f"{game['player1_username']} {p1_vp}:{p2_vp} {game['player2_username']} → {winner_name}",
        game["event_id"],
    )
    await refresh_spectator_dashboard(bot, game["event_id"])

async def _auto_confirm_after_24h(bot, game_id: str, message: discord.Message, guild: discord.Guild):
    await asyncio.sleep(86400)
    game = db_get_game(game_id)
    if game and game["state"] == GS.SUBMITTED:
        await _confirm_game(bot, game_id, message, guild)
