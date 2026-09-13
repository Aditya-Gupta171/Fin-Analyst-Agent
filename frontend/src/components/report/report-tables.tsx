import { SeverityBadge } from "@/components/badges";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { titleCase } from "@/lib/format";
import type { AreaScore, CandidateRule, IntegrityCheck, MetricRow } from "@/lib/types";

export function Scorecard({ scorecard }: { scorecard: AreaScore[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Scorecard</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {scorecard.map((area) => (
            <div key={area.area} className="rounded-md border p-3 text-sm">
              <div className="mb-1 flex items-center justify-between">
                <span className="font-medium">{titleCase(area.area.replace(/_/g, " "))}</span>
                <Badge variant={area.status === "concern" ? "destructive" : "outline"}>{area.status}</Badge>
              </div>
              <div className="text-xs text-muted-foreground">
                {area.fired} fired · {area.passed} passed · {area.insufficient} insufficient data
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

export function MetricsTable({ metrics }: { metrics: MetricRow[] }) {
  const periods = Array.from(new Set(metrics.flatMap((m) => Object.keys(m.values)))).sort();

  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Metric</TableHead>
            {periods.map((period) => (
              <TableHead key={period}>{period}</TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {metrics.map((metric) => (
            <TableRow key={metric.key}>
              <TableCell className="font-medium">{metric.name}</TableCell>
              {periods.map((period) => (
                <TableCell key={period} className="text-muted-foreground">
                  {metric.values[period] ?? "—"}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function IntegrityChecks({ checks }: { checks: IntegrityCheck[] }) {
  if (checks.length === 0) return null;
  return (
    <div className="rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Check</TableHead>
            <TableHead>Period</TableHead>
            <TableHead>Outcome</TableHead>
            <TableHead>Detail</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {checks.map((check) => (
            <TableRow key={`${check.rule_id}-${check.period}`}>
              <TableCell className="font-medium">{check.title}</TableCell>
              <TableCell className="text-muted-foreground">{check.period}</TableCell>
              <TableCell>
                <Badge variant={check.outcome === "fired" ? "destructive" : "outline"}>
                  {check.outcome.replace(/_/g, " ")}
                </Badge>
              </TableCell>
              <TableCell className="text-muted-foreground">{check.detail ?? "—"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export function DataGaps({ gaps }: { gaps: string[] }) {
  if (gaps.length === 0) return null;
  return (
    <div>
      <h3 className="mb-2 text-sm font-semibold text-muted-foreground">Data gaps</h3>
      <ul className="list-inside list-disc text-sm">
        {gaps.map((gap) => (
          <li key={gap}>{gap}</li>
        ))}
      </ul>
    </div>
  );
}

export function CandidateRules({ candidates }: { candidates: CandidateRule[] }) {
  if (candidates.length === 0) {
    return <p className="text-sm text-muted-foreground">No rule candidates were proposed this run.</p>;
  }
  return (
    <div className="flex flex-col gap-3">
      {candidates.map((candidate) => (
        <Card key={candidate.id}>
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">{candidate.title}</CardTitle>
            <div className="flex gap-2">
              <SeverityBadge severity={candidate.severity} />
              <Badge variant="outline">{candidate.category}</Badge>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 text-sm">
            <p className="text-muted-foreground">{candidate.rationale}</p>
            <code className="rounded bg-muted px-2 py-1 font-mono text-xs">{candidate.when}</code>
            {candidate.validation_errors.length > 0 && (
              <ul className="list-inside list-disc text-xs text-destructive">
                {candidate.validation_errors.map((error) => (
                  <li key={error}>{error}</li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      ))}
      <p className="text-xs text-muted-foreground">
        Review these on the <a href="/learning" className="underline">Learning</a> console.
      </p>
    </div>
  );
}
