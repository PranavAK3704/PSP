# Pending — the open list

Everything not done, why it matters, and what "closed" looks like. Tick the box when it is
actually closed, not when it is started.

Grouped by **who is blocked**, because that is what decides what you can act on today.
Last reviewed: 2026-09-18.

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

### C8 · Merge the five money dispositions
- [ ] Decide

Measured at **+17.9 precision points for zero coverage cost** — the largest single accuracy
lever available, and it is one config line (`CFG().mergeMoneyClasses`). It is off because
collapsing `payment_not_received`, `payment_reconciliation`, `cod_shortfall`, `cod_pendency` and
`consumables_payment` into one changes how the desk routes work, which is a desk decision.

**Recommendation: turn it on.** Reversible by flipping it back.

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
