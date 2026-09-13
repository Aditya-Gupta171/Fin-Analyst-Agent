import { SEVERITY_ORDER, titleCase } from "@/lib/format";
import type { Finding, FindingOut, Severity } from "@/lib/types";
import { FindingCard } from "./finding-card";

export function FindingsList({
  findings,
  statusByFindingId,
  analysisId,
  documentId,
}: {
  findings: Finding[];
  statusByFindingId: Map<string, FindingOut["status"]>;
  analysisId: string;
  documentId: string;
}) {
  if (findings.length === 0) {
    return (
      <p className="rounded-lg border border-dashed p-8 text-center text-sm text-muted-foreground">
        No findings — the rules and the agent found nothing worth flagging this run.
      </p>
    );
  }

  const bySeverity = new Map<Severity, Finding[]>();
  for (const finding of findings) {
    const bucket = bySeverity.get(finding.severity) ?? [];
    bucket.push(finding);
    bySeverity.set(finding.severity, bucket);
  }

  return (
    <div className="flex flex-col gap-6">
      {SEVERITY_ORDER.filter((severity) => bySeverity.has(severity)).map((severity) => (
        <div key={severity} className="flex flex-col gap-3">
          <h3 className="text-sm font-semibold text-muted-foreground">
            {titleCase(severity)} ({bySeverity.get(severity)!.length})
          </h3>
          {bySeverity.get(severity)!.map((finding) => (
            <FindingCard
              key={finding.id}
              finding={finding}
              status={statusByFindingId.get(finding.id) ?? "open"}
              analysisId={analysisId}
              documentId={documentId}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
