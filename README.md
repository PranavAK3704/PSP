# Valmo Partner Support Platform — hackathon demo

A runnable slice of the BRD's **resolution engine** (not a ticketing system): a
captain chats by voice or text in Hinglish, and the issue is **resolved inside the
conversation, in seconds** — grounded in real data, gated by a trust spine, with an
adversarial verifier before any money moves. Plus proactive monitoring, the
append-only Concern Log, and the SOP Compiler.

> **Model note:** the active provider is **OpenAI via the gateway, running gpt-5.5
> on every tier** (fast == deep today). The fast/deep tier split is wired so a
> tiered provider can be swapped in without any pipeline change. **Claude and Gemini
> are swappable alternatives** — flip the `provider:` key in
> `backend/config/models.yaml` (no code change). See `PRODUCTION_DELTA.md §D`.

## Run it

```bash
./run.sh
```

That boots the backend on **:8077** and the frontend on **:5190** (opens a browser).
Or run the two halves manually:

```bash
# backend
cd backend && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python scripts/ingest_knowledge.py      # snapshot knowledge from the source repos
uvicorn app.main:app --port 8077

# frontend (new terminal)
cd frontend && npm install && npm run dev
```

**Env:** already set — `backend/.env` has a working OpenAI-gateway key. Nothing to create.

## The demo flow (what to show judges)

1. **Captain Panel** — click the Hinglish sample *"galat debit ₹1240, reversal karo…"*.
   Watch the right panel: the engine extracts intent → grounds in the ledger + Log10
   scans → locates the `hardstop_loss` disposition → runs the Executable Policy's
   checks → passes the **trust gate** → the **adversarial verifier AGREES** → reverses
   the debit idempotently → replies warmly in Hinglish, citing the evidence. No ticket.
2. **Proactive Monitor** — run the monitor for the same captain: a cheap rule
   first-pass fires one risk (a shipment about to breach D5), and only then does an
   LLM compose a partner-protective nudge.
3. **SOP Compiler** — paste a plain-language SOP; watch it compile into a strict
   Executable Policy.
4. **Concern Log** — every resolution is an immutable event; see stats + the ledger.
5. **Architecture** — the six primitives, trust spine, Partner Constitution, and the
   tiered model strategy (OpenAI gpt-5.5 today; Claude / Gemini swappable via config).

## How the repos plug in (easy to replace)

The **only** thing taken from `input-bot` / `valmo-l1-agent` is **knowledge/SOP
data** — no Kapture, no Metabase, no scraping, no L1 code (Kapture is being replaced
by this platform). The seam is one file:

- `backend/config/sources.yaml` — points at the repos' knowledge files today.
- When the new SOP/knowledge base ships, edit those paths and re-run
  `python backend/scripts/ingest_knowledge.py`. Nothing else changes.

## Layout

```
backend/
  config/models.yaml      # tiered model routing — OpenAI active; provider swap seam
  config/sources.yaml     # knowledge source repos — the repo swap seam
  app/llm/                # provider interface (openai_provider active; gemini/claude swappable)
  app/substrate/          # Layer 0: Captain Context + mock connectors (adapter pattern)
  app/knowledge/          # Layer 3: store + SOP compiler + Executable Policies
  app/engine/             # Layer 2: the resolution pipeline + dispositions
  app/trust/              # trust spine: gate + adversarial verifier + Constitution
  app/monitor/            # Layer 4: proactive monitoring
  app/ledger/             # the Concern Log (append-only + problem graph)
  scripts/ingest_knowledge.py
frontend/                 # React/Vite captain panel + live pipeline visualizer
PRODUCTION_DELTA.md       # exactly what's demo vs production, and what's pending
```

See **`PRODUCTION_DELTA.md`** for the full demo-vs-production ledger.
