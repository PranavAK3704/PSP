# Valmo Partner Support — Revised Product Vision & Where We Are

Captured from the product walkthrough. Goal: **a support system nobody has seen before.**
This is the north star; the BRD is the engineering spine; `PRODUCTION_DELTA.md` is the
build ledger. Status legend: ✅ built · 🟡 partial · ⬜ not yet.

---

## The one-line shape

> A partner raises anything, in natural conversation, on **WhatsApp now / Captain Panel
> later**. A stateful chat collects and structures the need, an L1 brain identifies a
> (possibly brand-new) disposition, figures out what data it needs, asks the partner for
> what's missing, pulls the rest via deterministic DB queries, and then either **answers**,
> **fetches data**, or **acts/escalates** — with a full audit trail, a satisfaction check,
> and CPD when we fall short. Humans (L3 functional teams) own their escalations on an
> accountable tracker; a KT engine turns spoken/typed/uploaded knowledge into structured
> policy; an audit engine watches everything and drives continuous improvement.

---

## The end-to-end flow (revised)

```
CHANNEL           WhatsApp (now)  |  Captain Panel (later)   ← engine is channel-agnostic
   │
CONVERSATION      friendly, stateful, MULTIMODAL (text · voice · files · images · sheets)
   │              collects → structures → stores the need
   │
UNDERSTANDING     identify disposition (may be NEW → emergent taxonomy / CPD)
(L1 brain)        → "what data does THIS disposition need?" (from its SOP)
   │
REQUIRED-DATA     is it all present?
LOOP              ├─ no → ask the partner: "I see this is a <X> issue re <Y>; I still need <Z>"
   │              │        ├─ partner pastes it            → continue
   │              │        ├─ "I don't have it"            → tell them where to find it (a/b/c)
   │              │        └─ partner drops off            → FRICTION signal (analyse)
   │              └─ yes → proceed
   │
RESOLUTION        three kinds of need:
   │              (a) PROCESS Q&A       — "how is a loss marked?"        → answer from POLICY/PROCEDURE
   │              (b) DATA / INFO       — "where is my shipment / truck?" → LLM picks a QUERY,
   │                                       query runs on DB (LLM never touches DB), converse over rows
   │              (c) ACTION / REDRESS  — "reverse my loss" → ask the SOP-defined clarifying Qs
   │                                       (date? amount? AWB? screenshot?), read why it was marked,
   │                                       explain, then per policy: escalate for reversal (or,
   │                                       aggressive policy: "reversal coming" + escalate to L3)
   │
TRUST SPINE       grounded · policy-as-code caps · calibrated confidence · adversarial verify
   │
ESCALATION  →     L3 FUNCTIONAL-TEAM PLATFORM (inbox, not a chatbot)
   │              SLA timer starts on landing + notification · governance (severity/
   │              recoverability/scalability) · each team OWNS its experience/metric ·
   │              breach ladder → Kaizen → GM/CXO
   │
AUDIT / CPD       logs every step (who · disposition · data required vs provided · query used ·
                  understanding · response · follow-ups) → satisfaction prompt → if unsatisfied,
                  ask what was missing → note → CPD. Follow-up agent re-checks stale issues later.

Cross-cutting: KT ENGINE (anyone contributes → structured → approved → stored → future auto-SOP),
               OPS DASHBOARD (all metrics/queries/dumps/active users/economy),
               CAPTAIN-PANEL MONITORING (flags on policies & procedures).
```

---

## Component status — where we are

| # | Component | Status | Notes / what's needed |
|---|---|---|---|
| 1 | **Channel-agnostic engine** | 🟡 | API is already channel-neutral. Need a **WhatsApp Business API** adapter (now) + Captain Panel embed (later). |
| 2 | **Conversation front — friendly, anything** | ✅ basic | Works single-turn. |
| 3 | **Stateful multi-turn conversation** | ⬜ | **The central new build.** Today it's one-shot. Needs a session/conversation manager that loops. |
| 4 | **Multimodal ingest (files/images/audio/sheets)** | ⬜ | Only browser voice-to-text today. Need file/image/audio upload → read → reason (Gemini/Claude are multimodal). |
| 5 | **Collect → structure → store the need** | 🟡 | Intent extraction + Concern Log exist; structured **slot-filling** does not. |
| 6 | **Disposition identification** | ✅ | Lexical + NOVEL detection works. |
| 7 | **Emergent NEW dispositions (not fixed)** | 🟡 | NOVEL→CPD flag exists; auto-cluster + close-the-loop into a real new disposition ⬜. |
| 8 | **Required-data resolution + ask-partner 3-way loop** | ⬜ | Big new piece. Today missing evidence → escalate. Need: derive required data from the SOP, ask for what's missing, handle paste / "don't have it→where to find" / drop-off. |
| 9 | **Friction / drop-off detection** | ⬜ | New. |
| 10 | **(a) Process Q&A** | ✅ | Grounded RAG answer (just built). |
| 11 | **(b) Data/info queries (LLM picks query → run → converse)** | 🟡 | Substrate + MetabaseProvider designed; the **LLM-selects-a-named-query then converse** loop ⬜. |
| 12 | **(c) Action/redress + clarifying Qs + attachments** | 🟡 | Reversal policy + trust spine + escalation ✅; the SOP-driven clarifying-question loop and attachment intake ⬜. |
| 13 | **DB query path (LLM never touches DB)** | 🟡 | `MetabaseProvider` stub; parameterized queries + query-selection ⬜. |
| 14 | **Trust spine (ground/cap/confidence/verify)** | ✅ | Real and working. |
| 15 | **L3 functional-team platform (inbox/tracker)** | ⬜ | Escalation produces a worked case + Concern; the **L3 surface** (inbox, notifications, ownership metrics) ⬜. |
| 16 | **SLA timer + breach ladder (Kaizen→GM/CXO)** | ⬜ | New. Timer starts on landing + notify. |
| 17 | **Governance framework (severity/recoverability/scalability)** | ⬜ | **Placeholder for now** (per you). |
| 18 | **KT engine (ingest→structure→approve→store)** | 🟡 | **SOP Compiler is the seed** (plain text → structured policy). Need: spoken/typed + **uploads (sheets/images)** intake, permission/approval workflow, KT queue from unsolved issues. |
| 19 | **KT → auto-SOP pattern recognition** | ⬜ | Explicitly future. |
| 20 | **Audit engine (full-trace log)** | 🟡 | Concern Log + AuditRecord concept exist; the **audit view** + follow-up-in-context logging ⬜. |
| 21 | **Satisfaction prompt + dissatisfaction→CPD loop** | ⬜ | New. Design together. |
| 22 | **Follow-up agent (re-check stale issues later)** | ⬜ | Explicitly later. |
| 23 | **Ops metrics dashboard (all metrics/dumps/active/economy)** | 🟡 | Stats API + Concern Log/Overview exist; the **full ops dashboard** ⬜. |
| 24 | **Captain-Panel monitoring flags** | 🟡 | Proactive monitor works (demo); the flags-on-policies/procedures surface ⬜. |
| 25 | **Policies vs Procedures — distinct types** | ⬜ | Today knowledge is one corpus. Need to **type** it: **Policies** = rigid partner-support (ours) · **Procedures** = functional-team supply-chain. |

**What is genuinely built and defensible today:** the resolution-engine core, the trust
spine, tiered models, knowledge ingestion (422 chunks, all repos), grounded process-Q&A,
one-shot resolution/escalation, the SOP Compiler, and basic proactive monitoring.

**What the revised vision mostly adds:** the *conversation* (stateful, multimodal,
slot-filling), the *channels* (WhatsApp), and the *human/ops platforms* (L3 tracker,
KT engine, audit/CPD, ops dashboard). The engine we built is the brain those surfaces wrap.

---

## The one structural change that matters most

Today the pipeline is **single-pass**: message → resolve. The vision needs a **stateful
conversation manager** that can *pause and come back to the partner* mid-resolution
(slot-filling, clarifying questions, attachments, "what else do you need?"). Everything
else (WhatsApp, multimodal, the 3 query tiers) hangs off that loop. This is the next
foundational build, and it reshapes `pipeline.py` from a function into a session-driven
state machine.

---

## Two modelling notes to lock in now

1. **Policies ≠ Procedures.** Type the knowledge base:
   - **Policy** — rigid partner-support rules we own (what we will/won't do for a partner).
   - **Procedure** — functional-team / supply-chain process (how the operation works).
   Both feed answers, but they have different owners, review paths, and authority.
2. **Dispositions are emergent, not a fixed list.** The current 2 are demo seeds. The
   real flow: unknown query → NOVEL → CPD logs it → KT/SOP authored → compiled → a *new*
   disposition + policy exists. Closing that loop is what makes the taxonomy self-grow.

---

## Suggested build sequence (proposal)

1. **Stateful conversation manager + session store** — the loop everything needs.
2. **Required-data slot-filling** — derive needed data from the disposition's SOP; the
   3-way ask-partner branch; friction signal.
3. **Multimodal intake** — files/images/audio → read → feed the loop.
4. **WhatsApp channel adapter** — put it where partners already are (thin layer over 1–3).
5. **Data-query path** — LLM selects a named Metabase query → deterministic run → converse.
6. **Policies-vs-Procedures typing** + emergent-disposition close-the-loop.
7. **L3 functional-team platform** (inbox → SLA timer → ownership metrics → breach ladder)
   with a **governance placeholder**.
8. **KT engine** (ingest + uploads → structure → approve → store).
9. **Audit / CPD engine** + satisfaction prompt.
10. **Ops dashboard**, then follow-up agent, then KT→auto-SOP (future).

---

## Are we on the right path?

Yes. The BRD-grade **engine core is right and reusable** — it becomes the L1 brain in this
larger picture. The revision doesn't invalidate anything built; it **wraps the engine in a
conversation and a set of human/ops platforms**. The biggest reframe is going stateful
(single-pass → conversation loop); the biggest net-new builds are the L3 platform, the KT
engine, and the audit/CPD loop. None of it conflicts with what exists.
