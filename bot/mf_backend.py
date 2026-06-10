"""
MetricFlow serving backend for the Slack bot.

This is the path that makes Slack answer questions THROUGH the MCP server's
MetricFlow semantic layer (the company-stack pattern) instead of Cube. It:

  1. reads the metric catalog from the MCP server (`mf list metrics`),
  2. asks the configured LLM to turn the question into a MetricFlow spec
     {metrics, group_by, order_by, limit}  (NEVER SQL — only defined metrics),
  3. executes it via the MCP server's `query_rows()` (same code path the AI
     client uses), returning rows the Slack formatter already understands.

Enabled with  SEMANTIC_BACKEND=metricflow  (default stays "cube").
"""
import json
import os
import sys

# Make the repo-root packages (mcp_server) importable regardless of cwd.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from mcp_server import server as mcp          # the SAME handlers the AI client calls

_CATALOG = None


def catalog() -> str:
    """The metric→dimensions menu, straight from MetricFlow via the MCP server."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = mcp.do_list_metrics()
    return _CATALOG


SYSTEM_TEMPLATE = """You translate business questions into a MetricFlow query spec (JSON).
You DO NOT write SQL. You only choose from the metrics and dimensions listed below.

CATALOG (each line is "metric: dimensions it can be grouped by"):
{catalog}

Rules:
- Output ONLY a single JSON object, no prose, no markdown fences.
- Use EXACT metric names from the catalog (e.g. "account_count", "block_rate",
  "payment_success_rate", "revenue").
- group_by uses dimension names EXACTLY as shown, dotted with the entity:
  e.g. "account__industry", "account__risk_scoring", "payment__status",
  "payment__payment_method". For a time series use "metric_time__month"
  (or __day / __week / __year).
- Shape for a data answer:
  {{
    "metrics": ["account_count"],
    "group_by": ["account__industry"],     // optional; omit for a single total
    "order_by": "-account_count",          // optional; "-" prefix = descending
    "limit": 100
  }}
- Pick the metric whose TOPIC matches the question:
    • payments / transactions / revenue / success-rate  → payment_* / revenue metrics
    • accounts / partners / merchants / customers        → account_* / block_rate / *_rate metrics
  Never mix metrics from two unrelated topics in one query.
- ONLY add a metric_time__* group_by when the user EXPLICITLY wants a time series
  ("trend", "over time", "by day/week/month/year", "monthly"). Otherwise omit it.
- If the message is clearly NOT about the data — a greeting, thanks, small talk,
  off-topic ("hi", "how are you", "test") — reply conversationally:
  {{ "chat": "<short, friendly reply>" }}
- ASK, DON'T GUESS: if it IS a data question but genuinely ambiguous in a way that
  changes the number (vague "how are we doing?", a word mapping to multiple
  metrics), ask ONE short clarifying question instead of guessing:
  {{ "clarify": "<one short question offering the options>" }}
  A specific, clear question must be ANSWERED, never clarified. {clarify_suffix}
- If it IS a data question but cannot be answered with the listed metrics:
  {{ "error": "short reason" }}
"""


def _resolve(question: str, clarify_ok: bool) -> dict:
    """Provider-pluggable LLM call, reusing the resolver's dispatch."""
    import resolver
    suffix = "" if clarify_ok else "Do not ask to clarify; make your best single choice."
    system = SYSTEM_TEMPLATE.format(catalog=catalog(), clarify_suffix=suffix)
    return resolver._resolve_json(system, question)


def _sanitize(spec: dict) -> dict:
    """Coerce the LLM spec into clean CLI args."""
    metrics = spec.get("metrics") or spec.get("metric") or []
    if isinstance(metrics, str):
        metrics = [m.strip() for m in metrics.split(",") if m.strip()]
    group_by = spec.get("group_by") or spec.get("dimensions") or []
    if isinstance(group_by, str):
        group_by = [g.strip() for g in group_by.split(",") if g.strip()]
    order_by = spec.get("order_by") or spec.get("order") or ""
    if isinstance(order_by, list):
        order_by = ",".join(order_by)
    limit = spec.get("limit") or 100
    return {
        "metrics": ",".join(metrics),
        "group_by": ",".join(group_by),
        "order_by": order_by or "",
        "limit": int(limit),
    }


def answer_question(question: str, clarify_ok: bool = True) -> dict:
    """Slack-facing entry point. Mirrors resolver.answer_question's contract:
    returns {'chat'} | {'clarify'} | {'query','rows'} | {'error'}."""
    spec = _resolve(question, clarify_ok)
    if "chat" in spec:
        return {"chat": spec["chat"]}
    if "clarify" in spec:
        if clarify_ok:
            return {"clarify": spec["clarify"]}
        return {"error": "I couldn't pin that down — please rephrase with a specific metric."}
    if "error" in spec:
        return {"error": spec["error"]}
    if not (spec.get("metrics") or spec.get("metric")):
        return {"error": "No metric matched that question."}

    args = _sanitize(spec)
    if not args["metrics"]:
        return {"error": "No metric matched that question."}
    result = mcp.query_rows(args["metrics"], args["group_by"], args["order_by"], args["limit"])
    if "error" in result:
        return {"error": result["error"], "query": args}
    return {"query": args, "rows": _scale_rates(result["rows"])}


def _scale_rates(rows: list) -> list:
    """MetricFlow ratio metrics return a 0–1 FRACTION (e.g. block_rate 0.0751),
    but the whole app (and the Cube backend) renders rates on a 0–100 scale —
    Cube defines them as `100.0 * num/den`. Scale fraction-valued rate/percent
    columns by 100 so 0.0751 shows as 7.5%, matching the Cube path exactly."""
    for row in rows:
        for k, v in row.items():
            name = k.split(".")[-1].lower()
            if ("rate" in name or "percent" in name) and isinstance(v, (int, float)) \
                    and not isinstance(v, bool) and 0 <= v <= 1:
                row[k] = v * 100
    return rows


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "how many accounts by industry?"
    print(json.dumps(answer_question(q), indent=2, default=str))
