"""
Tiny SQLite store mapping a Slack thread -> the Google Sheet it owns.

One row per thread. Lets follow-up messages in the SAME thread modify the SAME
spreadsheet (overwrite a tab, add a tab, etc.) instead of starting over.
"""

import os
import sqlite3

_DB = os.path.join(os.path.dirname(__file__), "bot_state.db")


def _conn():
    c = sqlite3.connect(_DB)
    c.execute(
        """create table if not exists threads (
               thread_ts      text primary key,
               spreadsheet_id text,
               last_tab       text,
               last_question  text
           )"""
    )
    return c


def get(thread_ts: str):
    with _conn() as c:
        row = c.execute(
            "select spreadsheet_id, last_tab, last_question from threads where thread_ts=?",
            (thread_ts,),
        ).fetchone()
    if not row:
        return None
    return {"spreadsheet_id": row[0], "last_tab": row[1], "last_question": row[2]}


def put(thread_ts: str, spreadsheet_id: str, last_tab: str, last_question: str):
    with _conn() as c:
        c.execute(
            """insert into threads(thread_ts, spreadsheet_id, last_tab, last_question)
               values(?,?,?,?)
               on conflict(thread_ts) do update set
                   spreadsheet_id=excluded.spreadsheet_id,
                   last_tab=excluded.last_tab,
                   last_question=excluded.last_question""",
            (thread_ts, spreadsheet_id, last_tab, last_question),
        )
