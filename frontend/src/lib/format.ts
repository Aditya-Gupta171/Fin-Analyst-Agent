import type { AnalysisStatus, Severity } from "./types";

export const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low", "info"];

export const SEVERITY_STYLES: Record<Severity, string> = {
  critical: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200",
  high: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-200",
  medium: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  low: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-200",
  info: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
};

export const RISK_STYLES: Record<string, string> = {
  high: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200",
  elevated: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-200",
  moderate: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-200",
  low: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
};

export const STATUS_STYLES: Record<AnalysisStatus, string> = {
  queued: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  running: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-200",
  succeeded: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-200",
  failed: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200",
};

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function formatSector(sector: string): string {
  return sector
    .split("_")
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

export function formatDocType(docType: string): string {
  return docType.toUpperCase() === docType ? docType : formatSector(docType);
}

export function titleCase(value: string): string {
  return value[0].toUpperCase() + value.slice(1);
}
