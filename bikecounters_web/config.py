"""
config.py — Location definitions for Plzeň cycling counters
"""
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent
DB_PATH    = BASE_DIR / "cyklo.db"

# ── ČHMÚ ──────────────────────────────────────────────────────────────────────
CHMI_STATION     = "11450"   # Plzeň-Mikulka
CHMI_HIST_BASE   = "https://opendata.chmi.cz/meteorology/climate/historical_csv/data/daily"
CHMI_RECENT_BASE = "https://opendata.chmi.cz/meteorology/climate/recent/data/daily"
CHMI_CACHE_DIR   = BASE_DIR / "chmi_cache"

# ── Source URLs ────────────────────────────────────────────────────────────────
ECO_URL    = "https://opendata.plzen.eu/public/opendata/ecocounter-traffic"
CAMERA_URL = "https://opendata.plzen.eu/public/opendata/camera-view/{cam_id}"

# ── Color palette for direction lines ─────────────────────────────────────────
PALETTE = [
    "#39d353", "#58a6ff", "#ffa657", "#f78166",
    "#d2a8ff", "#79c0ff", "#e3b341", "#ff7b72",
]

# ── Location definitions ───────────────────────────────────────────────────────
# Each location has:
#   id          – unique string key
#   name        – display name
#   section     – nav section: "eco" | "camera"
#   type        – "ecocounter" | "camera"
#   color       – primary hex color (used for combined line)
#   icon        – optional emoji
#   group       – nav group id (for Papírna / Skvrňany grouping)
#
# Eco-counter locations additionally have:
#   site_id     – siteId in the eco-counter CSV
#   collectors  – [{"source_id", "label", "color"}, ...]
#                 "in" and "out" direction
#
# Camera locations additionally have:
#   cam_id      – numeric camera ID for the API URL
#   collectors  – [{"collector_id", "source_id", "label", "color"}, ...]

LOCATIONS = [
    # ── Eco-counter ──────────────────────────────────────────────────────────
    {
        "id": "eco_prazdroj",
        "name": "U Prazdroje",
        "section": "eco",
        "type": "ecocounter",
        "color": "#39d353",
        "icon": "🍺",
        "group": None,
        "site_id": "300048586",
        "collectors": [
            {"source_id": "eco_300048586_in",  "label": "↗ Centrum → Doubravka", "color": "#39d353"},
            {"source_id": "eco_300048586_out", "label": "↙ Doubravka → Centrum", "color": "#1a6629"},
        ],
    },
    {
        "id": "eco_karlovarska",
        "name": "Karlovarska",
        "section": "eco",
        "type": "ecocounter",
        "color": "#58a6ff",
        "icon": "🚲",
        "group": None,
        "site_id": "300048587",
        "collectors": [
            {"source_id": "eco_300048587_in",  "label": "↗ Centrum → Bolevec", "color": "#58a6ff"},
            {"source_id": "eco_300048587_out", "label": "↙ Bolevec → Centrum", "color": "#1a4d8f"},
        ],
    },

    # ── Cameras ───────────────────────────────────────────────────────────────
    {
        "id": "cam_bhora",
        "name": "Bílá Hora",
        "section": "camera",
        "type": "camera",
        "color": "#f78166",
        "group": None,
        "cam_id": "29",
        "collectors": [
            {"collector_id": "8", "source_id": "cam_29_c8", "label": "→ Bílá Hora – Zruč",  "color": "#f78166"},
            {"collector_id": "9", "source_id": "cam_29_c9", "label": "← Zruč – Bílá Hora", "color": "#ffa657"},
        ],
    },
    {
        "id": "cam_sady",
        "name": "Sady Pětatřicátníků",
        "section": "camera",
        "type": "camera",
        "color": "#d2a8ff",
        "group": None,
        "cam_id": "1",
        "collectors": [
            {"collector_id": "23", "source_id": "cam_1_c23", "label": "→ Centrum",  "color": "#d2a8ff"},
            {"collector_id": "26", "source_id": "cam_1_c26", "label": "→ OC Plaza", "color": "#8b5cf6"},
        ],
    },
    {
        "id": "cam_koterov",
        "name": "Koterov",
        "section": "camera",
        "type": "camera",
        "color": "#ffa657",
        "group": None,
        "cam_id": "17",
        "collectors": [
            {"collector_id": "5", "source_id": "cam_17_c5", "label": "→ Koterov – Starý Plzenec", "color": "#ffa657"},
            {"collector_id": "6", "source_id": "cam_17_c6", "label": "← Starý Plzenec – Koterov", "color": "#e67e22"},
        ],
    },
    {
        "id": "cam_skvrn",
        "name": "Zadní Skvrňany",
        "section": "camera",
        "type": "camera",
        "color": "#79c0ff",
        "group": "skvrn",
        "cam_id": "19",
        "collectors": [
            {"collector_id": "7",  "source_id": "cam_19_c7",  "label": "Cyklostezka Skvrňany",   "color": "#79c0ff"},
            {"collector_id": "8",  "source_id": "cam_19_c8",  "label": "Přechod Vejprnice",       "color": "#58a6ff"},
            {"collector_id": "9",  "source_id": "cam_19_c9",  "label": "Přechod Skvrňany",        "color": "#1f6feb"},
            {"collector_id": "11", "source_id": "cam_19_c11", "label": "Cyklostezka Vejprnice",   "color": "#388bfd"},
        ],
    },
    {
        "id": "cam_prazdroj",
        "name": "U Prazdroje",
        "section": "camera",
        "type": "camera",
        "color": "#7ee787",
        "group": None,
        "cam_id": "5",
        "collectors": [
            {"collector_id": "5", "source_id": "cam_5_c5", "label": "U Prazdroje → Centrum",    "color": "#7ee787"},
            {"collector_id": "6", "source_id": "cam_5_c6", "label": "U Prazdroje → Rokycanská", "color": "#56d364"},
            {"collector_id": "7", "source_id": "cam_5_c7", "label": "Lobezká → U Prazdroje",    "color": "#2ea043"},
            {"collector_id": "8", "source_id": "cam_5_c8", "label": "U Prazdroje → Lobezká",    "color": "#39d353"},
        ],
    },
    {
        "id": "cam_papirna_c",
        "name": "Papírna (centrum)",
        "section": "camera",
        "type": "camera",
        "color": "#e3b341",
        "group": "papirna",
        "cam_id": "3",
        "collectors": [
            {"collector_id": "5", "source_id": "cam_3_c5", "label": "→ Centrum",   "color": "#e3b341"},
            {"collector_id": "6", "source_id": "cam_3_c6", "label": "→ Doudlevce", "color": "#d29922"},
        ],
    },
    {
        "id": "cam_papirna_s",
        "name": "Papírna (Slovany)",
        "section": "camera",
        "type": "camera",
        "color": "#f0883e",
        "group": "papirna",
        "cam_id": "23",
        "collectors": [
            {"collector_id": "6",  "source_id": "cam_23_c6",  "label": "Centrum → Slovany",  "color": "#f0883e"},
            {"collector_id": "7",  "source_id": "cam_23_c7",  "label": "Centrum → Lávka",    "color": "#e67e22"},
            {"collector_id": "9",  "source_id": "cam_23_c9",  "label": "Slovany → Centrum",  "color": "#d35400"},
            {"collector_id": "10", "source_id": "cam_23_c10", "label": "Lávka → Centrum",    "color": "#ffa657"},
            {"collector_id": "11", "source_id": "cam_23_c11", "label": "Lávka → Slovany",    "color": "#ff8c00"},
            {"collector_id": "12", "source_id": "cam_23_c12", "label": "Slovany → Lávka",    "color": "#ff6b35"},
        ],
    },
    {
        "id": "cam_papirna_d",
        "name": "Papírna (Doudlevce)",
        "section": "camera",
        "type": "camera",
        "color": "#bc8cff",
        "group": "papirna",
        "cam_id": "26",
        "collectors": [
            {"collector_id": "7", "source_id": "cam_26_c7", "label": "→ Doudlevce", "color": "#bc8cff"},
            {"collector_id": "8", "source_id": "cam_26_c8", "label": "← Doudlevce", "color": "#8b5cf6"},
        ],
    },
    {
        "id": "cam_rondel",
        "name": "Karlovarská (Rondel)",
        "section": "camera",
        "type": "camera",
        "color": "#ff7b72",
        "group": None,
        "cam_id": "9",
        "collectors": [
            {"collector_id": "5", "source_id": "cam_9_c5", "label": "→ Centrum",  "color": "#ff7b72"},
            {"collector_id": "6", "source_id": "cam_9_c6", "label": "→ Lochotín", "color": "#da3633"},
        ],
    },
    {
        "id": "cam_malesice",
        "name": "Malesice",
        "section": "camera",
        "type": "camera",
        "color": "#56d364",
        "group": None,
        "cam_id": "11",
        "collectors": [],   # collector map not yet known; filled after first ingest
    },
]

# ── Groups (for nav grouping) ─────────────────────────────────────────────────
GROUPS = {
    "papirna": {"label": "Papírna", "icon": "🏭"},
    "skvrn":   {"label": "Skvrňany / Vejprnice", "icon": "🌿"},
}

# ── Fast lookup helpers ────────────────────────────────────────────────────────
LOCATION_BY_ID   = {loc["id"]: loc for loc in LOCATIONS}

# source_id → (location_id, collector dict)
SOURCE_MAP = {}
for loc in LOCATIONS:
    for col in loc["collectors"]:
        SOURCE_MAP[col["source_id"]] = (loc["id"], col)
