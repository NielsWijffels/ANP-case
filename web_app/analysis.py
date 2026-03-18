"""
Analyse-engine — classificeert transcript-fragmenten op onderwerp en nieuwswaardigheid.
Gebruikt keyword-matching (altijd beschikbaar, gratis) met optionele LLM-integratie.
"""

from . import config

# ── Keyword sets per onderwerp ─────────────────────────────────────────────

TOPIC_KEYWORDS = {
    'ruimtelijke_ordening': [
        'bestemmingsplan', 'omgevingsvisie', 'omgevingsplan', 'ruimtelijke ordening',
        'planologie', 'structuurvisie', 'inpassingsplan', 'omgevingsvergunning',
        'bestemmingswijziging', 'grondbeleid', 'buitengebied', 'stedenbouwkundig',
    ],
    'woningbouw': [
        'woningbouw', 'sociale huur', 'nieuwbouw', 'bouwlocatie', 'betaalbaar wonen',
        'koopwoning', 'flexwoning', 'woningtekort', 'woningcorporatie',
        'woonvisie', 'bouwopgave', 'woningbouwprogramma', 'starterswoning',
        'huurwoning', 'woningmarkt', 'bouwvergunning',
    ],
    'infrastructuur': [
        'provinciale weg', 'openbaar vervoer', 'busverbinding', 'treinstation',
        'fietspad', 'verkeersveiligheid', 'wegonderhoud', 'bereikbaarheid',
        'mobiliteitsplan', 'verkeersknelpunt', 'rotonde', 'snelweg', 'asfalt',
        'trajectcontrole', 'carpoolplaats',
    ],
    'energie_klimaat': [
        'windmolen', 'windpark', 'zonnepaneel', 'zonneweide', 'warmtenet',
        'energietransitie', 'duurzaamheid', 'klimaatbeleid', 'co2',
        'warmtepomp', 'isolatie', 'gasvrij', 'energieneutraal', 'laadpaal',
        'regionale energiestrategie',
    ],
    'stikstof_natuur': [
        'stikstof', 'natura 2000', 'biodiversiteit', 'natuurgebied',
        'ecologisch', 'boerenprotest', 'landbouw', 'veestapel',
        'stikstofuitstoot', 'depositie', 'piekbelaster', 'agrarisch',
    ],
    'financien': [
        'begroting', 'jaarrekening', 'tekort', 'bezuiniging',
        'gemeentefonds', 'artikel 12', 'preventief toezicht',
        'weerstandsvermogen', 'schuld', 'begrotingstekort', 'ozb',
        'precariobelasting', 'accountantsverklaring',
    ],
    'veiligheid': [
        'veiligheidsregio', 'politie', 'criminaliteit', 'ondermijning',
        'brandweer', 'rampenplan', 'openbare orde', 'overlast',
        'handhaving', 'drugscriminaliteit', 'cameratoezicht',
    ],
    'zorg_welzijn': [
        'jeugdzorg', 'wmo', 'sociaal domein', 'mantelzorg', 'eenzaamheid',
        'armoede', 'schuldhulp', 'ggd', 'gezondheidszorg', 'ouderenzorg',
        'beschermd wonen', 'maatschappelijke ondersteuning', 'statushouder',
    ],
    'economie': [
        'werkgelegenheid', 'bedrijventerrein', 'mkb', 'ondernemers',
        'economische ontwikkeling', 'retail', 'winkelcentrum', 'horeca',
        'toerisme', 'arbeidsmarkt', 'vestigingsklimaat',
    ],
    'water': [
        'waterschap', 'dijkversterking', 'waterpeil', 'overstroming',
        'wateroverlast', 'klimaatadaptatie', 'riolering', 'waterkwaliteit',
        'droogte', 'waterveiligheid', 'gemaal',
    ],
    'onderwijs': [
        'basisschool', 'voortgezet onderwijs', 'schoolgebouw',
        'leerlingenvervoer', 'onderwijshuisvesting', 'passend onderwijs',
        'kinderopvang', 'mbo', 'hbo',
    ],
    'cultuur': [
        'monument', 'erfgoed', 'cultureel', 'museum', 'theater',
        'bibliotheek', 'cultuurbeleid', 'subsidie', 'restauratie',
    ],
}

# Nieuwswaardigheidsindicatoren
NEWS_KEYWORDS = [
    'motie', 'amendement', 'stemming', 'aangenomen', 'verworpen',
    'unaniem', 'coalitie', 'oppositie', 'breuk', 'crisis',
    'aftreden', 'ontslag', 'wethouder', 'burgemeester',
    'incident', 'schandaal', 'integriteit', 'onderzoek',
    'noodmaatregel', 'spoeddebat', 'interpellatie',
    'miljoen', 'miljard', 'explosief', 'schok', 'verrassing',
    'protest', 'demonstratie', 'bezwaar', 'rechtszaak',
    'wantrouwen', 'motie van wantrouwen', 'vertrouwensbreuk',
]


def classify_topics(text):
    """Classificeer een stuk tekst op onderwerpen.

    Returns: lijst van (topic_id, topic_naam, score) gesorteerd op score.
    Score is het aantal keyword-matches genormaliseerd.
    """
    if not text:
        return []

    lower = text.lower()
    results = []

    for topic_id, keywords in TOPIC_KEYWORDS.items():
        matches = sum(1 for kw in keywords if kw in lower)
        if matches > 0:
            # Score: matches / totaal keywords, gecapped op 1.0
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


def score_newsworthiness(text):
    """Scoor een stuk tekst op nieuwswaardigheid.

    Returns: score (0.0-1.0) en lijst van gevonden indicatoren.
    """
    if not text:
        return 0.0, []

    lower = text.lower()
    found = [kw for kw in NEWS_KEYWORDS if kw in lower]
    score = min(len(found) / 3.0, 1.0)  # 3+ indicatoren = max score
    return round(score, 2), found


def analyze_fragment(text, gemeente=None):
    """Analyseer een transcript-fragment volledig.

    Returns dict met:
    - topics: lijst van gedetecteerde onderwerpen met scores
    - news_score: nieuwswaardigheid (0-1)
    - news_indicators: welke keywords gevonden
    - relevant: of het fragment relevant genoeg is
    - summary: korte extractie van kernzin(nen)
    """
    topics = classify_topics(text)
    news_score, news_indicators = score_newsworthiness(text)

    # Bepaal of fragment relevant is
    has_relevant_topic = any(t['score'] >= config.SECTOR_SCORE_THRESHOLD for t in topics)
    is_newsworthy = news_score >= config.NEWS_SCORE_THRESHOLD

    # Eenvoudige extractieve samenvatting: pak zinnen met de meeste keywords
    sentences = [s.strip() for s in text.replace('!', '.').replace('?', '.').split('.') if len(s.strip()) > 20]
    scored_sentences = []
    lower_text = text.lower()
    all_keywords = NEWS_KEYWORDS + [kw for kws in TOPIC_KEYWORDS.values() for kw in kws]
    for sent in sentences:
        sent_lower = sent.lower()
        sent_score = sum(1 for kw in all_keywords if kw in sent_lower)
        if sent_score > 0:
            scored_sentences.append((sent_score, sent))
    scored_sentences.sort(reverse=True)
    summary = '. '.join(s[1] for s in scored_sentences[:2]) + '.' if scored_sentences else ''

    return {
        'topics': topics,
        'news_score': news_score,
        'news_indicators': news_indicators,
        'is_newsworthy': is_newsworthy,
        'has_relevant_topic': has_relevant_topic,
        'relevant': has_relevant_topic or is_newsworthy,
        'summary': summary,
        'gemeente': gemeente,
    }
