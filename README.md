# demo-api

One small FastAPI service that hosts the backend of every portfolio demo, each under its own prefix.
Today: **A1** (Ledgerly Support Assistant, `/a1`), from [a1-rag-docqa](https://github.com/arslandev009/a1-rag-docqa).

```
Cloudflare Pages (static UI + edge gateway: Turnstile, signed session)
      |  X-Gateway-Secret + real client IP
      v
demo-api on Render (free)  --/a1-->  docqa router  -->  Neon Postgres + pgvector (free)
```

- `GET /healthz`: open, for Render's health check (no database, no LLM).
- `/a1/health`, `/a1/sources`, `/a1/chat`: the A1 router; `/a1/sources` and `/a1/chat` need the `X-Gateway-Secret` header.
- No `/docs`, `/redoc` or OpenAPI schema is exposed.
- `docqa` and `llm-client` are installed from their GitHub repos **at pinned commits** (`requirements.txt`), so a deploy is reproducible.

## Environment (Render dashboard, never in the repo)
| Variable | Value |
|---|---|
| `DATABASE_URL` | Neon **direct** (non-pooled) connection string |
| `EMBED_PROVIDER`, `EMBED_MODEL` | `gemini`, `gemini-embedding-001` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | provider key and model id |
| `GATEWAY_SECRET` | long random string; the same value is set in the Pages project |
| `API_ALLOWED_PROVIDERS` | `gemini` |
| `API_BUDGET_STORE` | `db` (the daily LLM-call budget lives in Postgres, so spin-downs cannot reset it) |
| `LLM_LOG_PATH` | `/tmp/llm_calls.jsonl` |

Optional: `API_DAILY_LLM_CALLS` (default 300), `API_RATE_LIMIT_PER_MIN` (default 10, per client IP, in memory).

## Free-hosting behaviour
Render's free web service spins down after 15 minutes without traffic and wakes on the next request (about a minute),
and Neon's free compute also suspends when idle. The demo UI handles this with a warm-up request and a "waking the demo" message.

## Update the A1 backend
Change the two commit hashes in `requirements.txt` (or only the `docqa` one), push, and Render redeploys.

## Local checks
```
pip install -r requirements.txt pytest httpx
pytest -q
docker build -t demo-api:local .
```
