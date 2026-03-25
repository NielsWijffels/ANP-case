"""
Configuratie voor de RANST web applicatie.
Alle settings op één plek — wijzig hier voor productie.
"""
import os

# ── Paden ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(BASE_DIR, 'output')
RANST_DB = os.path.join(DB_DIR, 'ranst.db')
MEETINGS_DB = os.path.join(DB_DIR, 'meetings.db')
TEMP_DIR = os.path.join(DB_DIR, 'temp_alerts')
STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static')

# ── Server ─────────────────────────────────────────────────────────────────
HOST = '0.0.0.0'
PORT = 8000

# ── Pipeline ───────────────────────────────────────────────────────────────
ANALYSIS_INTERVAL = 300            # 5 minuten (seconden) — hoe vaak we analyseren
ANALYSIS_WINDOW = 1800             # 30 minuten (seconden) — rolling window voor analyse
TRANSCRIPT_CHUNK_SECONDS = 30      # Whisper chunk grootte
TEMP_ALERT_TTL = 86400             # 24 uur — alerts in temp map max zolang bewaard

# ── LLM configuratie ──────────────────────────────────────────────────────
LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'http://localhost:11434')
LLM_MODEL = os.environ.get('LLM_MODEL', os.environ.get('OLLAMA_MODEL', 'llama3.2:3b'))
LLM_SCHETS_MODEL = os.environ.get('LLM_SCHETS_MODEL', 'llama3.2:3b')  # Betere kwaliteit voor pre-generated schets
HUB_MODEL = os.environ.get('HUB_MODEL', 'llama3.2:3b')  # Model voor Hub RAG-queries

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
]

# ── Provincie → Gemeente mapping (2024) ────────────────────────────────────
PROVINCIE_GEMEENTEN = {
    'Groningen': [
        'Groningen', 'Westerkwartier', 'Midden-Groningen', 'Oldambt',
        'Veendam', 'Stadskanaal', 'Pekela', 'Westerwolde',
        'Het Hogeland', 'Eemsdelta',
    ],
    'Friesland': [
        'Leeuwarden', 'Smallingerland', 'Heerenveen', 'Súdwest-Fryslân',
        'De Fryske Marren', 'Waadhoeke', 'Noardeast-Fryslân', 'Opsterland',
        'Tytsjerksteradiel', 'Achtkarspelen', 'Harlingen', 'Ameland',
        'Schiermonnikoog', 'Terschelling', 'Vlieland', 'Dantumadiel',
        'Weststellingwerf', 'Ooststellingwerf',
    ],
    'Drenthe': [
        'Emmen', 'Assen', 'Hoogeveen', 'Meppel', 'Coevorden',
        'Westerveld', 'De Wolden', 'Borger-Odoorn', 'Aa en Hunze',
        'Tynaarlo', 'Midden-Drenthe', 'Noordenveld',
    ],
    'Overijssel': [
        'Enschede', 'Almelo', 'Deventer', 'Zwolle', 'Hengelo',
        'Oldenzaal', 'Haaksbergen', 'Borne', 'Losser', 'Tubbergen',
        'Dinkelland', 'Wierden', 'Hellendoorn', 'Rijssen-Holten',
        'Twenterand', 'Dalfsen', 'Olst-Wijhe', 'Staphorst',
        'Steenwijkerland', 'Zwartewaterland', 'Kampen', 'Hardenberg',
        'Raalte', 'Ommen', 'Hof van Twente',
    ],
    'Flevoland': [
        'Almere', 'Lelystad', 'Urk', 'Zeewolde', 'Dronten', 'Noordoostpolder',
    ],
    'Gelderland': [
        'Arnhem', 'Nijmegen', 'Apeldoorn', 'Ede', 'Harderwijk', 'Wageningen',
        'Zutphen', 'Doetinchem', 'Winterswijk', 'Aalten', 'Berkelland',
        'Bronckhorst', 'Lochem', 'Oost Gelre', 'Oude IJsselstreek', 'Doesburg',
        'Rheden', 'Rozendaal', 'Zevenaar', 'Duiven', 'Lingewaard',
        'Overbetuwe', 'Buren', 'Culemborg', 'Neder-Betuwe', 'Tiel',
        'West Betuwe', 'Zaltbommel', 'Maasdriel', 'Wijchen', 'Berg en Dal',
        'Beuningen', 'Druten', 'Heumen', 'Westervoort', 'Montferland',
        'Voorst', 'Brummen', 'Heerde', 'Nunspeet', 'Elburg', 'Ermelo',
        'Putten', 'Nijkerk', 'Barneveld', 'Scherpenzeel', 'Hattem',
        'Oldebroek', 'Epe', 'Renkum', 'West Maas en Waal',
    ],
    'Utrecht': [
        'Utrecht', 'Amersfoort', 'Nieuwegein', 'Veenendaal', 'Houten',
        'Zeist', 'De Bilt', 'Stichtse Vecht', 'Woerden', 'Lopik',
        'Montfoort', 'IJsselstein', 'Vijfheerenlanden', 'Oudewater',
        'De Ronde Venen', 'Wijk bij Duurstede', 'Utrechtse Heuvelrug',
        'Soest', 'Baarn', 'Bunschoten', 'Leusden', 'Eemnes', 'Bunnik',
        'Renswoude', 'Rhenen', 'Woudenberg',
    ],
    'Noord-Holland': [
        'Amsterdam', 'Haarlem', 'Alkmaar', 'Hoorn', 'Zaanstad', 'Hilversum',
        'Haarlemmermeer', 'Amstelveen', 'Purmerend', 'Dijk en Waard',
        'Enkhuizen', 'Medemblik', 'Hollands Kroon', 'Schagen', 'Bergen (NH)',
        'Heiloo', 'Castricum', 'Velsen', 'Beverwijk', 'Heemskerk',
        'Uitgeest', 'Waterland', 'Edam-Volendam', 'Landsmeer', 'Oostzaan',
        'Wormerland', 'Bloemendaal', 'Heemstede', 'Zandvoort', 'Stede Broec',
        'Drechterland', 'Koggenland', 'Opmeer', 'Den Helder', 'Texel',
        'Wijdemeren', 'Huizen', 'Blaricum', 'Gooise Meren', 'Laren',
        'Diemen', 'Ouder-Amstel', 'Uithoorn', 'Aalsmeer',
    ],
    'Zuid-Holland': [
        'Den Haag', 'Rotterdam', 'Dordrecht', 'Leiden', 'Zoetermeer',
        'Delft', 'Westland', 'Alphen aan den Rijn', 'Gouda', 'Katwijk',
        'Pijnacker-Nootdorp', 'Lansingerland', 'Nissewaard', 'Hoeksche Waard',
        'Goeree-Overflakkee', 'Sliedrecht', 'Hendrik-Ido-Ambacht',
        'Zwijndrecht', 'Papendrecht', 'Alblasserdam', 'Hardinxveld-Giessendam',
        'Molenlanden', 'Gorinchem', 'Bodegraven-Reeuwijk', 'Krimpenerwaard',
        'Capelle aan den IJssel', 'Krimpen aan den IJssel', 'Barendrecht',
        'Ridderkerk', 'Albrandswaard', 'Maassluis', 'Vlaardingen', 'Schiedam',
        'Voorne aan Zee', 'Midden-Delfland',
        'Rijswijk', 'Wassenaar', 'Leidschendam-Voorburg', 'Zuidplas',
        'Waddinxveen', 'Hillegom', 'Noordwijk', 'Leiderdorp', 'Oegstgeest',
        'Voorschoten', 'Zoeterwoude', 'Nieuwkoop',
        'Lisse', 'Teylingen', 'Kaag en Braassem',
    ],
    'Zeeland': [
        'Middelburg', 'Vlissingen', 'Goes', 'Terneuzen', 'Hulst',
        'Schouwen-Duiveland', 'Noord-Beveland', 'Veere', 'Borsele',
        'Kapelle', 'Reimerswaal', 'Sluis', 'Tholen',
    ],
    'Noord-Brabant': [
        "'s-Hertogenbosch", 'Tilburg', 'Eindhoven', 'Breda', 'Helmond',
        'Oss', 'Bergen op Zoom', 'Roosendaal', 'Waalwijk', 'Oosterhout',
        'Veldhoven', 'Etten-Leur', 'Altena', 'Meierijstad', 'Bernheze',
        'Maashorst', 'Nuenen/Gerwen/Nederwetten', 'Son en Breugel',
        'Heeze-Leende', 'Cranendonck', 'Waalre', 'Geldrop-Mierlo',
        'Deurne', 'Asten', 'Someren', 'Gemert-Bakel', 'Laarbeek',
        'Hilvarenbeek', 'Oirschot', 'Best', 'Oisterwijk', 'Boxtel',
        'Sint-Michielsgestel', 'Vught', 'Heusden', 'Loon op Zand',
        'Gilze en Rijen', 'Goirle', 'Dongen', 'Alphen-Chaam', 'Baarle-Nassau',
        'Zundert', 'Halderberge', 'Rucphen', 'Moerdijk', 'Steenbergen',
        'Woensdrecht', 'Geertruidenberg', 'Drimmelen', 'Boekel',
        'Bladel', 'Eersel', 'Land van Cuijk', 'Reusel-De Mierden',
        'Valkenswaard', 'Bergeijk',
    ],
    'Limburg': [
        'Maastricht', 'Heerlen', 'Venlo', 'Sittard-Geleen', 'Roermond',
        'Weert', 'Venray', 'Horst aan de Maas', 'Peel en Maas', 'Leudal',
        'Echt-Susteren', 'Maasgouw', 'Roerdalen', 'Beesel', 'Kerkrade',
        'Voerendaal', 'Simpelveld', 'Vaals', 'Gulpen-Wittem', 'Beekdaelen',
        'Brunssum', 'Landgraaf', 'Bergen (L)', 'Gennep', 'Mook en Middelaar',
        'Eijsden-Margraten', 'Stein', 'Beek', 'Meerssen', 'Nederweert',
        'Valkenburg aan de Geul',
    ],
}

# ── LLM Prompt Templates — per onderwerp ──────────────────────────────────
#
# Variabelen: {gemeente}, {text}
# Antwoord 'NIET_RELEVANT' als er niets relevants is.
#

def _pers(body: str) -> str:
    return (
        "INSTRUCTIE: Begin je antwoord met een pakkende krantenkop op de EERSTE REGEL "
        "(max 12 woorden, zonder gemeentenaam, geen aanhalingstekens, geen punt aan het eind). "
        "Schrijf daarna het nieuwsbericht. "
        "Gebruik directe citaten (aanhalingstekens) ALLEEN voor de meest impactvolle of opvallende uitspraken — maximaal één citaat per bericht, en alleen als het echt nieuwswaarde toevoegt.\n\n" +
        body.strip() +
        "\n\nGemeente: {gemeente}\n\n"
        "TRANSCRIPT:\n{text}\n\n"
        "ANTWOORD (eerste regel = koptekst, of exact 'NIET_RELEVANT' als er niets relevants is):"
    )

def _best(body: str) -> str:
    return (
        "INSTRUCTIE: Begin je antwoord met een pakkende berichtkop op de EERSTE REGEL "
        "(max 12 woorden, zonder gemeentenaam, geen aanhalingstekens, geen punt aan het eind). "
        "Schrijf daarna de gevraagde blokken. "
        "Gebruik directe citaten (aanhalingstekens) ALLEEN voor de meest impactvolle of beslissende uitspraken — maximaal één citaat per bericht, en alleen als het de analyse echt versterkt.\n\n" +
        body.strip() +
        "\n\nGemeente: {gemeente}\n\n"
        "TRANSCRIPT:\n{text}\n\n"
        "ANTWOORD (eerste regel = koptekst, of exact 'NIET_RELEVANT' als er niets relevants is):"
    )


PROMPTS_PERS = {
    'bestuur_politiek': _pers("""
Je bent nieuwsredacteur bij het ANP. Analyseer het transcript op nieuws over bestuur en politiek:
verkiezingen, coalitievorming, raadsbesluiten, bestuurlijke procedures of politieke verhoudingen.
Is er niets relevants? Antwoord exact 'NIET_RELEVANT'.
Is er wel iets? Schrijf een feitelijk, neutraal nieuwsbericht van 200-250 woorden.
Vermeld: de kern van het nieuws, welke partijen of personen betrokken zijn en hun standpunten, en de status (voorlopig/definitief). Wees concreet en specifiek.
"""),
    'wonen_ruimte': _pers("""
Je bent nieuwsredacteur bij het ANP. Analyseer het transcript op nieuws over wonen en ruimtelijke ordening:
woningbouw, bestemmingsplannen, vergunningen, nieuwbouwprojecten of ruimtelijke visies.
Is er niets relevants? Antwoord exact 'NIET_RELEVANT'.
Is er wel iets? Schrijf een feitelijk, neutraal nieuwsbericht van 200-250 woorden.
Vermeld: de kern, aantallen of locaties waar bekend, welke partijen voor/tegen zijn en de status.
"""),
    'klimaat_natuur_stikstof': _pers("""
Je bent nieuwsredacteur bij het ANP. Analyseer het transcript op nieuws over klimaat, natuur en stikstof:
energietransitie, natuurgebieden, stikstofmaatregelen, duurzaamheidsbeleid of milieubesluiten.
Is er niets relevants? Antwoord exact 'NIET_RELEVANT'.
Is er wel iets? Schrijf een feitelijk, neutraal nieuwsbericht van 200-250 woorden.
Vermeld: de kern, betrokken instanties en partijstandpunten waar relevant, en de status.
"""),
    'bereikbaarheid_infra': _pers("""
Je bent nieuwsredacteur bij het ANP. Analyseer het transcript op nieuws over bereikbaarheid en infrastructuur:
wegen, openbaar vervoer, fietspaden, bruggen, spoor of verkeersbesluiten.
Is er niets relevants? Antwoord exact 'NIET_RELEVANT'.
Is er wel iets? Schrijf een feitelijk, neutraal nieuwsbericht van 200-250 woorden.
Vermeld: het project of besluit, betrokken partijen, kosten/tijdlijn waar bekend en de status.
"""),
    'landbouw_platteland': _pers("""
Je bent nieuwsredacteur bij het ANP. Analyseer het transcript op nieuws over landbouw en het platteland:
boerenprotesten, grondbeleid, pacht, agrarische vergunningen of plattelandsontwikkeling.
Is er niets relevants? Antwoord exact 'NIET_RELEVANT'.
Is er wel iets? Schrijf een feitelijk, neutraal nieuwsbericht van 200-250 woorden.
Vermeld: de kern, betrokken partijen en hun standpunten, en de status.
"""),
    'economie_innovatie': _pers("""
Je bent nieuwsredacteur bij het ANP. Analyseer het transcript op nieuws over economie en innovatie:
bedrijventerreinen, werkgelegenheid, subsidies, startups, economisch beleid of innovatieprojecten.
Is er niets relevants? Antwoord exact 'NIET_RELEVANT'.
Is er wel iets? Schrijf een feitelijk, neutraal nieuwsbericht van 200-250 woorden.
Vermeld: de kern, betrokken organisaties, bedragen of aantallen waar bekend, en de status.
"""),
    'cultuur_sport_samenleving': _pers("""
Je bent nieuwsredacteur bij het ANP. Analyseer het transcript op nieuws over cultuur, sport en samenleving:
subsidies aan verenigingen, culturele instellingen, sportevenementen, sociale voorzieningen of gemeenschapsinitiatieven.
Is er niets relevants? Antwoord exact 'NIET_RELEVANT'.
Is er wel iets? Schrijf een feitelijk, neutraal nieuwsbericht van 200-250 woorden.
Vermeld: de kern, betrokken organisaties, bedragen waar known en de status.
"""),
    'financien_toezicht': _pers("""
Je bent nieuwsredacteur bij het ANP. Analyseer het transcript op nieuws over gemeentefinanciën en toezicht:
begrotingen, tekorten, accountantsrapporten, bezuinigingen of toezichtmaatregelen van provincie of Rijk.
Is er niets relevants? Antwoord exact 'NIET_RELEVANT'.
Is er wel iets? Schrijf een feitelijk, neutraal nieuwsbericht van 200-250 woorden.
Vermeld: de kern, bedragen waar known, oorzaken/gevolgen en de status.
"""),
    'veiligheid': _pers("""
Je bent nieuwsredacteur bij het ANP. Analyseer het transcript op nieuws over veiligheid:
politie-inzet, criminaliteit, ondermijning, brandweer, openbare orde of handhavingsbesluiten.
Is er niets relevants? Antwoord exact 'NIET_RELEVANT'.
Is er wel iets? Schrijf een feitelijk, neutraal nieuwsbericht van 200-250 woorden.
Vermeld: de kern, betrokken instanties en maatregelen, en de status.
"""),
}

def _best_body(onderwerp: str, focus: str) -> str:
    return f"""
Je bent bestuurlijk analist. Analyseer het transcript op bestuurlijk relevante informatie over {onderwerp}: {focus}.
Is er niets relevants? Antwoord exact 'NIET_RELEVANT'.
Is er wel iets? Geef dan de volgende drie blokken (wees uitgebreid en specifiek):

**Samenvatting** — Beschrijf in 5-8 zinnen wat er is besproken, wat de kern van het debat was en wat het resultaat of de status is. Noem concrete feiten, bedragen of aantallen waar beschikbaar.

**Partijstandpunten** — Beschrijf per partij of fractie (bij naam) hun standpunt in 2-3 zinnen. Wie steunde het voorstel, wie niet en waarom? Wat zijn de politieke scheidslijnen?

**Actiepunten** — Genummerde lijst van concrete, uitvoerbare acties die voortvloeien uit dit debat. Formuleer elke actie als een directe opdracht (wie doet wat, wanneer).
"""

PROMPTS_BESTUURLIJK = {
    'bestuur_politiek': _best(_best_body(
        'bestuur en politiek',
        'coalitievorming, raadsbesluiten, moties, amendementen, politieke verhoudingen, integriteit'
    )),
    'wonen_ruimte': _best(_best_body(
        'wonen en ruimtelijke ordening',
        'woningbouwopgaven, bestemmingsplannen, vergunningsverlening, ruimtelijke visies, aantallen woningen'
    )),
    'klimaat_natuur_stikstof': _best(_best_body(
        'klimaat, natuur en stikstof',
        'energietransitie, stikstofbesluiten, milieuvergunningen, Natura 2000, duurzaamheidsbeleid'
    )),
    'bereikbaarheid_infra': _best(_best_body(
        'bereikbaarheid en infrastructuur',
        'wegen, OV-concessies, spoorkwesties, verkeersbesluiten, budgetten en tijdlijnen'
    )),
    'landbouw_platteland': _best(_best_body(
        'landbouw en het platteland',
        'grondbeleid, pacht, agrarische vergunningen, boerenproblematiek, plattelandsbeleid'
    )),
    'economie_innovatie': _best(_best_body(
        'economie en innovatie',
        'economische ontwikkeling, subsidieverlening, bedrijventerreinen, werkgelegenheid, innovatieprogramma\'s'
    )),
    'cultuur_sport_samenleving': _best(_best_body(
        'cultuur, sport en samenleving',
        'subsidiebesluiten, voorzieningen, verenigingsbeleid, sociale vraagstukken, regionale samenwerking'
    )),
    'financien_toezicht': _best(_best_body(
        'financiën en toezicht',
        'begrotingen, tekorten, artikel 12, accountantsrapporten, bezuinigingen, financiële risico\'s'
    )),
    'veiligheid': _best(_best_body(
        'veiligheid',
        'openbare orde, handhaving, ondermijning, veiligheidsregio, politiesamenwerking, maatregelen'
    )),
}

# Backwards-compat: generieke fallbacks
PROMPT_PERS = PROMPTS_PERS['bestuur_politiek']
PROMPT_BESTUURLIJK = PROMPTS_BESTUURLIJK['bestuur_politiek']


# ── Gecombineerde prompts (meerdere thema's in één artikel) ──────────────────

def build_combined_pers_prompt(topic_names: list, gemeente: str, text: str) -> str:
    topics_str = ', '.join(topic_names)
    return (
        "INSTRUCTIE: Begin je antwoord met een pakkende krantenkop op de EERSTE REGEL "
        "(max 12 woorden, zonder gemeentenaam, geen aanhalingstekens, geen punt aan het eind).\n"
        "Schrijf daarna een feitelijk, neutraal nieuwsbericht van 200-250 woorden.\n"
        "Corrigeer evidente transcriptiefouten (bijv. 'T66' → 'D66', 'PVA' → 'PvdA').\n\n"
        f"Relevante thema's in dit fragment: {topics_str}\n"
        "Dek alle relevante thema's af in één samenhangend bericht. "
        "Vermeld betrokken partijen, standpunten en status (voorlopig/definitief).\n"
        "Is er niets relevants? Antwoord exact 'NIET_RELEVANT'.\n\n"
        f"Gemeente: {gemeente}\n\n"
        f"TRANSCRIPT:\n{text[:3500]}\n\n"
        "ANTWOORD (eerste regel = koptekst, of exact 'NIET_RELEVANT'):"
    )


def build_combined_bestuurlijk_prompt(topic_names: list, gemeente: str, text: str) -> str:
    topics_str = ', '.join(topic_names)
    return (
        "INSTRUCTIE: Begin je antwoord met een pakkende bestuurlijke kop op de EERSTE REGEL "
        "(max 12 woorden, zonder gemeentenaam, geen aanhalingstekens, geen punt aan het eind).\n"
        "Schrijf daarna een bestuurlijk inzicht in drie blokken: "
        "**Samenvatting** (wat er speelt), **Standpunten** (per fractie), **Actiepunten** (concrete vervolgstappen).\n"
        "Corrigeer evidente transcriptiefouten (bijv. 'T66' → 'D66', 'PVA' → 'PvdA').\n\n"
        f"Relevante thema's in dit fragment: {topics_str}\n"
        "Dek alle relevante thema's af. Wees concreet en actionable.\n"
        "Is er niets relevants? Antwoord exact 'NIET_RELEVANT'.\n\n"
        f"Gemeente: {gemeente}\n\n"
        f"TRANSCRIPT:\n{text[:3500]}\n\n"
        "ANTWOORD (eerste regel = koptekst, of exact 'NIET_RELEVANT'):"
    )
