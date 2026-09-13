"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError, uploadDocument } from "@/lib/api";
import { formatSector } from "@/lib/format";
import { SECTORS, type Sector } from "@/lib/types";

export function UploadDialog() {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [sector, setSector] = useState<Sector>("other");
  const router = useRouter();
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: () => {
      if (!file) throw new Error("Choose a file first");
      return uploadDocument(file, sector);
    },
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      for (const warning of result.warnings) {
        toast.warning(warning.message);
      }
      toast.success(`Ingested ${result.document.company_name}`);
      setOpen(false);
      setFile(null);
      router.push(`/documents/${result.document.id}`);
    },
    onError: (error) => {
      toast.error(error instanceof ApiError ? error.message : "Upload failed");
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button>Upload filing</Button>} />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Upload a filing</DialogTitle>
          <DialogDescription>
            An NSE/BSE XBRL results filing, or a DRHP/RHP/annual report PDF. Ingestion is deterministic — no
            LLM call happens here.
          </DialogDescription>
        </DialogHeader>
        <div className="grid gap-4 py-2">
          <div className="grid gap-2">
            <Label htmlFor="file">File</Label>
            <Input
              id="file"
              type="file"
              accept=".xml,.pdf"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="sector">Sector</Label>
            <Select value={sector} onValueChange={(value) => setSector(value as Sector)}>
              <SelectTrigger id="sector">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SECTORS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {formatSector(value)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button disabled={!file || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? "Ingesting..." : "Upload"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
