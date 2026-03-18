"""
FastAPI server — REST API + statische files voor de PWA.
Draait op Colab of lokaal, eenvoudig te porten naar productie.
"""
import asyncio
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from . import database as db
from . import analysis

# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(title="RANST", version="1.0.0")

# SSE clients voor real-time alerts
_sse_clients: Dict[int, List[asyncio.Queue]] = {}

# ── Auth helpers ───────────────────────────────────────────────────────────

def _get_token(request: Request) -> Optional[str]:
    auth = request.headers.get('Authorization', '')
    if auth.startswith('Bearer '):
        return auth[7:]
    return request.cookies.get('token')


def _require_user(request: Request) -> dict:
    token = _get_token(request)
    if not token:
        raise HTTPException(401, "Niet ingelogd")
    user = db.get_user_by_token(token)
    if not user:
        raise HTTPException(401, "Ongeldige sessie")
    return user


# ── Pydantic models ───────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ''
    role: str = 'ps'

class LoginRequest(BaseModel):
    email: str
    password: str

class PreferencesRequest(BaseModel):
    topics: dict = {}      # {topic_id: level} bijv. {'wonen_ruimte': 'bestuurlijk'}
    gemeenten: List[str] = []

# ── Auth endpoints ─────────────────────────────────────────────────────────

@app.post("/api/register")
def register(req: RegisterRequest):
    if len(req.password) < 4:
        raise HTTPException(400, "Wachtwoord te kort")
    user = db.create_user(req.email, req.password, req.name, req.role)
    if not user:
        raise HTTPException(400, "Email al in gebruik")
    token = db.create_token(user['id'])
    return {"user": user, "token": token}


@app.post("/api/login")
def login(req: LoginRequest):
    user = db.authenticate(req.email, req.password)
    if not user:
        raise HTTPException(401, "Onjuiste inloggegevens")
    token = db.create_token(user['id'])
    return {
        "user": {"id": user['id'], "email": user['email'],
                 "name": user['name'], "role": user['role']},
        "token": token,
    }


@app.post("/api/logout")
def logout(request: Request):
    token = _get_token(request)
    if token:
        db.delete_token(token)
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(_require_user)):
    return user


# ── Preferences ────────────────────────────────────────────────────────────

@app.get("/api/preferences")
def get_prefs(user: dict = Depends(_require_user)):
    return db.get_preferences(user['id'])


@app.put("/api/preferences")
def set_prefs(req: PreferencesRequest, user: dict = Depends(_require_user)):
    db.set_preferences(user['id'], req.topics, req.gemeenten)
    return db.get_preferences(user['id'])


# ── Topics & gemeenten ────────────────────────────────────────────────────

@app.get("/api/topics")
def get_topics():
    return {
        'topics': config.TOPICS,
        'interest_levels': {
            k: {'label': v['label'], 'description': v['description']}
            for k, v in config.INTEREST_LEVELS.items()
        },
    }


@app.get("/api/gemeenten")
def get_gemeenten():
    try:
        conn = db.get_meetings_db()
        rows = conn.execute(
            "SELECT naam FROM gemeenten ORDER BY naam"
        ).fetchall()
        conn.close()
        return [r['naam'] for r in rows]
    except Exception:
        return []


# ── Meetings ───────────────────────────────────────────────────────────────

@app.get("/api/meetings/live")
def meetings_live():
    conn = db.get_meetings_db()
    today = datetime.now().strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT m.id, m.datum, m.tijd, m.titel, m.type, m.url,
               m.has_livestream, m.status, g.naam as gemeente
        FROM meetings m
        JOIN gemeenten g ON g.id = m.gemeente_id
        WHERE m.status = 'live'
        ORDER BY m.tijd
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/meetings/today")
def meetings_today():
    conn = db.get_meetings_db()
    today = datetime.now().strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT m.id, m.datum, m.tijd, m.titel, m.type, m.url,
               m.has_livestream, m.status, g.naam as gemeente
        FROM meetings m
        JOIN gemeenten g ON g.id = m.gemeente_id
        WHERE m.datum = ?
        ORDER BY m.tijd, g.naam
    """, (today,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/meetings/upcoming")
def meetings_upcoming():
    conn = db.get_meetings_db()
    today = datetime.now().strftime('%Y-%m-%d')
    week = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT m.id, m.datum, m.tijd, m.titel, m.type, m.url,
               m.has_livestream, m.status, g.naam as gemeente
        FROM meetings m
        JOIN gemeenten g ON g.id = m.gemeente_id
        WHERE m.datum BETWEEN ? AND ? AND m.status = 'scheduled'
        ORDER BY m.datum, m.tijd, g.naam
    """, (today, week)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Transcript ─────────────────────────────────────────────────────────────

@app.get("/api/transcript/{meeting_id}")
def get_transcript(meeting_id: int, since: Optional[float] = None):
    chunks = db.get_transcript(meeting_id, since)
    return chunks


# ── Alerts ─────────────────────────────────────────────────────────────────

@app.get("/api/alerts")
def get_alerts(user: dict = Depends(_require_user),
               unread: bool = False, limit: int = 50):
    return db.get_user_alerts(user['id'], limit, unread)


@app.post("/api/alerts/{alert_id}/read")
def read_alert(alert_id: int, user: dict = Depends(_require_user)):
    db.mark_alert_read(user['id'], alert_id)
    return {"ok": True}


@app.get("/api/alerts/stream")
async def alert_stream(request: Request, token: Optional[str] = Query(None)):
    """Server-Sent Events stream voor real-time alerts.
    
    EventSource kan geen headers sturen, dus accepteert token als query param.
    """
    from starlette.responses import StreamingResponse

    # Auth via query param of header
    auth_token = token or _get_token(request)
    if not auth_token:
        raise HTTPException(401, "Niet ingelogd")
    user = db.get_user_by_token(auth_token)
    if not user:
        raise HTTPException(401, "Ongeldige sessie")

    queue = asyncio.Queue()
    user_id = user['id']
    if user_id not in _sse_clients:
        _sse_clients[user_id] = []
    _sse_clients[user_id].append(queue)

    async def event_generator():
        try:
            yield "data: {\"type\": \"connected\"}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse_clients[user_id].remove(queue)
            if not _sse_clients[user_id]:
                del _sse_clients[user_id]

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def push_alert_to_user(user_id: int, alert_data: dict):
    """Push een alert naar alle SSE connecties van een gebruiker."""
    if user_id in _sse_clients:
        for queue in _sse_clients[user_id]:
            await queue.put(alert_data)


# ── Demo endpoints ─────────────────────────────────────────────────────────

DEMO_TRANSCRIPT = [
    {"time": 0, "speaker": "Voorzitter", "text": "Ik open de vergadering van de gemeenteraad. Welkom allemaal. We beginnen met agendapunt drie, de woningbouwplannen voor het komende jaar."},
    {"time": 30, "speaker": "Wethouder Van Dam", "text": "Dank u voorzitter. We hebben de afgelopen maanden hard gewerkt aan het woningbouwprogramma. Er zijn vierhonderd nieuwe sociale huurwoningen gepland in de wijk Nieuwland. De woningcorporatie heeft bevestigd dat de bouwvergunning rond is."},
    {"time": 60, "speaker": "Raadslid Jansen (VVD)", "text": "Dit is goed nieuws, maar ik maak me zorgen over de bereikbaarheid. De provinciale weg naar het centrum kan dit verkeer niet aan. Er is een verkeersknelpunt bij de rotonde. Is hier overleg geweest met de provincie?"},
    {"time": 90, "speaker": "Wethouder Van Dam", "text": "Ja, we zijn in gesprek met Gedeputeerde Staten over een aanpassing van de N235. De provincie heeft aangegeven bereid te zijn om mee te financieren aan de verbreding. Dat staat in de begroting voor volgend jaar."},
    {"time": 120, "speaker": "Raadslid De Boer (GroenLinks)", "text": "Voorzitter, ik wil een motie indienen over de energietransitie in deze nieuwbouwwijk. Alle woningen moeten gasloos worden opgeleverd met warmtepompen en zonnepanelen. Dat past bij de regionale energiestrategie."},
    {"time": 150, "speaker": "Voorzitter", "text": "De motie wordt genoteerd. We brengen deze aan het eind van dit agendapunt in stemming. Zijn er meer vragen over de woningbouw?"},
    {"time": 180, "speaker": "Raadslid Bakker (PvdA)", "text": "Voorzitter, hoe staat het met de stikstofruimte voor dit project? We weten dat de Raad van State streng is. Ik wil niet dat we straks halverwege de bouw moeten stoppen vanwege een Natura 2000 uitspraak."},
    {"time": 210, "speaker": "Wethouder Van Dam", "text": "Goede vraag. De stikstofberekening is goedgekeurd. We zitten ruim onder de depositienorm. Bovendien hebben we een overeenkomst met twee piekbelasters in het buitengebied die hun vergunning inleveren."},
    {"time": 240, "speaker": "Raadslid Smit (CDA)", "text": "Dan wil ik het hebben over de financiën. De begroting laat een tekort zien van twee miljoen euro op het sociaal domein. De jeugdzorg kost ons jaarlijks meer dan begroot. Ik vraag de wethouder of preventief toezicht dreigt."},
    {"time": 270, "speaker": "Wethouder Financiën", "text": "Dat is een reëel risico. We zoeken oplossingen binnen de WMO en jeugdzorg. Ik sluit een bezuiniging op andere terreinen niet uit. We hebben ook het gemeentefonds aangeschreven maar daar verwachten we geen extra geld."},
    {"time": 300, "speaker": "Raadslid Jansen (VVD)", "text": "Voorzitter, dit is zorgwekkend. Een tekort van twee miljoen, mogelijke bezuinigingen, en artikel twaalf dreigt aan de horizon. Ik wil een spoeddebat over de financiën van deze gemeente."},
    {"time": 330, "speaker": "Voorzitter", "text": "Het verzoek voor een spoeddebat is genoteerd. We gaan nu stemmen over de motie van De Boer over gasloze woningen. Wie is voor? Ik tel zeventien stemmen voor en acht tegen. De motie is aangenomen."},
    {"time": 360, "speaker": "Raadslid De Boer (GroenLinks)", "text": "Dank u. Dan weten de ontwikkelaars waar ze aan toe zijn. Alle nieuwbouw in deze gemeente wordt duurzaam. Dit is een belangrijk signaal voor de klimaatdoelen."},
    {"time": 390, "speaker": "Voorzitter", "text": "We gaan door naar agendapunt vier: de veiligheidsregio. Er zijn zorgen over ondermijning in het buitengebied. De politie heeft een rapport uitgebracht over drugscriminaliteit."},
    {"time": 420, "speaker": "Burgemeester", "text": "Dank u voorzitter. Het rapport is alarmerend. We zien een toename van drugslabs in leegstaande boerderijen. De handhaving is opgevoerd en er is cameratoezicht geplaatst. Ik heb met de officier van justitie gesproken over extra capaciteit."},
]


@app.post("/api/demo/start")
async def demo_start(user: dict = Depends(_require_user)):
    """Start een demo-vergadering met gesimuleerd transcript."""
    # Maak een fictieve meeting in de meetings DB
    conn = db.get_meetings_db()
    # Gebruik een bestaande meeting van vandaag, of -1 als demo ID
    today = datetime.now().strftime('%Y-%m-%d')
    meeting = conn.execute(
        "SELECT m.id, g.naam FROM meetings m "
        "JOIN gemeenten g ON g.id = m.gemeente_id "
        "WHERE m.datum = ? LIMIT 1", (today,)
    ).fetchone()
    conn.close()

    meeting_id = meeting['id'] if meeting else -1
    gemeente = meeting['naam'] if meeting else 'Demo Gemeente'

    # Start achtergrondtaak om transcript stuk voor stuk toe te voegen
    asyncio.create_task(_demo_playback(meeting_id, gemeente))

    return {
        "meeting_id": meeting_id,
        "gemeente": gemeente,
        "message": "Demo gestart — transcript verschijnt in real-time",
    }


async def _demo_playback(meeting_id, gemeente):
    """Speel demo-transcript af met versnelde timing.

    Simuleert iteratieve analyse: elke 3 chunks (≈ 90 sec demo = 15 min echt)
    analyseert het rolling window voor alle topics × kanalen.
    """
    transcript_buffer = []

    for i, chunk in enumerate(DEMO_TRANSCRIPT):
        # Voeg transcript chunk toe
        db.add_transcript_chunk(
            meeting_id=meeting_id,
            text=chunk['text'],
            speaker=chunk.get('speaker'),
            start_time=chunk['time'],
            end_time=chunk['time'] + 30,
        )
        transcript_buffer.append(chunk['text'])

        # Analyseer elke 3 chunks (simuleert 5-minuten interval)
        if (i + 1) % 3 == 0 or i == len(DEMO_TRANSCRIPT) - 1:
            window_text = ' '.join(transcript_buffer)
            # Haal recente alerts op voor deduplicatie
            recent_alerts = db.get_recent_alerts_for_meeting(meeting_id)
            alerts = analysis.analyze_window_for_all_topics(
                text=window_text,
                gemeente=gemeente,
                meeting_id=meeting_id,
                livestream_url=None,
                recent_alerts=recent_alerts,
            )

            for alert_data in alerts:
                alert_id = db.create_alert(
                    meeting_id=meeting_id,
                    gemeente=gemeente,
                    topic=alert_data['topic'],
                    level=alert_data['level'],
                    title=alert_data['title'],
                    summary=alert_data['summary'],
                    score=alert_data['score'],
                    indicators=alert_data.get('indicators'),
                    livestream_url=alert_data.get('livestream_url'),
                    quote=window_text[:200],
                    t_start=chunk['time'] - 90,
                    t_end=chunk['time'] + 30,
                )
                await _push_to_matching_users(
                    alert_id, alert_data['topic'], alert_data['level'], gemeente
                )

            # Reset buffer na analyse (maar bewaar overlap voor volgende window)
            transcript_buffer = transcript_buffer[-2:]  # bewaar laatste 2 chunks als overlap

        # Wacht 3 seconden tussen chunks in demo modus
        await asyncio.sleep(3)


async def _push_to_matching_users(alert_id, topic, level, gemeente):
    """Push alert naar alle SSE clients die matchen op topic + level."""
    udb = db.get_users_db()
    alert = udb.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if not alert:
        udb.close()
        return

    alert_data = dict(alert)
    alert_data['type_event'] = 'alert'

    for user_id, queues in list(_sse_clients.items()):
        if not topic:
            continue
        # Check of user dit topic op het juiste level heeft
        match = udb.execute(
            "SELECT level FROM user_topics WHERE user_id = ? AND topic = ?",
            (user_id, topic)
        ).fetchone()
        if match and match['level'] == level:
            for q in queues:
                await q.put(alert_data)

    udb.close()


# ── Temp alerts (terugzoeken, max 1 uur) ───────────────────────────────────

@app.get("/api/temp-alerts")
def get_temp_alerts_endpoint(
    user: dict = Depends(_require_user),
    level: Optional[str] = None,
    topic: Optional[str] = None,
    limit: int = 50
):
    """Haal recente temp alerts op (max 1 uur oud), gefilterd op user-voorkeuren."""
    prefs = db.get_preferences(user['id'])
    all_alerts = analysis.get_temp_alerts(level=level, topic=topic, limit=limit * 3)

    # Filter op user-voorkeuren
    filtered = []
    for alert in all_alerts:
        a_topic = alert.get('topic')
        a_level = alert.get('level')
        if a_topic in prefs['topics'] and prefs['topics'][a_topic] == a_level:
            filtered.append(alert)
        if len(filtered) >= limit:
            break
    return filtered


# ── Transcript zoeken ──────────────────────────────────────────────────────

@app.get("/api/transcript/{meeting_id}/search")
def search_transcript(meeting_id: int, q: str = Query(..., min_length=2)):
    """Doorzoek het transcript van een vergadering."""
    chunks = db.get_transcript(meeting_id)
    results = []
    query_lower = q.lower()
    for chunk in chunks:
        if query_lower in chunk.get('text', '').lower():
            results.append(chunk)
    return results


# ── Stats ──────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def stats():
    conn = db.get_meetings_db()
    today = datetime.now().strftime('%Y-%m-%d')

    total = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    today_count = conn.execute(
        "SELECT COUNT(*) FROM meetings WHERE datum = ?", (today,)
    ).fetchone()[0]
    live = conn.execute(
        "SELECT COUNT(*) FROM meetings WHERE status = 'live'"
    ).fetchone()[0]
    scheduled = conn.execute(
        "SELECT COUNT(*) FROM meetings WHERE datum >= ? AND status = 'scheduled'",
        (today,)
    ).fetchone()[0]
    gemeenten = conn.execute("SELECT COUNT(*) FROM gemeenten").fetchone()[0]
    conn.close()

    udb = db.get_users_db()
    users = udb.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    alerts = udb.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
    udb.close()

    return {
        'total_meetings': total,
        'today': today_count,
        'live': live,
        'scheduled': scheduled,
        'gemeenten': gemeenten,
        'users': users,
        'alerts': alerts,
    }


# ── Statische files (PWA) ────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(config.STATIC_DIR, 'index.html'), 'r') as f:
        return f.read()


@app.get("/manifest.json")
async def manifest():
    with open(os.path.join(config.STATIC_DIR, 'manifest.json'), 'r') as f:
        return JSONResponse(json.loads(f.read()))


@app.get("/sw.js")
async def service_worker():
    with open(os.path.join(config.STATIC_DIR, 'sw.js'), 'r') as f:
        from starlette.responses import Response
        return Response(
            content=f.read(),
            media_type="application/javascript",
        )
