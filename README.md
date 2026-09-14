# Self-Learning AI Financial Analyst Agent

An AI analyst for Indian market filings: SEBI DRHP / RHP offer documents, Regulation 33 quarterly results and
annual reports. It reads a filing, checks it the way an experienced analyst would, and produces explainable
findings (red flags, anomalies, data gaps, questions for management), each backed by cited evidence. It is built
as a module that a larger fintech platform can call.

> **Status:** feature-complete through the full roadmap — engine, knowledge base, ingestion, the live agent,
> persistence/API, the learning service, and this web app.
> **Backend:** https://fin-analyst-agent.onrender.com · **Frontend:** `<Vercel URL>` (deploy it — see
> [Deploying](#deploying) — then fill in this link).

## Contents

- [Quickstart](#quickstart) — get it running on your machine
- [Configuration](#configuration) — every environment variable, and using Supabase instead of SQLite
- [How it works](#how-it-works)
  - [Core principle: the LLM never produces a number](#core-principle-the-llm-never-produces-a-number)
  - [Agent: plan → investigate → review → compose → propose](#agent-plan--investigate--review--compose--propose)
  - [Architecture](#architecture)
  - [Knowledge base: three layers](#knowledge-base-three-layers)
  - [Semantic knowledge base and retrieval](#semantic-knowledge-base-and-retrieval)
  - [How "self-learning" works (no fine-tuning)](#how-self-learning-works-no-fine-tuning)
  - [What the engine does today](#what-the-engine-does-today)
  - [Ingestion: from a filing to the canonical dataset](#ingestion-from-a-filing-to-the-canonical-dataset)
- [REST API reference](#rest-api-reference)
- [Frontend](#frontend)
- [Repository layout](#repository-layout)
- [Testing](#testing)
- [Deploying](#deploying)
- [Tech stack](#tech-stack)

## Quickstart

**Prerequisites:** Python 3.12, Node.js 20+, and a free [Groq API key](https://console.groq.com/keys) (Docker
is only needed if you want to build/run the deployment image locally — see [Deploying](#deploying)).

```bash
git clone <this-repo-url>
cd fin-analyst-agent
```

**1. Backend**

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev,embeddings]"   # macOS / Linux: .venv/bin/python
.venv/Scripts/python -m pytest                               # 327 tests, offline — see Testing

echo "GROQ_API_KEY=gsk_..." > ../.env                        # repo root, not backend/ — never commit it

.venv/Scripts/python -m uvicorn app.api.main:app --reload    # http://127.0.0.1:8000/docs
```

With no further configuration this persists to a local SQLite file at `data/app.db` (created on first
startup) — nothing else to set up. Without `GROQ_API_KEY` the app still runs and analyses still complete,
just rules-only (no agent-authored findings); see [Configuration](#configuration) for every other setting,
including pointing this at Supabase Postgres instead of SQLite.

**2. Frontend** (in a second terminal)

```bash
cd frontend
npm install
npm run dev                                                  # http://localhost:3000
```

**3. Try it** — open `http://localhost:3000`, click **Upload filing**, and pick one of the real fixtures
already in the repo (`backend/tests/fixtures/xbrl/nocil_q2fy25_standalone.xml` is a good first try — a real
NSE quarterly result). Click **Run analysis**, watch the live progress, then read the report. See
[Frontend](#frontend) for a tour of the rest of the app.

## Configuration

Settings are read from environment variables, or a `.env` file at the **repository root**
([`app/settings.py`](backend/app/settings.py) via `pydantic-settings`; never commit this file).

| Variable | Default | Purpose |
| --- | --- | --- |
| `GROQ_API_KEY` | unset | Enables the agent. Without it, analyses still complete, rules-only (no LLM). |
| `DATABASE_URL` | local SQLite file (`data/app.db`) | Postgres connection string for production; see below to point it at Supabase. |
| `CORS_ORIGINS` | `*` | Comma-free list (JSON array) of allowed frontend origins; tighten this once you have a deployed frontend URL. |
| `API_KEY` | unset | When set, `POST`/mutating endpoints require `Authorization: Bearer <key>`. Left unset on the live deployment by design — see [Deploying](#deploying). |
| `MAX_CONCURRENT_ANALYSES` | `2` | How many analyses the in-process job queue runs at once. |
| `NEXT_PUBLIC_API_BASE_URL` (frontend, `.env.local` in `frontend/`) | `http://localhost:8000` | Where the frontend looks for the backend. |

### Using Supabase Postgres instead of SQLite

1. Create a free project at [supabase.com](https://supabase.com) and open **Project Settings → Database →
   Connection string**, **"Transaction" pooler** tab (port 6543). Supabase's *direct* connection host
   (port 5432) is IPv6-only and won't resolve on a network without IPv6 egress; the pooler host
   (`aws-0-<region>.pooler.supabase.com`, username `postgres.<project-ref>`) works everywhere.
2. Add it to `.env` — the plain `postgresql://` URI Supabase gives you is fine as-is:
   ```
   DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```
   (`app/settings.py` upgrades it to `postgresql+asyncpg://` automatically.)
3. Apply the schema: `.venv/Scripts/python -m alembic upgrade head`.

The app reads `DATABASE_URL` on startup and works identically either way — switching databases is just that
one environment variable plus running the migration. Because the pooler runs in transaction mode, the engine
(`app/db/engine.py`) disables asyncpg's prepared-statement cache (`statement_cache_size=0`); without that,
queries intermittently fail with `DuplicatePreparedStatementError` once the pooler reuses a server
connection under a different client statement cache. The engine also sets `pool_pre_ping=True` and a
`pool_recycle` under the pooler's own idle timeout, since it silently closes connections it considers
idle — without those, a request can hit `asyncpg.exceptions.InterfaceError: connection is closed`.

## How it works

### Core principle: the LLM never produces a number

Language models are good at judgement and bad at arithmetic. So the system is split along that line:

| Deterministic engine (no LLM) | Agent (LLM) |
| --- | --- |
| Extracts and normalises reported figures | Decides which lines of enquiry matter for this filing |
| Checks statements reconcile | Interprets what a red flag means in context |
| Computes ~60 metrics with exact decimal arithmetic | Weighs benign explanations the filing gives |
| Evaluates rule packs and detects statistical anomalies | Writes the analyst narrative and questions |

The agent cites values as references, such as `{{m:dso@FY25}}`, and the server substitutes the verified figure. A
reference that does not exist is rejected, so a hallucinated number cannot reach an analysis. Two more checks
close the loop: [`bare_figures`](backend/app/engine/references.py) scans every sentence the model writes for a
number that was not put in as a citation (`"55%"`, `"₹80 crore"`, `"1.8x"`) and rejects it, forcing a repair; and
[`link_figures`](backend/app/analysis/evidence.py) rewrites a figure the model copies from its own context back
into the reference it came from, when exactly one reference has that value, so a correct number the model typed
by hand is turned into a citation rather than punished.

### Agent: plan → investigate → review → compose → propose

[`app/agent/graph.py`](backend/app/agent/graph.py) is a [LangGraph](https://github.com/langchain-ai/langgraph)
state machine over Groq's `openai/gpt-oss-120b` (reasoning) and `openai/gpt-oss-20b` (lighter structured tasks),
called through a small OpenAI-compatible gateway ([`app/llm/`](backend/app/llm)) built for the free tier's 8,000
tokens/minute-per-model limit:

- **`TokenBudget`** ([`budget.py`](backend/app/llm/budget.py)) reads Groq's `x-ratelimit-*` response headers and
  blocks the next call rather than firing into a 429; a 429 that does happen updates the same budget from
  `Retry-After`.
- **`ResponseCache`** ([`cache.py`](backend/app/llm/cache.py)) keys on the full request (model, messages, schema),
  so re-analysing the same filing costs nothing and tests are reproducible.
- **`generate()`** ([`structured.py`](backend/app/llm/structured.py)) converts a Pydantic model into the strict
  JSON-schema dialect Groq's gpt-oss models support (every property required, `additionalProperties: false`,
  optionals as nullable types, no `$ref`), and repairs a response up to twice — once for the provider's own
  schema rejection, once for a Pydantic or domain validation failure — by showing the model exactly what was
  wrong.

The graph itself, per filing:

1. **Plan** — the model reads the ~1.1K-token fact sheet and groups the fired rules and anomalies into up to five
   non-overlapping lines of enquiry, or dismisses a flag as immaterial with a stated reason.
2. **Investigate** — one call per enquiry, given only that enquiry's fired rules, evidence and matching knowledge
   excerpts. The model can request up to three tool lookups first (`metric_history`, `knowledge_search`,
   `rule_detail` — read-only, deterministic) before answering, or return `no_finding` with a reason.
3. **Review** — a single call critiques every draft finding at once: `accept`, `revise` (with concrete
   instructions), or `reject`, and sets the final severity.
4. **Revise** — findings sent back for revision get one more analyst turn with the reviewer's instructions
   attached.
5. **Compose** — the accepted, reviewed findings become a headline, an overall risk level, and executive
   strengths/concerns.
6. **Propose** — findings with no matching rule and unexplained anomalies are turned into at most two candidate
   rules in the same expression language as the rule packs; each is parsed and checked against the catalog
   before it is ever shown, so an invalid proposal is dropped rather than displayed. A human can then backtest a
   candidate against every stored filing and approve or reject it from the Learning Console — see
   [How "self-learning" works](#how-self-learning-works-no-fine-tuning).

Every node falls back to a deterministic result if its model call ultimately fails (grouped rule findings instead
of a plan, the rule-only summary instead of a composed one), so **an analysis always completes** — with or
without a working LLM. [`app/analysis/service.py`](backend/app/analysis/service.py) merges the agent's findings
with plain rule findings for anything the agent didn't cover into the versioned `AnalysisReport`
([`report.py`](backend/app/analysis/report.py)), which records `catalog_version`, `kb_version` and a hash of the
prompts (`prompt_version`) so a report can be reproduced.

Run live against the manufacturing fixture (Groq `gpt-oss-120b`, cold — no cache), the agent turned the five
fired rules into three specific, evidence-linked findings (grouping the two cash-conversion rules into one
enquiry), correctly identified and rejected an unsupported factoring explanation, asked for receivables ageing
by customer, and every figure in every finding, in the executive summary and in the strengths/concerns resolved
to a citation. Two safe fallbacks fired in that same run — the planner's JSON was rejected by Groq once and fell
back to grouping fired rules by category, and one proposed rule referenced a non-existent evidence name and was
dropped — both by design, and the analysis still completed correctly either way.

### Architecture

The full design is in [`docs/architecture.drawio`](docs/architecture.drawio). Open it in
[diagrams.net](https://app.diagrams.net) or the VS Code Draw.io extension. It has four pages — system
architecture, analysis pipeline & agent, knowledge base & self-learning loop, and integration contract & data
model — kept in sync with what's actually built (a solid border means implemented and tested; a dashed one
means an external service or a still-unbuilt integration point).

```
Filing ─► Ingestion ─► Financial engine ─► Fact sheet ─► Agent (planner → analyst → critic) ─► AnalysisReport
          (PDF/XBRL)   (metrics, rules,     (~1.1K       (Groq gpt-oss-120b/20b, KB retrieval)
                        anomalies)          tokens)
                              ▲                                        │
                              └────────── Learning service ◄───────────┘  analyst feedback
                                          (cohort baselines, rule reliability, proposed rules)
```

### Knowledge base: three layers

| Layer | Answers | Form | Status |
| --- | --- | --- | --- |
| **A. Semantic** | *Why does this matter?* | 49 curated markdown documents ([`knowledge/`](knowledge)) with hybrid retrieval | **done** |
| **B. Rules** | *What should be checked?* | Versioned YAML: taxonomy, metrics, rule packs ([`rules/`](rules)) | **done** |
| **C. Experience** | *What have we learned?* | Cohort baselines, feedback-calibrated rule reliability, chunk utility, candidate rules | **done** |

Rules are data, not code: an analyst can add a check by writing YAML, and the catalog validates every expression
at load time.

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

### Semantic knowledge base and retrieval

[`knowledge/`](knowledge) holds 49 documents written for this project: one for every `kb:` reference in the rules
(a test enforces this), guides to reading quarterly results, annual reports, DRHPs / RHPs and the three
statements, an analyst working method, and sector primers (banks, NBFCs, IT services, infrastructure / EPC,
manufacturing, pharma, FMCG, real estate, consumer internet). Every document uses the same sections — *what it
measures, how to read it, red flags, benign explanations, where to find it in Indian filings, questions for
management* — and each section becomes one chunk tagged with its kind. That lets the critic ask for **only benign
explanations** of a red flag, and lets the agent fetch the document a fired rule names directly, with no ranking.

Open questions go through hybrid retrieval ([`retriever.py`](backend/app/knowledge/retriever.py)): metadata
filters (document type, sector, section kind) → BM25 with finance abbreviation expansion (DSO, CFO, OFS, GCP…) and
local dense embeddings (`bge-small`, via fastembed/ONNX, since Groq serves no embedding models) → reciprocal rank
fusion → cross-encoder rerank of the top 8 → learned per-chunk utility boost from the learning service.

Measured on [`evals/retrieval.yaml`](evals/retrieval.yaml): 64 queries phrased as the agent asks them, many
paraphrased away from the documents' wording. Scores are document-level; run `python -m app.knowledge.evaluation`.

| Configuration | Recall@1 | Recall@3 | MRR |
| --- | --- | --- | --- |
| Lexical (BM25) | 84.4% | 95.3% | 0.905 |
| Dense (bge-small) | 73.4% | 87.5% | 0.816 |
| Hybrid (RRF) | 89.1% | 95.3% | 0.932 |
| **Hybrid + rerank (default)** | **93.8%** | **98.4%** | **0.961** |

Reranking all 20 fused candidates cost about 2 s per query on CPU with no quality gain over reranking the top 8
(about 0.6 s). Caveat: the documents and the queries were written by the same author, which likely flatters
lexical scores; queries from real analyst usage will be added as the system is used. The deployed backend runs
lexical-only by default (see [Deploying](#deploying)) — a documented trade-off for fitting in 512 MB of RAM.

### How "self-learning" works (no fine-tuning)

Thresholds can reference the company's peer cohort (sector × size band) instead of fixed numbers, for example
`dso > cohort_pct(dso, 90, 120)`. As filings are analysed, cohort baselines fill in and the same rule adapts:
98 days of receivables is flagged among fast-collecting manufacturers but not among slow-collecting ones
([`test_cohort_learning.py`](backend/tests/test_cohort_learning.py)). Until a cohort has enough observations, the
static fallback is used and the engine says so. Cohort baselines persist across filings (`app/db/baselines.py`);
three more feedback-driven loops sit on top of it, all backed by the `feedback` table an analyst's confirm/dismiss
verdicts land in (from the report UI's **Confirm** / **Dismiss** buttons, or `POST
/analyses/{id}/findings/{finding_id}/feedback`):

- **Rule reliability** (`app/db/reliability.py`) — a Beta-calibrated confidence per rule, starting at the
  static 0.6 and shifting as verdicts accumulate (`GET /rules/reliability`, or the Learning Console page).
- **Knowledge-chunk utility** (`app/db/chunk_utility.py`) — chunks that supported a confirmed finding get
  boosted in future retrieval, dismissed ones demoted; this is the "learned utility boost hook"
  `HybridRetriever.search()` has carried since step 2.
- **Rule backtesting and promotion** (`app/db/backtest.py`, `app/engine/promotion.py`) — a candidate the
  agent's rule-proposer emits can be backtested against every stored filing
  (`POST /rules/candidates/{id}/backtest`) before a human approves it; approving writes it into
  `rules/packs/learned/proposed.yaml` for review and commit, active on the next restart. All of this is also
  reachable from the frontend's Learning Console.

### What the engine does today

Given a canonical dataset for a filing, [`run_engine`](backend/app/engine/pipeline.py) returns:

- **Metrics:** 59 metrics (margins, growth, DSO/DIO/DPO, cash conversion, leverage, returns, offer-document
  valuation), each with the facts it used and notes (for example, "no opening balance, closing balance used").
- **Rule results:** 55 rules in 4 packs (`integrity`, `core`, `quarterly`, `offer_document` — plus `learned`
  once a candidate rule is approved), each **fired**, **passed** or **insufficient data** (with the exact
  missing inputs) for every applicable period, so persistence across years is visible. Severity escalates
  when compounding conditions hold.
- **Anomalies:** robust (median / MAD) outliers against the company's own history and its cohort.
- **Fact sheet:** a compact, fully citable text rendering for the agent (about 1.1K tokens for a 3-year annual
  report).

Design choices that matter for correctness:

- **Missing is not zero.** Boolean logic is three-valued (Kleene), so a rule reports "insufficient data" instead
  of silently passing. Lines that Schedule III only requires when an amount exists (exceptional items,
  borrowings, CWIP) are explicitly marked `nil_if_absent` in the taxonomy.
- **Indian conventions.** April–March fiscal years (`FY25`, `Q3FY25`, `H1FY25`, `9MFY25`), ₹ crore with lakh-crore
  digit grouping, Ind AS / Schedule III line items, SEBI ICDR thresholds.
- **Safe expressions.** Rules are parsed into a whitelisted AST and interpreted; nothing reaches `eval`.
- **Reproducibility.** The catalog has a content hash, recorded with every analysis.

### Ingestion: from a filing to the canonical dataset

Every source becomes the same [`FinancialDataset`](backend/app/domain/financials.py), so a new format never touches
analysis code. Each fact keeps its source (XBRL element, or PDF page and printed label). Ingestion has no LLM
involved at all — the whole path from raw file to canonical dataset is deterministic.

**NSE / BSE XBRL results** ([`xbrl.py`](backend/app/ingestion/xbrl.py)) — the highest-fidelity input. Element
mappings live in the taxonomy next to each line item. Built against unmodified public filings (NOCIL, Paramount
Communications, KSB; see [`tests/fixtures/xbrl`](backend/tests/fixtures/xbrl)), which revealed what a
specification alone would not: values in full rupees whatever rounding is declared, cash outflows tagged as
positive numbers, context dates that contradict the reporting period stated inside them, a January–December
fiscal year, and opening and closing cash told apart only by position. One filing holds only the current quarter
and year to date, so [`merge.py`](backend/app/ingestion/merge.py) combines filings for comparatives and flags any
figure a later filing restates.

**DRHP / RHP PDFs** ([`pdf/`](backend/app/ingestion/pdf)) — restated statements are located by their headings and
read from word positions rather than extracted text, because character spacing splits numbers (`1 5,647.23`) and
period headers wrap across lines. Columns are found from the right edges of the amounts, rows are mapped to the
taxonomy with section context ("Borrowings" under current or non-current liabilities) and sub-rows add up to their
header. Presentation differences are then corrected using the statements' own arithmetic
([`reconcile.py`](backend/app/ingestion/reconcile.py)): "total expenses" shown before finance costs and
depreciation, exceptional losses printed as positive amounts, and tax shown as "(expense) / credit".

Results on three real offer documents — every fact traced to a page, every integrity check passing except where
the filing itself explains the difference:

| Document | Periods | Facts | Integrity checks |
| --- | --- | --- | --- |
| Hyundai Motor India RHP | FY22–FY24, Q1FY24, Q1FY25 | 285 | all pass |
| Ola Electric Mobility RHP | FY22–FY24 | 180 | all pass except cash flow vs balance sheet cash (bank overdraft netted in the cash flow statement) |
| Rentomojo DRHP | FY23–FY25, H1FY26 | 212 | all pass except EPS growth vs profit growth (share count changed) |

Not yet covered: annual report and quarterly results PDFs (use the XBRL filing), and offer-structure facts (issue
size, offer for sale, objects), which in an RHP are largely blank until the price band is fixed and are left to the
agent's document tools. PDF parsing uses pdfium and pdfplumber (permissive licences) rather than PyMuPDF (AGPL).

## REST API reference

The FastAPI app exposes:

| Method & path | Does |
| --- | --- |
| `POST /documents` | Upload a filing (multipart `file`, optional `sector`/`source_url`); ingests it deterministically and stores the parsed dataset. |
| `GET /documents`, `GET /documents/{id}` | List / fetch an uploaded document. |
| `GET /documents/{id}/file` | Raw uploaded bytes — the evidence viewer's PDF source. |
| `POST /documents/{id}/analyses` | Queue an analysis job (engine + agent) for that document. |
| `GET /analyses/{id}`, `GET /analyses` | Poll an analysis's status, and the full `AnalysisReport` once it succeeds. |
| `GET /analyses/{id}/events` | Server-sent events with live progress (stage/message) until it finishes. |
| `GET /analyses/{id}/findings` | The report's findings, normalized for listing/filtering. |
| `POST /analyses/{id}/findings/{finding_id}/feedback` | Record an analyst's confirm/dismiss verdict. |
| `GET /rules/candidates` | List agent-proposed candidate rules, optionally filtered by status. |
| `POST /rules/candidates/{id}/backtest` | Run a candidate against every stored filing and persist the result. |
| `POST /rules/candidates/{id}/decision` | Approve (promotes it into `rules/packs/learned/`) or reject. |
| `GET /rules/reliability` | Feedback-calibrated confidence per rule that has at least one verdict. |
| `GET /health` | Catalog/knowledge-base/DB readiness. |

Full request/response schemas are on the OpenAPI UI at `/docs` (or `/openapi.json`) once the backend is running.

A background analysis job runs off the request thread (`app/jobs/`); a process restart marks any job still
`queued`/`running` as failed rather than leaving it stuck (there's no durable queue — a single container and
Groq's own free-tier rate limit make one unnecessary for now).

Approving a candidate rule writes it to `rules/packs/learned/proposed.yaml` — an ordinary, uncommitted file
like every other rule pack, for review before committing — but the *running* app only loads the catalog
once at startup, so it takes a restart to actually start firing.

## Frontend

Next.js (App Router) app in [`frontend/`](frontend) — see [Quickstart](#quickstart) to run it. Pages:

- **`/`** — document library and upload dialog.
- **`/documents/[id]`** — a filing's detail and analysis history, with a "Run analysis" button.
- **`/analyses/[id]`** — the full report: findings by severity with clickable evidence chips, a PDF evidence
  viewer (jumps to the cited page via pdf.js), a reasoning trace of every LLM call, scorecard/metrics/
  candidate rules, and confirm/dismiss feedback on each finding.
- **`/learning`** — the console for feedback-calibrated rule reliability and reviewing/backtesting/approving
  agent-proposed rules.

Request/response types come from the backend's own OpenAPI schema (`npm run gen:types` inside `frontend/`,
backend must be running) rather than hand-maintained duplicates that could drift.

## Repository layout

```
backend/
  app/
    domain/        canonical model: fiscal periods, facts, dataset, enums
    engine/        expression language, evaluator, catalog loader, rules, anomalies, fact sheet
    knowledge/     document parsing, chunking, BM25, embeddings, hybrid retriever, evaluation
    ingestion/     XBRL results, merging filings, PDF statement extraction, label mapping, reconciliation
    llm/           Groq client, rate-limit budget, response cache, strict-schema structured generation
    agent/         LangGraph state machine, prompts, structured I/O schemas, read-only tools
    analysis/      AnalysisReport, evidence index and citation guardrail, rules-only baseline, orchestration
    db/            SQLAlchemy models, async engine/session, cohort-baseline persistence
    jobs/          in-process async job queue, the analysis job runner, live progress store
    api/           FastAPI app: document/analysis/rules/health routers, request/response schemas
  alembic/         migration environment and versions (Postgres in production; tests bypass this)
  tests/           unit and end-to-end tests; fixtures from real public filings, no LLM network calls
knowledge/         knowledge base layer A: 49 curated documents
evals/             retrieval evaluation set
rules/             knowledge base layer B
  taxonomy/        97 canonical line items with filing-label aliases and XBRL element mappings
  metrics/         metric formulas
  packs/           integrity, core, quarterly, offer_document, learned (agent-promoted) rule packs
frontend/          Next.js web app — library, analysis report, evidence viewer, learning console
docs/
  architecture.drawio
Dockerfile           backend image for Render (build context is the repo root, see Deploying)
render.yaml          optional Render Blueprint (same setup, pre-filled)
```

## Testing

```bash
cd backend
.venv/Scripts/python -m pytest        # 327 tests
.venv/Scripts/ruff check app tests
```

Everything runs offline — no network calls, no model downloads. The agent graph is exercised through a
scripted fake gateway ([`test_agent_graph.py`](backend/tests/test_agent_graph.py), reused by the API tests
in `test_api_analyses.py`), so both the reasoning logic and the full upload → analyze → report HTTP flow are
tested without ever calling Groq. Ingestion tests run against real, unmodified public filings (the XBRL and
PDF fixtures referenced throughout this README), not synthetic data. `python -m app.knowledge.evaluation`
runs the retrieval ablation in the table above (downloads the embedding/reranking models once, on first run).

## Deploying

**Backend → Render (Docker web service, free tier).** Render's Docker deploys read the repo-root
`Dockerfile` directly — no Render-specific files needed for the manual path; `render.yaml` at the repo root
is an optional Blueprint that can pre-fill the same setup (New → Blueprint in Render's dashboard) if it
picks it up cleanly, with the manual steps below as the fallback either way.

1. [New → Web Service](https://dashboard.render.com/select-repo?type=web) → connect this GitHub repo.
2. **Runtime: Docker**. Leave **Dockerfile Path** as `Dockerfile` and **Docker Build Context Directory** as
   `.` (repo root) — Render should detect both automatically since they're at the default locations.
3. **Instance type: Free**. Add environment variables `GROQ_API_KEY` and `DATABASE_URL` (the same values as
   your local `.env`) in the service's **Environment** tab — never commit them.
4. Deploy. Render assigns the port via its own `$PORT` env var, which the Dockerfile's `CMD` already reads
   (`--port ${PORT:-8000}`), and runs `alembic upgrade head` on every start so the schema stays current with
   no manual step. Watch the **Logs** tab for the first build (~1–2 minutes to install dependencies).
5. Once it's live, verify `https://<service-name>.onrender.com/health` returns `{"status": "ok", ...}`.

The free tier's 512 MB RAM is why the deployed image installs the backend **without** the `embeddings`
extra (see the Dockerfile) — `fastembed`'s ONNX models push memory well past what fits comfortably. The
knowledge base's own loader (`app/api/main.py`'s `_load_knowledge_base`) falls back to lexical-only (BM25)
search automatically when fastembed isn't installed — a real, measured retrieval-quality trade-off
(`evals/retrieval.yaml`), not silently accepted: hybrid+rerank scores 93.8%/98.4% Recall@1/3 versus lexical
alone, still usable but not the ceiling this project is capable of. A paid instance with more RAM can
restore it by installing `"./backend[embeddings]"` in the Dockerfile instead. (Hugging Face Spaces was the
original pick here for its generous free-tier RAM, but Spaces now gates the Docker SDK behind a paid PRO
plan — Render's free Docker web services don't have that restriction as of this writing.)

**Frontend → Vercel.**

1. [Import the GitHub repo](https://vercel.com/new) as a new project.
2. Set **Root Directory** to `frontend` (Vercel auto-detects Next.js from there — no other build config
   needed).
3. Add the environment variable `NEXT_PUBLIC_API_BASE_URL` = your Render service's URL from above
   (`https://<service-name>.onrender.com`).
4. Deploy. Once you have the Vercel URL, optionally tighten the backend's `CORS_ORIGINS` env var on Render
   from the default `*` to that exact URL (`Settings.cors_origins`, `app/settings.py`) — left open by default
   so local development and quick testing aren't blocked by it.

Fill in both URLs at the top of this README once live. No `API_KEY` is set on the deployed backend by
design, so a reviewer can use the live app without a shared secret — the trade-off (documented here rather
than silently accepted, the same as the job queue's no-durable-queue trade-off above) is that anyone with the
URL can trigger an analysis; Groq's own free-tier rate limits bound how much that could cost. Render's free
tier also spins the service down after periods of inactivity — the first request after a while can take
~30–60 seconds to wake it back up; this is normal, not a bug.

## Tech stack

| Area | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| Agent | LangGraph, Groq `openai/gpt-oss-120b` (reasoning) and `openai/gpt-oss-20b` (classification, label mapping); httpx client with strict JSON-schema output, a rate-limit budget and a response cache |
| Knowledge base | BM25 + fastembed (local ONNX embeddings and cross-encoder reranking), reciprocal rank fusion |
| Persistence | SQLAlchemy 2.0 (async) + Alembic; Supabase Postgres in production, SQLite with zero setup otherwise |
| Parsing | defusedxml for NSE/BSE XBRL, pypdfium2 and pdfplumber for PDFs |
| Frontend | Next.js (App Router), TypeScript, Tailwind, shadcn/ui, TanStack Query, Recharts, pdf.js; types generated from the backend's OpenAPI schema via openapi-typescript/openapi-fetch |
| Hosting | Render (Docker backend, free tier), Vercel (frontend) |
