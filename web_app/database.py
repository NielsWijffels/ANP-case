"""
Database module — artikelen en transcripten.
Geen authenticatie: voorkeuren worden in de browser (localStorage) opgeslagen.
"""
import json
import os
import sqlite3

from . import config


def get_db():
    """Open de hoofd-database (ranst.db)."""
    os.makedirs(config.DB_DIR, exist_ok=True)
    conn = sqlite3.connect(config.RANST_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id      INTEGER,
            gemeente        TEXT,
            topic           TEXT,
            level           TEXT,
            title           TEXT,
            body            TEXT,
            score           REAL,
            indicators      TEXT,
            livestream_url  TEXT,
            t_start         REAL,
            t_end           REAL,
            updates         TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_articles_created
            ON articles(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_articles_topic
            ON articles(topic, level);

        CREATE TABLE IF NOT EXISTS transcript_chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id  INTEGER NOT NULL,
            start_time  REAL,
            end_time    REAL,
            speaker     TEXT,
            text        TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_chunks_meeting
            ON transcript_chunks(meeting_id, start_time);

        CREATE TABLE IF NOT EXISTS meeting_summaries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id  INTEGER NOT NULL UNIQUE,
            gemeente    TEXT,
            summary     TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS meeting_schets (
            meeting_id  INTEGER NOT NULL PRIMARY KEY,
            schets      TEXT NOT NULL,
            input_hash  TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );
    """)
    # Migratie: voeg input_hash toe aan meeting_schets als die nog niet bestaat
    try:
        conn.execute("ALTER TABLE meeting_schets ADD COLUMN input_hash TEXT")
        conn.commit()
    except Exception:
        pass
    conn.commit()
    return conn


def get_meetings_db():
    """Open de meetings database."""
    if not os.path.exists(config.MEETINGS_DB):
        return None
    conn = sqlite3.connect(config.MEETINGS_DB, timeout=1)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=1000")
    return conn


# ── Articles ────────────────────────────────────────────────────────────────

def create_article(meeting_id, gemeente, topic, level, title, body,
                   score, indicators=None, livestream_url=None,
                   t_start=None, t_end=None, topics=None):
    conn = get_db()
    for col, coltype in [("updates", "TEXT"), ("topics", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {coltype}")
            conn.commit()
        except Exception:
            pass
    indicators_json = json.dumps(indicators) if indicators else None
    topics_json = json.dumps(topics) if topics else None
    cur = conn.execute(
        "INSERT INTO articles "
        "(meeting_id, gemeente, topic, level, title, body, score, indicators, "
        "livestream_url, t_start, t_end, topics) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (meeting_id, gemeente, topic, level, title, body,
         score, indicators_json, livestream_url, t_start, t_end, topics_json)
    )
    article_id = cur.lastrowid
    conn.commit()
    conn.close()
    return article_id


def get_articles(gemeenten=None, topics=None, level=None, limit=100, offset=0):
    """Haal artikelen op, optioneel gefilterd op gemeente/topic/level."""
    conn = get_db()
    wheres, params = [], []

    if gemeenten:
        placeholders = ','.join('?' * len(gemeenten))
        wheres.append(f"gemeente IN ({placeholders})")
        params.extend(gemeenten)
    if topics:
        placeholders = ','.join('?' * len(topics))
        wheres.append(f"topic IN ({placeholders})")
        params.extend(topics)
    if level:
        wheres.append("level = ?")
        params.append(level)

    wheres.append("meeting_id IS NOT NULL")
    wheres.append("(body IS NOT NULL AND length(trim(body)) > 10 AND body NOT LIKE 'NIET_RELEVANT%' AND title NOT LIKE 'NIET_RELEVANT%')")
    where_sql = f"WHERE {' AND '.join(wheres)}" if wheres else ""
    rows = conn.execute(
        f"SELECT * FROM articles {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset]
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_articles_for_meeting(meeting_id, limit=20):
    """Haal recente artikelen voor een meeting op (voor deduplicatie)."""
    conn = get_db()
    rows = conn.execute(
        "SELECT id, topic, level, title, body AS summary, score FROM articles "
        "WHERE meeting_id = ? ORDER BY created_at DESC LIMIT ?",
        (meeting_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_article_update(article_id, update_text):
    """Voeg een update toe aan een bestaand artikel (JSON-array in updates kolom)."""
    conn = get_db()
    row = conn.execute("SELECT updates FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        conn.close()
        return
    existing = json.loads(row['updates']) if row['updates'] else []
    existing.append(update_text)
    conn.execute(
        "UPDATE articles SET updates = ? WHERE id = ?",
        (json.dumps(existing, ensure_ascii=False), article_id)
    )
    conn.commit()
    conn.close()


# ── Transcripts ──────────────────────────────────────────────────────────────

def add_transcript_chunk(meeting_id, text, speaker=None,
                         start_time=None, end_time=None):
    conn = get_db()
    conn.execute(
        "INSERT INTO transcript_chunks "
        "(meeting_id, start_time, end_time, speaker, text) VALUES (?,?,?,?,?)",
        (meeting_id, start_time, end_time, speaker, text)
    )
    conn.commit()
    conn.close()


def save_meeting_summary(meeting_id, gemeente, summary):
    conn = get_db()
    conn.execute(
        "INSERT INTO meeting_summaries (meeting_id, gemeente, summary) VALUES (?,?,?) "
        "ON CONFLICT(meeting_id) DO UPDATE SET summary=excluded.summary, created_at=datetime('now')",
        (meeting_id, gemeente, summary)
    )
    conn.commit()
    conn.close()


def get_meeting_summary(meeting_id):
    conn = get_db()
    row = conn.execute(
        "SELECT summary, created_at FROM meeting_summaries WHERE meeting_id = ?",
        (meeting_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_articles_for_meeting(meeting_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM articles WHERE meeting_id = ? ORDER BY created_at DESC",
        (meeting_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_transcript(meeting_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM transcript_chunks WHERE meeting_id = ? ORDER BY start_time",
        (meeting_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_window_text(meeting_id, last_n_seconds=1800):
    """Haal transcript-tekst op voor de laatste last_n_seconds (standaard 30 min).

    Gebruikt start_time voor een precieze tijdsgebaseerde window,
    onafhankelijk van het aantal segmenten.
    """
    conn = get_db()
    # Bepaal het eindpunt (laatste start_time voor deze meeting)
    row = conn.execute(
        "SELECT MAX(start_time) FROM transcript_chunks WHERE meeting_id = ?",
        (meeting_id,)
    ).fetchone()
    max_time = row[0] if row and row[0] is not None else 0
    cutoff = max_time - last_n_seconds

    rows = conn.execute(
        "SELECT speaker, text FROM transcript_chunks "
        "WHERE meeting_id = ? AND start_time >= ? "
        "ORDER BY start_time ASC",
        (meeting_id, cutoff)
    ).fetchall()
    conn.close()
    return '\n'.join(f"{r['speaker'] or 'Spreker'}: {r['text']}" for r in rows)


def get_meeting_duration(meeting_id):
    """Geef de tijdsduur in seconden van het tot nu toe opgenomen transcript."""
    conn = get_db()
    row = conn.execute(
        "SELECT MIN(start_time), MAX(start_time) FROM transcript_chunks WHERE meeting_id = ?",
        (meeting_id,)
    ).fetchone()
    conn.close()
    if row and row[0] is not None and row[1] is not None:
        return row[1] - row[0]
    return 0
