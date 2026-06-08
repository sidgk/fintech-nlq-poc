"""
Deterministic fast-path — the top exec questions resolve to a FIXED, reviewed
query spec with NO LLM call. Two wins at once:
  • speed   — sub-second (skips the ~7s model call)
  • accuracy — 100% reproducible; zero interpretation risk

Anything not matched here falls through to the LLM resolver as normal. To add a
metric to the fast-path, add a canonical phrasing + its spec below (and certify
the metric in the semantic layer). This list IS the "certified questions" set.
"""

import re

# Strip filler so many phrasings of the same question collapse to one key:
# "what's our total revenue?" / "show me total revenue" -> "total revenue"
_FILLER = re.compile(
    r"\b(what's|whats|what is|what are|what|our|the|a|an|me|show|give|tell|"
    r"how much is|how many|how much|can you|could you|please|do we have|"
    r"did we have|we have|do we|did|we|have|had|got|is|are|was|were|of|get|"
    r"currently|right now)\b")


def canon(q: str) -> str:
    q = (q or "").lower()
    q = _FILLER.sub(" ", q)                 # strip filler FIRST (apostrophes intact)
    q = re.sub(r"[^a-z0-9 ]", " ", q)       # then drop punctuation
    return re.sub(r"\s+", " ", q).strip()


# (canonical phrasings) -> fixed query spec (fully-qualified, certified members)
_ENTRIES = [
    (["total revenue", "revenue", "overall revenue", "gross revenue", "total amount",
      "payment volume", "total sales"],
     {"measures": ["payments_overview.total_amount"]}),
    (["success rate", "overall success rate", "approval rate", "acceptance rate"],
     {"measures": ["payments_overview.success_rate"]}),
    (["total payments", "number payments", "payments count", "payment count",
      "count payments", "transactions", "total transactions"],
     {"measures": ["payments_overview.count"]}),
    (["successful payments", "approved payments", "completed payments",
      "number successful payments"],
     {"measures": ["payments_overview.succeeded_count"]}),
    (["failed payments", "declined payments", "rejected payments", "number failed payments"],
     {"measures": ["payments_overview.failed_count"]}),
    (["average payment", "average payment amount", "average transaction value",
      "mean payment", "avg payment"],
     {"measures": ["payments_overview.avg_amount"]}),
    (["revenue by merchant category", "revenue by category", "revenue per category",
      "revenue per merchant category"],
     {"measures": ["payments_overview.total_amount"],
      "dimensions": ["payments_overview.merchants_category"]}),
    (["success rate by card brand", "success rate by brand", "approval rate by card brand"],
     {"measures": ["payments_overview.success_rate"],
      "dimensions": ["payments_overview.cards_brand"]}),
    (["revenue by merchant country", "revenue by country", "revenue per country"],
     {"measures": ["payments_overview.total_amount"],
      "dimensions": ["payments_overview.merchants_country"]}),
]

_INDEX = {}
for _aliases, _spec in _ENTRIES:
    for _a in _aliases:
        _INDEX[canon(_a)] = _spec


def match(question: str):
    """Return a fixed query spec for a canonical exec question, else None."""
    spec = _INDEX.get(canon(question))
    return dict(spec) if spec else None
