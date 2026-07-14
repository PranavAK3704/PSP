# syntax=docker/dockerfile:1
# Valmo Partner Support Platform — PRODUCTION image for Render.
# ONE image: FastAPI serves the built React SPA at "/" and the API at "/api/*"
# (same-origin, SSE included). This is unrelated to the archived hackathon nginx
# flow in _deploy_archive/ — local dev still uses run.sh (Vite on :5190).

# ---- Stage 1: build the React (Vite) frontend --------------------------------
FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build            # -> /fe/dist

# ---- Stage 2: Python backend serving the built SPA (final image) -------------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app/backend

# Backend deps first (better layer caching).
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Backend source. Secrets (backend/data/*.txt) and the big loss DB
# (backend/data/*.db) are excluded via .dockerignore — in prod the LLM/Turso
# credentials come from env and the loss data is read from Turso.
COPY backend/ /app/backend/

# Built SPA from stage 1 → served by FastAPI (main.py resolves ../../frontend/dist).
COPY --from=frontend /fe/dist /app/frontend/dist

# Render injects $PORT at runtime; default to 8077 for a plain `docker run`.
ENV PORT=8077
EXPOSE 8077
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8077}"]
