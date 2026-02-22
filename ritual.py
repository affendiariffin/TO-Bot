"""
ritual.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Team pairing ritual state machine for Teams 3s/5s/8s events.

Contains:
  • _layout_mission_pickers / build_pairing_dashboard_embed
  • _get_unpaired / _update_dashboard / _post_ritual_update / _wait_for_both
  • Views: SelectDefenderView, SelectAttackersView, ChooseAttackerView,
            SelectLayoutView, SelectMissionView
  • run_pairing_phase / run_layout_mission_phase / _finalise_5s_scrum
  • run_ritual_35   — entry point for Teams 3s/5s ritual
  • run_ritual_8    — entry point for Teams 8s NTL ritual  (ritual_8s.py)
  • RollOffView
  • /round begin-ritual command
  • /roll command (standalone D6 roller)
"""
import discord
from discord import app_commands, ui
import asyncio
import random as _random
from datetime import datetime
from typing import List, Optional
from config import (GUILD, GUILD_ID, COLOUR_GOLD, COLOUR_AMBER, COLOUR_SLATE,
                    fe)
from state import PS, TRS, FMT, is_to
from database import *
from services import ac_active_events
from commands_teams import ensure_pairing_room_thread  # thread helper lives there
from commands_round import round_grp                   # begin-ritual hangs off /round
from ritual_8s import run_ritual_8                     # Teams 8s NTL ritual


def _layout_mission_pickers(slot: int, fmt: str, layout_winner: str) -> tuple:
    other = "team_b" if layout_winner == "team_a" else "team_a"
    if fmt == FMT.TEAMS_3:
        lut = {1: (layout_winner, other), 2: (other, layout_winner), 3: (other, layout_winner)}
        return lut.get(slot, (layout_winner, other))
    if fmt == FMT.TEAMS_5:
        return (layout_winner, other) if slot % 2 == 1 else (other, layout_winner)
    if fmt == FMT.TEAMS_8:
        winner_slots = {1, 4, 5, 8}
        return (layout_winner, other) if slot in winner_slots else (other, layout_winner)
    return layout_winner, other


def build_pairing_dashboard_embed(
    event: dict, round_obj: dict, tr: dict, state: dict,
    pairings: List[dict], team_a: dict, team_b: dict,
    members_a: List[dict], members_b: List[dict],
) -> discord.Embed:
    fmt         = event.get("format", "teams_8")
    phase_count = FMT.phase_count(fmt)
    phase       = state["current_phase"]
    step        = state["current_step"]

    embed = discord.Embed(
        title=f"🎲 LIVE PAIRING  ·  Round {round_obj['round_number']}  ·  Phase {phase}/{phase_count}",
        color=COLOUR_AMBER,
    )

    lw = tr.get("layout_picker", "")
    lw_name = team_a["team_name"] if lw == "team_a" else (team_b["team_name"] if lw == "team_b" else "TBD")
    embed.add_field(
        name=f"⚔️  {team_a['team_name']}  vs  {team_b['team_name']}",
        value=f"Roll-off: **{lw_name}** picks layouts" if lw else "⏳ Awaiting roll-off...",
        inline=False,
    )

    confirmed = [p for p in pairings if p.get("attacker_player_id")]
    if confirmed:
        lines = ["```", f"{'Slot':<5} {'Team A':<20} {'Team B':<20} {'Layout':<7} Mission", "─"*60]
        for p in confirmed:
            def pname(pid, members):
                m = next((x for x in members if x["player_id"] == pid), None)
                return f"{m['player_username'][:14]} ({fe(m.get('army','?'))})" if m else (pid or "?")[:16]
            la = str(p.get("layout_number") or "—")
            mi = p.get("mission_code") or "—"
            lines.append(f"{p['pairing_slot']:<5} {pname(p['defender_player_id'], members_a):<20} "
                          f"{pname(p['attacker_player_id'], members_b):<20} {la:<7} {mi}")
        lines.append("```")
        embed.add_field(name="✅ Confirmed Pairings", value="\n".join(lines), inline=False)

    used_a = {p["defender_player_id"] for p in pairings if p.get("defender_player_id")}
    used_a |= {p.get("refused_player_id") for p in pairings if p.get("refused_player_id")}
    used_b = {p["attacker_player_id"] for p in pairings if p.get("attacker_player_id")}

    rem_a = [m for m in members_a if m["player_id"] not in used_a and m["role"] != "substitute"]
    rem_b = [m for m in members_b if m["player_id"] not in used_b and m["role"] != "substitute"]

    if rem_a or rem_b:
        embed.add_field(
            name=f"📋 Remaining — {team_a['team_name']}",
            value=", ".join(f"{fe(m.get('army',''))} {m['player_username']}" for m in rem_a) or "—",
            inline=True,
        )
        embed.add_field(
            name=f"📋 Remaining — {team_b['team_name']}",
            value=", ".join(f"{fe(m.get('army',''))} {m['player_username']}" for m in rem_b) or "—",
            inline=True,
        )

    step_labels = {
        PS.AWAIT_ROLLOFF:   "⏳ Awaiting captain roll-off...",
        PS.AWAIT_DEFENDERS: "🛡️ Both captains selecting Defender (secret)...",
        PS.AWAIT_ATTACKERS: "⚔️ Both captains selecting Attackers (secret)...",
        PS.AWAIT_CHOICE:    "🎯 Both captains choosing which Attacker to send (secret)...",
        PS.AWAIT_LAYOUT_A:  f"🗺️ {team_a['team_name']} selecting Layout...",
        PS.AWAIT_MISSION_A: f"🎯 {team_a['team_name']} selecting Mission...",
        PS.AWAIT_LAYOUT_B:  f"🗺️ {team_b['team_name']} selecting Layout...",
        PS.AWAIT_MISSION_B: f"🎯 {team_b['team_name']} selecting Mission...",
        PS.COMPLETE:        "✅ All pairings confirmed!",
    }
    embed.add_field(name="CURRENT STEP", value=step_labels.get(step, step), inline=False)
    embed.set_footer(text=f"Phase {phase}/{phase_count}  ·  Captains receive prompts via DM")
    return embed


def _get_unpaired(members: List[dict], pairings: List[dict], side: str) -> List[dict]:
    if side == "a":
        used = {p["defender_player_id"] for p in pairings if p.get("defender_player_id")}
        used |= {p.get("refused_player_id") for p in pairings if p.get("refused_player_id")}
    else:
        used = {p["attacker_player_id"] for p in pairings if p.get("attacker_player_id")}
    return [m for m in members if m["player_id"] not in used and m["role"] != "substitute"]


async def _update_dashboard(bot, tr_id: str, guild: discord.Guild):
    tr      = db_get_team_round(tr_id)
    if not tr or not tr.get("pairing_thread_id"):
        return
    thread  = guild.get_thread(int(tr["pairing_thread_id"]))
    if not thread:
        return
    event   = db_get_event(tr["event_id"])
    rnd     = db_get_round(tr["round_id"])
    state   = db_get_pairing_state(tr_id)
    pairings = db_get_team_pairings(tr_id)
    team_a  = db_get_team(tr["team_a_id"])
    team_b  = db_get_team(tr["team_b_id"])
    ma      = db_get_team_members(tr["team_a_id"])
    mb      = db_get_team_members(tr["team_b_id"])
    embed   = build_pairing_dashboard_embed(event, rnd, tr, state, pairings, team_a, team_b, ma, mb)
    try:
        pins = await thread.pins()
        dashboard_msg = next((m for m in pins if m.author.id == bot.user.id), None)
        if dashboard_msg:
            await dashboard_msg.edit(embed=embed)
        else:
            msg = await thread.send(embed=embed)
            await msg.pin()
    except Exception as e:
        print(f"⚠️ Dashboard update failed: {e}")


async def _post_ritual_update(thread: discord.Thread, text: str):
    try:
        await thread.send(text, silent=True)
    except Exception:
        pass


async def _wait_for_both(bot, tr_id: str, field_a: str, field_b: str,
                          timeout: int = 600) -> bool:
    for _ in range(timeout // 3):
        await asyncio.sleep(3)
        state = db_get_pairing_state(tr_id)
        if not state:
            return False
        if state.get(field_a) and state.get(field_b):
            return True
    return False


# ── Ritual interaction views ──────────────────────────────────────────────────

class SelectDefenderView(ui.View):
    def __init__(self, tr_id: str, captain_id: str, eligible: List[dict], side: str):
        super().__init__(timeout=600)
        self.tr_id      = tr_id
        self.captain_id = captain_id
        self.side       = side
        options = [
            discord.SelectOption(label=m["player_username"][:25], value=m["player_id"],
                                  description=str(m.get("army","?"))[:50], emoji=fe(m.get("army","")))
            for m in eligible[:25]
        ]
        sel = ui.Select(placeholder="Choose your Defender...", options=options, min_values=1, max_values=1)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.captain_id:
            await interaction.response.send_message("❌ Only the captain can select.", ephemeral=True); return
        chosen = interaction.data["values"][0]
        field  = "defender_a" if self.side == "a" else "defender_b"
        if db_get_pairing_state(self.tr_id).get(field):
            await interaction.response.send_message("✅ Already submitted.", ephemeral=True); return
        db_update_pairing_state(self.tr_id, {field: chosen})
        self.stop()
        await interaction.response.edit_message(
            content="🛡️ **Defender submitted.** ⏳ Waiting for opponent...", view=None)


class SelectAttackersView(ui.View):
    def __init__(self, tr_id: str, captain_id: str, eligible: List[dict], side: str, count: int = 2):
        super().__init__(timeout=600)
        self.tr_id      = tr_id
        self.captain_id = captain_id
        self.side       = side
        options = [
            discord.SelectOption(label=m["player_username"][:25], value=m["player_id"],
                                  description=str(m.get("army","?"))[:50], emoji=fe(m.get("army","")))
            for m in eligible[:25]
        ]
        sel = ui.Select(placeholder=f"Choose {count} Attacker(s)...", options=options,
                         min_values=count, max_values=count)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.captain_id:
            await interaction.response.send_message("❌ Only the captain can select.", ephemeral=True); return
        chosen = interaction.data["values"]
        field  = "attackers_a" if self.side == "a" else "attackers_b"
        if db_get_pairing_state(self.tr_id).get(field):
            await interaction.response.send_message("✅ Already submitted.", ephemeral=True); return
        db_update_pairing_state(self.tr_id, {field: chosen})
        self.stop()
        await interaction.response.edit_message(
            content="⚔️ **Attackers submitted.** ⏳ Waiting for opponent...", view=None)


class ChooseAttackerView(ui.View):
    def __init__(self, tr_id: str, captain_id: str, side: str, opp_attackers: List[dict]):
        super().__init__(timeout=600)
        self.tr_id      = tr_id
        self.captain_id = captain_id
        self.side       = side
        options = [
            discord.SelectOption(label=m["player_username"][:25], value=m["player_id"],
                                  description=str(m.get("army","?"))[:50], emoji=fe(m.get("army","")))
            for m in opp_attackers[:25]
        ]
        sel = ui.Select(placeholder="Choose which attacker faces your defender...",
                         options=options, min_values=1, max_values=1)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.captain_id:
            await interaction.response.send_message("❌ Only the captain can choose.", ephemeral=True); return
        chosen = interaction.data["values"][0]
        field  = "choice_a" if self.side == "a" else "choice_b"
        if db_get_pairing_state(self.tr_id).get(field):
            await interaction.response.send_message("✅ Already submitted.", ephemeral=True); return
        db_update_pairing_state(self.tr_id, {field: chosen})
        self.stop()
        await interaction.response.edit_message(
            content="🎯 **Choice submitted.** ⏳ Waiting for opponent...", view=None)


class SelectLayoutView(ui.View):
    def __init__(self, tr_id: str, captain_id: str, slot: int, available_layouts: List[str]):
        super().__init__(timeout=300)
        self.tr_id      = tr_id
        self.captain_id = captain_id
        self.slot       = slot
        options = [discord.SelectOption(label=f"Layout {l}", value=l) for l in available_layouts[:25]]
        sel = ui.Select(placeholder=f"Choose layout for Slot {slot}...",
                         options=options, min_values=1, max_values=1)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.captain_id:
            await interaction.response.send_message("❌ Only the relevant captain can pick.", ephemeral=True); return
        chosen = interaction.data["values"][0]
        pairings = db_get_team_pairings(self.tr_id)
        pairing  = next((p for p in pairings if p["pairing_slot"] == self.slot and not p.get("layout_number")), None)
        if pairing:
            db_update_team_pairing(pairing["pairing_id"], {"layout_number": int(chosen)})
        self.stop()
        await interaction.response.edit_message(
            content=f"🗺️ **Layout {chosen} selected for Slot {self.slot}.**", view=None)


class SelectMissionView(ui.View):
    def __init__(self, tr_id: str, captain_id: str, slot: int, layout: int):
        super().__init__(timeout=300)
        self.tr_id      = tr_id
        self.captain_id = captain_id
        self.slot       = slot
        _missions = db_get_missions()
        valid = [(code, m) for code, m in _missions.items()
                 if str(layout) in m.get("layouts", [])]
        options = [
            discord.SelectOption(label=f"{code}: {m['name']}", value=code,
                                  description=m["deployment"][:50])
            for code, m in (valid or list(_missions.items()))[:25]
        ]
        sel = ui.Select(placeholder=f"Choose mission for Slot {slot}...",
                         options=options, min_values=1, max_values=1)
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.captain_id:
            await interaction.response.send_message("❌ Only the relevant captain can pick.", ephemeral=True); return
        chosen = interaction.data["values"][0]
        pairings = db_get_team_pairings(self.tr_id)
        pairing  = next((p for p in pairings if p["pairing_slot"] == self.slot and not p.get("mission_code")), None)
        if pairing:
            db_update_team_pairing(pairing["pairing_id"], {"mission_code": chosen})
        self.stop()
        await interaction.response.edit_message(
            content=f"🎯 **Mission {chosen} selected for Slot {self.slot}.**", view=None)


# ── Core ritual phase engine ──────────────────────────────────────────────────

async def run_pairing_phase(bot, guild: discord.Guild, tr_id: str, phase: int) -> bool:
    tr      = db_get_team_round(tr_id)
    event   = db_get_event(tr["event_id"])
    fmt     = event.get("format", "teams_5")
    team_a  = db_get_team(tr["team_a_id"])
    team_b  = db_get_team(tr["team_b_id"])
    ma_all  = db_get_team_members(tr["team_a_id"])
    mb_all  = db_get_team_members(tr["team_b_id"])
    pairings = db_get_team_pairings(tr_id)
    thread  = guild.get_thread(int(tr["pairing_thread_id"])) if tr.get("pairing_thread_id") else None
    cap_a, cap_b = team_a["captain_id"], team_b["captain_id"]
    mem_a = guild.get_member(int(cap_a))
    mem_b = guild.get_member(int(cap_b))

    # Reset per-phase state fields
    db_update_pairing_state(tr_id, {
        "current_phase": phase, "current_step": PS.AWAIT_DEFENDERS,
        "defender_a": None, "defender_b": None,
        "attackers_a": None, "attackers_b": None,
        "choice_a": None, "choice_b": None,
    })
    await _update_dashboard(bot, tr_id, guild)
    if thread:
        await _post_ritual_update(thread, f"🛡️ **Phase {phase} — Both captains selecting Defender...**")

    elig_a = _get_unpaired(ma_all, pairings, "a")
    elig_b = _get_unpaired(mb_all, pairings, "b")

    if mem_a:
        try: await mem_a.send(f"🛡️ **Phase {phase}: Choose your Defender** ({team_a['team_name']}):",
                               view=SelectDefenderView(tr_id, cap_a, elig_a, "a"))
        except Exception as e: print(f"⚠️ DM cap_a: {e}")
    if mem_b:
        try: await mem_b.send(f"🛡️ **Phase {phase}: Choose your Defender** ({team_b['team_name']}):",
                               view=SelectDefenderView(tr_id, cap_b, elig_b, "b"))
        except Exception as e: print(f"⚠️ DM cap_b: {e}")

    if not await _wait_for_both(bot, tr_id, "defender_a", "defender_b"):
        if thread: await thread.send("⚠️ Defender selection timed out. TO intervention required.")
        return False

    state   = db_get_pairing_state(tr_id)
    def_a_id, def_b_id = state["defender_a"], state["defender_b"]
    def_a = next((m for m in ma_all if m["player_id"] == def_a_id), {})
    def_b = next((m for m in mb_all if m["player_id"] == def_b_id), {})

    await _update_dashboard(bot, tr_id, guild)
    if thread:
        await _post_ritual_update(thread,
            f"🛡️ **Defenders revealed!** "
            f"{team_a['team_name']}: {fe(def_a.get('army',''))} **{def_a.get('player_username','?')}**  |  "
            f"{team_b['team_name']}: {fe(def_b.get('army',''))} **{def_b.get('player_username','?')}**")

    # ── Attackers ─────────────────────────────────────────────────────────────
    db_update_pairing_state(tr_id, {"current_step": PS.AWAIT_ATTACKERS})
    elig_att_a = [m for m in elig_a if m["player_id"] != def_a_id]
    elig_att_b = [m for m in elig_b if m["player_id"] != def_b_id]
    cnt_a, cnt_b = min(2, len(elig_att_a)), min(2, len(elig_att_b))

    if mem_a:
        try: await mem_a.send(f"⚔️ **Phase {phase}: Choose {cnt_a} Attacker(s):**",
                               view=SelectAttackersView(tr_id, cap_a, elig_att_a, "a", cnt_a))
        except Exception as e: print(f"⚠️ DM cap_a: {e}")
    if mem_b:
        try: await mem_b.send(f"⚔️ **Phase {phase}: Choose {cnt_b} Attacker(s):**",
                               view=SelectAttackersView(tr_id, cap_b, elig_att_b, "b", cnt_b))
        except Exception as e: print(f"⚠️ DM cap_b: {e}")

    if not await _wait_for_both(bot, tr_id, "attackers_a", "attackers_b"):
        if thread: await thread.send("⚠️ Attacker selection timed out.")
        return False

    state  = db_get_pairing_state(tr_id)
    atts_a = state["attackers_a"] or []
    atts_b = state["attackers_b"] or []
    att_a_members = [m for m in ma_all if m["player_id"] in atts_a]
    att_b_members = [m for m in mb_all if m["player_id"] in atts_b]

    await _update_dashboard(bot, tr_id, guild)
    if thread:
        await _post_ritual_update(thread,
            f"⚔️ **Attackers revealed!**\n"
            f"{team_a['team_name']}: " + ", ".join(f"{fe(m.get('army',''))} **{m['player_username']}**" for m in att_a_members) +
            f"\n{team_b['team_name']}: " + ", ".join(f"{fe(m.get('army',''))} **{m['player_username']}**" for m in att_b_members))

    # ── Choices ───────────────────────────────────────────────────────────────
    db_update_pairing_state(tr_id, {"current_step": PS.AWAIT_CHOICE})

    if mem_a:
        try: await mem_a.send(
            f"🎯 **Phase {phase}: Choose which attacker faces your defender "
            f"({def_a.get('player_username','?')}):**",
            view=ChooseAttackerView(tr_id, cap_a, "a", att_b_members))
        except Exception as e: print(f"⚠️ DM cap_a: {e}")
    if mem_b:
        try: await mem_b.send(
            f"🎯 **Phase {phase}: Choose which attacker faces your defender "
            f"({def_b.get('player_username','?')}):**",
            view=ChooseAttackerView(tr_id, cap_b, "b", att_a_members))
        except Exception as e: print(f"⚠️ DM cap_b: {e}")

    if not await _wait_for_both(bot, tr_id, "choice_a", "choice_b"):
        if thread: await thread.send("⚠️ Choice selection timed out.")
        return False

    state    = db_get_pairing_state(tr_id)
    choice_a = state["choice_a"]
    choice_b = state["choice_b"]
    refused_b = next((p for p in atts_b if p != choice_a), None)
    refused_a = next((p for p in atts_a if p != choice_b), None)

    # ── Confirm pairings ──────────────────────────────────────────────────────
    confirmed_so_far = len([p for p in pairings if p.get("attacker_player_id")])
    slot_1, slot_2   = confirmed_so_far + 1, confirmed_so_far + 2
    lw = tr.get("layout_picker", "team_a")

    pid1 = db_create_team_pairing(tr_id, slot_1)
    lp1, mp1 = _layout_mission_pickers(slot_1, fmt, lw)
    db_update_team_pairing(pid1, {
        "defender_player_id": def_a_id, "defender_team_id": tr["team_a_id"],
        "attacker_player_id": choice_a, "attacker_team_id": tr["team_b_id"],
        "refused_player_id": refused_b, "layout_picker_team": lp1, "mission_picker_team": mp1,
    })

    pid2 = db_create_team_pairing(tr_id, slot_2)
    lp2, mp2 = _layout_mission_pickers(slot_2, fmt, lw)
    db_update_team_pairing(pid2, {
        "defender_player_id": def_b_id, "defender_team_id": tr["team_b_id"],
        "attacker_player_id": choice_b, "attacker_team_id": tr["team_a_id"],
        "refused_player_id": refused_a, "layout_picker_team": lp2, "mission_picker_team": mp2,
    })

    # Teams 3s: refused attackers auto-match as slot 3
    if fmt == FMT.TEAMS_3 and phase == 1 and refused_a and refused_b:
        pid3 = db_create_team_pairing(tr_id, 3)
        lp3, mp3 = _layout_mission_pickers(3, fmt, lw)
        db_update_team_pairing(pid3, {
            "defender_player_id": refused_a, "defender_team_id": tr["team_a_id"],
            "attacker_player_id": refused_b, "attacker_team_id": tr["team_b_id"],
            "layout_picker_team": lp3, "mission_picker_team": mp3,
        })

    ch_a_m = next((m for m in mb_all if m["player_id"] == choice_a), {})
    ch_b_m = next((m for m in ma_all if m["player_id"] == choice_b), {})

    await _update_dashboard(bot, tr_id, guild)
    if thread:
        msg = (
            f"✅ **Phase {phase} pairings confirmed!**\n"
            f"Slot {slot_1}: {fe(def_a.get('army',''))} **{def_a.get('player_username','?')}** vs "
            f"{fe(ch_a_m.get('army',''))} **{ch_a_m.get('player_username','?')}**\n"
            f"Slot {slot_2}: {fe(def_b.get('army',''))} **{def_b.get('player_username','?')}** vs "
            f"{fe(ch_b_m.get('army',''))} **{ch_b_m.get('player_username','?')}**"
        )
        if fmt == FMT.TEAMS_3 and phase == 1 and refused_a and refused_b:
            ref_a_m = next((m for m in ma_all if m["player_id"] == refused_a), {})
            ref_b_m = next((m for m in mb_all if m["player_id"] == refused_b), {})
            msg += (f"\nSlot 3 (auto): {fe(ref_a_m.get('army',''))} **{ref_a_m.get('player_username','?')}** vs "
                    f"{fe(ref_b_m.get('army',''))} **{ref_b_m.get('player_username','?')}**")
        await _post_ritual_update(thread, msg)

    return True


async def run_layout_mission_phase(bot, guild: discord.Guild, tr_id: str, slots: List[int]):
    tr     = db_get_team_round(tr_id)
    event  = db_get_event(tr["event_id"])
    fmt    = event.get("format", "teams_5")
    lw     = tr.get("layout_picker", "team_a")
    team_a = db_get_team(tr["team_a_id"])
    team_b = db_get_team(tr["team_b_id"])
    thread = guild.get_thread(int(tr["pairing_thread_id"])) if tr.get("pairing_thread_id") else None
    all_layouts = [str(i) for i in range(1, 9)]

    for slot in slots:
        pairings = db_get_team_pairings(tr_id)
        pairing  = next((p for p in pairings if p["pairing_slot"] == slot), None)
        if not pairing:
            continue

        lp, mp = _layout_mission_pickers(slot, fmt, lw)
        layout_cap_id    = team_a["captain_id"] if lp == "team_a" else team_b["captain_id"]
        mission_cap_id   = team_a["captain_id"] if mp == "team_a" else team_b["captain_id"]
        layout_team_name = team_a["team_name"] if lp == "team_a" else team_b["team_name"]
        mission_team_name= team_a["team_name"] if mp == "team_a" else team_b["team_name"]

        used_layouts = [str(p["layout_number"]) for p in pairings if p.get("layout_number")]
        available    = [l for l in all_layouts if l not in used_layouts] or all_layouts

        db_update_pairing_state(tr_id, {"current_step": PS.AWAIT_LAYOUT_A if lp == "team_a" else PS.AWAIT_LAYOUT_B})
        await _update_dashboard(bot, tr_id, guild)
        if thread:
            await _post_ritual_update(thread, f"🗺️ **Slot {slot}: {layout_team_name}** choosing layout...")

        layout_member = guild.get_member(int(layout_cap_id))
        if layout_member:
            try: await layout_member.send(f"🗺️ **Slot {slot}: Choose a layout:**",
                                           view=SelectLayoutView(tr_id, layout_cap_id, slot, available))
            except Exception as e: print(f"⚠️ DM layout: {e}")

        # Poll for layout selection
        layout_num = int(available[0])
        for _ in range(100):
            await asyncio.sleep(3)
            updated = db_get_team_pairings(tr_id)
            p = next((x for x in updated if x["pairing_slot"] == slot and x.get("layout_number")), None)
            if p:
                layout_num = p["layout_number"]
                break

        db_update_pairing_state(tr_id, {"current_step": PS.AWAIT_MISSION_A if mp == "team_a" else PS.AWAIT_MISSION_B})
        await _update_dashboard(bot, tr_id, guild)
        if thread:
            await _post_ritual_update(thread, f"🎯 **Slot {slot}: {mission_team_name}** choosing mission...")

        mission_member = guild.get_member(int(mission_cap_id))
        if mission_member:
            try: await mission_member.send(f"🎯 **Slot {slot}: Choose a mission for Layout {layout_num}:**",
                                            view=SelectMissionView(tr_id, mission_cap_id, slot, layout_num))
            except Exception as e: print(f"⚠️ DM mission: {e}")

        for _ in range(100):
            await asyncio.sleep(3)
            updated = db_get_team_pairings(tr_id)
            if next((x for x in updated if x["pairing_slot"] == slot and x.get("mission_code")), None):
                break

        await _update_dashboard(bot, tr_id, guild)


async def _finalise_5s_scrum(bot, guild: discord.Guild, tr_id: str, slot: int):
    tr      = db_get_team_round(tr_id)
    ma_all  = db_get_team_members(tr["team_a_id"])
    mb_all  = db_get_team_members(tr["team_b_id"])
    pairings = db_get_team_pairings(tr_id)
    rem_a   = _get_unpaired(ma_all, pairings, "a")
    rem_b   = _get_unpaired(mb_all, pairings, "b")
    if not rem_a or not rem_b:
        return
    event = db_get_event(tr["event_id"])
    lw    = tr.get("layout_picker", "team_a")
    lp, mp = _layout_mission_pickers(slot, event.get("format","teams_5"), lw)
    pid = db_create_team_pairing(tr_id, slot)
    db_update_team_pairing(pid, {
        "defender_player_id": rem_a[0]["player_id"], "defender_team_id": tr["team_a_id"],
        "attacker_player_id": rem_b[0]["player_id"], "attacker_team_id": tr["team_b_id"],
        "layout_picker_team": lp, "mission_picker_team": mp,
    })
    thread = guild.get_thread(int(tr["pairing_thread_id"])) if tr.get("pairing_thread_id") else None
    if thread:
        await _post_ritual_update(thread,
            f"🤝 **SCRUM — Slot {slot} (auto):** "
            f"{fe(rem_a[0].get('army',''))} **{rem_a[0]['player_username']}** vs "
            f"{fe(rem_b[0].get('army',''))} **{rem_b[0]['player_username']}**")


async def run_ritual_35(bot, guild: discord.Guild, tr_id: str):
    """Full ritual for Teams 3s and 5s."""
    tr    = db_get_team_round(tr_id)
    event = db_get_event(tr["event_id"])
    fmt   = event.get("format", "teams_5")
    for phase in range(1, FMT.phase_count(fmt) + 1):
        ok = await run_pairing_phase(bot, guild, tr_id, phase)
        if not ok:
            return
        pairings    = db_get_team_pairings(tr_id)
        slot_offset = (phase - 1) * 2
        slots       = [slot_offset + 1, slot_offset + 2]
        if fmt == FMT.TEAMS_3 and phase == 1:
            slots.append(3)
        await run_layout_mission_phase(bot, guild, tr_id, slots)

    if fmt == FMT.TEAMS_5:
        await _finalise_5s_scrum(bot, guild, tr_id, slot=5)
        await run_layout_mission_phase(bot, guild, tr_id, [5])

    db_update_pairing_state(tr_id, {"current_step": PS.COMPLETE})
    db_update_team_round(tr_id, {"state": TRS.PLAYING})
    await _update_dashboard(bot, tr_id, guild)

    tr     = db_get_team_round(tr_id)
    thread = guild.get_thread(int(tr["pairing_thread_id"])) if tr.get("pairing_thread_id") else None
    if thread:
        await thread.send(embed=discord.Embed(
            title="✅ Pairing Ritual Complete!",
            description="All pairings confirmed. Good luck to both teams! ⚔️",
            color=COLOUR_GOLD,
        ))


# ── Roll-off view ─────────────────────────────────────────────────────────────

class RollOffView(ui.View):
    def __init__(self, tr_id: str, cap_a_id: str, cap_b_id: str,
                  team_a_name: str, team_b_name: str, bot_ref):
        super().__init__(timeout=600)
        self.tr_id       = tr_id
        self.cap_a_id    = cap_a_id
        self.cap_b_id    = cap_b_id
        self.team_a_name = team_a_name
        self.team_b_name = team_b_name
        self._bot        = bot_ref
        self.rolls       = {}
        self._lock       = asyncio.Lock()

    @ui.button(label="🎲 Roll D6", style=discord.ButtonStyle.primary)
    async def roll(self, interaction: discord.Interaction, button: ui.Button):
        uid = str(interaction.user.id)
        if uid not in (self.cap_a_id, self.cap_b_id):
            await interaction.response.send_message("❌ Only the two captains roll.", ephemeral=True); return
        async with self._lock:
            if uid in self.rolls:
                await interaction.response.send_message(
                    f"ℹ️ You already rolled **{self.rolls[uid]}**. Waiting for opponent...", ephemeral=True); return
            result = _random.randint(1, 6)
            self.rolls[uid] = result
        if len(self.rolls) < 2:
            await interaction.response.send_message(
                f"🎲 You rolled **{result}**. Waiting for the other captain...", ephemeral=True); return

        roll_a = self.rolls.get(self.cap_a_id, 0)
        roll_b = self.rolls.get(self.cap_b_id, 0)
        tie_note = ""
        while roll_a == roll_b:
            roll_a, roll_b = _random.randint(1, 6), _random.randint(1, 6)
            tie_note = "*Tie — rerolled.*\n"

        layout_pick = "team_a" if roll_a > roll_b else "team_b"
        winner_name = self.team_a_name if layout_pick == "team_a" else self.team_b_name
        db_update_team_round(self.tr_id, {"layout_picker": layout_pick})
        db_update_pairing_state(self.tr_id, {"current_step": PS.AWAIT_DEFENDERS})
        self.stop()

        await interaction.response.send_message(
            f"{tie_note}"
            f"🎲 **Roll-off result!**\n"
            f"<@{self.cap_a_id}>: **{roll_a}**  ·  <@{self.cap_b_id}>: **{roll_b}**\n"
            f"🏆 **{winner_name}** wins and picks layouts!"
        )

        tr    = db_get_team_round(self.tr_id)
        event = db_get_event(tr["event_id"])
        fmt   = event.get("format", "teams_5")
        if fmt in (FMT.TEAMS_3, FMT.TEAMS_5):
            asyncio.create_task(run_ritual_35(self._bot, interaction.guild, self.tr_id))
        elif fmt == FMT.TEAMS_8:
            asyncio.create_task(run_ritual_8(self._bot, interaction.guild, self.tr_id))


# ── /round begin-ritual ───────────────────────────────────────────────────────

@round_grp.command(name="begin-ritual", description="[TO] Start the pairing ritual for team matchups this round")
@app_commands.describe(event_id="The event")
@app_commands.autocomplete(event_id=ac_active_events)
async def round_begin_ritual(interaction: discord.Interaction, event_id: str):
    if not is_to(interaction):
        await interaction.response.send_message("❌ Crew only.", ephemeral=True); return
    await interaction.response.defer(ephemeral=True)

    event = db_get_event(event_id)
    if not event:
        await interaction.followup.send("❌ Event not found.", ephemeral=True); return
    fmt = event.get("format", "singles")
    if fmt not in (FMT.TEAMS_3, FMT.TEAMS_5, FMT.TEAMS_8):
        await interaction.followup.send("❌ Ritual is only for Teams 3s/5s/8s events.", ephemeral=True); return

    rnd = db_get_current_round(event_id)
    if not rnd:
        await interaction.followup.send("❌ No active round.", ephemeral=True); return

    team_rounds = db_get_team_rounds(rnd["round_id"])
    pending = [tr for tr in team_rounds if tr["state"] == TRS.PAIRING and tr.get("team_b_id")]
    if not pending:
        await interaction.followup.send("❌ No pending team matchups found.", ephemeral=True); return

    pr_thread = await ensure_pairing_room_thread(bot, event_id, interaction.guild, event["name"])
    started = 0

    for tr in pending:
        team_a = db_get_team(tr["team_a_id"])
        team_b = db_get_team(tr["team_b_id"])
        db_create_pairing_state(tr["team_round_id"])

        if pr_thread:
            db_update_team_round(tr["team_round_id"], {"pairing_thread_id": str(pr_thread.id)})
            view = RollOffView(
                tr_id=tr["team_round_id"],
                cap_a_id=team_a["captain_id"],
                cap_b_id=team_b["captain_id"],
                team_a_name=team_a["team_name"],
                team_b_name=team_b["team_name"],
                bot_ref=bot,
            )
            await _update_dashboard(bot, tr["team_round_id"], interaction.guild)
            await pr_thread.send(
                f"⚔️ **{team_a['team_name']}**  vs  **{team_b['team_name']}**\n"
                f"Captains: <@{team_a['captain_id']}> and <@{team_b['captain_id']}> — roll to determine layout picker:",
                view=view,
            )
        started += 1

    await interaction.followup.send(
        f"✅ Pairing ritual started for {started} matchup(s).\n"
        f"Pairing Room: {pr_thread.mention if pr_thread else '#event-noticeboard'}",
        ephemeral=True,
    )


# ── /roll ─────────────────────────────────────────────────────────────────────
# Registered in main.py via tree.add_command(ritual.roll_dice)

@app_commands.command(name="roll", description="Roll a D6")
async def roll_dice(interaction: discord.Interaction):
    result = _random.randint(1, 6)
    await interaction.response.send_message(
        f"🎲 {interaction.user.display_name} rolled a **{result}**!"
    )
