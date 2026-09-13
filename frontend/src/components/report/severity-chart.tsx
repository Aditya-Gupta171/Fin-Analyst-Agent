"use client";

import { Bar, BarChart, CartesianGrid, Cell, XAxis, YAxis } from "recharts";

import { SEVERITY_ORDER } from "@/lib/format";
import type { Finding } from "@/lib/types";

const COLORS: Record<string, string> = {
  critical: "#dc2626",
  high: "#ea580c",
  medium: "#d97706",
  low: "#2563eb",
  info: "#64748b",
};

export function SeverityChart({ findings }: { findings: Finding[] }) {
  const counts = SEVERITY_ORDER.map((severity) => ({
    severity,
    count: findings.filter((f) => f.severity === severity).length,
  })).filter((row) => row.count > 0);

  if (counts.length === 0) return null;

  return (
    <BarChart width={360} height={180} data={counts} layout="vertical" margin={{ left: 8 }}>
      <CartesianGrid strokeDasharray="3 3" horizontal={false} />
      <XAxis type="number" allowDecimals={false} />
      <YAxis type="category" dataKey="severity" width={64} tickFormatter={(v: string) => v} />
      <Bar dataKey="count" radius={[0, 4, 4, 0]}>
        {counts.map((row) => (
          <Cell key={row.severity} fill={COLORS[row.severity]} />
        ))}
      </Bar>
    </BarChart>
  );
}
