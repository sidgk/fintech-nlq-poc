"""
analytics.py — read the bot's query log. Run:  python bot/analytics.py

It's just SQL over the SQLite log (bot_state.db). The same queries work verbatim
if the log lives in Postgres / the warehouse in production.
"""

import sqlite3

import querylog

querylog.init()


def q(sql, args=()):
    with sqlite3.connect(querylog.DB) as c:
        return c.execute(sql, args).fetchall()


def main():
    total = q("SELECT COUNT(*) FROM query_log")[0][0]
    if not total:
        print("No questions logged yet. Ask the bot something in Slack first.")
        return

    print(f"\n=== KA26 / Data Bot — query analytics ({total} total questions) ===\n")

    print("• By kind:")
    for kind, n in q("SELECT kind, COUNT(*) FROM query_log GROUP BY kind ORDER BY 2 DESC"):
        print(f"    {kind or '?':10} {n}")

    hits = q("SELECT COALESCE(SUM(cache_hit),0), COUNT(*) FROM query_log WHERE kind='data'")[0]
    if hits[1]:
        print(f"\n• Cache hit rate (data questions): {hits[0]}/{hits[1]} = "
              f"{100*hits[0]/hits[1]:.0f}%  (each hit = an LLM + DB call SAVED)")

    print("\n• Latency (ms): fresh vs cached")
    for label, where in [("fresh ", "cache_hit=0"), ("cached", "cache_hit=1")]:
        r = q(f"SELECT AVG(latency_ms), MAX(latency_ms) FROM query_log WHERE kind='data' AND {where}")
        if r and r[0][0] is not None:
            print(f"    {label}: avg {r[0][0]:.0f}ms  max {r[0][1]}ms")

    print("\n• Top 10 most-asked questions (cache pays off most here):")
    for question, n in q("""SELECT question, COUNT(*) FROM query_log
                            WHERE kind='data' GROUP BY normalized ORDER BY 2 DESC LIMIT 10"""):
        print(f"    {n:3}×  {question[:70]}")

    errs = q("""SELECT question, COUNT(*) FROM query_log WHERE kind='error'
                GROUP BY normalized ORDER BY 2 DESC LIMIT 10""")
    if errs:
        print("\n• Questions we COULDN'T answer (gaps to fix in the semantic layer):")
        for question, n in errs:
            print(f"    {n:3}×  {question[:70]}")

    menu = q("""SELECT kind, COUNT(*) FROM query_log WHERE kind LIKE 'menu:%'
                GROUP BY kind ORDER BY 2 DESC""")
    if menu:
        print("\n• Follow-up option adoption (which option people pick most):")
        for kind, n in menu:
            print(f"    {kind.replace('menu:',''):14} {n}")

    up = q("SELECT COUNT(*) FROM query_log WHERE feedback=1")[0][0]
    down = q("SELECT COUNT(*) FROM query_log WHERE feedback=-1")[0][0]
    if up or down:
        print(f"\n• Feedback: 👍 {up}   👎 {down}"
              + (f"   ({100*up/(up+down):.0f}% positive)" if (up + down) else ""))
        bad = q("SELECT question FROM query_log WHERE feedback=-1 ORDER BY ts DESC LIMIT 10")
        if bad:
            print("    👎 answers to review (improve the metric or prompt):")
            for (question,) in bad:
                print(f"        {(question or '')[:65]}")

    print("\n• 10 most recent:")
    for created, user, kind, hit, q_text in q("""SELECT created_at, user_id, kind, cache_hit, question
                                                 FROM query_log ORDER BY ts DESC LIMIT 10"""):
        tag = "cache" if hit else "fresh"
        print(f"    {created}  {kind:7} {tag}  {(q_text or '')[:55]}")
    print()


if __name__ == "__main__":
    main()
