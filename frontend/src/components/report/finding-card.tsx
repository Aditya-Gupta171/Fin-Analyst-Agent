"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle } from "lucide-react";
import { toast } from "sonner";

import { SeverityBadge } from "@/components/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, api, unwrap } from "@/lib/api";
import type { Finding, FindingOut } from "@/lib/types";
import { EvidenceChip } from "./evidence-sheet";

export function FindingCard({
  finding,
  status,
  analysisId,
  documentId,
}: {
  finding: Finding;
  status: FindingOut["status"];
  analysisId: string;
  documentId: string;
}) {
  const queryClient = useQueryClient();

  const feedback = useMutation({
    mutationFn: async (verdict: "confirm" | "dismiss") =>
      unwrap(
        await api.POST("/analyses/{analysis_id}/findings/{finding_id}/feedback", {
          params: { path: { analysis_id: analysisId, finding_id: finding.id } },
          body: { verdict },
        }),
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["analyses", analysisId, "findings"] });
      toast.success("Feedback recorded");
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not record feedback"),
  });

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
        <div className="flex flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            <SeverityBadge severity={finding.severity} />
            <Badge variant="outline">{finding.origin === "agent" ? "Agent" : "Rule"}</Badge>
            <Badge variant="outline">{finding.category}</Badge>
            {status !== "open" && (
              <Badge variant={status === "confirmed" ? "default" : "secondary"}>{status}</Badge>
            )}
          </div>
          <CardTitle className="text-base">{finding.title}</CardTitle>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button
            size="sm"
            variant={status === "confirmed" ? "default" : "outline"}
            disabled={feedback.isPending}
            onClick={() => feedback.mutate("confirm")}
          >
            <CheckCircle2 /> Confirm
          </Button>
          <Button
            size="sm"
            variant={status === "dismissed" ? "destructive" : "outline"}
            disabled={feedback.isPending}
            onClick={() => feedback.mutate("dismiss")}
          >
            <XCircle /> Dismiss
          </Button>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <p>{finding.summary}</p>
        <p className="text-muted-foreground">{finding.analysis}</p>

        {finding.evidence.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {finding.evidence.map((item) => (
              <EvidenceChip key={item.ref} evidence={item} documentId={documentId} />
            ))}
          </div>
        )}

        {finding.benign_explanations_considered.length > 0 && (
          <Detail title="Benign explanations considered" items={finding.benign_explanations_considered} />
        )}
        {finding.questions_for_management.length > 0 && (
          <Detail title="Questions for management" items={finding.questions_for_management} />
        )}
        {finding.knowledge.length > 0 && (
          <div>
            <div className="mb-1 text-xs tracking-wide text-muted-foreground uppercase">Knowledge cited</div>
            <ul className="flex flex-col gap-0.5 text-xs text-muted-foreground">
              {finding.knowledge.map((citation) => (
                <li key={citation.chunk_id}>
                  {citation.document} — {citation.section}
                </li>
              ))}
            </ul>
          </div>
        )}
        {finding.critique && (
          <p className="text-xs text-muted-foreground italic">
            Critic: {finding.critique.decision} — {finding.critique.reasons}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function Detail({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="mb-1 text-xs tracking-wide text-muted-foreground uppercase">{title}</div>
      <ul className="list-inside list-disc space-y-0.5">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
