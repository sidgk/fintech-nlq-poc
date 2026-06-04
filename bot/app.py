"""
app.py — Slack front door (Socket Mode, no public URL needed).

Mention the bot or DM it a question:
    @data-bot how many successful card payments last month?
It calls the resolver and replies with the number + the resolved query.
"""

import os
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from resolver import answer_question, classify_intent

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


def export_to_sheet(thread_ts: str, question: str, rows: list) -> str:
    """Create or modify the thread's Google Sheet based on the user's intent.
    Returns a one-line Slack status with the link."""
    import sheets
    import thread_store

    state = thread_store.get(thread_ts)
    if not state:
        # First question in this thread → new spreadsheet.
        sid, url, tab = sheets.create_spreadsheet(question, question, rows)
        thread_store.put(thread_ts, sid, tab, question)
        return f":bar_chart: <{url}|Open in Google Sheets> · tab *{tab}*"

    sid = state["spreadsheet_id"]
    url = sheets.url_for(sid)
    action = classify_intent(state["last_question"], question)

    if action == "new_sheet":
        sid, url, tab = sheets.create_spreadsheet(question, question, rows)
        thread_store.put(thread_ts, sid, tab, question)
        return f":bar_chart: New sheet → <{url}|open> · tab *{tab}*"
    if action == "refine":
        tab = sheets.replace_tab(sid, state["last_tab"], rows)
        thread_store.put(thread_ts, sid, tab, question)
        return f":pencil2: Updated tab *{tab}* → <{url}|open>"
    # default: a different question → new tab in the same spreadsheet
    tab = sheets.add_tab(sid, question, rows)
    thread_store.put(thread_ts, sid, tab, question)
    return f":heavy_plus_sign: Added tab *{tab}* → <{url}|open>"


def handle_question(question: str, thread_ts: str) -> str:
    out = answer_question(question)
    reply = format_reply(question, out)
    if _sheets_ready() and out.get("rows"):
        try:
            reply += "\n\n" + export_to_sheet(thread_ts, question, out["rows"])
        except Exception as e:  # never let a Sheets hiccup break the answer
            reply += f"\n\n_(Google Sheet export skipped: {e})_"
    return reply


@app.event("app_mention")
def on_mention(event, say):
    text = event.get("text", "")
    # strip the leading <@BOTID> mention
    question = text.split(">", 1)[-1].strip() if ">" in text else text
    thread_ts = event.get("thread_ts") or event.get("ts")
    say(text=handle_question(question, thread_ts), thread_ts=thread_ts)


@app.event("message")
def on_message(event, say):
    # ignore the bot's own messages, edits, joins, etc.
    if event.get("bot_id") or event.get("subtype"):
        return
    text = event.get("text", "") or ""
    # if the message @mentions the bot, let on_mention handle it (avoid double reply)
    if BOT_USER_ID and f"<@{BOT_USER_ID}>" in text:
        return

    if event.get("channel_type") == "im":
        # DM: thread by the message itself
        thread_ts = event.get("thread_ts") or event.get("ts")
        say(text=handle_question(text, thread_ts))
        return

    # Mention-free follow-up inside a channel thread the bot already owns.
    # (Requires the `message.channels` scope/event to be subscribed in Slack.)
    thread_ts = event.get("thread_ts")
    if thread_ts:
        import thread_store
        if thread_store.get(thread_ts):
            say(text=handle_question(text, thread_ts), thread_ts=thread_ts)


if __name__ == "__main__":
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
