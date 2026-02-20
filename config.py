"""
config.py — FND TTS Tournament Bot
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Environment config, all Warhammer data (factions, detachments,
missions, room colours), and small timestamp/colour helpers.

Imported by: virtually every other module.

NOTE: Variable names deliberately avoid the _ID suffix to prevent
Railpack from treating them as build-time secrets (Railpack 0.17.2 bug).
Railway dashboard variables should match the new names below.
"""
import os
from datetime import datetime, timezone
from typing import Dict, Optional
import discord

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

TOKEN        = os.environ["DISCORD_TOKEN"]
GUILD_ID     = int(os.environ["GUILD"])          # Railway var: GUILD
DATABASE_URL = os.environ["DATABASE_URL"]

EVENT_NOTICEBOARD_ID  = int(os.getenv("EVENT_NOTICEBOARD",   0))   # Railway var: EVENT_NOTICEBOARD
WHATS_PLAYING_ID      = int(os.getenv("WHATS_PLAYING",        0))   # Railway var: WHATS_PLAYING
ANNOUNCEMENTS_ID      = int(os.getenv("ANNOUNCEMENTS_CHANNEL",0))   # Railway var: ANNOUNCEMENTS_CHANNEL
BOT_LOGS_ID           = int(os.getenv("BOT_LOGS_CHANNEL",     0))   # Railway var: BOT_LOGS_CHANNEL
CREW_ROLE_ID          = int(os.getenv("CREW_ROLE",            0))   # Railway var: CREW_ROLE
PLAYER_ROLE_ID        = int(os.getenv("PLAYER_ROLE",          0))   # Railway var: PLAYER_ROLE
CAPTAINS_ROLE_ID      = int(os.getenv("CAPTAINS_ROLE",        0))   # Railway var: CAPTAINS_ROLE
LOG_BATCH_MINUTES     = int(os.getenv("LOG_BATCH_MINUTES",    15))

GUILD = discord.Object(id=GUILD_ID)

# ══════════════════════════════════════════════════════════════════════════════
# WARHAMMER DATA
# ══════════════════════════════════════════════════════════════════════════════

# Room accent colours
ROOM_COLOURS: Dict[int, discord.Color] = {
    1:  discord.Color.from_rgb(180,  30,  30),   # Crimson
    2:  discord.Color.from_rgb( 30,  80, 180),   # Royal Blue
    3:  discord.Color.from_rgb( 20, 140,  60),   # Forest Green
    4:  discord.Color.from_rgb(160,  80,   0),   # Burnt Orange
    5:  discord.Color.from_rgb(120,  20, 160),   # Deep Purple
    6:  discord.Color.from_rgb(  0, 140, 150),   # Teal
    7:  discord.Color.from_rgb(180, 150,   0),   # Gold
    9:  discord.Color.from_rgb(180,  50, 120),   # Magenta
    10: discord.Color.from_rgb( 60, 100,  60),   # Olive
}
FALLBACK_COLOUR     = discord.Color.from_rgb(100, 100, 100)
COLOUR_GOLD         = discord.Color.from_rgb(212, 175,  55)
COLOUR_CRIMSON      = discord.Color.from_rgb(180,  30,  30)
COLOUR_AMBER        = discord.Color.from_rgb(200, 140,   0)
COLOUR_SLATE        = discord.Color.from_rgb( 60,  70,  90)

FACTION_EMOJIS = {
    "Adepta Sororitas": "⛪",       "Adeptus Custodes": "🛡️",
    "Adeptus Mechanicus": "⚙️",    "Aeldari": "💎",
    "Astra Militarum": "🪖",       "Black Templars": "✝️",
    "Blood Angels": "🩸",           "Chaos Daemons": "👹",
    "Chaos Knights": "⚡",          "Chaos Space Marines": "🔥",
    "Dark Angels": "🌑",            "Death Guard": "☠️",
    "Deathwatch": "🔭",             "Drukhari": "🗡️",
    "Emperor's Children": "😈",    "Genestealer Cults": "🧬",
    "Grey Knights": "🔮",           "Imperial Agents": "🕵️",
    "Imperial Knights": "🏰",      "Leagues of Votann": "🔨",
    "Necrons": "💀",                "Orks": "🟢",
    "Space Marines": "⚔️",         "Space Wolves": "🐺",
    "T'au Empire": "🔵",            "Thousand Sons": "🌀",
    "Tyranids": "🦎",               "World Eaters": "🪓",
    "Other": "❓",
}

FACTION_COLOURS = {
    "Adepta Sororitas":    discord.Color.from_rgb(180,  60,  60),
    "Adeptus Custodes":    discord.Color.from_rgb(212, 175,  55),
    "Adeptus Mechanicus":  discord.Color.from_rgb(160,  30,  30),
    "Aeldari":             discord.Color.from_rgb(100, 200, 180),
    "Astra Militarum":     discord.Color.from_rgb(100, 120,  60),
    "Black Templars":      discord.Color.from_rgb( 30,  30,  30),
    "Blood Angels":        discord.Color.from_rgb(180,  20,  20),
    "Chaos Daemons":       discord.Color.from_rgb(140,  40, 140),
    "Chaos Knights":       discord.Color.from_rgb( 80,  20,  80),
    "Chaos Space Marines": discord.Color.from_rgb(120,  20,  20),
    "Dark Angels":         discord.Color.from_rgb( 20,  80,  40),
    "Death Guard":         discord.Color.from_rgb( 80, 100,  40),
    "Deathwatch":          discord.Color.from_rgb( 20,  20,  60),
    "Drukhari":            discord.Color.from_rgb(100,  20, 120),
    "Emperor's Children":  discord.Color.from_rgb(200,  80, 160),
    "Genestealer Cults":   discord.Color.from_rgb(120,  60, 140),
    "Grey Knights":        discord.Color.from_rgb(140, 160, 180),
    "Imperial Agents":     discord.Color.from_rgb(100, 100, 120),
    "Imperial Knights":    discord.Color.from_rgb(140, 100,  20),
    "Leagues of Votann":   discord.Color.from_rgb(100,  80,  40),
    "Necrons":             discord.Color.from_rgb( 40, 180, 100),
    "Orks":                discord.Color.from_rgb( 60, 130,  40),
    "Space Marines":       discord.Color.from_rgb( 30,  80, 160),
    "Space Wolves":        discord.Color.from_rgb( 60, 100, 160),
    "T'au Empire":         discord.Color.from_rgb( 60, 160, 200),
    "Thousand Sons":       discord.Color.from_rgb( 40,  80, 160),
    "Tyranids":            discord.Color.from_rgb(120, 180,  40),
    "World Eaters":        discord.Color.from_rgb(180,  40,  20),
    "Other":               discord.Color.from_rgb(100, 100, 100),
}

WARHAMMER_DETACHMENTS = {
    "Adepta Sororitas": ["Hallowed Martyrs","Righteous Crusaders","Penitent Host","Army of Faith","Glorious Crusade","Other"],
    "Adeptus Custodes": ["Shield Host","Talons of the Emperor","Auric Champions","Other"],
    "Adeptus Mechanicus": ["Skitarii Maniple","Rad-Zone Corps","Cohort Cybernetica","Explorator Maniple","Other"],
    "Aeldari": ["Aspect Host","Battle Host","Warhost","Strands of Fate","Other"],
    "Astra Militarum": ["Combined Arms","Hammer of the Emperor","Mechanised Infantry","Bridgehead Assault","Leman Russ Spearhead","Other"],
    "Black Templars": ["Righteous Crusaders","Templar Brethren","Other"],
    "Blood Angels": ["Liberator Assault Group","Sons of Sanguinius","Other"],
    "Chaos Daemons": ["Daemonic Incursion","Plague Purge","Warpstorm","Excess of Violence","Other"],
    "Chaos Knights": ["Traitoris Lance","Iconoclast Horde","Other"],
    "Chaos Space Marines": ["Warband","Raiders","Pactbound Zealots","Council of Traitors","Soulforged Pack","Other"],
    "Dark Angels": ["Unforgiven Task Force","Deathwing Strikeforce","Ravenwing Attackers","Inner Circle Task Force","Other"],
    "Death Guard": ["Plague Company","Inexorable Advance","Other"],
    "Deathwatch": ["Black Spear Task Force","Other"],
    "Drukhari": ["Realspace Raiders","Court of the Archon","Skysplinter Assault","Other"],
    "Emperor's Children": ["Kakophoni","Flawless Blades","Carnival of Excess","Coterie of the Conceited","Slaanesh's Chosen","Court of the Phoenician","Other"],
    "Genestealer Cults": ["Host of Ascension","Xenocreed Congregation","Biosanctic Broodsurge","Outlander Claw","Brood Brother Auxilia","Final Day","Other"],
    "Grey Knights": ["Brotherhood Strike","Hallowed Conclave","Banishers","Sanctic Spearhead","Augurium Taskforce","Warpbane Task Force","Other"],
    "Imperial Agents": ["Ordo Xenos Alien Hunters","Ordo Hereticus Purgation Force","Ordo Malleus","Imperialis Fleet","Veiled Blade Elimination Force","Other"],
    "Imperial Knights": ["Valourstrike Lance","Gate Warden Lance","Questoris Companions","Spearhead-at-Arms","Questor Forgepact","Other"],
    "Leagues of Votann": ["Needgaârd Oathband","Persecution Prospect","Dêlve Assault Shift","Brandfast Oathband","Hearthfyre Arsenal","Hearthband","Other"],
    "Necrons": ["Awakened Dynasty","Annihilation Legion","Canoptek Court","Obeisance Phalanx","Hypercrypt Legion","Starshatter Arsenal","Cryptek Conclave","Cursed Legion","Pantheon of Woe","Other"],
    "Orks": ["War Horde","Da Big Hunt","Kult of Speed","Dread Mob","Green Tide","Bully Boyz","Taktikal Brigade","More Dakka","Other"],
    "Space Marines": ["Gladius Task Force","1st Company Task Force","Vanguard Spearhead","Stormlance Task Force","Firestorm Assault Force","Ironstorm Spearhead","Anvil Siege Force","Librarius Conclave","Blade of Ultramar","Hammer of Avernii","Spearpoint Taskforce","Forgefather's Seekers","Emperor's Shield","Shadowmark Talon","Bastion Task Force","Orbital Assault Force","Reclamation Force","Other"],
    "Space Wolves": ["Saga of the Hunter","Saga of the Bold","Saga of the Beastslayer","Champions of Fenris","Saga of the Great Wolf","Other"],
    "T'au Empire": ["Kauyon","Mont'ka","Retaliation Cadre","Kroot Hunting Pack","Auxiliary Cadre","Experimental Prototype Cadre","Other"],
    "Thousand Sons": ["Grand Coven","Changehost of Deceit","Warpmeld Pact","Rubricae Phalanx","Warpforged Cabal","Hexwarp Thrallband","Other"],
    "Tyranids": ["Invasion Fleet","Vanguard Onslaught","Crusher Stampede","Assimilation Swarm","Synaptic Nexus","Unending Swarm","Warrior Bioform Onslaught","Subterranean Assault","Other"],
    "World Eaters": ["Berzerker Warband","Cult of Blood","Khorne Daemonkin","Possessed Slaughterband","Goretrack Onslaught","Vessels of Wrath","Other"],
    "Other": ["Other"],
}
WARHAMMER_ARMIES = sorted([a for a in WARHAMMER_DETACHMENTS if a != "Other"]) + ["Other"]

TOURNAMENT_MISSIONS = {
    "A": {"name":"Take and Hold",   "deployment":"Tipping Point",       "layouts":["1","2","4","6","7","8"]},
    "B": {"name":"Supply Drop",     "deployment":"Tipping Point",       "layouts":["1","2","4","6","7","8"]},
    "C": {"name":"Linchpin",        "deployment":"Tipping Point",       "layouts":["1","2","4","6","7","8"]},
    "D": {"name":"Scorched Earth",  "deployment":"Tipping Point",       "layouts":["1","2","4","6","7","8"]},
    "E": {"name":"Take and Hold",   "deployment":"Hammer and Anvil",    "layouts":["1","7","8"]},
    "F": {"name":"Hidden Supplies", "deployment":"Hammer and Anvil",    "layouts":["1","7","8"]},
    "G": {"name":"Purge the Foe",   "deployment":"Hammer and Anvil",    "layouts":["1","7","8"]},
    "H": {"name":"Supply Drop",     "deployment":"Hammer and Anvil",    "layouts":["1","7","8"]},
    "I": {"name":"Hidden Supplies", "deployment":"Search and Destroy",  "layouts":["1","2","3","4","6"]},
    "J": {"name":"Linchpin",        "deployment":"Search and Destroy",  "layouts":["1","2","3","4","6"]},
    "K": {"name":"Scorched Earth",  "deployment":"Search and Destroy",  "layouts":["1","2","3","4","6"]},
    "L": {"name":"Take and Hold",   "deployment":"Search and Destroy",  "layouts":["1","2","3","4","6"]},
    "M": {"name":"Purge the Foe",   "deployment":"Crucible of Battle",  "layouts":["1","2","4","6","8"]},
    "N": {"name":"Hidden Supplies", "deployment":"Crucible of Battle",  "layouts":["1","2","4","6","8"]},
    "O": {"name":"Terraform",       "deployment":"Crucible of Battle",  "layouts":["1","2","4","6","8"]},
    "P": {"name":"Scorched Earth",  "deployment":"Crucible of Battle",  "layouts":["1","2","4","6","8"]},
    "Q": {"name":"Supply Drop",     "deployment":"Sweeping Engagement", "layouts":["3","5"]},
    "R": {"name":"Terraform",       "deployment":"Sweeping Engagement", "layouts":["3","5"]},
    "S": {"name":"Linchpin",        "deployment":"Dawn of War",         "layouts":["5"]},
    "T": {"name":"Purge the Foe",   "deployment":"Dawn of War",         "layouts":["5"]},
}

GAME_ROOM_PREFIX = "Game Room"

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def fe(army: str) -> str:
    """Faction emoji shorthand."""
    return FACTION_EMOJIS.get(army, "⚔️")

def faction_colour(army: str) -> discord.Color:
    return FACTION_COLOURS.get(army, FALLBACK_COLOUR)

def room_colour(room_number: Optional[int]) -> discord.Color:
    return ROOM_COLOURS.get(room_number, FALLBACK_COLOUR)

def ts(dt: datetime) -> str:
    """Discord relative timestamp string."""
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:R>"

def ts_full(dt: datetime) -> str:
    """Discord long datetime timestamp."""
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:F>"

SEP = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
