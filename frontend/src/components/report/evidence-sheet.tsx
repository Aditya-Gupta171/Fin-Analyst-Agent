"use client";

import { useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { documentFileUrl } from "@/lib/api";
import { renderPdfPage } from "@/lib/pdf";
import type { EvidenceItem } from "@/lib/types";

export function EvidenceChip({
  evidence,
  documentId,
}: {
  evidence: EvidenceItem;
  documentId: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} className="inline-flex">
        <Badge variant="secondary" className="cursor-pointer hover:bg-secondary/80">
          {evidence.label}: {evidence.display}
        </Badge>
      </button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent className="overflow-y-auto sm:max-w-lg">
          <SheetHeader>
            <SheetTitle>{evidence.label}</SheetTitle>
            <SheetDescription>
              {evidence.period ? `Period ${evidence.period}` : "Document-level figure"}
            </SheetDescription>
          </SheetHeader>
          <div className="flex flex-col gap-4 px-4 pb-4 text-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
              <dt className="text-muted-foreground">Value</dt>
              <dd>{evidence.display}</dd>
              {evidence.source && (
                <>
                  <dt className="text-muted-foreground">Source label</dt>
                  <dd>{evidence.source}</dd>
                </>
              )}
              <dt className="text-muted-foreground">Reference</dt>
              <dd className="font-mono text-xs">{evidence.ref}</dd>
            </dl>
            {evidence.page != null && (
              <PdfPagePreview documentId={documentId} page={evidence.page} />
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}

function PdfPagePreview({ documentId, page }: { documentId: string; page: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const canvas = canvasRef.current;
    if (!canvas) return;
    setError(null);
    renderPdfPage(documentFileUrl(documentId), page, canvas).catch((err: unknown) => {
      if (!cancelled) setError(err instanceof Error ? err.message : "Could not render the page");
    });
    return () => {
      cancelled = true;
    };
  }, [documentId, page]);

  return (
    <div>
      <div className="mb-2 text-xs tracking-wide text-muted-foreground uppercase">
        Source — page {page}
      </div>
      {error ? (
        <p className="text-sm text-destructive">{error}</p>
      ) : (
        <canvas ref={canvasRef} className="w-full rounded border" />
      )}
    </div>
  );
}
