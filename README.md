# Self-Learning AI Financial Analyst Agent

An AI analyst for Indian market filings — SEBI **DRHP / RHP** offer documents, **Regulation 33 quarterly results**
and **annual reports**. You upload a filing; it extracts the numbers, checks them the way an experienced equity /
credit analyst would, and produces an explainable report: red flags, anomalies, data gaps and questions for
management, **every figure traced back to the page it came from**. Analyst feedback on the findings makes it
better over time — without any model fine-tuning.

It is built as a self-contained module (REST API + web app) that a larger fintech platform can call.

```
Filing ─► Ingestion ─► Financial engine ─► Fact sheet ─► AI agent ─────────────────► Analysis report
(PDF /    (no LLM)     (metrics, rules,    (~1.1K        (plan → investigate →       (findings, evidence,
 XBRL)                  anomalies)          tokens)       review → compose)           scorecard, trace)
                              ▲                                                            │
                              └──────────── Learning loop ◄──── analyst confirm / dismiss ─┘
```

---

## Contents

1. [What it does](#1-what-it-does)
2. [Quickstart — set up and run](#2-quickstart--set-up-and-run)
3. [Configuration](#3-configuration)
4. [How it works, end to end](#4-how-it-works-end-to-end)
5. [Architecture deep dive](#5-architecture-deep-dive) — ingestion · engine · knowledge base · LLM gateway ·
   agent · guardrail · report · learning · jobs · API · database · frontend
6. [REST API reference](#6-rest-api-reference)
7. [Database schema](#7-database-schema)
8. [Tech stack](#8-tech-stack)
9. [Repository layout](#9-repository-layout)
10. [Testing and evaluation](#10-testing-and-evaluation)
11. [Troubleshooting](#11-troubleshooting)
12. [Design decisions and trade-offs](#12-design-decisions-and-trade-offs)
13. [Limitations and roadmap](#13-limitations-and-roadmap)
14. [Deploying (optional)](#14-deploying-optional)

---

## 1. What it does

| You give it | You get back |
| --- | --- |
| An NSE/BSE **XBRL** quarterly result, or a **DRHP/RHP PDF** | A structured dataset of ~100 line items per period, each fact tagged with its source (XBRL element, or PDF page + printed label) |
| — | **59 metrics** (margins, growth, DSO/DIO/DPO, cash conversion, leverage, returns, IPO valuation) computed with exact decimal arithmetic |
| — | **55 rules** evaluated per period (fired / passed / insufficient data), plus statistical **anomalies** against the company's own history and its peer cohort |
| — | An **AI-written analysis**: findings grouped into lines of enquiry, benign explanations weighed, severity reviewed by a critic, an executive summary, and questions for management |
| — | A **web app** to read the report, click any figure to see the source PDF page, confirm/dismiss findings, and review rules the agent proposes |

**The core idea — the LLM never produces a number.** Language models are good at judgement and bad at arithmetic,
so the system is split on that line:

| Deterministic engine (no LLM) | AI agent (LLM) |
| --- | --- |
| Extracts and normalises reported figures | Decides which lines of enquiry matter for this filing |
| Checks the statements reconcile | Interprets what a red flag means in context |
| Computes metrics, evaluates rules, finds anomalies | Weighs the benign explanations the filing offers |
| Is fully reproducible and unit-tested | Writes the narrative and the questions for management |

The agent may only *cite* numbers, as references like `{{m:dso@FY25}}`; the server substitutes the verified value.
A made-up reference is rejected, and a number typed without a reference is caught and sent back for repair — so a
hallucinated figure cannot reach a report.

---

## 2. Quickstart — set up and run

### Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.12+ | backend |
| Node.js | 20+ | frontend |
| Groq API key | free | [console.groq.com/keys](https://console.groq.com/keys). Optional — without it the app runs **rules-only** (no AI-written findings). |
| Supabase project | free | Optional — without it the app uses a local SQLite file. |

Commands below are for **Windows PowerShell**; on macOS / Linux replace `.venv\Scripts\python` with
`.venv/bin/python`.

### Step 1 — clone

```bash
git clone https://github.com/Aditya-Gupta171/Fin-Analyst-Agent.git
```
```bash
cd Fin-Analyst-Agent
```

### Step 2 — create the `.env` file (repository root, *not* `backend/`)

```
GROQ_API_KEY=gsk_your_key_here
# Optional — omit to use a local SQLite file at data/app.db:
# DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
```

Never commit this file (it is gitignored). See [Configuration](#3-configuration) for every setting.

### Step 3 — backend

All backend commands run **from the `backend` folder** — running them from the repo root is the most common
mistake (`'.venv\Scripts\python' is not recognized`).

```bash
cd backend
```
```bash
python -m venv .venv
```
```bash
.venv\Scripts\python -m pip install -e ".[dev,embeddings]"
```
```bash
.venv\Scripts\python -m pytest
```
All 353 tests run offline in about 30 seconds — no network, no API key needed.

**Only if you use Supabase/Postgres**, create the schema once (and after pulling new migrations):

```bash
.venv\Scripts\python -m alembic upgrade head
```

Start the API:

```bash
.venv\Scripts\python -m uvicorn app.api.main:app --reload
```

- Wait for `Application startup complete`. **The very first start takes ~1 minute**: it downloads the two local
  retrieval models (~0.5 GB, once) and embeds the knowledge base (cached afterwards — later starts take seconds).
- Open **http://127.0.0.1:8000/docs** for the interactive API, or **http://127.0.0.1:8000/health** — it should
  return `"status": "ok"` and `"llm_enabled": true` if your Groq key was picked up.

### Step 4 — frontend (second terminal)

```bash
cd frontend
```
```bash
npm install
```
```bash
npm run dev
```

Open **http://localhost:3000**. The frontend expects the backend at `http://localhost:8000`; to point it
elsewhere create `frontend/.env.local` with `NEXT_PUBLIC_API_BASE_URL=<url>`.

### Step 5 — try it

1. Click **Upload filing** and choose `backend/tests/fixtures/xbrl/nocil_q2fy25_standalone.xml` (a real NSE
   quarterly result). Pick a sector (e.g. *chemicals*).
2. On the document page click **Run analysis** and watch the live progress (engine → plan → investigate →
   review → compose).
3. Read the report: click any evidence chip to see the value, its source label and — for PDFs — the source page.
   Confirm or dismiss findings; see the effect on the **Learning** page.

Real DRHP/RHP PDFs work the same way (the ingestion tests were built on the Hyundai Motor India, Ola Electric and
Rentomojo offer documents). A PDF analysis makes more LLM calls; on Groq's free tier it takes a few minutes.

---

## 3. Configuration

Settings come from environment variables or the repo-root `.env` ([`app/settings.py`](backend/app/settings.py),
pydantic-settings; names are case-insensitive).

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | unset | Enables the AI agent. Without it analyses still complete, **rules-only**. |
| `DATABASE_URL` | SQLite at `data/app.db` | Postgres/Supabase connection string. A plain `postgresql://` URL is upgraded to the async driver automatically. |
| `REASONING_MODEL` | `openai/gpt-oss-120b` | Groq model for planning, investigating, reviewing and composing. |
| `FAST_MODEL` | `openai/gpt-oss-20b` | Groq model for lighter structured tasks (rule proposals). |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | Any OpenAI-compatible endpoint serving the same models. |
| `LLM_TIMEOUT_SECONDS` | `90` | Per-request timeout. |
| `LLM_MAX_RETRIES` | `4` | HTTP retries on 408/409/429/5xx (with `Retry-After` / exponential backoff). |
| `LLM_CACHE_DIR` | `data/llm_cache` | On-disk response cache — re-running the same analysis costs nothing. |
| `API_KEY` | unset | When set, **mutating** requests (`POST`) need `Authorization: Bearer <key>`. Reads stay open so the browser's live progress stream and PDF viewer keep working. |
| `CORS_ORIGINS` | `["*"]` | JSON list of allowed frontend origins. |
| `MAX_UPLOAD_MB` | `100` | Largest accepted upload (a 600-page DRHP fits); bigger returns `413`. |
| `MAX_CONCURRENT_ANALYSES` | `2` | How many analyses run at once in the background queue. |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | *Frontend*, in `frontend/.env.local`: where the backend is. |

### Using Supabase Postgres instead of SQLite

1. Create a free project at [supabase.com](https://supabase.com) → **Project Settings → Database → Connection
   string → "Transaction" pooler** (port 6543). Use the pooler host (`aws-0-<region>.pooler.supabase.com`, user
   `postgres.<project-ref>`): the direct host is IPv6-only and fails on many networks.
2. Put it in `.env` as `DATABASE_URL=...` (the plain URI Supabase shows is fine).
3. From `backend/`: `.venv\Scripts\python -m alembic upgrade head`.

On Postgres the app never creates tables itself — Alembic owns the schema, so it always matches the migrations.
The migrations also **enable row-level security on every table** and revoke Supabase's `anon`/`authenticated`
grants ([`5f3c9a1e7d24`](backend/alembic/versions/5f3c9a1e7d24_lock_down_public_tables.py)): Supabase otherwise
exposes `public` tables through its REST API to anyone holding the publishable anon key. The app doesn't use that
API — it connects directly as the table owner, which RLS doesn't restrict.

Pooler-specific engine settings ([`app/db/engine.py`](backend/app/db/engine.py)): the prepared-statement cache is
disabled (`statement_cache_size=0`, else `DuplicatePreparedStatementError` in transaction mode), and
`pool_pre_ping` + `pool_recycle=280` survive the pooler silently closing idle connections.

> Free Supabase projects **pause after ~7 days of inactivity**; the backend then fails to start with
> `tenant/user postgres.<ref> not found`. Restore the project from the Supabase dashboard.

---

## 4. How it works, end to end

What happens when you upload a filing and click **Run analysis**:

```
 Browser (Next.js)                 FastAPI backend                                        Database
 ─────────────────                 ───────────────                                        ────────
 Upload filing ── POST /documents ─► ingestion (XBRL or PDF → FinancialDataset, no LLM) ──► documents
 Run analysis ─ POST /documents/{id}/analyses ─► analyses row (queued) ─► job queue ──────► analyses
                                        │  202 Accepted (returns immediately)
                                        ▼
                             background worker thread
                             ├─ load cohort baselines, rule reliability, chunk boosts ◄─── (learning tables)
                             ├─ engine: validate → metrics → rules → anomalies
                             ├─ fact sheet (~1.1K tokens of citable text)
                             ├─ agent (LangGraph + Groq): plan → investigate → review
                             │        → [revise → re-check] → compose → propose
                             ├─ merge agent findings + uncovered rule findings → AnalysisReport
                             └─ persist report, findings, candidate rules, baselines ──────► analyses, findings…
 Progress panel ◄── GET /analyses/{id}/events (Server-Sent Events: stage + message)
 Report page    ◄── GET /analyses/{id}  (the full AnalysisReport JSON)
 Confirm/Dismiss ── POST …/findings/{fid}/feedback ──────────────────────────────────────► feedback
                                        └─► next analysis: rule reliability, chunk boosts, cohorts updated
```

1. **Ingestion** turns any supported file into one canonical `FinancialDataset` (facts × periods), deterministically.
2. **The engine** computes metrics and evaluates rules for every period, and detects statistical anomalies.
3. **The fact sheet** renders all of that as compact text in which every value carries a reference, e.g.
   `[m:dso@FY25] DSO = 98 days`.
4. **The agent** reads the fact sheet, decides what matters, investigates each line of enquiry (optionally calling
   read-only tools and the knowledge base), has a critic review its drafts, and writes the summary — citing values
   only by reference.
5. **The report** merges the agent's findings with any fired rule the agent didn't cover, renders every reference
   to its verified value, and records the catalog / knowledge-base / prompt versions for reproducibility.
6. **Feedback** from the analyst feeds three learning loops that change future analyses.

If the LLM is unavailable at any step, that step falls back to a deterministic result — **an analysis always
completes**, with or without a working model.

---

## 5. Architecture deep dive

### 5.1 Domain model — [`app/domain/`](backend/app/domain)

- **`Period`** — Indian fiscal periods: `FY25`, `Q3FY25`, `H1FY25`, `9MFY25`, with a configurable fiscal-year-end
  month (some companies use January–December). Knows its year-ago comparable and previous sequential period, and
  can be built from start/end dates.
- **`Fact`** — `key` + `period` + a `Decimal` value (or text) + a `SourceRef` (page, section, raw label,
  extraction method). Every number keeps its provenance. Amounts are ₹ crore; ratios are fractions.
- **`FinancialDataset`** — company info + document info + facts. Every ingestion path produces this, so a new file
  format never touches analysis code.

### 5.2 Ingestion — [`app/ingestion/`](backend/app/ingestion) (no LLM)

The [router](backend/app/ingestion/router.py) picks a parser from the file's first bytes (`%PDF` → offer document,
`<`/`.xml` → XBRL).

**XBRL results** ([`xbrl.py`](backend/app/ingestion/xbrl.py)) — the highest-fidelity input; both NSE/BSE schemas
(`in-bse-fin` and the 2025 integrated filing). Element → line-item mappings live in the taxonomy YAML. Built against
unmodified public filings, it handles what the specification doesn't say: values in full rupees (converted to
crore), outflows tagged as positive numbers, context dates that contradict the stated reporting period, opening vs
closing cash told apart only by position, and exceptional items with the wrong sign. [`merge.py`](backend/app/ingestion/merge.py)
combines several filings into comparatives and flags restated figures.

**DRHP / RHP PDFs** ([`pdf/`](backend/app/ingestion/pdf)):
1. Find the restated financial statements by their headings (fast full-text via pdfium).
2. Read **word positions** (pdfplumber), not extracted text — character spacing splits numbers (`1 5,647.23`) and
   period headers wrap across lines.
3. Find columns by clustering the **right edges** of amounts; read each column's period from the dates above it.
4. Map row labels to the taxonomy (stemmed-token Jaccard similarity) using section context — "Borrowings" under
   current vs non-current liabilities — and roll sub-rows up into their header.
5. [`reconcile.py`](backend/app/ingestion/reconcile.py) corrects presentation differences using the statements'
   own arithmetic: tax shown as "(expense)/credit", totals printed before finance costs, exceptional losses
   printed positive.

| Document | Periods | Facts | Integrity checks |
| --- | --- | --- | --- |
| Hyundai Motor India RHP | FY22–FY24, Q1FY24, Q1FY25 | 285 | all pass |
| Ola Electric Mobility RHP | FY22–FY24 | 180 | all pass except cash-flow vs balance-sheet cash (overdraft netted in the cash flow) |
| Rentomojo DRHP | FY23–FY25, H1FY26 | 212 | all pass except EPS vs profit growth (share count changed) |

### 5.3 Financial engine — [`app/engine/`](backend/app/engine) + [`rules/`](rules) (no LLM)

**Rules are data, not code.** Three kinds of versioned YAML:

| Folder | Contents |
| --- | --- |
| [`rules/taxonomy/`](rules/taxonomy) | 97 canonical line items (Ind AS / Schedule III) with filing-label aliases and XBRL element mappings |
| [`rules/metrics/`](rules/metrics) | 59 metric formulas, e.g. `ebitda = revenue_from_operations - (total_expenses - finance_costs - depreciation_amortisation)` |
| [`rules/packs/`](rules/packs) | 55 rules: `core` (30), `offer_document` (10), `integrity` (9), `quarterly` (6); `learned/` for approved agent proposals |

```yaml
- id: WC_RECEIVABLES_OUTPACE_REVENUE
  severity: high
  when: receivables_growth - revenue_growth > 0.20 and trade_receivables > 0.05 * revenue_from_operations
  escalations:
    - { when: cfo_to_pat < 0.5, severity: critical }
  evidence: [receivables_growth, revenue_growth, dso, cfo_to_pat]
  kb: working-capital/receivables
  questions:
    - Were credit terms extended to key customers during the year?
```

- **Safe expression language** ([`expressions.py`](backend/app/engine/expressions.py)) — parsed with Python's `ast`
  and checked against a whitelist (arithmetic, comparisons, `and/or/not`, ~19 registered functions such as
  `prior`, `growth`, `avg`, `ttm`, `streak`, `cohort_pct`, `history_z`); interpreted by a tree-walking
  [evaluator](backend/app/engine/evaluator.py). Nothing ever reaches `eval()`.
- **Missing is not zero** — boolean logic is three-valued (Kleene): a rule with missing inputs reports
  *insufficient data* with the exact missing facts, rather than silently passing. Line items Schedule III only
  requires when non-zero are marked `nil_if_absent`.
- **The catalog** ([`catalog.py`](backend/app/engine/catalog.py)) validates everything at load (schema, unknown
  names, metric dependency cycles) and hashes its content — the `catalog_version` recorded on every report.
- **The pipeline** ([`pipeline.py`](backend/app/engine/pipeline.py)): validate dataset → size bucket (annualised
  revenue: small < ₹500 cr, mid < ₹5,000 cr, large) → every metric for every period → every rule for every period
  (fired / passed / insufficient data, with severity escalation) → anomalies. A rule that errors is skipped and
  reported instead of failing the analysis.
- **Anomalies** ([`anomalies.py`](backend/app/engine/anomalies.py)) — robust z-score (median/MAD, |z| > 3.5)
  against the company's own history (≥ 3 prior periods), and percentile rank (≤ 5% or ≥ 95%) against its
  sector × size cohort (≥ 8 peers).
- **Fact sheet** ([`factsheet.py`](backend/app/engine/factsheet.py)) — the agent's entire view of the filing in
  ~1.1K tokens: header, integrity checks, fired rules with evidence, anomalies, key metrics, data gaps — every
  value tagged with its reference.

### 5.4 Knowledge base and retrieval — [`knowledge/`](knowledge) + [`app/knowledge/`](backend/app/knowledge)

**49 curated markdown documents** written for this project: one for every `kb:` reference in the rules (a test
enforces it), guides to reading quarterly results, annual reports, DRHPs/RHPs and the three statements, an analyst
working method, and nine sector primers (banks, NBFCs, IT services, infrastructure, manufacturing, pharma, FMCG,
real estate, consumer internet). Every document has the same sections — *what it measures, how to read it, red
flags, benign explanations, where to find it in Indian filings, questions for management* — and each section is
one retrievable chunk tagged with its kind (295 chunks). That lets the critic fetch **only benign explanations**,
and the agent fetch the exact document a fired rule names, with no search at all.

Open questions use **hybrid retrieval** ([`retriever.py`](backend/app/knowledge/retriever.py)):

```
metadata filters (doc type, sector, section kind)
  → BM25 (with finance abbreviation expansion: DSO, CFO, OFS, GCP, CWIP, RPT, …)
  + dense embeddings (snowflake-arctic-embed-m, local ONNX via fastembed, with its query instruction)
  → reciprocal rank fusion
  → cross-encoder rerank of the top 8 (ms-marco-MiniLM-L-12)
  → learned per-chunk utility boost (from analyst feedback)
```

Measured on [`evals/retrieval.yaml`](evals/retrieval.yaml) — 64 queries phrased the way the agent asks, many
paraphrased away from the documents' wording (`python -m app.knowledge.evaluation`):

| Configuration | Recall@1 | Recall@3 | MRR |
| --- | --- | --- | --- |
| Lexical (BM25) | 84.4% | 95.3% | 0.905 |
| Dense (arctic-embed-m) | 90.6% | 96.9% | 0.939 |
| Hybrid (RRF) | 93.8% | 98.4% | 0.962 |
| **Hybrid + rerank (default)** | **98.4%** | **100%** | **0.992** |

The models were chosen by an ablation over fastembed's local models (same queries, same pipeline):

| Embedder (hybrid + MiniLM-L6 rerank) | Recall@1 | Recall@3 | Embed corpus (CPU) |
| --- | --- | --- | --- |
| bge-small-en-v1.5 (previous default) | 93.8% | 98.4% | ~15 s |
| bge-base-en-v1.5 + query instruction | 93.8% | 100% | ~44 s |
| arctic-embed-m, *no* query instruction | 60.9% | 89.1% | ~48 s |
| **arctic-embed-m + query instruction** | **96.9%** | **100%** | ~48 s (cached after first run) |

| Reranker (on arctic-embed-m) | Recall@1 | Recall@3 | ms/query |
| --- | --- | --- | --- |
| none | 93.8% | 98.4% | ~25 |
| jina-reranker-v1-tiny / -turbo | 92.2% / 93.8% | 100% | ~520 / ~700 |
| ms-marco-MiniLM-L-6 | 96.9% | 100% | ~840 |
| **ms-marco-MiniLM-L-12 (default)** | **98.4%** | **100%** | ~1,100–1,300 |

- Retrieval models like arctic-embed and bge expect an instruction in front of the *query* ("Represent this
  sentence for searching relevant passages: "), which fastembed does not add; [`embeddings.py`](backend/app/knowledge/embeddings.py)
  applies it per model — without it arctic-embed-m falls from 91% to 31% dense recall@1.
- Document embeddings are cached in `data/embedding_cache/`, keyed by model and exact chunk texts, so a restart with
  an unchanged knowledge base loads it in under a second; editing a document re-embeds automatically.
- The reranker's extra latency is immaterial here: the agent runs a handful of knowledge searches per filing, next
  to minutes of LLM calls.
- Caveats: with 64 queries one query is ~1.6 points, so treat the gains as a clear direction rather than a precise
  margin; and the queries and documents share an author, which likely flatters lexical scores.
- Without the `embeddings` extra installed, the loader falls back to BM25-only automatically.

### 5.5 LLM gateway — [`app/llm/`](backend/app/llm)

A small OpenAI-compatible client built around Groq's free tier (8,000 tokens/minute per model):

- **[`groq.py`](backend/app/llm/groq.py)** — `POST /chat/completions` with `response_format: json_schema, strict:
  true`; retries 408/409/429/5xx using `Retry-After` or exponential backoff with jitter.
- **[`budget.py`](backend/app/llm/budget.py) — TokenBudget** reads Groq's `x-ratelimit-remaining-*` headers and
  *waits before sending* rather than running into a 429.
- **[`cache.py`](backend/app/llm/cache.py) — ResponseCache**: sha256 of the full request (model, messages, schema) →
  JSON on disk. Re-analysing a filing costs nothing; tests are reproducible.
- **[`structured.py`](backend/app/llm/structured.py) — `generate()`** converts a Pydantic model into Groq's strict
  JSON-schema dialect (every property required, `additionalProperties: false`, optionals as nullable types, no
  `$ref`), then runs a **repair loop** (up to 3 attempts): if the provider rejects the JSON, Pydantic fails, or the
  domain validator finds problems, the model is shown exactly what was wrong and asked to fix it.
- **[`gateway.py`](backend/app/llm/gateway.py)** maps tiers (`reasoning` / `fast`) to models and records a
  `CallRecord` (node, model, tokens, latency, attempts, outcome) per call — each analysis gets its own trace.

### 5.6 The agent — [`app/agent/`](backend/app/agent)

A [LangGraph](https://github.com/langchain-ai/langgraph) state machine ([`graph.py`](backend/app/agent/graph.py)):

```
START → plan → investigate → review ─┬─ any "revise" ─► revise → recheck ─┐
                                      └──────────────────────────────────┴─► compose → propose → END
```

| Node | Model | What it does | Deterministic fallback |
| --- | --- | --- | --- |
| **plan** | 120b | Groups fired rules and anomalies into ≤ 5 non-overlapping *lines of enquiry*, or dismisses a mild flag as immaterial with a reason (never a high/critical or integrity rule) | Group fired rules by category |
| **investigate** | 120b | One call per enquiry with only its rules, evidence and knowledge excerpts. May request up to 3 read-only lookups (`metric_history`, `knowledge_search`, `rule_detail`) before answering `submit_finding` or `no_finding` | Skip; its rules surface as rule findings |
| **review** | 120b | A critic: `accept`, `revise` (with instructions) or `reject` each draft, and set the final severity | Findings marked *unreviewed* |
| **revise** | 120b | The analyst rewrites a draft per the critic — or withdraws it | Keep the draft |
| **recheck** | 120b | Final accept/reject pass over revised drafts | Revised drafts marked *unreviewed* |
| **compose** | 120b | Headline, overall risk, executive summary, strengths and concerns | Deterministic summary |
| **propose** | 20b | ≤ 2 *candidate rules* in the rule expression language for patterns no rule covers; parsed and validated before they're ever shown | No proposals |

Tool use is built into the output schema (`action: request_context | submit_finding | no_finding`) because the
provider can't combine tool calls with strict JSON output. Prompts are in [`prompts.py`](backend/app/agent/prompts.py);
their hash is the report's `prompt_version`. Raw filing text never reaches the model — only the extracted,
structured fact sheet — which limits prompt-injection exposure.

### 5.7 The numbers-by-reference guardrail

1. The fact sheet shows values with references: `[m:dso@FY25] DSO = 98 days`, `[f:trade_receivables@FY25] …`.
   (`f:` = reported fact, `m:` = computed metric, `x:` = rule evidence expression.)
2. The model must write `{{m:dso@FY25}}`, never "98 days".
3. [`check_prose`](backend/app/analysis/evidence.py) rejects unknown references and **bare figures**
   ([`bare_figures`](backend/app/engine/references.py) matches `55%`, `₹80 crore`, `1.8x`, `98 days`, …) — the
   problems go back into the repair loop.
4. [`link_figures`](backend/app/analysis/evidence.py) auto-repairs a figure the model copied by hand into its
   reference — only when exactly one reference *shown in that prompt* has that value (so it can't cite an unrelated
   metric that happens to be equal), preferring the finding's own evidence.
5. When the report is built, every reference is rendered to its verified display value.

Result: every number in a finding, the summary and the strengths/concerns resolves to a fact with its source.

### 5.8 The report — [`app/analysis/`](backend/app/analysis)

[`service.py`](backend/app/analysis/service.py) builds the versioned `AnalysisReport`
([`report.py`](backend/app/analysis/report.py)):

- **Findings** — agent findings (`A1`, `A2`, …) first, then a rule finding (`R-<rule_id>`) for every fired rule the
  agent didn't cover or dismiss. Each has severity, confidence, summary, analysis, **evidence items** (value, page,
  source label), knowledge citations, benign explanations considered, questions for management, and the critic's
  verdict.
- **Dismissed rules** with the agent's stated reason; **scorecard** per area; **integrity checks**; **anomalies**;
  **data gaps**; the **metrics table**; **candidate rules**.
- **Provenance** — mode (`agent` / `rules_only`), `catalog_version`, `kb_version`, `prompt_version`, models, token
  usage and fallback notes — plus the full **LLM call trace**.

### 5.9 How "self-learning" works (no fine-tuning)

All four loops are driven by stored data, not model weights:

| Loop | Mechanism | Code |
| --- | --- | --- |
| **Peer cohorts** | Thresholds can reference the company's sector × size cohort, e.g. `dso > cohort_pct(dso, 90, 120)` (90th percentile of peers, 120 days until ≥ 8 peers exist). Every analysed filing adds its metrics to its cohort, so the same rule adapts — 98 days of receivables is flagged among fast-collecting manufacturers but not slow-collecting ones. | [`db/baselines.py`](backend/app/db/baselines.py) |
| **Rule reliability** | Each rule's confidence is a Beta posterior over analyst verdicts: `(3 + confirms) / (5 + confirms + dismisses)`, starting at 0.6. Only the latest verdict per finding counts. | [`db/reliability.py`](backend/app/db/reliability.py) |
| **Knowledge utility** | Knowledge chunks cited by confirmed findings get a retrieval boost (+0.2 each), dismissed ones a penalty (−0.1), clamped to [−0.5, 1.0]. | [`db/chunk_utility.py`](backend/app/db/chunk_utility.py) |
| **Rule proposals** | The agent proposes candidate rules → an analyst **backtests** one against every stored filing → **approves** it → it's appended to `rules/packs/learned/proposed.yaml` (active after a restart; commit it to keep it). | [`db/backtest.py`](backend/app/db/backtest.py), [`engine/promotion.py`](backend/app/engine/promotion.py) |

### 5.10 Background jobs — [`app/jobs/`](backend/app/jobs)

An analysis takes seconds (rules-only) to minutes (agent on the free tier), so `POST …/analyses` returns
**202 Accepted** immediately and a background job does the work:

- [`queue.py`](backend/app/jobs/queue.py) — in-process asyncio tasks limited by a semaphore
  (`MAX_CONCURRENT_ANALYSES`).
- [`runner.py`](backend/app/jobs/runner.py) — loads learning state, runs engine + agent in a **worker thread**
  (`asyncio.to_thread`) so the API stays responsive, persists the results, and always ends the job `succeeded` or
  `failed` whatever goes wrong.
- [`progress.py`](backend/app/jobs/progress.py) — in-memory stage/message per job, streamed to the browser as SSE.
- On startup, any job left `queued`/`running` by a crash is marked `failed` (there is no durable queue — see
  [trade-offs](#12-design-decisions-and-trade-offs)).

### 5.11 API layer — [`app/api/`](backend/app/api)

FastAPI with a lifespan hook that loads the rule catalog, knowledge base and LLM gateway once into a shared
`AppState`. Routers: `documents`, `analyses`, `rules`, `health`. Upload parsing runs off the event loop; uploads
are size-limited; stored files are served inline only if they are PDFs (anything else downloads, with `nosniff`).
Full schemas at `/docs`.

### 5.12 Frontend — [`frontend/`](frontend)

Next.js (App Router) + TypeScript + Tailwind + shadcn/ui, data via **TanStack Query**. Types come from the backend's
own OpenAPI schema (`npm run gen:types` with the backend running) via openapi-typescript/openapi-fetch, so the two
can't drift.

| Page | What's there |
| --- | --- |
| `/` | Document library and the upload dialog |
| `/documents/[id]` | A filing's details, its analysis history, and **Run analysis** |
| `/analyses/[id]` | Live progress (SSE) while running; then the report — risk badge, headline, summary, severity chart, strengths/concerns, findings with **evidence chips** (click → value, source label and the **source PDF page** rendered with pdf.js), confirm/dismiss buttons, scorecard & metrics, the **reasoning trace** of every LLM call, candidate rules |
| `/learning` | Feedback-calibrated rule reliability; backtest / approve / reject agent-proposed rules |

Error boundaries (`error.tsx`, `global-error.tsx`) and a `not-found.tsx` keep a bad response from blanking the app.

---

## 6. REST API reference

| Method & path | Does |
| --- | --- |
| `GET /health` | DB, catalog and knowledge-base readiness; `llm_enabled` |
| `POST /documents` | Upload a filing (multipart `file`, optional `sector`, `source_url`); ingests it and returns the document plus ingestion warnings and unmapped labels. `413` if too large, `422` if unsupported. |
| `GET /documents` | List uploaded documents |
| `GET /documents/{id}` | One document with its parsed dataset |
| `GET /documents/{id}/file` | The raw uploaded file (the evidence viewer's PDF source) |
| `POST /documents/{id}/analyses` | Queue an analysis → `202` with the analysis id |
| `GET /analyses?document_id=` | List analyses, newest first |
| `GET /analyses/{id}` | Status, current stage, and the full `AnalysisReport` once succeeded |
| `GET /analyses/{id}/events` | Server-Sent Events: `{status, stage, message}` until the run finishes |
| `GET /analyses/{id}/findings` | The report's findings with their feedback status |
| `POST /analyses/{id}/findings/{finding_id}/feedback` | Record `{"verdict": "confirm" \| "dismiss", "reason"?}` |
| `GET /rules/candidates?status_filter=` | Agent-proposed candidate rules |
| `POST /rules/candidates/{id}/backtest` | Run a candidate against every stored filing and save the result |
| `POST /rules/candidates/{id}/decision` | `{"decision": "approve" \| "reject"}` — approve promotes it into `rules/packs/learned/`; `409` if already decided, `422` if it failed validation |
| `GET /rules/reliability` | Beta-calibrated confidence per rule that has feedback |

Example:

```bash
curl -F "file=@backend/tests/fixtures/xbrl/nocil_q2fy25_standalone.xml" -F sector=chemicals http://127.0.0.1:8000/documents
```

---

## 7. Database schema

Six tables ([`app/db/models.py`](backend/app/db/models.py), migrations in [`backend/alembic/`](backend/alembic)).
Primary keys are UUID hex strings. The report is stored as one immutable JSON document; only the parts that change
or get queried (finding status, feedback, learning data) are separate rows.

| Table | Holds | Key columns |
| --- | --- | --- |
| `documents` | Uploaded filings | company, sector, doc type, basis, `file_bytes` (the original file), `dataset_json` (the parsed facts) |
| `analyses` | One row per analysis run | `document_id`, `status` (queued/running/succeeded/failed), catalog/KB/prompt versions, `report_json`, error, timestamps |
| `findings` | Findings of each report, for listing and feedback | `analysis_id`, `finding_id` (`A1` / `R-…`), severity, origin, `rule_ids`, `chunk_ids`, status |
| `feedback` | Analyst verdicts | `analysis_id`, `finding_id`, verdict (confirm/dismiss), reason, author |
| `cohort_baselines` | Metric samples for peer-cohort thresholds | metric, sector, size bucket, period kind, value, `document_id` |
| `rule_versions` | Agent-proposed candidate rules | rule id, `definition_json`, status (candidate/approved/rejected), `backtest_result` |

SQLite (zero setup) and Postgres run the same models; SQLite tables are created at startup, Postgres tables by
Alembic.

---

## 8. Tech stack

| Area | Choice | Why |
| --- | --- | --- |
| Language / API | **Python 3.12, FastAPI, Pydantic v2** | Typed request/response models double as the OpenAPI schema the frontend is generated from |
| AI orchestration | **LangGraph** | Explicit nodes and conditional edges (review → revise → recheck) with a deterministic fallback per node; easy to test |
| LLM | **Groq** `openai/gpt-oss-120b` (reasoning) and `gpt-oss-20b` (light tasks) via a custom **httpx** client | Fast, free tier; strict JSON-schema output; own rate-limit budget, response cache and repair loop |
| Retrieval | **BM25** + **fastembed** (local ONNX: `snowflake-arctic-embed-m`, `ms-marco-MiniLM-L-12` cross-encoder), reciprocal rank fusion | No embedding API needed (Groq serves none); free, private, deterministic |
| Numerics | Python **`Decimal`**, a whitelisted **AST** expression language, **numpy** for embeddings | Exact money arithmetic; rules as safe, versioned data |
| Parsing | **defusedxml** (XBRL), **pypdfium2** + **pdfplumber** (PDF) | Safe XML; word-position PDF reading; permissive licences (not PyMuPDF/AGPL) |
| Persistence | **SQLAlchemy 2.0 (async)** + **Alembic**; **Supabase Postgres** (asyncpg) or **SQLite** (aiosqlite) | Same models on both; zero-setup local default |
| Background work | asyncio tasks + worker threads, **Server-Sent Events** | No extra infrastructure for a single-container app |
| Frontend | **Next.js** (App Router), **TypeScript**, **Tailwind CSS**, **shadcn/ui**, **TanStack Query**, **Recharts**, **pdf.js**, **openapi-typescript / openapi-fetch** | Typed end-to-end API client; PDF page rendering for evidence |
| Quality | **pytest** (353 offline tests), **ruff**, ESLint, TypeScript | Everything testable without network or keys |

---

## 9. Repository layout

```
backend/
  app/
    domain/        canonical model: fiscal periods, facts, the dataset, enums
    ingestion/     XBRL parser, filing merge, PDF statement extraction, label mapping, reconciliation
    engine/        expression language, evaluator, catalog loader, rules, anomalies, fact sheet, promotion
    knowledge/     document parsing, chunking, BM25, embeddings, hybrid retriever, retrieval evaluation
    llm/           Groq client, rate-limit budget, response cache, strict-schema structured generation
    agent/         LangGraph state machine, prompts, structured I/O schemas, read-only tools
    analysis/      AnalysisReport, evidence index + citation guardrail, rules-only baseline, orchestration
    db/            SQLAlchemy models, engine/session, cohort baselines, reliability, chunk utility, backtest
    jobs/          background job queue, the analysis job runner, live progress store
    api/           FastAPI app, routers (documents, analyses, rules, health), schemas, auth dependency
    settings.py    configuration (environment / .env)
  alembic/         database migrations (Postgres)
  tests/           unit + end-to-end tests; fixtures are real public filings; no network calls
frontend/
  src/app/         pages: library, document, analysis report, learning console, error / not-found
  src/components/  report view, finding card, evidence sheet + PDF preview, progress panel, shadcn/ui primitives
  src/lib/         typed API client, generated OpenAPI types, formatting, pdf.js helpers
knowledge/         the 49 knowledge-base documents (layer A)
rules/             taxonomy, metrics and rule packs (layer B)
evals/             retrieval evaluation queries
docs/              architecture.drawio — system, pipeline, knowledge/learning loop, data model (diagrams.net)
data/              (gitignored) SQLite DB, LLM response cache, embedding cache, sample filings
```

---

## 10. Testing and evaluation

```bash
cd backend
```
```bash
.venv\Scripts\python -m pytest
```
```bash
.venv\Scripts\ruff check app tests
```

- **353 tests, fully offline** (~30 s). The agent graph runs against a **scripted fake gateway**
  ([`fake_gateway.py`](backend/tests/fake_gateway.py)) that replays model outputs per node — including repair
  sequences and forced failures — so the reasoning logic *and* the whole upload → analyse → report HTTP flow are
  tested without calling Groq.
- **Ingestion tests use real, unmodified public filings** (NSE XBRL results for NOCIL, Paramount Communications and
  KSB; page extracts of the Hyundai, Ola Electric and Rentomojo offer documents), not synthetic data.
- **Retrieval evaluation**: `.venv\Scripts\python -m app.knowledge.evaluation` prints the recall/MRR table above
  (downloads the models once).
- **Frontend**: `npm run lint`, `npx tsc --noEmit` and `npm run build` in `frontend/`.

---

## 11. Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `'.venv\Scripts\python' is not recognized` | You're in the repo root. `cd backend` first. |
| `tenant/user postgres.<ref> not found` on startup | The Supabase project is paused (free tier, ~7 days idle). Restore it in the dashboard. |
| `connection is closed` / `DuplicatePreparedStatementError` | Use Supabase's **transaction pooler** URL (port 6543); the engine is configured for it. |
| First backend start takes ~1 minute | One-time model download and knowledge-base embedding; cached afterwards. |
| `/health` shows `"llm_enabled": false` | `GROQ_API_KEY` isn't set in the repo-root `.env`. Analyses still run, rules-only. |
| An analysis takes several minutes | Groq's free tier allows 8K tokens/minute; the budget waits rather than failing. Re-running the same filing is instant (cached). |
| Report says "planner fell back" or "no rule proposals" | Groq rejected the model's JSON after repairs; that step used its deterministic fallback. The analysis is still complete. |
| Findings marked *"Not reviewed by the critic"* | The review call failed; those findings reached the report without a second opinion. |
| Frontend shows network errors | Backend not running on `http://localhost:8000`, or set `NEXT_PUBLIC_API_BASE_URL` in `frontend/.env.local`. |
| Approved rule doesn't fire | The catalog loads at startup — restart the backend. |

---

## 12. Design decisions and trade-offs

- **Deterministic core, LLM at the edges.** Numbers, rules and ingestion are pure code: reproducible, testable and
  auditable. The LLM adds judgement and narrative, and every step has a fallback.
- **Rules as YAML, not code**, so analysts can add checks without a deploy; the catalog validates them at load.
- **Structured fact sheet instead of raw PDFs for the model** — cheaper, citable, and far less exposed to prompt
  injection hidden in a filing.
- **In-process job queue** rather than Celery/Redis: one container, and Groq's rate limit is the real bottleneck.
  The cost: a restart fails in-flight jobs (they're marked failed, not lost silently).
- **Local embedding models** instead of an embedding API: free and private; the cost is ~1 GB RAM for retrieval and
  a one-time download.
- **Report stored as one JSON document**: reports are immutable; only mutable/queryable parts are rows.
- **Uploaded files stored in the database**: simplest possible setup; object storage would scale better.

## 13. Limitations and roadmap

- Annual-report and quarterly-results **PDFs** aren't parsed yet (use the XBRL filing); no OCR for scanned PDFs.
- No user accounts: `API_KEY` is a single shared key; feedback authorship isn't verified.
- The job queue isn't durable, and progress lives in one process — horizontal scaling needs a real queue.
- Agent output quality isn't evaluated automatically yet (retrieval is); a golden-set evaluation per fixture is the
  next step.
- No frontend tests yet.
- Groq occasionally rejects the planner's / proposer's JSON; those steps then fall back deterministically.

---

## 14. Deploying (optional)

The repo runs fully locally; these are the steps for a hosted setup.

- **Backend → any Docker host** (e.g. Render). The repo-root [`Dockerfile`](Dockerfile) builds from the repo root
  (it needs `rules/` and `knowledge/`), runs `alembic upgrade head` on every start, and listens on `$PORT`. Set
  `GROQ_API_KEY` and `DATABASE_URL` as environment variables; [`render.yaml`](render.yaml) is an optional Render
  Blueprint. The image installs the backend **without** the `embeddings` extra so it fits a 512 MB free instance;
  retrieval then falls back to BM25-only (84.4% recall@1 instead of 98.4%) — install `"./backend[embeddings]"` on a
  larger instance to restore it.
- **Frontend → Vercel**: import the repo, set **Root Directory** to `frontend`, and set
  `NEXT_PUBLIC_API_BASE_URL` to the backend URL. Then narrow the backend's `CORS_ORIGINS` to the frontend URL.
- With no `API_KEY`, anyone with the URL can trigger analyses (bounded by Groq's free-tier limits); set one for
  anything beyond a demo.
