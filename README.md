# demo-api

One small FastAPI service that hosts the backend **and the web UI** of every portfolio demo, one free Render instance for all.
Today: **A1** (Ledgerly Support Assistant) from [a1-rag-docqa](https://github.com/arslandev009/a1-rag-docqa).

```
visitor -> /demo/a1/        static UI (HTML/CSS/JS, served by this service)
        -> POST /session    Cloudflare Turnstile check -> signed 30-minute session cookie
        -> /a1/chat, /a1/sources, /a1/health   docqa router  ->  Neon Postgres + pgvector
```

- `GET /healthz`: Render's health check (no database, no LLM). `GET /` redirects to `/demo/a1/`.
- `/a1/health` is open; `/a1/sources` and `/a1/chat` need a verified session (or the operator token) when protection is on.
- **Protection is optional.** With `TURNSTILE_SECRET` and `SESSION_SECRET` set, a visitor must pass Turnstile once; the session
  id is also the rate-limit key, so no limit depends on client IP headers (those can be spoofed behind a proxy). Without them
  the demo is open (fine for a first deploy, not for a public link).
- No `/docs`, `/redoc` or OpenAPI schema; a strict Content-Security-Policy on every response.
- `docqa` and `llm-client` are installed from their GitHub repos **at pinned commits** (`requirements.txt`).

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

Do **not** set `GATEWAY_SECRET` here: the browser calls the API directly, and that variable would require a header it cannot send.
Optional: `API_DAILY_LLM_CALLS` (default 300), `API_RATE_LIMIT_PER_MIN` (default 10 per session).

## Free-hosting behaviour
Render's free web service spins down after 15 minutes without traffic, so opening the demo page after a quiet period can take
up to a minute while the server wakes up; Neon's free compute also suspends when idle. The video is the instant showcase;
a portfolio page can send a harmless request to `/healthz` when it loads, so the demo is usually awake by the time it is clicked.

## Update the A1 backend
Change the commit hashes in `requirements.txt` (or only the `docqa` one), push, and Render redeploys.

## Local checks
```
pip install -r requirements.txt pytest httpx
pytest -q
docker build -t demo-api:local .
```
