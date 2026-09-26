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

/** Rough share of the run done once each agent stage starts (app/agent/graph.py runs them in this order). */
const STAGE_PROGRESS: Record<string, number> = {
  engine: 10,
  plan: 20,
  investigate: 35,
  review: 65,
  revise: 75,
  compose: 85,
  propose: 92,
  done: 100,
};

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
        // the server ends the stream once the run finishes; stop EventSource from reconnecting to it
        if (data.error || data.status === "succeeded" || data.status === "failed") source.close();
      } catch {
        // malformed/partial event — ignore, the next one will arrive shortly
      }
    };
    // No onerror close: on a dropped connection EventSource reconnects by itself, and the page's own
    // polling still decides when the run is done if the stream stays down.
    return () => source.close();
  }, [analysisId]);

  return (
    <div className="flex flex-col items-center gap-4 rounded-lg border p-12 text-center">
      <StatusBadge status={event?.status ?? "queued"} />
      <Progress value={(event?.stage && STAGE_PROGRESS[event.stage]) || 5} className="w-64" />
      <div>
        <p className="font-medium">{event?.stage ?? "Waiting to start"}</p>
        <p className="text-sm text-muted-foreground">
          {event?.message ?? "The job queue will pick this up shortly."}
        </p>
      </div>
    </div>
  );
}
