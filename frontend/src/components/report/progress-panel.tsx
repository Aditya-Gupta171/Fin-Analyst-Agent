"use client";

import { useEffect, useState } from "react";

import { StatusBadge } from "@/components/badges";
import { Progress } from "@/components/ui/progress";
import { API_BASE_URL } from "@/lib/api";
import type { AnalysisStatus } from "@/lib/types";

interface ProgressEvent {
  status: AnalysisStatus;
  stage: string | null;
  message: string | null;
  error?: string;
}

/** A live look at a run in progress via SSE — the source of truth for when it's actually done is the
 * polled AnalysisDetail query in the page itself, not this stream (see app/analyses/[id]/page.tsx). */
export function ProgressPanel({ analysisId }: { analysisId: string }) {
  const [event, setEvent] = useState<ProgressEvent | null>(null);

  useEffect(() => {
    const source = new EventSource(`${API_BASE_URL}/analyses/${analysisId}/events`);
    source.onmessage = (message) => {
      try {
        const data = JSON.parse(message.data) as ProgressEvent;
        if (!data.error) setEvent(data);
      } catch {
        // malformed/partial event — ignore, the next one will arrive shortly
      }
    };
    source.onerror = () => source.close();
    return () => source.close();
  }, [analysisId]);

  return (
    <div className="flex flex-col items-center gap-4 rounded-lg border p-12 text-center">
      <StatusBadge status={event?.status ?? "queued"} />
      <Progress value={event?.status === "running" ? 60 : 15} className="w-64" />
      <div>
        <p className="font-medium">{event?.stage ?? "Waiting to start"}</p>
        <p className="text-sm text-muted-foreground">
          {event?.message ?? "The job queue will pick this up shortly."}
        </p>
      </div>
    </div>
  );
}
