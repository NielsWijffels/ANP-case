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
TEMP_DIR = os.path.join(DB_DIR, 'temp_alerts')
STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')

# ── Server ─────────────────────────────────────────────────────────────────
HOST = '0.0.0.0'
PORT = 8000
SECRET_KEY = os.environ.get('SECRET_KEY', 'demo-secret-change-in-production')

# ── Demo modus ─────────────────────────────────────────────────────────────
DEMO_MODE = os.environ.get('DEMO_MODE', '1') == '1'

# ── Pipeline ───────────────────────────────────────────────────────────────
ANALYSIS_INTERVAL = 300            # 5 minuten (seconden) — hoe vaak we analyseren
ANALYSIS_WINDOW = 1800             # 30 minuten (seconden) — rolling window voor analyse
TRANSCRIPT_CHUNK_SECONDS = 30      # Whisper chunk grootte
TEMP_ALERT_TTL = 3600              # 1 uur — alerts in temp map max zolang bewaard

# ── LLM configuratie ──────────────────────────────────────────────────────
LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'http://localhost:11434')
LLM_MODEL = os.environ.get('LLM_MODEL', 'mistral')

# ── Interesse-niveaus ──────────────────────────────────────────────────────
#
# Per onderwerp kiest de gebruiker één van drie niveaus:
#   geen         — geen meldingen
#   bestuurlijk  — brede relevantie, lage drempel, actionable output
#   pers         — alleen nieuwswaardig, korte informatieve samenvatting
#
INTEREST_LEVELS = {
    'geen': {
        'label': 'Geen interesse',
        'description': 'Geen meldingen voor dit onderwerp',
        'threshold': None,   # nooit triggeren
    },
    'bestuurlijk': {
        'label': 'Bestuurlijk',
        'description': 'Provinciale Staten, beleidsmedewerkers — actionable inzichten',
        'threshold': 0.25,   # lage drempel: alles dat relevant is
    },
    'pers': {
        'label': 'Nieuwsredactie',
        'description': 'Journalisten, ANP — alleen nieuwswaardige zaken',
        'threshold': 0.65,   # hoge drempel: echt nieuwswaardig
    },
}

# ── 9 Onderwerpen (herindeling) ───────────────────────────────────────────
TOPICS = {
    'bestuur_politiek':         'Bestuur & Politiek',
    'wonen_ruimte':             'Wonen & Ruimte',
    'klimaat_natuur_stikstof':  'Klimaat, Natuur & Stikstof',
    'bereikbaarheid_infra':     'Bereikbaarheid & Infra',
    'landbouw_platteland':      'Landbouw & Platteland',
    'economie_innovatie':       'Economie & Innovatie',
    'cultuur_sport_samenleving':'Cultuur, Sport & Samenleving',
    'financien_toezicht':       'Financiën & Toezicht',
    'veiligheid':               'Veiligheid',
}

# ── Provincies ─────────────────────────────────────────────────────────────
PROVINCIES = [
    'Drenthe', 'Flevoland', 'Friesland', 'Gelderland', 'Groningen',
    'Limburg', 'Noord-Brabant', 'Noord-Holland', 'Overijssel',
    'Utrecht', 'Zeeland', 'Zuid-Holland',
    'Bonaire', 'Saba', 'Sint Eustatius',
]

# ── LLM Prompt Templates ──────────────────────────────────────────────────
#
# {topic_name}  = onderwerp naam (bijv. "Wonen & Ruimte")
# {gemeente}    = naam van gemeente
# {text}        = transcript-fragment (max 3000 chars)
#

PROMPT_PERS = """Je bent een ervaren nieuwsredacteur voor het ANP (Algemeen Nederlands Persbureau).
Je analyseert een transcript van een vergadering in {gemeente}.

ONDERWERP: {topic_name}

OPDRACHT:
Schrijf een kort, informatief nieuwsbericht van MAXIMAAL 100 woorden over dit onderwerp.
Schrijf het als een krantenartikel: lead-zin (wie, wat, waar, wanneer), kern, en afsluiting.
Gebruik alleen feiten uit het transcript. Citeer maximaal 1 relevante quote letterlijk.
Noem betrokken sprekers en hun partij wanneer bekend.
Vermeld stemgedrag ALLEEN als het gaat om verkiezingen of grote besluiten met brede maatschappelijke impact.

Als het fragment NIET nieuwswaardig is voor het brede publiek, antwoord dan exact: NIET_RELEVANT

TRANSCRIPT:
{text}

NIEUWSBERICHT:"""

PROMPT_BESTUURLIJK = """Je bent een beleidsanalist voor Provinciale Staten.
Je analyseert een transcript van een vergadering in {gemeente}.

ONDERWERP: {topic_name}

OPDRACHT:
Geef een bestuurlijk inzicht van maximaal 200 woorden. Focus op:
1. KERN: Wat is besproken en besloten over dit onderwerp?
2. PARTIJEN & STANDPUNTEN: Welke partijen nemen welk standpunt in? Wie is voor, wie is tegen?
3. STEMGEDRAG: Als er gestemd is: uitslag per partij (voor/tegen/onthouding)
4. IMPACT: Wat betekent dit voor de provincie / het beleid?
5. ACTIE: Welke concrete vervolgstappen worden verwacht?

Let op: voor bestuurlijke relevantie kijk je NIET naar hoe spectaculair iets is voor het nieuws.
Kijk in plaats daarvan naar:
- Beleidswijzigingen en nieuwe regelgeving
- Budgettaire beslissingen en financiële impact
- Interbestuurlijke verhoudingen (gemeente-provincie-rijk)
- Politieke verschuivingen en coalitiedynamiek
- Effecten op lopend provinciaal beleid
- Moties, amendementen en toezeggingen

Als het fragment NIET bestuurlijk relevant is, antwoord dan exact: NIET_RELEVANT

TRANSCRIPT:
{text}

BESTUURLIJK INZICHT:"""
