"""
FastAPI server — REST API + statische files voor de RANST PWA.
Geen authenticatie: de app werkt direct na download.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import StreamingResponse, Response

from . import config
from . import database as db
from . import analysis

# ── App ────────────────────────────────────────────────────────────────────

app = FastAPI(title="RANST", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API Key authenticatie ───────────────────────────────────────────────────

def _get_valid_keys() -> set:
    raw = os.environ.get('RANST_API_KEYS', '')
    return {k.strip() for k in raw.split(',') if k.strip()}

@app.middleware("http")
async def api_key_middleware(request: Request, call_next):
    # Publieke paden — geen key nodig
    public = {'/docs', '/openapi.json', '/redoc', '/health', '/app',
              '/api/provinces', '/api/topics'}
    if request.url.path in public:
        return await call_next(request)

    valid_keys = _get_valid_keys()
    # Als er geen keys zijn geconfigureerd, alles doorlaten (dev-mode)
    if not valid_keys:
        return await call_next(request)

    # Key via header of query param (SSE kan geen headers sturen)
    key = (request.headers.get('X-API-Key') or
           request.query_params.get('key') or '')
    if key not in valid_keys:
        from starlette.responses import JSONResponse as _JR
        return _JR({'detail': 'Ongeldige of ontbrekende API key'}, status_code=401)

    return await call_next(request)

# SSE: alle verbonden clients
_sse_clients: list = []

# Chunk-teller per meeting voor analyse-trigger
_ingest_counters: dict = {}

# Per-meeting asyncio lock — voorkomt concurrent analyses (race condition → duplicaten)
_analysis_locks: dict = {}

# Prefetch voortgang — zichtbaar via /api/prefetch/status
_prefetch_status: dict = {
    'fase': None,          # None | '1_details' | '2_pdf' | '3a_priority' | '3b_background' | 'klaar'
    'done': 0,
    'total': 0,
    'huidig': None,        # 'Gemeente datum'
    'gestart': None,
}

# Analyse elke N whisper-segmenten (~30 sec/segment → 10 = ~5 min)
ANALYZE_EVERY = 10

# Minimale transcript-duur (seconden) voordat de eerste analyse wordt uitgevoerd
ANALYZE_MIN_DURATION = 60   # 1 minuut minimaal transcript


# ── Topics & Provincies ────────────────────────────────────────────────────

@app.get("/api/topics")
def get_topics():
    return {
        'topics': config.TOPICS,
        'levels': {
            'pers': 'Pers',
            'bestuurlijk': 'Bestuurlijk',
        },
    }


@app.get("/api/provinces")
def get_provinces():
    return config.PROVINCIE_GEMEENTEN


# ── Meetings ────────────────────────────────────────────────────────────────

def _meetings_conn():
    conn = db.get_meetings_db()
    if conn is None:
        return None
    return conn


@app.get("/api/meetings/calendar")
def meetings_calendar(
    start: Optional[str] = None,
    end: Optional[str] = None,
    gemeenten: Optional[str] = None,
):
    """Meetings voor een datumrange, optioneel gefilterd op gemeenten.

    start/end: YYYY-MM-DD  (default: vandaag t/m 14 dagen)
    gemeenten: kommagescheiden lijst
    """
    conn = _meetings_conn()
    if conn is None:
        return {}

    today = datetime.now().strftime('%Y-%m-%d')
    start = start or today
    end = end or (datetime.now() + timedelta(days=13)).strftime('%Y-%m-%d')

    gemeente_filter = [g.strip() for g in gemeenten.split(',')] if gemeenten else []

    if gemeente_filter:
        placeholders = ','.join('?' * len(gemeente_filter))
        rows = conn.execute(f"""
            SELECT m.id, m.datum, m.tijd, m.titel, m.type, m.url,
                   m.has_livestream, m.status, g.naam AS gemeente
            FROM meetings m
            JOIN gemeenten g ON g.id = m.gemeente_id
            WHERE m.datum BETWEEN ? AND ?
              AND g.naam IN ({placeholders})
            ORDER BY m.datum, CASE WHEN m.tijd IS NULL OR m.tijd = '' THEN 1 ELSE 0 END, m.tijd, g.naam
        """, [start, end] + gemeente_filter).fetchall()
    else:
        rows = conn.execute("""
            SELECT m.id, m.datum, m.tijd, m.titel, m.type, m.url,
                   m.has_livestream, m.status, g.naam AS gemeente
            FROM meetings m
            JOIN gemeenten g ON g.id = m.gemeente_id
            WHERE m.datum BETWEEN ? AND ?
            ORDER BY m.datum, CASE WHEN m.tijd IS NULL OR m.tijd = '' THEN 1 ELSE 0 END, m.tijd, g.naam
        """, [start, end]).fetchall()

    conn.close()

    # Groepeer op datum
    by_date: dict = {}
    for r in rows:
        d = r['datum']
        if d not in by_date:
            by_date[d] = []
        by_date[d].append(dict(r))
    return by_date


@app.get("/api/meetings/upcoming")
def meetings_upcoming(gemeenten: Optional[str] = None, limit: int = 25):
    """Aankomende vergaderingen vanaf nu (max 30 dagen vooruit)."""
    conn = _meetings_conn()
    if conn is None:
        return []
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo('Europe/Amsterdam'))
    except Exception:
        from datetime import timezone, timedelta as _td2
        now = datetime.now(timezone(_td2(hours=1)))
    today = now.strftime('%Y-%m-%d')
    now_t = now.strftime('%H:%M')
    end_date = (now + timedelta(days=30)).strftime('%Y-%m-%d')
    gemeente_filter = [g.strip() for g in gemeenten.split(',')] if gemeenten else []
    gem_clause = ''
    params = [today, now_t, today, end_date]
    if gemeente_filter:
        placeholders = ','.join('?' * len(gemeente_filter))
        gem_clause = f'AND g.naam IN ({placeholders})'
        params.extend(gemeente_filter)
    params.append(limit)
    rows = conn.execute(f"""
        SELECT m.id, m.datum, m.tijd, m.titel, m.type, m.url,
               m.has_livestream, m.status, g.naam AS gemeente
        FROM meetings m JOIN gemeenten g ON g.id = m.gemeente_id
        WHERE ((m.datum = ? AND m.tijd >= ?) OR m.datum > ?)
          AND m.datum <= ? {gem_clause}
        ORDER BY m.datum, m.tijd LIMIT ?
    """, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/meetings/today")
def meetings_today(gemeenten: Optional[str] = None, date: Optional[str] = None):
    """Alle vergaderingen van vandaag (of opgegeven datum YYYY-MM-DD)."""
    conn = _meetings_conn()
    if conn is None:
        return []
    target = date if date else datetime.now().strftime('%Y-%m-%d')
    gemeente_filter = [g.strip() for g in gemeenten.split(',')] if gemeenten else []
    if gemeente_filter:
        placeholders = ','.join('?' * len(gemeente_filter))
        rows = conn.execute(f"""
            SELECT m.id, m.datum, m.tijd, m.titel, m.type, m.url,
                   m.has_livestream, m.status, g.naam AS gemeente
            FROM meetings m JOIN gemeenten g ON g.id = m.gemeente_id
            WHERE m.datum = ? AND g.naam IN ({placeholders})
            ORDER BY m.tijd, g.naam
        """, [target] + gemeente_filter).fetchall()
    else:
        rows = conn.execute("""
            SELECT m.id, m.datum, m.tijd, m.titel, m.type, m.url,
                   m.has_livestream, m.status, g.naam AS gemeente
            FROM meetings m JOIN gemeenten g ON g.id = m.gemeente_id
            WHERE m.datum = ? ORDER BY m.tijd, g.naam
        """, [target]).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/meetings/live")
def meetings_live():
    conn = _meetings_conn()
    if conn is None:
        return []
    rows = conn.execute("""
        SELECT m.id, m.datum, m.tijd, m.titel, m.type, m.url,
               m.has_livestream, m.status, g.naam AS gemeente
        FROM meetings m
        JOIN gemeenten g ON g.id = m.gemeente_id
        WHERE m.status = 'live'
        ORDER BY m.tijd
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/meetings/check-live")
async def check_live_meetings():
    """
    Ping alle vergaderingen die vandaag al begonnen zijn en update hun live-status.
    Retourneert lijst van gevonden live en beëindigde vergaderingen.
    """
    import urllib.request
    import ssl
    import re

    ctx = ssl.create_default_context()

    def _fetch(url: str, timeout: int = 8) -> tuple:
        h = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Accept-Language": "nl-NL,nl;q=0.9",
            "Accept": "text/html,*/*",
        }
        req = urllib.request.Request(url, headers=h)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
            return resp.read(200000).decode("utf-8", errors="replace"), resp.getcode()
        except urllib.error.HTTPError as e:
            return "", e.code
        except Exception:
            return "", 0

    # NL tijd = UTC+1 (CET, vóór zomertijd op 29 maart)
    from datetime import timezone
    nl_now = datetime.now(timezone.utc) + timedelta(hours=1)
    nl_time = nl_now.strftime("%H:%M")
    nl_date = nl_now.strftime("%Y-%m-%d")

    conn = _meetings_conn()
    if conn is None:
        return {"error": "DB niet beschikbaar"}

    # Haal vergaderingen op die vandaag gestart zijn en nog niet beëindigd
    rows = conn.execute(f"""
        SELECT m.id, m.datum, m.tijd, m.titel, g.naam, m.url,
               m.has_livestream, m.status
        FROM meetings m JOIN gemeenten g ON g.id = m.gemeente_id
        WHERE m.datum = '{nl_date}'
          AND (m.tijd IS NULL OR m.tijd <= '{nl_time}')
          AND m.status != 'ended'
        ORDER BY m.tijd ASC
    """).fetchall()

    results = {"checked": 0, "live": [], "ended": [], "no_stream": []}

    for mid, datum, tijd, titel, gemeente, url, has_ls, status in rows:
        results["checked"] += 1
        if not url:
            results["no_stream"].append({"id": mid, "gemeente": gemeente, "titel": titel})
            continue

        html, code = _fetch(url)

        if code == 0 or (not html and code != 200):
            # Onbereikbaar = waarschijnlijk beëindigd als het al live was
            if status == "live":
                conn.execute(
                    "UPDATE meetings SET status='ended', ended_at=datetime('now') WHERE id=?",
                    (mid,),
                )
                results["ended"].append({"id": mid, "gemeente": gemeente, "titel": titel})
            else:
                results["no_stream"].append({"id": mid, "gemeente": gemeente, "titel": titel, "reason": f"HTTP {code}"})
            continue

        # Detecteer live stream
        is_live = False
        stream_url = None
        reason = None

        # Direct m3u8
        m3u8 = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
        if m3u8:
            stream_url = m3u8.group(1)
            is_live = True
            reason = "m3u8 stream"

        # YouTube embed
        yt = re.search(r'youtube\.com/embed/([a-zA-Z0-9_-]{11})', html)
        if yt and not is_live:
            stream_url = f"https://www.youtube.com/watch?v={yt.group(1)}"
            is_live = True
            reason = "YouTube embed"

        # CompanyWebcast player
        cwc = re.search(r'sdk\.companywebcast\.com/sdk/player/\?id=([a-zA-Z0-9_-]+)', html)
        if cwc and not is_live:
            pid = cwc.group(1)
            cfg_data, _ = _fetch(
                f"https://sdk.companywebcast.com/portal/configuration/1.0/api/Configurations/{pid}/"
            )
            if cfg_data and cfg_data not in ("[]", "") and not cfg_data.startswith("ERROR"):
                is_live = True
                reason = f"CWC actief (id={pid})"
            else:
                reason = f"CWC id={pid} (geen actieve broadcast)"

        # NotUBiz Wowza + isLive check
        if "wbroker.notubiz.nl" in html and not is_live:
            if '"isLive":true' in html or '"is_live":true' in html.lower():
                is_live = True
                reason = "NotUBiz isLive=true"
            elif 'class="live"' in html or "status-live" in html:
                is_live = True
                reason = "NotUBiz live CSS"
            else:
                reason = "Wowza broker aanwezig (niet gestart)"

        # has_livestream=1 en pagina bereikbaar maar geen JS-stream: mark als "mogelijk live"
        if has_ls and not is_live and not reason and code == 200:
            reason = "Pagina bereikbaar, stream via JS (niet detecteerbaar zonder browser)"

        if is_live:
            conn.execute("""
                UPDATE meetings
                SET status='live', joined_at=COALESCE(joined_at, datetime('now'))
                WHERE id=?
            """, (mid,))
            results["live"].append({
                "id": mid, "gemeente": gemeente, "titel": titel,
                "tijd": tijd, "stream_url": stream_url, "reason": reason,
            })
        else:
            results["no_stream"].append({
                "id": mid, "gemeente": gemeente, "titel": titel,
                "tijd": tijd, "reason": reason or f"HTTP {code}",
            })

    conn.commit()
    conn.close()
    results["nl_tijd"] = nl_time
    return results


# ── Meeting detail ──────────────────────────────────────────────────────────

@app.get("/api/meetings/{meeting_id}")
def get_meeting(meeting_id: int):
    conn = _meetings_conn()
    if conn is None:
        raise HTTPException(404, "Meetings DB niet gevonden")
    row = conn.execute("""
        SELECT m.id, m.datum, m.tijd, m.titel, m.type, m.url,
               m.has_livestream, m.status, m.external_id, m.bron,
               m.voorzitter, m.locatie, m.categorie,
               g.naam AS gemeente, g.notubiz_id, g.platforms
        FROM meetings m
        JOIN gemeenten g ON g.id = m.gemeente_id
        WHERE m.id = ?
    """, (meeting_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(404, "Meeting niet gevonden")
    return dict(row)


def _get_go_platform_details(external_id: str, meeting_url: str) -> dict:
    """Haal agenda, sprekers en documenten op van GemeenteOplossingen of TYPO3 portalen."""
    import urllib.request as _req
    import urllib.parse as _parse
    import ssl as _ssl
    import re as _re

    if not meeting_url:
        return {'error': 'Geen URL beschikbaar'}

    ctx = _ssl.create_default_context()
    headers = {'User-Agent': 'Mozilla/5.0 (RANST/1.0)'}

    def _fetch(url):
        try:
            req = _req.Request(url, headers=headers)
            return _req.urlopen(req, timeout=10, context=ctx).read().decode('utf-8', errors='replace')
        except Exception:
            return None

    def _clean(s):
        import html as _html_mod
        s = _re.sub(r'<[^>]+>', ' ', s)
        s = _html_mod.unescape(s)
        return _re.sub(r'\s+', ' ', s).strip()

    html = _fetch(meeting_url)
    if not html:
        return {'error': 'Pagina niet bereikbaar'}

    # Extraheer base URL (bijv. https://raad.sliedrecht.nl)
    base_url = _re.match(r'(https?://[^/]+)', meeting_url)
    base_url = base_url.group(1) if base_url else ''

    # ── TYPO3 branch (bijv. Gooise Meren — tx_windmeetings structuur) ──────
    if 'tx_windmeetings' in html or 'rowLevel' in html:
        # Agenda items: <div class="rowLevel{N}">...<span class="nr">nr</span>...<h{N}>title
        agenda = []
        rows = _re.findall(
            r'<div[^>]+class=["\'][^"\']*rowLevel\d[^"\']*["\'][^>]*>.*?'
            r'<span[^>]+class=["\'][^"\']*\bnr\b[^"\']*["\'][^>]*>([^<]+)</span>\s*(.*?)'
            r'</h[2345]>',
            html, _re.S | _re.I
        )
        for nr, title_html in rows[:40]:
            title = _clean(title_html)
            if title and len(title) > 2:
                agenda.append({'number': nr.strip(), 'title': title[:200], 'type': ''})

        # Documents: tx_windmeetings downloadDocument links
        documents = []
        seen_docs = set()
        for m in _re.finditer(
            r'href=["\']([^"\']*tx_windmeetings_agendadetail[^"\']+)["\'][^>]*>([^<]*(?:<span[^>]*>[^<]*</span>[^<]*)?)',
            html, _re.I
        ):
            href = _parse.unquote(m.group(1).replace('&amp;', '&'))
            doc_m = _re.search(r'\[document\]=(\d+)', href)
            if not doc_m:
                continue
            doc_id = doc_m.group(1)
            if doc_id in seen_docs:
                continue
            seen_docs.add(doc_id)
            raw_title = _clean(m.group(2))
            # Remove filesize suffix (bijv. " - 111 KB")
            raw_title = _re.sub(r'\s*-\s*\d+[\d,.]*\s*(?:KB|MB|GB)', '', raw_title).strip()
            full_url = href if href.startswith('http') else base_url + href
            if raw_title:
                documents.append({'title': raw_title[:100], 'url': full_url})

        # Members: scrape fractie pages van samenstelling-gemeenteraad
        speakers = []
        try:
            sam_url = f'{base_url}/gemeenteraad/samenstelling-gemeenteraad/'
            sam_html = _fetch(sam_url)
            if sam_html:
                fractie_links = _re.findall(
                    r'href=["\']([^"\']*samenstelling-gemeenteraad/partijpagina/[^"\']+)["\']',
                    sam_html, _re.I
                )
                seen_spk = set()
                for link in fractie_links[:15]:
                    full_link = link if link.startswith('http') else base_url + link
                    fp_html = _fetch(full_link)
                    if not fp_html:
                        continue
                    entries = _re.findall(r'class=["\']h3["\'][^>]*>([^<]+)</h', fp_html, _re.I)
                    for entry in entries:
                        entry = entry.strip()
                        m2 = _re.match(
                            r'^(Fractievoorzitter|Gemeenteraadslid|Steunfractielid|Burgerraadslid'
                            r'|Raadslid|Griffier|Burgemeester|Wethouder|Plaatsvervanger)'
                            r'\s+(.+?)\s*\(([^)]+)\)\s*$',
                            entry
                        )
                        if m2 and m2.group(2) not in seen_spk:
                            seen_spk.add(m2.group(2))
                            speakers.append({
                                'name': m2.group(2).strip(),
                                'function': m2.group(1).strip(),
                                'party': m2.group(3).strip(),
                            })
        except Exception:
            pass

        return {
            'platform': 'typo3',
            'external_id': external_id,
            'agenda': agenda,
            'speakers': speakers,
            'documents': documents,
        }

    # ── Agenda (GemeenteOplossingen agenda-row structuur) ──────────────────
    agenda = []
    rows = _re.findall(
        r'<div[^>]+class=["\']agenda-row["\'][^>]*>.*?<span[^>]+class=["\']nr["\'][^>]*>(\d*[A-Z]?\d*\.?\d*)</span>.*?<h4>(.*?)</h4>',
        html, _re.S
    )
    for nr, raw_title in rows[:30]:
        title = _clean(raw_title)
        title = _re.sub(r'^\d+\s+', '', title).strip()
        if 2 < len(title) < 200:
            agenda.append({'number': nr.strip(), 'title': title, 'type': ''})

    # Fallback: oudere GO structuur met <li class="agenda...">
    if not agenda:
        items_raw = _re.findall(
            r'class=["\'][^"\']*agenda[^"\']*["\'][^>]*>(.*?)</(?:li|div)',
            html, _re.S | _re.I
        )
        for item in items_raw[:20]:
            title = _clean(item)
            if 3 < len(title) < 200:
                agenda.append({'number': '', 'title': title, 'type': ''})

    # ── Sprekers via /Raad ─────────────────────────────────────────────────
    speakers = []
    if base_url:
        raad_html = _fetch(f"{base_url}/Raad")
        if raad_html:
            members = _re.findall(
                r'<span[^>]+class=["\']name["\'][^>]*>([^<]+)</span>\s*<span[^>]*>([^<]*)</span>',
                raad_html
            )
            seen = set()
            for name, functie in members:
                name = name.strip()
                if name and ' ' in name and name not in seen and '@' not in name:
                    seen.add(name)
                    speakers.append({'name': name, 'function': functie.strip(), 'party': ''})

    # ── Documenten (PDF links op de vergaderpagina) ────────────────────────
    documents = []
    pdf_links = _re.findall(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', html, _re.I)
    seen_docs = set()
    for link in pdf_links:
        if not link.startswith('http'):
            link = base_url + link
        filename = link.split('/')[-1].split('?')[0]
        filename = _re.sub(r'[-_]', ' ', filename.replace('.pdf', '').replace('.PDF', ''))
        title = _parse.unquote(filename).strip()[:100]
        if link not in seen_docs and title:
            seen_docs.add(link)
            documents.append({'title': title, 'url': link})

    return {
        'platform': 'gemeenteoplossingen',
        'external_id': external_id,
        'agenda': agenda,
        'speakers': speakers,
        'documents': documents,
    }


def _get_qualigraf_platform_details(external_id: str, go_url: str) -> dict:
    """Haal agenda, sprekers en documenten op van Qualigraf/Parlaeus portalen."""
    import urllib.request as _req
    import ssl as _ssl
    import re as _re
    import json as _json
    import html as _html_mod
    from urllib.parse import unquote as _unquote

    if not go_url or not external_id:
        return {'error': 'Geen URL of external_id beschikbaar'}

    # Zorg dat go_url de root is (zonder /app suffix)
    frontend_url = go_url.rstrip('/')
    if frontend_url.endswith('/app'):
        frontend_url = frontend_url[:-4]

    ctx = _ssl.create_default_context()
    headers = {'User-Agent': 'Mozilla/5.0 (RANST/1.0)'}

    def _fetch(url):
        try:
            req = _req.Request(url, headers=headers)
            return _req.urlopen(req, timeout=10, context=ctx).read().decode('utf-8', errors='replace')
        except Exception:
            return None

    def _fetch_json(url):
        try:
            h = {**headers, 'Accept': 'application/json'}
            req = _req.Request(url, headers=h)
            body = _req.urlopen(req, timeout=10, context=ctx).read().decode('utf-8', errors='replace')
            return _json.loads(body)
        except Exception:
            return None

    def _clean(s):
        s = _re.sub(r'<[^>]+>', ' ', s)
        s = _html_mod.unescape(s)
        return _re.sub(r'\s+', ' ', s).strip()

    hexkey = external_id

    # ── Agenda & Documenten ──────────────────────────────────────────────────
    agenda_html = _fetch(f'{frontend_url}/user/agenda/action=view/ag={hexkey}')
    agenda = []
    documents = []

    if agenda_html:
        # Agenda items: <div id="ap{id}"> ... <h{N}>nr. Title</h{N}>
        for raw in _re.findall(
            r'<div[^>]+id=["\']ap\d+[^>]*>.*?<h\d[^>]*>([^<]+)</h\d>',
            agenda_html, _re.S
        )[:50]:
            title = _clean(raw)
            m_nr = _re.match(r'^([\d]+[A-Z]?\.?\s*)', title)
            nr = m_nr.group(1).rstrip() if m_nr else ''
            title_clean = title[len(m_nr.group(0)):].strip() if m_nr else title
            if title_clean and len(title_clean) > 2:
                agenda.append({'number': nr, 'title': title_clean[:200], 'type': ''})

        # Documents: /user/showdoc/action=view/id={hexkey}/type=pdf/{name}
        seen_docs = set()
        for m_d in _re.finditer(
            r'href=["\'](/user/showdoc/action=view/id=([a-f0-9]+)/type=[^"\']+)["\'][^>]*>([^<]*)',
            agenda_html, _re.I
        ):
            doc_id = m_d.group(2)
            if doc_id in seen_docs:
                continue
            seen_docs.add(doc_id)
            raw_title = m_d.group(3).strip()
            fn_m = _re.search(r'/type=[^/]+/(.+)$', m_d.group(1))
            filename = _unquote(fn_m.group(1)) if fn_m else doc_id
            title = raw_title or filename.replace('_', ' ').replace('+', ' ')
            documents.append({'title': title[:100], 'url': f'{frontend_url}{m_d.group(1)}'})

    # ── Members ─────────────────────────────────────────────────────────────
    speakers = []
    members_data = _fetch_json(f'{frontend_url}/vji/public/councilperiod/action=datalist')
    if members_data:
        for block in members_data.get('blocks', []):
            party = block.get('category', '').strip()
            for member in block.get('members', []):
                name = member.get('name', '').strip()
                func = member.get('function', '').strip()
                if not name or 'niet ingenomen' in name.lower():
                    continue
                speakers.append({'name': name, 'function': func, 'party': party})

    return {
        'platform': 'qualigraf',
        'external_id': external_id,
        'agenda': agenda,
        'speakers': speakers,
        'documents': documents,
    }


def _get_ibabs_platform_details(external_id: str, slug: str, ibabs_domain: str = None) -> dict:
    """Haal agenda en sprekers op via iBabs scraper."""
    import sys as _sys
    import os as _os
    _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _root not in _sys.path:
        _sys.path.insert(0, _root)
    try:
        from ibabs_scraper import IbabsScraper
    except ImportError:
        return {'error': 'ibabs_scraper niet gevonden'}

    if not slug:
        return {'error': 'Geen site slug voor iBabs gemeente'}

    try:
        scraper = IbabsScraper(slug, domain=ibabs_domain)
        detail = scraper.get_meeting_detail(external_id)
        if not detail:
            return {'error': 'Agenda niet beschikbaar'}

        agenda = [
            {'number': it.get('number'), 'title': it.get('title'), 'type': ''}
            for it in (detail.get('items') or [])
            if it.get('title')
        ]
        documents = []
        for it in (detail.get('items') or []):
            for doc in (it.get('documents') or []):
                if doc.get('title') and doc.get('download_url'):
                    documents.append({'title': doc['title'], 'url': doc['download_url']})
        for doc in (detail.get('agenda_documents') or []):
            if doc.get('title') and doc.get('download_url'):
                documents.append({'title': doc['title'], 'url': doc['download_url']})

        # Sprekers: probeer Members JSON endpoint (publiek op sommige iBabs sites)
        speakers = _ibabs_fetch_members(scraper.base_url, external_id)

        return {
            'platform': 'ibabs',
            'external_id': external_id,
            'start_time': detail.get('start_time'),
            'agenda': agenda,
            'speakers': speakers,
            'documents': documents,
        }
    except Exception as e:
        return {'error': str(e)}


def _ibabs_fetch_raadsleden(base_url: str) -> list:
    """Haal raadsleden op via bestuurlijkeinformatie.nl /People/Profiles/{uuid}."""
    import urllib.request as _req
    import ssl as _ssl
    import re as _re

    ctx = _ssl.create_default_context()
    headers = {'User-Agent': 'Mozilla/5.0 (RANST/1.0)'}

    def fetch(url):
        try:
            req = _req.Request(url, headers=headers)
            resp = _req.urlopen(req, timeout=10, context=ctx)
            return resp.read(524288).decode('utf-8', errors='replace')
        except Exception:
            return None

    # Stap 1: /People pagina → vind UUID voor "gemeenteraad"
    html = fetch(f"{base_url}/People")
    if not html:
        return []

    # Nav dropdown bevat links als /People/Profiles/{uuid}
    # Voorkeur: "gemeenteraad", anders eerste /People/Profiles link
    uuid = None
    for pat, priority in [
        (r'/People/Profiles/([0-9a-f-]{36})[^"\']*"[^"\']*(?:gemeenteraad|raad)[^"\']*"', True),
        (r'(?:gemeenteraad|raad)[^"\'<>]{0,80}/People/Profiles/([0-9a-f-]{36})', True),
        (r'/People/Profiles/([0-9a-f-]{36})', False),
    ]:
        m = _re.search(pat, html, _re.I)
        if m:
            uuid = m.group(1)
            break

    if not uuid:
        # Alternatief: zoek link-tekst die "raad" bevat naast een uuid
        links = _re.findall(r'href=["\']([^"\']*People/Profiles/([0-9a-f-]{36})[^"\']*)["\'][^>]*>([^<]+)', html)
        for href, uid, label in links:
            if 'raad' in label.lower():
                uuid = uid
                break
        if not uuid and links:
            uuid = links[0][1]

    if not uuid:
        return []

    # Stap 2: /People/Profiles/{uuid} → parse card-body divs
    html2 = fetch(f"{base_url}/People/Profiles/{uuid}")
    if not html2:
        return []

    speakers = []
    # Elke card-body bevat: Naam\nFunctie\n(telefoon\nemail)
    for block in _re.findall(r'class=["\']card-body["\'][^>]*>(.*?)</div>', html2, _re.S | _re.I):
        text = _re.sub(r'<[^>]+>', '\n', block)
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            continue
        name = lines[0]
        # Filter: naam moet minstens 2 woorden hebben en geen URL/mail zijn
        if ' ' not in name or '@' in name or 'http' in name:
            continue
        functie = lines[1] if len(lines) > 1 else ''
        speakers.append({'name': name, 'function': functie, 'party': ''})

    return speakers


def _ibabs_fetch_members(base_url: str, agenda_id: str) -> list:
    """Haal raadsleden op voor een iBabs vergadering."""
    # Primair: scrape People/Profiles van bestuurlijkeinformatie.nl
    speakers = _ibabs_fetch_raadsleden(base_url)
    if speakers:
        return speakers

    # Fallback: parse voorzitter uit agenda HTML
    import urllib.request as _req
    import ssl as _ssl
    import re as _re

    ctx = _ssl.create_default_context()
    try:
        url = f"{base_url}/Agenda/Index/{agenda_id}"
        req = _req.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = _req.urlopen(req, timeout=10, context=ctx)
        html = resp.read(262144).decode('utf-8', errors='replace')
        vz = _re.search(r'[Vv]oorzitter[:\s]+([A-Z][^\n<]{2,50})', html)
        if vz:
            speakers.append({'name': vz.group(1).strip(), 'function': 'Voorzitter', 'party': ''})
    except Exception:
        pass

    return speakers


@app.get("/api/meetings/{meeting_id}/platform-details")
async def get_platform_details(meeting_id: int):
    """Haal live meeting-details op van het externe platform (NotUBiz agenda, sprekers)."""
    import urllib.request as _req
    import ssl as _ssl
    import json as _json

    conn = _meetings_conn()
    if conn is None:
        return {}
    row = conn.execute("""
        SELECT m.external_id, m.bron, m.url, g.notubiz_id, g.slug, g.go_url
        FROM meetings m JOIN gemeenten g ON g.id = m.gemeente_id
        WHERE m.id = ?
    """, (meeting_id,)).fetchone()
    conn.close()
    if not row or not row['external_id']:
        return {}

    bron = row['bron']

    # ── iBabs ──────────────────────────────────────────────────────────────
    if bron == 'ibabs_scrape':
        # go_url wordt (her)gebruikt als custom iBabs base domain (bijv. statenvanaruba.ibabs.org)
        ibabs_domain = row['go_url'] if row['go_url'] and 'ibabs' in (row['go_url'] or '') else None
        return _get_ibabs_platform_details(row['external_id'], row['slug'], ibabs_domain=ibabs_domain)

    # ── Qualigraf / Parlaeus ────────────────────────────────────────────────
    if bron in ('qualigraf_scrape', 'parlaeus_scrape'):
        go_url = row['go_url'] or row['url']
        return _get_qualigraf_platform_details(row['external_id'], go_url)

    # ── GemeenteOplossingen ─────────────────────────────────────────────────
    if bron == 'go_scrape':
        return _get_go_platform_details(row['external_id'], row['url'])

    if bron != 'notubiz_api':
        return {}

    ctx = _ssl.create_default_context()
    try:
        req = _req.Request(
            f"https://api.notubiz.nl/events/{row['external_id']}",
            headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'},
        )
        resp = _req.urlopen(req, timeout=10, context=ctx)
        data = _json.loads(resp.read(131072).decode('utf-8'))
    except Exception as e:
        return {'error': str(e)}

    # event kan een list of dict zijn afhankelijk van de NotUBiz versie
    events = data.get('event', {})
    event = events[0] if isinstance(events, list) and events else (events if isinstance(events, dict) else {})

    def _list(obj, key):
        val = obj.get(key, {})
        if not val:
            return []
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            items = val.get(list(val.keys())[0], [])
            return [items] if isinstance(items, dict) else (items if isinstance(items, list) else [])
        return []

    # Agenda: NotUBiz gebruikt 'agenda' → 'agendaitem' of 'agenda_items'
    agenda_raw = _list(event, 'agenda_items') or _list(event.get('agenda', {}), 'agendaitem')
    speakers_raw = _list(event, 'speakers')
    docs_raw = _list(event, 'documents')

    agenda = []
    for item in agenda_raw:
        if not isinstance(item, dict):
            continue
        attrs = item.get('@attributes', {})
        agenda.append({
            'number': item.get('numbering', attrs.get('number', attrs.get('seq', ''))),
            'title': item.get('title', item.get('description', '')),
            'type': item.get('type', ''),
        })

    # Sprekers: probeer 'speakers' array, dan chairman/clerk/secretary velden
    speakers = []
    for sp in speakers_raw:
        if not isinstance(sp, dict):
            continue
        speakers.append({
            'name': sp.get('fullname', sp.get('name', '')),
            'function': sp.get('function', ''),
            'party': sp.get('party', sp.get('politicalParty', '')),
        })
    if not speakers:
        for field, func in [('chairman', 'Voorzitter'), ('clerk', 'Griffier'), ('secretary', 'Secretaris')]:
            val = event.get(field, '')
            name = val.strip() if isinstance(val, str) else ''
            if name:
                speakers.append({'name': name, 'function': func, 'party': ''})

    # Haal raadsleden op via organisations/{id}/parties (altijd, naast evt. event-sprekers)
    notubiz_org_id = row['notubiz_id']
    if notubiz_org_id:
        try:
            req2 = _req.Request(
                f"https://api.notubiz.nl/organisations/{notubiz_org_id}/parties",
                headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'},
            )
            resp2 = _req.urlopen(req2, timeout=10, context=ctx)
            pdata = _json.loads(resp2.read(131072).decode('utf-8'))
            parties_raw = pdata.get('parties', {})
            party_list = parties_raw if isinstance(parties_raw, list) else parties_raw.get('party', [])
            if isinstance(party_list, dict):
                party_list = [party_list]
            for party in party_list:
                pname = party.get('name', party.get('@attributes', {}).get('name', ''))
                members = party.get('members', {})
                mlist = members if isinstance(members, list) else members.get('member', [])
                if isinstance(mlist, dict):
                    mlist = [mlist]
                for m in mlist:
                    fullname = m.get('fullname', m.get('name', ''))
                    if fullname:
                        speakers.append({
                            'name': fullname,
                            'function': m.get('function', 'Raadslid'),
                            'party': pname,
                        })
        except Exception:
            pass

    documents = []
    for doc in docs_raw:
        if not isinstance(doc, dict):
            continue
        title = doc.get('title', doc.get('filename', ''))
        url = doc.get('url', doc.get('downloadUrl', ''))
        if title and url:
            documents.append({'title': title, 'url': url})

    return {
        'platform': 'notubiz',
        'external_id': row['external_id'],
        'agenda': agenda,
        'speakers': speakers,
        'documents': documents,
    }


@app.get("/api/meetings/{meeting_id}/summary")
def get_meeting_summary(meeting_id: int):
    return db.get_meeting_summary(meeting_id) or {}


@app.post("/api/meetings/{meeting_id}/summarize")
async def create_meeting_summary(meeting_id: int):
    """Genereer een eindverslag voor een vergadering op basis van alle artikelen."""
    gemeente = None
    conn = _meetings_conn()
    if conn:
        row = conn.execute(
            "SELECT g.naam FROM meetings m JOIN gemeenten g ON g.id = m.gemeente_id WHERE m.id = ?",
            (meeting_id,)
        ).fetchone()
        if row:
            gemeente = row['naam']
        conn.close()

    arts = db.get_articles_for_meeting(meeting_id)
    if not arts:
        raise HTTPException(400, "Geen artikelen gevonden voor deze vergadering")

    summary = await analysis.generate_meeting_summary(meeting_id, gemeente or 'onbekend', arts)
    if not summary:
        raise HTTPException(500, "Verslag genereren mislukt")

    db.save_meeting_summary(meeting_id, gemeente, summary)
    return {'summary': summary, 'created_at': datetime.now().isoformat()}


_PROCEDUREEL = {
    'opening', 'sluiting', 'rondvraag', 'mededelingen', 'ingekomen stukken',
    'vaststellen agenda', 'vaststelling agenda', 'notulen', 'verslag',
    'ingekomen', 'stukken ter kennisname', 'hamerstukken', 'collegevergadering',
    'agenda-initiatieven van de raad',
}

# Regex voor categorie-headers zoals "A(lgemeen)", "B(esluit)", "1.", "I."
import re as _re
_HEADER_PAT = _re.compile(r'^[A-Z]\([^)]+\)$|^[A-Z]\.$|^\d+\.$|^[IVX]+\.$')

def _agenda_titel(t: str) -> str:
    """Zet agenda-titel om naar leesbaar formaat (title case, strip datum-prefix)."""
    t = _re.sub(r'^\d{4}-\d{2}-\d{2}\s*', '', t).strip()
    return t.title() if t.isupper() else t

def _is_procedureel(t: str) -> bool:
    """True als agenda-item procedureel of een categorie-header is."""
    tl = t.lower().rstrip('.')
    if tl in _PROCEDUREEL:
        return True
    if any(tl.startswith(p) for p in _PROCEDUREEL):
        return True
    if _HEADER_PAT.match(t.strip()):
        return True
    return False


def _build_fallback_schets(meeting_type, gemeente, titel, agenda_items, doc_titles,
                           prev_titel, has_livestream=True, prev_summary=''):
    """Bouw een simpele schets zonder LLM, puur op basis van beschikbare data."""
    lines = []

    # Inhoudelijke agendapunten (gefilterd)
    inhoud_punten = []
    if agenda_items:
        for it in agenda_items:
            t = it.get('title', '').strip()
            if t and not _is_procedureel(t):
                inhoud_punten.append(_agenda_titel(t))

    # Alinea 1: agenda — parafraseer thema's, geen letterlijke opsomming
    if agenda_items:
        if inhoud_punten:
            n = len(inhoud_punten)
            if n == 1:
                lines.append(f"**Agenda**\n{gemeente} vergadert voornamelijk over {inhoud_punten[0].lower()}.")
            elif n == 2:
                lines.append(f"**Agenda**\n{gemeente} bespreekt onder meer {inhoud_punten[0].lower()} en {inhoud_punten[1].lower()}.")
            else:
                lines.append(f"**Agenda**\n{gemeente} heeft een gevarieerde agenda met onderwerpen als {inhoud_punten[0].lower()}, {inhoud_punten[1].lower()} en meer.")
        else:
            lines.append(f"**Agenda**\nDeze vergadering van {gemeente} heeft een voornamelijk procedurele agenda.")
    else:
        lines.append(f"**Agenda**\nDe agenda voor deze vergadering van {gemeente} is nog niet gepubliceerd.")

    # Vorige vergadering — met samenvatting en link naar huidige vergadering
    if prev_titel:
        if prev_summary:
            # Pak de eerste 2 zinnen als kerninhoud
            eerste_alinea = prev_summary.split('\n\n')[0].strip()
            zinnen = [z.strip() for z in _re.split(r'(?<=[.!?])\s+', eerste_alinea) if z.strip()]
            samenvatting = ' '.join(zinnen[:2])

            # Verbinding met de huidige vergadering detecteren
            huidige_context = ' '.join(
                [titel] + [i.get('title', '') if isinstance(i, dict) else str(i) for i in agenda_items[:5]]
            ).lower()
            prev_context = (prev_titel + ' ' + prev_summary[:200]).lower()

            # Haal agenda-item titels op als leesbare lijst
            def _item_titel(i):
                return (i.get('title', '') if isinstance(i, dict) else str(i)).strip()

            item_titels = [_item_titel(i) for i in agenda_items if _item_titel(i)]

            # Check of notulen van de vorige vergadering op de agenda staan
            notulen_item = next(
                (t for t in item_titels if 'notulen' in t.lower()), None
            )

            # Zoek een niet-procedureel inhoudelijk item voor de linktekst
            inhoudelijk = [
                t for t in item_titels
                if not _is_procedureel(t) and 'notulen' not in t.lower()
            ]

            if notulen_item and inhoudelijk:
                link_zin = f"De notulen van die vergadering staan op de agenda. Op de huidige vergadering staat verder onder meer {inhoudelijk[0].rstrip('.')} centraal."
            elif notulen_item:
                link_zin = "De notulen van die vergadering worden op de huidige vergadering vastgesteld."
            elif inhoudelijk:
                link_zin = f"Op de huidige vergadering staat onder meer {inhoudelijk[0].rstrip('.')} op de agenda."
            else:
                link_zin = "Dit was de direct voorafgaande vergadering."

            lines.append(f"**Vorige vergadering**\n*{prev_titel}*\n\n{samenvatting} {link_zin}")
        else:
            lines.append(f"**Vorige vergadering**\n*{prev_titel}*")

    # Livestream status
    if not has_livestream:
        lines.append("**Livestream**\nDeze vergadering wordt niet live gestreamd.")

    return '\n\n'.join(lines)


async def _generate_and_cache_schets(meeting_id: int):
    """Genereer vergaderschets en sla op in cache. Geeft schets-tekst terug, of None bij fout."""
    conn = _meetings_conn()
    if conn is None:
        return None
    m = conn.execute("""
        SELECT m.id, m.datum, m.titel, m.type, m.has_livestream,
               g.naam AS gemeente, g.id AS gemeente_id
        FROM meetings m JOIN gemeenten g ON g.id=m.gemeente_id
        WHERE m.id=?
    """, (meeting_id,)).fetchone()
    if not m:
        conn.close()
        return None

    # Bijlagen: titels + geëxtraheerde tekst
    docs_rows = conn.execute(
        "SELECT title, extracted_text FROM meeting_documents WHERE meeting_id=? ORDER BY agenda_item_nr, title LIMIT 20",
        (meeting_id,)
    ).fetchall()
    doc_titles = [r['title'] for r in docs_rows if r['title']]
    doc_excerpts = [
        {'title': r['title'], 'text': r['extracted_text']}
        for r in docs_rows if r['title'] and r['extracted_text']
    ]

    # Vorige vergadering van dezelfde gemeente (eerder dan deze datum)
    prev_row = conn.execute("""
        SELECT m.id, m.titel FROM meetings m
        WHERE m.gemeente_id=? AND m.datum < ?
        ORDER BY m.datum DESC LIMIT 1
    """, (m['gemeente_id'], m['datum'])).fetchone()
    conn.close()

    # Vorige samenvatting ophalen
    prev_summary = ''
    prev_titel = ''
    if prev_row:
        prev_titel = prev_row['titel'] or ''
        s = db.get_meeting_summary(prev_row['id'])
        if s:
            prev_summary = s.get('summary', '')

    # Platform-details voor agenda-items
    try:
        details = await get_platform_details(meeting_id)
        agenda_items = details.get('agenda', [])
    except Exception:
        agenda_items = []

    # Eerdere schets: recent + zelfde naam + agenda-overlap
    prev_schets_list = _fetch_prev_schets_list(
        gemeente_id=m['gemeente_id'],
        datum=m['datum'],
        meeting_type=m['type'] or '',
        titel=m['titel'] or '',
        agenda_items=agenda_items,
        doc_titles=doc_titles,
    )

    # Alleen LLM gebruiken als er transcript-tekst beschikbaar is
    transcript = db.get_transcript(meeting_id)
    if transcript:
        schets = await analysis.generate_meeting_schets(
            meeting_type=m['type'] or '',
            gemeente=m['gemeente'],
            datum=m['datum'],
            titel=m['titel'] or '',
            agenda_items=agenda_items,
            doc_titles=doc_titles,
            doc_excerpts=doc_excerpts,
            prev_summary=prev_summary,
            prev_titel=prev_titel,
            prev_schets_list=prev_schets_list,
        )
    else:
        schets = None

    if not schets:
        schets = _build_fallback_schets(
            meeting_type=m['type'] or '',
            gemeente=m['gemeente'],
            titel=m['titel'] or '',
            agenda_items=agenda_items,
            doc_titles=doc_titles,
            prev_titel=prev_titel,
            has_livestream=bool(m['has_livestream']),
            prev_summary=prev_summary,
        )

    # Cache opslaan
    ranst = db.get_db()
    ranst.execute(
        "INSERT OR REPLACE INTO meeting_schets (meeting_id, schets, created_at) VALUES (?, ?, datetime('now'))",
        (meeting_id, schets)
    )
    ranst.commit()
    ranst.close()
    return schets


@app.get("/api/meetings/{meeting_id}/schets")
async def get_meeting_schets(meeting_id: int):
    """Haal vergaderschets op (vooruitblik voor de vergadering begint)."""
    # Check cache
    ranst = db.get_db()
    row = ranst.execute(
        "SELECT schets, created_at FROM meeting_schets WHERE meeting_id=?", (meeting_id,)
    ).fetchone()
    ranst.close()
    if row:
        return {'schets': row['schets'], 'created_at': row['created_at']}

    schets = await _generate_and_cache_schets(meeting_id)
    if schets is None:
        return {'schets': '_Vergaderdata niet beschikbaar._'}

    conn = _meetings_conn()
    has_livestream = False
    if conn:
        m = conn.execute("SELECT has_livestream FROM meetings WHERE id=?", (meeting_id,)).fetchone()
        conn.close()
        if m:
            has_livestream = bool(m['has_livestream'])

    return {'schets': schets, 'has_livestream': has_livestream, 'created_at': datetime.now().isoformat()}


@app.get("/api/meetings/{meeting_id}/documents")
def get_meeting_documents(meeting_id: int):
    """Haal opgeslagen bijlagen op uit de DB, aangevuld met platform-data indien leeg."""
    conn = _meetings_conn()
    if conn is None:
        return []
    rows = conn.execute("""
        SELECT agenda_item_nr, agenda_item_title, title, download_url
        FROM meeting_documents WHERE meeting_id = ?
        ORDER BY agenda_item_nr, title
    """, (meeting_id,)).fetchall()
    conn.close()
    return [{'nr': r[0], 'agenda_item': r[1], 'title': r[2], 'url': r[3]} for r in rows]


@app.get("/api/meetings/{meeting_id}/raadsleden")
async def get_meeting_raadsleden(meeting_id: int):
    """Haal raadsleden op (gecached in DB, anders live van platform)."""
    conn = _meetings_conn()
    if conn is None:
        return []

    # Check of raadsleden tabel bestaat en gevuld is
    try:
        rows = conn.execute(
            "SELECT naam, partij, functie FROM raadsleden WHERE gemeente_id = "
            "(SELECT gemeente_id FROM meetings WHERE id = ?) ORDER BY naam",
            (meeting_id,)
        ).fetchall()
        conn.close()
        if rows:
            return [{'name': r[0], 'party': r[1], 'function': r[2]} for r in rows]
    except Exception:
        conn.close()

    # Fallback: haal live op via platform-details
    details = await get_platform_details(meeting_id)
    return details.get('speakers', [])


async def _prefetch_meeting_data(meeting_id: int):
    """Haal raadsleden + bijlagen op voor een vergadering en sla op in DB."""
    details = await get_platform_details(meeting_id)
    speakers = details.get('speakers', [])
    if not speakers:
        return

    conn = _meetings_conn()
    if conn is None:
        return

    # Maak raadsleden tabel als die niet bestaat
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raadsleden (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gemeente_id INTEGER NOT NULL,
            naam TEXT NOT NULL,
            partij TEXT,
            functie TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(gemeente_id, naam)
        )
    """)

    gemeente_id = conn.execute(
        "SELECT gemeente_id FROM meetings WHERE id = ?", (meeting_id,)
    ).fetchone()
    if not gemeente_id:
        conn.close()
        return

    gid = gemeente_id[0]
    for sp in speakers:
        conn.execute("""
            INSERT OR REPLACE INTO raadsleden (gemeente_id, naam, partij, functie)
            VALUES (?, ?, ?, ?)
        """, (gid, sp.get('name', ''), sp.get('party', ''), sp.get('function', 'Raadslid')))
    conn.commit()
    conn.close()


@app.get("/api/meetings/{meeting_id}/articles")
def get_meeting_articles(meeting_id: int):
    return db.get_articles_for_meeting(meeting_id)


@app.get("/api/transcript/{meeting_id}")
def get_transcript(meeting_id: int):
    return db.get_transcript(meeting_id)


# ── Artikelen ───────────────────────────────────────────────────────────────

@app.get("/api/articles")
def get_articles(
    gemeenten: Optional[str] = None,
    topics: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
):
    gem_list = [g.strip() for g in gemeenten.split(',')] if gemeenten else None
    top_list = [t.strip() for t in topics.split(',')] if topics else None
    return db.get_articles(
        gemeenten=gem_list,
        topics=top_list,
        level=level,
        limit=limit,
        offset=offset,
    )


@app.get("/api/articles/stream")
async def articles_stream(request: Request):
    """SSE stream — pusht nieuwe artikelen naar alle verbonden clients."""
    queue = asyncio.Queue()
    _sse_clients.append(queue)

    async def generator():
        try:
            yield 'data: {"type":"connected"}\n\n'
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30)
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse_clients.remove(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _broadcast_article(article: dict):
    """Push een nieuw artikel naar alle SSE clients."""
    payload = {**article, 'type': 'article'}
    for queue in list(_sse_clients):
        await queue.put(payload)


async def _broadcast_article_update(article_id: int, update_text: str):
    """Push een update voor een bestaand artikel naar alle SSE clients."""
    payload = {'type': 'article_update', 'id': article_id, 'update_text': update_text}
    for queue in list(_sse_clients):
        await queue.put(payload)


async def _broadcast_meeting_status(meeting_id: int, status: str):
    """Push een meeting-statuswijziging naar alle SSE clients."""
    payload = {'type': 'meeting_status', 'meeting_id': meeting_id, 'status': status}
    for queue in list(_sse_clients):
        await queue.put(payload)


# ── Ingest (lokale pipeline) ─────────────────────────────────────────────────

class IngestChunkRequest(BaseModel):
    meeting_id: int
    text: str
    speaker: Optional[str] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    gemeente: Optional[str] = None
    livestream_url: Optional[str] = None


@app.post("/api/ingest/chunk")
async def ingest_chunk(req: IngestChunkRequest):
    """Ontvang een transcript-chunk van de lokale pipeline.

    Slaat de chunk op en triggert analyse elke ANALYZE_EVERY chunks
    (standaard 10 × 30 sec = 5 min), met 30-min lookback window.
    """
    db.add_transcript_chunk(
        meeting_id=req.meeting_id,
        text=req.text,
        speaker=req.speaker,
        start_time=req.start_time,
        end_time=req.end_time,
    )

    count = _ingest_counters.get(req.meeting_id, 0) + 1
    _ingest_counters[req.meeting_id] = count

    # Markeer de vergadering als 'live' bij de eerste chunk
    if count == 1:
        conn = _meetings_conn()
        if conn:
            try:
                conn.execute(
                    "UPDATE meetings SET status = 'live', has_livestream = 1 WHERE id = ?",
                    (req.meeting_id,)
                )
                conn.commit()
            except Exception:
                pass
            finally:
                conn.close()
        await _broadcast_meeting_status(req.meeting_id, 'live')

    duration = db.get_meeting_duration(req.meeting_id)
    if count % ANALYZE_EVERY == 0 and duration >= ANALYZE_MIN_DURATION:
        # Per-meeting lock: voorkomt race condition bij burst van chunks
        lock = _analysis_locks.setdefault(req.meeting_id, asyncio.Lock())
        if lock.locked():
            # Analyse al bezig voor deze meeting — overslaan
            return {"ingested": True, "analyzed": False, "skipped": True}

        async with lock:
            window_text = db.get_window_text(req.meeting_id)
            recent = db.get_recent_articles_for_meeting(req.meeting_id)

            alerts = await analysis.analyze_window_for_all_topics(
                text=window_text,
                gemeente=req.gemeente,
                meeting_id=req.meeting_id,
                livestream_url=req.livestream_url,
                recent_alerts=recent,
            )

        generated = 0
        for alert in alerts:
            action = alert.get('action', 'new')
            prev_id = alert.get('prev_id')
            update_text = alert.get('update_text')

            if action == 'update' and prev_id and update_text:
                # Voeg update toe aan bestaand artikel — geen nieuw artikel
                db.add_article_update(prev_id, update_text)
                await _broadcast_article_update(prev_id, update_text)
                generated += 1
            else:
                # Nieuw artikel aanmaken
                article_id = db.create_article(
                    meeting_id=req.meeting_id,
                    gemeente=req.gemeente,
                    topic=alert['topic'],
                    level=alert['level'],
                    title=alert['title'],
                    body=alert['summary'],
                    score=alert['score'],
                    indicators=alert.get('indicators'),
                    livestream_url=alert.get('livestream_url'),
                    t_start=req.start_time,
                    t_end=req.end_time,
                    topics=alert.get('topics'),
                )
                article = {
                    'id': article_id,
                    'meeting_id': req.meeting_id,
                    'gemeente': req.gemeente,
                    'topic': alert['topic'],
                    'level': alert['level'],
                    'title': alert['title'],
                    'body': alert['summary'],
                    'score': alert['score'],
                    'indicators': alert.get('indicators'),
                    'livestream_url': alert.get('livestream_url'),
                    't_start': req.start_time,
                    't_end': req.end_time,
                    'created_at': datetime.now().isoformat(),
                }
                await _broadcast_article(article)
                generated += 1

        return {"ingested": True, "analyzed": True, "articles": generated}

    return {"ingested": True, "analyzed": False}


@app.post("/api/meetings/{meeting_id}/set-live")
async def set_meeting_live(meeting_id: int):
    """Markeer vergadering als live (aangeroepen door monitor)."""
    conn = _meetings_conn()
    if conn:
        try:
            conn.execute(
                "UPDATE meetings SET status = 'live', has_livestream = 1 WHERE id = ?",
                (meeting_id,)
            )
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()
    await _broadcast_meeting_status(meeting_id, 'live')
    return {"ok": True}


@app.post("/api/meetings/{meeting_id}/finish")
async def finish_meeting(meeting_id: int):
    """Markeer vergadering als afgelopen (aangeroepen door pipeline bij afsluiten)."""
    changed = False
    conn = _meetings_conn()
    if conn:
        try:
            cur = conn.execute(
                "UPDATE meetings SET status = 'afgelopen', ended_at = datetime('now') "
                "WHERE id = ? AND status = 'live'",
                (meeting_id,)
            )
            conn.commit()
            changed = cur.rowcount > 0
        except Exception:
            pass
        finally:
            conn.close()
    if changed:
        await _broadcast_meeting_status(meeting_id, 'afgelopen')
        # Vernieuw schets van de eerstvolgende vergadering van dezelfde gemeente
        asyncio.create_task(_refresh_followup_schets(meeting_id))
    return {"ok": True}


# ── Live stream watcher ──────────────────────────────────────────────────────

def _page_indicates_live(html: str) -> bool:
    """Kijk of een vergaderpagina signalen van een actieve stream bevat."""
    import re
    if not html:
        return False
    patterns = [
        r'"isLive"\s*:\s*true',
        r'"live"\s*:\s*true',
        r'is-live',
        r'class="[^"]*\blive\b[^"]*"',
        r'\.m3u8',
        r'videojs',
        r'jwplayer',
        r'hls\.js',
        r'companywebcast\.com',
        r'wowza',
        r'brightcove',
    ]
    return any(re.search(p, html, re.I) for p in patterns)


def _page_has_stream_embed(html: str) -> bool:
    """Kijk of een vergaderpagina een video/stream-embed bevat (pre-live detectie).
    Ruimere set patronen dan _page_indicates_live — detecteert of een stream VERWACHT wordt."""
    import re
    if not html:
        return False
    patterns = [
        r'videojs', r'jwplayer', r'hls\.js',
        r'companywebcast\.com',
        r'wowza', r'brightcove',
        r'youtube\.com/embed', r'youtu\.be/',
        r'vimeo\.com/video',
        r'livestream\.com',
        r'raadlive\.nl',
        r'webcastsdk', r'webcast',
        r'\.m3u8', r'rtmp://',
        r'<video\b',
        r'notubiz\.nl.*["\']video',
        r'player\.php\?',
        r'embed.*stream|stream.*embed',
        r'ibabs.*live|live.*ibabs',
    ]
    return any(re.search(p, html, re.I) for p in patterns)


async def _detect_livestream_flags():
    """Scan aankomende meetings op video-embeds en zet has_livestream=1 indien gevonden.
    Draait als achtergrondtaak bij server-start, in batches van 5 om belasting te beperken."""
    conn = _meetings_conn()
    if not conn:
        return
    today = datetime.now().strftime('%Y-%m-%d')
    end = (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT id, url FROM meetings
        WHERE has_livestream = 0 AND datum BETWEEN ? AND ?
          AND url IS NOT NULL AND url != ''
          AND status IN ('scheduled', 'gemist')
        ORDER BY datum, tijd
    """, (today, end)).fetchall()
    conn.close()

    if not rows:
        return
    print(f'[livestream_detect] {len(rows)} meetings scannen op stream-embed…')

    found = 0
    for i in range(0, len(rows), 5):
        batch = rows[i:i+5]
        results = await asyncio.gather(
            *[_fetch_for_live_check(r['url']) for r in batch],
            return_exceptions=True
        )
        for r, html in zip(batch, results):
            if isinstance(html, str) and _page_has_stream_embed(html):
                def _write_flag(rid=r['id']):
                    try:
                        c = _meetings_conn()
                        if c:
                            c.execute("UPDATE meetings SET has_livestream=1 WHERE id=?", (rid,))
                            c.commit()
                            c.close()
                    except Exception:
                        pass
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, _write_flag)
                found += 1
        await asyncio.sleep(2)  # ruimte voor andere taken tussen batches

    print(f'[livestream_detect] klaar — {found} nieuwe streams gevonden')


async def _fetch_for_live_check(url: str) -> str:
    """Haal meeting-pagina op in een thread (blokkeert event loop niet)."""
    import urllib.request as _req
    import ssl as _ssl

    def _sync():
        try:
            ctx = _ssl.create_default_context()
            req = _req.Request(url, headers={'User-Agent': 'Mozilla/5.0 (RANST/1.0)'})
            resp = _req.urlopen(req, timeout=8, context=ctx)
            return resp.read(32768).decode('utf-8', errors='replace')
        except Exception:
            return ''

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync)


async def _auto_finish_meeting(meeting_id: int):
    conn = _meetings_conn()
    if not conn:
        return
    try:
        cur = conn.execute(
            "UPDATE meetings SET status='afgelopen', ended_at=datetime('now') "
            "WHERE id=? AND status='live'", (meeting_id,)
        )
        conn.commit()
        changed = cur.rowcount > 0
    except Exception:
        changed = False
    finally:
        conn.close()
    if changed:
        await _broadcast_meeting_status(meeting_id, 'afgelopen')


async def _auto_set_live(meeting_id: int):
    conn = _meetings_conn()
    if not conn:
        return
    try:
        cur = conn.execute(
            "UPDATE meetings SET status='live', has_livestream=1 "
            "WHERE id=? AND status IN ('scheduled','gemist')", (meeting_id,)
        )
        conn.commit()
        changed = cur.rowcount > 0
    except Exception:
        changed = False
    finally:
        conn.close()
    if changed:
        await _broadcast_meeting_status(meeting_id, 'live')


async def _do_stream_check():
    """Check live-status van alle vergaderingen van vandaag.

    Tijdvenster per meeting:
      - 10 min vóór starttijd → begin pingen
      - tot 2 uur ná starttijd → detecteer live-start
      - al live: controleer of stream nog actief is (auto-finish na 5 uur)
    Draait elk uur via _livestream_watcher.
    Checkt ALLE meetings met URL, ongeacht has_livestream-vlag.
    """
    conn = _meetings_conn()
    if not conn:
        return

    # Vergadertijden zijn opgeslagen in Nederlandse tijd — gebruik NL-tijdzone
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo('Europe/Amsterdam'))
    except Exception:
        from datetime import timezone, timedelta
        now = datetime.now(timezone(timedelta(hours=1)))
    today = now.strftime('%Y-%m-%d')
    now_min = now.hour * 60 + now.minute

    # Ook gisteren meenemen voor vergaderingen die na 22:00 begonnen
    # (22:00 NL + 2h window overlapt met UTC-datum van vandaag op de server)
    from datetime import timedelta as _td
    yesterday = (now - _td(days=1)).strftime('%Y-%m-%d')

    try:
        # Meetings die al als live gemarkeerd zijn — controleer of ze nog draaien
        currently_live = conn.execute(
            "SELECT m.id, m.url, m.tijd FROM meetings m "
            "WHERE m.status='live' AND m.datum IN (?,?)", (today, yesterday)
        ).fetchall()

        # Alle meetings van vandaag (en gisteren) met een URL
        candidates = conn.execute(
            "SELECT m.id, m.url, m.tijd, m.datum FROM meetings m "
            "WHERE m.datum IN (?,?) AND m.status IN ('scheduled','gemist') "
            "AND m.tijd IS NOT NULL AND m.url IS NOT NULL AND m.url != ''",
            (today, yesterday)
        ).fetchall()
    finally:
        conn.close()

    # Check bestaande live-meetings: zijn ze nog live?
    for m in currently_live:
        url = m['url'] or ''
        tijd = (m['tijd'] or '00:00')[:5]
        try:
            h, mi = map(int, tijd.split(':'))
            start_min = h * 60 + mi
        except Exception:
            start_min = 0

        # Auto-finish na 5 uur (vergaderingen duren zelden langer)
        if now_min > start_min + 300:
            await _auto_finish_meeting(m['id'])
            continue
        # Niet auto-finishen op basis van paginainhoud: moderne streamingplatforms
        # laden hun player via JavaScript, zodat een statische fetch nooit
        # live-indicatoren teruggeeft, ook als de vergadering wél live is.

    # Markeer meetings als 'afgelopen' als ze lang geleden begonnen en geen stream hebben
    # (scheduled meetings die niet live zijn geworden)
    finish_conn = _meetings_conn()
    if finish_conn:
        try:
            # Geen livestream: afgelopen na 2.5 uur
            finish_conn.execute("""
                UPDATE meetings SET status='afgelopen', ended_at=COALESCE(ended_at, datetime('now'))
                WHERE status='scheduled'
                  AND datum IN (?,?)
                  AND has_livestream = 0
                  AND tijd IS NOT NULL
                  AND (CAST(substr(tijd,1,2) AS INTEGER)*60 + CAST(substr(tijd,4,2) AS INTEGER))
                      <= ? - 150
            """, (today, yesterday, now_min))
            # Met livestream maar nooit live gegaan: afgelopen na 4 uur
            finish_conn.execute("""
                UPDATE meetings SET status='afgelopen', ended_at=COALESCE(ended_at, datetime('now'))
                WHERE status='scheduled'
                  AND datum IN (?,?)
                  AND has_livestream = 1
                  AND tijd IS NOT NULL
                  AND (CAST(substr(tijd,1,2) AS INTEGER)*60 + CAST(substr(tijd,4,2) AS INTEGER))
                      <= ? - 240
            """, (today, yesterday, now_min))
            finish_conn.commit()
        finally:
            finish_conn.close()

    # Check kandidaten: 5 min voor start tot 2 uur na start
    # Gisterse meetings krijgen start_min + 1440 (volgende dag = +24u) zodat
    # het venster correct uitkomt t.o.v. now_min
    check_tasks = []
    for m in candidates:
        url = m['url'] or ''
        tijd = (m['tijd'] or '99:99')[:5]
        if not url:
            continue
        try:
            h, mi = map(int, tijd.split(':'))
            start_min = h * 60 + mi
        except Exception:
            continue
        # Gisterse datum: verschuif start_min naar gisteren (negatief t.o.v. vandaag)
        datum = m['datum'] if len(m) > 3 else today
        if datum == yesterday:
            start_min -= 1440
        # Tijdvenster: 10 min voor start tot 2 uur na start
        if start_min - 10 <= now_min <= start_min + 120:
            check_tasks.append((m['id'], url))

    async def _check_one(mid, url):
        html = await _fetch_for_live_check(url)
        if _page_indicates_live(html):
            await _auto_set_live(mid)

    if check_tasks:
        print(f'[stream_check] {len(check_tasks)} meetings pingen...')
        await asyncio.gather(*[_check_one(mid, url) for mid, url in check_tasks],
                             return_exceptions=True)


async def _livestream_watcher():
    """Achtergrond-task: ping streams elk uur.
    Tijdvenster: 10 min voor start t/m 2 uur na start.
    """
    # Directe startup-scan
    try:
        await _do_stream_check()
    except Exception as e:
        print(f'[stream_watcher] startup-scan: {e}')

    while True:
        await asyncio.sleep(3600)  # elk uur
        try:
            await _do_stream_check()
        except Exception as e:
            print(f'[stream_watcher] {e}')


def _sync_go_meetings_sync():
    """
    Synchroniseer GO-vergaderingen voor alle gemeenten met go_url.
    Voegt nieuwe vergaderingen toe en verwijdert vergaderingen die niet meer
    op de website staan (geannuleerd / verzet).
    Draait in een thread zodat de event loop niet blokkeert.
    """
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from go_scraper import GoScraper
    except ImportError:
        print('[go_sync] go_scraper niet gevonden, skip')
        return

    import sqlite3
    from datetime import date

    conn = sqlite3.connect(config.MEETINGS_DB)
    conn.row_factory = sqlite3.Row
    today = date.today().isoformat()

    gemeenten = conn.execute(
        "SELECT id, naam, go_url FROM gemeenten WHERE go_url IS NOT NULL AND go_url != ''"
    ).fetchall()

    added = deleted = 0
    for g in gemeenten:
        try:
            scraper = GoScraper(g['go_url'])
            meetings = scraper.get_meetings(months_back=0, months_forward=4)
        except Exception as e:
            print(f'[go_sync] {g["naam"]}: scrape fout — {e}')
            continue

        # Verzamelde externe IDs van huidige scrape
        fresh_external_ids = {m['path'] for m in meetings}

        # Verwijder scheduled/aankomende meetings die verdwenen zijn van de site
        old_rows = conn.execute(
            """SELECT id, external_id FROM meetings
               WHERE gemeente_id = ? AND bron = 'go_scrape'
               AND status = 'scheduled' AND datum >= ?""",
            (g['id'], today)
        ).fetchall()
        for row in old_rows:
            if row['external_id'] and row['external_id'] not in fresh_external_ids:
                conn.execute("DELETE FROM meetings WHERE id = ?", (row['id'],))
                deleted += 1

        # Voeg nieuwe meetings toe
        for m in meetings:
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO meetings
                       (gemeente_id, external_id, datum, tijd, titel, type, url, bron, has_livestream)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'go_scrape', 0)""",
                    (g['id'], m['path'], m['datum'], m['tijd'],
                     m['title'], m['meeting_type'], m['url'])
                )
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    added += 1
            except Exception:
                pass

    conn.commit()
    conn.close()
    print(f'[go_sync] klaar: {added} toegevoegd, {deleted} verwijderd')


async def _go_sync_watcher():
    """Achtergrond-task: herlaad GO-vergaderingen elke 24 uur."""
    await asyncio.sleep(60)  # Wacht even na opstarten
    while True:
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, _sync_go_meetings_sync)
        except Exception as e:
            print(f'[go_sync] {e}')
        await asyncio.sleep(86400)  # 24 uur


def _sync_notubiz_meetings_sync():
    """Sync aankomende NotUBiz-vergaderingen voor alle gemeenten met notubiz_id."""
    import ssl as _ssl, urllib.request as _ureq, json as _jso
    from datetime import date as _date

    conn = _meetings_conn()
    if not conn:
        return
    conn.execute("PRAGMA journal_mode=WAL")
    orgs = conn.execute(
        "SELECT id, naam, notubiz_id FROM gemeenten WHERE notubiz_id IS NOT NULL"
    ).fetchall()
    conn.close()

    ctx = _ssl.create_default_context()
    today = _date.today().isoformat()
    year  = _date.today().year
    added = 0

    for g in orgs:
        try:
            url = (f'https://api.notubiz.nl/organisations/{g["notubiz_id"]}'
                   f'/events?year={year}&limit=100')
            req  = _ureq.Request(url, headers={'User-Agent': 'Mozilla/5.0',
                                               'Accept': 'application/json'})
            resp = _ureq.urlopen(req, timeout=15, context=ctx)
            data = _jso.loads(resp.read().decode('utf-8', errors='replace'))

            events = data.get('event', [])
            if not isinstance(events, list):
                events = [events] if events else []

            c2 = _meetings_conn()
            if not c2:
                continue
            c2.execute("PRAGMA journal_mode=WAL")
            for ev in events:
                ext_id = str(ev.get('id', ''))
                raw_dt = (ev.get('date') or '')
                datum  = raw_dt[:10]
                if not datum or datum < today:
                    continue
                tijd  = raw_dt[11:16] or None
                titel = ev.get('name', '') or ''
                url_v = ev.get('url', '') or ''
                try:
                    c2.execute(
                        """INSERT OR IGNORE INTO meetings
                           (gemeente_id, external_id, datum, tijd, titel, url, bron, has_livestream)
                           VALUES (?, ?, ?, ?, ?, ?, 'notubiz_api', 0)""",
                        (g['id'], ext_id, datum, tijd, titel, url_v)
                    )
                    if c2.execute("SELECT changes()").fetchone()[0] > 0:
                        added += 1
                except Exception:
                    pass
            c2.commit()
            c2.close()
        except Exception as e:
            print(f'[notubiz_sync] {g["naam"]}: {e}')

    print(f'[notubiz_sync] klaar: {added} nieuwe vergaderingen')


def _sync_ibabs_meetings_sync():
    """Sync iBabs vergaderingen voor alle iBabs-gemeenten."""
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    try:
        from ibabs_sync import cmd_sync
        cmd_sync(days=90, sync_docs=False)
    except Exception as e:
        print(f'[ibabs_sync] fout: {e}')


def _sync_qualigraf_meetings_sync():
    """Sync Qualigraf/Parlaeus vergaderingen."""
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    try:
        from qualigraf_sync import cmd_sync
        cmd_sync(days=90, sync_docs=False)
    except Exception as e:
        print(f'[qualigraf_sync] fout: {e}')


async def _daily_meeting_watcher():
    """Dagelijkse sync van alle vergaderplatforms (NotUBiz, iBabs, Qualigraf, GO)."""
    await asyncio.sleep(3600)  # 1 uur na start, dan dagelijks om ~03:00
    while True:
        print('[daily_sync] vergadering-sync gestart...')
        loop = asyncio.get_event_loop()
        for fn, label in [
            (_sync_notubiz_meetings_sync, 'notubiz'),
            (_sync_ibabs_meetings_sync,   'ibabs'),
            (_sync_qualigraf_meetings_sync,'qualigraf'),
            (_sync_go_meetings_sync,       'go'),
        ]:
            try:
                await loop.run_in_executor(None, fn)
            except Exception as e:
                print(f'[daily_sync] {label}: {e}')
        print('[daily_sync] vergadering-sync klaar')
        await asyncio.sleep(86400)


async def _monthly_raadsleden_watcher():
    """Maandelijkse sync van raadsleden via scrape-scripts."""
    import subprocess as _sub, sys as _sys, os as _os
    await asyncio.sleep(7200)  # 2 uur na start, dan maandelijks
    while True:
        print('[monthly_sync] raadsleden-sync gestart...')
        base = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        for script in ['scrape_raadsleden.py', 'scrape_missing_raadsleden.py']:
            path = _os.path.join(base, script)
            if _os.path.exists(path):
                try:
                    _sub.run([_sys.executable, path], timeout=7200,
                             capture_output=True)
                    print(f'[monthly_sync] {script} klaar')
                except Exception as e:
                    print(f'[monthly_sync] {script}: {e}')
        await asyncio.sleep(30 * 86400)  # ~30 dagen


async def _refresh_followup_schets(meeting_id: int):
    """Genereer de schets van de vervolgvergadering opnieuw met verse context."""
    conn = _meetings_conn()
    if not conn:
        return
    try:
        row = conn.execute(
            "SELECT gemeente_id, datum FROM meetings WHERE id = ?", (meeting_id,)
        ).fetchone()
        if not row:
            return
        nxt = conn.execute(
            "SELECT id FROM meetings WHERE gemeente_id = ? AND datum > ? "
            "ORDER BY datum, tijd LIMIT 1",
            (row['gemeente_id'], row['datum'])
        ).fetchone()
        if not nxt:
            return
        followup_id = nxt['id']
    except Exception:
        return
    finally:
        conn.close()

    # Wis oude cache zodat _generate_and_cache_schets een verse versie opslaat
    ranst = db.get_db()
    ranst.execute("DELETE FROM meeting_schets WHERE meeting_id = ?", (followup_id,))
    ranst.commit()
    ranst.close()

    print(f"[finish] Vergaderschets herbouwen voor follow-up meeting {followup_id}...")
    schets = await _generate_and_cache_schets(followup_id)
    if schets:
        print(f"[finish] ✓ Schets klaar voor meeting {followup_id} ({len(schets)} tekens)")


# ── Test ────────────────────────────────────────────────────────────────────

@app.post("/api/test/notify")
async def test_notify():
    """Stuur een nep-artikel via SSE om de notificatie-layout te testen."""
    article = {
        'id': 99999,
        'meeting_id': 1,
        'gemeente': 'Amsterdam',
        'topic': 'bestuur_politiek',
        'level': 'pers',
        'title': 'Gemeenteraad stemt in met nieuw woningbouwplan',
        'body': 'De gemeenteraad van Amsterdam heeft dinsdagavond ingestemd met een ambitieus woningbouwplan voor de Amstelkwartier-wijk. Er worden 5.000 nieuwe woningen gebouwd, waarvan 40 procent in de sociale sector. De stemming was 32 voor en 13 tegen.',
        'score': 0.87,
        'indicators': None,
        'livestream_url': None,
        't_start': None,
        't_end': None,
        'created_at': datetime.now().isoformat(),
    }
    await _broadcast_article(article)
    return {'ok': True}


@app.post("/api/test/bulk")
async def test_bulk(gemeenten: Optional[str] = Query(None)):
    """Stuur meerdere gesimuleerde artikelen voor layout-test."""
    import asyncio as _aio
    now = datetime.now()

    # Use provided municipalities or fall back to defaults
    gem_list = [g.strip() for g in gemeenten.split(',')] if gemeenten else [
        'Westland', 'Tilburg', 'Groningen', 'Alphen aan den Rijn', 'Venlo', 'Súdwest-Fryslân'
    ]
    # Cycle through provided municipalities for the 12 articles
    def gem(i): return gem_list[i % len(gem_list)]

    articles = [
        {'id':90001,'meeting_id':10,'gemeente':gem(0),'topic':'bestuur_politiek','level':'pers',
         'title':f'Grootste partij behoudt negen zetels in {gem(0)}',
         'body':f'De lokale lijsttrekker blijft de grootste partij in de gemeenteraad van {gem(0)} met negen zetels. De VVD verliest twee zetels. Een nieuwe lokale partij maakt een opvallende entree. De coalitieonderhandelingen starten naar verwachting volgende week.',
         'score':0.88,'indicators':None,'livestream_url':None,'t_start':None,'t_end':None,'created_at':now.isoformat()},
        {'id':90002,'meeting_id':10,'gemeente':gem(0),'topic':'bestuur_politiek','level':'bestuurlijk',
         'title':f'Coalitieformatie {gem(0)}: winnaar zoekt meerderheid',
         'body':f'Samenvatting: De winnende partij in {gem(0)} behoudt negen zetels. Een nieuwe lokale lijst maakt een opvallende entree. De coalitieformatie wordt complex door de versnippering.\n\nStandpunten: Winnende partij wil continuïteit van beleid. CDA staat open voor coalitiegesprekken. Nieuwe lijst wil invloed uitoefenen op lokaal beleid.\n\nActiepunten: 1. Neem kennis van de definitieve zetelverdeling. 2. Monitor de coalitieonderhandelingen. 3. Beoordeel impact op provinciaal beleid.',
         'score':0.82,'indicators':None,'livestream_url':None,'t_start':None,'t_end':None,'created_at':now.isoformat()},
        {'id':90003,'meeting_id':11,'gemeente':gem(1),'topic':'wonen_ruimte','level':'pers',
         'title':f'{gem(1)} stemt in met bouw van 8.200 woningen tot 2030',
         'body':f'De gemeenteraad van {gem(1)} heeft ingestemd met het nieuwe woonprogramma 2025-2030. Het plan voorziet in de bouw van 8.200 woningen, waarvan veertig procent in de sociale sector. VVD stemde tegen vanwege het hoge aandeel sociale huur.',
         'score':0.86,'indicators':None,'livestream_url':None,'t_start':None,'t_end':None,'created_at':now.isoformat()},
        {'id':90004,'meeting_id':11,'gemeente':gem(1),'topic':'wonen_ruimte','level':'bestuurlijk',
         'title':f'Woonprogramma {gem(1)} 2025-2030: 8.200 woningen, 40% sociaal',
         'body':f'Samenvatting: {gem(1)} stelt een woonprogramma vast met 8.200 nieuwe woningen tot 2030, waarvan 40% sociaal. VVD stemde tegen.\n\nStandpunten: Wethouder beschouwt het als historisch. VVD vreest verdringen van middenhuur. GroenLinks wil aanvullende duurzaamheidseisen.\n\nActiepunten: 1. Toets het programma aan de provinciale woonvisie. 2. Controleer de 40%-norm. 3. Plan de goedkeuringsprocedure in.',
         'score':0.81,'indicators':None,'livestream_url':None,'t_start':None,'t_end':None,'created_at':now.isoformat()},
        {'id':90005,'meeting_id':12,'gemeente':gem(2),'topic':'financien_toezicht','level':'pers',
         'title':f'{gem(2)} presenteert begroting met tekort van 14,2 miljoen euro',
         'body':f'De gemeente {gem(2)} presenteerde een begroting 2026 met een tekort van 14,2 miljoen euro. Wethouder Financiën wees op de dalende rijksbijdragen na 2026 als hoofdoorzaak. De raad is verdeeld: coalitiepartijen willen bezuinigen op subsidies, de oppositie pleit voor het aanspreken van reserves.',
         'score':0.84,'indicators':None,'livestream_url':None,'t_start':None,'t_end':None,'created_at':now.isoformat()},
        {'id':90006,'meeting_id':12,'gemeente':gem(2),'topic':'financien_toezicht','level':'bestuurlijk',
         'title':f'Begrotingstekort {gem(2)} 2026: toezicht en ravijnjaar-effect',
         'body':f'Samenvatting: {gem(2)} kampt met een begrotingstekort van 14,2 miljoen euro in 2026, veroorzaakt door teruglopende rijksbijdragen. De provincie houdt financieel toezicht.\n\nStandpunten: Wethouder wil bezuinigingen spreiden over vier jaar. PvdA wil cultuur en welzijn ontzien. SP pleit voor aanspreken van reserves.\n\nActiepunten: 1. Activeer het financieel toezichtprotocol. 2. Controleer solvabiliteitsnorm. 3. Volg de raadsvergadering volgende week.',
         'score':0.77,'indicators':None,'livestream_url':None,'t_start':None,'t_end':None,'created_at':now.isoformat()},
        {'id':90007,'meeting_id':13,'gemeente':gem(3),'topic':'bereikbaarheid_infra','level':'pers',
         'title':f'{gem(3)} dringt bij provincie aan op herstel buslijn',
         'body':f'De gemeenteraad van {gem(3)} heeft een motie aangenomen die het college opdraagt bij de provincie aan te dringen op herstel van een regionale buslijn. De lijn werd in 2024 opgeheven door bezuinigingen bij de vervoersconcessie.',
         'score':0.73,'indicators':None,'livestream_url':None,'t_start':None,'t_end':None,'created_at':now.isoformat()},
        {'id':90008,'meeting_id':13,'gemeente':gem(3),'topic':'bereikbaarheid_infra','level':'bestuurlijk',
         'title':f'Motie OV-herstel {gem(3)}: provincie gevraagd om actie',
         'body':f'Samenvatting: De raad van {gem(3)} dringt aan op herstel van een buslijn via een aangenomen motie. De lijn werd geschrapt bij de heraanbesteding van de OV-concessie.\n\nStandpunten: CDA wijst op sociale isolatie van kernen. PvdA koppelt het aan de bredere bereikbaarheidsdiscussie. VVD vindt het primair een provinciale verantwoordelijkheid.\n\nActiepunten: 1. Beoordeel het verzoek binnen de lopende concessie. 2. Inventariseer vergelijkbare verzoeken. 3. Toets of herstel mogelijk is binnen het concessiebudget.',
         'score':0.71,'indicators':None,'livestream_url':None,'t_start':None,'t_end':None,'created_at':now.isoformat()},
        {'id':90009,'meeting_id':14,'gemeente':gem(4),'topic':'economie_innovatie','level':'pers',
         'title':f'{gem(4)} tekent intentieverklaring voor grote bedrijfsuitbreiding',
         'body':f'{gem(4)} heeft een intentieverklaring getekend met drie bedrijven voor een uitbreiding van een bedrijventerrein. Het gaat om een investering van circa 85 miljoen euro en naar schatting 600 nieuwe arbeidsplaatsen. Milieuorganisaties uitten bezwaar vanwege een nabijgelegen Natura 2000-gebied.',
         'score':0.89,'indicators':None,'livestream_url':None,'t_start':None,'t_end':None,'created_at':now.isoformat()},
        {'id':90010,'meeting_id':14,'gemeente':gem(4),'topic':'economie_innovatie','level':'bestuurlijk',
         'title':f'Bedrijfsuitbreiding {gem(4)}: stikstofrisico bij Natura 2000-gebied',
         'body':f'Samenvatting: {gem(4)} tekent een intentieverklaring voor uitbreiding van een bedrijventerrein. De investering bedraagt 85 miljoen euro. Het plan grenst aan een Natura 2000-gebied, wat juridische complicaties kan opleveren.\n\nStandpunten: Wethouder ziet kansen voor de regionale economie. GroenLinks stelt vraagtekens bij stikstofimpact. Milieuorganisaties kondigen bezwaar aan.\n\nActiepunten: 1. Toets binnen de provinciale omgevingsvisie. 2. Toets de stikstofruimte. 3. Provincie is bevoegd gezag voor de vergunning.',
         'score':0.83,'indicators':None,'livestream_url':None,'t_start':None,'t_end':None,'created_at':now.isoformat()},
        {'id':90011,'meeting_id':15,'gemeente':gem(5),'topic':'klimaat_natuur_stikstof','level':'pers',
         'title':f'{gem(5)} versnelt warmtenet-uitrol na historisch raadsbesluit',
         'body':f'De gemeenteraad van {gem(5)} heeft unaniem besloten de uitrol van het warmtenet te versnellen. Het budget wordt verhoogd met 12 miljoen euro. Twee wijken worden als eerste aangesloten.',
         'score':0.74,'indicators':None,'livestream_url':None,'t_start':None,'t_end':None,'created_at':now.isoformat()},
        {'id':90012,'meeting_id':15,'gemeente':gem(5),'topic':'klimaat_natuur_stikstof','level':'bestuurlijk',
         'title':f'Warmtenet {gem(5)}: versneld schema, 12 miljoen extra',
         'body':f'Samenvatting: De raad van {gem(5)} besluit de warmtenet-uitrol te versnellen met 12 miljoen euro extra budget.\n\nStandpunten: Wethouder noemt het historisch voor de energietransitie. VVD vraagt om financieel risicobeheer. GroenLinks wil aanvullende duurzaamheidseisen bij nieuwbouw.\n\nActiepunten: 1. Opdracht aan netbeheerder voor versneld schema. 2. Communicatieplan bewoners opstellen. 3. Koppeling met provinciaal warmtebeleid beoordelen.',
         'score':0.76,'indicators':None,'livestream_url':None,'t_start':None,'t_end':None,'created_at':now.isoformat()},
    ]
    for a in articles:
        await _broadcast_article(a)
        await _aio.sleep(0.1)
    return {'ok': True, 'sent': len(articles)}


# ── Stats ───────────────────────────────────────────────────────────────────

@app.get("/api/gemeenten")
def get_all_gemeenten():
    """Lijst van alle gemeenten met naam en wapen_url."""
    conn = _meetings_conn()
    if conn is None:
        return []
    rows = conn.execute("SELECT naam, wapen_url FROM gemeenten ORDER BY naam").fetchall()
    conn.close()
    return [{'naam': r[0], 'wapen_url': r[1]} for r in rows]


@app.get("/api/gemeenten/urls")
def get_gemeenten_urls():
    """Alle gemeenten met hun geconfigureerde URLs en platforms."""
    conn = _meetings_conn()
    if conn is None:
        return []
    rows = conn.execute(
        "SELECT naam, platforms, go_url, website, wapen_url FROM gemeenten ORDER BY naam"
    ).fetchall()
    conn.close()
    return [{'naam': r[0], 'platforms': r[1], 'go_url': r[2], 'website': r[3], 'wapen_url': r[4]} for r in rows]


class GemeenteUrlUpdate(BaseModel):
    go_url: Optional[str] = None
    website: Optional[str] = None


@app.put("/api/gemeente/{naam}/url")
def update_gemeente_url(naam: str, body: GemeenteUrlUpdate):
    """Sla go_url en/of website op voor een gemeente."""
    conn = _meetings_conn()
    if conn is None:
        raise HTTPException(status_code=503, detail="Database niet beschikbaar")
    updates, params = [], []
    if body.go_url is not None:
        updates.append("go_url = ?"); params.append(body.go_url or None)
    if body.website is not None:
        updates.append("website = ?"); params.append(body.website or None)
    if not updates:
        raise HTTPException(status_code=400, detail="Niets om bij te werken")
    params.append(naam)
    affected = conn.execute(
        f"UPDATE gemeenten SET {', '.join(updates)} WHERE naam = ?", params
    ).rowcount
    conn.commit()
    conn.close()
    if affected == 0:
        raise HTTPException(status_code=404, detail="Gemeente niet gevonden")
    return {"ok": True}


@app.get("/api/gemeente/{naam}/info")
def get_gemeente_info(naam: str):
    """Basisinfo over een gemeente: id, slug, notubiz_id, platforms + afgeleide URLs."""
    conn = _meetings_conn()
    if conn is None:
        return {}
    row = conn.execute(
        "SELECT id, naam, slug, notubiz_id, platforms, website, wapen_url FROM gemeenten WHERE naam = ?", (naam,)
    ).fetchone()
    conn.close()
    if not row:
        return {}
    slug = row[2] or ''
    raadssite = f"https://{slug}.notubiz.nl" if slug else None
    # Gebruik opgeslagen website als die er is, anders afgeleid van slug
    website = row[5] or (f"https://www.{slug}.nl" if slug else None)
    griffie_email = f"griffie@{slug}.nl" if slug else None
    return {
        'id': row[0], 'naam': row[1], 'slug': slug,
        'notubiz_id': row[3], 'platforms': row[4],
        'raadssite': raadssite,
        'website': website,
        'griffie_email': griffie_email,
        'wapen_url': row[6],
    }


@app.get("/api/gemeente/{naam}/meetings")
def get_gemeente_meetings(naam: str, from_date: str = None, to_date: str = None, q: str = None, limit: int = 100):
    """Alle vergaderingen voor een gemeente, optioneel gefilterd op datum en zoekterm."""
    conn = _meetings_conn()
    if conn is None:
        return []
    conditions = ["g.naam = ?"]
    params = [naam]
    if from_date:
        conditions.append("m.datum >= ?"); params.append(from_date)
    if to_date:
        conditions.append("m.datum <= ?"); params.append(to_date)
    if q:
        conditions.append("(m.titel LIKE ? OR m.type LIKE ?)"); params += [f'%{q}%', f'%{q}%']
    where = " AND ".join(conditions)
    rows = conn.execute(f"""
        SELECT m.id, m.datum, m.tijd, m.titel, m.type, m.status, m.has_livestream, m.url
        FROM meetings m JOIN gemeenten g ON m.gemeente_id = g.id
        WHERE {where} ORDER BY m.datum DESC, m.tijd DESC LIMIT ?
    """, params + [limit]).fetchall()
    conn.close()
    return [{'id': r[0], 'datum': r[1], 'tijd': r[2], 'titel': r[3], 'type': r[4],
             'status': r[5], 'has_livestream': r[6], 'url': r[7]} for r in rows]


@app.get("/api/gemeente/{naam}/moties")
async def get_gemeente_moties(naam: str, limit: int = 50):
    """Haal recente moties en amendementen op via iBabs of NotUBiz."""
    import urllib.request as _req
    import urllib.parse as _parse
    import ssl as _ssl
    import json as _json
    import re as _re
    import html as _html

    conn = _meetings_conn()
    if conn is None:
        return []

    row = conn.execute(
        "SELECT slug, platforms, notubiz_id, go_url FROM gemeenten WHERE naam = ?", (naam,)
    ).fetchone()
    conn.close()

    if not row:
        return []

    slug = row[0] or ''
    platforms = _json.loads(row[1] or '[]')
    notubiz_id = row[2]
    go_url = row[3] or ''
    ctx = _ssl.create_default_context()

    def _status_from_text(text):
        tl = (text or '').lower()
        if 'aangenomen' in tl: return 'Aangenomen'
        if 'verworpen' in tl: return 'Verworpen'
        if 'ingetrokken' in tl: return 'Ingetrokken'
        if 'afgedaan' in tl or 'afdoening' in tl: return 'Afgedaan'
        return 'Ingediend'

    def _status_from_title(title):
        tl = (title or '').lower()
        if 'raadsbesluit' in tl or 'aangenomen' in tl or 'instemmen' in tl: return 'Aangenomen'
        if 'verworpen' in tl: return 'Verworpen'
        if 'ingetrokken' in tl: return 'Ingetrokken'
        if 'afdoen' in tl or 'afdoening' in tl or 'afgedaan' in tl: return 'Afgedaan'
        if 'bestuurlijke reactie' in tl or ' rib ' in tl: return 'Beantwoord'
        return 'Ingediend'

    def _fetch_text(url, method='GET', data=None, extra_headers=None):
        """Hulpfunctie: haal URL op als tekst. Geeft None bij fout."""
        headers = {
            'User-Agent': 'Mozilla/5.0 (compatible; RANST/1.0)',
            'Accept': 'text/html,*/*',
            'Accept-Language': 'nl-NL,nl;q=0.9',
        }
        if extra_headers:
            headers.update(extra_headers)
        try:
            req = _req.Request(url, data=data, headers=headers, method=method)
            resp = _req.urlopen(req, timeout=20, context=ctx)
            raw = b''
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                raw += chunk
                if len(raw) > 2_000_000:
                    break
            return raw.decode('utf-8', errors='replace')
        except Exception:
            return None

    def _fetch_json_local(url, method='GET', data=None, extra_headers=None):
        """Hulpfunctie: haal URL op als JSON. Geeft None bij fout."""
        hdrs = {'Accept': 'application/json, */*'}
        if extra_headers:
            hdrs.update(extra_headers)
        body = _fetch_text(url, method=method, data=data, extra_headers=hdrs)
        if not body:
            return None
        try:
            return _json.loads(body)
        except Exception:
            return None

    # ─── GO CMS (gemeenteoplossingen) ────────────────────────────────────
    # Document type IDs: 17=Moties, 10=Amendementen
    if 'gemeenteoplossingen' in platforms and slug:
        moties = []
        # Construct the GO CMS base URL from slug
        go_base = go_url.rstrip('/') if go_url else f'https://raad.{slug}.nl'
        try:
            url = (f'{go_base}/Documenten?documentsoorten=17%2C10'
                   f'&isAjax=true&pagina=1&sorteren=bijgewerkt-aflopend')
            data = _fetch_json_local(url)
            if data and 'documents' in data:
                for doc in data['documents'][:limit]:
                    desc = _html.unescape(doc.get('description', '') or '')
                    pub = (doc.get('publicationDate') or {}).get('date', '') or ''
                    datum = pub[:10] if pub else ''
                    dt = doc.get('documentType', {}) or {}
                    type_name = dt.get('name', 'Moties')
                    mot_type = 'Amendement' if 'amendement' in type_name.lower() else 'Motie'
                    # Also detect from description
                    dl = desc.lower()
                    if 'amendement' in dl:
                        mot_type = 'Amendement'
                    elif 'motie' in dl:
                        mot_type = 'Motie'
                    doc_url = doc.get('url', '') or ''
                    if doc_url and not doc_url.startswith('http'):
                        doc_url = go_base + doc_url
                    clean = _re.sub(r'\s+', ' ', desc).strip()
                    moties.append({
                        'nr': '',
                        'titel': clean[:120],
                        'type': mot_type,
                        'status': _status_from_title(desc),
                        'vergadering': '',
                        'datum': datum,
                        'url': doc_url,
                    })
        except Exception:
            pass

        if moties:
            seen = set()
            result = []
            for m in sorted(moties, key=lambda x: x['datum'], reverse=True):
                key = m['titel'][:60]
                if key not in seen:
                    seen.add(key)
                    result.append(m)
            return result[:limit]

    # ─── iBabs Reports (per-site moties/amendementen register) ───────────
    # Haal UUIDs op uit /Reports pagina, dan POST naar /Reports/GetReportData/{uuid}
    if 'ibabs' in platforms and slug:
        moties = []
        base = f'https://{slug}.bestuurlijkeinformatie.nl'
        try:
            reports_html = _fetch_text(f'{base}/Reports')
            if reports_html:
                # Zoek Reports/Details links met "motie" of "amendement" in tekst
                report_links = _re.findall(
                    r'/Reports/Details/([a-f0-9-]{36})["\'][^>]*>([^<]{2,60})',
                    reports_html
                )
                seen_uuids = set()
                report_uuids = []  # list of (uuid, label)
                for uuid, label in report_links:
                    lbl = label.strip().lower()
                    if uuid not in seen_uuids and ('motie' in lbl or 'amendement' in lbl):
                        seen_uuids.add(uuid)
                        report_uuids.append((uuid, label.strip()))

                for uuid, label in report_uuids:
                    is_amendement = 'amendement' in label.lower()
                    mot_type_default = 'Amendement' if is_amendement else 'Motie'
                    payload = _parse.urlencode({
                        'draw': '1',
                        'start': '0',
                        'length': str(limit),
                    }).encode()
                    data = _fetch_json_local(
                        f'{base}/Reports/GetReportData/{uuid}',
                        method='POST',
                        data=payload,
                        extra_headers={
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'X-Requested-With': 'XMLHttpRequest',
                        }
                    )
                    if not data or 'data' not in data:
                        continue
                    for item in data['data']:
                        title = _html.unescape(item.get('title', '') or '')
                        nummer = item.get('nummer', '') or ''
                        besluit = item.get('besluitvorming', '') or ''
                        tl = title.lower()
                        if 'amendement' in tl:
                            mot_type = 'Amendement'
                        elif 'motie' in tl:
                            mot_type = 'Motie'
                        else:
                            mot_type = mot_type_default
                        status = _status_from_text(besluit) if besluit else _status_from_title(title)
                        row_id = item.get('DT_RowId', '')
                        item_url = f'{base}/Reports/Item/{row_id}' if row_id else ''
                        moties.append({
                            'nr': nummer,
                            'titel': title[:120],
                            'type': mot_type,
                            'status': status,
                            'vergadering': '',
                            'datum': '',
                            'url': item_url,
                        })
        except Exception:
            pass

        if moties:
            seen = set()
            result = []
            for m in moties:
                key = m['titel'][:60]
                if key not in seen:
                    seen.add(key)
                    result.append(m)
            return result[:limit]

    # ─── Qualigraf/Parlaeus: scan raadsvergaderingen op moties-docs ───────
    if any(p in platforms for p in ('qualigraf', 'parlaeus')):
        moties = []
        q_base = go_url.rstrip('/') if go_url else ''
        if not q_base and slug:
            if 'qualigraf' in platforms:
                q_base = f'https://{slug}.qualigraf.nl'
            else:
                q_base = f'https://{slug}.parlaeus.nl'

        if q_base:
            try:
                from datetime import datetime as _dt, timedelta as _td
                now = _dt.now()
                start_s = (now - _td(days=365)).strftime('%d-%m-%Y')
                end_s   = now.strftime('%d-%m-%Y')

                # Resolve API base via /vji/general/app
                cfg = _fetch_json_local(f'{q_base}/vji/general/app')
                api_base = q_base
                if cfg and cfg.get('baseurl'):
                    api_base = cfg['baseurl'].rstrip('/')

                cal_url = (f'{api_base}/vji/public/calendar2/action=datalist'
                           f'/start={start_s}/end={end_s}')
                cal = _fetch_json_local(cal_url)
                events = cal.get('events', []) if cal else []

                # Filter op raadsvergaderingen
                raad_events = [
                    e for e in events
                    if any(w in e.get('name', '').lower()
                           for w in ['raadsvergadering', 'gemeenteraad', 'raad '])
                ][:8]  # max 8 vergaderingen scannen

                doc_re = _re.compile(
                    r'<a[^>]+href=["\']'
                    r'(/user/showdoc/action=view/id=([a-f0-9]+)/type=(?:pdf|doc)[^"\']*)'
                    r'["\'][^>]*>(.*?)</a>',
                    _re.S | _re.I
                )

                for ev in raad_events:
                    hexkey = ev.get('hexkey', '')
                    if not hexkey:
                        continue
                    raw_date = ev.get('startDate', '')
                    try:
                        datum = _dt.strptime(raw_date, '%Y/%m/%d').strftime('%Y-%m-%d')
                    except Exception:
                        datum = raw_date.replace('/', '-')

                    html_page = _fetch_text(f'{api_base}/user/agenda/action=view/ag={hexkey}')
                    if not html_page:
                        continue

                    seen_docs = set()
                    for m2 in doc_re.finditer(html_page):
                        path   = m2.group(1)
                        doc_id = m2.group(2)
                        inner  = m2.group(3)
                        if doc_id in seen_docs:
                            continue
                        # Filename bevat 'motie' of 'amendement'
                        path_lc = path.lower()
                        inner_lc = _re.sub(r'<[^>]+>', '', inner).lower()
                        if not any(w in path_lc or w in inner_lc
                                   for w in ['motie', 'amendement']):
                            continue
                        seen_docs.add(doc_id)
                        title = _re.sub(r'<[^>]+>', ' ', inner).strip()
                        title = _re.sub(r'\s+', ' ', title).strip()
                        # Strip bestandsextensie
                        title = _re.sub(r'\.(pdf|docx?)$', '', title, flags=_re.I).strip()
                        if not title:
                            fn_m = _re.search(r'/([^/]+)$', path)
                            title = _parse.unquote(fn_m.group(1)) if fn_m else doc_id
                            title = _re.sub(r'\.(pdf|docx?)$', '', title, flags=_re.I).strip()
                        mot_type = 'Amendement' if 'amendement' in path_lc or 'amendement' in inner_lc else 'Motie'
                        moties.append({
                            'nr': '',
                            'titel': title[:120],
                            'type': mot_type,
                            'status': _status_from_title(title),
                            'vergadering': ev.get('name', ''),
                            'datum': datum,
                            'url': api_base + path,
                        })
            except Exception:
                pass

        if moties:
            seen = set()
            result = []
            for m in sorted(moties, key=lambda x: x['datum'], reverse=True):
                key = m['titel'][:60]
                if key not in seen:
                    seen.add(key)
                    result.append(m)
            return result[:limit]

    # ─── iBabs ───────────────────────────────────────────────────────────
    if 'ibabs' in platforms and slug:
        moties = []
        base = f'https://{slug}.bestuurlijkeinformatie.nl'
        date_from = '2026-01-01'

        for query in ['motie', 'amendement']:
            try:
                payload = _parse.urlencode({
                    'Query': query,
                    'NumberOfItemsPerPage': '50',
                    'DateFrom': date_from,
                }).encode()
                r = _req.Request(
                    f'{base}/search/search', data=payload,
                    headers={
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'User-Agent': 'Mozilla/5.0',
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest',
                    }
                )
                resp = _req.urlopen(r, timeout=15, context=ctx)
                raw = b''
                while True:
                    chunk = resp.read(65536)
                    if not chunk: break
                    raw += chunk
                    if len(raw) > 1_000_000: break
                data = _json.loads(raw.decode('utf-8', errors='replace'))
                for item in data.get('Results', []):
                    # ObjectType=2 zijn agendapunten, niet de documenten zelf
                    if item.get('ObjectType') == 2:
                        continue
                    title = _html.unescape(item.get('Title', ''))
                    # Verifieer dat titel ook echt motie/amendement is
                    tl = title.lower()
                    if not any(w in tl for w in ['motie', 'amendement', 'initiatiefvoorstel']):
                        continue
                    # Strip datumprefix (bijv. "20260127 Motie...")
                    clean = _re.sub(r'^\d{8}\s+', '', title).strip()
                    # Detecteer type uit titel
                    mot_type = 'Amendement' if 'amendement' in clean.lower() else 'Motie'
                    # Datum
                    raw_date = (item.get('Date') or '')[:10]
                    datum = raw_date if raw_date and raw_date != '0001-01-01' else ''
                    # Status uit tekst + titel
                    text = item.get('Text', '') or ''
                    status = _status_from_text(text) if 'aangenomen' in text.lower() or 'verworpen' in text.lower() else _status_from_title(title)
                    moties.append({
                        'nr': '',
                        'titel': clean[:120],
                        'type': mot_type,
                        'status': status,
                        'vergadering': '',
                        'datum': datum,
                        'url': f'{base}/search/results',
                    })
            except Exception:
                pass

        # Dedupliceer + sorteer op datum desc
        seen = set()
        result = []
        for m in sorted(moties, key=lambda x: x['datum'], reverse=True):
            key = m['titel'][:60]
            if key not in seen:
                seen.add(key)
                result.append(m)
        if result:
            return result[:limit]

    # ─── NotUBiz: module_id=6 items ──────────────────────────────────────
    if notubiz_id:
        moties = []
        try:
            url = f'https://api.notubiz.nl/organisations/{notubiz_id}/modules/6/items?year=2026&limit={limit}'
            r = _req.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
            resp = _req.urlopen(r, timeout=20, context=ctx)
            raw = b''
            while True:
                chunk = resp.read(65536)
                if not chunk: break
                raw += chunk
                if len(raw) > 2_000_000: break
            data = _json.loads(raw.decode('utf-8', errors='replace'))
            items = data.get('item', [])
            if not isinstance(items, list):
                items = [items] if items else []
            for item in items:
                attrs = {a['label']: a.get('value') for a in item.get('attributes', {}).get('attribute', [])}
                title = attrs.get('Titel') or ''
                if not title:
                    continue
                datum_raw = (attrs.get('Datum motie') or attrs.get('Datum') or attrs.get('Aanmaakdatum') or '')[:10]
                datum = datum_raw if datum_raw and datum_raw > '2000-01-01' else ''
                status_raw = (attrs.get('Uitslag') or '').lower()
                if 'aangenomen' in status_raw: status = 'Aangenomen'
                elif 'verworpen' in status_raw: status = 'Verworpen'
                elif 'ingetrokken' in status_raw: status = 'Ingetrokken'
                else: status = _status_from_title(title)
                mot_type = attrs.get('Type') or 'Motie'
                doc = attrs.get('Hoofddocument') or {}
                url_val = (doc.get('url', '') if isinstance(doc, dict) else '') or ''
                vergadering = attrs.get('Ingediend in') or attrs.get('Gekoppeld evenement') or ''
                if isinstance(vergadering, list): vergadering = vergadering[0] if vergadering else ''
                moties.append({
                    'nr': str(attrs.get('RIS-nummer', '') or ''),
                    'titel': title[:120],
                    'type': mot_type,
                    'status': status,
                    'vergadering': str(vergadering)[:80],
                    'datum': datum,
                    'url': url_val,
                })
        except Exception:
            pass

        # Sorteer op datum desc
        moties.sort(key=lambda x: x['datum'], reverse=True)
        seen = set()
        result = []
        for m in moties:
            key = m['titel'][:60]
            if key not in seen:
                seen.add(key)
                result.append(m)
        return result[:limit]

    # ─── NotUBiz fallback: document-titel zoeken in events ───────────────
    conn2 = _meetings_conn()
    if conn2 is None:
        return []
    rows = conn2.execute("""
        SELECT m.id, m.external_id, m.datum, m.titel
        FROM meetings m JOIN gemeenten g ON m.gemeente_id = g.id
        WHERE g.naam = ? AND m.bron = 'notubiz_api'
              AND m.datum >= '2026-01-01' AND m.datum <= date('now')
              AND (m.type LIKE '%Raad%' OR m.type LIKE '%raad%' OR m.titel LIKE '%RAAD%')
        ORDER BY m.datum DESC LIMIT 10
    """, (naam,)).fetchall()
    conn2.close()

    if not rows:
        return []

    moties = []

    def _find_moties_docs(items, vergadering_datum, vergadering_titel):
        for item in items:
            if not isinstance(item, dict):
                continue
            docs = item.get('documents', {})
            dl = docs.get('document', []) if isinstance(docs, dict) else []
            if not isinstance(dl, list):
                dl = [dl] if dl else []
            for d in dl:
                if not isinstance(d, dict):
                    continue
                title = d.get('title', '')
                if not any(w in title.lower() for w in ['motie', 'amendement', 'initiatiefvoorstel']):
                    continue
                clean = _re.sub(r'\.(pdf|docx?)$', '', title, flags=_re.I).strip()
                clean = _re.sub(r'^\d{3,4}\.\d{2,4}\s*', '', clean).strip()
                clean = _re.sub(r'^Motie\s+', '', clean, flags=_re.I).strip()
                moties.append({
                    'nr': '',
                    'titel': clean[:120],
                    'type': 'Motie' if 'motie' in title.lower() else 'Amendement',
                    'status': _status_from_title(title),
                    'vergadering': vergadering_titel,
                    'datum': vergadering_datum,
                    'url': d.get('url', '') or d.get('download_url', ''),
                })
            sub = item.get('agendaitem', [])
            if not isinstance(sub, list):
                sub = [sub] if sub else []
            _find_moties_docs(sub, vergadering_datum, vergadering_titel)

    for row in rows:
        eid = row['external_id']
        try:
            r = _req.Request(
                f'https://api.notubiz.nl/events/{eid}',
                headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'},
            )
            resp = _req.urlopen(r, timeout=15, context=ctx)
            raw = b''
            while True:
                chunk = resp.read(65536)
                if not chunk: break
                raw += chunk
                if len(raw) > 2_000_000: break
            data = _json.loads(raw.decode('utf-8', errors='replace'))
            event = data.get('event', [{}])
            if isinstance(event, list):
                event = event[0] if event else {}
            agenda = event.get('agenda', {})
            items = agenda.get('agendaitem', [])
            if not isinstance(items, list):
                items = [items] if items else []
            _find_moties_docs(items, row['datum'], row['titel'])
        except Exception:
            pass

    seen = set()
    result = []
    for m in moties:
        key = m['titel'][:60]
        if key not in seen:
            seen.add(key)
            result.append(m)
    return result[:limit]


@app.get("/api/gemeente/{naam}/raadsleden")
def get_gemeente_raadsleden(naam: str):
    """Alle gecachte raadsleden voor een gemeente."""
    conn = _meetings_conn()
    if conn is None:
        return []
    try:
        rows = conn.execute("""
            SELECT r.naam, r.partij, r.functie
            FROM raadsleden r JOIN gemeenten g ON r.gemeente_id = g.id
            WHERE g.naam = ? ORDER BY r.functie, r.naam
        """, (naam,)).fetchall()
        conn.close()
        return [{'name': r[0], 'party': r[1], 'function': r[2]} for r in rows]
    except Exception:
        conn.close()
        return []


@app.get("/api/stats")
def stats():
    result = {}
    conn = _meetings_conn()
    if conn:
        today = datetime.now().strftime('%Y-%m-%d')
        result['total_meetings'] = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
        result['live'] = conn.execute("SELECT COUNT(*) FROM meetings WHERE status='live'").fetchone()[0]
        result['today'] = conn.execute("SELECT COUNT(*) FROM meetings WHERE datum=?", (today,)).fetchone()[0]
        result['gemeenten'] = conn.execute("SELECT COUNT(*) FROM gemeenten").fetchone()[0]
        conn.close()

    art_conn = db.get_db()
    result['articles'] = art_conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    art_conn.close()

    return result


@app.get("/api/coverage")
def coverage():
    """Tabel van alle gemeenten met raadsleden-count, moties-count en vergaderingen-count."""
    conn = _meetings_conn()
    if not conn:
        return []
    today = datetime.now().strftime('%Y-%m-%d')
    rows = conn.execute("""
        SELECT
            g.naam,
            g.slug,
            g.platforms,
            COALESCE(r.cnt, 0)   AS raadsleden,
            COALESCE(m.cnt, 0)   AS moties,
            COALESCE(t.cnt, 0)   AS vergaderingen,
            COALESCE(u.cnt, 0)   AS aankomend
        FROM gemeenten g
        LEFT JOIN (
            SELECT gemeente_id, COUNT(*) AS cnt FROM raadsleden GROUP BY gemeente_id
        ) r ON r.gemeente_id = g.id
        LEFT JOIN (
            SELECT m2.gemeente_id, COUNT(*) AS cnt
            FROM meeting_documents d
            JOIN meetings m2 ON m2.id = d.meeting_id
            WHERE LOWER(d.title) LIKE '%motie%'
               OR LOWER(d.title) LIKE '%amendement%'
            GROUP BY m2.gemeente_id
        ) m ON m.gemeente_id = g.id
        LEFT JOIN (
            SELECT gemeente_id, COUNT(*) AS cnt FROM meetings GROUP BY gemeente_id
        ) t ON t.gemeente_id = g.id
        LEFT JOIN (
            SELECT gemeente_id, COUNT(*) AS cnt
            FROM meetings
            WHERE datum >= ?
            GROUP BY gemeente_id
        ) u ON u.gemeente_id = g.id
        ORDER BY g.naam
    """, (today,)).fetchall()
    conn.close()
    return [
        {"naam": r[0], "slug": r[1], "platforms": r[2],
         "raadsleden": r[3], "moties": r[4], "vergaderingen": r[5], "aankomend": r[6]}
        for r in rows
    ]


# ── Bijlagen tekst extractie ────────────────────────────────────────────────

def _extract_pdf_text(data: bytes, max_chars: int = 1200) -> str:
    """Extraheer platte tekst uit PDF bytes via pdfminer.six."""
    try:
        from pdfminer.high_level import extract_text
        import io
        text = extract_text(io.BytesIO(data))
        # Ruimte normaliseren, lege regels comprimeren
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return ' '.join(lines)[:max_chars]
    except Exception:
        return ''


def _extract_document_text(url: str, max_chars: int = 1200) -> str:
    """Download een bijlage-URL en extraheer de tekst (PDF of HTML)."""
    import urllib.request as _req
    import ssl as _ssl
    ctx = _ssl.create_default_context()
    try:
        req = _req.Request(url, headers={'User-Agent': 'Mozilla/5.0 (RANST/1.0)'})
        resp = _req.urlopen(req, timeout=15, context=ctx)
        data = resp.read(524288)  # max 512KB
        content_type = resp.headers.get('Content-Type', '')
        if 'pdf' in content_type or url.lower().endswith('.pdf'):
            return _extract_pdf_text(data, max_chars)
        # HTML / tekst: strip tags
        text = data.decode('utf-8', errors='replace')
        import re as _re
        text = _re.sub(r'<[^>]+>', ' ', text)
        text = _re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]
    except Exception:
        return ''


async def _prefetch_document_texts(meeting_id: int, conn):
    """Extraheer tekst uit bijlagen voor één vergadering (max 4 docs, alleen als nog niet gedaan)."""
    loop = asyncio.get_event_loop()
    rows = conn.execute("""
        SELECT id, title, download_url FROM meeting_documents
        WHERE meeting_id=? AND (extracted_text IS NULL OR extracted_text='')
        ORDER BY agenda_item_nr LIMIT 4
    """, (meeting_id,)).fetchall()

    for row in rows:
        url = row['download_url']
        if not url:
            continue
        try:
            text = await loop.run_in_executor(None, _extract_document_text, url)
            if text:
                conn.execute(
                    "UPDATE meeting_documents SET extracted_text=? WHERE id=?",
                    (text, row['id'])
                )
                conn.commit()
        except Exception:
            pass
        await asyncio.sleep(0.3)


# ── Prefetch: raadsleden + bijlagen voor aankomende vergaderingen ────────────

def _ensure_raadsleden_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS raadsleden (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gemeente_id INTEGER NOT NULL,
            naam TEXT NOT NULL,
            partij TEXT,
            functie TEXT,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(gemeente_id, naam)
        )
    """)
    conn.commit()


def _store_meeting_prefetch(meeting_id: int, gemeente_id: int, details: dict, conn):
    """Sla bijlagen en raadsleden op uit platform-details."""
    # Bijlagen — met agenda-item context
    items = details.get('agenda', [])
    item_map = {it.get('number', ''): it.get('title', '') for it in items}

    docs = details.get('documents_full') or details.get('documents', [])
    for doc in docs:
        title = doc.get('title', '')
        url = doc.get('url', '') or doc.get('download_url', '')
        if not title or not url:
            continue
        agenda_nr = doc.get('agenda_nr', doc.get('number', ''))
        agenda_title = doc.get('agenda_title', item_map.get(str(agenda_nr), ''))
        try:
            conn.execute("""
                INSERT OR IGNORE INTO meeting_documents
                    (meeting_id, agenda_item_nr, agenda_item_title, doc_id, title, download_url)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (meeting_id, str(agenda_nr) if agenda_nr else None,
                  agenda_title or None,
                  url,  # url als doc_id (uniek per meeting)
                  title[:200], url))
        except Exception:
            pass

    # Raadsleden
    _ensure_raadsleden_table(conn)
    for sp in details.get('speakers', []):
        naam = sp.get('name', '').strip()
        if not naam:
            continue
        try:
            conn.execute("""
                INSERT OR REPLACE INTO raadsleden (gemeente_id, naam, partij, functie, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
            """, (gemeente_id, naam, sp.get('party', ''), sp.get('function', 'Raadslid')))
        except Exception:
            pass

    # Tijdstip: update alleen als nog NULL in DB (voor iBabs meetings)
    start_time = details.get('start_time')
    if start_time:
        try:
            conn.execute("""
                UPDATE meetings SET tijd=? WHERE id=? AND (tijd IS NULL OR tijd='')
            """, (start_time, meeting_id))
        except Exception:
            pass

    conn.commit()


def _normalize_meeting_title(titel: str) -> str:
    """Strip datums, nummers en stopwoorden voor vergelijkbare titelnormalisatie."""
    import re
    t = (titel or '').lower()
    # Verwijder datums (dd-mm-yyyy, dd/mm, maandnamen)
    t = re.sub(r'\b\d{1,2}[-/]\d{1,2}([-/]\d{2,4})?\b', '', t)
    t = re.sub(r'\b(januari|februari|maart|april|mei|juni|juli|augustus|september|oktober|november|december)\b', '', t)
    t = re.sub(r'\b\d{4}\b', '', t)
    # Verwijder nummers en stopwoorden
    t = re.sub(r'\b(vergadering|gemeente|raad|commissie|nr|no|bijeenkomst|\d+e?)\b', '', t)
    return re.sub(r'\s+', ' ', t).strip()


def _title_similarity(a: str, b: str) -> float:
    """Eenvoudige woordoverlap-score tussen twee genormaliseerde titels (0.0 – 1.0)."""
    wa = set(_normalize_meeting_title(a).split())
    wb = set(_normalize_meeting_title(b).split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / max(len(wa), len(wb))


def _fetch_prev_schets_list(gemeente_id: int, datum: str, meeting_type: str,
                            titel: str, agenda_items: list, doc_titles: list,
                            max_recent: int = 3, max_same: int = 1) -> list:
    """
    Haal eerdere vergaderschets op als context:
    - max_recent meest recente vergaderingen (alle typen)
    - max_same eerdere vergadering met dezelfde/vergelijkbare naam (thematische continuïteit)
    - Agenda-trefwoord overlap als extra relevantiesignaal
    """
    result = []
    seen_ids = set()

    conn = _meetings_conn()
    if not conn:
        return result

    # Recente vergaderingen (alle typen)
    recent_rows = conn.execute("""
        SELECT mt.id, mt.datum, mt.titel, mt.type
        FROM meetings mt
        WHERE mt.gemeente_id = ? AND mt.datum < ?
        ORDER BY mt.datum DESC
        LIMIT 10
    """, (gemeente_id, datum)).fetchall()

    # Zoek ook verder terug voor zelfde-naam vergaderingen (max 2 jaar)
    from datetime import datetime as _dt, timedelta as _td
    far_start = (_dt.strptime(datum, '%Y-%m-%d') - _td(days=730)).strftime('%Y-%m-%d')
    same_rows = conn.execute("""
        SELECT mt.id, mt.datum, mt.titel, mt.type
        FROM meetings mt
        WHERE mt.gemeente_id = ? AND mt.datum BETWEEN ? AND ?
        ORDER BY mt.datum DESC
        LIMIT 20
    """, (gemeente_id, far_start, datum)).fetchall()
    conn.close()

    # Agenda-trefwoorden van huidige vergadering
    cur_words = set()
    for it in agenda_items:
        cur_words.update((it.get('title') or '').lower().split())
    for t in doc_titles:
        cur_words.update(t.lower().split())
    cur_words -= {'de', 'het', 'een', 'van', 'en', 'in', 'op', 'te', 'voor', 'met', 'aan'}

    def _schets_for(row):
        r2 = db.get_db()
        c = r2.execute("SELECT schets FROM meeting_schets WHERE meeting_id=?", (row['id'],)).fetchone()
        r2.close()
        if c and c['schets']:
            return {'id': row['id'], 'datum': row['datum'], 'titel': row['titel'] or '',
                    'type': row['type'] or '', 'schets': c['schets']}
        return None

    # 1. Voeg recente toe
    for row in recent_rows:
        if len([r for r in result if r.get('_source') == 'recent']) >= max_recent:
            break
        s = _schets_for(row)
        if s:
            s['_source'] = 'recent'
            result.append(s)
            seen_ids.add(row['id'])

    # 2. Voeg zelfde-naam vergadering toe (zelfde type + hoge titel-overlap)
    same_added = 0
    for row in same_rows:
        if same_added >= max_same:
            break
        if row['id'] in seen_ids:
            continue
        sim = _title_similarity(titel, row['titel'] or '')
        if sim < 0.4:
            continue
        s = _schets_for(row)
        if s:
            s['_source'] = 'same_title'
            result.append(s)
            seen_ids.add(row['id'])
            same_added += 1

    # 3. Voeg agenda-overlap toe als die nog niet in resultaat zit
    if cur_words:
        for row in same_rows:
            if len(result) >= max_recent + max_same + 1:
                break
            if row['id'] in seen_ids:
                continue
            row_words = set((row['titel'] or '').lower().split())
            overlap = len(cur_words & row_words)
            if overlap >= 2:
                s = _schets_for(row)
                if s:
                    s['_source'] = 'agenda_overlap'
                    result.append(s)
                    seen_ids.add(row['id'])

    # Sorteer op datum aflopend (meest recent eerst)
    result.sort(key=lambda x: x['datum'], reverse=True)
    return result


def _schets_input_hash(agenda_items: list, doc_titles: list) -> str:
    """Maak een hash van de invoer zodat we kunnen detecteren of iets veranderd is."""
    import hashlib, json as _json
    agenda_key = sorted(it.get('title', '') for it in agenda_items if it.get('title'))
    docs_key   = sorted(doc_titles)
    payload = _json.dumps({'agenda': agenda_key, 'docs': docs_key}, ensure_ascii=False)
    return hashlib.sha1(payload.encode()).hexdigest()


async def _prefetch_upcoming_meetings():
    """Haal platform-details op voor alle vergaderingen komende 14 dagen."""
    conn = _meetings_conn()
    if conn is None:
        return

    today = datetime.now().strftime('%Y-%m-%d')
    end = (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')

    rows = conn.execute("""
        SELECT m.id, m.gemeente_id, m.bron
        FROM meetings m
        WHERE m.datum BETWEEN ? AND ?
        ORDER BY m.datum
        LIMIT 10
    """, (today, end)).fetchall()
    conn.close()

    _prefetch_status.update({'fase': '1_details', 'done': 0, 'total': len(rows), 'huidig': None, 'gestart': datetime.now().isoformat()})
    print(f"[prefetch] {len(rows)} vergaderingen ophalen ({today} → {end})")
    done = 0
    for row in rows:
        mid, gid, bron = row['id'], row['gemeente_id'], row['bron']
        try:
            details = await get_platform_details(mid)
            if details and (details.get('speakers') or details.get('documents')):
                conn2 = _meetings_conn()
                if conn2:
                    _store_meeting_prefetch(mid, gid, details, conn2)
                    conn2.close()
                done += 1
        except Exception as e:
            print(f"[prefetch] meeting {mid} fout: {e}")
        _prefetch_status['done'] = done
        await asyncio.sleep(0.5)  # belast servers niet te zwaar

    print(f"[prefetch] klaar — {done}/{len(rows)} meetings opgeslagen")

    # Prioriteitswindows
    priority_end  = (datetime.now() + timedelta(days=2)).strftime('%Y-%m-%d')  # vandaag+morgen
    background_end = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d') # dag 3-7
    # Voorbij dag 7: niet pre-genereren, alleen op aanvraag

    # Fase 2: bijlagentekst extraheren (alleen komende 7 dagen)
    print("[prefetch] fase 2: bijlagentekst extraheren (7 dagen)…")
    conn4 = _meetings_conn()
    if conn4:
        text_rows = conn4.execute("""
            SELECT DISTINCT md.meeting_id FROM meeting_documents md
            WHERE md.meeting_id IN (
                SELECT id FROM meetings WHERE datum BETWEEN ? AND ?
            ) AND (md.extracted_text IS NULL OR md.extracted_text='')
            ORDER BY (SELECT datum FROM meetings WHERE id=md.meeting_id)
            LIMIT 100
        """, (today, background_end)).fetchall()
        conn4.close()

        _prefetch_status.update({'fase': '2_pdf', 'done': 0, 'total': len(text_rows), 'huidig': None})
        for i, tr in enumerate(text_rows):
            conn5 = _meetings_conn()
            if conn5:
                await _prefetch_document_texts(tr['meeting_id'], conn5)
                conn5.close()
            _prefetch_status['done'] = i + 1
            await asyncio.sleep(0.5)
    print("[prefetch] fase 2 klaar")

    # Hulpfunctie: haal schets-input op voor één meeting
    async def _schets_input(m, docs_conn):
        doc_titles, doc_excerpts, prev_summary, prev_titel = [], [], '', ''
        doc_rows = docs_conn.execute(
            "SELECT title, extracted_text FROM meeting_documents WHERE meeting_id=? LIMIT 15",
            (m['id'],)
        ).fetchall()
        doc_titles = [r['title'] for r in doc_rows if r['title']]
        doc_excerpts = [
            {'title': r['title'], 'text': r['extracted_text']}
            for r in doc_rows if r['title'] and r['extracted_text']
        ]
        prev_row = docs_conn.execute(
            "SELECT id, titel FROM meetings WHERE gemeente_id=? AND datum < ? ORDER BY datum DESC LIMIT 1",
            (m['gemeente_id'], m['datum'])
        ).fetchone()
        if prev_row:
            prev_titel = prev_row['titel'] or ''
            s = db.get_meeting_summary(prev_row['id'])
            if s:
                prev_summary = s.get('summary', '')

        # Haal contextuele schets op: recent + zelfde naam + agenda-overlap
        prev_schets_list = _fetch_prev_schets_list(
            gemeente_id=m['gemeente_id'],
            datum=m['datum'],
            meeting_type=m['type'] or '',
            titel=m['titel'] or '',
            agenda_items=[],  # agenda nog niet bekend hier; doc_titles als proxy
            doc_titles=doc_titles,
        )

        return doc_titles, doc_excerpts, prev_summary, prev_titel, prev_schets_list

    async def _gen_schets(m, delay: float):
        _prefetch_status['huidig'] = f"{m['gemeente']} {m['datum']}"
        try:
            docs_conn = _meetings_conn()
            if not docs_conn:
                return
            doc_titles, doc_excerpts, prev_summary, prev_titel, prev_schets_list = await _schets_input(m, docs_conn)
            docs_conn.close()

            details = await get_platform_details(m['id'])
            agenda_items = details.get('agenda', []) if details else []

            # Bereken hash van huidige invoer
            new_hash = _schets_input_hash(agenda_items, doc_titles)

            # Sla over als invoer niet veranderd is
            ranst_check = db.get_db()
            cached = ranst_check.execute(
                "SELECT input_hash FROM meeting_schets WHERE meeting_id=?", (m['id'],)
            ).fetchone()
            ranst_check.close()
            if cached and cached['input_hash'] == new_hash:
                print(f"[prefetch] schets ongewijzigd: {m['gemeente']} {m['datum']}")
                await asyncio.sleep(delay)
                return

            schets = await analysis.generate_meeting_schets(
                meeting_type=m['type'] or '',
                gemeente=m['gemeente'],
                datum=m['datum'],
                titel=m['titel'] or '',
                agenda_items=agenda_items,
                doc_titles=doc_titles,
                doc_excerpts=doc_excerpts,
                prev_summary=prev_summary,
                prev_titel=prev_titel,
                prev_schets_list=prev_schets_list,
            )
            if not schets:
                schets = _build_fallback_schets(
                    meeting_type=m['type'] or '',
                    gemeente=m['gemeente'],
                    titel=m['titel'] or '',
                    agenda_items=agenda_items,
                    doc_titles=doc_titles,
                    prev_titel=prev_titel,
                )
            if schets:
                ranst2 = db.get_db()
                ranst2.execute(
                    "INSERT OR REPLACE INTO meeting_schets (meeting_id, schets, input_hash, created_at) "
                    "VALUES (?, ?, ?, datetime('now'))",
                    (m['id'], schets, new_hash)
                )
                ranst2.commit()
                ranst2.close()
                _prefetch_status['done'] += 1
                action = 'bijgewerkt' if cached else 'nieuw'
                print(f"[prefetch] schets {action}: {m['gemeente']} {m['datum']}")
        except Exception as e:
            print(f"[prefetch] schets fout {m['id']}: {e}")
        await asyncio.sleep(delay)

    # Laad gecachede hashes eenmalig — alle meetings checken (niet alleen zonder schets)
    ranst = db.get_db()
    cached_hashes = {r[0]: r[1] for r in ranst.execute(
        "SELECT meeting_id, input_hash FROM meeting_schets"
    ).fetchall()}
    ranst.close()

    conn6 = _meetings_conn()
    if not conn6:
        return
    schets_rows = conn6.execute("""
        SELECT m.id, m.datum, m.titel, m.type, g.naam AS gemeente, g.id AS gemeente_id
        FROM meetings m JOIN gemeenten g ON g.id=m.gemeente_id
        WHERE m.datum BETWEEN ? AND ?
        ORDER BY m.datum
    """, (today, background_end)).fetchall()
    conn6.close()

    # Alle meetings meegeven — hash-check in _gen_schets bepaalt of regeneratie nodig is
    priority   = [m for m in schets_rows if m['datum'] <= priority_end][:5]
    background = [m for m in schets_rows if m['datum'] > priority_end][:5]

    # Fase 3a: vandaag + morgen — snel (0.5s delay)
    print(f"[prefetch] fase 3a: {len(priority)} schets prioriteit (vandaag+morgen)…")
    _prefetch_status.update({'fase': '3a_priority', 'done': 0, 'total': len(priority)})
    for m in priority:
        await _gen_schets(m, delay=0.5)

    # Fase 3b: dag 3-7 — rustig op achtergrond (3s delay)
    print(f"[prefetch] fase 3b: {len(background)} schets achtergrond (dag 3-7)…")
    _prefetch_status.update({'fase': '3b_background', 'done': 0, 'total': len(background)})
    for m in background:
        pass  # RAM-check verwijderd — macOS beheert swap zelf
        await _gen_schets(m, delay=3.0)

    _prefetch_status.update({'fase': 'klaar', 'huidig': None})


@app.get("/api/prefetch/status")
async def prefetch_status():
    """Live voortgang van de prefetch-pipeline."""
    return _prefetch_status


@app.post("/api/sync/prefetch")
async def trigger_prefetch():
    """Handmatig starten van prefetch voor aankomende vergaderingen."""
    asyncio.create_task(_prefetch_upcoming_meetings())
    return {'status': 'gestart'}


@app.post("/api/sync/detect-streams")
async def trigger_detect_streams():
    """Herdetecteer livestream-vlaggen voor aankomende meetings — URL-scan + platform-heuristiek."""
    asyncio.create_task(_detect_livestream_flags())
    asyncio.create_task(_mark_streams_by_platform())
    return {'status': 'gestart'}


async def _mark_streams_by_platform():
    """Zet has_livestream=1 voor meetings waarvan de gemeente een streaming platform heeft
    en de meeting-titel past bij een vergadering die live uitgezonden wordt."""
    import json as _json
    try:
        conn = _meetings_conn()
        if not conn:
            return
        today = datetime.now().strftime('%Y-%m-%d')
        end   = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')

        STREAM_KEYWORDS = [
            'raadsvergadering', 'gemeenteraad', 'commissievergadering',
            'duidingsdebat', 'duidingsbijeenkomst', 'politieke avond',
            'besluitvormende', 'beeldvormende', 'oordeelvormende',
            'raadsavond', 'raadsdebat', 'raadsinformatieavond',
            'politieke markt', 'raadsbijeenkomst', 'raadsinformatief', 'duidingsgesprek',
        ]
        SKIP_KEYWORDS = [
            'fractievergadering', 'centraal stembureau', 'inauguratie', 'geloofsbrieven',
            'formulierenmarkt', 'inwerkprogramma', 'presidium', 'raadsledenspreekuur',
            'agendacommissie', 'afscheidsdiner', 'netwerk', 'publieksacademie',
            'definitieve uitslag', 'officiële uitslag', 'formele csb', 'welkomstdag raads',
            'regeldag', 'benoemde raadsleden', 'bezwaarschrift', 'lunch',
        ]
        STREAM_PLATFORMS = {'notubiz', 'cwc', 'youtube', 'raadlive', 'iad', 'parlaeus'}

        gem_rows = conn.execute(
            "SELECT naam, platforms FROM gemeenten WHERE platforms IS NOT NULL AND platforms != ''"
        ).fetchall()
        stream_gemeenten = set()
        for gr in gem_rows:
            try:
                platforms = _json.loads(gr['platforms'])
                if any(p in STREAM_PLATFORMS for p in platforms):
                    stream_gemeenten.add(gr['naam'])
            except Exception:
                pass

        rows = conn.execute("""
            SELECT m.id, m.titel, g.naam AS gemeente
            FROM meetings m JOIN gemeenten g ON g.id = m.gemeente_id
            WHERE m.datum BETWEEN ? AND ?
              AND m.has_livestream = 0
              AND m.status IN ('scheduled', 'gemist')
        """, (today, end)).fetchall()

        updated = 0
        for r in rows:
            title_lower = (r['titel'] or '').lower()
            if any(kw in title_lower for kw in SKIP_KEYWORDS):
                continue
            if r['gemeente'] in stream_gemeenten and any(kw in title_lower for kw in STREAM_KEYWORDS):
                conn.execute("UPDATE meetings SET has_livestream=1 WHERE id=?", (r['id'],))
                updated += 1

        conn.commit()
        conn.close()
        print(f'[mark_streams_by_platform] {updated} meetings bijgewerkt')
    except Exception as e:
        print(f'[mark_streams_by_platform] fout (niet fataal): {e}')


async def _delayed_start(coro_fn, delay_secs: int):
    """Start een coroutine pas na delay_secs seconden — voorkomt DB-lock opstapeling bij startup."""
    await asyncio.sleep(delay_secs)
    try:
        await coro_fn()
    except Exception as e:
        print(f'[delayed_start:{coro_fn.__name__}] {e}')


@app.on_event("startup")
async def startup_prefetch():
    # Watchers met kleine vertraging starten zodat de server eerst requests accepteert
    asyncio.create_task(_delayed_start(_livestream_watcher, 5))
    asyncio.create_task(_delayed_start(_daily_meeting_watcher, 5))
    asyncio.create_task(_delayed_start(_monthly_raadsleden_watcher, 5))
    # Zware eenmalige DB-taken gespreid starten zodat de server requests kan afhandelen
    asyncio.create_task(_delayed_start(_go_sync_watcher, 10))
    asyncio.create_task(_delayed_start(_prefetch_upcoming_meetings, 20))
    asyncio.create_task(_delayed_start(_detect_livestream_flags, 35))
    asyncio.create_task(_delayed_start(_mark_streams_by_platform, 50))


@app.post("/api/meetings/stream-check")
async def trigger_stream_check():
    """Trigger handmatige live-stream check (Electron kan dit aanroepen)."""
    asyncio.create_task(_do_stream_check())
    return {"ok": True}


@app.post("/api/sync/go")
async def trigger_go_sync():
    """Herlaad vergaderingen van alle GO-portalen (GemeenteOplossingen)."""
    loop = asyncio.get_event_loop()
    asyncio.create_task(loop.run_in_executor(None, _sync_go_meetings_sync))
    return {"ok": True, "status": "gestart"}


# ── Intelligence Hub ─────────────────────────────────────────────────────────

# Partijen
_HUB_PARTIES = [
    ('vvd', 'VVD'), ('d66', 'D66'), ('cda', 'CDA'),
    ('pvda', 'PvdA'), ('groenlinks', 'GroenLinks'), ('sp', 'SP'),
    ('pvv', 'PVV'), ('christenunie', 'ChristenUnie'),
    ('sgp', 'SGP'), ('volt', 'Volt'), ('fvd', 'FvD'),
    ('pvdd', 'PvdD'), ('nsc', 'NSC'), ('bbb', 'BBB'),
    ('50plus', '50Plus'), ('denk', 'DENK'), ('ja21', 'JA21'),
    ('lokaalbelang', 'Lokaal Belang'), ('gemeentebelangen', 'Gemeentebelangen'),
]

# Stopwoorden voor keyword-extractie
_HUB_STOPWORDS = {
    'zijn', 'over', 'deze', 'heeft', 'worden', 'wordt', 'voor', 'maar', 'meer',
    'niet', 'geen', 'naar', 'door', 'alle', 'veel', 'want', 'toen', 'ook',
    'haar', 'mijn', 'jouw', 'onze', 'jullie', 'kunnen', 'zullen', 'hebben',
    'waren', 'werd', 'welke', 'welk', 'waar', 'wanneer', 'waarmee', 'hoeveel',
    'zodat', 'waarbij', 'zoals', 'hierbij', 'hierin', 'hiervan', 'daarin',
    'daarmee', 'daarvoor', 'hiervoor', 'ingediend', 'ingesteld', 'gemeente',
    'gemeenten', 'raad', 'raadsleden', 'vergadering', 'agendapunt', 'bijlage',
}


# Provincie-aliassen: kortere/alternatieve namen → canonieke naam in PROVINCIE_GEMEENTEN
_PROV_ALIASES: dict = {
    'brabant':        'Noord-Brabant',
    'noord brabant':  'Noord-Brabant',
    'n brabant':      'Noord-Brabant',
    'noord holland':  'Noord-Holland',
    'n holland':      'Noord-Holland',
    'nh':             'Noord-Holland',
    'zuid holland':   'Zuid-Holland',
    'z holland':      'Zuid-Holland',
    'zh':             'Zuid-Holland',
    'fryslan':        'Friesland',
    'fryslân':        'Friesland',
}

# Regio-namen → lijst van gemeenten binnen die regio
_REGIO_GEMEENTEN: dict = {
    'twente': [
        'Enschede', 'Almelo', 'Hengelo', 'Oldenzaal', 'Haaksbergen',
        'Borne', 'Losser', 'Tubbergen', 'Dinkelland', 'Wierden',
        'Hellendoorn', 'Rijssen-Holten', 'Twenterand', 'Hof van Twente',
    ],
    'achterhoek': [
        'Doetinchem', 'Winterswijk', 'Aalten', 'Berkelland', 'Bronckhorst',
        'Lochem', 'Oost Gelre', 'Oude IJsselstreek',
    ],
    'veluwe': [
        'Harderwijk', 'Nunspeet', 'Elburg', 'Ermelo', 'Putten',
        'Nijkerk', 'Barneveld', 'Hattem', 'Oldebroek', 'Epe', 'Heerde',
        'Voorst', 'Brummen', 'Apeldoorn',
    ],
    'rivierenland': [
        'Tiel', 'Culemborg', 'Buren', 'Neder-Betuwe', 'West Betuwe',
        'Zaltbommel', 'Maasdriel', 'West Maas en Waal',
    ],
    'west-brabant': [
        'Breda', 'Bergen op Zoom', 'Roosendaal', 'Waalwijk', 'Oosterhout',
        'Etten-Leur', 'Halderberge', 'Rucphen', 'Moerdijk', 'Steenbergen',
        'Woensdrecht', 'Geertruidenberg', 'Drimmelen', 'Altena',
        'Gilze en Rijen', 'Goirle', 'Dongen', 'Zundert', 'Baarle-Nassau',
    ],
    'food valley': ['Ede', 'Wageningen', 'Barneveld', 'Nijkerk', 'Scherpenzeel', 'Renswoude', 'Rhenen', 'Woudenberg'],
    'gooi': ['Hilversum', 'Huizen', 'Blaricum', 'Gooise Meren', 'Laren', 'Wijdemeren', 'Eemnes'],
    'west-friesland': ['Hoorn', 'Enkhuizen', 'Medemblik', 'Hollands Kroon', 'Dijk en Waard', 'Stede Broec', 'Koggenland', 'Opmeer', 'Drechterland'],
    'kennemerland': ['Haarlem', 'Velsen', 'Beverwijk', 'Heemskerk', 'Uitgeest', 'Bloemendaal', 'Heemstede', 'Zandvoort', 'Castricum'],
    'alblasserwaard': ['Molenlanden', 'Gorinchem', 'Hardinxveld-Giessendam', 'Alblasserdam', 'Sliedrecht', 'Hendrik-Ido-Ambacht', 'Zwijndrecht', 'Papendrecht'],
    'drechtsteden': ['Dordrecht', 'Zwijndrecht', 'Sliedrecht', 'Papendrecht', 'Alblasserdam', 'Hendrik-Ido-Ambacht'],
}


# ── Synoniemtabel voor keyword-uitbreiding ───────────────────────────────────
# Elke ingang mappt op een lijst synoniemen / nauw verwante termen.
# Bidirectioneel: zowel "azc" → [...] als "asielzoekerscentrum" → [...].
_HUB_SYNONYMS: dict = {
    # Asiel / migratie / opvang
    'azc':                    ['asielzoekerscentrum', 'asielopvang', 'opvanglocatie', 'asielzoekers'],
    'asielzoekerscentrum':    ['azc', 'asielopvang', 'opvanglocatie', 'asielzoekers'],
    'asielopvang':            ['azc', 'asielzoekerscentrum', 'noodopvang', 'opvanglocatie'],
    'asielzoekers':           ['azc', 'asielzoekerscentrum', 'vluchtelingen', 'asielopvang'],
    'vluchtelingen':          ['asielzoekers', 'statushouders', 'vergunninghouders', 'opvang'],
    'statushouders':          ['vergunninghouders', 'vluchtelingen', 'asielzoekers'],
    'vergunninghouders':      ['statushouders', 'vluchtelingen'],
    'noodopvang':             ['opvang', 'azc', 'crisisopvang', 'noodlocatie'],
    'opvanglocatie':          ['azc', 'asielopvang', 'noodopvang'],
    'immigratie':             ['migratie', 'asielzoekers', 'vluchtelingen'],
    'migratie':               ['immigratie', 'asielzoekers'],

    # Wonen / ruimtelijk
    'woningbouw':             ['woningen', 'nieuwbouw', 'bouwplan', 'woningplan'],
    'nieuwbouw':              ['woningbouw', 'bouwplan', 'woningen', 'woonwijk'],
    'bestemmingsplan':        ['omgevingsplan', 'bouwbestemming', 'ruimtelijke ordening'],
    'omgevingsplan':          ['bestemmingsplan', 'omgevingsvisie', 'ruimtelijk plan'],
    'omgevingsvisie':         ['omgevingsplan', 'structuurvisie', 'ruimtelijke visie'],
    'woonvisie':              ['woningbouwprogramma', 'woonbeleid', 'woningbouw'],
    'corporatie':             ['woningcorporatie', 'sociale huur', 'huurwoningen'],
    'woningcorporatie':       ['corporatie', 'sociale huur', 'huurwoningen'],
    'sociale huur':           ['huurwoningen', 'corporatie', 'woningcorporatie'],

    # Energie / klimaat
    'windmolens':             ['windturbines', 'windpark', 'windenergie'],
    'windturbines':           ['windmolens', 'windpark', 'windenergie'],
    'windpark':               ['windmolens', 'windturbines', 'windenergie'],
    'zonnepanelen':           ['zonnepark', 'zonne-energie', 'pvinstallatie'],
    'zonnepark':              ['zonnepanelen', 'zonne-energie', 'solarveld'],
    'aardgasvrij':            ['gasloos', 'warmtenet', 'energietransitie', 'warmtepomp'],
    'energietransitie':       ['aardgasvrij', 'duurzaamheid', 'klimaatakkoord', 'verduurzaming'],
    'duurzaamheid':           ['energietransitie', 'klimaat', 'verduurzaming'],
    'warmtenet':              ['warmtepomp', 'aardgasvrij', 'stadsverwarming'],
    'warmtepomp':             ['warmtenet', 'aardgasvrij'],
    'klimaatakkoord':         ['energietransitie', 'duurzaamheid', 'klimaat'],

    # Veiligheid / criminaliteit
    'ondermijning':           ['georganiseerde criminaliteit', 'drugscriminaliteit', 'ondermijnende criminaliteit'],
    'drugscriminaliteit':     ['ondermijning', 'drugs', 'drugshandel'],
    'handhaving':             ['toezicht', 'boa', 'politie', 'controleren'],
    'overlast':               ['hinder', 'buurtoverlast', 'leefbaarheid'],

    # Financiën / beleid
    'bezuinigingen':          ['bezuiniging', 'kostenbesparing', 'taakstelling', 'ombuigingen'],
    'begroting':              ['financiën', 'budget', 'jaarrekening', 'gemeentebegroting'],
    'jaarrekening':           ['begroting', 'financieel jaarverslag', 'resultaat'],
    'ozb':                    ['onroerendezaakbelasting', 'gemeentebelasting', 'belasting'],
    'onroerendezaakbelasting': ['ozb', 'gemeentebelasting'],
    'subsidie':               ['subsidies', 'financiering', 'bijdrage', 'cofinanciering'],

    # Zorg / sociaal domein
    'jeugdzorg':              ['jeugdhulp', 'cjg', 'jeugdhulpverlening', 'jeugd'],
    'jeugdhulp':              ['jeugdzorg', 'cjg', 'jeugd'],
    'wmo':                    ['thuiszorg', 'hulp thuis', 'ondersteuning', 'maatschappelijke ondersteuning'],
    'thuiszorg':              ['wmo', 'hulp thuis', 'mantelzorg'],
    'bijstand':               ['uitkering', 'participatiewet', 'sociale dienst', 'levensonderhoud'],
    'participatiewet':        ['bijstand', 'uitkering', 're-integratie'],
    'armoede':                ['schulden', 'financiële problemen', 'bestaanszekerheid', 'minima'],
    'schulden':               ['armoede', 'schuldhulpverlening', 'schuldensanering'],

    # Openbare ruimte / infra
    'parkeren':               ['parkeerbeleid', 'parkeernorm', 'parkeerplaatsen', 'parkeergarage'],
    'fietspad':               ['fietspaden', 'fietsinfrastructuur', 'fietsroute', 'fietsstrook'],
    'riolering':              ['riool', 'wateroverlast', 'afvalwater', 'rioleringsstelsel'],
    'wegenonderhoud':         ['wegbeheer', 'asfalt', 'bestrating', 'wegen'],
    'verkeer':                ['verkeersveiligheid', 'mobiliteit', 'verkeersoverlast'],
    'mobiliteit':             ['verkeer', 'openbaar vervoer', 'bereikbaarheid'],

    # Onderwijs / sport / cultuur
    'schoolgebouw':           ['school', 'onderwijshuisvesting', 'scholen', 'brede school'],
    'sportaccommodatie':      ['sporthal', 'zwembad', 'sportveld', 'sportfaciliteit'],
    'zwembad':                ['sportaccommodatie', 'recreatievoorziening'],

    # Bestuurlijk
    'coalitieakkoord':        ['collegeprogramma', 'bestuursakkoord', 'coalitieprogram'],
    'motie':                  ['amendement', 'motie van treurnis', 'motie van wantrouwen'],
    'amendement':             ['motie', 'wijzigingsvoorstel'],
    'raadsvoorstel':          ['voorstel', 'besluitvorming', 'agendapunt'],
    'interpellatie':          ['spoeddebat', 'vragenuur', 'mondelinge vraag'],
    'omgevingswet':           ['omgevingsplan', 'ruimtelijke ordening', 'vergunning', 'omgevingsvisie'],
    'vergunning':             ['omgevingsvergunning', 'bouwvergunning', 'ontheffing'],

    # Economie / arbeidsmarkt
    'arbeidsmarkt':           ['werkgelegenheid', 'banen', 'werkeloosheid', 'vacatures'],
    'werkgelegenheid':        ['arbeidsmarkt', 'banen', 'economie'],
    'bedrijventerrein':       ['industrieterrein', 'bedrijfslocatie', 'vestigingsklimaat'],
    'toerisme':               ['recreatie', 'toeristen', 'bezoekersaantallen'],
}


def _hub_expand_keywords(keywords: list) -> tuple:
    """Geef (primary_kws, synonym_kws) terug.
    primary_kws = originele trefwoorden (hoge score bij match).
    synonym_kws = uitgebreide synoniemen (lagere score bij match).
    """
    synonym_set = []
    for kw in keywords:
        for syn in _HUB_SYNONYMS.get(kw, []):
            # Splits samengestelde synoniemen op spaties (bijv. "hulp thuis" → twee termen)
            for part in syn.split():
                if len(part) >= 3 and part not in keywords and part not in synonym_set:
                    synonym_set.append(part)
    return keywords, synonym_set


def _hub_parse_filters(query: str) -> dict:
    """Extraheer locatie, partij, datum en topic-filters uit vrije tekst.
    Volgorde: eerst locatie (provincie/regio > gemeente), dan partij, dan datum.
    """
    import re
    from datetime import date, timedelta
    filters: dict = {}
    q_low = query.lower()

    # ── 1. Datumfilters ─────────────────────────────────────────────────────
    if 'afgelopen week' in q_low or 'deze week' in q_low:
        filters['date_from'] = (date.today() - timedelta(days=7)).isoformat()
    elif 'afgelopen 2 weken' in q_low or 'twee weken' in q_low:
        filters['date_from'] = (date.today() - timedelta(days=14)).isoformat()
    elif 'afgelopen maand' in q_low or 'deze maand' in q_low:
        filters['date_from'] = (date.today() - timedelta(days=30)).isoformat()
    elif 'dit jaar' in q_low or 'afgelopen jaar' in q_low:
        filters['date_from'] = date.today().replace(month=1, day=1).isoformat()
    maanden = {
        'januari': 1, 'februari': 2, 'maart': 3, 'april': 4, 'mei': 5, 'juni': 6,
        'juli': 7, 'augustus': 8, 'september': 9, 'oktober': 10, 'november': 11, 'december': 12,
    }
    for naam, nr in maanden.items():
        if naam in q_low:
            jaar = date.today().year
            filters['date_from'] = date(jaar, nr, 1).isoformat()
            last_day = 28 if nr == 2 else (30 if nr in [4, 6, 9, 11] else 31)
            filters['date_to'] = date(jaar, nr, last_day).isoformat()
            break

    # ── 2. Provincie detectie → gemeente_list ───────────────────────────────
    # Eerst: aliassen (kortere namen zoals "brabant", "holland")
    detected_location_words = set()
    for alias, canonical in _PROV_ALIASES.items():
        if alias in q_low and canonical in config.PROVINCIE_GEMEENTEN:
            filters['gemeente_list'] = config.PROVINCIE_GEMEENTEN[canonical]
            filters['province'] = canonical
            detected_location_words.update(alias.split())
            break

    # Dan: exacte provincie-naam
    if 'gemeente_list' not in filters:
        for prov_name, gem_list in config.PROVINCIE_GEMEENTEN.items():
            if prov_name.lower() in q_low:
                filters['gemeente_list'] = gem_list
                filters['province'] = prov_name
                detected_location_words.update(prov_name.lower().split('-'))
                detected_location_words.update(prov_name.lower().split())
                break

    # Dan: regio-namen (Twente, Achterhoek, etc.)
    if 'gemeente_list' not in filters:
        for regio, gem_list in _REGIO_GEMEENTEN.items():
            if regio in q_low:
                filters['gemeente_list'] = gem_list
                filters['province'] = regio.title()
                detected_location_words.update(regio.split())
                break

    # ── 3. Gemeente detectie (alleen als geen provincie/regio) ────────────────
    if 'gemeente_list' not in filters:
        # Haal alle bekende gemeente-namen op uit DB
        try:
            import sqlite3 as _sq3
            _gc = _sq3.connect(config.MEETINGS_DB)
            _gem_rows = _gc.execute("SELECT DISTINCT naam FROM gemeenten ORDER BY LENGTH(naam) DESC").fetchall()
            _gc.close()
            for (gname,) in _gem_rows:
                if gname and gname.lower() in q_low:
                    filters['gemeente'] = gname
                    detected_location_words.update(gname.lower().split())
                    break
        except Exception:
            pass

    # Sla gedetecteerde locatiewoorden op zodat hub_search ze kan uitsluiten van keywords
    if detected_location_words:
        filters['location_words'] = detected_location_words

    # ── 4. Partij detectie ──────────────────────────────────────────────────
    detected_parties = []
    for alias, canonical in _HUB_PARTIES:
        # Gebruik negatieve lookahead/lookbehind voor woordgrenzen (geen re \b)
        pat = '(?<![a-zA-Z0-9])' + re.escape(alias) + '(?![a-zA-Z0-9])'
        if re.search(pat, q_low):
            if canonical not in detected_parties:
                detected_parties.append(canonical)
    if detected_parties:
        filters['parties'] = detected_parties

    # ── 5. Query-type bepalen ───────────────────────────────────────────────
    if detected_parties:
        filters['query_type'] = 'party_research'
    elif 'gemeente_list' in filters or filters.get('province'):
        filters['query_type'] = 'gebied_research'
    elif 'gemeente' in filters:
        filters['query_type'] = 'gemeente_research'
    else:
        filters['query_type'] = 'general'

    return filters


def _hub_retrieve(query: str, gemeente: str = None, date_from: str = None,
                  date_to: str = None, top_k: int = 12,
                  gemeente_list: list = None, parties: list = None) -> list:
    """Haal relevante chunks op uit transcripten en documenten.
    Filtert eerst op locatie (gemeente of gemeente_list), dan op keywords + synoniemen.
    Exacte keyword-matches scoren zwaarder dan synoniematches.
    """
    import sqlite3, re

    keywords = [
        w for w in re.split(r'\W+', query.lower())
        if len(w) >= 3 and w not in _HUB_STOPWORDS
    ]
    # Synoniemuitbreiding
    primary_kws, synonym_kws = _hub_expand_keywords(keywords)

    # Partijnamen als zoekterm
    party_terms = []
    if parties:
        for p in parties:
            party_terms.append(p.lower())
            for alias, canonical in _HUB_PARTIES:
                if canonical == p:
                    party_terms.append(alias)

    # SQL-termen: primaire + synoniemen + partij (alles voor WHERE-clausule)
    all_search_terms = primary_kws + [s for s in synonym_kws if s not in primary_kws]
    all_search_terms += [pt for pt in party_terms if pt not in all_search_terms]
    if not all_search_terms:
        return []

    results = []

    def _loc_filter(q_str: str, p_list: list):
        """Voeg locatiefilter toe aan SQL-query en params."""
        if gemeente:
            q_str += ' AND LOWER(g.naam) = ?'
            p_list.append(gemeente.lower())
        elif gemeente_list:
            placeholders = ','.join('?' * len(gemeente_list))
            q_str += f' AND g.naam IN ({placeholders})'
            p_list.extend(gemeente_list)
        if date_from:
            q_str += ' AND m.datum >= ?'
            p_list.append(date_from)
        if date_to:
            q_str += ' AND m.datum <= ?'
            p_list.append(date_to)
        return q_str, p_list

    try:
        conn_r = sqlite3.connect(config.RANST_DB)
        conn_r.execute(f"ATTACH DATABASE '{config.MEETINGS_DB}' AS mdb")

        # ── Transcripten ──────────────────────────────────────────────────
        kw_clauses = ' OR '.join([f"LOWER(tc.text) LIKE ?" for _ in all_search_terms])
        params = [f'%{w}%' for w in all_search_terms]

        q_tr = f"""
            SELECT tc.id, tc.meeting_id, tc.start_time, tc.end_time,
                   tc.speaker, tc.text, m.datum, m.titel, g.naam
            FROM transcript_chunks tc
            JOIN mdb.meetings m ON m.id = tc.meeting_id
            JOIN mdb.gemeenten g ON g.id = m.gemeente_id
            WHERE ({kw_clauses})
        """
        q_tr, params = _loc_filter(q_tr, params)
        q_tr += ' ORDER BY m.datum DESC LIMIT ?'
        params.append(top_k * 4)

        for row in conn_r.execute(q_tr, params).fetchall():
            text_low = (row[5] or '').lower()
            speaker_low = (row[4] or '').lower()

            # Primaire keywords: score 2 per treffer
            score = sum(2 for w in primary_kws if w in text_low)
            # Synoniemen: score 1 per treffer
            score += sum(1 for w in synonym_kws if w in text_low)
            # Party boost
            for pt in party_terms:
                if pt in speaker_low:
                    score += 4
                if pt in text_low:
                    score += 1

            if score == 0:
                continue

            results.append({
                'type': 'transcript',
                'id': f'T{row[0]}',
                'meeting_id': row[1],
                'start_time': row[2],
                'end_time': row[3],
                'speaker': row[4] or 'Onbekend',
                'text': row[5] or '',
                'datum': row[6],
                'vergadering': row[7],
                'gemeente': row[8],
                'score': score,
            })

        conn_r.close()
    except Exception:
        pass

    try:
        conn_m = sqlite3.connect(config.MEETINGS_DB)

        # ── Documenten ────────────────────────────────────────────────────
        doc_clauses = ' OR '.join([
            f"(LOWER(d.title) LIKE ? OR LOWER(d.extracted_text) LIKE ?)"
            for _ in all_search_terms
        ])
        dparams = []
        for w in all_search_terms:
            dparams += [f'%{w}%', f'%{w}%']

        q_doc = f"""
            SELECT d.id, d.meeting_id, d.title, d.download_url, d.extracted_text,
                   m.datum, m.titel, g.naam
            FROM meeting_documents d
            JOIN meetings m ON m.id = d.meeting_id
            JOIN gemeenten g ON g.id = m.gemeente_id
            WHERE d.extracted_text IS NOT NULL AND d.extracted_text != ''
              AND ({doc_clauses})
        """
        q_doc, dparams = _loc_filter(q_doc, dparams)
        q_doc += ' ORDER BY m.datum DESC LIMIT ?'
        dparams.append(top_k * 4)

        for row in conn_m.execute(q_doc, dparams).fetchall():
            text = row[4] or ''
            text_low = text.lower()
            title_low = (row[2] or '').lower()

            # Primaire keywords in tekst én titel
            score  = sum(2 for w in primary_kws if w in text_low)
            score += sum(3 for w in primary_kws if w in title_low)   # titelmatch zwaarder
            # Synoniemen
            score += sum(1 for w in synonym_kws if w in text_low)
            score += sum(2 for w in synonym_kws if w in title_low)
            # Party boost
            for pt in party_terms:
                if pt in title_low:
                    score += 3
                if pt in text_low:
                    score += 1
            if score == 0:
                continue

            results.append({
                'type': 'document',
                'id': f'D{row[0]}',
                'meeting_id': row[1],
                'title': row[2] or 'Document',
                'url': row[3] or '',
                'text': text,
                'datum': row[5],
                'vergadering': row[6],
                'gemeente': row[7],
                'score': score,
            })

        conn_m.close()
    except Exception:
        pass

    # Sorteer op score desc, dan datum desc
    results.sort(key=lambda x: (x['score'], x.get('datum', '') or ''), reverse=True)
    return results[:top_k]


def _best_snippet(text: str, keywords: list, window: int = 600) -> str:
    """Geef het deel van de tekst met de hoogste keyword-dichtheid."""
    if not keywords or not text:
        return text[:window]
    low = text.lower()
    best_pos, best_score = 0, 0
    for pos in range(0, max(1, len(text) - window), 80):
        chunk = low[pos:pos + window]
        score = sum(chunk.count(w) for w in keywords)
        if score > best_score:
            best_score = score
            best_pos = pos
    return text[best_pos:best_pos + window].strip()


def _hub_build_context(chunks: list, keywords: list = None, parties: list = None) -> str:
    """Bouw genummerde context-string voor LLM met voldoende context per bron."""
    parts = []
    party_terms = []
    if parties:
        for p in parties:
            party_terms.append(p.lower())
            for alias, canonical in _HUB_PARTIES:
                if canonical == p:
                    party_terms.append(alias)

    all_kws = (keywords or []) + party_terms

    for i, c in enumerate(chunks, 1):
        if c['type'] == 'transcript':
            mins = int((c.get('start_time') or 0) // 60)
            speaker = c.get('speaker', 'Onbekend')
            header = (
                f"[Bron {i}] Transcript — {c['gemeente']}, "
                f"{c.get('vergadering', '?')} ({c.get('datum', '?')}) "
                f"— {speaker} op {mins}m"
            )
        else:
            header = (
                f"[Bron {i}] Document — {c['gemeente']}, "
                f"{c.get('title', c.get('vergadering', '?'))} ({c.get('datum', '?')})"
            )
        snippet = _best_snippet(c['text'], all_kws, window=600)
        parts.append(f"{header}\n{snippet}")

    return '\n\n---\n\n'.join(parts)


@app.post("/api/hub/search")
async def hub_search(body: dict):
    """RAG Intelligence Hub — altijd LLM-antwoord met bronnen. Streamt SSE."""
    import json as _json
    import re as _re
    import asyncio as _asyncio
    import requests as _requests

    query = (body.get('query') or '').strip()
    if not query:
        raise HTTPException(400, "Geen zoekvraag opgegeven")

    # Vorige context (follow-up): optioneel meesturen
    prev_context = (body.get('prev_context') or '').strip()

    # Filters uit body (kunnen ook uit parse komen)
    gemeente_override = body.get('gemeente') or None
    date_from_override = body.get('date_from') or None
    date_to_override = body.get('date_to') or None

    # ── Parse filters ──────────────────────────────────────────────────────
    parsed = _hub_parse_filters(query)
    gemeente = gemeente_override or parsed.get('gemeente')
    gemeente_list = parsed.get('gemeente_list')
    province = parsed.get('province')
    parties = parsed.get('parties', [])
    query_type = parsed.get('query_type', 'general')
    date_from = date_from_override or parsed.get('date_from')
    date_to = date_to_override or parsed.get('date_to')

    # Gemeente-override gaat boven province
    if gemeente_override:
        gemeente_list = None
        province = None
        query_type = 'gemeente_research'

    # Keywords voor retrieval en snippet-extractie
    # Locatiewoorden (provincie, gemeente) uitsluiten — die filteren via SQL, niet via keyword-match
    _loc_words = parsed.get('location_words', set())
    _base_kws = [
        w for w in _re.split(r'\W+', query.lower())
        if len(w) >= 3 and w not in _HUB_STOPWORDS and w not in _loc_words
    ]
    _primary_kws, _synonym_kws = _hub_expand_keywords(_base_kws)
    # kws = primaire + synoniemen, gebruikt voor snippet-highlighting en context
    kws = _primary_kws + [s for s in _synonym_kws if s not in _primary_kws]

    def _call_llm(prompt: str, max_tokens: int = 600) -> str:
        """Roep het LLM aan. Altijd — ook bij fout geeft fallback tekst."""
        try:
            resp = _requests.post(
                f"{config.LLM_BASE_URL}/api/generate",
                json={
                    "model": config.HUB_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "keep_alive": "10m",
                    "options": {
                        "num_predict": max_tokens,
                        "temperature": 0.2,
                        "top_p": 0.9,
                    },
                },
                timeout=120,
            )
            if resp.status_code == 200:
                text = resp.json().get('response', '').strip()
                if text and len(text) > 20:
                    return text
        except Exception:
            pass
        return None

    def _build_prompt(query: str, context: str, query_type: str,
                      gemeente: str, province: str, parties: list,
                      total_docs: int, prev_context: str) -> tuple:
        """Bouw de LLM-prompt op basis van query-type. Retourneert (prompt, max_tokens)."""

        prev_section = ""
        if prev_context:
            prev_section = (
                f"\n\nVORIGE CONTEXT (gebruik dit als aanvulling, niet herhalen):\n"
                f"{prev_context[:800]}\n"
            )

        if query_type == 'party_research' and parties:
            party_str = ', '.join(parties)
            loc_str = f" in {province}" if province else (f" in {gemeente}" if gemeente else "")
            prompt = (
                f"Je bent een politiek analist die gemeenteraadsdata analyseert.\n"
                f"VRAAG: {query}\n\n"
                f"BRONNEN ({total_docs} gevonden{loc_str}):\n{context}\n"
                f"{prev_section}\n"
                f"Schrijf een analyse over {party_str}{loc_str} op basis van de bronnen.\n"
                f"Behandel: (1) Standpunten die ze innemen, (2) Moties of besluiten die ze steunen/indienen, "
                f"(3) Concrete voorbeelden uit de bronnen.\n"
                f"Schrijf 2-4 alinea's lopende tekst in correct Nederlands. "
                f"Gebruik ALLEEN feiten uit de bronnen. Vermeld bronnummer als [Bron N].\n"
                f"ANTWOORD:"
            )
            return prompt, 700

        elif query_type in ('gebied_research',) and province:
            prompt = (
                f"Je bent een politiek analist die gemeenteraadsdata analyseert.\n"
                f"VRAAG: {query}\n\n"
                f"BRONNEN ({total_docs} gevonden in {province}):\n{context}\n"
                f"{prev_section}\n"
                f"Schrijf een overzicht van wat er in {province} speelt rond dit onderwerp.\n"
                f"Noem concrete gemeenten, besluiten of uitspraken. "
                f"Schrijf 2-4 alinea's lopende tekst in correct Nederlands. "
                f"Gebruik ALLEEN feiten uit de bronnen. Vermeld bronnummer als [Bron N].\n"
                f"ANTWOORD:"
            )
            return prompt, 700

        elif query_type == 'gemeente_research' and gemeente:
            prompt = (
                f"Je bent een politiek analist die gemeenteraadsdata analyseert.\n"
                f"VRAAG: {query}\n\n"
                f"BRONNEN ({total_docs} gevonden in {gemeente}):\n{context}\n"
                f"{prev_section}\n"
                f"Schrijf een analyse voor de gemeente {gemeente}.\n"
                f"Beschrijf concreet: welke voorstellen, besluiten of standpunten zijn er? "
                f"Schrijf 2-4 alinea's lopende tekst in correct Nederlands. "
                f"Gebruik ALLEEN feiten uit de bronnen. Vermeld bronnummer als [Bron N].\n"
                f"ANTWOORD:"
            )
            return prompt, 600

        else:
            # Algemene vraag
            loc_str = f" in Nederland" if not province else f" in {province}"
            prompt = (
                f"Je bent een politiek analist die gemeenteraadsdata analyseert.\n"
                f"VRAAG: {query}\n\n"
                f"BRONNEN ({total_docs} gevonden{loc_str}):\n{context}\n"
                f"{prev_section}\n"
                f"Schrijf een helder overzicht op basis van de bronnen.\n"
                f"Beschrijf wat er concreet speelt, noem gemeenten en besluiten. "
                f"Schrijf 2-4 alinea's in correct Nederlands. "
                f"Gebruik ALLEEN feiten uit de bronnen. Vermeld bronnummer als [Bron N].\n"
                f"ANTWOORD:"
            )
            return prompt, 600

    def _make_followups(query: str, query_type: str, gemeente: str,
                        province: str, parties: list, chunks: list) -> list:
        """Genereer 2-3 relevante vervolgvragen op basis van context."""
        fqs = []
        kws_short = [w for w in _re.split(r'\W+', query.lower()) if len(w) >= 4]
        topic = ' '.join(kws_short[:3]) if kws_short else query[:30]

        gem_names = list({c.get('gemeente', '') for c in chunks if c.get('gemeente')})
        has_docs = any(c['type'] == 'document' for c in chunks)
        has_transcripts = any(c['type'] == 'transcript' for c in chunks)
        has_moties = any(
            any(kw in (c.get('title', '') + c.get('vergadering', '')).lower()
                for kw in ['motie', 'besluit', 'amendement'])
            for c in chunks
        )

        if query_type == 'party_research' and parties:
            party = parties[0]
            # Drill down op gemeente
            if gem_names:
                top_gem = gem_names[0]
                fqs.append({
                    'text': f"{party} in {top_gem} — details",
                    'query': query,
                    'gemeente': top_gem,
                })
            # Vergelijk met andere partij
            _CONTRAST = {
                'VVD': 'D66', 'D66': 'VVD', 'CDA': 'PvdA', 'PvdA': 'CDA',
                'GroenLinks': 'VVD', 'SP': 'VVD', 'PVV': 'GroenLinks',
            }
            if party in _CONTRAST:
                other = _CONTRAST[party]
                fqs.append({
                    'text': f"Vergelijk {party} met {other}",
                    'query': query.replace(parties[0], f"{parties[0]} en {other}"),
                    'gemeente': gemeente,
                })
            if has_moties:
                fqs.append({
                    'text': f"Welke moties heeft {party} ingediend?",
                    'query': f"Moties en besluiten van {party} over {topic}",
                    'gemeente': gemeente,
                })

        elif query_type == 'gebied_research' and province:
            if len(gem_names) >= 2:
                fqs.append({
                    'text': f"Zoom in op {gem_names[0]}",
                    'query': query,
                    'gemeente': gem_names[0],
                })
            if len(gem_names) >= 2:
                fqs.append({
                    'text': f"Vergelijk {gem_names[0]} en {gem_names[1]}",
                    'query': f"Verschil tussen {gem_names[0]} en {gem_names[1]} over {topic}",
                    'gemeente': None,
                })

        else:
            if gem_names:
                fqs.append({
                    'text': f"Meer over {gem_names[0]}",
                    'query': query,
                    'gemeente': gem_names[0],
                })
            if has_transcripts:
                fqs.append({
                    'text': f"Wat zeiden raadsleden over {topic}?",
                    'query': f"Raadsleden uitspraken over {topic}",
                    'gemeente': gemeente,
                })
            if has_moties:
                fqs.append({
                    'text': f"Ingediende moties over {topic}",
                    'query': f"Moties en besluiten over {topic}",
                    'gemeente': gemeente,
                })
            if len(gem_names) >= 2 and not gemeente:
                fqs.append({
                    'text': f"Vergelijk {gem_names[0]} en {gem_names[1]}",
                    'query': f"Verschil {gem_names[0]} {gem_names[1]} {topic}",
                    'gemeente': None,
                })

        return fqs[:3]

    async def _stream():
        loop = _asyncio.get_event_loop()

        # ── Stap 1: stuur entity feedback ──────────────────────────────────
        yield f"data: {_json.dumps({'type': 'entities', 'parties': parties, 'province': province, 'gemeente': gemeente, 'query_type': query_type})}\n\n"

        # ── Stap 2: retrieval ───────────────────────────────────────────────
        yield f"data: {_json.dumps({'type': 'status', 'text': 'Bronnen zoeken...'})}\n\n"

        top_k = 12 if not gemeente else 8
        chunks = await loop.run_in_executor(None, lambda: _hub_retrieve(
            query, gemeente=gemeente, date_from=date_from, date_to=date_to,
            top_k=top_k, gemeente_list=gemeente_list, parties=parties or None,
        ))

        # Fallback: verbreed zoekbereik als niets gevonden met filters
        if not chunks and (date_from or date_to or gemeente or gemeente_list):
            chunks = await loop.run_in_executor(None, lambda: _hub_retrieve(
                query, gemeente=gemeente, date_from=None, date_to=None,
                top_k=top_k, gemeente_list=gemeente_list, parties=parties or None,
            ))

        # Stuur bronnen naar UI
        sources_payload = []
        for i, c in enumerate(chunks[:8], 1):
            s = {
                'ref': i, 'type': c['type'],
                'datum': c.get('datum'), 'gemeente': c.get('gemeente'),
            }
            if c['type'] == 'transcript':
                s.update({
                    'vergadering': c.get('vergadering'),
                    'speaker': c.get('speaker'),
                    'start_time': c.get('start_time'),
                    'meeting_id': c.get('meeting_id'),
                    'preview': c['text'][:200],
                })
            else:
                s.update({
                    'title': c.get('title'),
                    'url': c.get('url'),
                    'vergadering': c.get('vergadering'),
                    'preview': c['text'][:200],
                })
            sources_payload.append(s)
        yield f"data: {_json.dumps({'type': 'sources', 'sources': sources_payload})}\n\n"

        # ── Stap 3: als geen chunks → eerlijk melden ────────────────────────
        if not chunks:
            yield f"data: {_json.dumps({'type': 'token', 'text': 'Geen relevante documenten of transcripten gevonden voor deze zoekvraag. Probeer een bredere of andere formulering.'})}\n\n"
            yield f"data: {_json.dumps({'type': 'done'})}\n\n"
            return

        # ── Stap 4: bouw context en prompt ─────────────────────────────────
        yield f"data: {_json.dumps({'type': 'status', 'text': 'Antwoord genereren...'})}\n\n"

        context = _hub_build_context(chunks[:8], keywords=kws, parties=parties or None)
        total_docs = len(chunks)

        llm_prompt, max_tok = _build_prompt(
            query=query, context=context, query_type=query_type,
            gemeente=gemeente, province=province, parties=parties,
            total_docs=total_docs, prev_context=prev_context,
        )

        # ── Stap 5: LLM aanroepen ───────────────────────────────────────────
        answer = await loop.run_in_executor(None, lambda: _call_llm(llm_prompt, max_tokens=max_tok))

        if not answer:
            # Fallback: geef gestructureerde samenvatting als LLM faalt
            lines = [f"**{total_docs} bronnen gevonden**\n"]
            for i, c in enumerate(chunks[:6], 1):
                if c['type'] == 'transcript':
                    snippet = _best_snippet(c['text'], kws, 200)
                    lines.append(f"[Bron {i}] **{c.get('speaker', 'Spreker')}** ({c.get('gemeente')}, {c.get('datum')}): {snippet}")
                else:
                    snippet = _best_snippet(c['text'], kws, 200)
                    lines.append(f"[Bron {i}] **{c.get('title', 'Document')}** ({c.get('gemeente')}, {c.get('datum')}): {snippet}")
            answer = '\n\n'.join(lines)

        # Stuur antwoord als token (of gestreamd als LLM streaming aan staat)
        yield f"data: {_json.dumps({'type': 'token', 'text': answer})}\n\n"

        # ── Stap 6: altijd vervolgvragen ────────────────────────────────────
        followups = _make_followups(query, query_type, gemeente, province, parties, chunks)
        for fq in followups:
            yield f"data: {_json.dumps({'type': 'followup', 'text': fq['text'], 'query': fq['query'], 'gemeente': fq.get('gemeente')})}\n\n"

        yield f"data: {_json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})



# ── Statische files (PWA) ────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


_APP_HTML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'ranst-desktop', 'renderer', 'index.html')

@app.get("/app", response_class=HTMLResponse)
async def serve_electron_app():
    """Serveert de Electron UI — geen auth vereist zodat Electron hem kan laden."""
    with open(_APP_HTML, encoding='utf-8') as f:
        return f.read()

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(config.STATIC_DIR, 'index.html'), encoding='utf-8') as f:
        return f.read()


@app.get("/manifest.json")
async def manifest():
    with open(os.path.join(config.STATIC_DIR, 'manifest.json')) as f:
        return JSONResponse(json.loads(f.read()))


@app.get("/sw.js")
async def service_worker():
    with open(os.path.join(config.STATIC_DIR, 'sw.js')) as f:
        return Response(content=f.read(), media_type="application/javascript")
