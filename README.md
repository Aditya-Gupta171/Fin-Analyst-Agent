# Self-Learning AI Financial Analyst Agent

An AI analyst for Indian market filings: SEBI DRHP / RHP offer documents, Regulation 33 quarterly results and
annual reports. It reads a filing, checks it the way an experienced analyst would, and produces explainable
findings (red flags, anomalies, data gaps, questions for management), each backed by cited evidence. It is built
as a module that a larger fintech platform can call.

> **Status:** in development. The deterministic financial engine and the rules knowledge base (layer B) are
> complete and tested. The agent, semantic knowledge base, learning service, API and web app are next
> ([roadmap](#roadmap)).

## Core principle: the LLM never produces a number

Language models are good at judgement and bad at arithmetic. So the system is split along that line:

| Deterministic engine (no LLM) | Agent (LLM) |
| --- | --- |
| Extracts and normalises reported figures | Decides which lines of enquiry matter for this filing |
| Checks statements reconcile | Interprets what a red flag means in context |
| Computes ~60 metrics with exact decimal arithmetic | Weighs benign explanations the filing gives |
| Evaluates rule packs and detects statistical anomalies | Writes the analyst narrative and questions |

The agent cites values as references, such as `{{m:dso@FY25}}`, and the server substitutes the verified figure. A
reference that does not exist is rejected, so a hallucinated number cannot reach an analysis.

## Architecture

The full design is in [`docs/architecture.drawio`](docs/architecture.drawio). Open it in
[diagrams.net](https://app.diagrams.net) or the VS Code Draw.io extension. It has four pages: system
architecture, analysis pipeline, knowledge base and self-learning loop, and integration contract and data model.

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
| **A. Semantic** | *Why does this matter?* | Curated markdown (Ind AS, SEBI ICDR/LODR, analyst playbooks) indexed in pgvector | planned |
| **B. Rules** | *What should be checked?* | Versioned YAML: taxonomy, metrics, rule packs ([`rules/`](rules)) | **done** |
| **C. Experience** | *What have we learned?* | Cohort baselines, feedback, precedents, rule reliability | interfaces done |

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

### How "self-learning" works (no fine-tuning)

Thresholds can reference the company's peer cohort (sector × size band) instead of fixed numbers, for example
`dso > cohort_pct(dso, 90, 120)`. As filings are analysed, cohort baselines fill in and the same rule adapts:
98 days of receivables is flagged among fast-collecting manufacturers but not among slow-collecting ones
([`test_cohort_learning.py`](backend/tests/test_cohort_learning.py)). Until a cohort has enough observations, the
static fallback is used and the engine says so. Analyst feedback, precedent memory and agent-proposed rules build
on this (see page 3 of the architecture diagram).

## What the engine does today

Given a canonical dataset for a filing, [`run_engine`](backend/app/engine/pipeline.py) returns:

- **Metrics:** 59 metrics (margins, growth, DSO/DIO/DPO, cash conversion, leverage, returns, offer-document
  valuation), each with the facts it used and notes (for example, "no opening balance, closing balance used").
- **Rule results:** 55 rules in 4 packs (`integrity`, `core`, `quarterly`, `offer_document`), each **fired**,
  **passed** or **insufficient data** (with the exact missing inputs) for every applicable period, so persistence
  across years is visible. Severity escalates when compounding conditions hold.
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

## Repository layout

```
backend/
  app/
    domain/        canonical model: fiscal periods, facts, dataset, enums
    engine/        expression language, evaluator, catalog loader, rules, anomalies, fact sheet
  tests/           unit and end-to-end tests with annual, quarterly and RHP fixtures
rules/             knowledge base layer B
  taxonomy/        93 canonical line items with filing-label aliases
  metrics/         metric formulas
  packs/           integrity, core, quarterly, offer_document rule packs
docs/
  architecture.drawio
```

## Running locally

Requires Python 3.12.

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # macOS / Linux: .venv/bin/python
.venv/Scripts/python -m pytest
.venv/Scripts/ruff check app tests
```

## Tech stack

| Area | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| Agent | LangGraph, Groq `openai/gpt-oss-120b` (reasoning) and `openai/gpt-oss-20b` (classification, label mapping) |
| Knowledge base | Supabase Postgres with pgvector and full-text search, fastembed (local embeddings and reranking) |
| Parsing | PyMuPDF, Docling, lxml for NSE/BSE XBRL |
| Frontend | Next.js, TypeScript, Tailwind, shadcn/ui, Recharts, pdf.js |
| Hosting | Hugging Face Spaces (Docker backend), Vercel (frontend) |

## Roadmap

1. ~~Deterministic engine, taxonomy, metric registry, rule packs, fact sheet~~
2. Semantic knowledge base (layer A): curated content, chunking, hybrid retrieval
3. Ingestion: NSE/BSE XBRL, PDF section routing and table extraction, label mapping
4. Agent: planner, analyst with tools, critic, report composer, rule proposer
5. Persistence, job queue and REST API with webhooks
6. Learning service: cohort baselines, feedback, rule backtesting
7. Web app: library, upload, report with evidence viewer, reasoning trace, learning console
8. Sample corpus of public Indian filings, evaluation set, deployment
