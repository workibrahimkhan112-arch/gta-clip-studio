# GTA Clip Studio frontend

A local-first React + TypeScript editing desk. All server data uses the shared `../CONTRACTS.md`; there are no runtime mocks or external media/font requests.

## Run

```sh
npm install
npm run dev
```

Vite listens on **127.0.0.1:5173** and proxies relative `/api` requests (including native EventSource and media) to **127.0.0.1:8000**. The root development coordinator can invoke `npm run dev` in this directory. Run the backend separately when not using that coordinator.

```sh
npm run build
npm test
```

The production bundle is written to `frontend/dist`, where the contracted FastAPI server can serve it. Client routes require SPA fallback in other production servers. `npm run preview` previews static assets only; use the backend for production API access.

## Workflows

- **Import:** reference an absolute server-local path without copying, upload a file using multipart XHR with measured transfer progress and cancellation, or queue a public YouTube import. Canceling an upload does not claim to roll back any server-side work already received.
- **Review:** play the actual source media, seek a real candidate timeline, inspect metadata and component scores, persist highlight selection.
- **Captions:** edit title and relative-time transcript segments. Editing a segment clears only that segment’s word timings. Unchanged word timings are preserved. No text or speech timings are invented. The source preview overlays approximate segment captions, not final rendering.
- **Export:** use one clip’s editor or batch-export selected visible clips. Styles are none, standard, and animated. No-transcript warnings explain the manual editor and optional cached local model. Jobs are serialized by the server and can be canceled individually. Finished files download through the contracted file endpoint.
- **Settings:** every setting in the contract is read and persisted via GET/PUT, including output directory, resource budget, analysis recovery chunk size, and optional refinement. System dependency warnings, actual benchmark records, and inactive orphan-cache cleanup are included.

## Structure

- `src/services/api.ts`: typed API client, validation error handling, multipart upload.
- `src/stores`: Zustand server-resource state, connection status, import dialog and notifications.
- `src/hooks/useLiveData.ts`: one native SSE stream, four-second polling fallback, periodic background refresh and focus refresh.
- `src/components/ui`: local shadcn-style primitives using Radix Dialog, Select, Checkbox, Tabs, Tooltip, and Slot.
- `src/components`: workflow components, real media player, candidate timeline, editor and export controls.
- `src/pages`: route-level screens, lazy loaded.
- `src/styles`: responsive charcoal/lime visual system; local system fonts and reduced-motion support.

## Contract notes

No extension is required for the core workflows. Individual `GET /api/benchmarks` record fields are intentionally unspecified in the shared contract; the UI displays the actual returned fields rather than inventing a schema. Health only reports transcription package availability, not cached-model availability; the UI does not claim a detected package guarantees transcription.

The browser may not decode every FFmpeg-supported source codec. In that case it reports the preview failure and allows retry; analysis/export still use the original source. A browser-compatible proxy/transcode endpoint would be an optional future extension, not a core dependency.

Native EventSource reconnects automatically. If the stream fails or goes silent, the client refreshes jobs and related resources through REST; connection status distinguishes live, polling, and offline states. Current-stage progress is never presented as overall job completion. Failed refreshes retain last received data with a visible warning.
