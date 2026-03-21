# The Ledger Frontend

Internal loan decisioning demo UI for The Ledger.

## Run locally

```bash
cd frontend
npm install
npm run dev
```

## Backend hookup later

Set `NEXT_PUBLIC_LEDGER_API_BASE_URL` to point at a thin FastAPI wrapper or MCP-backed service when you are ready.

The UI currently falls back to `src/data/mock-data.ts`, so the mock demo works without the Python backend.

## Python API wrapper

To run the thin Python API wrapper locally:

```bash
python scripts/run_web_api.py
```

Then set:

```bash
NEXT_PUBLIC_LEDGER_API_BASE_URL=http://127.0.0.1:8000
NEXT_PUBLIC_LEDGER_API_KEY=dev-viewer-key
```

If the API is unavailable, the frontend automatically falls back to mock data.
If you enable API-key auth on the backend, the frontend will send `NEXT_PUBLIC_LEDGER_API_KEY` as a Bearer token.

## Demo Roles

- `dev-viewer-key`: application list, application detail, and timeline screens
- `dev-reviewer-key`: review queue
- `dev-compliance-key`: compliance screen
- `dev-analyst-key`: agent performance screen
- `dev-admin-key`: full access, including refresh
