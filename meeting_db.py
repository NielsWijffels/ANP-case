#!/usr/bin/env python3
"""
Gemeente Vergader Database & Live Monitor
==========================================
Houdt een SQLite database bij van alle gemeentevergaderingen.
Draait dagelijks om schema's bij te werken en monitort automatisch
wanneer livestreams beginnen.

Gebruik:
    python meeting_db.py update       # Haal nieuwe vergaderingen op en sla op in DB
    python meeting_db.py today        # Toon vergaderingen van vandaag
    python meeting_db.py upcoming     # Toon vergaderingen komende 7 dagen
    python meeting_db.py monitor      # Start live monitor (joint streams automatisch)
    python meeting_db.py stats        # Toon database statistieken
"""

import json
import os
import re
import signal
import sqlite3
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ============================================================================
# CONFIGURATIE
# ============================================================================
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
DB_PATH = os.path.join(DB_DIR, 'meetings.db')
STREAMS_PATH = os.path.join(DB_DIR, 'gemeente_streams.json')

SSL_CTX = ssl.create_default_context()
API_HEADERS = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}

# Monitor: hoeveel minuten van tevoren de stream "klaar" zetten
MONITOR_LEAD_MINUTES = 2
# Monitor: check-interval in seconden
MONITOR_CHECK_INTERVAL = 30
# Livestream probe: interval in minuten
STREAM_PROBE_INTERVAL = 5
# Probing draait 24/7 zodat geen enkele livestream gemist wordt
MONITOR_WINDOW_START = 0   # uur (inclusief)
MONITOR_WINDOW_END   = 24  # uur (exclusief)

# Types die (vrijwel) altijd een livestream hebben
_LIVESTREAM_KEYWORDS = (
    'raad', 'commissie', 'politiek', 'debat', 'besluit',
    'stadserf', 'info/debat',
)

def _type_has_livestream(cat_type):
    """Bepaal of het type vergadering normaal een livestream heeft."""
    if not cat_type:
        return False
    lower = cat_type.lower()
    return any(kw in lower for kw in _LIVESTREAM_KEYWORDS)

# ============================================================================
# DATABASE SETUP
# ============================================================================

def get_db():
    """Open database connectie en maak tabellen aan als ze niet bestaan."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS gemeenten (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            naam        TEXT UNIQUE NOT NULL,
            slug        TEXT,
            notubiz_id  INTEGER,
            platforms   TEXT,          -- JSON array van beschikbare platforms
            updated_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS meetings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            gemeente_id     INTEGER NOT NULL REFERENCES gemeenten(id),
            external_id     TEXT,      -- NotUBiz event ID of vergadering-URL
            datum           TEXT NOT NULL,  -- YYYY-MM-DD
            tijd            TEXT,          -- HH:MM
            titel           TEXT,
            type            TEXT,          -- Raad, Commissie, etc.
            categorie       TEXT,
            locatie         TEXT,
            voorzitter      TEXT,
            url             TEXT,          -- Directe vergader-URL
            bron            TEXT,          -- notubiz_api, ibabs_scrape, etc.
            has_livestream  INTEGER DEFAULT 0,
            status          TEXT DEFAULT 'scheduled',  -- scheduled, live, ended, missed
            joined_at       TEXT,          -- Wanneer de monitor joinde
            ended_at        TEXT,
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(gemeente_id, external_id)
        );

        CREATE INDEX IF NOT EXISTS idx_meetings_datum ON meetings(datum);
        CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status);
        CREATE INDEX IF NOT EXISTS idx_meetings_gemeente ON meetings(gemeente_id);
        CREATE INDEX IF NOT EXISTS idx_meetings_livestream ON meetings(has_livestream, datum, status);

        CREATE TABLE IF NOT EXISTS sync_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT DEFAULT (datetime('now')),
            gemeenten_synced    INTEGER,
            meetings_added      INTEGER,
            meetings_updated    INTEGER,
            duration_seconds    REAL
        );
    """)
    conn.commit()
    return conn


# ============================================================================
# NOTUBIZ API
# ============================================================================

def _fetch_json(url, timeout=12):
    """Haal JSON op van een URL."""
    try:
        req = urllib.request.Request(url, headers=API_HEADERS)
        resp = urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)
        return json.loads(resp.read(131072).decode('utf-8'))
    except Exception:
        return None


def _fetch_html(url, timeout=10):
    """Haal HTML-pagina op als string."""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'text/html',
        })
        resp = urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)
        return resp.read(262144).decode('utf-8', errors='replace')
    except Exception:
        return None


def probe_livestream(url):
    """Probe een vergader-URL om te checken of er een actieve livestream is.

    Checkt de HTML voor video-player indicatoren, iframes, of stream-URLs.
    Returns True als livestream-indicatoren gevonden zijn.
    """
    if not url:
        return False

    html = _fetch_html(url)
    if not html:
        return False

    lower = html.lower()
    # Indicatoren van een actieve video/livestream op de pagina
    indicators = (
        '.m3u8',           # HLS stream
        'video-player',    # Video player component
        'livestream',      # Expliciet livestream label
        'notubiz-player',  # NotUBiz specifieke player
        'player.notubiz',  # NotUBiz player domein
        'videostream',     # Video stream
        '<video',          # HTML5 video element
        'mediaplayer',     # Media player
        'live-uitzending', # Live uitzending
        'webcast',         # Webcast
    )
    return any(ind in lower for ind in indicators)


def fetch_notubiz_events(org_id):
    """Haal alle events op voor een NotUBiz organisatie."""
    data = _fetch_json(f'https://api.notubiz.nl/organisations/{org_id}/events')
    if not data:
        return []

    events = data.get('events', {}).get('event', [])
    if not isinstance(events, list):
        events = [events] if events else []

    return events


# ============================================================================
# DATABASE UPDATE (dagelijks draaien)
# ============================================================================

def sync_gemeente(conn, gemeente_data):
    """Synchroniseer vergaderingen voor één gemeente naar de database."""
    naam = gemeente_data['gemeente']
    slug = gemeente_data.get('slug', '')
    sources = gemeente_data.get('sources', [])
    platforms = json.dumps([s['platform'] for s in sources])

    # Zoek notubiz_id
    notubiz_id = None
    for s in sources:
        if s['platform'] == 'notubiz' and 'notubiz_id' in s:
            notubiz_id = s['notubiz_id']
            break

    # Upsert gemeente
    conn.execute("""
        INSERT INTO gemeenten (naam, slug, notubiz_id, platforms, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(naam) DO UPDATE SET
            slug = excluded.slug,
            notubiz_id = excluded.notubiz_id,
            platforms = excluded.platforms,
            updated_at = datetime('now')
    """, (naam, slug, notubiz_id, platforms))

    gemeente_id = conn.execute(
        "SELECT id FROM gemeenten WHERE naam = ?", (naam,)
    ).fetchone()['id']

    added = 0
    updated = 0

    # Alleen NotUBiz-gemeenten hebben betrouwbare schedule data
    if notubiz_id is None:
        return added, updated

    events = fetch_notubiz_events(notubiz_id)
    today = datetime.now().strftime('%Y-%m-%d')

    for event in events:
        attrs = event.get('@attributes', {})
        event_date = attrs.get('date', '')
        event_time = attrs.get('time', '')
        event_id = str(attrs.get('id', ''))

        # Sla verlopen events over
        if event_date < today:
            continue

        category = event.get('category', {})
        cat_type = category.get('type', {}).get('label', '')
        raw_url = event.get('url', '')
        # URL bevat soms datum na spatie
        clean_url = raw_url.split(' ')[0] if raw_url else ''

        has_livestream = 1 if _type_has_livestream(cat_type) else 0

        # Upsert meeting
        existing = conn.execute(
            "SELECT id, status FROM meetings WHERE gemeente_id = ? AND external_id = ?",
            (gemeente_id, event_id)
        ).fetchone()

        if existing:
            # Update alleen als meeting nog niet gestart/afgelopen is
            if existing['status'] in ('scheduled', 'missed'):
                conn.execute("""
                    UPDATE meetings SET
                        datum = ?, tijd = ?, titel = ?, type = ?,
                        categorie = ?, locatie = ?, voorzitter = ?,
                        url = ?, has_livestream = ?, updated_at = datetime('now')
                    WHERE id = ?
                """, (
                    event_date, event_time, event.get('title', ''),
                    cat_type, category.get('title', ''),
                    event.get('location', ''), event.get('chairman', ''),
                    clean_url, has_livestream, existing['id']
                ))
                updated += 1
        else:
            conn.execute("""
                INSERT INTO meetings
                    (gemeente_id, external_id, datum, tijd, titel, type,
                     categorie, locatie, voorzitter, url, bron, has_livestream)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'notubiz_api', ?)
            """, (
                gemeente_id, event_id, event_date, event_time,
                event.get('title', ''), cat_type,
                category.get('title', ''), event.get('location', ''),
                event.get('chairman', ''), clean_url, has_livestream
            ))
            added += 1

    return added, updated


def cmd_update():
    """Haal alle vergaderingen op en sla op in de database."""
    if not os.path.exists(STREAMS_PATH):
        print(f"FOUT: {STREAMS_PATH} niet gevonden.")
        print("Draai eerst: python gemeente_stream_finder.py")
        sys.exit(1)

    with open(STREAMS_PATH, 'r', encoding='utf-8') as f:
        streams_data = json.load(f)

    gemeenten = streams_data['gemeenten']
    # Filter op gemeenten met notubiz (andere bronnen zijn niet betrouwbaar genoeg)
    notubiz_gemeenten = [
        g for g in gemeenten
        if any(s['platform'] == 'notubiz' for s in g.get('sources', []))
    ]

    total = len(notubiz_gemeenten)
    print("=" * 70)
    print("DATABASE UPDATE")
    print(f"Synchroniseren van {total} gemeenten (NotUBiz)...")
    print(f"Database: {DB_PATH}")
    print("=" * 70)

    conn = get_db()
    start = time.time()
    total_added = 0
    total_updated = 0
    synced = 0

    # Serieel per gemeente (API rate limiting)
    for i, g in enumerate(notubiz_gemeenten, 1):
        try:
            added, updated = sync_gemeente(conn, g)
            total_added += added
            total_updated += updated
            synced += 1

            status = f"+{added}" if added else ""
            if updated:
                status += f" ~{updated}"
            if status:
                print(f"  [{i}/{total}] {g['gemeente']}: {status}")
            else:
                print(f"  [{i}/{total}] {g['gemeente']}: up-to-date")

            conn.commit()

            # Kleine pauze om API niet te overbelasten
            if i % 10 == 0:
                time.sleep(0.5)

        except Exception as e:
            print(f"  [{i}/{total}] {g['gemeente']}: FOUT ({e})")

    elapsed = time.time() - start

    # Log sync
    conn.execute("""
        INSERT INTO sync_log (gemeenten_synced, meetings_added, meetings_updated, duration_seconds)
        VALUES (?, ?, ?, ?)
    """, (synced, total_added, total_updated, round(elapsed, 1)))
    conn.commit()

    # Markeer gemiste vergaderingen
    today = datetime.now().strftime('%Y-%m-%d')
    missed = conn.execute("""
        UPDATE meetings SET status = 'missed'
        WHERE datum < ? AND status = 'scheduled'
    """, (today,)).rowcount
    conn.commit()

    # Stats
    total_in_db = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    upcoming = conn.execute(
        "SELECT COUNT(*) FROM meetings WHERE datum >= ? AND status = 'scheduled'",
        (today,)
    ).fetchone()[0]
    today_count = conn.execute(
        "SELECT COUNT(*) FROM meetings WHERE datum = ?", (today,)
    ).fetchone()[0]

    conn.close()

    print(f"\n{'=' * 70}")
    print("SYNC RESULTAAT")
    print(f"{'=' * 70}")
    print(f"Gemeenten gesynchroniseerd: {synced}")
    print(f"Vergaderingen toegevoegd:   {total_added}")
    print(f"Vergaderingen bijgewerkt:   {total_updated}")
    print(f"Gemist gemarkeerd:          {missed}")
    print(f"Totaal in database:         {total_in_db}")
    print(f"Aankomend:                  {upcoming}")
    print(f"Vandaag:                    {today_count}")
    print(f"Duur:                       {elapsed:.0f}s")


# ============================================================================
# QUERIES
# ============================================================================

def cmd_today():
    """Toon vergaderingen van vandaag."""
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')

    rows = conn.execute("""
        SELECT m.*, g.naam as gemeente
        FROM meetings m
        JOIN gemeenten g ON g.id = m.gemeente_id
        WHERE m.datum = ?
        ORDER BY m.tijd, g.naam
    """, (today,)).fetchall()

    conn.close()

    print(f"\n{'=' * 70}")
    print(f"VERGADERINGEN VANDAAG ({today}) — {len(rows)} stuks")
    print(f"{'=' * 70}")

    if not rows:
        print("  Geen vergaderingen vandaag.")
        return

    for r in rows:
        livestream = " [LIVESTREAM]" if r['has_livestream'] else ""
        status_icon = {
            'scheduled': ' ',
            'live': '►',
            'ended': '✓',
            'missed': '✗',
        }.get(r['status'], '?')
        print(f"  {status_icon} {r['tijd'] or '??:??':>5s}  {r['gemeente']:30s}  {r['titel']}{livestream}")
        if r['url']:
            print(f"           {r['url']}")


def cmd_upcoming():
    """Toon vergaderingen komende 7 dagen."""
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    week = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')

    rows = conn.execute("""
        SELECT m.*, g.naam as gemeente
        FROM meetings m
        JOIN gemeenten g ON g.id = m.gemeente_id
        WHERE m.datum BETWEEN ? AND ?
          AND m.status = 'scheduled'
        ORDER BY m.datum, m.tijd, g.naam
    """, (today, week)).fetchall()

    conn.close()

    print(f"\n{'=' * 70}")
    print(f"VERGADERINGEN {today} t/m {week} — {len(rows)} stuks")
    print(f"{'=' * 70}")

    current_date = None
    for r in rows:
        if r['datum'] != current_date:
            current_date = r['datum']
            # Dag naam
            dt = datetime.strptime(current_date, '%Y-%m-%d')
            dag = ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo'][dt.weekday()]
            print(f"\n  --- {dag} {current_date} ---")

        livestream = " [LIVE]" if r['has_livestream'] else ""
        print(f"    {r['tijd'] or '??:??':>5s}  {r['gemeente']:28s}  {r['titel']}{livestream}")


def cmd_stats():
    """Toon database statistieken."""
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')

    total = conn.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
    gemeenten = conn.execute("SELECT COUNT(*) FROM gemeenten").fetchone()[0]
    scheduled = conn.execute(
        "SELECT COUNT(*) FROM meetings WHERE status = 'scheduled' AND datum >= ?",
        (today,)
    ).fetchone()[0]
    today_count = conn.execute(
        "SELECT COUNT(*) FROM meetings WHERE datum = ?", (today,)
    ).fetchone()[0]
    with_stream = conn.execute(
        "SELECT COUNT(*) FROM meetings WHERE has_livestream = 1 AND datum >= ? AND status = 'scheduled'",
        (today,)
    ).fetchone()[0]
    joined = conn.execute(
        "SELECT COUNT(*) FROM meetings WHERE status IN ('live', 'ended')"
    ).fetchone()[0]

    last_sync = conn.execute(
        "SELECT * FROM sync_log ORDER BY id DESC LIMIT 1"
    ).fetchone()

    # Vergaderingen per dag (komende week)
    week = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
    per_day = conn.execute("""
        SELECT datum, COUNT(*) as n
        FROM meetings
        WHERE datum BETWEEN ? AND ? AND status = 'scheduled'
        GROUP BY datum
        ORDER BY datum
    """, (today, week)).fetchall()

    conn.close()

    print(f"\n{'=' * 70}")
    print("DATABASE STATISTIEKEN")
    print(f"{'=' * 70}")
    print(f"  Database:            {DB_PATH}")
    print(f"  Gemeenten:           {gemeenten}")
    print(f"  Totaal meetings:     {total}")
    print(f"  Aankomend:           {scheduled}")
    print(f"  Met livestream:      {with_stream}")
    print(f"  Vandaag:             {today_count}")
    print(f"  Gejoined:            {joined}")

    if last_sync:
        print(f"\n  Laatste sync:        {last_sync['timestamp']}")
        print(f"    Gemeenten:         {last_sync['gemeenten_synced']}")
        print(f"    Toegevoegd:        {last_sync['meetings_added']}")
        print(f"    Bijgewerkt:        {last_sync['meetings_updated']}")

    if per_day:
        print(f"\n  Komende week:")
        for row in per_day:
            dt = datetime.strptime(row['datum'], '%Y-%m-%d')
            dag = ['ma', 'di', 'wo', 'do', 'vr', 'za', 'zo'][dt.weekday()]
            bar = '█' * row['n']
            print(f"    {dag} {row['datum']}: {row['n']:3d} {bar}")


# ============================================================================
# LIVE MONITOR
# ============================================================================

def _in_monitor_window(hour):
    """Check of het huidige uur binnen het monitorvenster valt."""
    return MONITOR_WINDOW_START <= hour < MONITOR_WINDOW_END


def _run_stream_probes(conn, today, now):
    """Probe vergaderingen zonder has_livestream om te checken of er een
    stream actief is. Draait elke STREAM_PROBE_INTERVAL minuten.

    Checkt alleen vergaderingen van vandaag die:
    - status = 'scheduled'
    - has_livestream = 0
    - een URL hebben
    - nog niet recent geprobed zijn (cooldown van STREAM_PROBE_INTERVAL min)
    """
    cutoff_time = (now - timedelta(minutes=STREAM_PROBE_INTERVAL)).strftime(
        '%Y-%m-%d %H:%M:%S'
    )

    to_probe = conn.execute("""
        SELECT m.id, m.url, m.titel, m.tijd, g.naam as gemeente,
               m.last_stream_check
        FROM meetings m
        JOIN gemeenten g ON g.id = m.gemeente_id
        WHERE m.datum = ?
          AND m.status = 'scheduled'
          AND m.has_livestream = 0
          AND m.url IS NOT NULL
          AND m.url != ''
          AND (m.last_stream_check IS NULL OR m.last_stream_check < ?)
        ORDER BY m.tijd
    """, (today, cutoff_time)).fetchall()

    if not to_probe:
        return 0

    found = 0
    print(f"\n  [{now.strftime('%H:%M:%S')}] Probing {len(to_probe)} vergaderingen op livestream...")

    for meeting in to_probe:
        has_stream = probe_livestream(meeting['url'])

        conn.execute("""
            UPDATE meetings
            SET last_stream_check = datetime('now')
            WHERE id = ?
        """, (meeting['id'],))

        if has_stream:
            conn.execute("""
                UPDATE meetings
                SET has_livestream = 1, updated_at = datetime('now')
                WHERE id = ?
            """, (meeting['id'],))
            found += 1
            print(f"    ► LIVESTREAM GEVONDEN: {meeting['gemeente']} — {meeting['titel']}")
            print(f"      URL: {meeting['url']}")

    conn.commit()

    if found:
        print(f"  → {found} nieuwe livestream(s) gedetecteerd")
    else:
        print(f"  → Geen nieuwe livestreams gevonden")

    return found


def cmd_monitor():
    """Start de live monitor.
    - Elke 30s: check of een vergadering met has_livestream=1 moet starten
    - Elke 5 min (08:00-23:00): probe vergaderingen zonder livestream-markering
    - Vergaderingen die al live zijn blijven actief tot 4h na starttijd
    """
    conn = get_db()
    running = True

    # Zorg dat last_stream_check kolom bestaat (migratie)
    try:
        conn.execute("ALTER TABLE meetings ADD COLUMN last_stream_check TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # kolom bestaat al

    def handle_signal(sig, frame):
        nonlocal running
        print("\n\nMonitor gestopt.")
        running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    print("=" * 70)
    print("LIVE MONITOR GESTART")
    print(f"Database: {DB_PATH}")
    print(f"Check interval: elke {MONITOR_CHECK_INTERVAL}s")
    print(f"Stream probe: elke {STREAM_PROBE_INTERVAL} min ({MONITOR_WINDOW_START:02d}:00-{MONITOR_WINDOW_END:02d}:00)")
    print(f"Lead time: {MONITOR_LEAD_MINUTES} min voor starttijd")
    print("Druk Ctrl+C om te stoppen")
    print("=" * 70)

    last_probe_minute = -1

    while running:
        now = datetime.now()
        today = now.strftime('%Y-%m-%d')
        current_time = now.strftime('%H:%M')

        # === 1. LIVESTREAM PROBE (elke 5 min, binnen monitorvenster) ===
        probe_minute = (now.hour * 60 + now.minute) // STREAM_PROBE_INTERVAL
        in_window = _in_monitor_window(now.hour)

        if in_window and probe_minute != last_probe_minute:
            last_probe_minute = probe_minute
            _run_stream_probes(conn, today, now)

        # === 2. CHECK STARTENDE VERGADERINGEN (elke 30s) ===
        lead_time = (now + timedelta(minutes=MONITOR_LEAD_MINUTES)).strftime('%H:%M')

        starting = conn.execute("""
            SELECT m.*, g.naam as gemeente
            FROM meetings m
            JOIN gemeenten g ON g.id = m.gemeente_id
            WHERE m.datum = ?
              AND m.tijd <= ?
              AND m.status = 'scheduled'
              AND m.has_livestream = 1
            ORDER BY m.tijd
        """, (today, lead_time)).fetchall()

        for meeting in starting:
            print(f"\n{'!' * 70}")
            print(f"  LIVESTREAM START: {meeting['gemeente']}")
            print(f"  Vergadering: {meeting['titel']}")
            print(f"  Tijd: {meeting['tijd']}")
            print(f"  URL: {meeting['url']}")
            print(f"  Locatie: {meeting['locatie']}")
            print(f"  Voorzitter: {meeting['voorzitter']}")
            print(f"{'!' * 70}")

            conn.execute("""
                UPDATE meetings
                SET status = 'live', joined_at = datetime('now')
                WHERE id = ?
            """, (meeting['id'],))
            conn.commit()

            # TODO: Hier komt de integratie met het transcriptie-model
            # from streaming_main import StreamingProcessor
            # processor = StreamingProcessor(config)
            # processor.process_stream(meeting['url'])

        # === 3. MARKEER AFGELOPEN VERGADERINGEN ===
        # Vergaderingen die > 4 uur geleden begonnen zijn afsluiten
        cutoff = (now - timedelta(hours=4)).strftime('%H:%M')
        ended = conn.execute("""
            UPDATE meetings
            SET status = 'ended', ended_at = datetime('now')
            WHERE datum = ? AND tijd < ? AND status = 'live'
        """, (today, cutoff)).rowcount
        if ended:
            conn.commit()

        # === 4. STATUS OVERZICHT (elke 5 min) ===
        if now.minute % 5 == 0 and now.second < MONITOR_CHECK_INTERVAL:
            upcoming = conn.execute("""
                SELECT m.tijd, g.naam, m.titel
                FROM meetings m
                JOIN gemeenten g ON g.id = m.gemeente_id
                WHERE m.datum = ? AND m.tijd > ? AND m.status = 'scheduled'
                  AND m.has_livestream = 1
                ORDER BY m.tijd
                LIMIT 5
            """, (today, current_time)).fetchall()

            live_count = conn.execute(
                "SELECT COUNT(*) FROM meetings WHERE datum = ? AND status = 'live'",
                (today,)
            ).fetchone()[0]

            unprobed = conn.execute("""
                SELECT COUNT(*) FROM meetings
                WHERE datum = ? AND status = 'scheduled'
                  AND has_livestream = 0 AND url IS NOT NULL AND url != ''
            """, (today,)).fetchone()[0]

            window_status = "ACTIEF" if in_window else "PAUZE (buiten venster)"
            print(f"\n  [{now.strftime('%H:%M:%S')}] Monitor {window_status}")
            print(f"    Live: {live_count} | Aankomend: {len(upcoming)} | Ongeprobed: {unprobed}")
            for u in upcoming:
                print(f"    {u['tijd']}  {u['naam']:28s}  {u['titel']}")

        time.sleep(MONITOR_CHECK_INTERVAL)

    conn.close()


# ============================================================================
# MAIN
# ============================================================================

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == 'update':
        cmd_update()
    elif cmd == 'today':
        cmd_today()
    elif cmd == 'upcoming':
        cmd_upcoming()
    elif cmd == 'monitor':
        cmd_monitor()
    elif cmd == 'stats':
        cmd_stats()
    else:
        print(f"Onbekend commando: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
