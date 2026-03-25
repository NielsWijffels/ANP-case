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

import asyncio
import json
import os
import time
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


# ── LLM samenvatting generatie (Ollama lokaal, gratis) ────────────────────

def _call_ollama(prompt: str, max_tokens: int = 600, temperature: float = 0.3) -> 'str | None':
    """Roep lokale Ollama aan. Vereist: `ollama run mistral` (of ander model)."""
    import requests as _req
    try:
        resp = _req.post(
            f"{config.LLM_BASE_URL}/api/generate",
            json={
                'model': config.LLM_MODEL,
                'prompt': prompt,
                'stream': False,
                'options': {'num_predict': max_tokens, 'temperature': temperature},
            },
            timeout=600,
        )
        if resp.status_code == 200:
            text = resp.json().get('response', '').strip()
            return text if text and text != 'NIET_RELEVANT' else None
    except Exception:
        pass
    return None


async def _call_claude(prompt: str, max_tokens: int = 600) -> 'str | None':
    """Wrapper zodat generate-functies async blijven. Draait Ollama in threadpool."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call_ollama, prompt, max_tokens)


async def generate_pers_summary(text: str, topic_id: str, gemeente: str) -> 'str | None':
    """Genereer pers-samenvatting via Ollama (max 250 woorden nieuwsbericht)."""
    template = config.PROMPTS_PERS.get(topic_id, config.PROMPTS_PERS['bestuur_politiek'])
    prompt = template.format(gemeente=gemeente or 'onbekende gemeente', text=text[:4000])
    return await _call_claude(prompt, max_tokens=700)


async def generate_bestuurlijk_summary(text: str, topic_id: str, gemeente: str) -> 'str | None':
    """Genereer bestuurlijk inzicht via Ollama (Samenvatting / Standpunten / Actiepunten)."""
    template = config.PROMPTS_BESTUURLIJK.get(topic_id, config.PROMPTS_BESTUURLIJK['bestuur_politiek'])
    prompt = template.format(gemeente=gemeente or 'onbekende gemeente', text=text[:4000])
    return await _call_claude(prompt, max_tokens=1200)


# ── Headline extractor ─────────────────────────────────────────────────────

def _extract_headline(text: str, fallback: str) -> tuple:
    """Extract eerste regel als koptekst, rest als body.

    LLM-prompts vragen om de koptekst als EERSTE REGEL zonder aanhalingstekens.
    Geeft (headline, body) terug.
    """
    import re as _re
    if not text:
        return fallback, ''
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if not lines:
        return fallback, text
    # Generieke LLM-koppen overslaan — zoek de eerste echte inhoudszin
    SKIP_HEADERS = {'samenvatting', 'summary', 'overzicht', 'analyse', 'nieuws', 'bericht',
                    'standpunten', 'partijstandpunten', 'actiepunten', 'inleiding', 'conclusie'}
    for i, line in enumerate(lines):
        headline = line
        # Strip markdown ornaments / LLM artefacten
        headline = _re.sub(r'^#+\s*', '', headline)
        headline = _re.sub(r'^\*\*(.+)\*\*$', r'\1', headline)
        headline = _re.sub(r'^\*(.+)\*$', r'\1', headline)
        headline = headline.strip('"\':.').strip()
        # Kop mag niet generiek, te kort of te lang zijn
        if not headline or len(headline) < 10 or len(headline) > 140:
            continue
        if headline.lower().rstrip(':') in SKIP_HEADERS:
            continue
        body = '\n'.join(lines[i+1:]).strip() if i+1 < len(lines) else ''
        return headline, body or text
    return fallback, text


# ── Deduplicatie ───────────────────────────────────────────────────────────

def _tokenize(text):
    """Simpele tokenisatie: lowercase woorden, min 3 chars."""
    return set(w for w in text.lower().split() if len(w) >= 3)


def _similarity(text_a, text_b):
    """Jaccard-similariteit tussen twee teksten (0.0 – 1.0)."""
    if not text_a or not text_b:
        return 0.0
    tokens_a = _tokenize(text_a)
    tokens_b = _tokenize(text_b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _new_keywords(new_text, old_text):
    """Vind relevante keywords in new_text die niet in old_text voorkomen."""
    all_kw = set(NEWS_KEYWORDS_PERS + BESTUURLIJK_KEYWORDS)
    new_lower = new_text.lower()
    old_lower = old_text.lower()
    return [kw for kw in all_kw if kw in new_lower and kw not in old_lower]


async def _llm_check_overlap(new_summary, prev_summary, prev_id, kanaal, gemeente):
    """LLM-gebaseerde overlap check tussen nieuw artikel en voorgaand artikel.

    Vraagt de LLM of er een relevante toevoeging is ten opzichte van het vorige artikel.
    De LLM krijgt beide samenvattingen en beslist semantisch — niet alleen op woordniveau.

    Returns:
        ('skip', None, None)              — geen relevante toevoeging
        ('update', update_text, prev_id)  — relevante toevoeging, korte updatetekst
        ('new', None, None)               — geen overlap, geheel nieuw artikel
    """
    if not prev_summary:
        return ('new', None, None)

    stijl = (
        "journalistieke, feitelijke stijl (max 15 woorden, begin met werkwoord of feit)"
        if kanaal == 'pers' else
        "bestuurlijke, actionable stijl (max 15 woorden, begin met wat er concreet veranderd is)"
    )

    prompt = f"""Je vergelijkt twee samenvattingen van hetzelfde debat in {gemeente}.

VORIG ARTIKEL:
{prev_summary[:600]}

NIEUW ARTIKEL:
{new_summary[:600]}

Beoordeel: bevat het NIEUWE ARTIKEL relevante nieuwe informatie ten opzichte van het VORIGE ARTIKEL?
Let op semantische overlap, niet alleen woordovereenkomst. Een stemuitslag, nieuw standpunt, \
gewijzigd cijfer of nieuw besluit telt als relevante toevoeging.

Antwoord met precies één van de volgende opties:
- GEEN_TOEVOEGING
- UPDATE: [één zin in {stijl}]"""

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _call_ollama, prompt, 80)

    if result:
        result = result.strip()
        if result.upper().startswith('GEEN_TOEVOEGING'):
            return ('skip', None, None)
        if result.upper().startswith('UPDATE:'):
            update_text = result[7:].strip()
            if update_text:
                return ('update', update_text, prev_id)

    # Fallback op Jaccard als LLM geen bruikbaar antwoord geeft
    sim = _similarity(new_summary, prev_summary)
    if sim >= 0.70:
        return ('skip', None, None)
    new_kw = _new_keywords(new_summary, prev_summary)
    if sim >= 0.35 and len(new_kw) >= 2:
        return ('update', None, prev_id)
    if sim >= 0.35:
        return ('skip', None, None)
    return ('new', None, None)


async def check_dedup(new_summary, topic_id, level, recent_alerts, kanaal, gemeente):
    """Check deduplicatie voor één (topic, level) combinatie.

    Vergelijkt met het meest recente vorige artikel voor dit topic+level.
    Returns: ('skip'|'update'|'new', update_text|None, prev_id|None)
    """
    for prev in recent_alerts:
        if prev.get('topic') != topic_id or prev.get('level') != level:
            continue
        prev_summary = prev.get('summary', '')
        prev_id = prev.get('id')

        # Snelle Jaccard-check vóór LLM: bij hoge similariteit direct skippen
        quick_sim = _similarity(new_summary, prev_summary)
        if quick_sim >= 0.55:
            return ('skip', None, None)

        return await _llm_check_overlap(new_summary, prev_summary, prev_id, kanaal, gemeente)

    return ('new', None, None)


# ── Iteratieve analyse (elke 5 min, rolling 30 min window) ────────────────

async def analyze_window_for_all_topics(text, gemeente=None, meeting_id=None,
                                        livestream_url=None, recent_alerts=None):
    """Analyseer een 30-minuten rolling window voor alle onderwerpen en kanalen.

    Stap 1: Keyword-classificatie (gratis pre-filter)
    Stap 2: Sonnet-calls parallel voor alle relevante topic × kanaal combinaties

    Returns: lijst van gegenereerde alerts [{topic, level, title, summary, ...}]
    """
    if not text or len(text.strip()) < 50:
        return []

    if recent_alerts is None:
        recent_alerts = []

    fragment = analyze_fragment(text, gemeente)
    timestamp = datetime.now().isoformat()

    # ── Bepaal welke (topic, kanaal) combinaties uitgewerkt worden ──────────
    taken = []  # [(topic_id, topic_name, topic_score, kanaal)]
    for topic_data in fragment['topics']:
        topic_id = topic_data['topic']
        topic_name = topic_data['name']
        topic_score = topic_data['score']
        if fragment['is_pers_relevant'] and topic_score >= 0.2:
            taken.append((topic_id, topic_name, topic_score, 'pers'))
        if fragment['is_bestuurlijk_relevant'] and topic_score >= 0.15:
            taken.append((topic_id, topic_name, topic_score, 'bestuurlijk'))

    if not taken:
        return []

    # ── Groepeer matching topics per kanaal ───────────────────────────────────
    pers_topics   = [(tid, tname, tscore) for tid, tname, tscore, k in taken if k == 'pers']
    best_topics   = [(tid, tname, tscore) for tid, tname, tscore, k in taken if k == 'bestuurlijk']

    # ── Één gecombineerde LLM-call per kanaal ─────────────────────────────────
    async def _genereer_combined(topics_list, kanaal):
        topic_names = [tname for _, tname, _ in topics_list]
        if kanaal == 'pers':
            prompt = config.build_combined_pers_prompt(topic_names, gemeente or '', text)
        else:
            prompt = config.build_combined_bestuurlijk_prompt(topic_names, gemeente or '', text)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _call_ollama, prompt, 900)

    tasks = []
    if pers_topics:
        tasks.append(('pers', pers_topics, _genereer_combined(pers_topics, 'pers')))
    if best_topics:
        tasks.append(('bestuurlijk', best_topics, _genereer_combined(best_topics, 'bestuurlijk')))

    resultaten = await asyncio.gather(*[t[2] for t in tasks])

    # ── Deduplicatie en verwerking ────────────────────────────────────────────
    alerts = []
    for (kanaal, topics_list, _), summary in zip(tasks, resultaten):
        if not summary or summary.strip() == 'NIET_RELEVANT':
            continue

        # Gebruik het eerste topic als primair topic (hoogste score)
        primary_topic_id   = topics_list[0][0]
        primary_topic_name = topics_list[0][1]
        all_topic_ids      = [tid for tid, _, _ in topics_list]

        # Deduplicatie: vergelijk met vorig artikel voor dit level
        prev_for_level = next(
            (p for p in reversed(recent_alerts) if p.get('level') == kanaal),
            None
        )
        if prev_for_level:
            sim = _similarity(summary, prev_for_level.get('summary', ''))
            if sim >= 0.55:
                continue

        score = fragment['pers_score'] if kanaal == 'pers' else fragment['bestuurlijk_score']
        indicators = fragment['pers_indicators'] if kanaal == 'pers' else fragment['bestuurlijk_indicators']

        headline, body = _extract_headline(summary, primary_topic_name)

        alert = {
            'action': 'new',
            'prev_id': None,
            'update_text': None,
            'topic': primary_topic_id,
            'topics': all_topic_ids,          # Alle matching topics
            'topic_name': primary_topic_name,
            'level': kanaal,
            'title': f"{gemeente}: {headline}",
            'summary': body or summary,
            'score': score,
            'indicators': indicators,
            'gemeente': gemeente,
            'meeting_id': meeting_id,
            'livestream_url': livestream_url,
            'timestamp': timestamp,
            'has_transcript': True,
        }
        alerts.append(alert)
        recent_alerts.append({**alert, 'summary': summary, 'id': None})

    _save_temp_alerts(alerts, meeting_id)
    _cleanup_old_temp_alerts()

    return alerts


async def generate_meeting_summary(meeting_id: int, gemeente: str, articles: list) -> 'str | None':
    """Genereer een eindverslag van een vergadering op basis van alle gegenereerde artikelen.

    Combineert pers- en bestuurlijk perspectief voor een breed publiek.
    Maximaal 500 woorden; minder is beter als de inhoud het toelaat.
    """
    if not articles:
        return None

    # Bouw een overzicht van alle artikelen
    artikelen_tekst = []
    for a in articles:
        level_label = 'Pers' if a.get('level') == 'pers' else 'Bestuurlijk'
        topic_label = config.TOPICS.get(a.get('topic', ''), a.get('topic', ''))
        artikelen_tekst.append(
            f"[{level_label} — {topic_label}]\n{a.get('title','')}\n{a.get('body') or a.get('summary','')}"
        )
        # Voeg eventuele updates toe
        updates = a.get('updates')
        if updates:
            if isinstance(updates, str):
                try:
                    import json as _json
                    updates = _json.loads(updates)
                except Exception:
                    updates = []
            for u in updates:
                artikelen_tekst.append(f"  ↳ Update: {u}")

    gecombineerd = '\n\n'.join(artikelen_tekst)

    prompt = f"""Je schrijft een beknopt vergaderverslag van een gemeenteraadsvergadering in {gemeente}.

Hieronder staan alle nieuwsberichten en bestuurlijke analyses die tijdens de vergadering zijn gegenereerd:

{gecombineerd[:4000]}

Schrijf een helder, feitelijk verslag van maximaal 500 woorden voor een breed publiek (journalisten én bestuurders).
Noem alle belangrijke besluiten, standpunten en ontwikkelingen. Gebruik geen bulletpoints, maar lopende tekst met \
korte alinea's per onderwerp. Als je minder woorden nodig hebt is dat beter.
Schrijf in de derde persoon (niet "wij"). Geen inleiding of afsluiting nodig — begin direct met de inhoud."""

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call_ollama, prompt, 900)


async def generate_meeting_schets(
    meeting_type: str,
    gemeente: str,
    datum: str,
    titel: str,
    agenda_items: list,
    doc_titles: list,
    doc_excerpts: list = None,
    prev_summary: str = '',
    prev_titel: str = '',
    prev_schets_list: list = None,
) -> 'str | None':
    """Genereer een vooruitblik op een vergadering: puur op basis van aangeleverde data."""

    agenda_str = '\n'.join(
        f"- {it.get('number','') + '. ' if it.get('number') else ''}{it.get('title','')}"
        for it in agenda_items[:20]
        if it.get('title')
    ) or None

    # Bijlagen: gebruik geëxtraheerde tekst als die er is, anders alleen titel
    if doc_excerpts:
        docs_str = '\n\n'.join(
            f"[{d['title']}]\n{d['text'][:800]}"
            for d in (doc_excerpts or [])[:4]
            if d.get('text')
        ) or None
    elif doc_titles:
        docs_str = '\n'.join(f"- {t}" for t in doc_titles[:10])
    else:
        docs_str = None

    # Vergadertype label voor context
    if 'raad' in (meeting_type or '').lower():
        type_label = 'raadsvergadering (besluitvorming)'
    elif any(w in (meeting_type or '').lower() for w in ['commissie', 'committee']):
        type_label = 'commissievergadering (voorbereiding/debat, geen finale besluitvorming)'
    else:
        type_label = meeting_type or 'vergadering'

    # Vorige vergadering — alleen meegeven wat we echt weten
    if prev_summary and prev_titel:
        prev_block = f"""
VORIGE VERGADERING — {prev_titel}:
(Gebruik onderstaande samenvatting als enige bron voor de context-alinea. Verzin niets.)
{prev_summary[:600]}"""
        prev_instruction = (
            "2. **Vorige vergadering** — vat in 1-2 zinnen samen wat er de vorige keer speelde, "
            "puur gebaseerd op de samenvatting hierboven. Citeer geen namen die niet in de samenvatting staan."
        )
    elif prev_titel:
        prev_block = f"\nVOORGAANDE VERGADERING: {prev_titel}"
        prev_instruction = (
            f"2. **Vorige vergadering** — de voorgaande vergadering was '{prev_titel}'. "
            "Schrijf alleen deze titel. Voeg geen verdere inhoud toe want die is niet beschikbaar."
        )
    else:
        prev_block = ''
        prev_instruction = "2. Sla de alinea over vorige vergadering volledig over — er zijn geen gegevens."

    # Detecteer ceremoniële/niet-substantiële vergaderingen
    CEREMONIAL_KEYWORDS = [
        'afscheid', 'installatie', 'beëdiging', 'inauguratie',
        'nieuwjaar', 'opening raad', 'kennismaking', 'constituerende',
    ]
    is_ceremonial = any(kw in (titel or '').lower() for kw in CEREMONIAL_KEYWORDS)

    # Eerdere schets — alle typen (raad + commissie informeren elkaar), gelabeld
    prev_schets_block = ''
    if prev_schets_list:
        schets_parts = []
        for ps in prev_schets_list[:3]:
            ps_type = ps.get('type', '')
            label = f"{ps_type} ({ps['datum']}) — {ps['titel']}"
            schets_parts.append(f"[{label}]\n{ps['schets'][:500]}")
        prev_schets_block = (
            "\n\nEERDERE VERGADERINGEN (raad én commissie — gebruik als context; "
            "citeer alleen wat erin staat; let op doorverwijzingen naar komende punten):\n\n"
            + '\n\n---\n\n'.join(schets_parts)
        )

    sections = []
    if agenda_str:
        sections.append(f"AGENDAPUNTEN:\n{agenda_str}")
    if docs_str:
        sections.append(f"BIJLAGEN (titels):\n{docs_str}")

    data_block = '\n\n'.join(sections) if sections else 'Geen agendadata beschikbaar.'

    if is_ceremonial:
        prompt = f"""Je bent een feitelijk rapporteur. Je schrijft UITSLUITEND op basis van de onderstaande gegevens.

STRIKTE REGELS:
- Gebruik ALLEEN informatie die letterlijk in de gegevens hieronder staat.
- Verzin GEEN feiten, namen of achtergronden.

VERGADERING: {titel}
GEMEENTE: {gemeente}
DATUM: {datum}
TYPE: {type_label}

{data_block}
{prev_block}

Dit is een ceremoniële vergadering (afscheid, installatie of soortgelijk).
Schrijf een korte schets van maximaal 80 woorden: beschrijf het doel van de bijeenkomst op basis van de titel en beschikbare data. Geen speculatie over wat er gezegd wordt.

Begin direct. Geen inleiding of afsluiting."""
    else:
        context_instruction = (
            "4. **Context** — noem in maximaal twee zinnen relevante punten uit eerdere "
            "vergaderingen (raad óf commissie) die direct verband houden met de huidige agenda, "
            "inclusief punten die in een eerdere vergadering werden doorverwezen naar déze vergadering. "
            "Alleen opnemen als er een expliciet verband in de data staat."
            if prev_schets_list else ''
        )
        prompt = f"""Je bent een feitelijk rapporteur. Je schrijft UITSLUITEND op basis van de onderstaande gegevens.

STRIKTE REGELS:
- Gebruik ALLEEN informatie die letterlijk in de gegevens hieronder staat.
- Verzin GEEN feiten, namen, besluiten, standpunten of achtergronden.
- Als iets niet in de gegevens staat, schrijf je het niet.
- Geen speculatie. Geen aannames. Geen algemene politieke kennis toevoegen.
- Eerdere vergaderschetsen (raad én commissie) mogen als context dienen, maar verzin geen vervolg.
- Let speciaal op: als in een eerdere schets staat dat een onderwerp in déze vergadering behandeld wordt, neem dat dan mee.

VERGADERING: {titel}
GEMEENTE: {gemeente}
DATUM: {datum}
TYPE: {type_label}

{data_block}
{prev_block}{prev_schets_block}

Schrijf een feitelijke schets van maximaal 200 woorden in alinea's met **vetgedrukte** koppen:
1. **Agenda** — parafraseer in 1-2 zinnen waar de vergadering over zal gaan op basis van de agendapunten. Noem geen exacte titels, maar geef een beeld van de thema's en wat er speelt.
{prev_instruction}
3. **Type vergadering** — beschrijf in één zin wat dit type vergadering betekent (besluitvorming of voorbereiding).
{context_instruction}

Begin direct met de eerste alinea. Geen inleiding of afsluiting."""

    loop = asyncio.get_event_loop()
    # Gebruik het betere schets-model (standaard: mistral) voor pre-generated schets
    def _call_schets():
        import requests as _req
        try:
            resp = _req.post(
                f"{config.LLM_BASE_URL}/api/generate",
                json={
                    'model': config.LLM_SCHETS_MODEL,
                    'prompt': prompt,
                    'stream': False,
                    'options': {'num_predict': 500, 'temperature': 0.0, 'num_ctx': 8192},
                    'keep_alive': 0,   # laad model direct uit na generatie → RAM vrij
                },
                timeout=600,
            )
            if resp.status_code == 200:
                return resp.json().get('response', '').strip() or None
        except Exception:
            pass
        return None
    return await loop.run_in_executor(None, _call_schets)


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
