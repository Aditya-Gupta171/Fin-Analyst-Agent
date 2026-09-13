// Friendly aliases over the generated OpenAPI schema (src/lib/api-schema.ts, regenerate with
// `npm run gen:types` against a running backend) so the rest of the app never spells out
// `components["schemas"][...]`.
import type { components } from "./api-schema";

export type DocumentSummary = components["schemas"]["DocumentSummary"];
export type DocumentDetail = components["schemas"]["DocumentDetail"];
export type DocumentUploadResponse = components["schemas"]["DocumentUploadResponse"];
export type IngestionWarning = components["schemas"]["IngestionWarning"];

export type AnalysisSummary = components["schemas"]["AnalysisSummary"];
export type AnalysisDetail = components["schemas"]["AnalysisDetail"];
export type AnalysisReport = components["schemas"]["AnalysisReport"];
export type AnalysisStatus = AnalysisSummary["status"];

export type Finding = components["schemas"]["Finding"];
export type FindingOut = components["schemas"]["FindingOut"];
export type EvidenceItem = components["schemas"]["EvidenceItem"];
export type KnowledgeCitation = components["schemas"]["KnowledgeCitation"];
export type AreaScore = components["schemas"]["AreaScore"];
export type MetricRow = components["schemas"]["MetricRow"];
export type IntegrityCheck = components["schemas"]["IntegrityCheck"];
export type Anomaly = components["schemas"]["Anomaly"];
export type DismissedRule = components["schemas"]["DismissedRule"];
export type CandidateRule = components["schemas"]["CandidateRule"];
export type Provenance = components["schemas"]["Provenance"];
export type CallRecord = components["schemas"]["CallRecord"];

export type RuleCandidateOut = components["schemas"]["RuleCandidateOut"];
export type RuleReliability = components["schemas"]["RuleReliability"];

// RuleCandidateOut.backtest_result is typed as a bare `dict` in the backend (app/api/schemas.py), so
// FastAPI's OpenAPI schema has no nested shape for it to generate — this mirrors the actual pydantic
// model it's populated from (app/db/backtest.py's BacktestResult) by hand.
export interface BacktestHit {
  document_id: string;
  company_name: string;
  period: string;
  severity: Severity;
}

export interface BacktestResult {
  documents_evaluated: number;
  fired: BacktestHit[];
  insufficient_data_count: number;
  errors: string[];
}

// RuleCandidateOut.definition is likewise a bare `dict` — the shape a CandidateRule (app/analysis/report.py)
// actually always dumps to.
export interface CandidateDefinition {
  id: string;
  title: string;
  category: string;
  severity: Severity;
  when: string;
  rationale: string;
  evidence: string[];
}

export type Sector = components["schemas"]["Sector"];
export type DocType = components["schemas"]["DocType"];
export type Basis = components["schemas"]["Basis"];
export type Severity = components["schemas"]["Severity"];
export type Unit = components["schemas"]["Unit"];

export const SECTORS: Sector[] = [
  "banking",
  "nbfc",
  "insurance",
  "it_services",
  "manufacturing",
  "infrastructure",
  "pharma_healthcare",
  "fmcg_consumer",
  "auto",
  "chemicals",
  "metals_mining",
  "energy_utilities",
  "real_estate",
  "telecom_media",
  "consumer_internet",
  "other",
];
