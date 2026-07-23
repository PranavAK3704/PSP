# Valmo Partner Support — Revised Product Vision & Where We Are

Captured from the product walkthrough. Goal: **a support system nobody has seen before.**
This is the north star; the BRD is the engineering spine; `PRODUCTION_DELTA.md` is the
build ledger. Status legend: ✅ built · 🟡 partial · ⬜ not yet.

> **Update — 23 Jul 2026.** Most of the revised vision has since shipped. The biggest
> reframe in this doc — "go stateful" — is **done**: the engine is a stateful, LLM-driven
> agentic conversation, not a single pass. The L3 platform, SLA/breach ladder, KT engine,
> audit/CPD, the satisfaction→CPD loop, policies-vs-procedures typing, and proactive
> monitoring are all built. **Net-new since this was written:** auth/RBAC + team access,
> durable state (Turso), the **governance framework + SOP conformance loop**, and in-app
> voice/conversation mode. The status table below is updated to match; the remaining ⬜/🟡
> items are the true frontier — multimodal *chat* ingest, the live data connection, the
> follow-up agent, and KT→auto-SOP.

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
| 1 | **Channel-agnostic engine** | ✅ | API is channel-neutral. Captain Panel (React) is live; the **WhatsApp** webhook adapter is built (outbound stubbed pending a Meta account). |
| 2 | **Conversation front — friendly, anything** | ✅ | Voice + text, Hinglish. |
| 3 | **Stateful multi-turn conversation** | ✅ | **Built.** Replaced the single-pass pipeline with an LLM-driven bounded agentic tool-use loop + a session store that pauses and resumes mid-resolution. |
| 4 | **Multimodal ingest (files/images/audio/sheets)** | 🟡 | **Document upload** (Excel/Word/PDF/CSV → text → compile) is built for SOP/framework authoring. Image/audio ingest *inside a captain chat* is not yet wired. |
| 5 | **Collect → structure → store the need** | ✅ | The agentic loop gathers + structures; every turn is written to the Concern Log. |
| 6 | **Disposition identification** | ✅ | Emergent taxonomy + disposition routing wired. |
| 7 | **Emergent NEW dispositions (not fixed)** | 🟡 | The system proposes new disposition keys; auto-cluster → close-the-loop into a live new disposition is still partial. |
| 8 | **Required-data resolution + ask-partner loop** | ✅ | The agent asks for exactly what the SOP needs (e.g. the COD form-fill flow: fill form → bot files concern → L3 clears → bot notifies). |
| 9 | **Friction / drop-off detection** | 🟡 | Dissatisfaction is captured (satisfaction→CPD); an explicit drop-off signal is partial. |
| 10 | **(a) Process Q&A** | ✅ | Grounded RAG. |
| 11 | **(b) Data/info queries (LLM picks query → run → converse)** | 🟡 | The LLM selects a **whitelisted named query** that runs deterministically; the live data source behind it is still a stub. |
| 12 | **(c) Action/redress + clarifying Qs + attachments** | ✅ | Reversal policy + trust spine + escalation + attachment intake + the SOP-driven clarifying loop. |
| 13 | **DB query path (LLM never touches DB)** | 🟡 | Named parameterized queries are deterministic; the **live** Meesho-DB/Metabase connection is designed, not built. |
| 14 | **Trust spine (ground/cap/confidence/verify)** | ✅ | Real and working. |
| 15 | **L3 functional-team platform (inbox/tracker)** | ✅ | **Built** — accountable inbox with the fully-worked case, per-team ownership metrics, and a captain-facing "My Cases". |
| 16 | **SLA timer + breach ladder (Kaizen→GM/CXO)** | ✅ | **Built** — per-team SLA + breach ladder (Team POC → Kaizen → GM → CXO). |
| 17 | **Governance framework** | 🟡 | **Framework engine + SOP conformance loop are built** (classify → mandate → conform → approval gate). The seed framework *content* and the L3 severity scorer remain placeholders for Valmo's real model. |
| 18 | **KT engine (ingest→structure→approve→store)** | ✅ | Contribution → LLM-structured → approval queue → store, plus the SOP Compiler and document uploads. |
| 19 | **KT → auto-SOP pattern recognition** | ⬜ | Still future. |
| 20 | **Audit engine (full-trace log)** | ✅ | Concern Log full trace + the Auditing Studio (editable rubric, batch runner, scores). |
| 21 | **Satisfaction prompt + dissatisfaction→CPD loop** | ✅ | 👍/👎 → dissatisfaction logged as a CPD item. |
| 22 | **Follow-up agent (re-check stale issues later)** | ⬜ | Still later. |
| 23 | **Ops metrics dashboard** | 🟡 | Insights + Support-Command dashboards are live; the full custom-metric builder is pending. |
| 24 | **Captain-Panel monitoring flags** | ✅ | Proactive monitor (cheap-first-pass → LLM only on a real risk); the always-on event-stream subscription is still on-demand. |
| 25 | **Policies vs Procedures — distinct types** | ✅ | Typed at ingest and by the KT engine; policies win conflicts. |
| 26 | **Auth, roles & team access** *(net-new)* | ✅ | Login + server-enforced RBAC (Admin/Editor/Viewer) + an approver-only team panel with one-click credential handoff. |
| 27 | **Durable state** *(net-new)* | ✅ | Turso keeps authored content (SOPs, brains, framework, users) across free-tier restarts (no disk). |
| 28 | **Voice / conversation mode** *(net-new)* | ✅ | In-app hands-free voice (Web Speech STT/TTS) with a reactive visualiser; Sarvam-ready swap for production Indic voice. |

**What is genuinely built and defensible today:** the resolution-engine core and trust
spine; the **stateful agentic conversation**; grounded process-Q&A; SOP-driven resolution +
escalation with attachments; the SOP Compiler; the **L3 platform** (SLA + breach ladder +
My Cases); the KT engine; **audit/CPD** with the satisfaction loop; proactive monitoring;
policies-vs-procedures typing; **auth/RBAC + team access**; **durable state**; the
**governance framework + conformance loop**; and **in-app voice mode**.

**What the revised vision still adds (the frontier):** multimodal ingest *inside a captain
chat* (image/audio), the **live data connection** (Meesho DB/Metabase — designed, not built),
the follow-up agent, KT→auto-SOP pattern recognition, real WhatsApp outbound, and real
money-movement (behind finance sign-off). The engine is the brain; these are the last wraps.

---

## The one structural change that mattered most — done

The original single-pass pipeline (message → resolve) has been **replaced by a stateful,
LLM-driven agentic conversation** that pauses and comes back to the partner mid-resolution
(slot-filling, clarifying questions, attachments, "what else do you need?"). Everything else
(WhatsApp, the query tiers, the human/ops platforms) hangs off that loop. `pipeline.py` is
gone; the reactive path is now `engine/conversation.py` + `engine/tools.py` + a session store —
the model reasons, deterministic tools carry the guarantees, and `apply_policy` is the sole
money path.

---

## Two modelling notes — now locked in

1. **Policies ≠ Procedures.** ✅ The knowledge base is typed at ingest and by the KT engine:
   **Policy** = rigid partner-support rules we own; **Procedure** = functional-team /
   supply-chain process. Both feed answers with different owners/authority; policies win conflicts.
2. **Dispositions are emergent, not a fixed list.** ✅ Routing is disposition-driven and the
   system proposes new disposition keys. The last mile — auto-clustering NOVEL signals and
   closing the loop into a fully live new disposition with no human — is still partial (🟡).

---

## Build sequence — status

1. ✅ **Stateful conversation manager + session store** — done.
2. ✅ **Required-data ask-partner loop** (SOP-driven) — done; explicit friction signal 🟡.
3. 🟡 **Multimodal intake** — document uploads done; in-chat image/audio ⬜.
4. 🟡 **WhatsApp channel adapter** — inbound + engine wired; outbound ⬜ (needs a Meta account).
5. 🟡 **Data-query path** — named-query selection done; the **live** data source ⬜.
6. ✅ **Policies-vs-Procedures typing** — done; emergent-disposition close-the-loop 🟡.
7. ✅ **L3 platform** (inbox → SLA → ownership metrics → breach ladder) — done.
8. ✅ **KT engine** (ingest + uploads → structure → approve → store) — done.
9. ✅ **Audit / CPD engine + satisfaction prompt** — done.
10. 🟡 **Ops dashboard** partial; follow-up agent + KT→auto-SOP still future.

**Also shipped (not in the original sequence):** auth/RBAC + team access, durable state
(Turso), the **governance framework + SOP conformance loop**, and in-app voice mode.

---

## Are we on the right path?

Yes — and most of it is now built. The BRD-grade **engine core** became the L1 brain, and the
revision's wraps — a stateful conversation plus the human/ops platforms (L3, KT, audit/CPD) —
are shipped, along with auth/RBAC, durable state, the governance conformance loop, and voice.
What remains is the **frontier that depends on external access**: the live data connection,
real WhatsApp outbound, and real money-movement (finance sign-off), plus in-chat multimodal,
the follow-up agent, and KT→auto-SOP. None of it conflicts with what exists — it extends it.
