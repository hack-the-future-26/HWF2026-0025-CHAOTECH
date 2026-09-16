# Deploying AwaazIQ — frontend on Vercel, backend on Railway, database on Supabase

This is a real three-service split, not a single "deploy" button: a static
frontend, a stateful Python API, and a Postgres database that starts empty
and gets filled by this project's own real data loaders. Follow the steps
in order — later steps need environment values only earlier steps produce.

## 0. Before you start

You'll need free accounts on all three platforms:
- **Vercel** — https://vercel.com (sign up with GitHub, so it can read this repo)
- **Railway** — https://railway.app (sign up with GitHub)
- **Supabase** — https://supabase.com (sign up with GitHub)

None of these need a paid plan for this project's current scale.

## 1. Supabase — create the database first

1. Create a new Supabase project (any name/region).
2. Once it's provisioned: **Project Settings → Database → Connection string → URI**.
   Copy the **Session pooler** connection string (not the direct one) — Railway's
   network reaches Supabase's pooler more reliably than the direct IPv6 host.
   It looks like:
   ```
   postgresql://postgres.xxxxxxxxxxxx:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```
3. Replace `[YOUR-PASSWORD]` with the real database password you set when creating
   the project (Supabase shows it once at project creation; reset it under
   Project Settings → Database if you didn't save it).
4. Keep this connection string — it becomes `DATABASE_URL` in step 2.

You do **not** need to create any tables by hand. `backend/create_tables.py`
and the loader scripts build the whole schema and fill it with real data.

## 2. Railway — deploy the backend

1. New Project → Deploy from GitHub repo → pick this repo.
2. **Settings → Root Directory**: set to `backend`. Railway will find
   `backend/railway.json` and use its build/start config automatically.
3. **Variables** tab — add:
   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | the Supabase connection string from step 1 |
   | `AWAAZIQ_POTHOLE_MODEL_URL` | *(optional, see step 2a)* |
   | `AWAAZIQ_VLM_DAMAGE_MODEL` | `0` unless you're about to demo C6 live (see step 4) |
4. Deploy. Railway builds with Nixpacks (Python auto-detected from
   `requirements.txt`) and runs `python create_tables.py && uvicorn main:app
   --host 0.0.0.0 --port $PORT` (from `railway.json`) — this creates every
   table on the fresh Supabase database.
5. Once it's live, note the public URL Railway gives you
   (`https://<something>.up.railway.app`) — you need it in step 3.

### 2a. Getting the real data onto Supabase

The database is empty right after step 2 — no villages, no schools, no
scores. Get a shell on the Railway service (**Railway dashboard → your
service → the "⋮" menu → "Shell"**, or `railway run bash` locally with the
Railway CLI pointed at this service) and run the full loader sequence from
the main `README.md`'s "Data-loading scripts" section, in the order listed
there, finishing with `python -m intelligence.recompute`. This is the exact
same sequence used to build the local `hackathon.db` — it works against
whatever `DATABASE_URL` is set to, SQLite or Postgres.

### 2b. The pothole model file (150 MB, not in git)

Pick one:
- **Simplest**: upload `models/pothole_best.pt` to a Supabase Storage bucket
  (Storage → New bucket → upload the file → copy its public or signed URL),
  and set `AWAAZIQ_POTHOLE_MODEL_URL` to that URL on Railway. The backend
  downloads it once, automatically, the first time a photo is checked.
- **Alternative**: add a Railway volume mounted inside the service and copy
  the file there once via the Railway shell, then set
  `AWAAZIQ_POTHOLE_MODEL` to that path.

Without either, C5 (pothole/crack detection) reports "not available" —
every other check still works, and no complaint is ever rejected because
of it (see `README.md`'s own "Ground rules").

## 3. Vercel — deploy the frontend

1. New Project → Import this repo.
2. **Root Directory**: set to `frontend`.
3. **Framework Preset**: "Other" (this is plain static HTML/JS, no build step).
   Leave Build Command and Output Directory blank.
4. Before deploying, point the frontend at your Railway backend: edit
   `frontend/js/config.js` and set
   ```js
   window.AWAAZIQ_API_BASE = "https://<your-railway-app>.up.railway.app";
   ```
   commit and push — this is the one line every page's own API-resolution
   logic checks before falling back to `localhost` (see the comment in that
   file). Local dev and the ngrok single-origin phone demo are unaffected;
   this only matters once frontend and backend are two separate hosts.
5. Deploy. Vercel gives you a `https://<something>.vercel.app` URL serving
   `index.html` (officials dashboard), `report.html` (citizen form),
   `signin.html`, and `photo-lab.html`.

### 3a. CORS

`backend/main.py` already allows all origins (`allow_origins=["*"]`), so
Vercel calling the Railway API needs no backend change.

## 4. Demoing C6 (building/bridge damage) on the deployed backend

C6 calls a vision-LLM through your own machine's local OmniRoute gateway —
that's deliberate prototype-stage scope (see `README.md`), not something
meant to run unattended in the cloud. To turn it on for a live demo without
changing any code:

1. On your own machine, with the OmniRoute server running (`omniroute serve
   --port 20128 --daemon --no-open` if it isn't already):
   ```
   omniroute tunnel create <type>      # e.g. cloudflare — see `omniroute tunnel --help`
   omniroute tokens create             # prints a real scoped access token
   ```
2. On Railway, set `OMNIROUTE_BASE_URL` to the tunnel's public URL and
   `OMNIROUTE_API_KEY` to the token from step 1, and `AWAAZIQ_VLM_DAMAGE_MODEL=1`.
3. When you're done demoing, unset those two variables (or set
   `AWAAZIQ_VLM_DAMAGE_MODEL=0`) and stop the tunnel — C6 falls back to its
   proxy signal automatically the moment the gateway is unreachable, so
   nothing breaks either way.

Note: the `omniroute` npm package would also need to be installed on the
Railway service for this specific path (it isn't part of `backend/
requirements.txt`, which is Python-only) — not set up in this pass, since
it's an on-demand demo feature, not a default-on one. Ask if you want that
wired into the Railway build too.

## 5. Verifying it worked

- `https://<railway-app>.up.railway.app/docs` — FastAPI's interactive API
  docs; if this loads, the backend and its DB connection are healthy.
- `https://<vercel-app>.vercel.app/index.html` — should show real villages
  on the map once step 2a's loaders have run.
- `https://<vercel-app>.vercel.app/report.html` — file a real test report
  end to end; check it shows up via `/index.html`.
