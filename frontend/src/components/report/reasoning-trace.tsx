import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { CallRecord, Provenance } from "@/lib/types";

export function ReasoningTrace({ trace, provenance }: { trace: CallRecord[]; provenance: Provenance }) {
  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Provenance</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm sm:grid-cols-4">
          <Field label="Mode" value={provenance.mode} />
          <Field label="Catalog version" value={provenance.catalog_version} />
          <Field label="KB version" value={provenance.kb_version ?? "—"} />
          <Field label="Prompt version" value={provenance.prompt_version ?? "—"} />
          <Field label="Models" value={provenance.models.join(", ") || "—"} />
          <Field
            label="Tokens"
            value={`${provenance.token_usage.prompt_tokens + provenance.token_usage.completion_tokens} (${provenance.token_usage.calls} calls, ${provenance.token_usage.cached_calls} cached)`}
          />
          {provenance.notes.length > 0 && (
            <div className="col-span-full">
              <div className="mb-1 text-xs tracking-wide text-muted-foreground uppercase">Notes</div>
              <ul className="list-inside list-disc text-muted-foreground">
                {provenance.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      {trace.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No model calls were made — this report was produced rules-only.
        </p>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Node</TableHead>
                <TableHead>Model</TableHead>
                <TableHead>Tokens (prompt/completion/reasoning)</TableHead>
                <TableHead>Latency</TableHead>
                <TableHead>Attempts</TableHead>
                <TableHead>Outcome</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {trace.map((call, index) => (
                <TableRow key={`${call.node}-${index}`}>
                  <TableCell className="font-mono text-xs">{call.node}</TableCell>
                  <TableCell>
                    {call.model} {call.cached && <Badge variant="secondary">cached</Badge>}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {call.prompt_tokens} / {call.completion_tokens} / {call.reasoning_tokens}
                  </TableCell>
                  <TableCell className="text-muted-foreground">{call.latency_ms} ms</TableCell>
                  <TableCell className="text-muted-foreground">{call.attempts}</TableCell>
                  <TableCell>
                    <Badge variant={call.outcome === "ok" ? "default" : call.outcome === "repaired" ? "secondary" : "destructive"}>
                      {call.outcome}
                    </Badge>
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

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-xs tracking-wide text-muted-foreground uppercase">{label}</div>
      <div>{value}</div>
    </div>
  );
}
