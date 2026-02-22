"""
ritual_8s.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Teams 8s NTL pairing ritual — full 3-phase implementation.

NTL 8-Player Pairing Process (Chapter Approved 10th Ed):
──────────────────────────────────────────────────────────
8 layout/mission slots. Layout duplicates (1 and 8 appear twice):
  1(C/K), 1(H/N), 2(C/P), 4(A/I), 6(M/I), 7(B/E), 8(C/H), 8(G/O)

Layout pickers per slot (winner of roll-off = Team A):
  Team A picks layout: slots 1, 4, 5, 7
  Team B picks layout: slots 2, 3, 6
  Slot 8 (SCRUM): no layout pick — Team B picks mission only

Phase structure:
  Phase 1 → steps 1-5  → pairings 1 & 2
  Phase 2 → steps 6-8  → pairings 3 & 4
  Phase 3 → steps 9-11 → pairings 5, 6, 7 (refused) & 8 (scrum)

Each phase: Defender reveal → Attacker reveal → Attacker choice reveal
Phase 3 special:
  • The 2 players NOT put forward as attackers go straight to Pairing 8 (SCRUM)
  • The 2 attackers NOT chosen face each other as Pairing 7
  • Pairing 7: Team A picks layout + mission
  • Pairing 8: Team B picks mission only (layout pre-assigned, e.g. Layout 8)

Add to ritual.py by inserting this file's contents before run_ritual_35,
then updating the RollOffView dispatch:

    if fmt == FMT.TEAMS_8:
        asyncio.create_task(run_ritual_8(bot, interaction.guild, self.tr_id))

──────────────────────────────────────────────────────────
"""
import discord
from discord import ui
import asyncio

from config import (COLOUR_GOLD, COLOUR_AMBER, fe)
from state import FMT
from database import (
    db_get_event, db_get_team, db_get_team_members,
    db_get_team_round, db_update_team_round,
    db_get_pairing_state, db_update_pairing_state,
    db_create_team_pairing, db_get_team_pairings, db_update_team_pairing,
    db_get_mission,
)


# ── 8s Layout table ─────────────────────────────────────────────────────────
# All 8 layout slots for an 8-player event, in order.
# Slots 1 and 8 each appear twice (different mission pairs).
# When a captain picks Layout 1 or Layout 8 first, they also own the missions
# for THAT instance (the second instance's missions go to whoever gets slot 8/1 next).
LAYOUTS_8S = [
    {"slot": 1, "layout": "1",  "missions": ["C", "K"]},   # Tipping Point: Linchpin / S&D: Scorched Earth
    {"slot": 2, "layout": "1",  "missions": ["H", "N"]},   # H&A: Supply Drop / Crucible: Hidden Supplies
    {"slot": 3, "layout": "2",  "missions": ["C", "P"]},   # Tipping Point: Linchpin / Crucible: Scorched Earth
    {"slot": 4, "layout": "4",  "missions": ["A", "I"]},   # Tipping Point: Take and Hold / S&D: Hidden Supplies
    {"slot": 5, "layout": "6",  "missions": ["M", "I"]},   # Crucible: Purge the Foe / S&D: Hidden Supplies
    {"slot": 6, "layout": "7",  "missions": ["B", "E"]},   # Tipping Point: Supply Drop / H&A: Take and Hold
    {"slot": 7, "layout": "8",  "missions": ["C", "H"]},   # Tipping Point: Linchpin / H&A: Supply Drop
    {"slot": 8, "layout": "8",  "missions": ["G", "O"]},   # H&A: Purge the Foe / Crucible: Terraform
]

# Which team picks layout (scrum slot 8 = no layout pick, team_b mission only)
# Based on PDF: Team A picks layouts for 1,4,5,7 — Team B picks 2,3,6
# Slot 8 is the scrum: layout is the second "8", no layout choice made, team_b picks mission
SLOT_LAYOUT_PICKER = {
    1: "team_a",
    2: "team_b",
    3: "team_b",
    4: "team_a",
    5: "team_a",
    6: "team_b",
    7: "team_a",
    8: None,        # SCRUM — no layout pick
}
SLOT_MISSION_PICKER = {
    1: "team_b",
    2: "team_a",
    3: "team_a",
    4: "team_b",
    5: "team_b",
    6: "team_a",
    7: "team_b",
    8: "team_b",    # SCRUM — team_b picks mission only
}


# ── SelectMission8sView ──────────────────────────────────────────────────────
# For slots with a fixed 2-mission pair (not free-choice from layout).

class SelectMission8sView(ui.View):
    """Mission selection restricted to the 2 valid missions for a given 8s slot."""
    def __init__(self, tr_id: str, captain_id: str, slot: int, valid_missions: list):
        super().__init__(timeout=300)
        self.tr_id      = tr_id
        self.captain_id = captain_id
        self.slot       = slot
        options = []
        for code in valid_missions:
            m = db_get_mission(code)
            options.append(discord.SelectOption(
                label=f"{code}: {m.get('name', code)}",
                value=code,
                description=m.get("deployment", "")[:50],
            ))
        sel = ui.Select(
            placeholder=f"Choose mission for Slot {slot}...",
            options=options,
            min_values=1, max_values=1,
        )
        sel.callback = self._on_select
        self.add_item(sel)

    async def _on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.captain_id:
            await interaction.response.send_message(
                "❌ Only the relevant captain can pick.", ephemeral=True)
            return
        chosen   = interaction.data["values"][0]
        pairings = db_get_team_pairings(self.tr_id)
        pairing  = next(
            (p for p in pairings if p["pairing_slot"] == self.slot and not p.get("mission_code")),
            None,
        )
        if pairing:
            db_update_team_pairing(pairing["pairing_id"], {"mission_code": chosen})
        self.stop()
        await interaction.response.edit_message(
            content=f"🎯 **Mission {chosen} selected for Slot {self.slot}.**", view=None)


# ── _run_8s_pairing_phase ────────────────────────────────────────────────────

async def _run_8s_pairing_phase(
    bot, guild: discord.Guild, tr_id: str, phase: int,
    team_a: dict, team_b: dict,
    ma_all: list, mb_all: list,
    thread,
) -> bool:
    """
    Run one defender/attacker/choice phase for Teams 8s.

    Returns True on success, False on timeout.
    Phase 3 has special scrum logic (handled in caller).
    """
    cap_a = team_a["captain_id"]
    cap_b = team_b["captain_id"]
    mem_a = guild.get_member(int(cap_a))
    mem_b = guild.get_member(int(cap_b))

    # Reset per-phase state
    db_update_pairing_state(tr_id, {
        "current_phase": phase,
        "current_step":  PS.AWAIT_DEFENDERS,
        "defender_a":    None, "defender_b":   None,
        "attackers_a":   None, "attackers_b":  None,
        "choice_a":      None, "choice_b":     None,
    })
    await _update_dashboard(bot, tr_id, guild)

    if thread:
        await _post_ritual_update(
            thread,
            f"🛡️ **Phase {phase}/3 — Both captains selecting their Defender (in secret)...**",
        )

    # ── Step: Defenders ───────────────────────────────────────────────────────
    pairings  = db_get_team_pairings(tr_id)
    elig_a    = _get_unpaired(ma_all, pairings, "a")
    elig_b    = _get_unpaired(mb_all, pairings, "b")

    if mem_a:
        try:
            await mem_a.send(
                f"🛡️ **Phase {phase}/3 — Choose your Defender** ({team_a['team_name']}):",
                view=SelectDefenderView(tr_id, cap_a, elig_a, "a"),
            )
        except Exception as e:
            print(f"⚠️ DM cap_a defender: {e}")
    if mem_b:
        try:
            await mem_b.send(
                f"🛡️ **Phase {phase}/3 — Choose your Defender** ({team_b['team_name']}):",
                view=SelectDefenderView(tr_id, cap_b, elig_b, "b"),
            )
        except Exception as e:
            print(f"⚠️ DM cap_b defender: {e}")

    if not await _wait_for_both(bot, tr_id, "defender_a", "defender_b"):
        if thread:
            await thread.send("⚠️ Defender selection timed out. TO intervention required.")
        return False

    state    = db_get_pairing_state(tr_id)
    def_a_id = state["defender_a"]
    def_b_id = state["defender_b"]
    def_a    = next((m for m in ma_all if m["player_id"] == def_a_id), {})
    def_b    = next((m for m in mb_all if m["player_id"] == def_b_id), {})

    await _update_dashboard(bot, tr_id, guild)
    if thread:
        await _post_ritual_update(
            thread,
            f"🛡️ **Defenders revealed!**\n"
            f"{team_a['team_name']}: {fe(def_a.get('army',''))} **{def_a.get('player_username','?')}**"
            f"  |  "
            f"{team_b['team_name']}: {fe(def_b.get('army',''))} **{def_b.get('player_username','?')}**",
        )

    # ── Step: Attackers ───────────────────────────────────────────────────────
    db_update_pairing_state(tr_id, {"current_step": PS.AWAIT_ATTACKERS})

    # Refresh eligibles excluding just-selected defenders
    pairings   = db_get_team_pairings(tr_id)
    elig_att_a = [m for m in elig_a if m["player_id"] != def_a_id]
    elig_att_b = [m for m in elig_b if m["player_id"] != def_b_id]

    # In Phase 3 the remaining pool may be exactly 3 per side (2 attackers + 1 scrum auto).
    # We still ask for 2 attackers; the 1 leftover becomes the scrum.
    cnt_a = min(2, len(elig_att_a))
    cnt_b = min(2, len(elig_att_b))

    if mem_a:
        try:
            await mem_a.send(
                f"⚔️ **Phase {phase}/3 — Choose {cnt_a} Attacker(s):**\n"
                f"*(Your remaining player will go to the SCRUM if this is Phase 3)*",
                view=SelectAttackersView(tr_id, cap_a, elig_att_a, "a", cnt_a),
            )
        except Exception as e:
            print(f"⚠️ DM cap_a attackers: {e}")
    if mem_b:
        try:
            await mem_b.send(
                f"⚔️ **Phase {phase}/3 — Choose {cnt_b} Attacker(s):**\n"
                f"*(Your remaining player will go to the SCRUM if this is Phase 3)*",
                view=SelectAttackersView(tr_id, cap_b, elig_att_b, "b", cnt_b),
            )
        except Exception as e:
            print(f"⚠️ DM cap_b attackers: {e}")

    if not await _wait_for_both(bot, tr_id, "attackers_a", "attackers_b"):
        if thread:
            await thread.send("⚠️ Attacker selection timed out. TO intervention required.")
        return False

    state     = db_get_pairing_state(tr_id)
    atts_a    = state["attackers_a"] or []
    atts_b    = state["attackers_b"] or []
    att_a_members = [m for m in ma_all if m["player_id"] in atts_a]
    att_b_members = [m for m in mb_all if m["player_id"] in atts_b]

    await _update_dashboard(bot, tr_id, guild)
    if thread:
        await _post_ritual_update(
            thread,
            f"⚔️ **Attackers revealed!**\n"
            f"{team_a['team_name']}: " +
            ", ".join(f"{fe(m.get('army',''))} **{m['player_username']}**" for m in att_a_members) +
            f"\n{team_b['team_name']}: " +
            ", ".join(f"{fe(m.get('army',''))} **{m['player_username']}**" for m in att_b_members),
        )

    # ── Step: Choices ─────────────────────────────────────────────────────────
    db_update_pairing_state(tr_id, {"current_step": PS.AWAIT_CHOICE})

    if mem_a:
        try:
            await mem_a.send(
                f"🎯 **Phase {phase}/3 — Choose which attacker faces your defender "
                f"({def_a.get('player_username','?')}):**",
                view=ChooseAttackerView(tr_id, cap_a, "a", att_b_members),
            )
        except Exception as e:
            print(f"⚠️ DM cap_a choice: {e}")
    if mem_b:
        try:
            await mem_b.send(
                f"🎯 **Phase {phase}/3 — Choose which attacker faces your defender "
                f"({def_b.get('player_username','?')}):**",
                view=ChooseAttackerView(tr_id, cap_b, "b", att_a_members),
            )
        except Exception as e:
            print(f"⚠️ DM cap_b choice: {e}")

    if not await _wait_for_both(bot, tr_id, "choice_a", "choice_b"):
        if thread:
            await thread.send("⚠️ Choice selection timed out. TO intervention required.")
        return False

    state    = db_get_pairing_state(tr_id)
    choice_a = state["choice_a"]   # attacker from team_b sent against def_a
    choice_b = state["choice_b"]   # attacker from team_a sent against def_b

    refused_b = next((p for p in atts_b if p != choice_a), None)
    refused_a = next((p for p in atts_a if p != choice_b), None)

    # ── Write pairings to DB ──────────────────────────────────────────────────
    existing  = db_get_team_pairings(tr_id)
    confirmed = len([p for p in existing if p.get("attacker_player_id")])
    slot_1    = confirmed + 1    # e.g. 1, 3, 5
    slot_2    = confirmed + 2    # e.g. 2, 4, 6
    lw        = db_get_team_round(tr_id).get("layout_picker", "team_a")

    pid1 = db_create_team_pairing(tr_id, slot_1)
    db_update_team_pairing(pid1, {
        "defender_player_id":  def_a_id,
        "defender_team_id":    team_a["team_id"] if "team_id" in team_a else db_get_team_round(tr_id)["team_a_id"],
        "attacker_player_id":  choice_a,
        "attacker_team_id":    db_get_team_round(tr_id)["team_b_id"],
        "refused_player_id":   refused_b,
        "layout_picker_team":  SLOT_LAYOUT_PICKER[slot_1] or lw,
        "mission_picker_team": SLOT_MISSION_PICKER[slot_1],
        "layout_number":       int(LAYOUTS_8S[slot_1 - 1]["layout"]),
    })

    pid2 = db_create_team_pairing(tr_id, slot_2)
    db_update_team_pairing(pid2, {
        "defender_player_id":  def_b_id,
        "defender_team_id":    db_get_team_round(tr_id)["team_b_id"],
        "attacker_player_id":  choice_b,
        "attacker_team_id":    db_get_team_round(tr_id)["team_a_id"],
        "refused_player_id":   refused_a,
        "layout_picker_team":  SLOT_LAYOUT_PICKER[slot_2] or lw,
        "mission_picker_team": SLOT_MISSION_PICKER[slot_2],
        "layout_number":       int(LAYOUTS_8S[slot_2 - 1]["layout"]),
    })

    ch_a_m = next((m for m in mb_all if m["player_id"] == choice_a), {})
    ch_b_m = next((m for m in ma_all if m["player_id"] == choice_b), {})

    await _update_dashboard(bot, tr_id, guild)
    if thread:
        await _post_ritual_update(
            thread,
            f"✅ **Phase {phase}/3 pairings confirmed!**\n"
            f"Slot {slot_1}: {fe(def_a.get('army',''))} **{def_a.get('player_username','?')}** "
            f"vs {fe(ch_a_m.get('army',''))} **{ch_a_m.get('player_username','?')}**\n"
            f"Slot {slot_2}: {fe(def_b.get('army',''))} **{def_b.get('player_username','?')}** "
            f"vs {fe(ch_b_m.get('army',''))} **{ch_b_m.get('player_username','?')}**",
        )

    # Return the refused player IDs so Phase 3 caller can wire Pairing 7
    return True, refused_a, refused_b


# ── _run_8s_mission_phase ─────────────────────────────────────────────────────

async def _run_8s_mission_phase(
    bot, guild: discord.Guild, tr_id: str, slots: list,
    team_a: dict, team_b: dict, thread,
):
    """
    For each slot in `slots`, DM the appropriate captain to choose the mission
    from the 2 valid missions defined in LAYOUTS_8S.
    Layout is already pre-written into the pairing row.
    """
    cap_a = team_a["captain_id"]
    cap_b = team_b["captain_id"]

    for slot in slots:
        slot_def   = LAYOUTS_8S[slot - 1]
        valid_missions = slot_def["missions"]
        mission_team   = SLOT_MISSION_PICKER[slot]
        mission_cap_id = cap_a if mission_team == "team_a" else cap_b
        mission_team_name = team_a["team_name"] if mission_team == "team_a" else team_b["team_name"]

        db_update_pairing_state(
            tr_id,
            {"current_step": PS.AWAIT_MISSION_A if mission_team == "team_a" else PS.AWAIT_MISSION_B},
        )
        await _update_dashboard(bot, tr_id, guild)

        if thread:
            await _post_ritual_update(
                thread,
                f"🎯 **Slot {slot} (Layout {slot_def['layout']}): "
                f"{mission_team_name}** choosing mission from: {' / '.join(valid_missions)}...",
            )

        mission_member = guild.get_member(int(mission_cap_id))
        if mission_member:
            try:
                await mission_member.send(
                    f"🎯 **Slot {slot} — Choose mission for Layout {slot_def['layout']}:**\n"
                    f"Options: {', '.join(valid_missions)}",
                    view=SelectMission8sView(tr_id, mission_cap_id, slot, valid_missions),
                )
            except Exception as e:
                print(f"⚠️ DM mission slot {slot}: {e}")

        # Poll until mission is set
        for _ in range(100):
            await asyncio.sleep(3)
            updated = db_get_team_pairings(tr_id)
            if next((p for p in updated if p["pairing_slot"] == slot and p.get("mission_code")), None):
                break

        await _update_dashboard(bot, tr_id, guild)


# ── run_ritual_8 — MAIN ENTRY POINT ──────────────────────────────────────────

async def run_ritual_8(bot, guild: discord.Guild, tr_id: str):
    """
    Full NTL 3-phase pairing ritual for Teams 8s.

    Slot assignments:
      Phase 1 → Slots 1 & 2    (steps 1-5)
      Phase 2 → Slots 3 & 4    (steps 6-8)
      Phase 3 → Slots 5 & 6    (steps 9-11)
                Slot 7 = refused attacker auto-pairing (from Phase 3)
                Slot 8 = SCRUM (last two unplaced players)

    Layout is pre-assigned from LAYOUTS_8S. Mission is chosen by the
    designated captain immediately after each pairing phase.
    Slot 8 (SCRUM): Team B picks mission only.
    """
    tr     = db_get_team_round(tr_id)
    event  = db_get_event(tr["event_id"])
    team_a = db_get_team(tr["team_a_id"])
    team_b = db_get_team(tr["team_b_id"])
    ma_all = db_get_team_members(tr["team_a_id"])
    mb_all = db_get_team_members(tr["team_b_id"])
    thread = guild.get_thread(int(tr["pairing_thread_id"])) if tr.get("pairing_thread_id") else None

    if thread:
        await thread.send(embed=discord.Embed(
            title="⚔️ Teams 8s Pairing Ritual — Starting!",
            description=(
                "**3 phases** of defender/attacker selection.\n"
                "Captains will receive DMs for each selection.\n\n"
                "📋 **Layout/Mission table:**\n"
                "```\n"
                "Slot  Layout  Mission options\n"
                "─────────────────────────────\n"
                + "\n".join(
                    f"  {s['slot']}     {s['layout']}       {' / '.join(s['missions'])}"
                    for s in LAYOUTS_8S
                ) +
                "\n```\n"
                "Slot 8 is the **SCRUM** — no layout choice, Team B picks mission."
            ),
            color=COLOUR_AMBER,
        ))

    # ──────────────────────────────────────────────────────────────────────────
    # PHASE 1 → Slots 1 & 2
    # ──────────────────────────────────────────────────────────────────────────
    result = await _run_8s_pairing_phase(
        bot, guild, tr_id, phase=1,
        team_a=team_a, team_b=team_b,
        ma_all=ma_all, mb_all=mb_all,
        thread=thread,
    )
    if result is False:
        return
    _, _, _ = result   # refused_a, refused_b not needed for phase 1

    await _run_8s_mission_phase(bot, guild, tr_id, slots=[1, 2],
                                 team_a=team_a, team_b=team_b, thread=thread)

    # ──────────────────────────────────────────────────────────────────────────
    # PHASE 2 → Slots 3 & 4
    # ──────────────────────────────────────────────────────────────────────────
    result = await _run_8s_pairing_phase(
        bot, guild, tr_id, phase=2,
        team_a=team_a, team_b=team_b,
        ma_all=ma_all, mb_all=mb_all,
        thread=thread,
    )
    if result is False:
        return

    await _run_8s_mission_phase(bot, guild, tr_id, slots=[3, 4],
                                 team_a=team_a, team_b=team_b, thread=thread)

    # ──────────────────────────────────────────────────────────────────────────
    # PHASE 3 → Slots 5, 6, 7 (refused), 8 (scrum)
    # ──────────────────────────────────────────────────────────────────────────
    result = await _run_8s_pairing_phase(
        bot, guild, tr_id, phase=3,
        team_a=team_a, team_b=team_b,
        ma_all=ma_all, mb_all=mb_all,
        thread=thread,
    )
    if result is False:
        return
    _, refused_a, refused_b = result

    # Missions for slots 5 & 6
    await _run_8s_mission_phase(bot, guild, tr_id, slots=[5, 6],
                                 team_a=team_a, team_b=team_b, thread=thread)

    # ── Slot 7: Refused attackers face each other ─────────────────────────────
    # The attacker each team did NOT choose automatically becomes Pairing 7.
    # Team A picks layout + mission for Slot 7.
    ref_a_m = next((m for m in ma_all if m["player_id"] == refused_a), {}) if refused_a else {}
    ref_b_m = next((m for m in mb_all if m["player_id"] == refused_b), {}) if refused_b else {}

    if refused_a and refused_b:
        pid7 = db_create_team_pairing(tr_id, 7)
        db_update_team_pairing(pid7, {
            "defender_player_id":  refused_a,
            "defender_team_id":    tr["team_a_id"],
            "attacker_player_id":  refused_b,
            "attacker_team_id":    tr["team_b_id"],
            "layout_picker_team":  SLOT_LAYOUT_PICKER[7],   # team_a
            "mission_picker_team": SLOT_MISSION_PICKER[7],  # team_b
            "layout_number":       int(LAYOUTS_8S[6]["layout"]),  # Layout 8
        })
        if thread:
            await _post_ritual_update(
                thread,
                f"🤝 **Slot 7 (Refused Attackers — Auto):**\n"
                f"{fe(ref_a_m.get('army',''))} **{ref_a_m.get('player_username','?')}** "
                f"vs {fe(ref_b_m.get('army',''))} **{ref_b_m.get('player_username','?')}**",
            )
        await _run_8s_mission_phase(bot, guild, tr_id, slots=[7],
                                     team_a=team_a, team_b=team_b, thread=thread)

    # ── Slot 8: SCRUM — last remaining players ────────────────────────────────
    # The players never put forward as attackers (held back) are the SCRUM pair.
    # Layout is pre-assigned (second Layout 8, missions G/O).
    # Team B picks mission only — no layout choice.
    pairings = db_get_team_pairings(tr_id)
    rem_a    = _get_unpaired(ma_all, pairings, "a")
    rem_b    = _get_unpaired(mb_all, pairings, "b")

    if rem_a and rem_b:
        scrum_a = rem_a[0]
        scrum_b = rem_b[0]
        pid8 = db_create_team_pairing(tr_id, 8)
        db_update_team_pairing(pid8, {
            "defender_player_id":  scrum_a["player_id"],
            "defender_team_id":    tr["team_a_id"],
            "attacker_player_id":  scrum_b["player_id"],
            "attacker_team_id":    tr["team_b_id"],
            "layout_picker_team":  None,               # SCRUM: no layout pick
            "mission_picker_team": SLOT_MISSION_PICKER[8],  # team_b
            "layout_number":       int(LAYOUTS_8S[7]["layout"]),  # Layout 8 (second instance)
        })
        if thread:
            await _post_ritual_update(
                thread,
                f"🎲 **Slot 8 — SCRUM (Auto):**\n"
                f"{fe(scrum_a.get('army',''))} **{scrum_a['player_username']}** "
                f"vs {fe(scrum_b.get('army',''))} **{scrum_b['player_username']}**\n"
                f"*(Layout 8 pre-assigned — {team_b['team_name']} picks mission)*",
            )
        await _run_8s_mission_phase(bot, guild, tr_id, slots=[8],
                                     team_a=team_a, team_b=team_b, thread=thread)

    # ── Finalise ──────────────────────────────────────────────────────────────
    db_update_pairing_state(tr_id, {"current_step": PS.COMPLETE})
    db_update_team_round(tr_id, {"state": TRS.PLAYING})
    await _update_dashboard(bot, tr_id, guild)

    if thread:
        # Post final summary table
        final_pairings = db_get_team_pairings(tr_id)
        lines = ["```", f"{'Slot':<5} {'Layout':<7} {'Mission':<8} {team_a['team_name'][:14]:<16} {team_b['team_name'][:14]:<16}", "─" * 60]
        for p in sorted(final_pairings, key=lambda x: x["pairing_slot"]):
            def pname(pid, members):
                m = next((x for x in members if x["player_id"] == pid), None)
                return f"{m['player_username'][:12]} ({fe(m.get('army','?'))})" if m else "?"
            slot_label = str(p["pairing_slot"])
            if p["pairing_slot"] == 7:
                slot_label += " ⚡"
            elif p["pairing_slot"] == 8:
                slot_label += " 🎲"
            lines.append(
                f"{slot_label:<5} "
                f"{str(p.get('layout_number') or '—'):<7} "
                f"{str(p.get('mission_code') or '—'):<8} "
                f"{pname(p.get('defender_player_id'), ma_all):<16} "
                f"{pname(p.get('attacker_player_id'), mb_all):<16}"
            )
        lines.append("```")
        lines.append("⚡ = Refused-attacker auto-pairing  |  🎲 = SCRUM")
        await thread.send(embed=discord.Embed(
            title="✅ Teams 8s Pairing Ritual Complete!",
            description="\n".join(lines) + "\n\nGood luck to both teams! ⚔️",
            color=COLOUR_GOLD,
        ))
