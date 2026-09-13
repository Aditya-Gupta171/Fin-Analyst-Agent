"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { UploadDialog } from "@/components/upload-dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, unwrap } from "@/lib/api";
import { formatDateTime, formatDocType, formatSector } from "@/lib/format";

export default function LibraryPage() {
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["documents"],
    queryFn: async () => unwrap(await api.GET("/documents")),
  });

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Document library</h1>
          <p className="text-sm text-muted-foreground">
            Upload a filing to ingest it, then run an analysis from its detail page.
          </p>
        </div>
        <UploadDialog />
      </div>

      {isError && (
        <Alert variant="destructive">
          <AlertTitle>Couldn&apos;t load documents</AlertTitle>
          <AlertDescription>{(error as Error).message}</AlertDescription>
        </Alert>
      )}

      {isPending && (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </div>
      )}

      {data && data.length === 0 && (
        <div className="rounded-lg border border-dashed p-12 text-center text-sm text-muted-foreground">
          No filings uploaded yet.
        </div>
      )}

      {data && data.length > 0 && (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Company</TableHead>
                <TableHead>Sector</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Periods</TableHead>
                <TableHead>Uploaded</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((document) => (
                <TableRow key={document.id} className="cursor-pointer">
                  <TableCell className="font-medium">
                    <Link href={`/documents/${document.id}`} className="hover:underline">
                      {document.company_name}
                    </Link>
                  </TableCell>
                  <TableCell>{formatSector(document.sector)}</TableCell>
                  <TableCell>{formatDocType(document.doc_type)}</TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {document.periods.map((period) => (
                        <Badge key={period} variant="secondary">
                          {period}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatDateTime(document.created_at)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
