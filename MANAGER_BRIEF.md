# Manager brief — the whole system in plain English

A one-hour cram sheet. Read top to bottom; the Q&A at the end is your safety net.

---

## The 60-second pitch

> "A stakeholder asks a question in plain English in Slack — *'what was revenue by
> merchant category last month?'* — and gets the number back in seconds. The
> trick is that the AI **never writes SQL**. It can only pick from a set of
> business metrics we've defined and reviewed — our **semantic layer**. Cube turns
> that pick into real SQL, runs it on a clean, dbt-built data warehouse, and
> returns the answer. So it's fast like ChatGPT, but **governed and auditable**
> like a finance system."

---

## 1. What is a "semantic layer"? (the core idea)

**Analogy: a restaurant menu.**
- The **kitchen** = your database. Full of raw ingredients: columns like `amount_cents`, `status`, `merchant_id`. Messy, technical, not for customers.
- The **menu** = the semantic layer. It lists **dishes** (business metrics) in language people understand: *Revenue*, *Success rate*, *Failed payments*. Each dish has **one fixed recipe**.
- **Customers order from the menu.** They never walk into the kitchen and cook their own version.

"**Semantic**" literally means *meaning*. The semantic layer is where the **meaning** of your data lives:
- **Revenue** *means* `SUM(amount_cents / 100)`.
- **Success rate** *means* `succeeded ÷ total`.
- **"approved"** is a synonym for *succeeded*.
- A payment **joins** to a merchant on `merchant_id`.

In this project, the semantic layer is **literally a folder of plain-text files** (`model/*.yml`). Open `model/cubes/payments.yml` — that file *is* the semantic layer. Adding a metric = adding a few lines of YAML, reviewed in Git like any code.

**Why it matters (say this to your manager):**
1. **One source of truth.** "Revenue" is defined once. Nobody's number disagrees with anyone else's.
2. **Governance.** People — and the AI — can only use metrics we've approved. They can't invent a calculation.
3. **Trust.** Every answer traces back to a reviewed definition. That's what makes it safe for finance/risk.

---

## 2. What is Cube? Where does it add value?

**Cube is the software that runs the semantic layer.** The YAML files are just definitions; Cube is the engine that makes them *live*. It does three jobs:

1. **Serves the menu as an API.** Apps (our Slack bot) ask Cube "give me Revenue by Category" without knowing any SQL.
2. **Compiles to SQL.** Cube translates that request into the *exact* SQL — the joins, the `SUM`, the `GROUP BY`, the date filter — and runs it on the database.
3. **Caches** results so repeated questions are instant (and cheap, once you're on a pay-per-query warehouse).

**Where it adds value:** Cube is the **guardrail between people and raw SQL**. Without it, every analyst hand-writes SQL against raw tables — slow, inconsistent, ungovernable. With it, the calculation of every metric is centralized, consistent, and reusable across Slack, dashboards, APIs — anything.

**Is Cube a "server"?** Yes. It's not just files — it's a **running service** (in our demo, a Docker container on `localhost:4000`). That live service is what answers queries. So "the semantic layer" = the definitions (YAML) + Cube serving them.

> Note if asked about "MCP": we do **not** use an MCP server here. Our bot talks to
> Cube over its normal **REST API**. (MCP is a different protocol for connecting AI
> to tools; not part of this design.)

---

## 3. What is the role of the LLM (the AI model)?

The LLM (here: **Llama 3.1, running locally** — free, private, no rate limits) is a **translator**, nothing more.

- **Input:** the user's English question + the menu of available metrics (Cube's catalog).
- **Output:** a small structured **query spec** (JSON) that says *which metric, which grouping, which time filter* — using **only items that exist on the menu**.
- **What it never does:** it never writes SQL, never connects to the database, never sees a raw row. It picks from the menu; Cube cooks.

That constraint is the entire safety story. A normal "text-to-SQL" bot lets the AI write arbitrary SQL — it can hallucinate joins, miscompute, or hit the wrong table. Here the AI **physically cannot** do that, because all it can emit is a choice among defined metrics, which we then validate.

We also added **guardrails** around the LLM: we auto-correct any metric name it gets slightly wrong, strip time-buckets it adds when not asked, and route greetings/small-talk away from the data pipeline. So even an imperfect 8-billion-parameter model produces dependable results.

---

## 4. How a user query translates — end to end

```
You in Slack:  "@Data Bot revenue by merchant category last month"
      │
      ▼
1. LLM (Llama)   reads Cube's menu, translates →
                 {measures:[total_amount], dimensions:[merchants_category],
                  timeDimensions:[{created_at, last month}]}
                 (only menu items; never SQL)
      │
      ▼
2. Guardrails    qualify names, drop stray time-buckets, validate
      │
      ▼
3. Cube          compiles that spec → real SQL:
                 SELECT merchants.category, SUM(amount_cents/100)
                 FROM gold.fct_payments JOIN gold.dim_merchants … WHERE created_at … 
      │
      ▼
4. Postgres      runs the SQL on clean "gold" tables, returns rows
      │
      ▼
5. Bot           formats the answer in Slack (+ Google Sheet, + chart,
                 + "how was this calculated?" lineage on request)
```

---

## 5. Where does the clean data come from? (the pipeline behind Cube)

Cube reads **clean** tables. Those are produced by a separate pipeline — this is the "is the data *correct*" layer (vs. Cube, the "make it *askable*" layer):

**Medallion architecture, built by dbt, orchestrated by Airflow:**
- 🥉 **Bronze** (`raw`) — data as it lands: messy, duplicates, wrong types.
- 🥈 **Silver** (`stg_*`) — cleaned: de-duplicated, correctly typed.
- 🥇 **Gold** (`fct_payments`, `dim_*`) — the **star schema**: a central facts table + dimension tables, tested for quality.

- **dbt** = the tool that writes these transformations as SQL models (+ data-quality tests).
- **Airflow + Cosmos** = the scheduler that runs the dbt pipeline on a schedule and shows it as a visual graph (one box per step).

So the full stack: **dbt cleans → Cube serves → LLM translates → Slack delivers.**

---

## 6. Anticipated manager questions (your safety net)

**Q: Why not just use ChatGPT / text-to-SQL on our database?**
> Because it's ungovernable. A text-to-SQL model invents queries against raw tables — it can compute "revenue" five different ways or join the wrong table, and you can't trust or audit it. Our AI can only pick from metrics we've defined and reviewed, so every answer is consistent and traceable. Speed of AI, safety of a governed system.

**Q: What exactly is the semantic layer — is it a product we bought?**
> No — it's a folder of definition files in our Git repo (`model/`), served by an open-source tool called Cube. Adding a metric is a few lines of YAML, code-reviewed like any change. The governance is the whole point.

**Q: How do we know a number is right?**
> Three ways: (1) the metric's formula is defined once and reviewed; (2) the data is built by dbt with automated quality tests that fail the pipeline if something's off; (3) the bot can show full **lineage** on demand — the formula, the source tables, the Medallion transformations, and the exact SQL that produced the number.

**Q: Does the AI touch our database / could it leak data?**
> No. The AI only outputs a structured pick from the menu. It never connects to the database and never writes SQL. Cube runs the (pre-approved) SQL.

**Q: What does this cost / what's the AI?**
> The POC runs the AI model (Llama 3.1) locally — free, private, no per-query cost, no data leaving the machine. In production we'd point Cube at our real warehouse and could use a hosted model if we wanted.

**Q: How does it scale to real data?**
> Same `model/` files — we just point Cube at the production warehouse (e.g. Athena/Trino over our data lake) instead of the demo Postgres. Cube's caching keeps it fast and cheap. The semantic layer and the bot don't change.

**Q: Who maintains it?**
> The ongoing work is the **metric definitions** (the semantic layer) and a set of **golden test questions** we re-run on every change. That's how we expand domain by domain while keeping trust.

---

## 7. Vocabulary cheat-sheet

| Term | One line |
|---|---|
| **Semantic layer** | The business "menu" — metric definitions (meaning), in `model/*.yml` |
| **Metric / measure** | A number people ask for: revenue, success rate, count |
| **Dimension** | A way to slice a metric: by category, brand, country, time |
| **Cube** | The server that serves the menu + compiles requests into SQL |
| **Query spec** | The structured JSON the AI produces (a pick from the menu) |
| **dbt** | The tool that transforms raw → clean tables (with tests) |
| **Medallion** | Bronze (raw) → Silver (clean) → Gold (star schema) |
| **Airflow / Cosmos** | The scheduler that runs the dbt pipeline as a visual DAG |
| **Star schema** | A facts table surrounded by dimension tables |
| **Lineage** | The traceable path from source column → final number |
| **LLM (Llama)** | The translator: English → query spec. Never writes SQL. |

---

**The one sentence to anchor on:** *"It's natural-language analytics with the safety of a governed semantic layer — the AI translates the question, but a reviewed metric layer (Cube) does the math, on data a tested dbt pipeline keeps clean."*
