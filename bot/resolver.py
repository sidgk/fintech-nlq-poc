"""
resolver.py — the 'AI' step.

Flow:
  1. Pull the semantic layer's catalog from Cube's /meta endpoint.
  2. Ask Claude to map the question -> a Cube QUERY SPEC (JSON), constrained
     to members that actually exist. Claude never writes SQL.
  3. Send that spec to Cube's /load endpoint. Cube compiles it to SQL,
     runs it on Postgres, and returns the numbers.
"""

import os
import json
import time
import requests

CUBE_API_URL = os.environ.get("CUBE_API_URL", "http://localhost:4000/cubejs-api/v1")

# ── LLM provider ──────────────────────────────────────────────────────────
# The resolver is a constrained *translator*, not a SQL writer — any capable
# model with JSON output works, so the trust story (LLM can only pick defined
# members) is identical regardless of vendor. We default to Gemini 2.5 Flash
# because Google AI Studio's free tier (1500 req/day, 15 req/min, no credit
# card) is plenty for a POC. Set LLM_PROVIDER=anthropic to use Claude instead.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()

# Anthropic (Claude) — used when LLM_PROVIDER=anthropic. Needs ANTHROPIC_API_KEY.
ANTHROPIC_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")

# Gemini (Google AI Studio free tier) — default. Needs GEMINI_API_KEY from
# https://aistudio.google.com/apikey . Same REST recipe KA26's Heli uses.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def fetch_catalog() -> str:
    """Read the semantic layer and render a compact catalog for the prompt."""
    resp = requests.get(f"{CUBE_API_URL}/meta", timeout=30)
    resp.raise_for_status()
    cubes = resp.json().get("cubes", [])

    lines = []
    for c in cubes:
        # Focus the model on the curated view; skip raw cubes if a view exists.
        kind = "VIEW" if c.get("type") == "view" else "CUBE"
        lines.append(f"\n## {kind}: {c['name']} — {c.get('title','')}")
        if c.get("description"):
            lines.append(f"   {c['description']}")
        for m in c.get("measures", []):
            lines.append(f"   [measure]   {m['name']} :: {m.get('shortTitle','')} — {m.get('description','')}")
        for d in c.get("dimensions", []):
            lines.append(f"   [dimension] {d['name']} ({d.get('type')}) — {d.get('description','')}")
    return "\n".join(lines)


SYSTEM_TEMPLATE = """You translate business questions into a Cube query spec (JSON).
You DO NOT write SQL. You only choose from the members listed in the catalog below.

CATALOG OF AVAILABLE MEASURES & DIMENSIONS:
{catalog}

Rules:
- Output ONLY a single JSON object, no prose, no markdown fences.
- Use exact member names from the catalog (e.g. "payments_overview.succeeded_count").
- Prefer the VIEW (payments_overview) over raw cubes when it covers the question.
- Shape:
  {{
    "measures": ["..."],
    "dimensions": ["..."],            // optional, for grouping/breakdowns
    "timeDimensions": [{{ "dimension": "...created_at", "dateRange": "last month" }}],  // optional
    "filters": [{{ "member": "...", "operator": "equals", "values": ["card"] }}],       // optional
    "limit": 100
  }}
- dateRange accepts relative strings: "today", "yesterday", "this week",
  "last week", "last 7 days", "last 30 days", "this month", "last month", "this year".
- filter operators: equals, notEquals, contains, gt, gte, lt, lte, set, notSet.
- If the question cannot be answered with the available members, output:
  {{ "error": "short reason" }}
"""


def _strip_to_json(text: str) -> dict:
    """Tolerate stray markdown fences, then parse the JSON object."""
    text = text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def _resolve_with_gemini(system: str, question: str) -> dict:
    """Google AI Studio free tier. JSON mode → no fence stripping needed."""
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY not set. Get a free key at "
            "https://aistudio.google.com/apikey and add it to .env."
        )
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": question}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 1024,
            # Constrain output to valid JSON.
            "responseMimeType": "application/json",
            # Disable "thinking" — this is mechanical translation, not reasoning.
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    url = f"{GEMINI_BASE}/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    resp = requests.post(url, json=body, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
    return _strip_to_json(text)


def _resolve_with_anthropic(system: str, question: str) -> dict:
    from anthropic import Anthropic  # lazy — only imported on this path
    client = Anthropic()  # reads ANTHROPIC_API_KEY from env
    msg = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": question}],
    )
    text = "".join(b.text for b in msg.content if b.type == "text")
    return _strip_to_json(text)


def question_to_query(question: str, catalog: str) -> dict:
    system = SYSTEM_TEMPLATE.format(catalog=catalog)
    if LLM_PROVIDER == "anthropic":
        return _resolve_with_anthropic(system, question)
    return _resolve_with_gemini(system, question)


INTENT_SYSTEM = """You classify how a follow-up analytics request relates to the
PREVIOUS request in the same chat thread. Output ONLY JSON: {"action": "<value>"}.
Allowed values:
- "refine": same question, changed slightly (different units, sorting, a filter,
  a different time window) -> the previous result should be OVERWRITTEN.
- "new_tab": a DIFFERENT question or breakdown -> add it ALONGSIDE the previous one.
- "new_sheet": the user explicitly asks for a NEW / SEPARATE spreadsheet or file.
When unsure, choose "new_tab"."""


def classify_intent(prev_question: str, new_question: str) -> str:
    """Return 'refine' | 'new_tab' | 'new_sheet' for a thread follow-up."""
    user = f'Previous request: "{prev_question}"\nNew request: "{new_question}"'
    try:
        if LLM_PROVIDER == "anthropic":
            out = _resolve_with_anthropic(INTENT_SYSTEM, user)
        else:
            out = _resolve_with_gemini(INTENT_SYSTEM, user)
        action = str(out.get("action", "new_tab")).strip().lower()
        return action if action in ("refine", "new_tab", "new_sheet") else "new_tab"
    except Exception:
        return "new_tab"


def run_query(cube_query: dict) -> dict:
    """POST the spec to Cube. Cube may answer 'Continue wait' while building — poll."""
    for _ in range(10):
        resp = requests.post(
            f"{CUBE_API_URL}/load",
            json={"query": cube_query},
            headers={"Content-Type": "application/json"},
            timeout=60,
        )
        body = resp.json()
        if isinstance(body, dict) and body.get("error") == "Continue wait":
            time.sleep(1)
            continue
        return body
    return {"error": "timed out waiting for Cube"}


def answer_question(question: str) -> dict:
    """End-to-end: returns {'query':..., 'rows':...} or {'error':...}."""
    catalog = fetch_catalog()
    spec = question_to_query(question, catalog)
    if "error" in spec:
        return {"error": spec["error"]}
    result = run_query(spec)
    if "error" in result:
        return {"error": result["error"], "query": spec}
    return {"query": spec, "rows": result.get("data", [])}


if __name__ == "__main__":
    # Quick CLI test without Slack: python resolver.py "how many successful card payments last month?"
    import sys
    q = " ".join(sys.argv[1:]) or "how many successful card payments did we have last month?"
    out = answer_question(q)
    print(json.dumps(out, indent=2, default=str))
