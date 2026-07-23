# Deploy to Render — pilot runbook

This deploys the Valmo Partner Support Platform as **one Docker web service** on
Render: FastAPI serves the built React SPA at `/` and the API at `/api/*`
(same-origin, SSE included). Authored config (SOPs, brains, the governance
framework, users) is persisted to **Turso** — durable across redeploys, since the
free tier has no disk. Secrets are set in the Render dashboard — never committed.

> This is the production path. Local dev is unchanged: `./run.sh` (Vite :5190 +
> uvicorn :8077).

## What ships
- `Dockerfile` — multi-stage: node builds the SPA → python serves it + the API.
- `render.yaml` — the web service + a 1 GB disk mounted at `/data` (= `PSP_STATE_DIR`).
- `.dockerignore` — excludes `node_modules`, `.venv`, `dist`, `backend/data/*.db`
  (loss data comes from Turso), `backend/data/*.txt` (secrets come from env).

## 0. Push to GitHub (the repo is ALREADY initialised + committed)
The project is already a clean Git repo (committed, secrets gitignored, and the
Docker image is verified to build + run). Just push it so Render can deploy from it:
```bash
cd /Users/pranav.akella/PSP
git branch -M main
# create an EMPTY GitHub repo (no README/license), then:
git remote add origin git@github.com:<your-org>/valmo-psp.git
git push -u origin main
```
`.gitignore` already keeps `backend/data/*.txt` (LLM/Turso keys), `.env`, and the
loss `.db` out of Git — the working tree has no secrets staged.

## 1. Create the service from the Blueprint
1. Render Dashboard → **New** → **Blueprint**.
2. Connect the GitHub repo you just pushed.
3. Render reads `render.yaml` and proposes a web service `valmo-psp` with a 1 GB
   disk mounted at `/data`. Apply it.

## 2. Set the environment secrets (dashboard only — no real values in Git)
On the service → **Environment**, set these (all declared `sync: false`):

| Key | What it is |
| --- | --- |
| `OPENAI_API_KEY` | LLM gateway key (the active brain) |
| `OPENAI_BASE_URL` | LLM gateway base URL |
| `TURSO_DATABASE_URL` | Turso loss-DB URL |
| `TURSO_AUTH_TOKEN` | Turso auth token |
| `AUTH_SECRET` | random secret that signs session tokens — generate a long random string |
| `INITIAL_ADMIN_EMAIL` | email of the first approver (seeded on first boot) |
| `INITIAL_ADMIN_PASSWORD` | password for that first approver |

`PSP_STATE_DIR=/data` is already set by `render.yaml`. `PORT` is injected by Render
automatically. Generate `AUTH_SECRET` with e.g. `python -c "import secrets;print(secrets.token_urlsafe(48))"`.

## 3. Confirm the disk mount
On the service → **Disks**: confirm `psp-state` is mounted at `/data`. This is
where `users.json`, `blueprints.json`, `kt_queue.json`, `audit_rubric.json`,
`audits.json`, `traces.json`, `concern_log.json`, `cpd_log.json` live — so
authored work and the team roster survive every redeploy.

## 4. Deploy
Trigger the first deploy (auto after Blueprint apply, or **Manual Deploy**). Watch
the build: stage 1 builds the SPA, stage 2 installs the backend. When live, the
health check `GET /api/health` should return `200`.

## 5. First login (seeded approver)
Open the service URL. Sign in with `INITIAL_ADMIN_EMAIL` / `INITIAL_ADMIN_PASSWORD`
(the seeded **approver**). If you did NOT set those env vars, the app seeds a
documented default `admin@valmo.local` / `valmo-admin` and logs a WARNING — change
it immediately by adding a new approver and retiring the default.

## 6. Add your team (Team admin — approver only)
In the **sidebar** (bottom-left) → the **group** icon (visible to approvers) → **Add a member**: email, name,
a temp password, and a role:
- **viewer** — read-only.
- **author** — can draft/compile/queue knowledge (blueprints, SOPs, KT, nuances).
- **approver** — can make content go live (approve blueprint/SOP/KT) and manage the team.

Share each teammate their temp password; they sign in and start authoring. Only
approvers can flip anything live — the server enforces this regardless of the UI.

## Redeploys
Push to `main` → Render rebuilds and redeploys. Because state lives on the `/data`
disk, users, authored blueprints/SOPs/KT and the ledger all persist. The static
knowledge corpus and loss data (Turso) are unaffected.
