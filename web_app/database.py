"""
Database module — gebruikers, voorkeuren, alerts en transcripten.
Aparte SQLite database (users.db) naast de bestaande meetings.db.
"""
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime

from . import config


def _hash_password(password, salt=None):
    """Hash wachtwoord met salt (SHA-256)."""
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def _verify_password(password, stored):
    """Verifieer wachtwoord tegen opgeslagen hash."""
    salt = stored.split(':')[0]
    return _hash_password(password, salt) == stored


def get_users_db():
    """Open users database connectie."""
    os.makedirs(config.DB_DIR, exist_ok=True)
    conn = sqlite3.connect(config.USERS_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT UNIQUE NOT NULL,
            password    TEXT NOT NULL,
            name        TEXT,
            role        TEXT DEFAULT 'ps',
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_topics (
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            topic       TEXT NOT NULL,
            level       TEXT NOT NULL DEFAULT 'geen',
            PRIMARY KEY (user_id, topic)
        );

        CREATE TABLE IF NOT EXISTS user_gemeenten (
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            gemeente    TEXT NOT NULL,
            PRIMARY KEY (user_id, gemeente)
        );

        CREATE TABLE IF NOT EXISTS tokens (
            token       TEXT PRIMARY KEY,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            meeting_id      INTEGER,
            gemeente        TEXT,
            topic           TEXT,
            level           TEXT,
            title           TEXT,
            summary         TEXT,
            quote           TEXT,
            score           REAL,
            indicators      TEXT,
            livestream_url  TEXT,
            timestamp_start REAL,
            timestamp_end   REAL,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS user_alerts (
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            alert_id    INTEGER NOT NULL REFERENCES alerts(id) ON DELETE CASCADE,
            read        INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, alert_id)
        );

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
        CREATE INDEX IF NOT EXISTS idx_alerts_created
            ON alerts(created_at);
    """)

    # Migratie: voeg 'level' kolom toe aan user_topics als die ontbreekt
    try:
        conn.execute("SELECT level FROM user_topics LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE user_topics ADD COLUMN level TEXT NOT NULL DEFAULT 'geen'")
        conn.commit()

    # Migratie: voeg 'level' kolom toe aan alerts als die ontbreekt
    try:
        conn.execute("SELECT level FROM alerts LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE alerts ADD COLUMN level TEXT")
        conn.commit()

    # Migratie: voeg 'indicators' kolom toe aan alerts als die ontbreekt
    try:
        conn.execute("SELECT indicators FROM alerts LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE alerts ADD COLUMN indicators TEXT")
        conn.commit()

    # Migratie: voeg 'livestream_url' kolom toe aan alerts als die ontbreekt
    try:
        conn.execute("SELECT livestream_url FROM alerts LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE alerts ADD COLUMN livestream_url TEXT")
        conn.commit()

    conn.commit()
    return conn


def get_meetings_db():
    """Open de meetings database (read-only voor de web app)."""
    conn = sqlite3.connect(config.MEETINGS_DB)
    conn.row_factory = sqlite3.Row
    return conn


# ── User CRUD ──────────────────────────────────────────────────────────────

def create_user(email, password, name='', role='ps'):
    conn = get_users_db()
    pw_hash = _hash_password(password)
    try:
        conn.execute(
            "INSERT INTO users (email, password, name, role) VALUES (?,?,?,?)",
            (email, pw_hash, name, role)
        )
        conn.commit()
        user = conn.execute(
            "SELECT id, email, name, role FROM users WHERE email = ?", (email,)
        ).fetchone()
        conn.close()
        return dict(user)
    except sqlite3.IntegrityError:
        conn.close()
        return None


def authenticate(email, password):
    conn = get_users_db()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()
    conn.close()
    if user and _verify_password(password, user['password']):
        return dict(user)
    return None


def create_token(user_id):
    token = secrets.token_urlsafe(32)
    conn = get_users_db()
    conn.execute("INSERT INTO tokens (token, user_id) VALUES (?,?)", (token, user_id))
    conn.commit()
    conn.close()
    return token


def get_user_by_token(token):
    conn = get_users_db()
    row = conn.execute(
        "SELECT u.id, u.email, u.name, u.role FROM tokens t "
        "JOIN users u ON u.id = t.user_id WHERE t.token = ?", (token,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_token(token):
    conn = get_users_db()
    conn.execute("DELETE FROM tokens WHERE token = ?", (token,))
    conn.commit()
    conn.close()


# ── Preferences ────────────────────────────────────────────────────────────

def get_preferences(user_id):
    conn = get_users_db()
    # Topics met interest level (geen/bestuurlijk/pers)
    topic_rows = conn.execute(
        "SELECT topic, level FROM user_topics WHERE user_id = ?", (user_id,)
    ).fetchall()
    topics = {r['topic']: r['level'] for r in topic_rows}
    gemeenten = [r['gemeente'] for r in conn.execute(
        "SELECT gemeente FROM user_gemeenten WHERE user_id = ?", (user_id,)
    ).fetchall()]
    conn.close()
    return {'topics': topics, 'gemeenten': gemeenten}


def set_preferences(user_id, topics=None, gemeenten=None):
    """Sla voorkeuren op.

    topics: dict {topic_id: level} bijv. {'wonen_ruimte': 'bestuurlijk', ...}
            of list [topic_id, ...] (backwards compat, zet level op 'bestuurlijk')
    gemeenten: list [gemeente_naam, ...]
    """
    conn = get_users_db()
    if topics is not None:
        conn.execute("DELETE FROM user_topics WHERE user_id = ?", (user_id,))
        if isinstance(topics, dict):
            for topic_id, level in topics.items():
                if level and level != 'geen':
                    conn.execute(
                        "INSERT OR IGNORE INTO user_topics (user_id, topic, level) VALUES (?,?,?)",
                        (user_id, topic_id, level)
                    )
        else:
            # Backwards compatible: list → level 'bestuurlijk'
            for t in topics:
                conn.execute(
                    "INSERT OR IGNORE INTO user_topics (user_id, topic, level) VALUES (?,?,?)",
                    (user_id, t, 'bestuurlijk')
                )
    if gemeenten is not None:
        conn.execute("DELETE FROM user_gemeenten WHERE user_id = ?", (user_id,))
        for g in gemeenten:
            conn.execute(
                "INSERT OR IGNORE INTO user_gemeenten (user_id, gemeente) VALUES (?,?)",
                (user_id, g)
            )
    conn.commit()
    conn.close()


# ── Alerts ─────────────────────────────────────────────────────────────────

def create_alert(meeting_id, gemeente, topic, level, title, summary,
                 score, indicators=None, livestream_url=None,
                 quote=None, t_start=None, t_end=None):
    """Maak een alert aan en koppel aan relevante gebruikers.

    level: 'pers' of 'bestuurlijk'
    Routing: gebruikers die dit topic op dit level (of hoger) hebben staan.
    """
    conn = get_users_db()
    indicators_json = json.dumps(indicators) if indicators else None
    cur = conn.execute(
        "INSERT INTO alerts (meeting_id, gemeente, topic, level, title, summary, "
        "quote, score, indicators, livestream_url, timestamp_start, timestamp_end) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (meeting_id, gemeente, topic, level, title, summary,
         quote, score, indicators_json, livestream_url, t_start, t_end)
    )
    alert_id = cur.lastrowid

    # Koppel alert aan gebruikers met matching topic + level + gemeente
    if level == 'pers':
        # Pers-alerts gaan naar gebruikers met dit topic op 'pers' niveau
        users = conn.execute(
            "SELECT DISTINCT u.id FROM users u "
            "JOIN user_topics ut ON ut.user_id = u.id "
            "LEFT JOIN user_gemeenten ug ON ug.user_id = u.id "
            "WHERE ut.topic = ? AND ut.level = 'pers' "
            "AND (ug.gemeente = ? OR ug.gemeente IS NULL "
            "     OR NOT EXISTS (SELECT 1 FROM user_gemeenten WHERE user_id = u.id))",
            (topic, gemeente)
        ).fetchall()
    else:
        # Bestuurlijk-alerts gaan naar gebruikers met dit topic op 'bestuurlijk'
        users = conn.execute(
            "SELECT DISTINCT u.id FROM users u "
            "JOIN user_topics ut ON ut.user_id = u.id "
            "LEFT JOIN user_gemeenten ug ON ug.user_id = u.id "
            "WHERE ut.topic = ? AND ut.level = 'bestuurlijk' "
            "AND (ug.gemeente = ? OR ug.gemeente IS NULL "
            "     OR NOT EXISTS (SELECT 1 FROM user_gemeenten WHERE user_id = u.id))",
            (topic, gemeente)
        ).fetchall()

    for u in users:
        conn.execute(
            "INSERT OR IGNORE INTO user_alerts (user_id, alert_id) VALUES (?,?)",
            (u['id'], alert_id)
        )

    conn.commit()
    conn.close()
    return alert_id


def get_user_alerts(user_id, limit=50, unread_only=False):
    conn = get_users_db()
    where = "AND ua.read = 0" if unread_only else ""
    rows = conn.execute(f"""
        SELECT a.*, ua.read
        FROM user_alerts ua
        JOIN alerts a ON a.id = ua.alert_id
        WHERE ua.user_id = ? {where}
        ORDER BY a.created_at DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_alert_read(user_id, alert_id):
    conn = get_users_db()
    conn.execute(
        "UPDATE user_alerts SET read = 1 WHERE user_id = ? AND alert_id = ?",
        (user_id, alert_id)
    )
    conn.commit()
    conn.close()


# ── Transcripts ────────────────────────────────────────────────────────────

def add_transcript_chunk(meeting_id, text, speaker=None,
                         start_time=None, end_time=None):
    conn = get_users_db()
    conn.execute(
        "INSERT INTO transcript_chunks (meeting_id, start_time, end_time, speaker, text) "
        "VALUES (?,?,?,?,?)",
        (meeting_id, start_time, end_time, speaker, text)
    )
    conn.commit()
    conn.close()


def get_transcript(meeting_id, since_time=None):
    conn = get_users_db()
    if since_time is not None:
        rows = conn.execute(
            "SELECT * FROM transcript_chunks WHERE meeting_id = ? "
            "AND start_time >= ? ORDER BY start_time",
            (meeting_id, since_time)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM transcript_chunks WHERE meeting_id = ? "
            "ORDER BY start_time",
            (meeting_id,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_recent_text(meeting_id, last_n_minutes=5):
    """Haal de tekst van de laatste N minuten op voor analyse."""
    conn = get_users_db()
    rows = conn.execute(
        "SELECT text, speaker FROM transcript_chunks "
        "WHERE meeting_id = ? "
        "ORDER BY start_time DESC LIMIT ?",
        (meeting_id, last_n_minutes * 2)  # ~2 chunks per minuut
    ).fetchall()
    conn.close()
    rows = list(reversed(rows))
    return ' '.join(r['text'] for r in rows)
