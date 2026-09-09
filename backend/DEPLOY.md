# Deploying (P2 Step 9)

## What's done

`backend/Dockerfile`, `.dockerignore`, and a root `docker-compose.yml` are written and ready. They have **not** been verified with an actual `docker build`/`docker run` — Docker isn't installed in the environment this was built in. Test locally before pushing anywhere:

```bash
docker compose up --build
curl http://localhost:8000/
```

## Why deployment itself isn't done

Deploying to Railway or Render means creating/using an account, a project, and a live public URL under your name — that needs your login, not something to do on your behalf without you there. Steps below are exact; someone on the team just needs to click through them once.

## Railway (recommended — simpler for a single container)

1. `railway login`, then from the repo root: `railway init`
2. `railway up` — it'll detect `backend/Dockerfile` via the root `docker-compose.yml`, or point it directly: `railway up --dockerfile backend/Dockerfile`
3. Railway assigns a public URL automatically. Set it as the value P4 uses for `API_BASE`.

## Render

1. New → Web Service → connect the repo.
2. Root directory: leave as repo root. Dockerfile path: `backend/Dockerfile`.
3. Render builds and assigns a public URL.

## The one thing that will bite you: SQLite persistence

**Both platforms' free/starter tiers typically use ephemeral filesystems** — anything written to disk (including `backend/hackathon.db`) can be wiped on every redeploy, restart, or scale event. Right now the whole database — real gazetteer, real Census population data, the 1,000 seeded synthetic reports, everything from `load_gazetteer.py` and `load_demographic_data.py` — lives in that one file.

Before deploying for real, pick one:

1. **Accept the reset** — fine for a hackathon demo if you re-run `load_gazetteer.py` → `load_demographic_data.py` → `seed_synthetic_data.py` right before judging, and don't rely on data surviving between now and demo day.
2. **Attach a persistent volume** — both Railway and Render support mounting a persistent disk at a path; point `DATABASE_URL` (see `backend/.env`) at a file inside that mounted volume instead of the container's own filesystem.
3. **Switch to a managed Postgres** — Railway/Render both offer one-click Postgres addons. This is the "real" fix and matches the build plan's original tech-stack recommendation, but means re-adding `psycopg2-binary` and pointing `DATABASE_URL` at the managed instance — more work than a hackathon deploy strictly needs.

For a 48-hour sprint, option 1 or 2 is the pragmatic choice. Whoever runs the actual deploy should decide and note it here.

## Confirm it works (P2 Step 9's actual finish line)

Once deployed:
```bash
curl https://<your-deployed-url>/
curl https://<your-deployed-url>/citizen-reports
```
Both should respond the same way they do locally. Then update `frontend-test/index.html`'s `API_BASE` (and whatever P4 builds) to point at the real URL instead of `localhost:8000`.
