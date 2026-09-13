# Self-Learning AI Financial Analyst Agent

An AI analyst for Indian market filings: SEBI DRHP / RHP offer documents, Regulation 33 quarterly results and
annual reports. It reads a filing, checks it the way an experienced analyst would, and produces explainable
findings (red flags, anomalies, data gaps, questions for management), each backed by cited evidence. It is built
as a module that a larger fintech platform can call.

> **Status:** in development. Complete, tested and evaluated: the deterministic financial engine, both static
> knowledge-base layers (rules catalog and semantic knowledge base with hybrid retrieval), and ingestion of NSE /
> BSE XBRL results and DRHP / RHP restated financial statements. The agent, learning service, API and web app are
> next ([roadmap](#roadmap)).

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
| **A. Semantic** | *Why does this matter?* | 49 curated markdown documents ([`knowledge/`](knowledge)) with hybrid retrieval | **done** |
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
lexical scores; queries from real analyst usage will be added as the system is used.

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

## Ingestion: from a filing to the canonical dataset

Every source becomes the same [`FinancialDataset`](backend/app/domain/financials.py), so a new format never touches
analysis code. Each fact keeps its source (XBRL element, or PDF page and printed label).

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

## Repository layout

```
backend/
  app/
    domain/        canonical model: fiscal periods, facts, dataset, enums
    engine/        expression language, evaluator, catalog loader, rules, anomalies, fact sheet
    knowledge/     document parsing, chunking, BM25, embeddings, hybrid retriever, evaluation
    ingestion/     XBRL results, merging filings, PDF statement extraction, label mapping, reconciliation
  tests/           unit and end-to-end tests; fixtures from real public filings
knowledge/         knowledge base layer A: 49 curated documents
evals/             retrieval evaluation set
rules/             knowledge base layer B
  taxonomy/        97 canonical line items with filing-label aliases and XBRL element mappings
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
.venv/Scripts/python -m pip install -e ".[dev,embeddings]"   # macOS / Linux: .venv/bin/python
.venv/Scripts/python -m pytest                               # no model downloads needed
.venv/Scripts/ruff check app tests
.venv/Scripts/python -m app.knowledge.evaluation             # retrieval ablation; downloads models once
```

## Tech stack

| Area | Choice |
| --- | --- |
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| Agent | LangGraph, Groq `openai/gpt-oss-120b` (reasoning) and `openai/gpt-oss-20b` (classification, label mapping) |
| Knowledge base | Supabase Postgres with pgvector and full-text search, fastembed (local embeddings and reranking) |
| Parsing | defusedxml for NSE/BSE XBRL, pypdfium2 and pdfplumber for PDFs |
| Frontend | Next.js, TypeScript, Tailwind, shadcn/ui, Recharts, pdf.js |
| Hosting | Hugging Face Spaces (Docker backend), Vercel (frontend) |

## Roadmap

1. ~~Deterministic engine, taxonomy, metric registry, rule packs, fact sheet~~
2. ~~Semantic knowledge base (layer A): curated content, chunking, hybrid retrieval, evaluation~~
3. ~~Ingestion: NSE/BSE XBRL, DRHP/RHP statement extraction, label mapping, merging and reconciliation~~
   (annual report PDFs and offer-structure facts to follow)
4. Agent: planner, analyst with tools, critic, report composer, rule proposer
5. Persistence, job queue and REST API with webhooks
6. Learning service: cohort baselines, feedback, rule backtesting
7. Web app: library, upload, report with evidence viewer, reasoning trace, learning console
8. Sample corpus of public Indian filings, evaluation set, deployment
