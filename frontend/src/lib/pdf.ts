"use client";

import type { PDFDocumentProxy } from "pdfjs-dist";

let pdfjsPromise: Promise<typeof import("pdfjs-dist")> | null = null;

/** Loads pdf.js and points it at a worker script matching the installed version, from a CDN — avoids
 * the webpack worker-bundling friction of shipping the worker file ourselves. */
function getPdfjs() {
  pdfjsPromise ??= import("pdfjs-dist").then((lib) => {
    lib.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${lib.version}/build/pdf.worker.min.mjs`;
    return lib;
  });
  return pdfjsPromise;
}

const documentCache = new Map<string, Promise<PDFDocumentProxy>>();

function loadDocument(url: string): Promise<PDFDocumentProxy> {
  let cached = documentCache.get(url);
  if (!cached) {
    cached = getPdfjs().then((lib) => lib.getDocument({ url }).promise);
    documentCache.set(url, cached);
  }
  return cached;
}

export async function getPageCount(url: string): Promise<number> {
  const doc = await loadDocument(url);
  return doc.numPages;
}

export async function renderPdfPage(
  url: string,
  pageNumber: number,
  canvas: HTMLCanvasElement,
  scale = 1.4,
): Promise<void> {
  const doc = await loadDocument(url);
  const page = await doc.getPage(pageNumber);
  const viewport = page.getViewport({ scale });
  canvas.width = viewport.width;
  canvas.height = viewport.height;
  await page.render({ canvas, viewport }).promise;
}
