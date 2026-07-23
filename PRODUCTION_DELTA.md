# Production Delta — Demo vs. the BRD-grade build

This is the honest ledger of **what the hackathon demo does differently from
production** and **what is pending** to make it the real thing. Everything below
was designed so the swap is a config/connector change, not a rewrite (BRD §15.2:
"everything that changes is data/config, not code").

> **v4.0 build note.** The reactive path is now an **LLM-driven bounded agentic
> tool-use loop** (not a hardcoded state machine). The model runs the conversation;
> deterministic code runs only as tools — `apply_policy` is the sole money path
> (checks + gate + adversarial verifier). See §J for the full v4.0 deltas.

---

## A. Done differently from production (deliberate demo substitutions)

| # | Area | Demo build | Production (per BRD) | Where to swap |
|---|------|-----------|----------------------|---------------|
| 1 | **Reasoning model** | **OpenAI `gpt-5.5` on every tier** via the gateway (fast == deep today); the fast/deep split + Claude/Gemini are config-swappable | Claude tiers — **and a free/cheap tool-capable model on the ~70% conversation tier** (Groq Llama 3.3 / OSS), premium only for the money path (§10) | `config/models.yaml` → `provider` + per-node tier; `app/llm/claude_provider.py` written |
| 2 | **Data substrate** | `DemoDataProvider` returns canned **Metabase-shaped query results** from seed (`app/substrate/seed.py`) | `MetabaseProvider` runs **parameterized queries against Meesho DB root** (via Metabase), assembled by the Captain Context service (§6.1–6.2) | `app/substrate/captain_context.py::_provider` → `MetabaseProvider`; set `METABASE_*` env |
| 3 | **Disposition retrieval** | Lexical/keyword scorer over the corpus (`app/knowledge/store.py`) | Voyage AI (or self-hosted BGE/E5) embeddings + pgvector (§10, §12) | `store.retrieve()` — contract unchanged |
| 4 | **Datastore** | Append-only **JSON**, now written through to **Turso** (durable across free-tier restarts — no disk) via `durable_state.py` | Postgres (Concern Log, decisions, audit) + pgvector (§8, §12) | `app/ledger/concern_log.py`, `app/knowledge/*` persistence |
| 5 | **Event stream** | Monitor scans a captain on demand (button) | Kafka / managed pub/sub, always-on consumer fleet (§5, §12, §14) | `app/monitor/` — subscribe to the stream instead of on-demand scan |
| 6 | **Voice (ASR/TTS)** | **Full in-app conversation mode** — browser Web Speech STT+TTS, hands-free loop, reactive orb | Whisper (Groq) ASR + Sarvam Indic TTS (§10) | frontend `CaptainPanel` voice; add server ASR/TTS nodes |
| 7 | **Money "write"** | `_act()` records an idempotent write (nothing real moves) | Real financial write-back with idempotency keys + exactly-once (§13) | `app/engine/pipeline.py::_act` → real payments API |
| 8 | **SOP source** | Knowledge **ingested from your 3 repos** (snapshot) | Live SOP authoring tool → SOP Compiler → Executable Policies (§4.3) | `config/sources.yaml` → new paths, re-run `scripts/ingest_knowledge.py` |
| 9 | **Adversarial verifier** | Second Gemini call (deep tier) | Second Claude model (Opus for high-value) (§11) | same registry node `adversarial_verify` |
| 10 | **Auth / identity** | **Built** — signed-token login + server-enforced RBAC (Admin/Editor/Viewer) + team management; the *captain* is still picked from a dropdown for the demo | Login-scoped Captain Panel handoff + full RBAC/SSO (§13) | `app/auth/*` done; captain SSO handoff pending |
| 11 | **Calibrated confidence** | Fixed threshold `0.80` with model-reported confidence | Continuously **recalibrated** probability so 90%-confident ≈ 90% right (§11) | `app/trust/gate.py::CONFIDENCE_THRESHOLD` + a calibration job |
| 12 | **Frontend home** | Standalone React demo UI | Embedded React widget/iframe **inside the Captain Panel** (§12) | frontend embed |

---

## B. Faithfully real in the demo (not faked)

These are production-shaped already, so judges see the real thing:

- **The deterministic pipeline** — intent → ground → locate disposition → apply
  compiled policy → trust gate → adversarial verify → ACT/escalate → explain →
  learn. One bounded LLM call per reasoning node; money decision executed in code.
- **Executable Policies** — real declarative checks run in code against grounded
  data (the hardstop `HS_1_1` logic + your `losses_sla.csv` D5 TAT matrix).
- **The trust spine** — grounded generation (every claim cites a source row),
  policy-as-code money cap, confidence gate, and a genuine adversarial verifier
  that *actually refutes* when evidence is weak (you saw it flip).
- **Partner Constitution** — 9 principles enforced on every decision.
- **Concern Log** — real append-only ledger + problem graph, with live stats.
- **Proactive monitoring** — real cheap-first-pass rule filter; LLM invoked only
  when a risk fires (the cost-discipline claim in §5.2).
- **SOP Compiler** — really compiles plain text → Executable Policy via the deep tier.
- **Tiered model routing** — real per-node tier selection via `models.yaml`.
- **Knowledge** — **422 real chunks from all three repos** (input-bot 6 files, valmo-l1-agent
  sop_structured + stage0_domain + valmo_kt, valmo-platform seedSops). Every SOP/KT/FAQ.
- **Intent routing** — real `request_type` + `about_own_account` classification: only a
  concrete **action request** runs a money policy; explain/status/general questions are
  answered from retrieved SOP knowledge (grounded RAG), no money movement, no fabricated action.
- **Query-based provenance** — every evidence row is tagged with the Metabase query that
  would produce it (`get_loss_attribution`, `get_shipment_scan_history`, …).
- **Stateful multi-turn conversation** — real session store + SOP-driven slot-filling +
  the 3-way missing-data branch (provide / "don't have→where" / drop-off→friction).
- **Data-query path** — the LLM *selects* a whitelisted named query; it runs deterministically
  (LLM never touches the DB); the rows are answered over. ("where is my shipment / payout")
- **Log10 as a distinct connector** — scans/shipments separated from the Metabase account
  data; Captain Context composes both with per-source provenance.
- **WhatsApp channel** — real webhook adapter + phone→captain mapping, driving the same engine
  multi-turn (demo echoes outbound; live = Graph API POST).
- **L3 functional-team platform** — escalation inbox, SLA timer, breach ladder (Team POC →
  Kaizen → GM → CXO), per-team ownership metrics, governance **placeholder**.
- **KT engine** — free-form contribution → LLM-structured policy/procedure → approval queue.
- **Audit + CPD** — full audit trail from the Concern Log + satisfaction prompt (👍/👎) →
  dissatisfaction logged as a CPD item.
- **Policies vs Procedures** — knowledge typed at ingest (49 policy / 373 procedure).

---

## C. Data-access decision (confirmed with the team)

We will get **Meesho DB root access via Metabase**. Of the three ways to feed data
to the engine:

- **(a) Let the model query the DB directly** — rejected. Worst latency (multi-hop
  tool loops), highest cost (rows + schema burn tokens every call), non-deterministic,
  and a SQL/prompt-injection surface. Violates the "execute deterministically" spine.
- **(b) Parameterized queries** (Metabase cards / SQL by `captain_id`/AWB) — **the
  default.** Indexed, ~ms, and **adds ZERO LLM cost** (tokens are spent only reasoning
  over the pulled rows). Deterministic, auditable, cacheable.
- **(c) Materialized view / read-model** — **for the hot paths.** Lowest read latency
  at 10k scale; small refresh-job cost; watch staleness.

**Chosen: hybrid.** Reactive chat → parameterized queries, cached in Redis for the
conversation. Proactive monitoring at 10k → feed off the Captain Panel event stream
(post-migration) or a CDC-refreshed materialized view — **never** query per-captain-
per-interval. The model never issues SQL.

**BRD cost note (gap to fold in):** the data-access choice adds **no LLM cost**, so the
§10.2 token envelope (₹4.5–7L/mo) is unchanged. The incremental is *infra only* — a
read replica + a Redis Captain-Context cache + a view-refresh job — which is partially
inside §14 (Redis + always-on compute) but is **not line-itemed**. Add a §14 line:
"Meesho DB read (replica/Metabase) + Captain-Context read-model/cache — ~₹15–40k/mo."

---

## D. Build-now queue (sequenced — what to do next)

Ordered so each unblocks the next. Per §18, "the CRM/ops plumbing is the 80%".

**Now — foundation**
1. **Wire `MetabaseProvider`** — implement the parameterized queries in
   `metabase_provider.py` against the read replica; assemble in the Captain Context
   service; cache in Redis. (Blocks everything data-grounded.)
2. **Postgres + pgvector** — migrate the Concern Log, policies, and knowledge off JSON
   files (schema in §8). Move retrieval to real embeddings (Voyage/BGE).
3. **Generic check-interpreter** — make the SOP module fully data-driven: evaluate any
   compiled policy's declarative `checks` against grounded data, so a new SOP needs
   **no new executor code**. (Removes the last code coupling; see §5 of this build.)

**Next — as new SOPs land (not yet drafted)**
4. Each new plain-language SOP → **SOP Compiler → Executable Policy** → auto-registered
   by disposition. New data need = a new named query the SOP declares.
5. **Captain Panel event ingestion** (migration Aug–Oct) — subscribe to panel events
   (push, not poll) to drive the monitor and to keep the Captain Context read-model warm.

**Then — trust, autonomy, ops**
6. **Financial write-back** — idempotency + exactly-once; human-in-loop until a
   disposition earns autonomy (§13). Highest-risk surface; ship last.
7. **Phase 0 shadow run + measured D** per disposition — gates all autonomy (§16).
8. **Auth / RBAC / PII / residency / secrets / immutable audit log** (§13).
9. **End-to-end tracing** (OTel, one `trace_id`) + deterministic replay (§11, §12).
10. **Eval harness** — golden set + regression gate, LLM-as-judge + human spot-check,
    online shadow eval, canary + auto-rollback (§15.1).
11. **Per-disposition kill-switches + feature flags** (§11).
12. **Keep-warm deploy** — Cloud Run min-instances, backpressure, self-host
    embeddings+ASR at peak (§10.3, §14).

**Surfaces to build (new — from the dashboards discussion)**
13. **Functional-team workspace** — RBAC-scoped escalation inbox; each Concern arrives
    pre-worked (timeline, pulled rows, failed check, partner's strongest argument);
    actions: resolve / approve / reject / request-info / author-SOP; TAT clock.
14. **Ops control tower** — defined metrics (D, resolution-in-conversation %, escalation
    rate, calibration, CSAT, pendencies by POC, ₹ recovered) + raw drill-down to the
    full execution trace of any Concern.
15. **Custom-metric builder** — user defines `{name, formula, description}` over a
    **field catalog**; if a referenced field isn't instrumented anywhere in the workflow,
    the dashboard **calls it out** ("can't compute — instrument field X here").
16. **CPD board** (NOVEL clusters → author-an-SOP), **autonomy control panel**,
    **calibration/drift monitor**, **P&L guardrail tracker** (Tier-3 budget), **cost
    dashboard**, **deterministic-replay** view.

**Later**
17. Whisper ASR + Sarvam TTS server-side voice (§10).
18. Automatic knowledge conflict-dedup resolution (`[LATER]`, §4.5).
19. SOP authoring-tool integration (demo consumes SOPs, does not author them, §4.3).

---

## F. Integration deltas (external systems — what each needs to go live)

Every integration is behind an adapter/provider, so "going live" = provisioning
credentials + implementing one method, not reworking the engine.

| Integration | Demo state | To go live (the delta) | Owner / blocker |
|---|---|---|---|
| **WhatsApp Business** (channel) | `channels/whatsapp.py` parses webhooks + drives the engine multi-turn; outbound is echoed | Meta Business account + **verified phone number** + permanent access token + a **public HTTPS webhook** (verify token); implement `send()` as a Graph API POST; store phone→captain map in the captain master | Meta approval + hosting. **Not doable in this sandbox** — needs a public URL + Meta account |
| **Captain Panel** (channel) | standalone React demo | Embed the widget as a tab/iframe; login-scoped captain id handoff | Captain Panel team; migration Aug–Oct |
| **Log10** (scans/shipments) | `Log10Connector` returns canned scans/shipments | Set `LOG10_API_BASE`/`LOG10_API_KEY`; implement `get_scans`/`get_shipments` against the Log10 API | Log10 API contract + key (open dependency) |
| **Meesho DB via Metabase** (account data) | `DemoDataProvider` returns canned rows | Swap to `MetabaseProvider`; set `METABASE_URL`/session; implement the named parameterized queries against the read replica | DB root / Metabase access |
| **Money write-back** | `_act()` records an idempotent write; nothing moves | Real payments API with idempotency keys + exactly-once; human-in-loop until autonomy earned | Finance sign-off (highest risk) |
| **Voice (ASR/TTS)** | browser STT for input only | Whisper (Groq) ASR + Sarvam Indic TTS as server nodes | keys exist in input-bot; wiring pending |
| **Multimodal (files/images/audio)** | not built | Provider `read_attachment()` (Gemini/Claude are multimodal) + upload intake into the gather step | **Feasible, not yet built** — next core build |
| **Event stream** (proactive monitor) | on-demand scan | Subscribe to Captain Panel / Log10 events (Kafka/pub-sub) or CDC view; never poll per-captain | migration + infra |

## G. API deltas (endpoints — real vs stubbed)

All live now on the demo backend (`:8077`). "Stub" = shape is correct, swap the body.

| Endpoint | Method | State | Note |
|---|---|---|---|
| `/api/chat` | POST (SSE) | **real** | Stateful, multi-turn; `conversation_id` + `need_input`/`resolved` |
| `/api/whatsapp/webhook` | POST | **real (local)** | Parses webhook, runs a turn keyed by phone; `send()` stubbed for Graph API |
| `/api/monitor/{id}` | GET (SSE) | **real** | On-demand; event-stream subscription pending |
| `/api/l3/inbox` | GET | **real** | Escalations + SLA/breach/ladder + team metrics (from Concern Log) |
| `/api/kt/submit` `/api/kt` `/api/kt/review` | POST/GET/POST | **real** | Structure → approval queue → approve/reject |
| `/api/satisfaction` | POST | **real** | 👍/👎 → CPD |
| `/api/audit` `/api/insights` | GET | **real** | Audit trail, CPD feed, CSAT, aggregate metrics |
| `/api/sop/compile` | POST | **real** | Plain SOP → Executable Policy |
| `/api/captains` `/api/captain/{id}` `/api/ledger` `/api/dispositions` `/api/policies` `/api/constitution` `/api/knowledge/search` | GET | **real** | Read models |

**Governance** — 🟡 mostly built. The editable **Governance Framework** (dimensions / bands /
metrics / accountability, or structured from an uploaded doc) and the **SOP conformance loop**
(`governance.classify_band` / `check_conformance` → a compile-stream stage + `/api/sop/conformance`
+ an approve-time gate on high-severity violations) are live. What's still a placeholder: the seed
framework *content* (a clearly-labelled example — swap in Valmo's real bands) and the
`l3.platform._governance_placeholder` severity scorer.

## H. Feasibility deltas (what genuinely cannot run in this environment)

Honest constraints — none are engine problems; all are access/provisioning:

1. **Real WhatsApp** — needs a Meta Business account, a verified number, a permanent
   token, and a public HTTPS webhook. The sandbox has no public URL / Meta account. The
   adapter is built and correct; it can't complete the round-trip to Meta here.
2. **Real Log10 / Meesho DB** — no network credentials in the sandbox. Connectors +
   canned data are in place; flip to live by setting env + implementing the one call.
3. **Real money movement** — no payments API access, and it must stay gated behind
   finance sign-off + Phase-0 measured D regardless.
4. **Multimodal ingest** — *feasible* (models are multimodal) but **not yet built**; it's
   the next core build (upload → `read_attachment()` → gather step).
5. **Claude** — no `ANTHROPIC_API_KEY`; Gemini stands in tier-for-tier (§E flips it).
6. **Persistence at scale** — JSON files, not Postgres/pgvector/Redis (fine for demo,
   not for 10k concurrent).

**If you can unblock any of these** (a WhatsApp test number + token, a Log10 staging
endpoint, a Metabase read login, or an Anthropic key), tell me and I'll wire it live —
each is a small, contained change.

## J. v4.0 deltas (this build cycle)

**Agentic conversation (replaces the hardcoded state machine).** The reactive path is
now an LLM-driven bounded agentic tool-use loop (`app/engine/conversation.py`). The model
converses naturally and calls tools (`app/engine/tools.py`): `search_sops`,
`get_captain_context`, `run_data_query`, and **`apply_policy`** — the ONLY money path,
which runs the Executable-Policy checks + trust gate + adversarial verifier + idempotent
write in code. Intelligence in the model; guarantees in the tools. This is what fixed the
brittle keyword/gather loops (tampered-shipment, "no debit yet / future", record leakage).

**Free/cheap conversational tier (config).** Because money cannot move without the
deterministic `apply_policy` tool + premium verifier, the ~70% conversational traffic can
run a **free/near-free tool-capable model** (Groq Llama 3.3 70B, a free-tier Flash, or
self-hosted OSS). Premium (Claude Opus / Gemini Pro) is reserved for `apply_policy`
reasoning, the adversarial verifier, and SOP compilation. Requirement: reliable
function-calling. Swap per-node in `config/models.yaml`.

**Policies vs Procedures (typed + LLM-processed).** Knowledge is typed **policy** (rigid
partner-support rule Valmo owns — binding, favours the partner) vs **procedure**
(functional-team / supply-chain process). Typed at ingest (`scripts/ingest_knowledge.py`,
61 policy / 405 procedure) AND by the KT engine at contribution time; surfaced through
`store.retrieve` and the `search_sops` tool; the agent is instructed policies win conflicts.
The **structuring of incoming knowledge is LLM-handled** (KT engine + SOP Compiler).

**Canonical knowledge sourcing.** Ingest pulls the LIVE SOP Redressal Tracker (published
CSV) + the Collated SOPs doc as source-of-truth (deprecating the stale repo snapshot);
form/template links are preserved and surfaced to the partner verbatim (deterministic
append if the model omits one).

**New human/ops surfaces (built):** WhatsApp channel adapter (`app/channels/`), L3
functional-team platform (`app/l3/` — inbox, SLA, breach ladder, ownership; governance =
placeholder), KT engine (`app/kt/`), Audit + CPD + satisfaction (`app/audit/`), Ops control
tower (frontend `Insights`). All read from the Concern Log.

**Economics update.** Kapture confirmed at **₹1.2 Cr/quarter = ₹4.8 Cr/year** (was a
₹4–8 Cr/yr range). Data-access adds no LLM cost; add a §14 line for DB read
replica/Metabase + Captain-Context cache (~₹15–40k/mo).

**Dead code pruned.** `app/engine/{intent,answer,explain,pipeline}.py` (superseded by the agent
loop) are gone. A later verified sweep removed ~50 more dead items: unused imports across ~12
modules, the orphaned `/api/sop/gaps` endpoint + `knowledge/gaps.py`, `openai_provider._parse_json`,
the unused `Check` dataclass, dead CSS blocks, and stale build artifacts (`dist/`, `.agent-memory/`).
Intentional future seams (the swappable Claude/Gemini providers, the Metabase/Log10/WhatsApp
adapters, the tiered `models.yaml` nodes) were explicitly kept. See §M.

---

## K. LLM gateway + credential deltas (hackathon Bifrost gateway)

The demo runs on the hackathon **Bifrost gateway** (`gateway-buildathon.ltl.sh`,
OpenAI-compatible). The active config runs **`gpt-5.5` on every tier** (fast == deep today);
the fast/deep split is wired so a cheaper conversational model can slot in without a pipeline
change. Getting a server-side app onto it surfaced several gateway-specific realities, all
handled in `app/llm/openai_provider.py`:

- **Edge WAF blocks by client fingerprint.** The gateway sits behind an Akamai WAF that
  403s "Access Denied" to non-browser clients. A `User-Agent` alone is insufficient — the
  full browser signal set is required (`sec-ch-ua`, `Sec-Fetch-*`, `Accept-Language`,
  `Origin`, `Referer`). Verified: minimal headers → 403, full set → 200. **Fixed in code.**
- **Edge WAF also blocks by origin IP/ASN (layer 2).** Even with correct headers, requests
  from a **datacenter egress IP** (the deploy cluster) are 403'd, while an office-network
  laptop passes. This is *not* app-fixable — it needs the organizers to either expose a
  cluster-internal endpoint or allowlist the cluster egress IP/ASN. **Open (organizer infra).**
  The hook is wired: drop the internal URL in `backend/data/llm_base_url.txt` → redeploy.
- **`gpt-5.5` rejects custom `temperature`** (GPT-5/o-series reasoning models allow only the
  default). The provider omits `temperature` for `gpt-5*`/`o1*`/`o3*`/`o4*` models,
  sends `0.1` for `gpt-4o`. `_supports_custom_temperature()`.
- **~70–75% flaky `401 virtual_key_not_found`** (per-request routing miss). Retried hard
  (jittered); residual 403 retried with backoff. Prod (direct Claude API) has none of this.

**Credential strategy — env-first, baked-file fallback** (`docker-entrypoint.sh`): a real
runtime env var always wins; otherwise the entrypoint bakes a key from `backend/data/`
(`llm_key.txt` → `OPENAI_API_KEY`, `anthropic_key.txt` → `ANTHROPIC_API_KEY`,
`gemini_key.txt` → `GEMINI_API_KEY`). This is required because the hackathon deploy passes
**no runtime env vars**, yet keeps the app 12-factor-friendly for prod (inject env, no
rebuild). `backend/data/` is excluded from the source-submission zip (never ships the key
in source) but IS copied into the image (so the container has it). **Production:** no baked
key — inject `ANTHROPIC_API_KEY` via the platform secret store.

---

## I. Migrating to Claude (the production end state) — a true drop-in

The `anthropic` SDK is now in `requirements.txt` (baked into the image) and
`ClaudeProvider` is fully implemented — including the tool-using `chat()` method with
Gemini↔Anthropic message + tool-id translation (`app/llm/claude_provider.py`). So the
swap is config + key only, **no pipeline code changes**:

1. Set `provider: claude` in `backend/config/models.yaml` (Claude tiers already point at
   `claude-haiku-4-5-20251001` / `claude-opus-4-8`).
2. Supply the key by **either** path (env-first, baked fallback — see §K):
   - **Local:** `export ANTHROPIC_API_KEY=sk-ant-...` before starting uvicorn, **or**
   - **Deployed (no runtime env):** write the key to `backend/data/anthropic_key.txt`
     (the entrypoint bakes it into `ANTHROPIC_API_KEY`), then redeploy.
3. Restart / redeploy — done.

Optional: `backend/data/anthropic_base_url.txt` overrides the Anthropic base URL (e.g. a
gateway), mirroring the OpenAI `llm_base_url.txt` hook.

---

## L. Real loss-data layer (Metabase → SQLite snapshot)

**What.** The engine now resolves loss/debit disputes against a **real data snapshot**, not
seed fixtures. `scripts/build_valmo_db.py` loads a Metabase export of the loss query
(`gold.valmo_lost_awb_2k24_v1`) into `backend/data/valmo.db` (SQLite, indexed on AWB) — a
PII-stripped, stratified subset (~37k rows across all `reason_l1`, 6.5 MB, shippable in the
image). `app/substrate/loss_db.py` looks up a disputed AWB locally; `policy_exec._eval_real_loss`
decides from the real fields.

**Why SQLite, not live Metabase** (per the ops team's own pattern): no real-time need for
disputes; a live Metabase session token is brittle/slow/rate-limited and can't live in a
container. A scheduled snapshot → local SQLite is fast, offline, and has no live dependency.
Also fixes the earlier "no DB in the container" gap — the baked `valmo.db` means the DEPLOYED
app does real verifications/reversals; the honest "couldn't verify → escalate with CN" path
(§WS2) becomes the true fallback only for AWBs not in the snapshot.

**Data-grounded decisioning.** The disputed AWB's row decides everything — the LLM does not:
- `reason_l1` (15 real categories) → the **disposition + owning team** (`policies._TAXONOMY`).
- Two real reversal signals: **`facility_inscan`** (shipment scanned in at a facility → it
  connected) and **`attribution_changed`** (debit already re-attributed; also implied when an
  AWB has >1 row — the multi-row case). Present ⇒ auto-reverse (within cap) for the data-decidable
  categories (hardstop / intransit / dual_scan_mismatch); else escalate to the owning team with
  the real record. `loss_percentage=0%`/`leg=meesho` ⇒ "not charged to you"; `debit_revoked` ⇒
  "already reversed". The trust gate + adversarial verifier still run on every reversal.

**Dispositions.** 16 total: the hero `hardstop_loss` + 15 from `reason_l1`
(shipment_shortage, bag_shortage, wrong_rvp_pickup, damage, secondary_qc_fail,
dual_scan_mismatch, seller_dependency, pilot_lost_on_field, intransit, data_platform_issue,
sc_migration_issue, rto_vehicle_placement, not_found, debit_revoked, other_loss). Teams routed
to Losses & Debits / RVP-Returns / Quality-QC / Seller-Ops / Tech-Data-Platform / RTO-Logistics.

**Deltas / pending.** Caps are conservative placeholders pending the real refund SOPs (product
owner to confirm per category). Only the two signals in this dataset drive auto-reversal; other
categories escalate because their SOP-specific evidence (e.g. shortage evidence-mail SLAs) isn't
in this export. Production refresh = the same query via the **Metabase API key** (not a session
token) on a schedule, or a warehouse read-replica. Still needed from ops: the **COD-pendency**
and **payout** queries + a sample export to extend the same pattern to those disputes.

---

## M. This build cycle (v5.0 deltas)

Shipped since §J, all deployed on Render (Docker) with Turso durability:

- **Durable state (Turso).** All authored content (SOPs, brains, the governance framework, user
  accounts) is written through to **Turso** over HTTP, with a local-file fallback and clobber-safe
  reads (`read_confirmed`) so a transport blip never lets a baked seed overwrite authored data.
  This closes the free-tier "JSON lost on redeploy" gap (Render's free tier has no disk).
- **Auth / RBAC / team access.** Signed-token login, pbkdf2-hashed passwords, server-enforced
  roles (author / approver / viewer, shown as **Admin / Editor / Viewer**), an approver-only team
  panel with a strong-password generator + one-click credential handoff, and initial-admin seeding.
- **Governance framework + SOP conformance loop.** An editable Governance Framework (or one
  structured from an uploaded document) plus a conformance check that scores every compiled SOP
  against the framework's mandates — accountable owner, ₹-cap on money moves, a cited partner
  right, idempotency, priority band. Surfaced in the compile stream, stamped on each stored SOP,
  exposed at `/api/sop/conformance`, and gated at approve-time on high-severity violations.
- **Three-level knowledge model.** SOP scenario *name* (title) separated from concern *category*
  (disposition); disposition routing wired end-to-end; the taxonomy stays emergent.
- **In-app voice / conversation mode.** Hands-free browser Web Speech STT/TTS with a reactive
  visualiser; Sarvam-ready swap for production Indic voice.
- **Reliability hardening.** A 32-finding adversarial audit fixed the high/medium issues (an XSS
  escape gap, a durable-store data-loss race, a false money-resolution reply on escalate, a
  token-parse 500, disposition routing that never fired, unlocked concurrent writes, and more).
- **Dead-code sweep.** ~50 verified-dead items removed (details in §J), zero regressions —
  backend compiles + imports, frontend builds, container boots (health 200), 69 SOPs intact.
- **Leadership documentation.** A code-grounded system document (DOCX + PDF) generated for review.

**Model note (supersedes §A #1 / §K):** the active reasoning model is **OpenAI `gpt-5.5` on every
tier** via the gateway; Claude and Gemini remain config-swappable with no pipeline change.
