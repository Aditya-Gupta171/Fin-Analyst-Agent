"use client";

import { useQuery } from "@tanstack/react-query";

import { RiskBadge } from "@/components/badges";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, unwrap } from "@/lib/api";
import type { AnalysisReport, FindingOut } from "@/lib/types";
import { CandidateRules, DataGaps, IntegrityChecks, MetricsTable, Scorecard } from "./report-tables";
import { FindingsList } from "./findings-list";
import { ReasoningTrace } from "./reasoning-trace";
import { SeverityChart } from "./severity-chart";

export function ReportView({
  report,
  analysisId,
  documentId,
}: {
  report: AnalysisReport;
  analysisId: string;
  documentId: string;
}) {
  const findingsQuery = useQuery({
    queryKey: ["analyses", analysisId, "findings"],
    queryFn: async () =>
      unwrap(await api.GET("/analyses/{analysis_id}/findings", { params: { path: { analysis_id: analysisId } } })),
  });

  const statusByFindingId = new Map<string, FindingOut["status"]>(
    (findingsQuery.data ?? []).map((f) => [f.finding_id, f.status]),
  );

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <div className="mb-1 flex items-center gap-2">
            <RiskBadge risk={report.overall_risk} />
            <span className="text-xs text-muted-foreground">
              {report.anchor_period} · generated {new Date(report.generated_at).toLocaleString()}
            </span>
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">{report.headline}</h1>
          <p className="mt-2 max-w-3xl text-sm text-muted-foreground">{report.executive_summary}</p>
        </div>
        <SeverityChart findings={report.findings} />
      </div>

      {(report.strengths.length > 0 || report.concerns.length > 0) && (
        <div className="grid gap-4 sm:grid-cols-2">
          {report.strengths.length > 0 && (
            <ListCard title="Strengths" items={report.strengths} />
          )}
          {report.concerns.length > 0 && <ListCard title="Concerns" items={report.concerns} />}
        </div>
      )}

      <Tabs defaultValue="findings">
        <TabsList>
          <TabsTrigger value="findings">Findings ({report.findings.length})</TabsTrigger>
          <TabsTrigger value="scorecard">Scorecard &amp; metrics</TabsTrigger>
          <TabsTrigger value="trace">Reasoning trace</TabsTrigger>
          <TabsTrigger value="candidates">Candidate rules ({report.candidate_rules.length})</TabsTrigger>
        </TabsList>
        <TabsContent value="findings" className="pt-4">
          <FindingsList
            findings={report.findings}
            statusByFindingId={statusByFindingId}
            analysisId={analysisId}
            documentId={documentId}
          />
        </TabsContent>
        <TabsContent value="scorecard" className="flex flex-col gap-6 pt-4">
          <Scorecard scorecard={report.scorecard} />
          <IntegrityChecks checks={report.integrity} />
          <MetricsTable metrics={report.metrics} />
          <DataGaps gaps={report.data_gaps} />
        </TabsContent>
        <TabsContent value="trace" className="pt-4">
          <ReasoningTrace trace={report.trace} provenance={report.provenance} />
        </TabsContent>
        <TabsContent value="candidates" className="pt-4">
          <CandidateRules candidates={report.candidate_rules} />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function ListCard({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="rounded-lg border p-4">
      <h3 className="mb-2 text-sm font-semibold">{title}</h3>
      <ul className="list-inside list-disc space-y-1 text-sm text-muted-foreground">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
