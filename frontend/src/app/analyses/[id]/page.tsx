"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { use } from "react";

import { ProgressPanel } from "@/components/report/progress-panel";
import { ReportView } from "@/components/report/report-view";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError, api, unwrap } from "@/lib/api";

export default function AnalysisPage({ params }: PageProps<"/analyses/[id]">) {
  const { id } = use(params);

  const analysisQuery = useQuery({
    queryKey: ["analyses", id],
    queryFn: async () => unwrap(await api.GET("/analyses/{analysis_id}", { params: { path: { analysis_id: id } } })),
    refetchInterval: (query) =>
      query.state.data?.status === "running" || query.state.data?.status === "queued" ? 1500 : false,
  });

  if (analysisQuery.isPending) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (analysisQuery.isError) {
    const notFound = analysisQuery.error instanceof ApiError && analysisQuery.error.status === 404;
    return (
      <Alert variant="destructive">
        <AlertTitle>{notFound ? "Analysis not found" : "Couldn't load analysis"}</AlertTitle>
        <AlertDescription>
          {notFound ? "It may have been removed." : (analysisQuery.error as Error).message}
        </AlertDescription>
      </Alert>
    );
  }

  const analysis = analysisQuery.data;

  return (
    <div className="flex flex-col gap-4">
      <Link href={`/documents/${analysis.document_id}`} className="text-sm text-muted-foreground hover:underline">
        &larr; Document
      </Link>

      {(analysis.status === "queued" || analysis.status === "running") && <ProgressPanel analysisId={id} />}

      {analysis.status === "failed" && (
        <Alert variant="destructive">
          <AlertTitle>Analysis failed</AlertTitle>
          <AlertDescription>{analysis.error_message ?? "Unknown error."}</AlertDescription>
        </Alert>
      )}

      {analysis.status === "succeeded" && analysis.report && (
        <ReportView report={analysis.report} analysisId={id} documentId={analysis.document_id} />
      )}
    </div>
  );
}
