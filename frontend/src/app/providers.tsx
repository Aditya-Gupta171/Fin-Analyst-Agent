"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import { ApiError } from "@/lib/api";

let browserQueryClient: QueryClient | undefined;

function getQueryClient() {
  // A fresh client per server render; one shared client reused across renders in the browser.
  if (typeof window === "undefined") return new QueryClient();
  browserQueryClient ??= new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,
        // Retrying a 4xx (not found, validation) is pointless — it'll fail identically every time —
        // so only retry the errors a retry could plausibly fix. This also sidesteps a TanStack Query
        // quirk: a queued retry additionally waits on the window being "focused"
        // (query-core/retryer.js canContinue()), which some embedded/automated browser contexts never
        // report — reserving retries for the cases that need them keeps a stray 404 from hanging.
        retry: (failureCount, error) => !(error instanceof ApiError && error.status < 500) && failureCount < 2,
      },
    },
  });
  return browserQueryClient;
}

export function Providers({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={getQueryClient()}>{children}</QueryClientProvider>;
}
