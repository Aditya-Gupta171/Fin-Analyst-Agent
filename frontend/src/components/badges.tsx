import { Badge } from "@/components/ui/badge";
import { RISK_STYLES, SEVERITY_STYLES, STATUS_STYLES, titleCase } from "@/lib/format";
import type { AnalysisStatus, Severity } from "@/lib/types";
import { cn } from "@/lib/utils";

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <Badge variant="outline" className={cn("border-transparent", SEVERITY_STYLES[severity])}>
      {titleCase(severity)}
    </Badge>
  );
}

export function StatusBadge({ status }: { status: AnalysisStatus }) {
  return (
    <Badge variant="outline" className={cn("border-transparent", STATUS_STYLES[status])}>
      {titleCase(status)}
    </Badge>
  );
}

export function RiskBadge({ risk }: { risk: string }) {
  return (
    <Badge
      variant="outline"
      className={cn("border-transparent", RISK_STYLES[risk] ?? "bg-muted text-muted-foreground")}
    >
      {titleCase(risk)} risk
    </Badge>
  );
}
