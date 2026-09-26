"use client";

/** Last-resort boundary for an error in the root layout itself. It replaces the whole document, so it
 * can't rely on the app's global styles and keeps its own minimal inline styling. */
export default function GlobalError({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
        <title>Something went wrong</title>
        <h2>Something went wrong</h2>
        <p>{error.message || "An unexpected error occurred."}</p>
        <button onClick={() => retry()}>Try again</button>
      </body>
    </html>
  );
}
