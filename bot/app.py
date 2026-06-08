"""
app.py — Slack front door (Socket Mode, no public URL needed).

Mention the bot or DM it a question:
    @data-bot how many successful card payments last month?
It calls the resolver and replies with the number + the resolved query.
"""

import os
import re
import sys
import time
import threading

import querylog

from slack_bolt import App
# Prefer the websocket-client-based handler (ping/pong heartbeat + auto-reconnect)
# over the fragile pure-Python builtin one that went silently deaf on BrokenPipe.
try:
    from slack_bolt.adapter.socket_mode.websocket_client import SocketModeHandler
except Exception:
    from slack_bolt.adapter.socket_mode import SocketModeHandler

from resolver import answer_question, classify_intent, is_lineage, is_certified

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


def _model_name() -> str:
    p = os.environ.get("LLM_PROVIDER", "").lower()
    if p == "ollama":
        return os.environ.get("OLLAMA_MODEL", "ollama")
    if p == "anthropic":
        return os.environ.get("CLAUDE_MODEL", "claude")
    return os.environ.get("GEMINI_MODEL", "gemini")


def _certified_only() -> bool:
    return os.environ.get("CERTIFIED_ONLY", "").lower() in ("1", "true", "yes")


def _cert_check(spec):
    """(block_note, caveat). In CERTIFIED_ONLY mode an uncertified metric is blocked;
    otherwise a caveat is appended to the answer. Certified metrics → (None, None)."""
    uncert = [m for m in (spec or {}).get("measures", []) or [] if not is_certified(m)]
    if not uncert:
        return None, None
    names = ", ".join(m.split(".")[-1] for m in uncert)
    if _certified_only():
        return (f":lock: *{names}* is not **certified** for executive reporting yet — "
                f"I'm holding the number back. Ask the data team to certify it."), None
    return None, (f"\n\n:warning: _*{names}* is **experimental** — not yet certified for "
                  f"exec reporting; use with caution._")


def _finish_uncached(question, context_key, out, _log):
    """Format + log a freshly-computed result. Returns (text, out)."""
    if out.get("chat"):                           # greeting / small talk / unclear
        _log("chat", None, 0, out["chat"], False)
        return out["chat"], None
    if out.get("clarify"):                         # ambiguous → ask, don't guess
        _log("clarify", None, 0, out["clarify"], False)
        return out["clarify"], {"clarify": True}
    if "error" in out and "rows" not in out:
        ans = format_reply(question, out)
        _log("error", out.get("query"), 0, ans, False)
        return ans, out
    block, caveat = _cert_check(out.get("query"))   # certification gate
    if block:
        _log("blocked", out.get("query"), 0, block, False)
        return block, None
    if out.get("query"):
        _LAST_QUERY[context_key] = out["query"]   # for a follow-up lineage ask
        _LAST_QUESTION[context_key] = question
    ans = format_reply(question, out)
    if caveat:
        ans += caveat
    out["log_id"] = _log("data", out.get("query"), len(out.get("rows", [])), ans, False)
    return ans, out


def compute_answer(question: str, context_key: str, meta: dict = None, clarify_ok: bool = True):
    """FAST path: returns (reply_text, out). Caches standalone data questions
    (compute once → serve everyone) and logs every interaction. Does NOT touch
    Google Sheets — numbers reach the user in seconds; the sheet follows."""
    meta = meta or {}
    t0 = time.time()

    def _log(kind, spec, rc, answer, cache_hit):
        return querylog.log(meta.get("user"), meta.get("channel"), question, kind, spec,
                            rc, answer, cache_hit, int((time.time() - t0) * 1000), _model_name())

    def _from_cache(c):
        block, _ = _cert_check(c.get("spec"))      # strict-mode block on cache hits too
        if block:
            _log("blocked", c.get("spec"), 0, block, True)
            return block, None
        _LAST_QUERY[context_key] = c["spec"]
        _LAST_QUESTION[context_key] = question
        log_id = _log("data", c["spec"], c["row_count"], c["answer"], True)
        return c["answer"], {"query": c["spec"], "rows": c["rows"], "cached": True, "log_id": log_id}

    # Lineage — depends on thread state, not cacheable.
    if _wants_lineage(question, context_key):
        try:
            import lineage
            ans = lineage.explain(_LAST_QUERY[context_key])
        except Exception as e:
            ans = f":warning: couldn't build the lineage: {e}"
        _log("lineage", None, 0, ans, False)
        return ans, None

    # Contextual follow-up ("the same per quarter") — depends on thread, not cacheable.
    prev = _LAST_QUESTION.get(context_key)
    if prev and any(w in f" {question.lower()} " for w in _REFERENTIAL):
        q_for_resolver = (
            f'Earlier request: "{prev}". Follow-up: "{question}". Answer the '
            f"follow-up; where it says the same/those/it/again, reuse the earlier "
            f"request's measures and dimensions and only apply the new change."
        )
        return _finish_uncached(question, context_key,
                                answer_question(q_for_resolver, clarify_ok), _log)

    # Standalone question — cacheable. Serve from cache if fresh.
    cached = querylog.cache_get(question)
    if cached and cached.get("spec"):
        return _from_cache(cached)

    # Single-flight: only the first of N identical concurrent questions computes;
    # the rest wait and read the cache → the DB is hit once, not N times.
    leader, ev = querylog.single_flight_begin(question)
    if not leader:
        ev.wait(timeout=30)
        cached = querylog.cache_get(question)
        if cached and cached.get("spec"):
            return _from_cache(cached)
    try:
        out = answer_question(question, clarify_ok)
        text, ret = _finish_uncached(question, context_key, out, _log)
        if ret is not None and "error" not in out and ret.get("rows") is not None:
            querylog.cache_put(question, text, ret.get("query"), ret.get("rows", []))
        return text, ret
    finally:
        querylog.single_flight_end(question)


def handle_question(question: str, thread_ts: str) -> str:
    """Combined answer + sheet (used by tests/CLI). The bot uses the faster
    two-phase path in _respond_async instead."""
    text, out = compute_answer(question, thread_ts)
    if out and _sheets_ready() and out.get("rows"):
        try:
            text += "\n\n" + export_to_sheet(thread_ts, question, out["rows"])
        except Exception as e:
            text += f"\n\n_(Google Sheet export skipped: {e})_"
    return text


# ── Follow-up menu (numbered options — works WITHOUT Slack interactivity) ─────
_PENDING_MENU = {}      # context_key -> {question, answer, log_id, spec}
_AWAITING_FEEDBACK = {}  # context_key -> {question, answer, spec, log_id}; next msg = feedback
_AWAITING_CLARIFY = {}   # context_key -> {question}; next msg answers a clarifying question
_MSG_TO_LOG = {}        # answer message ts -> query_log id (for 👍/👎 emoji reactions)
_MENU_LABEL = {1: "sheet_numbers", 2: "sheet_chart", 3: "lineage", 4: "feedback"}


def _menu_footer() -> str:
    lines = ["", "_Reply with a number:_"]
    if _sheets_ready():
        lines.append("`1` 📊 Numbers in Google Sheets")
        lines.append("`2` 📈 Chart in Google Sheets")
    lines.append("`3` 🧬 How was this calculated?")
    lines.append("`4` 💬 Leave feedback")
    return "\n".join(lines)


def _parse_choice(text: str):
    """Return 1-4 if the message is just a menu pick ('2', 'option 2', '2.')."""
    m = re.match(r"^\s*(option\s*)?([1-4])\s*[.)]?\s*$", (text or "").lower())
    return int(m.group(2)) if m else None


def _feedback_sentiment(text: str) -> str:
    """Light classification so the acknowledgment fits the feedback (and for analytics)."""
    t = (text or "").lower()
    pos = ("good", "great", "perfect", "thank", "helpful", "nice", "love", "correct",
           "accurate", "satisfied", "excellent", "awesome", "works", "spot on",
           "exactly", "well done", "clear", "👍")
    neg = ("wrong", "incorrect", "too high", "too low", "not right", "bad", "error",
           "issue", "problem", "doesn't", "does not", "confusing", "missing", "fix",
           "broken", "inaccurate", "not correct", "should be", "mistake", "👎")
    has_pos, has_neg = any(w in t for w in pos), any(w in t for w in neg)
    if has_pos and has_neg:
        return "mixed"
    if has_neg:
        return "negative"
    if has_pos:
        return "positive"
    return "neutral"


def _get_or_create_sheet(question: str, rows: list, want_chart: bool) -> str:
    """Reuse the cached Google Sheet for this question (+ chart variant), or build
    it once and share it. Same question → same sheet, for everyone."""
    cache_key = question + ("::chart" if want_chart else "::plain")
    cached = querylog.sheet_cache_get(cache_key)
    if cached:
        return cached["url"]
    import sheets
    sid, url, tab = sheets.create_spreadsheet(question, question, rows)
    if want_chart:
        try:
            sheets.add_chart(sid, tab, with_trend=_wants_trend(question))
        except Exception:
            pass
    try:
        sheets.share_anyone(sid)                   # any user who opens the link can view
    except Exception:
        pass
    querylog.sheet_cache_put(cache_key, sid, url)
    return url


def _menu_sheet(question, want_chart, say, reply_thread_ts):
    try:
        say(text=":bar_chart: one sec — preparing your Google Sheet…", thread_ts=reply_thread_ts)
        cached = querylog.cache_get(question)
        rows = cached["rows"] if (cached and cached.get("rows")) else \
            answer_question(question).get("rows", [])
        if not rows:
            say(text=":warning: there's no data to put in a sheet for that one.",
                thread_ts=reply_thread_ts)
            return
        url = _get_or_create_sheet(question, rows, want_chart)
        say(text=f":bar_chart: <{url}|Open in Google Sheet>", thread_ts=reply_thread_ts)
    except Exception as e:
        say(text=f":warning: couldn't build the sheet: {e}", thread_ts=reply_thread_ts)


def _handle_menu(choice, pending, say, reply_thread_ts, meta=None, context_key=None):
    """Run the action the user selected — and LOG the pick (so analytics show the
    most-adopted option per question)."""
    question = pending.get("question", "")
    m = meta or {}
    try:
        querylog.log(m.get("user"), m.get("channel"), question,
                     f"menu:{_MENU_LABEL.get(choice, choice)}", pending.get("spec"),
                     0, f"option {choice}", False, 0, _model_name())
    except Exception:
        pass

    if choice in (1, 2):                                   # 1 = numbers, 2 = chart
        threading.Thread(target=_menu_sheet,
                         args=(question, choice == 2, say, reply_thread_ts), daemon=True).start()
    elif choice == 3:                                      # lineage
        spec = pending.get("spec")
        if not spec:
            say(text="I don't have a calculation to explain for that.", thread_ts=reply_thread_ts)
            return
        try:
            import lineage
            say(text=lineage.explain(spec), thread_ts=reply_thread_ts)
        except Exception as e:
            say(text=f":warning: couldn't build the lineage: {e}", thread_ts=reply_thread_ts)
    elif choice == 4:                                      # leave free-text feedback
        _AWAITING_FEEDBACK[context_key] = {
            "question": question, "answer": pending.get("answer"),
            "spec": pending.get("spec"), "log_id": pending.get("log_id")}
        say(text="💬 Sure — what's your feedback on this answer? Just type it in your next message.",
            thread_ts=reply_thread_ts)


def _route(text, context_key, say, reply_thread_ts, meta):
    """Clarification reply → pending feedback → menu pick → else a new question."""
    m = meta or {}

    # 0) Were we waiting for the user to answer a clarifying question?
    clar = _AWAITING_CLARIFY.pop(context_key, None)
    if clar is not None:
        combined = f'{clar["question"]} — clarified as: {text}'
        _respond_async(say, combined, context_key, reply_thread_ts, meta=meta, clarify_ok=False)
        return

    # 1) Were we waiting for free-text feedback in this conversation?
    fb = _AWAITING_FEEDBACK.pop(context_key, None)
    if fb is not None:
        sentiment = _feedback_sentiment(text)
        querylog.add_feedback_text(m.get("user"), m.get("channel"), fb.get("question"),
                                   fb.get("answer"), fb.get("spec"), fb.get("log_id"),
                                   text, sentiment)
        if sentiment == "positive":
            ack = "🙏 Thank you! Glad it was helpful — noted. 😊"
        elif sentiment in ("negative", "mixed"):
            ack = "🙏 Thanks for flagging that — logged, and we'll work on it."
        else:
            ack = "🙏 Thanks! I've logged your feedback."
        say(text=ack, thread_ts=reply_thread_ts)
        return

    # 2) A numbered menu pick for the last answer?
    choice = _parse_choice(text)
    pending = _PENDING_MENU.get(context_key)
    if choice and pending:
        _handle_menu(choice, pending, say, reply_thread_ts, meta, context_key)
        return

    # 3) A brand-new question.
    _respond_async(say, text, context_key, reply_thread_ts, meta=meta)


def _respond_async(say, question: str, context_key: str, reply_thread_ts: str = None,
                   waiting: str = None, meta: dict = None, clarify_ok: bool = True):
    """Answer in a background thread (instant ack). `context_key` keys the
    per-conversation memory + the Google Sheet. `reply_thread_ts` is where the
    reply is shown: None = top level (DMs, so replies aren't hidden in threads);
    a ts = threaded (channel mentions)."""
    if waiting is None:
        waiting = ":wave: Hang on, I'm working on it…"

    def work():
        placeholder = None
        try:
            placeholder = say(text=waiting, thread_ts=reply_thread_ts)
        except Exception:
            pass
        ch = placeholder.get("channel") if placeholder else None
        ts = placeholder.get("ts") if placeholder else None

        def show(t):
            try:
                if ch and ts:
                    app.client.chat_update(channel=ch, ts=ts, text=t)
                else:
                    say(text=t, thread_ts=reply_thread_ts)
            except Exception:
                try:
                    say(text=t, thread_ts=reply_thread_ts)
                except Exception:
                    pass

        # Compute + show the numbers, then a numbered menu. The Google Sheet is
        # built ONLY if the user picks 1/2 (no wasted work; one sheet per question).
        try:
            text, out = compute_answer(question, context_key, meta, clarify_ok)
        except Exception as e:
            msg = str(e)
            key = os.environ.get("GEMINI_API_KEY", "")
            if key:
                msg = msg.replace(key, "***")     # never echo the API key to Slack
            show(f":warning: sorry, that failed: {msg}")
            return

        if out and out.get("clarify"):
            # ask, don't guess — the user's next message answers the clarification
            _AWAITING_CLARIFY[context_key] = {"question": question}
        elif out and out.get("rows") is not None and "error" not in (out or {}):
            _PENDING_MENU[context_key] = {"question": question,
                                          "answer": text,         # before the footer
                                          "log_id": out.get("log_id"),
                                          "spec": out.get("query")}
            if ts:
                _MSG_TO_LOG[ts] = out.get("log_id")     # map answer msg → log row for 👍/👎
            text = text + "\n" + _menu_footer()
        show(text)

    threading.Thread(target=work, daemon=True).start()


def _strip_mentions(text: str) -> str:
    """Remove <@USERID> mention markup so the resolver sees a clean question."""
    return re.sub(r"<@[A-Z0-9]+>", "", text or "").strip()


@app.event("app_mention")
def on_mention(event, say):
    # Fires for @mentions in CHANNELS (not DMs). Reply threaded under the mention.
    question = _strip_mentions(event.get("text", ""))
    thread_ts = event.get("thread_ts") or event.get("ts")
    meta = {"user": event.get("user"), "channel": event.get("channel")}
    _route(question, thread_ts, say, thread_ts, meta)


@app.event("message")
def on_message(event, say):
    # ignore the bot's own messages, edits, joins, etc.
    if event.get("bot_id") or event.get("subtype"):
        return
    text = _strip_mentions(event.get("text", ""))
    if not text:
        return

    # DMs: ALWAYS handle here (Slack fires no app_mention for DMs). The whole DM
    # is one conversation → reply at TOP LEVEL (visible, not hidden in a thread)
    # and key memory on the DM channel so follow-ups share context.
    if event.get("channel_type") == "im":
        meta = {"user": event.get("user"), "channel": event.get("channel")}
        _route(text, event.get("channel"), say, None, meta)
        return

    # In a CHANNEL: if it @mentions the bot, app_mention already handles it.
    if BOT_USER_ID and f"<@{BOT_USER_ID}>" in (event.get("text") or ""):
        return

    # Mention-free follow-up inside a channel thread the bot already owns.
    thread_ts = event.get("thread_ts")
    if thread_ts:
        import thread_store
        if _PENDING_MENU.get(thread_ts) or thread_store.get(thread_ts):
            meta = {"user": event.get("user"), "channel": event.get("channel")}
            _route(text, thread_ts, say, thread_ts, meta)


@app.event("reaction_added")
def on_reaction(event):
    """Capture 👍/👎 emoji reactions on the bot's answers as feedback.
    (Requires the `reactions:read` scope + the `reaction_added` event subscription.)"""
    log_id = _MSG_TO_LOG.get((event.get("item") or {}).get("ts"))
    if not log_id:
        return
    emoji = event.get("reaction", "")
    if emoji in ("thumbsup", "+1", "white_check_mark", "heavy_check_mark"):
        querylog.set_feedback(log_id, 1)
    elif emoji in ("thumbsdown", "-1"):
        querylog.set_feedback(log_id, -1)


@app.event("reaction_removed")
def on_reaction_removed(event):
    log_id = _MSG_TO_LOG.get((event.get("item") or {}).get("ts"))
    if log_id and event.get("reaction", "") in ("thumbsup", "+1", "thumbsdown", "-1"):
        querylog.set_feedback(log_id, None)             # un-react = clear


def _catchup_missed_dms():
    """On startup, answer any DM the bot missed while it was disconnected. Uses
    only the im:history scope we already have. Naturally idempotent: once we
    reply (in-thread), the message has a reply, so we won't answer it twice."""
    import time as _t
    _t.sleep(6)                                     # let the connection settle
    try:
        cutoff = _t.time() - 2 * 3600               # only the last 2 hours
        ims = app.client.conversations_list(types="im", limit=50).get("channels", [])
        for im in ims:
            ch = im.get("id")
            try:
                msgs = app.client.conversations_history(channel=ch, limit=5).get("messages", [])
            except Exception:
                continue
            if not msgs:
                continue
            top = msgs[0]                            # newest message in this DM
            if top.get("bot_id") or top.get("user") == BOT_USER_ID:
                continue                             # bot spoke last → nothing pending
            if top.get("reply_count"):
                continue                             # already has a reply → answered
            if float(top.get("ts", 0)) < cutoff:
                continue                             # too old to bother
            text = _strip_mentions(top.get("text", ""))
            if not text:
                continue

            # Post top-level into this DM (param MUST be `text=` — that's how
            # _respond_async calls say()). Bug before: it was `t`, so nothing posted.
            def say(text=None, thread_ts=None, _ch=ch):
                return app.client.chat_postMessage(channel=_ch, text=text, thread_ts=thread_ts)

            print(f"[catchup] answering missed DM: {text[:50]!r}", flush=True)
            _respond_async(say, text, context_key=ch, reply_thread_ts=None,
                           waiting=":wave: Sorry for the delay — catching up on this now…",
                           meta={"user": top.get("user"), "channel": ch})
    except Exception as e:
        print(f"[catchup] error: {e}", flush=True)


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
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])

    # Watchdog: if the Slack WebSocket stays dead (we saw BrokenPipeErrors that
    # left the bot alive but deaf), exit so the keepalive wrapper restarts us
    # with a fresh connection. Only acts on a clearly-disconnected socket.
    def _watchdog():
        import time as _t
        bad = 0
        while True:
            _t.sleep(20)
            try:
                connected = handler.client is not None and handler.client.is_connected()
            except Exception:
                connected = True            # can't tell → don't restart (no false positives)
            bad = bad + 1 if not connected else 0
            if bad >= 4:                    # ~80s continuously down
                print("[watchdog] Slack socket down ~80s — exiting for restart", flush=True)
                os._exit(1)

    threading.Thread(target=_watchdog, daemon=True).start()
    # Catch up on any DM missed while we were disconnected/restarting.
    threading.Thread(target=_catchup_missed_dms, daemon=True).start()
    handler.start()
