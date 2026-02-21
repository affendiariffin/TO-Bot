"""
FACTIONS & DETACHMENTS — move armies/detachments from code to shared DB table
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SITUATION
─────────
Both bots currently embed faction/detachment data in code:
  Tournament bot: config.py   → FACTION_EMOJIS, FACTION_COLOURS, WARHAMMER_DETACHMENTS
  LFG bot:        constants.py → FACTION_EMOJIS, ARMY_DETACHMENTS
The lists have diverged. Neither bot can update them without a redeploy.

SOLUTION
────────
Two shared tables both bots read from the same Railway PostgreSQL DB:

  warhammer_factions   (army_name PK, emoji, colour_r/g/b, sort_order, active)
  warhammer_detachments (id SERIAL, army_name FK, detachment_name, sort_order, active)

In-memory cache (same pattern as tournament_missions):
  • Loaded once at startup via init_factions_cache()
  • Single source of truth: db_get_faction(army), db_get_factions(), db_get_detachments(army)
  • /faction reload command reloads without restart
  • LFG bot calls same functions, just imports from its own database.py

DETACHMENT DATA SOURCE
──────────────────────
constants.py (LFG bot) has the most complete/current lists — used as the seed.
Tournament bot's config.py WARHAMMER_DETACHMENTS had fewer entries per army.

FILES TO CHANGE — Tournament Bot
─────────────────────────────────
  database.py       — add tables, seed, cache, db_get_faction/detachments
  config.py         — DELETE FACTION_EMOJIS, FACTION_COLOURS, WARHAMMER_DETACHMENTS,
                      WARHAMMER_ARMIES; fe() and faction_colour() become thin wrappers
                      around db cache
  services.py       — ac_armies, ac_detachments read from cache
  main.py           — call init_factions_cache() at startup

FILES TO CHANGE — LFG Bot
──────────────────────────
  database.py (LFG) — add same tables/seed/cache functions
  constants.py      — DELETE FACTION_EMOJIS, ARMY_DETACHMENTS, COMMON_DETACHMENTS
  autocomplete.py   — faction_autocomplete, detachment_autocomplete read from cache
  bot.py (LFG)      — add init_factions_cache() call at startup
"""


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 1 — SQL (run once, or via init_db)
# Works for both bots — they share the same Railway PostgreSQL instance.
# ═════════════════════════════════════════════════════════════════════════════

SEED_SQL = """
-- ── Tables ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS warhammer_factions (
    army_name   TEXT PRIMARY KEY,
    emoji       TEXT NOT NULL DEFAULT '⚔️',
    colour_r    SMALLINT NOT NULL DEFAULT 100,
    colour_g    SMALLINT NOT NULL DEFAULT 100,
    colour_b    SMALLINT NOT NULL DEFAULT 100,
    sort_order  INT  NOT NULL DEFAULT 0,
    active      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS warhammer_detachments (
    detachment_id   SERIAL PRIMARY KEY,
    army_name       TEXT NOT NULL REFERENCES warhammer_factions(army_name) ON DELETE CASCADE,
    detachment_name TEXT NOT NULL,
    sort_order      INT  NOT NULL DEFAULT 0,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE(army_name, detachment_name)
);

-- ── Factions seed ──────────────────────────────────────────────────────────
-- colour_r/g/b taken from Tournament bot config.py FACTION_COLOURS.
-- emoji taken from Tournament bot config.py FACTION_EMOJIS (authoritative).

INSERT INTO warhammer_factions (army_name, emoji, colour_r, colour_g, colour_b, sort_order) VALUES
    ('Adepta Sororitas',    '⛪',  180,  60,  60,  1),
    ('Adeptus Custodes',    '🛡️', 212, 175,  55,  2),
    ('Adeptus Mechanicus',  '⚙️', 160,  30,  30,  3),
    ('Aeldari',             '💎', 100, 200, 180,  4),
    ('Astra Militarum',     '🪖', 100, 120,  60,  5),
    ('Black Templars',      '✝️',  30,  30,  30,  6),
    ('Blood Angels',        '🩸', 180,  20,  20,  7),
    ('Chaos Daemons',       '👹', 140,  40, 140,  8),
    ('Chaos Knights',       '⚡',  80,  20,  80,  9),
    ('Chaos Space Marines', '🔥', 120,  20,  20, 10),
    ('Dark Angels',         '🌑',  20,  80,  40, 11),
    ('Death Guard',         '☠️',  80, 100,  40, 12),
    ('Deathwatch',          '🔭',  20,  20,  60, 13),
    ('Drukhari',            '🗡️', 100,  20, 120, 14),
    ('Emperor''s Children', '😈', 200,  80, 160, 15),
    ('Genestealer Cults',   '🧬', 120,  60, 140, 16),
    ('Grey Knights',        '🔮', 140, 160, 180, 17),
    ('Imperial Agents',     '🕵️', 100, 100, 120, 18),
    ('Imperial Knights',    '🏰', 140, 100,  20, 19),
    ('Leagues of Votann',   '🔨', 100,  80,  40, 20),
    ('Necrons',             '💀',  40, 180, 100, 21),
    ('Orks',                '🟢',  60, 130,  40, 22),
    ('Space Marines',       '⚔️',  30,  80, 160, 23),
    ('Space Wolves',        '🐺',  60, 100, 160, 24),
    ('T''au Empire',        '🔵',  60, 160, 200, 25),
    ('Thousand Sons',       '🌀',  40,  80, 160, 26),
    ('Tyranids',            '🦎', 120, 180,  40, 27),
    ('World Eaters',        '🪓', 180,  40,  20, 28),
    ('Other',               '❓', 100, 100, 100, 99)
ON CONFLICT (army_name) DO NOTHING;

-- ── Detachments seed ───────────────────────────────────────────────────────
-- Source: constants.py (LFG bot) — most complete list available.
-- sort_order within each faction: alphabetical.

INSERT INTO warhammer_detachments (army_name, detachment_name, sort_order) VALUES
    -- Adepta Sororitas
    ('Adepta Sororitas', 'Army of Faith',                    1),
    ('Adepta Sororitas', 'Bringers of Flame',                2),
    ('Adepta Sororitas', 'Champions of Faith',               3),
    ('Adepta Sororitas', 'Hallowed Martyrs',                 4),
    ('Adepta Sororitas', 'Penitent Host',                    5),
    ('Adepta Sororitas', 'Other',                           99),
    -- Adeptus Custodes
    ('Adeptus Custodes', 'Auric Champions',                  1),
    ('Adeptus Custodes', 'Shield Host',                      2),
    ('Adeptus Custodes', 'Talons of the Emperor',            3),
    ('Adeptus Custodes', 'Other',                           99),
    -- Adeptus Mechanicus
    ('Adeptus Mechanicus', 'Cohort Cybernetica',             1),
    ('Adeptus Mechanicus', 'Data-Psalm Conclave',            2),
    ('Adeptus Mechanicus', 'Explorator Maniple',             3),
    ('Adeptus Mechanicus', 'Rad-Cohort',                     4),
    ('Adeptus Mechanicus', 'Skitarii Hunter Cohort',         5),
    ('Adeptus Mechanicus', 'Other',                         99),
    -- Aeldari
    ('Aeldari', 'Battle Host',                               1),
    ('Aeldari', 'Guardian Battlehost',                       2),
    ('Aeldari', 'Warhost',                                   3),
    ('Aeldari', 'Windrider Host',                            4),
    ('Aeldari', 'Other',                                    99),
    -- Astra Militarum
    ('Astra Militarum', 'Bridgehead Assault',                1),
    ('Astra Militarum', 'Combined Arms',                     2),
    ('Astra Militarum', 'Hammer of the Emperor',             3),
    ('Astra Militarum', 'Leman Russ Spearhead',              4),
    ('Astra Militarum', 'Mechanised Infantry',               5),
    ('Astra Militarum', 'Other',                            99),
    -- Black Templars
    ('Black Templars', 'Righteous Crusaders',                1),
    ('Black Templars', 'Templar Brethren',                   2),
    ('Black Templars', 'Other',                             99),
    -- Blood Angels
    ('Blood Angels', 'Liberator Assault Group',              1),
    ('Blood Angels', 'Sons of Sanguinius',                   2),
    ('Blood Angels', 'Other',                               99),
    -- Chaos Daemons
    ('Chaos Daemons', 'Daemonic Incursion',                  1),
    ('Chaos Daemons', 'Excess of Violence',                  2),
    ('Chaos Daemons', 'Khorne Daemons',                      3),
    ('Chaos Daemons', 'Nurgle Daemons',                      4),
    ('Chaos Daemons', 'Plague Purge',                        5),
    ('Chaos Daemons', 'Slaanesh Daemons',                    6),
    ('Chaos Daemons', 'Tzeentch Daemons',                    7),
    ('Chaos Daemons', 'Warpstorm',                           8),
    ('Chaos Daemons', 'Other',                              99),
    -- Chaos Knights
    ('Chaos Knights', 'Iconoclast Horde',                    1),
    ('Chaos Knights', 'Traitoris Lance',                     2),
    ('Chaos Knights', 'Other',                              99),
    -- Chaos Space Marines
    ('Chaos Space Marines', 'Council of Traitors',           1),
    ('Chaos Space Marines', 'Pactbound Zealots',             2),
    ('Chaos Space Marines', 'Raiders',                       3),
    ('Chaos Space Marines', 'Soulforged Pack',               4),
    ('Chaos Space Marines', 'Warband',                       5),
    ('Chaos Space Marines', 'Other',                        99),
    -- Dark Angels
    ('Dark Angels', 'Deathwing Strikeforce',                 1),
    ('Dark Angels', 'Inner Circle Task Force',               2),
    ('Dark Angels', 'Lion''s Blade Task Force',              3),
    ('Dark Angels', 'Ravenwing Attackers',                   4),
    ('Dark Angels', 'Unforgiven Task Force',                 5),
    ('Dark Angels', 'Wrath of the Rock',                     6),
    ('Dark Angels', 'Other',                                99),
    -- Death Guard
    ('Death Guard', 'Champions of Contagion',                1),
    ('Death Guard', 'Inexorable Advance',                    2),
    ('Death Guard', 'Mortarion''s Hammer',                   3),
    ('Death Guard', 'Plague Company',                        4),
    ('Death Guard', 'Virulent Vectorium',                    5),
    ('Death Guard', 'Other',                                99),
    -- Deathwatch
    ('Deathwatch', 'Black Spear Task Force',                 1),
    ('Deathwatch', 'Other',                                 99),
    -- Drukhari
    ('Drukhari', 'Court of the Archon',                      1),
    ('Drukhari', 'Realspace Raiders',                        2),
    ('Drukhari', 'Skysplinter Assault',                      3),
    ('Drukhari', 'Spectacle of Spite',                       4),
    ('Drukhari', 'Other',                                   99),
    -- Emperor's Children
    ('Emperor''s Children', 'Carnival of Excess',            1),
    ('Emperor''s Children', 'Coterie of the Conceited',      2),
    ('Emperor''s Children', 'Court of the Phoenician',       3),
    ('Emperor''s Children', 'Mercurial Host',                4),
    ('Emperor''s Children', 'Slaanesh''s Chosen',            5),
    ('Emperor''s Children', 'Other',                        99),
    -- Genestealer Cults
    ('Genestealer Cults', 'Biosanctic Broodsurge',           1),
    ('Genestealer Cults', 'Brood Brother Auxilia',           2),
    ('Genestealer Cults', 'Final Day',                       3),
    ('Genestealer Cults', 'Host of Ascension',               4),
    ('Genestealer Cults', 'Outlander Claw',                  5),
    ('Genestealer Cults', 'Xenocreed Congregation',          6),
    ('Genestealer Cults', 'Other',                          99),
    -- Grey Knights
    ('Grey Knights', 'Augurium Taskforce',                   1),
    ('Grey Knights', 'Banishers',                            2),
    ('Grey Knights', 'Brotherhood Strike',                   3),
    ('Grey Knights', 'Hallowed Conclave',                    4),
    ('Grey Knights', 'Sanctic Spearhead',                    5),
    ('Grey Knights', 'Warpbane Task Force',                  6),
    ('Grey Knights', 'Other',                               99),
    -- Imperial Agents
    ('Imperial Agents', 'Imperialis Fleet',                  1),
    ('Imperial Agents', 'Ordo Hereticus Purgation Force',    2),
    ('Imperial Agents', 'Ordo Malleus Daemon Hunters',       3),
    ('Imperial Agents', 'Ordo Xenos Alien Hunters',          4),
    ('Imperial Agents', 'Veiled Blade Elimination Force',    5),
    ('Imperial Agents', 'Other',                            99),
    -- Imperial Knights
    ('Imperial Knights', 'Gate Warden Lance',                1),
    ('Imperial Knights', 'Questoris Companions',             2),
    ('Imperial Knights', 'Questor Forgepact',                3),
    ('Imperial Knights', 'Spearhead-at-Arms',                4),
    ('Imperial Knights', 'Valourstrike Lance',               5),
    ('Imperial Knights', 'Other',                           99),
    -- Leagues of Votann
    ('Leagues of Votann', 'Hearthband',                      1),
    ('Leagues of Votann', 'Hearthfyre Arsenal',              2),
    ('Leagues of Votann', 'Needgaard Oathband',              3),
    ('Leagues of Votann', 'Other',                          99),
    -- Necrons
    ('Necrons', 'Annihilation Legion',                       1),
    ('Necrons', 'Awakened Dynasty',                          2),
    ('Necrons', 'Canoptek Court',                            3),
    ('Necrons', 'Cryptek Conclave',                          4),
    ('Necrons', 'Cursed Legion',                             5),
    ('Necrons', 'Hypercrypt Legion',                         6),
    ('Necrons', 'Obeisance Phalanx',                         7),
    ('Necrons', 'Pantheon of Woe',                           8),
    ('Necrons', 'Starshatter Arsenal',                       9),
    ('Necrons', 'Other',                                    99),
    -- Orks
    ('Orks', 'Bully Boyz',                                   1),
    ('Orks', 'Da Big Hunt',                                  2),
    ('Orks', 'Dread Mob',                                    3),
    ('Orks', 'Green Tide',                                   4),
    ('Orks', 'Kult of Speed',                                5),
    ('Orks', 'More Dakka',                                   6),
    ('Orks', 'Taktikal Brigade',                             7),
    ('Orks', 'War Horde',                                    8),
    ('Orks', 'Other',                                       99),
    -- Space Marines (consolidated — includes subfaction detachments)
    ('Space Marines', '1st Company Task Force',              1),
    ('Space Marines', 'Angelic Inheritors',                  2),
    ('Space Marines', 'Anvil Siege Force',                   3),
    ('Space Marines', 'Bastion Task Force',                  4),
    ('Space Marines', 'Black Spear Task Force',              5),
    ('Space Marines', 'Blade of Ultramar',                   6),
    ('Space Marines', 'Champions of Fenris',                 7),
    ('Space Marines', 'Company of Hunters',                  8),
    ('Space Marines', 'Emperor''s Shield',                   9),
    ('Space Marines', 'Firestorm Assault Force',            10),
    ('Space Marines', 'Forgefather''s Seekers',             11),
    ('Space Marines', 'Gladius Task Force',                 12),
    ('Space Marines', 'Godhammer Assault Force',            13),
    ('Space Marines', 'Hammer of Avernii',                  14),
    ('Space Marines', 'Inner Circle Task Force',            15),
    ('Space Marines', 'Ironstorm Spearhead',                16),
    ('Space Marines', 'Liberator Assault Group',            17),
    ('Space Marines', 'Librarius Conclave',                 18),
    ('Space Marines', 'Lion''s Blade Task Force',           19),
    ('Space Marines', 'Orbital Assault Force',              20),
    ('Space Marines', 'Rage-Cursed Onslaught',              21),
    ('Space Marines', 'Reclamation Force',                  22),
    ('Space Marines', 'Saga of the Beastslayer',            23),
    ('Space Marines', 'Saga of the Bold',                   24),
    ('Space Marines', 'Saga of the Great Wolf',             25),
    ('Space Marines', 'Saga of the Hunter',                 26),
    ('Space Marines', 'Shadowmark Talon',                   27),
    ('Space Marines', 'Spearpoint Task Force',              28),
    ('Space Marines', 'Stormlance Task Force',              29),
    ('Space Marines', 'The Angelic Host',                   30),
    ('Space Marines', 'The Lost Brethren',                  31),
    ('Space Marines', 'Unforgiven Task Force',              32),
    ('Space Marines', 'Vanguard Spearhead',                 33),
    ('Space Marines', 'Vindication Task Force',             34),
    ('Space Marines', 'Wrathful Procession',                35),
    ('Space Marines', 'Wrath of the Rock',                  36),
    ('Space Marines', 'Other',                              99),
    -- Space Wolves (kept separate in LFG bot, merged into Space Marines in Tournament bot)
    -- Adding as own faction to match Tournament bot's config.py which has Space Wolves separate
    ('Space Marines', 'Companions of Vehemence',            37),
    -- T'au Empire
    ('T''au Empire', 'Auxiliary Cadre',                      1),
    ('T''au Empire', 'Experimental Prototype Cadre',         2),
    ('T''au Empire', 'Kauyon',                               3),
    ('T''au Empire', 'Kroot Hunting Pack',                   4),
    ('T''au Empire', 'Kroot Raiding Party',                  5),
    ('T''au Empire', 'Mont''ka',                             6),
    ('T''au Empire', 'Retaliation Cadre',                    7),
    ('T''au Empire', 'Starfire Cadre',                       8),
    ('T''au Empire', 'Other',                               99),
    -- Thousand Sons
    ('Thousand Sons', 'Changehost of Deceit',                1),
    ('Thousand Sons', 'Grand Coven',                         2),
    ('Thousand Sons', 'Hexwarp Thrallband',                  3),
    ('Thousand Sons', 'Rubricae Phalanx',                    4),
    ('Thousand Sons', 'Warpforged Cabal',                    5),
    ('Thousand Sons', 'Warpmeld Pact',                       6),
    ('Thousand Sons', 'Other',                              99),
    -- Tyranids
    ('Tyranids', 'Assimilation Swarm',                       1),
    ('Tyranids', 'Crusher Stampede',                         2),
    ('Tyranids', 'Invasion Fleet',                           3),
    ('Tyranids', 'Synaptic Nexus',                           4),
    ('Tyranids', 'Unending Swarm',                           5),
    ('Tyranids', 'Vanguard Onslaught',                       6),
    ('Tyranids', 'Other',                                   99),
    -- World Eaters
    ('World Eaters', 'Berzerker Warband',                    1),
    ('World Eaters', 'Cult of Blood',                        2),
    ('World Eaters', 'Goretrack Onslaught',                  3),
    ('World Eaters', 'Other',                               99),
    -- Other
    ('Other', 'Other',                                       1)
ON CONFLICT (army_name, detachment_name) DO NOTHING;
"""


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 2 — database.py additions (Tournament Bot AND LFG Bot)
# Paste into each bot's database.py.
# The cache functions are identical — both bots read from the same tables.
# ═════════════════════════════════════════════════════════════════════════════

DATABASE_ADDITIONS = '''
# ── Factions / detachments cache ──────────────────────────────────────────────

# Cache structures populated by init_factions_cache():
#   _factions_cache = {
#       "Space Marines": {"emoji": "⚔️", "colour": (30, 80, 160), "sort_order": 23},
#       ...
#   }
#   _detachments_cache = {
#       "Space Marines": ["Gladius Task Force", "Ironstorm Spearhead", ..., "Other"],
#       ...
#   }
_factions_cache:    dict[str, dict] = {}
_detachments_cache: dict[str, list] = {}


def _create_faction_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS warhammer_factions (
                army_name   TEXT PRIMARY KEY,
                emoji       TEXT    NOT NULL DEFAULT \'⚔️\',
                colour_r    SMALLINT NOT NULL DEFAULT 100,
                colour_g    SMALLINT NOT NULL DEFAULT 100,
                colour_b    SMALLINT NOT NULL DEFAULT 100,
                sort_order  INT     NOT NULL DEFAULT 0,
                active      BOOLEAN NOT NULL DEFAULT TRUE
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS warhammer_detachments (
                detachment_id   SERIAL PRIMARY KEY,
                army_name       TEXT NOT NULL
                    REFERENCES warhammer_factions(army_name) ON DELETE CASCADE,
                detachment_name TEXT NOT NULL,
                sort_order      INT  NOT NULL DEFAULT 0,
                active          BOOLEAN NOT NULL DEFAULT TRUE,
                UNIQUE(army_name, detachment_name)
            )
        """)
        conn.commit()


def init_factions_cache():
    """
    Load all active factions and their detachments from DB into memory.
    Call once at startup. Re-call after DB edits via /faction reload.
    """
    global _factions_cache, _detachments_cache
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT army_name, emoji, colour_r, colour_g, colour_b, sort_order
                FROM warhammer_factions
                WHERE active = TRUE
                ORDER BY sort_order, army_name
            """)
            factions = cur.fetchall()
            cur.execute("""
                SELECT army_name, detachment_name
                FROM warhammer_detachments
                WHERE active = TRUE
                ORDER BY army_name, sort_order, detachment_name
            """)
            detachments = cur.fetchall()

    _factions_cache = {
        r["army_name"]: {
            "emoji":      r["emoji"],
            "colour":     (r["colour_r"], r["colour_g"], r["colour_b"]),
            "sort_order": r["sort_order"],
        }
        for r in factions
    }
    _detachments_cache = {}
    for r in detachments:
        _detachments_cache.setdefault(r["army_name"], []).append(r["detachment_name"])

    print(f"✅ Factions cache loaded ({len(_factions_cache)} factions, "
          f"{sum(len(v) for v in _detachments_cache.values())} detachments)")
    return _factions_cache, _detachments_cache


def db_get_faction(army_name: str) -> dict:
    """Return {emoji, colour, sort_order} for a faction, or {} if not found."""
    return _factions_cache.get(army_name, {})


def db_get_factions() -> dict[str, dict]:
    """Return full {army_name: {emoji, colour, sort_order}} mapping (sorted)."""
    return _factions_cache


def db_get_army_names() -> list[str]:
    """Return sorted list of army names. Replaces WARHAMMER_ARMIES."""
    return list(_factions_cache.keys())   # already sorted by sort_order from query


def db_get_detachments(army_name: str) -> list[str]:
    """
    Return detachment list for an army.
    Falls back to [\'Other\'] if army not found.
    Replaces WARHAMMER_DETACHMENTS.get(army, [\'Other\']).
    """
    return _detachments_cache.get(army_name, ["Other"])


def db_get_all_detachments_flat() -> list[str]:
    """
    Return all detachment names as a flat deduplicated list.
    Replaces COMMON_DETACHMENTS in the LFG bot.
    """
    seen = set()
    result = []
    for dets in _detachments_cache.values():
        for d in dets:
            if d not in seen:
                seen.add(d)
                result.append(d)
    return result
'''


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 3 — init_db() additions (both bots)
# Add inside init_db() after existing table creation:
# ═════════════════════════════════════════════════════════════════════════════

INIT_DB_ADDITION = '''
    # Add inside init_db(), right before conn.commit() / print statement:
    _create_faction_tables(conn)
    # Note: seeding is done via the SQL script in Section 1, not in code,
    # to avoid overwriting manual DB edits on subsequent startups.
'''


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 4 — main.py (Tournament Bot) startup call
# ═════════════════════════════════════════════════════════════════════════════

TOURNAMENT_MAIN_ADDITION = '''
# In on_ready(), after init_db() and init_missions_cache():
    from database import init_factions_cache
    init_factions_cache()
'''


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 5 — config.py (Tournament Bot) changes
# ═════════════════════════════════════════════════════════════════════════════

TOURNAMENT_CONFIG_CHANGES = '''
In config.py:

1. DELETE these entirely:
   - FACTION_EMOJIS  dict
   - FACTION_COLOURS dict
   - WARHAMMER_DETACHMENTS dict
   - WARHAMMER_ARMIES list

2. REPLACE the fe() and faction_colour() helper functions with cache-backed versions:

def fe(army: str) -> str:
    """Faction emoji shorthand. Reads from DB cache."""
    from database import db_get_faction
    f = db_get_faction(army)
    return f.get("emoji", "⚔️") if f else "⚔️"

def faction_colour(army: str) -> discord.Color:
    """Faction embed colour. Reads from DB cache."""
    from database import db_get_faction
    f = db_get_faction(army)
    if f:
        r, g, b = f["colour"]
        return discord.Color.from_rgb(r, g, b)
    return FALLBACK_COLOUR

3. Remove WARHAMMER_ARMIES and WARHAMMER_DETACHMENTS from any imports
   in services.py, commands_event.py, and anywhere else they appear.
'''


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 6 — services.py (Tournament Bot) — update ac_armies, ac_detachments
# ═════════════════════════════════════════════════════════════════════════════

TOURNAMENT_SERVICES_CHANGES = '''
# Add to imports:
from database import db_get_army_names, db_get_detachments, db_get_factions

# Replace ac_armies:
async def ac_armies(i: discord.Interaction, current: str):
    return [
        app_commands.Choice(name=f"{data[\'emoji\']} {army}", value=army)
        for army, data in db_get_factions().items()
        if current.lower() in army.lower()
    ][:25]

# Replace ac_detachments:
async def ac_detachments(i: discord.Interaction, current: str):
    army = getattr(i.namespace, "army", "") or ""
    dets = db_get_detachments(army) if army else db_get_all_detachments_flat()
    return [
        app_commands.Choice(name=d, value=d)
        for d in dets
        if current.lower() in d.lower()
    ][:25]
'''


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 7 — LFG Bot changes
# ═════════════════════════════════════════════════════════════════════════════

LFG_CHANGES = '''
━━━ constants.py ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DELETE:
  - FACTION_EMOJIS dict
  - ARMY_DETACHMENTS dict
  - COMMON_DETACHMENTS list

KEEP:
  - POINT_VALUES  (not faction data)
  - TOURNAMENT_MISSIONS  (will be removed separately when LFG bot adopts
    the tournament_missions table — out of scope here)


━━━ autocomplete.py ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Add to imports:
    from database import db_get_factions, db_get_detachments, db_get_all_detachments_flat

Replace faction_autocomplete:
    async def faction_autocomplete(interaction: discord.Interaction, current: str):
        return [
            app_commands.Choice(name=f"{data[\'emoji\']} {army}", value=army)
            for army, data in db_get_factions().items()
            if not current or current.lower() in army.lower()
        ][:25]

Replace detachment_autocomplete:
    async def detachment_autocomplete(interaction: discord.Interaction, current: str):
        army = getattr(getattr(interaction, "namespace", None), "army", None)
        if army:
            dets = db_get_detachments(army)
        else:
            dets = db_get_all_detachments_flat()
        return [
            app_commands.Choice(name=d, value=d)
            for d in dets
            if not current or current.lower() in d.lower()
        ][:25]

Remove from imports:
    FACTION_EMOJIS, ARMY_DETACHMENTS, COMMON_DETACHMENTS


━━━ bot.py / main entry point (LFG Bot) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

In on_ready (or equivalent startup):
    from database import init_factions_cache
    init_factions_cache()

Remove from config/constants imports:
    WARHAMMER_ARMIES, WARHAMMER_DETACHMENTS (if referenced in bot.py directly)
'''


# ═════════════════════════════════════════════════════════════════════════════
# SECTION 8 — /faction reload admin command (Tournament Bot)
# Add to commands_event.py or a new commands_admin.py
# ═════════════════════════════════════════════════════════════════════════════

FACTION_RELOAD_COMMAND = '''
@app_commands.command(name="faction-reload",
                      description="[Admin] Reload factions and detachments cache from database")
@app_commands.guild_only()
async def faction_reload(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        return
    from database import init_factions_cache, db_get_factions
    init_factions_cache()
    factions = db_get_factions()
    lines = [f"{data[\'emoji\']}  **{army}**" for army, data in factions.items()]
    embed = discord.Embed(
        title=f"✅  Factions Cache Reloaded  ({len(factions)} factions)",
        description="\n".join(lines),
        color=COLOUR_GOLD,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

# Register in main.py:
#   tree.add_command(faction_reload, guild=discord.Object(id=GUILD_ID))
'''

print("Factions DB migration ready — apply Section 1 SQL first, then Sections 2-8 in order.")
print("Both bots share the same warhammer_factions and warhammer_detachments tables.")
