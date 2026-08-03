# Valmo Partner Support Platform

An LLM-driven **resolution engine** (not a ticketing system): a delivery partner ("captain"
or pilot) chats by voice or text in Hinglish and the issue is **resolved inside the
conversation, in seconds** — grounded in real data, gated by a trust spine + an adversarial
verifier before any money moves. Around that core: proactive monitoring, an append-only
Concern Log, the SOP Compiler, an L3 escalation platform, audit/CPD + a governance-conformance
loop, role-based access, and a Kapture-ticket auditing engine.

> **Status: working demo, production-in-progress.** Today it runs as a single Docker image on
> **Render**, backed by **Turso** — a hackathon deployment, *not* production-grade. The
> production target is the **Meesho managed stack**: GKE via the standard CI/CD path, Cloud SQL,
> Vault/External-Secrets, Meesho SSO, **PrismSDK** for Gold/Platinum data, and the Meesho
> observability stack. The migration is tracked in **[`PRODUCTION_DELTA.md`](PRODUCTION_DELTA.md)** —
> the gap scorecard against Meesho's "Builders → Production" checklist, what's done, and what's pending.

## Production readiness (Meesho "Builders → Production")
- **Done in-repo (no Meesho infra needed):** AUTH_SECRET signs with a random per-process secret
  when unset (no forgeable public constant); CORS restricted to an allowlist (no wildcard);
  structured request logging + a correlation id (`X-Request-Id`); PII columns dropped from the
  loss-data build (names / worker IDs / evidence-image URLs / remarks); `python-multipart` bumped
  past CVE-2024-53981. RBAC + pbkdf2 password hashing + `/api/health` were already in place.
- **Pending — needs Meesho infra / POCs:** GKE + CI/CD (Jenkins/DevOps-Lib → Turbo-Turtle →
  Helm/ArgoCD), **Cloud SQL** (replace Turso), **Vault/External-Secrets**, **Meesho SSO**, the
  observability stack, an approved enterprise **LLM path** (replace the hackathon gateway), and
  **PrismSDK** for Gold/Platinum data (replacing the Metabase design).

The full scorecard, the three migration tracks, and the **"what we need from Meesho"** list are in
[`PRODUCTION_DELTA.md`](PRODUCTION_DELTA.md).

## Run it (local demo)
```bash
./run.sh      # backend :8077 + frontend :5190 (opens a browser)
```
Or the two halves manually:
```bash
cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
python scripts/ingest_knowledge.py       # snapshot knowledge from the source repos
uvicorn app.main:app --port 8077
# new terminal
cd frontend && npm install && npm run dev
```
**Env:** set `AUTH_SECRET` (any strong string) + `INITIAL_ADMIN_EMAIL`/`INITIAL_ADMIN_PASSWORD`;
the LLM key + `TURSO_*` come from `backend/data/*` or env (never committed — see `.gitignore`).
In production these move to the platform secret store (Vault).

## Model
OpenAI **gpt-5.5** on every tier via the gateway (fast == deep today). Claude / Gemini are
swappable — flip `provider:` in `backend/config/models.yaml`, no pipeline change. In production
this becomes the **approved enterprise LLM path**.

## Layout
```
backend/
  config/models.yaml      # tiered model routing — OpenAI active; provider swap seam
  config/sources.yaml     # knowledge source repos — the ingest swap seam
  app/llm/                # provider interface (openai active; gemini/claude swappable)
  app/substrate/          # data layer: Captain Context + connectors (Metabase stub → PrismSDK)
  app/knowledge/          # store + SOP compiler + governance framework/conformance
  app/engine/             # the agentic resolution loop + tools + dispositions
  app/trust/              # trust spine: gate + adversarial verifier + Partner Constitution
  app/l3/                 # L3 functional-team escalation platform (SLA + breach ladder)
  app/ledger/             # append-only Concern Log + per-concern trace
  app/audit/              # Auditing Studio (rubric + judge) + Kapture-ticket audit engine
  app/auth/               # signed-token RBAC (→ Meesho SSO in production)
  app/monitor/            # proactive monitoring
  scripts/                # knowledge ingest + the loss-data build/sync
frontend/                 # React/Vite panels + live pipeline visualizer
```

See **[`PRODUCTION_DELTA.md`](PRODUCTION_DELTA.md)** (production readiness + demo-vs-prod ledger)
and **[`PRODUCT_VISION.md`](PRODUCT_VISION.md)** (the north star).
