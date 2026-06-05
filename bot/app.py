"""
app.py — Slack front door (Socket Mode, no public URL needed).

Mention the bot or DM it a question:
    @data-bot how many successful card payments last month?
It calls the resolver and replies with the number + the resolved query.
"""

import os
import re
import sys
import threading

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from resolver import answer_question, classify_intent, is_lineage

app = App(token=os.environ["SLACK_BOT_TOKEN"])

# Google Sheets export is optional: ON only if SHEETS_ENABLED=true AND a Google
# OAuth token exists (see bot/google_auth.py). Until then the bot replies with
# the inline table only — no Google dependency needed to run.
SHEETS_ENABLED = os.environ.get("SHEETS_ENABLED", "false").lower() == "true"

# The bot's own user id, so the generic message handler can skip @mentions
# (handled by app_mention) and avoid answering twice.
try:
    BOT_USER_ID = app.client.auth_test().get("user_id")
except Exception:
    BOT_USER_ID = None


@app.middleware
def _log_every_request(body, next):
    """Print every Socket Mode request so we can confirm events actually arrive."""
    try:
        ev = (body or {}).get("event", {}) or {}
        print(f"[recv] event={ev.get('type')} ch={ev.get('channel_type')} "
              f"text={(ev.get('text') or '')[:60]!r}", file=sys.stderr, flush=True)
    except Exception:
        pass
    return next()


def _sheets_ready() -> bool:
    if not SHEETS_ENABLED:
        return False
    try:
        import google_auth
        return google_auth.is_ready()
    except Exception:
        return False


def _is_measure(name: str) -> bool:
    """A column is a number-to-format (vs a text dimension) based on its name."""
    return any(t in name for t in ("amount", "revenue", "value", "volume",
                                   "rate", "percent", "count", "avg", "average"))


def _fmt(key: str, val) -> str:
    """Pretty-print one cell: €1,234.56 / 79.6% / 12,345 / plain text."""
    name = key.split(".")[-1].lower()
    if val is None:
        return ""
    try:
        num = float(val)
    except (TypeError, ValueError):
        return str(val)                      # a dimension (e.g. 'travel')
    if any(t in name for t in ("amount", "revenue", "value", "volume", "avg", "average")):
        return f"€{num:,.2f}"
    if "rate" in name or "percent" in name:
        return f"{num:,.1f}%"
    if "count" in name:
        return f"{int(round(num)):,}"
    return f"{int(num):,}" if num == int(num) else f"{num:,.2f}"


def _table(rows: list) -> str:
    headers = list(rows[0].keys())
    short = [h.split(".")[-1] for h in headers]
    data = [[_fmt(h, r.get(h)) for h in headers] for r in rows[:20]]
    widths = [max(len(short[i]), *(len(row[i]) for row in data)) for i in range(len(headers))]
    rjust = [_is_measure(s.lower()) for s in short]   # right-align numbers

    def line(cells):
        return " | ".join(
            (c.rjust(widths[i]) if rjust[i] else c.ljust(widths[i]))
            for i, c in enumerate(cells)
        )

    out = [line(short), "-+-".join("-" * w for w in widths)]
    out += [line(row) for row in data]
    return "```\n" + "\n".join(out) + "\n```"


def format_reply(question: str, out: dict) -> str:
    if "chat" in out:
        return out["chat"]
    if "error" in out and "rows" not in out:
        return f":warning: I couldn't answer that with the current metrics.\n> {out['error']}"

    rows = out["rows"]
    if not rows:
        return "No data matched that question."

    # Single number answer
    if len(rows) == 1 and len(rows[0]) == 1:
        (k, v), = rows[0].items()
        body = f"*{_fmt(k, v)}*"
    else:
        body = _table(rows)

    spec = out.get("query", {})
    return f"{body}\n\n_resolved via semantic layer:_ `{spec}`"


def _wants_chart(q: str) -> bool:
    q = q.lower()
    return any(w in q for w in ("chart", "graph", "plot", "bar", "column", "visual",
                                "trend", "pictorial", "picture", "diagram", "draw",
                                "pie", "histogram"))


def _wants_trend(q: str) -> bool:
    q = q.lower()
    return any(w in q for w in ("trend", "trendline", "regression", "line"))


def export_to_sheet(thread_ts: str, question: str, rows: list) -> str:
    """Create or modify the thread's Google Sheet based on the user's intent,
    optionally drawing a chart. Returns a one-line Slack status with the link."""
    import sheets
    import thread_store

    state = thread_store.get(thread_ts)
    if not state:
        sid, url, tab = sheets.create_spreadsheet(question, question, rows)
        prefix, verb = ":bar_chart:", f"<{url}|Open in Google Sheets> · tab *{tab}*"
    else:
        sid = state["spreadsheet_id"]
        url = sheets.url_for(sid)
        action = classify_intent(state["last_question"], question)
        if action == "new_sheet":
            sid, url, tab = sheets.create_spreadsheet(question, question, rows)
            prefix, verb = ":bar_chart:", f"New sheet → <{url}|open> · tab *{tab}*"
        elif action == "refine":
            tab = sheets.replace_tab(sid, state["last_tab"], rows)
            prefix, verb = ":pencil2:", f"Updated tab *{tab}* → <{url}|open>"
        else:  # new_tab
            tab = sheets.add_tab(sid, question, rows)
            prefix, verb = ":heavy_plus_sign:", f"Added tab *{tab}* → <{url}|open>"

    thread_store.put(thread_ts, sid, tab, question)

    # Optional chart (and trend line) when the user asks for one.
    if _wants_chart(question):
        try:
            if sheets.add_chart(sid, tab, with_trend=_wants_trend(question)):
                verb += " · 📊 chart" + (" + trend line" if _wants_trend(question) else "")
        except Exception:
            pass

    return f"{prefix} {verb}"


# Remember the last query spec + question per thread, so "how was this
# calculated?" can explain it and "aggregate the same per quarter" has context.
_LAST_QUERY = {}
_LAST_QUESTION = {}

# Words that signal the message refers back to the previous question.
_REFERENTIAL = ("the same", "same ", "those", "these instead", "again", "instead",
                "as well", "redo", "do it", "do the same", " it ", "that one")


def _wants_lineage(text: str, thread_ts: str) -> bool:
    """Detect a 'how did you get this number?' question about the thread's last
    metric. Keyword fast-path first, then an LLM intent check for phrasings the
    keywords miss (the user won't always say 'lineage')."""
    if not _LAST_QUERY.get(thread_ts):
        return False                          # nothing to explain yet
    t = text.lower()
    keywords = (
        "how was this calculated", "how was it calculated", "how is this calculated",
        "how did you calculate", "how did you get this", "how did you get these",
        "how did you arrive", "how did you end up", "how did you come up",
        "how'd you get", "where does this come from", "where do these come from",
        "where did this come from", "where did these come from", "lineage",
        "show me the source", "show the source", "how was this derived",
        "how were these", "calculation flow", "how this metric", "trace this",
        "these numbers", "this number", "how did you compute", "what's the source",
    )
    if any(p in t for p in keywords):
        return True
    # LLM fallback only when the message hints at a "how/why/where" meta-question
    if any(w in t for w in ("how", "why", "where", "explain", "calculat",
                            "deriv", "come", "source", "number", "arrive", "figure")):
        return is_lineage(text)
    return False


def handle_question(question: str, thread_ts: str) -> str:
    # Lineage question about the thread's last metric — explain, don't re-query.
    if _wants_lineage(question, thread_ts):
        try:
            import lineage
            return lineage.explain(_LAST_QUERY[thread_ts])
        except Exception as e:
            return f":warning: couldn't build the lineage: {e}"

    # Contextual follow-up: if this references the previous question
    # ("the same per quarter", "those again"), give the resolver that context.
    q_for_resolver = question
    prev = _LAST_QUESTION.get(thread_ts)
    if prev and any(w in f" {question.lower()} " for w in _REFERENTIAL):
        q_for_resolver = (
            f'Earlier request: "{prev}". Follow-up: "{question}". Answer the '
            f"follow-up; where it says the same/those/it/again, reuse the earlier "
            f"request's measures and dimensions and only apply the new change."
        )

    out = answer_question(q_for_resolver)
    if out.get("chat"):                           # greeting / small talk / unclear
        return out["chat"]
    if out.get("query"):
        _LAST_QUERY[thread_ts] = out["query"]     # for a follow-up lineage ask
        _LAST_QUESTION[thread_ts] = question      # store the ORIGINAL phrasing
    reply = format_reply(question, out)
    if _sheets_ready() and out.get("rows"):
        try:
            reply += "\n\n" + export_to_sheet(thread_ts, question, out["rows"])
        except Exception as e:  # never let a Sheets hiccup break the answer
            reply += f"\n\n_(Google Sheet export skipped: {e})_"
    return reply


def _respond_async(say, question: str, thread_ts: str):
    """Answer in a background thread so the Slack event is acked instantly
    (no 3-second-timeout retries). Posts a placeholder, then edits it with the
    result when the (sometimes slow) pipeline + Sheets work finishes."""
    waiting = ":wave: Hang on, I'm working on it…"
    if _wants_chart(question):
        waiting = ":wave: Hang on, I'm working on it — crunching the numbers and drawing your chart… :bar_chart:"

    def work():
        placeholder = None
        try:
            placeholder = say(text=waiting, thread_ts=thread_ts)
        except Exception:
            pass
        try:
            text = handle_question(question, thread_ts)
        except Exception as e:
            msg = str(e)
            key = os.environ.get("GEMINI_API_KEY", "")
            if key:
                msg = msg.replace(key, "***")     # never echo the API key to Slack
            text = f":warning: sorry, that failed: {msg}"
        try:
            if placeholder and placeholder.get("ts"):
                app.client.chat_update(channel=placeholder["channel"],
                                       ts=placeholder["ts"], text=text)
            else:
                say(text=text, thread_ts=thread_ts)
        except Exception:
            say(text=text, thread_ts=thread_ts)

    threading.Thread(target=work, daemon=True).start()


def _strip_mentions(text: str) -> str:
    """Remove <@USERID> mention markup so the resolver sees a clean question."""
    return re.sub(r"<@[A-Z0-9]+>", "", text or "").strip()


@app.event("app_mention")
def on_mention(event, say):
    # Fires for @mentions in CHANNELS (not DMs).
    question = _strip_mentions(event.get("text", ""))
    thread_ts = event.get("thread_ts") or event.get("ts")
    _respond_async(say, question, thread_ts)


@app.event("message")
def on_message(event, say):
    # ignore the bot's own messages, edits, joins, etc.
    if event.get("bot_id") or event.get("subtype"):
        return
    text = _strip_mentions(event.get("text", ""))
    if not text:
        return

    # DMs: ALWAYS handle here. Slack does NOT fire app_mention for DMs, so even a
    # DM that @mentions the bot must be answered by this handler.
    if event.get("channel_type") == "im":
        thread_ts = event.get("thread_ts") or event.get("ts")
        _respond_async(say, text, thread_ts)
        return

    # In a CHANNEL: if it @mentions the bot, app_mention already handles it.
    if BOT_USER_ID and f"<@{BOT_USER_ID}>" in (event.get("text") or ""):
        return

    # Mention-free follow-up inside a channel thread the bot already owns.
    # (Requires the `message.channels` scope/event to be subscribed in Slack.)
    thread_ts = event.get("thread_ts")
    if thread_ts:
        import thread_store
        if thread_store.get(thread_ts):
            _respond_async(say, text, thread_ts)


if __name__ == "__main__":
    # Pre-warm the local model so the FIRST Slack question is fast (with
    # keep_alive=-1 in the resolver, it then stays pinned in RAM).
    if os.environ.get("LLM_PROVIDER", "").lower() == "ollama":
        try:
            import resolver
            resolver._resolve_with_ollama("Reply with an empty JSON object.", "warm up")
            print("Ollama model warmed and pinned.")
        except Exception:
            pass
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
