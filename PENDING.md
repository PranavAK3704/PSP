# Pending — the open list

Everything not done, why it matters, and what "closed" looks like. Tick the box when it is
actually closed, not when it is started.

Grouped by **who is blocked**, because that is what decides what you can act on today.
Last reviewed: 2026-09-18.

---

## 0. The plan changed on 2026-09-21 — Kapture is the sink

Management wants this live. The support agents are already onboarded to **Kapture**, and an
email to `valmo.partnersupport@meesho.com` becomes a ticket assigned to a real person there.

So this stops being a ticketing system and becomes a **triage layer that feeds one**. The Sheet
stays the system of record — which matters, because Kapture is going to be replaced and when it
is, `Kapture.gs` goes with it and nothing else moves.

### 0a · Point Kapture at a test inbox first
- [ ] Set `KAPTURE_EMAIL` to your own address
- [ ] File one ticket, check the format against what Kapture's parser actually wants
- [ ] Edit `kaptureBody_()` if it needs a different shape — that is the only function to change
- [ ] Then set `KAPTURE_EMAIL` to `valmo.partnersupport@meesho.com`

**Nobody here knows Kapture's parser.** The format is a sensible guess: labelled plain text,
one field per line, section markers. Verify before it matters.

### 0b · Auto-filing stays OFF until you have watched a week
- [ ] Watch what it *would* have filed, then decide

`CFG().kapture.autoFile` is `false`. Every false positive becomes a real ticket a real agent
works and closes, against measured 82% coverage and 65% category precision. You can loosen it
later; you cannot un-spam a desk.

### 0c · Chase the Slack reinstall — it is the only real blocker
- [ ] Find out where `chat:write` + `im:write` approval is

The enrichment loop — see a vague message, ask the raiser for specifics, attach the reply — is
built and works **except that the script cannot send the DM**. Today an agent copies the drafted
message into Slack and pastes the reply back. That is clumsy and it proves the loop; when the
scope lands, the only thing that changes is who presses send.

### 0d · The week-one measurement that decides whether this is worth keeping
- [ ] For every ticket this raises, did the existing Slack workflow already produce one?

The workflow is opt-in and catches motivated people. This catches the rest. **The value of this
system is exactly that delta.** Run both in parallel, do not auto-file, count the overlap. 90%
overlap means keep only the recurrence view; 40% means you found what was falling through.

---

## A. Blocked on you — nobody else can do these

### A1 · Invite the bot to the two real channels
- [ ] `/invite @psintake` in **#valmo-firefighters** (`C09JY7YLB3L`)
- [ ] `/invite @psintake` in **#valmo-lm-ams** (`C08T6NLL77H`) — private, so it must be done from inside
- [ ] Add both ids to the `CHANNELS` script property

**Why it matters:** you are listening to one sandbox channel. Everything else is tested but
receiving no real traffic.
**Measured now:** `intake-test` readable · the other two return `not_in_channel` and
`channel_not_found`.
**Closed when:** `healthCheck()` shows 3 channels and the `channels` tab has no `last_error`.

### A2 · Fill `_dc_contacts`
- [ ] Get a DC contact list from ops — code, centre name, and at least one of email / mobile
- [ ] Paste into the `_dc_contacts` tab

**Why it matters:** this is the whole of requirement 5. The code, the message and the dispatcher
are built and tested; the directory is empty, so **no delivery centre is being told anything**.
You have 11,723 DC codes and zero contacts.
**Closed when:** tickets show a timestamp in `dc_notified_at` rather than
`no contact row for <CODE>`. That error column is deliberately countable — it tells you how
stale the directory is.

### A3 · Turn acknowledgement on
- [ ] Read a few dry-run renders in the `acknowledge_error` column
- [ ] Set `INTAKE_NOTIFY` to `email`

**Why it matters:** `ACK · DRY RUN` in the header means nothing has ever been sent. Until this
flips, no partner has been told anything.
**Be careful:** with A2 done, this also mails **external, non-Meesho people**. A mistake lands in
a DC operator's inbox, not yours.
**Closed when:** the ACK chip is green and `acknowledged_at` is filling.

---

## B. Blocked on someone else at Meesho — one conversation each

### B4 · WhatsApp to delivery centres
- [ ] Find who runs Meesho's WhatsApp Business account
- [ ] Get a **utility template** approved for the DC notice
- [ ] Set `WA_TOKEN`, `WA_PHONE_ID`, `WA_TEMPLATE`, then `CFG().dcNotify.whatsapp = true`

**Why it matters:** the best channel for a DC operator, who will read WhatsApp and may never
open email. The adapter is written and throws a readable error until configured.
**Watch the date:** Meta begins charging for service and in-window utility messages on
**1 October 2026**. Budget assuming every message is charged. Messaging limits ladder
250 → 2,000 → 10,000 per 24h, raised by business verification.

### B5 · SMS to delivery centres
- [ ] Identify the aggregator already in Meesho's DLT chain (you cannot use a new one)
- [ ] Register the template as **Service-Implicit**
- [ ] Set `SMS_GATEWAY_URL`, `SMS_TEMPLATE_ID`, `SMS_ENTITY_ID`, then `CFG().dcNotify.sms = true`

**Why the category matters more than the cost:** register it as Promotional and it is
DND-scrubbed and delivered only 9am–9pm. DC problems get raised at 2am. It would pass testing
and then silently fail a third of the time, for months.
**Also:** message traceability is mandatory — a chain that is undefined or mismatched is
rejected by the operator outright, which is why a fresh MSG91/Gupshup account cannot send under
Meesho's header. Unwhitelisted URLs are a standard scrubbing rejection, so the SMS variant
degrades to reference-only with no link.

### B6 · Partner-facing status page
- [ ] Get "Anyone" web-app access approved for the Apps Script deployment

**Why it matters:** the status page is built and correct but **dormant** — the deployment is
Meesho-only, so a partner following the link hits a sign-in they can never pass. Today the link
is only added to emails going to `@meesho.com` addresses.
**Closed when:** the deployment access dropdown is changed. No code change.
**Do it in this order:** the roster gate (done) had to land first, or widening access would have
let anyone reclassify tickets.

---

## C. Decisions waiting on you — each is one line of config

### C7 · Acknowledgement speed
- [ ] Decide

Today a partner waits **0–16 minutes**, averaging 8. Six of those are load-bearing: the
acknowledgement cannot go out until the pipeline has decided the message is genuinely an issue,
or you would email everyone who typed "thanks". The other ten are an arbitrary trigger interval.

Folding notify into the end of `runPipeline` and moving the pipeline to every 2 minutes takes it
to **0–3 minutes, averaging 1.5**, and stays inside the runtime budget even if every run has
work (~16,600s of 21,600).

### C8 · Merge the five money dispositions — ~~turn it on~~ LEAVE IT OFF
- [x] Decided against, on evidence — see **E15**

I previously recommended turning this on, at +17.9 precision points. Scoring it properly against
held-out data and then checking where those dispositions actually ESCALATE changed the answer:
the money classes go to Cost Ops and the loss classes to the Losses functional team. The merge
buys accuracy by making a distinction the desk depends on impossible to express.

Leave `CFG().mergeMoneyClasses` off. The decision panel recovers most of the same value without
breaking routing.

---

## E. Measured on held-out data, 2026-09-18 — act on these

Scored the shipping classifier against 307 held-out rows it has never seen.

```
coverage    82.4%   answers 253, sends 54 to a human
precision   65.2%   of those it answers
end-to-end  53.7%   correct out of everything
```

### E12 · Five categories the classifier can NEVER produce
- [ ] Decide whether to collect exemplars for them

`loss_status_enquiry`, `dc_fnf_settlement`, `security_deposit`, `debit_dispute`, `cod_hardstop`
exist in the ops SOP taxonomy and have **zero** exemplars in the index. Any message about a
security deposit or an FnF settlement comes out NOVEL forever, however many times it is raised,
because there is nothing for it to match. This is invisible in the accuracy numbers — those rows
are simply not in the corpus.

### E13 · My required-evidence map disagrees with the SOPs
- [ ] Reconcile with ops

`Emit.gs`'s `REQUIRED_BY_DISPOSITION` has 9 entries I wrote. The SOPs are ops-authored and
disagree on two, and cover one I missed:

| disposition | SOP says | mine says |
|---|---|---|
| `load_planning` | AWB | dc_code |
| `payment_reconciliation` | AWB | mobile |
| `qc_failure` | AWB | *(nothing configured)* |

`hardstop_loss` and `shortage_loss` agree (AWB is the waybill under another name). I have not
changed these unilaterally — which identifier a case needs is an ops decision, not a code one.

### E14 · The SOP playbook is unused
- [ ] Decide whether to surface it in the agent UI

All 68 SOPs carry L1 `checks`, a `resolution` with a template and functional team, and 50 name
an `escalation` team. None of it reaches the agent. A ticket classified `hardstop_loss` could
show the three checks to run and who to escalate to; today it shows a category and nothing else.
This is the highest-value unbuilt thing I found.

### E15 · Do NOT merge the money and loss classes
- [x] Settled — measured, and the answer is no

Merging money + hardstop + shortage scores **53.7% → 80.5% end-to-end**, which looks like the
biggest lever available. It is a trap. The SOPs name the escalation teams:

```
money classes -> Cost Ops, Losses & Debits
loss classes  -> Losses functional team, Hub partners
```

Different teams. The merge scores better only because collapsing a distinction makes it
impossible to get wrong — while making every one of those tickets route to the wrong desk. The
metric improves and the operation gets worse.

**The alternative, measured:** when the classifier refuses, the truth is one of the two buttons
the decision panel already shows **48% of the time**. So a refusal is a one-click decision, not a
blank. Automatic 53.7% → 62.2% with one click, and the taxonomy stays intact.

This supersedes item **C8** below, which was based on the narrower 5-class measurement.

---

## D. Known gaps in the build — mine

### D9 · Nobody has tested 20 concurrent agents
- [ ] Open the web app in ~20 tabs, click state changes while `runPipeline` runs from the editor

The races that used to lose work are fixed structurally — the pipeline physically cannot write
the columns the desk owns — and every UI write takes the script lock. But that is reasoning, not
a measurement, and the honest answer to "have we tested it" is still no.

**What to watch for:** "too many scripts running simultaneously" (the ceiling is 30 across all
agents and all five triggers, because everything executes as you), and any edit that appears to
save and then reverts.

### D10 · Devanagari always routes to a human
- [ ] Decide whether this is worth closing

BM25 tokenises on `[a-z0-9]+`, so a Devanagari message yields no tokens and scores 0.00 against
every disposition. Measured, not accidental — it routes to a human instead of being confidently
mislabelled. Closing it needs a semantic tier (a multilingual embedding model scored 0.937 on
the exact message BM25 scores 0.00 on). See CLAUDE.md "Still open" for the prior art so it does
not have to be researched again.

### D11 · Hub names and cities
- [ ] Get a code → name/city mapping from ops

`_dc_codes` holds 11,723 bare codes. A ticket says `NQS`; nobody outside ops knows where that
is. This also makes `_dc_contacts` (A2) far easier to fill and check.

---

## Closed

- [x] **Python intake removed** — 97 files, 31,297 lines. `2eb01d9`
- [x] **Stress tested** — found and fixed a write amplification that would have exceeded the
      6-minute cap at ~10,000 row updates. `b7975c8`
- [x] **Agent identity, roster and assignment** — Google sign-in, `_agents` tab, per-ticket and
      bulk assign. `62927e7`
- [x] **Three work-losing bugs** — silent revert to NOVEL, notes destroyed by the refresh, and a
      reply sweep that lost data when it hit the cap. `62927e7`
- [x] **DC notification** — different message from the raiser's, email live, SMS/WhatsApp behind
      a dispatcher seam. `62927e7`
- [x] **Instant tabs and loading states** — tab switching was a 2s round trip. `413c0ec`
