# demo-api

One small FastAPI service that hosts the backend **and the web UI** of every portfolio demo, one free Render instance for all.
Today: **A1** (Ledgerly Support Assistant) from [a1-rag-docqa](https://github.com/arslandev009/a1-rag-docqa) and
**A2** (invoice extraction) from [a2-extraction-api](https://github.com/arslandev009/a2-extraction-api).

```
visitor -> /demo/a1/        static UI (HTML/CSS/JS, served by this service)
        -> POST /session    optional visitor check (Cloudflare Turnstile, off unless configured) -> signed session cookie
        -> /a1/chat, /a1/sources, /a1/health   docqa router  ->  Neon Postgres + pgvector
        -> /demo/a2/        static UI of A2 (invoice extraction)
        -> /a2/demo/samples, /a2/demo/extract   bundled sample PDFs  ->  extractor pipeline  ->  checked JSON
```

- `GET /healthz`: Render's health check (no database, no LLM). `GET /` redirects to `/demo/a1/`.
- `/a1/health` is open; `/a1/sources` and `/a1/chat` need a verified session (or the operator token) when protection is on.
- **Protection is optional.** With `TURNSTILE_SECRET` and `SESSION_SECRET` set, a visitor must pass Turnstile once; the session
  id is also the rate-limit key, so no limit depends on client IP headers (those can be spoofed behind a proxy). Without them
  the demo is open (fine for a first deploy, not for a public link).
- No `/docs`, `/redoc` or OpenAPI schema; a strict Content-Security-Policy on every response.
- `docqa` and `llm-client` are installed from their GitHub repos **at pinned commits** (`requirements.txt`).

## How the repos fit together
This repo is only the host: glue code, visitor protection, the UI files and the Dockerfile. The project code (`docqa`,
`a2-extraction`, `llm-client`) is installed from GitHub at the commits pinned in `requirements.txt` while the Docker image is
built. `/a1` and `/a2` are URL prefixes inside one FastAPI process, not separate servers. A new commit in a project repo reaches
the live demo only after its pin is changed here.

## A2: invoice extraction (sample-only demo)
- UI at `/demo/a2/`: the same Turnstile and session flow as A1; it reuses A1's stylesheet and favicon.
- Public routes: `GET /a2/demo/samples` and `POST /a2/demo/extract` with `{"sample": "<id>"}`. The visitor picks one of the
  8 sample PDFs bundled in `static/a2/samples/`; the server accepts only ids listed in `samples.json`. **Nothing can be uploaded**:
  free LLM tiers may log their inputs.
- On these routes: visitor session (when protection is on), a rate limit per session (per client when protection is off), a
  short result cache, and the **same daily LLM-call budget as A1** (table `api_usage`). A scanned PDF has no text layer and never
  reaches the model (0 AI calls). Errors: 429 rate limit, 503 budget used up or an unexpected failure, 502 provider failure;
  internal error text is never returned.
- A2's own upload router (`POST /a2/extract`, `GET /a2/health`) is mounted, but answers 404 unless `X-Operator-Token` matches
  `OPERATOR_TOKEN`. It is for smoke tests with real files.
- A2 uses the default LLM provider of A1's runtime (`API_ALLOWED_PROVIDERS`).
- `static/a2/samples/` is produced by `python tools/a2_assets.py` from a sibling `a2-extraction-api` checkout: it copies the PDFs
  and renders PNG previews (the CSP forbids PDF iframes).

## Environment (Render dashboard, never in the repo)
| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon **direct** (non-pooled) connection string |
| `EMBED_PROVIDER`, `EMBED_MODEL` | `gemini`, `gemini-embedding-001` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | provider key and model id |
| `API_ALLOWED_PROVIDERS` | `gemini` |
| `API_BUDGET_STORE` | `db` (the daily LLM-call budget lives in Postgres, so spin-downs cannot reset it) |
| `LLM_LOG_PATH` | `/tmp/llm_calls.jsonl` |
| `OPERATOR_TOKEN` | long random string; lets smoke tests call the API with `X-Operator-Token` (no browser) |
| `TURNSTILE_SECRET`, `SESSION_SECRET`, `TURNSTILE_SITE_KEY` | turn visitor protection on (secret key, random string, public site key) |
| `DEMO_VIDEO_URL` | optional: shows the walkthrough video link in the UI without a new deploy |
| `A2_VIDEO_URL`, `A2_REPO_URL`, `A2_README_URL` | optional: links shown in the A2 UI (`/demo/a2/config.json`) without a new deploy |

Do **not** set `GATEWAY_SECRET` here: the browser calls the API directly, and that variable would require a header it cannot send.
Optional: `API_DAILY_LLM_CALLS` (default 300), `API_RATE_LIMIT_PER_MIN` (default 10 per session).
A2 optional: `A2_RATE_LIMIT_PER_MIN` (default 6 per session), `A2_CACHE_TTL_S` (default 600 seconds, `0` turns the cache off),
`DEFAULT_CURRENCY` (passed to the extractor as its default currency).

## Free-hosting behaviour
Render's free web service spins down after 15 minutes without traffic, so opening the demo page after a quiet period can take
up to a minute while the server wakes up; Neon's free compute also suspends when idle. The video is the instant showcase;
a portfolio page can send a harmless request to `/healthz` when it loads, so the demo is usually awake by the time it is clicked.

## Update a backend (A1 or A2)
Change the commit hash of the package in `requirements.txt` (`docqa` for A1, `a2-extraction` for A2, `llm-client` for the
shared client), run `pytest -q`, push, and Render redeploys.

## Local checks
```
pip install -r requirements.txt pytest httpx
pytest -q
docker build -t demo-api:local .
```
