"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use } from "react";
import { toast } from "sonner";

import { StatusBadge } from "@/components/badges";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError, api, unwrap } from "@/lib/api";
import { formatDateTime, formatDocType, formatSector } from "@/lib/format";

export default function DocumentDetailPage({ params }: PageProps<"/documents/[id]">) {
  const { id } = use(params);
  const router = useRouter();
  const queryClient = useQueryClient();

  const documentQuery = useQuery({
    queryKey: ["documents", id],
    queryFn: async () => unwrap(await api.GET("/documents/{document_id}", { params: { path: { document_id: id } } })),
  });

  const analysesQuery = useQuery({
    queryKey: ["analyses", { documentId: id }],
    queryFn: async () => unwrap(await api.GET("/analyses", { params: { query: { document_id: id } } })),
    refetchInterval: (query) => (query.state.data?.some((a) => a.status === "running" || a.status === "queued") ? 2000 : false),
  });

  const trigger = useMutation({
    mutationFn: async () =>
      unwrap(await api.POST("/documents/{document_id}/analyses", { params: { path: { document_id: id } } })),
    onSuccess: (analysis) => {
      queryClient.invalidateQueries({ queryKey: ["analyses", { documentId: id }] });
      toast.success("Analysis started");
      router.push(`/analyses/${analysis.id}`);
    },
    onError: (error) => toast.error(error instanceof ApiError ? error.message : "Could not start analysis"),
  });

  if (documentQuery.isPending) {
    return (
      <div className="flex flex-col gap-3">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (documentQuery.isError) {
    const notFound = documentQuery.error instanceof ApiError && documentQuery.error.status === 404;
    return (
      <Alert variant="destructive">
        <AlertTitle>{notFound ? "Document not found" : "Couldn't load document"}</AlertTitle>
        <AlertDescription>
          {notFound ? "It may have been removed." : (documentQuery.error as Error).message}
        </AlertDescription>
      </Alert>
    );
  }

  const document = documentQuery.data;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <Link href="/" className="text-sm text-muted-foreground hover:underline">
          &larr; Library
        </Link>
        <div className="mt-2 flex items-center justify-between">
          <h1 className="text-2xl font-semibold tracking-tight">{document.company_name}</h1>
          <Button onClick={() => trigger.mutate()} disabled={trigger.isPending}>
            {trigger.isPending ? "Starting..." : "Run analysis"}
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Filing details</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm sm:grid-cols-4">
          <Field label="Sector" value={formatSector(document.sector)} />
          <Field label="Document type" value={formatDocType(document.doc_type)} />
          <Field label="Basis" value={document.basis} />
          <Field label="Filing date" value={document.filing_date ?? "—"} />
          <Field label="Periods" value={document.periods.join(", ")} />
          <Field label="Facts" value={String(document.fact_count)} />
          <Field label="Original file" value={document.original_filename} />
          <Field label="Uploaded" value={formatDateTime(document.created_at)} />
        </CardContent>
      </Card>

      <div>
        <h2 className="mb-2 text-lg font-medium">Analyses</h2>
        {analysesQuery.isPending && <Skeleton className="h-24 w-full" />}
        {analysesQuery.data && analysesQuery.data.length === 0 && (
          <p className="text-sm text-muted-foreground">No analyses yet — run one above.</p>
        )}
        {analysesQuery.data && analysesQuery.data.length > 0 && (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Status</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Finished</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {analysesQuery.data.map((analysis) => (
                  <TableRow key={analysis.id}>
                    <TableCell>
                      <StatusBadge status={analysis.status} />
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {analysis.started_at ? formatDateTime(analysis.started_at) : "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {analysis.finished_at ? formatDateTime(analysis.finished_at) : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      <Link href={`/analyses/${analysis.id}`} className="text-sm text-primary hover:underline">
                        View report &rarr;
                      </Link>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div>{value}</div>
    </div>
  );
}
