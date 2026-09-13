"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { SeverityBadge } from "@/components/badges";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, api, unwrap } from "@/lib/api";
import type { BacktestResult, CandidateDefinition, RuleCandidateOut } from "@/lib/types";

export default function LearningPage() {
  return (
    <div className="flex flex-col gap-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Learning console</h1>
        <p className="text-sm text-muted-foreground">
          Rule reliability calibrated from analyst feedback, and agent-proposed rules awaiting review.
        </p>
      </div>
      <ReliabilitySection />
      <CandidatesSection />
    </div>
  );
}

function ReliabilitySection() {
  const query = useQuery({
    queryKey: ["rules", "reliability"],
    queryFn: async () => unwrap(await api.GET("/rules/reliability")),
  });

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-medium">Rule reliability</h2>
      {query.isPending && <Skeleton className="h-24 w-full" />}
      {query.data && query.data.length === 0 && (
        <p className="text-sm text-muted-foreground">No feedback recorded yet.</p>
      )}
      {query.data && query.data.length > 0 && (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Rule</TableHead>
                <TableHead>Confirms</TableHead>
                <TableHead>Dismisses</TableHead>
                <TableHead>Score</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.data.map((row) => (
                <TableRow key={row.rule_id}>
                  <TableCell className="font-mono text-sm">{row.rule_id}</TableCell>
                  <TableCell>{row.confirms}</TableCell>
                  <TableCell>{row.dismisses}</TableCell>
                  <TableCell>{row.score.toFixed(2)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </section>
  );
}

function CandidatesSection() {
  const query = useQuery({
    queryKey: ["rules", "candidates"],
    queryFn: async () => unwrap(await api.GET("/rules/candidates")),
  });

  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-lg font-medium">Candidate rules</h2>
      {query.isPending && <Skeleton className="h-24 w-full" />}
      {query.data && query.data.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No candidates yet — the agent proposes one when it finds a pattern no active rule covers.
        </p>
      )}
      <div className="flex flex-col gap-3">
        {query.data?.map((candidate) => <CandidateCard key={candidate.id} candidate={candidate} />)}
      </div>
    </section>
  );
}

function CandidateCard({ candidate }: { candidate: RuleCandidateOut }) {
  const queryClient = useQueryClient();
  const definition = candidate.definition as unknown as CandidateDefinition;
  const backtestResult = candidate.backtest_result as unknown as BacktestResult | null;

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["rules", "candidates"] });

  const backtest = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/rules/candidates/{candidate_id}/backtest", { params: { path: { candidate_id: candidate.id } } })),
    onSuccess: () => {
      invalidate();
      toast.success("Backtest complete");
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Backtest failed"),
  });

  const decide = useMutation({
    mutationFn: async (decision: "approve" | "reject") =>
      unwrap(
        await api.POST("/rules/candidates/{candidate_id}/decision", {
          params: { path: { candidate_id: candidate.id } },
          body: { decision },
        }),
      ),
    onSuccess: (_, decision) => {
      invalidate();
      toast.success(decision === "approve" ? "Promoted into rules/packs/learned/" : "Rejected");
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Decision failed"),
  });

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <CardTitle className="text-base">{definition.title}</CardTitle>
        <div className="flex items-center gap-2">
          <SeverityBadge severity={definition.severity} />
          <Badge variant="outline">{definition.category}</Badge>
          <Badge
            variant={candidate.status === "approved" ? "default" : candidate.status === "rejected" ? "secondary" : "outline"}
          >
            {candidate.status}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        <p className="text-muted-foreground">{definition.rationale}</p>
        <code className="rounded bg-muted px-2 py-1 font-mono text-xs">{definition.when}</code>

        {backtestResult && (
          <div className="rounded-md border p-3 text-xs">
            <p>
              Fired on {backtestResult.fired.length} of {backtestResult.documents_evaluated} evaluated filings
              {backtestResult.insufficient_data_count > 0 &&
                ` (${backtestResult.insufficient_data_count} had insufficient data)`}
              .
            </p>
            {backtestResult.errors.length > 0 && (
              <ul className="mt-1 list-inside list-disc text-destructive">
                {backtestResult.errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {candidate.status === "candidate" && (
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={backtest.isPending} onClick={() => backtest.mutate()}>
              {backtest.isPending ? "Backtesting..." : "Backtest"}
            </Button>
            <Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate("approve")}>
              Approve
            </Button>
            <Button size="sm" variant="destructive" disabled={decide.isPending} onClick={() => decide.mutate("reject")}>
              Reject
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
