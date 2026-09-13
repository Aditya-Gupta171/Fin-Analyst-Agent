# Knowledge base — layer A (semantic)

Curated analyst knowledge the agent retrieves while reasoning: why a signal matters, how to read it in an Indian
filing, what would explain it away, and what to ask management. Layer B (`rules/`) says *what* to check; this
layer explains *why* and *how to judge it*.

## Conventions

Each document is a markdown file whose path is its id: `working-capital/receivables.md` is
`working-capital/receivables`, the value rules and metrics use in their `kb:` field. A test fails if any `kb:`
reference has no document.

Front matter:

```yaml
---
title: Trade receivables and collection risk
summary: One sentence, used in retrieval results.
doc_types: [annual_report, drhp, rhp]   # optional; omit when relevant to every filing type
sectors: [it_services]                  # optional; omit when sector-agnostic
regulations: [Ind AS 115, Schedule III] # optional
---
```

Standard section headings (each becomes a retrievable chunk tagged with its kind, so the critic can ask
specifically for benign explanations):

| Heading | Kind |
| --- | --- |
| `## What it measures` | `overview` |
| `## How to read it` | `interpretation` |
| `## Red flags` | `red_flags` |
| `## Benign explanations` | `benign_explanations` |
| `## Where to find it in Indian filings` | `sources` |
| `## Questions for management` | `questions` |

Other headings are allowed and are tagged `general`. Write in your own words, state thresholds as rules of thumb
rather than laws unless a regulation sets them, and cite the regulation or standard by name.
