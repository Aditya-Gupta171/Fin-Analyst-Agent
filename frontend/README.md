# Frontend

Next.js (App Router) web app for the [Self-Learning AI Financial Analyst Agent](../README.md) — document
library, analysis reports with an evidence viewer and reasoning trace, and a learning console. See the
main README's [Quickstart](../README.md#quickstart) and [Frontend](../README.md#frontend) sections for the
full setup.

```bash
npm install
npm run dev   # http://localhost:3000, expects the backend at http://localhost:8000
```

`npm run gen:types` regenerates `src/lib/api-schema.ts` from the backend's own `/openapi.json` (backend
must be running) — the source of truth for every request/response type this app uses.
