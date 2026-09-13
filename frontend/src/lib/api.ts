import createClient from "openapi-fetch";
import type { paths } from "./api-schema";
import type { Sector } from "./types";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export const api = createClient<paths>({ baseUrl: API_BASE_URL });

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Every route handler's typed {data, error, response} triple, unwrapped into a plain result or a throw. */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined) {
    throw new ApiError(result.response.status, errorMessage(result.error, result.response.status));
  }
  return result.data as T;
}

function errorMessage(error: unknown, status: number): string {
  const detail = (error as { detail?: unknown } | undefined)?.detail;
  if (typeof detail === "string") return detail;
  if (detail !== undefined) return JSON.stringify(detail);
  return `Request failed (${status})`;
}

/**
 * Multipart upload. openapi-fetch types the request body from the OpenAPI schema, which has no way to
 * represent a binary field other than `string` — a real `FormData` (what FastAPI actually expects) is
 * cast past that, with `bodySerializer` left as the identity function so it isn't JSON-stringified.
 */
export async function uploadDocument(file: File, sector: Sector, sourceUrl?: string) {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("sector", sector);
  if (sourceUrl) formData.append("source_url", sourceUrl);

  const result = await api.POST("/documents", {
    body: formData as unknown as { file: string; sector: Sector; source_url?: string | null },
    bodySerializer: (body) => body as unknown as BodyInit,
  });
  return unwrap(result);
}

export function documentFileUrl(documentId: string): string {
  return `${API_BASE_URL}/documents/${documentId}/file`;
}
