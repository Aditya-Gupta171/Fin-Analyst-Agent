import Link from "next/link";

export default function NotFound() {
  return (
    <div className="flex flex-col items-start gap-2">
      <h1 className="text-2xl font-semibold tracking-tight">Page not found</h1>
      <p className="text-sm text-muted-foreground">There is nothing at this address.</p>
      <Link href="/" className="text-sm underline">
        Back to the document library
      </Link>
    </div>
  );
}
