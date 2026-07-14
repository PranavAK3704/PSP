#!/usr/bin/env bash
# Run the Valmo Partner Support Platform locally — NO Docker.
# Backend (uvicorn :8077) + frontend (vite :5190). Credentials load via scripts/load_env.sh.
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "▶ Backend on :8077 ..."
cd "$ROOT/backend"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -r requirements.txt
. scripts/load_env.sh                    # LLM + Turso creds (env-first, baked-file fallback)
(uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8077}" --log-level warning &)

echo "▶ Frontend on :5190 ..."
cd "$ROOT/frontend"
[ -d node_modules ] || npm install
npm run dev -- --port 5190

# Production (GCP App Engine / Cloud Run buildpacks / VM): build the frontend once
# (npm run build), then serve the backend with:
#   . scripts/load_env.sh && uvicorn app.main:app --host 0.0.0.0 --port $PORT
# and inject secrets via GCP Secret Manager instead of the baked data/*.txt files.
# (To refresh the SOP corpus from source repos: python scripts/ingest_knowledge.py)
