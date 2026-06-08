# Fintech NLQ POC — "ask your data in Slack"

A working proof-of-concept: a stakeholder asks a plain-English question in Slack
and gets the number back, grounded in a **semantic layer** (not raw text-to-SQL).

```
Slack  ──question──▶  Resolver (local LLM)  ──query spec──▶  Cube (semantic layer)
                                                                 │ compiles to SQL
                          number ◀── Slack ◀── rows ◀────────  Postgres (your data)
```

The point: **the LLM never writes SQL.** It can only pick from metrics and
dimensions we defined. That's what makes it trustworthy enough for finance/risk.

### 📚 Docs
| Read this | For |
|---|---|
| **[OVERVIEW.md](OVERVIEW.md)** | plain-English explainer + FAQ (start here) |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | components, data flow, reliability, accuracy gates |
| **[WALKTHROUGH.md](WALKTHROUGH.md)** | deep technical trace, raw → answer |
| **[docs/accuracy.md](docs/accuracy.md)** | how we guarantee correct numbers |
| **[ROADMAP.md](ROADMAP.md)** | strategic plan (accuracy-first), 3-horizon rollout |
| **[CHANGELOG.md](CHANGELOG.md)** | what's been achieved so far |
| **[DEMO_SCRIPT.md](DEMO_SCRIPT.md)** | live demo steps |
| **[workflow/process.md](workflow/process.md)** | run / operate / extend |

> **Phase 1 is built** — the numbers are no longer hand-curated over raw tables.
> A **dbt Core Medallion pipeline** (Bronze `raw` → Silver `stg_*` → Gold
> `fct_payments`/`dim_*`), **orchestrated by Airflow + Cosmos**, produces a tested
> star schema that Cube reads. See **[ARCHITECTURE.md](ARCHITECTURE.md)** and the
> manager walkthrough in **[DEMO_SCRIPT.md](DEMO_SCRIPT.md)**.
>
> ```
> raw (BRONZE, messy)  ─dbt─▶  silver (typed+deduped)  ─dbt─▶  gold (star schema)  ─▶  Cube  ─▶  Slack
>          └────────────── Airflow DAG `dbt_medallion` (one task per model+test) ──────────────┘
> ```
> Quick start for the full stack: `docker compose up -d` then Airflow UI at
> **http://localhost:8088** (admin/admin), Cube at **http://localhost:4000**.

---

## What *is* the semantic layer? (the thing you asked about)

It's not magic and it's not a service you have to write — it's literally the
**`model/` folder** in this project:

```
model/
├── cubes/
│   ├── payments.yml     <- the fact table: measures + dimensions + joins
│   ├── cards.yml
│   ├── merchants.yml
│   └── customers.yml
└── views/
    └── payments_overview.yml   <- a clean, flat surface for questions
```

Each `.yml` file maps one database table to **business concepts**:

- **measures** = the numbers people ask for (`succeeded_count`, `total_amount`, `success_rate`)
- **dimensions** = the ways they slice them (`status`, `cards.brand`, `merchants.category`, `created_at`)
- **joins** = how tables connect, defined once so nobody re-derives them
- **descriptions** = plain-English meaning + synonyms — this is the bit the LLM
  reads to know that "revenue" means `total_amount` and "approved" means `succeeded_count`

Cube (running in Docker) reads that folder and exposes it as an API. Open
`payments.yml` — that file *is* the semantic layer. Adding a metric = adding a
few lines of YAML, code-reviewed in Git. That governance is the whole moat.

---

## Prerequisites (Apple Silicon Mac is fine — images are arm64-native)

- Docker Desktop
- Python 3.11+
- An Anthropic API key
- A Slack workspace where you can create an app

---

## Step 1 — start the data + semantic layer

```bash
docker compose up -d
```

This starts Postgres (auto-seeded with ~20k synthetic payments) and Cube.
Give it ~30s, then open the **Developer Playground**: http://localhost:4000

In the Playground you can click measures/dimensions and watch Cube generate the
SQL — a great thing to show your manager *before* the Slack part. Try:
measure `Payments overview Succeeded count`, dimension `Payments overview Cards brand`.

Sanity-check the API directly:

```bash
curl localhost:4000/cubejs-api/v1/meta | head
```

## Step 2 — create the Slack app (one-time, in the Slack UI)

1. https://api.slack.com/apps → **Create New App** → *From scratch*.
2. **Socket Mode** → toggle ON. This generates an **App-Level Token** (`xapp-…`)
   → put it in `.env` as `SLACK_APP_TOKEN`.
3. **OAuth & Permissions** → Bot Token Scopes: add
   `app_mentions:read`, `chat:write`, `im:history`, `im:read`, `im:write`.
4. **Event Subscriptions** → ON → *Subscribe to bot events*: add
   `app_mention` and `message.im`.
5. **Install App** to your workspace → copy the **Bot User OAuth Token** (`xoxb-…`)
   → put it in `.env` as `SLACK_BOT_TOKEN`.
6. Invite the bot to a channel (`/invite @your-bot`) or just DM it.

## Step 3 — run the bot

```bash
cp .env.example .env        # then fill in the three tokens/keys
set -a; source .env; set +a

python3 -m venv venv && source venv/bin/activate
pip install -r bot/requirements.txt

cd bot && python app.py
```

Now in Slack:

> @your-bot how many successful card payments did we have last month?
> @your-bot total revenue by merchant category
> @your-bot failed payments this week by card brand
> @your-bot average payment amount for enterprise customers

---

## No Slack yet? Test the brain from the terminal

```bash
set -a; source .env; set +a
cd bot && python resolver.py "total revenue by merchant category last 30 days"
```

It prints the resolved query spec **and** the numbers — proof the pipeline works
even before Slack is wired up. Good fallback if the Slack setup runs long tonight.

---

## Where this goes next (the honest part for your manager)

- The demo runs on local Postgres. In production, swap Cube's connection to
  **Athena/Trino over your Iceberg lake** — same `model/` files, the data source
  changes. Cube also caches (pre-aggregations), which matters for Athena cost.
- The real, ongoing work is **the `model/` definitions + their descriptions**, and
  a **golden-question eval** (a set of question→expected-answer pairs you run on
  every change). That's how you earn trust to expand domain by domain.
- Charts ("open a link, play with it") come later via Superset pointed at Cube.
