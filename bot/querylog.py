"""
querylog.py — persistent log of every Q&A + an answer cache.

Two jobs:
1. LOG every interaction (question, resolved spec, answer, latency, cache hit…)
   → powers analytics: what people ask, what fails, what to improve.
2. CACHE answers so an identical question is computed ONCE and served to everyone
   → 10 people asking the same thing = 1 LLM call + 1 database hit, not 10.

Storage: SQLite file (bot_state.db, gitignored). In production this is the same
schema in Postgres / the warehouse. Query it with plain SQL (see analytics.py).
"""

import os
import re
import json
import time
import sqlite3
import datetime
import threading

DB = os.path.join(os.path.dirname(__file__), "bot_state.db")
CACHE_TTL = int(os.environ.get("CACHE_TTL_SECONDS", "1800"))   # 30 min default

# Single-flight: if N identical questions arrive at once, only the first computes;
# the rest wait and read the cache. Prevents a thundering herd hitting the DB.
_inflight = {}
_inflight_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(DB, timeout=15)
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS query_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL, created_at TEXT, user_id TEXT, channel TEXT,
            question TEXT, normalized TEXT, kind TEXT, query_spec TEXT,
            row_count INTEGER, answer TEXT, cache_hit INTEGER,
            latency_ms INTEGER, model TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS answer_cache(
            normalized TEXT PRIMARY KEY, question TEXT, answer TEXT,
            query_spec TEXT, rows_json TEXT, row_count INTEGER, created_ts REAL)""")
        # one Google Sheet per question, reused across users (built on demand)
        c.execute("""CREATE TABLE IF NOT EXISTS sheet_cache(
            normalized TEXT PRIMARY KEY, question TEXT, spreadsheet_id TEXT,
            url TEXT, created_ts REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_log_norm ON query_log(normalized)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_log_ts ON query_log(ts)")
        # migration: add feedback column to existing DBs (1=👍, -1=👎, NULL=none)
        try:
            c.execute("ALTER TABLE query_log ADD COLUMN feedback INTEGER")
        except Exception:
            pass


def normalize(q: str) -> str:
    """Cache key: lowercase, collapse whitespace, drop trailing punctuation — so
    'Revenue by category' and 'revenue by  category?' share one cache entry."""
    return re.sub(r"\s+", " ", (q or "").strip().lower()).strip(" ?.!")


def cache_get(question: str):
    norm = normalize(question)
    try:
        with _conn() as c:
            r = c.execute("SELECT answer, query_spec, rows_json, row_count, created_ts "
                          "FROM answer_cache WHERE normalized=?", (norm,)).fetchone()
    except Exception:
        return None
    if not r:
        return None
    answer, spec, rows_json, rc, created = r
    if time.time() - created > CACHE_TTL:
        return None                                   # stale → recompute
    return {"answer": answer,
            "spec": json.loads(spec) if spec else None,
            "rows": json.loads(rows_json) if rows_json else [],
            "row_count": rc}


def cache_put(question, answer, spec, rows):
    norm = normalize(question)
    try:
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO answer_cache"
                      "(normalized, question, answer, query_spec, rows_json, row_count, created_ts) "
                      "VALUES(?,?,?,?,?,?,?)",
                      (norm, question, answer,
                       json.dumps(spec) if spec else None,
                       json.dumps(rows) if rows else None,
                       len(rows or []), time.time()))
    except Exception:
        pass


def log(user_id, channel, question, kind, spec, row_count, answer, cache_hit, latency_ms, model):
    """Insert a log row; returns its id (used to attach 👍/👎 feedback)."""
    try:
        with _conn() as c:
            cur = c.execute("""INSERT INTO query_log
                (ts, created_at, user_id, channel, question, normalized, kind,
                 query_spec, row_count, answer, cache_hit, latency_ms, model)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (time.time(), datetime.datetime.now().isoformat(timespec="seconds"),
                 user_id, channel, question, normalize(question), kind,
                 json.dumps(spec) if spec else None, row_count or 0,
                 (answer or "")[:4000], int(bool(cache_hit)), latency_ms, model))
            return cur.lastrowid
    except Exception:
        return None


def set_feedback(log_id, value):
    """value: 1 for 👍, -1 for 👎."""
    try:
        with _conn() as c:
            c.execute("UPDATE query_log SET feedback=? WHERE id=?", (value, log_id))
    except Exception:
        pass


def sheet_cache_get(question):
    """Return the existing Google Sheet for this question (if fresh), so the same
    question reuses one sheet across users instead of creating a new one each time."""
    norm = normalize(question)
    try:
        with _conn() as c:
            r = c.execute("SELECT spreadsheet_id, url, created_ts FROM sheet_cache "
                          "WHERE normalized=?", (norm,)).fetchone()
    except Exception:
        return None
    if not r:
        return None
    sid, url, created = r
    if time.time() - created > CACHE_TTL:
        return None
    return {"spreadsheet_id": sid, "url": url}


def sheet_cache_put(question, spreadsheet_id, url):
    norm = normalize(question)
    try:
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO sheet_cache"
                      "(normalized, question, spreadsheet_id, url, created_ts) VALUES(?,?,?,?,?)",
                      (norm, question, spreadsheet_id, url, time.time()))
    except Exception:
        pass


def single_flight_begin(question):
    """Return (is_leader, event). Non-leaders wait on the event, then re-read cache."""
    norm = normalize(question)
    with _inflight_lock:
        ev = _inflight.get(norm)
        if ev is None:
            _inflight[norm] = threading.Event()
            return True, _inflight[norm]
        return False, ev


def single_flight_end(question):
    norm = normalize(question)
    with _inflight_lock:
        ev = _inflight.pop(norm, None)
    if ev:
        ev.set()


init()
