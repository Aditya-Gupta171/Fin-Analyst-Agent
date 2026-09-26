"use client";

import { useEffect } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

/** Catches a render error in any page, so one malformed report or response shows this instead of a blank
 * screen; the nav and layout around it keep working. */
export default function PageError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex flex-col items-start gap-4">
      <Alert variant="destructive">
        <AlertTitle>Something went wrong on this page</AlertTitle>
        <AlertDescription>{error.message || "An unexpected error occurred."}</AlertDescription>
      </Alert>
      <Button variant="outline" onClick={() => retry()}>
        Try again
      </Button>
    </div>
  );
}
