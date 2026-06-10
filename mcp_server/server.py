"""
datalake-metrics — a custom MCP server over a MetricFlow semantic layer.

It exposes the **MetricFlow semantic layer** (defined in dbt, on the gold models)
to any MCP-compatible AI client (e.g. Claude Desktop) as a small set of tools:

    list_metrics()                          → what can I ask?
    list_dimensions(metric)                 → how can I slice it?
    query_metric(metrics, group_by, ...)    → the numbers (MetricFlow compiles the SQL)

The AI NEVER writes SQL. It picks a defined metric + dimensions; MetricFlow turns
that into governed SQL on the warehouse (here Postgres; in prod a cloud warehouse
such as Athena/Iceberg, Snowflake, or BigQuery via the matching dbt adapter).

This is the standard pattern for a custom-MCP + dbt-semantic-layer stack: the
metric definitions live in the dbt project, MetricFlow compiles them, and a thin
MCP server serves them to the AI. Swapping the warehouse adapter is the only
change needed to point it at a different backend.

Run standalone:  python mcp_server/server.py   (speaks MCP over stdio)
Connect it to Claude Desktop via mcp_server/claude_desktop_config.example.json
"""

import os
import re
import sys
import subprocess

from mcp.server.fastmcp import FastMCP

# MetricFlow prints animated spinners + status chatter; strip it for clean output.
_NOISE = re.compile(r"(Initiating query|Looking for all|We've found|Success 🦄|"
                    r"query completed|🔍|🌱|🦄|[⠁-⣿])")

_HERE = os.path.dirname(os.path.abspath(__file__))
DBT_DIR = os.path.abspath(os.path.join(_HERE, "..", "dbt"))
MF = os.path.join(os.path.dirname(sys.executable), "mf")          # the venv's MetricFlow CLI
ENV = {
    **os.environ,
    "DBT_PROFILES_DIR": DBT_DIR,
    "DBT_HOST": os.environ.get("DBT_HOST", "127.0.0.1"),
    "DBT_PORT": os.environ.get("DBT_PORT", "55432"),
}


def _mf(args):
    """Run a MetricFlow CLI command in the dbt project and return its output."""
    try:
        r = subprocess.run([MF, *args], cwd=DBT_DIR, env=ENV,
                           capture_output=True, text=True, timeout=120)
        out = r.stdout or ""
        lines = [l for l in out.splitlines() if l.strip() and not _NOISE.search(l)]
        clean = "\n".join(lines).strip()
        if r.returncode != 0:
            clean += "\n[error] " + (r.stderr or "")[:600]
        return clean or "(no output)"
    except Exception as e:
        return f"[error] {e}"


def do_list_metrics():
    return _mf(["list", "metrics"])


def do_list_dimensions(metric):
    return _mf(["list", "dimensions", "--metrics", metric])


def do_query(metrics, group_by="", order_by="", limit=100):
    args = ["query", "--metrics", metrics]
    if group_by:
        args += ["--group-by", group_by]
    if order_by:
        args += ["--order", order_by]
    args += ["--limit", str(limit)]
    return _mf(args)


mcp = FastMCP("datalake-metrics")


@mcp.tool()
def list_metrics() -> str:
    """List the governed business metrics available to query (the 'menu')."""
    return do_list_metrics()


@mcp.tool()
def list_dimensions(metric: str) -> str:
    """List the dimensions a metric can be grouped by (e.g. for 'account_count')."""
    return do_list_dimensions(metric)


@mcp.tool()
def query_metric(metrics: str, group_by: str = "", order_by: str = "", limit: int = 100) -> str:
    """Query a governed business metric from the semantic layer (MetricFlow).

    metrics:  comma-separated metric name(s), e.g. 'account_count' or 'block_rate'.
    group_by: comma-separated dimensions, e.g. 'account__industry',
              'account__business_entity', or 'metric_time__month'.
    order_by: e.g. '-account_count' (descending).
    limit:    max rows.

    Returns the numbers. The AI never writes SQL — MetricFlow compiles the metric
    definition into governed SQL and runs it on the warehouse.
    """
    return do_query(metrics, group_by, order_by, limit)


if __name__ == "__main__":
    mcp.run()
