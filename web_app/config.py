"""
Configuratie voor de RANST web applicatie.
Alle settings op één plek — wijzig hier voor productie.
"""
import os

# ── Paden ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(BASE_DIR, 'output')
MEETINGS_DB = os.path.join(DB_DIR, 'meetings.db')
USERS_DB = os.path.join(DB_DIR, 'users.db')
STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')

# ── Server ─────────────────────────────────────────────────────────────────
HOST = '0.0.0.0'
PORT = 8000
SECRET_KEY = os.environ.get('SECRET_KEY', 'demo-secret-change-in-production')

# ── Demo modus ─────────────────────────────────────────────────────────────
DEMO_MODE = os.environ.get('DEMO_MODE', '1') == '1'

# ── Pipeline ───────────────────────────────────────────────────────────────
ANALYSIS_INTERVAL = 300        # 5 minuten (seconden)
TRANSCRIPT_CHUNK_SECONDS = 30  # Whisper chunk grootte
NEWS_SCORE_THRESHOLD = 0.5     # ANP drempel (lager dan publicatie)
SECTOR_SCORE_THRESHOLD = 0.3   # PS sector drempel

# ── Beschikbare onderwerpen ────────────────────────────────────────────────
TOPICS = {
    'ruimtelijke_ordening': 'Ruimtelijke Ordening',
    'woningbouw': 'Woningbouw',
    'infrastructuur': 'Infrastructuur & Mobiliteit',
    'energie_klimaat': 'Energie & Klimaat',
    'stikstof_natuur': 'Stikstof & Natuur',
    'financien': 'Financiën & Toezicht',
    'veiligheid': 'Veiligheid',
    'zorg_welzijn': 'Zorg & Welzijn',
    'economie': 'Economie & Werkgelegenheid',
    'water': 'Water & Dijken',
    'onderwijs': 'Onderwijs',
    'cultuur': 'Cultuur & Erfgoed',
}

# ── Provincies ─────────────────────────────────────────────────────────────
PROVINCIES = [
    'Drenthe', 'Flevoland', 'Friesland', 'Gelderland', 'Groningen',
    'Limburg', 'Noord-Brabant', 'Noord-Holland', 'Overijssel',
    'Utrecht', 'Zeeland', 'Zuid-Holland',
    'Bonaire', 'Saba', 'Sint Eustatius',
]
