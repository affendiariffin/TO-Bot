# FND TTS Tournament Bot — Module Split

Original `bot.py` (5,231 lines) split into focused modules, each
manageable for editing with Claude without burning large token budgets.

---

## Module Map

| File | Lines | What lives here |
|---|---|---|
| `config.py` | ~190 | Env vars, all Warhammer data (factions, detachments, missions, room colours), `fe()`, `ts()`, colour helpers |
| `state.py` | ~195 | State enums (ES, FMT, TS, TRS, PS, RS, RndS, GS, JCS), `is_to()`, in-memory `judge_roster`, `thread_registry` |
| `database.py` | ~870 | `get_conn()`, `init_db()`, all `db_*` and `scorebot_*` functions |
| `threads.py` | ~435 | Discord thread helpers, Swiss pairing algorithm, room assignment, scoring formulas, team standings DB calls |
| `embeds.py` | ~385 | All `build_*` embed functions |
| `views.py` | ~710 | All `ui.View` and `ui.Modal` classes, `_confirm_game`, `_auto_confirm_after_24h` |
| `services.py` | ~155 | `refresh_spectator_dashboard`, `_refresh_judge_queue`, `log_immediate`, all `ac_*` autocomplete helpers |
| `commands_event.py` | ~290 | `/event` group commands, `/reg` group commands |
| `commands_round.py` | ~325 | `/round` singles commands, `/result` commands, `/event-finish`, `/standings`, `/my-list` |
| `commands_round_teams.py` | ~580 | `/round pair-teams`, `/result-team submit`, `/team-standings`, `TeamScoreModal` |
| `commands_teams.py` | ~625 | `/team` group commands, `TeamListSubmitModal`, captains/pairing-room thread helpers |
| `ritual.py` | ~750 | Pairing ritual state machine, all ritual Views, `run_ritual_35`, `RollOffView`, `/round begin-ritual`, `/roll` |
| `main.py` | ~110 | Bot setup, task loops, `on_ready`, `on_error`, `bot.run()` |

---

## Dependency Order

```
config.py
  └─ state.py
       └─ database.py
            ├─ threads.py
            │    ├─ embeds.py
            │    │    └─ views.py
            │    │         └─ services.py
            │    │              ├─ commands_event.py
            │    │              ├─ commands_round.py
            │    │              ├─ commands_round_teams.py
            │    │              ├─ commands_teams.py
            │    │              └─ ritual.py
            └─────────────────────────────── main.py
```

---

## Completing the Bot

When continuing work with Claude, paste **only the relevant module**.
Typical task → module to load:

| Task | Load this file |
|---|---|
| Add/fix a slash command | `commands_event.py`, `commands_round.py`, etc. |
| Change embed appearance | `embeds.py` |
| Fix a button/modal | `views.py` |
| Change DB schema or query | `database.py` |
| Fix Swiss pairing / scoring | `threads.py` |
| Ritual state machine | `ritual.py` |
| Background tasks / bot startup | `main.py` |

---

## Known TODOs (from original bot comments)

- `ritual.py`: Teams 8s ritual is stubbed out — `run_ritual_35` has a comment
  `# Teams 8s: handled in Chunk 5` which was never implemented.
- `roll_dice` command (bottom of `ritual.py`) is missing its `@tree.command` decorator.
- `main.py` import wiring needs verification once individual modules are completed —
  group variable names (`event_grp`, `reg_grp`, etc.) must match what each
  command module exports.
