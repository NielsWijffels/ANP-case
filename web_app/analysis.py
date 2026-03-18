"""
Analyse-engine — classificeert transcript-fragmenten op onderwerp en nieuwswaardigheid.

Twee kanalen met verschillende logica:
  - Bestuurlijk: brede relevantie, lage drempel, actionable output.
    Kijkt naar beleidswijzigingen, budgetbeslissingen, interbestuurlijke verhoudingen,
    politieke dynamiek, moties, amendementen.
  - Pers (Nieuwsredactie): alleen nieuwswaardig, hoge drempel, informatief krantenartikel.
    Kijkt naar impact op breed publiek, controverses, grote besluiten.

Keyword-matching altijd beschikbaar (gratis), LLM-samenvattingen optioneel via Ollama.
"""

import json
import os
import time
import requests
from datetime import datetime

from . import config

# ── Keyword sets per onderwerp (9 topics, herindeling) ─────────────────────

TOPIC_KEYWORDS = {
    'bestuur_politiek': [
        'coalitie', 'oppositie', 'motie', 'amendement', 'stemming',
        'aangenomen', 'verworpen', 'unaniem', 'raadsvergadering', 'commissie',
        'portefeuille', 'formatie', 'coalitieakkoord', 'raadsprogramma',
        'wethouder', 'burgemeester', 'griffier', 'gemeentesecretaris',
        'interpellatie', 'spoeddebat', 'schriftelijke vragen', 'toezegging',
        'raadsbesluit', 'collegebesluit', 'bestuursakkoord', 'herindeling',
        'integriteit', 'gedragscode', 'nevenfuncties', 'rekenkamer',
        'gemeenteraadsverkiezingen', 'wantrouwen', 'vertrouwensbreuk',
    ],
    'wonen_ruimte': [
        'woningbouw', 'sociale huur', 'nieuwbouw', 'bouwlocatie', 'betaalbaar wonen',
        'koopwoning', 'flexwoning', 'woningtekort', 'woningcorporatie',
        'woonvisie', 'bouwopgave', 'woningbouwprogramma', 'starterswoning',
        'huurwoning', 'woningmarkt', 'bouwvergunning',
        'bestemmingsplan', 'omgevingsvisie', 'omgevingsplan', 'ruimtelijke ordening',
        'planologie', 'structuurvisie', 'inpassingsplan', 'omgevingsvergunning',
        'bestemmingswijziging', 'grondbeleid', 'buitengebied', 'stedenbouwkundig',
        'woonwijk', 'binnenstedelijk', 'verdichting', 'transformatie',
    ],
    'klimaat_natuur_stikstof': [
        'windmolen', 'windpark', 'zonnepaneel', 'zonneweide', 'warmtenet',
        'energietransitie', 'duurzaamheid', 'klimaatbeleid', 'co2',
        'warmtepomp', 'isolatie', 'gasvrij', 'energieneutraal', 'laadpaal',
        'regionale energiestrategie', 'klimaatadaptatie',
        'stikstof', 'natura 2000', 'biodiversiteit', 'natuurgebied',
        'ecologisch', 'depositie', 'piekbelaster',
        'stikstofuitstoot', 'milieu', 'luchtkwaliteit', 'fijnstof',
        'circulaire economie', 'afvalverwerking', 'recycling',
    ],
    'bereikbaarheid_infra': [
        'provinciale weg', 'openbaar vervoer', 'busverbinding', 'treinstation',
        'fietspad', 'verkeersveiligheid', 'wegonderhoud', 'bereikbaarheid',
        'mobiliteitsplan', 'verkeersknelpunt', 'rotonde', 'snelweg', 'asfalt',
        'trajectcontrole', 'carpoolplaats', 'spoorweg', 'metro', 'tram',
        'waterschap', 'dijkversterking', 'waterpeil', 'overstroming',
        'wateroverlast', 'riolering', 'waterkwaliteit', 'droogte',
        'waterveiligheid', 'gemaal', 'brug', 'tunnel', 'viaduct',
    ],
    'landbouw_platteland': [
        'landbouw', 'agrarisch', 'veehouderij', 'veestapel', 'boerenbedrijf',
        'boerenprotest', 'platteland', 'leefbaarheid', 'krimp',
        'dorpshuis', 'voorzieningenniveau', 'glastuinbouw', 'akkerbouw',
        'biologisch', 'kringlooplandbouw', 'mestbeleid', 'pachter',
        'grondgebonden', 'intensieve veehouderij', 'megastal',
        'plattelandsbeleid', 'dorpsvisie', 'kleine kernen',
    ],
    'economie_innovatie': [
        'werkgelegenheid', 'bedrijventerrein', 'mkb', 'ondernemers',
        'economische ontwikkeling', 'retail', 'winkelcentrum', 'horeca',
        'toerisme', 'arbeidsmarkt', 'vestigingsklimaat',
        'innovatie', 'startup', 'hightech', 'campus', 'brainport',
        'digitalisering', 'data', 'kunstmatige intelligentie',
        'regionale economie', 'exportkracht', 'haven', 'logistiek',
    ],
    'cultuur_sport_samenleving': [
        'monument', 'erfgoed', 'cultureel', 'museum', 'theater',
        'bibliotheek', 'cultuurbeleid', 'restauratie',
        'sporthal', 'zwembad', 'sportvereniging', 'accommodatie',
        'basisschool', 'voortgezet onderwijs', 'schoolgebouw',
        'leerlingenvervoer', 'onderwijshuisvesting', 'passend onderwijs',
        'kinderopvang', 'mbo', 'hbo',
        'jeugdzorg', 'wmo', 'sociaal domein', 'mantelzorg', 'eenzaamheid',
        'armoede', 'schuldhulp', 'ggd', 'ouderenzorg',
        'beschermd wonen', 'maatschappelijke ondersteuning', 'statushouder',
        'subsidie', 'evenement', 'festival', 'wijkaanpak', 'burgerparticipatie',
    ],
    'financien_toezicht': [
        'begroting', 'jaarrekening', 'tekort', 'bezuiniging',
        'gemeentefonds', 'artikel 12', 'preventief toezicht',
        'weerstandsvermogen', 'schuld', 'begrotingstekort', 'ozb',
        'precariobelasting', 'accountantsverklaring',
        'miljoen', 'miljard', 'reserves', 'voorzieningen',
        'financieel perspectief', 'meerjaren', 'kadernota',
    ],
    'veiligheid': [
        'veiligheidsregio', 'politie', 'criminaliteit', 'ondermijning',
        'brandweer', 'rampenplan', 'openbare orde', 'overlast',
        'handhaving', 'drugscriminaliteit', 'cameratoezicht',
        'noodverordening', 'terrorisme', 'radicalisering',
        'cybersecurity', 'fraudebestrijding',
    ],
}

# ── Nieuwswaardigheid (pers-kanaal): breed publiek impact ──────────────────

NEWS_KEYWORDS_PERS = [
    'motie', 'amendement', 'stemming', 'aangenomen', 'verworpen',
    'unaniem', 'breuk', 'crisis', 'aftreden', 'ontslag',
    'incident', 'schandaal', 'integriteit', 'onderzoek',
    'noodmaatregel', 'spoeddebat', 'interpellatie',
    'miljoen', 'miljard', 'protest', 'demonstratie',
    'bezwaar', 'rechtszaak', 'wantrouwen', 'vertrouwensbreuk',
    'explosief', 'schok', 'verrassing',
]

# ── Bestuurlijke relevantie: andere criteria dan pers ──────────────────────

BESTUURLIJK_KEYWORDS = [
    'motie', 'amendement', 'stemming', 'aangenomen', 'verworpen',
    'toezegging', 'bestuursakkoord', 'coalitieakkoord', 'beleidswijziging',
    'raadsbesluit', 'collegebesluit', 'verordening', 'subsidie',
    'begroting', 'jaarrekening', 'kadernota', 'bezuiniging',
    'artikel 12', 'preventief toezicht', 'interbestuurlijk',
    'portefeuilleverdeling', 'wethouder', 'collegevorming',
    'herindeling', 'samenwerkingsverband', 'gemeenschappelijke regeling',
    'zienswijze', 'inspraak', 'raadsvoorstel', 'uitvoeringsprogramma',
    'monitoren', 'evaluatie', 'rekenkamer', 'WOO-verzoek',
    'omgevingsvisie', 'omgevingsplan', 'energiestrategie',
]


def classify_topics(text):
    """Classificeer tekst op de 9 onderwerpen. Returns gesorteerde lijst."""
    if not text:
        return []

    lower = text.lower()
    results = []

    for topic_id, keywords in TOPIC_KEYWORDS.items():
        matches = sum(1 for kw in keywords if kw in lower)
        if matches > 0:
            score = min(matches / max(len(keywords) * 0.3, 1), 1.0)
            topic_name = config.TOPICS.get(topic_id, topic_id)
            results.append({
                'topic': topic_id,
                'name': topic_name,
                'score': round(score, 2),
                'matches': matches,
            })

    results.sort(key=lambda x: x['score'], reverse=True)
    return results


def score_pers(text):
    """Scoor nieuwswaardigheid voor pers-kanaal (breed publiek impact)."""
    if not text:
        return 0.0, []
    lower = text.lower()
    found = [kw for kw in NEWS_KEYWORDS_PERS if kw in lower]
    score = min(len(found) / 3.0, 1.0)
    return round(score, 2), found


def score_bestuurlijk(text):
    """Scoor bestuurlijke relevantie (beleidsimpact, politieke dynamiek)."""
    if not text:
        return 0.0, []
    lower = text.lower()
    found = [kw for kw in BESTUURLIJK_KEYWORDS if kw in lower]
    # Lagere deler: al bij 2 indicatoren hoge score (breder relevant)
    score = min(len(found) / 2.0, 1.0)
    return round(score, 2), found


def analyze_fragment(text, gemeente=None):
    """Analyseer een transcript-fragment voor beide kanalen.

    Returns dict met topic-scores + scores per kanaal.
    """
    topics = classify_topics(text)
    pers_score, pers_indicators = score_pers(text)
    bestuurlijk_score, bestuurlijk_indicators = score_bestuurlijk(text)

    pers_level = config.INTEREST_LEVELS['pers']
    bestuurlijk_level = config.INTEREST_LEVELS['bestuurlijk']

    is_pers_relevant = pers_score >= pers_level['threshold']
    is_bestuurlijk_relevant = bestuurlijk_score >= bestuurlijk_level['threshold']

    # Extractieve samenvatting (fallback als LLM niet beschikbaar)
    sentences = [s.strip() for s in text.replace('!', '.').replace('?', '.').split('.')
                 if len(s.strip()) > 20]
    all_kw = NEWS_KEYWORDS_PERS + BESTUURLIJK_KEYWORDS + [
        kw for kws in TOPIC_KEYWORDS.values() for kw in kws]
    scored_sents = []
    for sent in sentences:
        sl = sent.lower()
        sc = sum(1 for kw in all_kw if kw in sl)
        if sc > 0:
            scored_sents.append((sc, sent))
    scored_sents.sort(reverse=True)
    summary = '. '.join(s[1] for s in scored_sents[:2]) + '.' if scored_sents else ''

    return {
        'topics': topics,
        'pers_score': pers_score,
        'pers_indicators': pers_indicators,
        'is_pers_relevant': is_pers_relevant,
        'bestuurlijk_score': bestuurlijk_score,
        'bestuurlijk_indicators': bestuurlijk_indicators,
        'is_bestuurlijk_relevant': is_bestuurlijk_relevant,
        'summary': summary,
        'gemeente': gemeente,
    }


# ── LLM samenvatting generatie ─────────────────────────────────────────────

def _call_llm(prompt, max_tokens=500):
    """Roep lokale Ollama LLM aan. Returns tekst of None bij fout."""
    try:
        resp = requests.post(
            f"{config.LLM_BASE_URL}/api/generate",
            json={
                'model': config.LLM_MODEL,
                'prompt': prompt,
                'stream': False,
                'options': {'num_predict': max_tokens, 'temperature': 0.3},
            },
            timeout=120,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get('response', '').strip()
    except Exception:
        pass
    return None


def generate_pers_summary(text, topic_name, gemeente):
    """Genereer pers-samenvatting via LLM (max 100 woorden krantenartikel)."""
    prompt = config.PROMPT_PERS.format(
        topic_name=topic_name,
        gemeente=gemeente or 'onbekende gemeente',
        text=text[:3000],
    )
    result = _call_llm(prompt, max_tokens=250)
    if result and result != 'NIET_RELEVANT':
        return result
    return None


def generate_bestuurlijk_summary(text, topic_name, gemeente):
    """Genereer bestuurlijk inzicht via LLM (actionable, max 200 woorden)."""
    prompt = config.PROMPT_BESTUURLIJK.format(
        topic_name=topic_name,
        gemeente=gemeente or 'onbekende gemeente',
        text=text[:3000],
    )
    result = _call_llm(prompt, max_tokens=500)
    if result and result != 'NIET_RELEVANT':
        return result
    return None


# ── Iteratieve analyse (elke 5 min, rolling 30 min window) ────────────────

def analyze_window_for_all_topics(text, gemeente=None, meeting_id=None,
                                  livestream_url=None):
    """Analyseer een 30-minuten rolling window voor alle onderwerpen en kanalen.

    Produceert alerts per topic × kanaal combinatie.
    Slaat resultaten op in temp_alerts/ map (max 1 uur bewaard).

    Returns: lijst van gegenereerde alerts [{topic, level, title, summary, ...}]
    """
    if not text or len(text.strip()) < 50:
        return []

    fragment = analyze_fragment(text, gemeente)
    alerts = []
    timestamp = datetime.now().isoformat()

    for topic_data in fragment['topics']:
        topic_id = topic_data['topic']
        topic_name = topic_data['name']
        topic_score = topic_data['score']

        # ── Pers-kanaal ──
        if fragment['is_pers_relevant'] and topic_score >= 0.2:
            pers_summary = generate_pers_summary(text, topic_name, gemeente)
            if not pers_summary:
                # Fallback: extractieve samenvatting
                pers_summary = fragment['summary'][:300] if fragment['summary'] else None

            if pers_summary:
                alert = {
                    'topic': topic_id,
                    'topic_name': topic_name,
                    'level': 'pers',
                    'title': f"{gemeente}: {topic_name}",
                    'summary': pers_summary,
                    'score': fragment['pers_score'],
                    'indicators': fragment['pers_indicators'],
                    'gemeente': gemeente,
                    'meeting_id': meeting_id,
                    'livestream_url': livestream_url,
                    'timestamp': timestamp,
                    'has_transcript': True,
                }
                alerts.append(alert)

        # ── Bestuurlijk kanaal ──
        if fragment['is_bestuurlijk_relevant'] and topic_score >= 0.15:
            bestuurlijk_summary = generate_bestuurlijk_summary(text, topic_name, gemeente)
            if not bestuurlijk_summary:
                bestuurlijk_summary = fragment['summary'][:500] if fragment['summary'] else None

            if bestuurlijk_summary:
                alert = {
                    'topic': topic_id,
                    'topic_name': topic_name,
                    'level': 'bestuurlijk',
                    'title': f"{gemeente}: {topic_name}",
                    'summary': bestuurlijk_summary,
                    'score': fragment['bestuurlijk_score'],
                    'indicators': fragment['bestuurlijk_indicators'],
                    'gemeente': gemeente,
                    'meeting_id': meeting_id,
                    'livestream_url': livestream_url,
                    'timestamp': timestamp,
                    'has_transcript': True,
                }
                alerts.append(alert)

    # Sla alerts op in temp map (1 uur bewaard)
    _save_temp_alerts(alerts, meeting_id)
    _cleanup_old_temp_alerts()

    return alerts


def _save_temp_alerts(alerts, meeting_id):
    """Sla alerts op in temp map voor terugzoeken (max 1 uur)."""
    os.makedirs(config.TEMP_DIR, exist_ok=True)
    for alert in alerts:
        fname = f"{alert['level']}_{alert['topic']}_{int(time.time())}.json"
        path = os.path.join(config.TEMP_DIR, fname)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(alert, f, ensure_ascii=False, indent=2)


def _cleanup_old_temp_alerts():
    """Verwijder temp alerts ouder dan TTL (standaard 1 uur)."""
    if not os.path.exists(config.TEMP_DIR):
        return
    now = time.time()
    for fname in os.listdir(config.TEMP_DIR):
        fpath = os.path.join(config.TEMP_DIR, fname)
        if os.path.isfile(fpath):
            age = now - os.path.getmtime(fpath)
            if age > config.TEMP_ALERT_TTL:
                os.remove(fpath)


def get_temp_alerts(level=None, topic=None, limit=50):
    """Haal recente temp alerts op (voor terugzoeken binnen 1 uur)."""
    if not os.path.exists(config.TEMP_DIR):
        return []
    alerts = []
    for fname in sorted(os.listdir(config.TEMP_DIR), reverse=True):
        if not fname.endswith('.json'):
            continue
        if level and not fname.startswith(level):
            continue
        fpath = os.path.join(config.TEMP_DIR, fname)
        try:
            with open(fpath, 'r', encoding='utf-8') as f:
                alert = json.load(f)
                if topic and alert.get('topic') != topic:
                    continue
                alerts.append(alert)
                if len(alerts) >= limit:
                    break
        except (json.JSONDecodeError, IOError):
            continue
    return alerts
