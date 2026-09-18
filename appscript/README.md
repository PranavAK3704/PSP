# Slack → Sheet → tickets, entirely in Apps Script

Everything runs inside one Google Sheet: storage, scheduling, the pipeline, email, and the UI.
No server, no database, nothing to deploy, nothing that spins down.

You paste the code by hand because there is no `clasp` and no OAuth push. That is the only
manual part — after setup it runs on triggers.

---

## What you do, once

### 1. Make the Sheet and open the editor

New Google Sheet → **Extensions → Apps Script**.

### 2. Confirm the runtime is V8

**Project Settings** → tick **"Show `appsscript.json` manifest file in editor"**.
Open `appsscript.json` and replace it with the copy in this folder.

`"runtimeVersion": "V8"` is not optional. Three things in this port — lookbehind assertions,
`\p{L}` character classes, and sticky regexes — do not exist on the legacy Rhino runtime. On
Rhino the files fail to compile, which is the good failure; what would be worse is them
half-working.

### 3. Paste the files

**File → New → Script** for each, named exactly as below, then paste. Order does not matter —
nothing in this project has cross-file top-level state — but this order makes the most sense to
read:

| # | File | What it is |
|---|---|---|
| 1 | `Config.gs` | settings, secrets, sheet plumbing, the four primitives |
| 2 | `Lexicons.gs` | the noise rules and the 262-term evidence lexicon |
| 3 | `SlackParser.gs` | reads Slack, appends to `raw_messages` |
| 4 | `Noise.gs` | the gate and the informational classifier |
| 5 | `Entities.gs` | DC codes in two tiers, plus five identifier patterns |
| 6 | `Evidence.gs` | "is this an issue at all?" |
| 7 | `Group.gs` | messages → one issue, then the register row |
| 8 | `Dedupe.gs` | duplicate linking, and the `SequenceMatcher` port |
| 9 | `Classify.gs` | BM25 disposition matching, or NOVEL |
| 10 | `Emit.gs` | the ticket draft: identity, title, flags, suppression |
| 11 | `Pipeline.gs` | the orchestrator |
| 12 | `Notify.gs` | the acknowledgement email |
| 13 | `WebApp.gs` | the queue's server side and the partner page |
| 14 | `Health.gs` | the three checks |
| 15 | `Setup.gs` | `setup()`, the triggers, daily rotation |
| 16 | `Tests.gs` | `runAllTests()` |

Then **File → New → HTML**, name it `UI` (the editor adds `.html`), and paste `UI.html`.

### 4. Set the four Script Properties

**Project Settings → Script Properties → Add script property.**

| Key | Value |
|---|---|
| `SLACK_BOT_TOKEN` | `xoxb-…` — the read-only bot token |
| `CHANNELS` | `C08T6NLL77H,C09ABCDEF` — comma-separated channel ids |
| `INTAKE_NOTIFY` | `off` to start. `email-dry` to see what would send. `email` when you mean it |
| `INTAKE_SECRET` | any long random string; it signs the partner status-page tokens |

The token goes here and never in a `.gs` file. This folder is committed to git; Script
Properties are not.

A fifth, `WEBAPP_URL`, is optional — set it to the deployment URL after step 7 and the
acknowledgement emails start carrying a tracking link.

### 5. Import the three seed CSVs

**File → Import → Upload**, and for each choose **Insert new sheet**:

| CSV | Becomes the tab | Rows |
|---|---|---|
| `seed/_dc_codes.csv` | `_dc_codes` | 11,723 hub codes |
| `seed/_dc_denylist.csv` | `_dc_denylist` | 140 tokens that look like codes and are not |
| `seed/_exemplars.csv` | `_exemplars` | 1,259 labelled messages |

`setup()` also creates two tabs that stay **visible**, because they are meant to be edited by
hand and hiding the tab you add teammates to is how it gets forgotten:

| Tab | What goes in it |
|---|---|
| `_agents` | `email · name · active · role · filter_json · last_seen_at · updated_at` — one row per support agent. `setup()` seeds you as the first one. Add the other 10–20 by typing rows. Anyone not on this tab is refused by the UI. |
| `_dc_contacts` | `dc_code · dc_name · email · mobile · whatsapp · active · notes` — how to reach a delivery centre. Without a row here, that DC is never notified, and the ticket records exactly that. |

Rename each imported tab to exactly the name in the middle column — Google names them after the
file, which is usually right but check.

These are tabs rather than constants in the code so you can edit them. The exemplars tab in
particular is written to by the UI every time somebody names a NOVEL ticket.

> `seed/_exemplars.csv` contains real partner message text and is gitignored. The other two
> derive from committed config and are in the repo.

### 6. Run `setup()`, then `runAllTests()`

Pick `setup` from the function dropdown and **Run**. Authorise when asked. It creates the
remaining tabs, hides the reference ones, and installs five triggers.

Then run `runAllTests`. **It must print `ALL PASS` before you trust anything.** It is 172
assertions, and the expected values came from running the reference Python implementation, not
from what this code happens to do.

If something fails, the message names the check and prints got-vs-want. The likely causes, in
order: the runtime is not V8; a seed CSV was not imported or the tab is misnamed; a file was
pasted partially.

> **After pasting a version that changed a tab's columns, run `migrateSchema()` too.** `setup()`
> only *creates* tabs — it will not add a column to one that already exists, and writing a new
> header over old rows would silently put every value under the wrong label. `migrateSchema()`
> rebuilds each row by column name, so new columns arrive empty and nothing shifts.

There is a second test that needs node and runs on your machine, not in Apps Script:

```
node appscript/tools/e2e.js
```

It stands up a fake Slack and an in-memory Sheet and runs the real `.gs` files unmodified — ten
messages in, tickets out, an agent acting on them, then a second pipeline run that must not undo
any of it. `runAllTests()` pins the algorithms; this pins the wiring, and every failure it checks
for is one that actually shipped once.

### 7. Deploy the UI

**Deploy → New deployment → Web app.** Execute as **Me**, access **Anyone within Meesho**.
Copy the URL into the `WEBAPP_URL` script property.

---

## Updating later

Edit one file here, paste it over the same file in the editor, then
**Deploy → Manage deployments → ✏️ → Version: New version → Deploy.**

**Your `/exec` URL does not change**, so nothing needs re-configuring — not `WEBAPP_URL`, not
the agents' bookmarks, not the links already in partners' inboxes. Google's own wording: editing
a deployment "updates the application for all users while maintaining the same URL".

Two ways to get this wrong, both quiet:

- **Skipping the new version** leaves the UI serving old code while the triggers run new code.
- **Clicking "New deployment" instead of editing the existing one mints a different `/exec`
  URL** and strands every agent's bookmark on frozen code, with no error on either side. Once
  you have handed the link to 20 people, always edit the existing deployment.

Never give agents the `/dev` URL — it requires edit access to the script project, which also
exposes `SLACK_BOT_TOKEN` and `INTAKE_SECRET` in Script Properties.

### The one property you must never edit

`INTAKE_SECRET` signs the partner status-page tokens, and `public_token` is *derived* from it —
the pipeline recomputes it every time a ticket updates. Change that property and **every status
link already emailed stops working**, silently: the old link matches no row and the page says
"not found".

Nothing can prevent that from inside the script, so it is detected instead. `healthCheck()`
stores a fingerprint of the secret on first run and shouts if it ever changes. If you rotate it
by accident, restoring the old value restores the links.

`WEBAPP_URL` is the only property tied to the deployment URL, and only one thing reads it: the
tracking link in the acknowledgement email. If you ever *do* create a new deployment, that is
the single property to update.

---

## The agent desk

Agents open the `/exec` URL and are identified by their Google sign-in — no passwords, no
accounts to manage. Because the deployment runs as *you* and is restricted to the Meesho domain,
`Session.getActiveUser()` returns the viewer's email, so the tool knows who is acting **without
any agent needing access to the spreadsheet**.

That has one consequence worth knowing: the Sheet's own revision history shows only you for
every change, so the `updated_by` column is the audit trail. It is written server-side and never
accepted from the browser.

**Adding an agent** is a row in `_agents`. **Removing one** is setting `active` to `FALSE`.
Anyone not on the roster gets a readable refusal rather than a silent failure — which also
closes the hole where any Meesho employee with the URL could append gold exemplars and
permanently skew the classifier.

**Keyboard:** `j`/`k` move · `1`/`2`/`3` set state · `a` assign · `m` my tickets · `/` search ·
`r` refresh · `Esc` unfocus.

Assignment is per-ticket from the detail pane, or in bulk — tick several in the list and assign
them together, which is how a lead actually distributes a morning backlog.

---

## Telling the delivery centre

Meesho AMs raise tickets on behalf of DCs who are not Meesho employees. Both now get told, and
they get **different messages** — the AM gets "we have your issue", the DC gets "an issue was
raised for your centre", because they did not raise it and the first wording reads as a mistake.

The DC is found from the `dc_code` the pipeline already extracts, looked up in `_dc_contacts`.
No code or no contact row means no message, and the ticket records which — "how many DCs never
heard from us" is the number that tells you the directory is stale.

| Channel | Status | What it needs |
|---|---|---|
| **Email** | **on** | nothing — ships today |
| WhatsApp | adapter ready, off | a template approved on Meesho's WhatsApp Business account, plus `WA_TOKEN` / `WA_PHONE_ID` / `WA_TEMPLATE`. Best UX for a DC operator. Note Meta begins charging for service and in-window utility messages from **1 Oct 2026** |
| SMS | adapter ready, off | DLT registration under Meesho's own principal entity and a pre-approved template. **The template must be registered Service-Implicit** — Promotional is DND-scrubbed and only delivered 9am–9pm, and DC problems get raised at 2am. Route through the aggregator already in Meesho's DLT chain; a new account cannot send under Meesho's header |

Turn one on in `CFG().dcNotify`. Both adapters throw a readable error if switched on without
their settings, rather than silently sending nothing.

---

## How it runs

```
every 1 min    pollSlack            Slack → raw_messages
every 5 min    runPipeline          raw_messages → issues → tickets
every 10 min   sendAcknowledgements tickets → email
every 30 min   sweepThreadReplies   late replies on older threads
daily 03:00    dailyMaintenance     archive old rows to Drive
```

A message is in the sheet within a minute and is a ticket within five.

Runtime cost is about 12,000 seconds a day against a Workspace allowance of 21,600 — a little
over half. `pollSlack` uses roughly 9,000 UrlFetch calls a day against 100,000.

Every entry point takes the script lock with `tryLock(0)` and exits immediately if a previous
run is still going. Nothing queues.

### The tabs

| Tab | Written by | What it holds |
|---|---|---|
| `raw_messages` | parser, then pipeline | every message, append-only, plus its assignment |
| `issues` | pipeline | one row per issue: tokens, latency, state, duplicate link |
| `tickets` | pipeline, then the UI | the product |
| `channels` | parser | what it is listening to, and any errors |
| `_state` | both | watermarks and counters |
| `_dc_codes`, `_dc_denylist`, `_exemplars` | you, then the UI | reference data |

---

## The three checks

Run `healthCheck()` from the editor, or read the chips at the top of the UI:

```
  ok   INTENT — 1259 exemplars · 23 classified, 4 NOVEL
  ok   POLLING — 2 channel(s) · last 14:31:02
  !!   ACK — DRY RUN, nothing is being sent
```

A warning is counted separately from a failure and never rounds up to OK. An earlier version of
this check printed "ALL THREE OK" while four acknowledgements were failing, which is the
specific thing it now refuses to do.

---

## Capacity — read this before you scale it

A Google Sheet holds **10,000,000 cells across all tabs**. Three tabs grow with traffic:

| Tab | Columns | Rows per 1,000 messages |
|---|---|---|
| `raw_messages` | 25 | 1,000 |
| `issues` | 24 | ~440 |
| `tickets` | 30 | ~260 |

That is about **136 cells per message** once the 180-day grouping window is full, so against an
8,000,000-cell working budget:

| Grouping window | Sustainable volume |
|---|---|
| 180 days (as shipped) | **~59,000 messages/month** |
| 90 days | ~99,000/month |
| 30 days | ~184,000/month |

**This is lower than the 100k–1M/month you mentioned, and lower than my own plan said** — that
estimate counted only `raw_messages` and missed that `issues` and `tickets` draw on the same
budget. The honest position:

- **Up to ~59k/month**: this design works as shipped. `dailyMaintenance()` archives old
  `raw_messages` rows to CSV files in Drive, which is the only move that actually returns
  cells — archiving to another tab spends the same budget.
- **Up to ~180k/month**: shorten `CFG().grouping.windows` to 30 days. You lose the ability to
  link a payment issue to the same person's issue five months earlier, which is a real loss;
  measure before accepting it.
- **Above that**: `raw_messages` needs to leave Sheets (BigQuery is the natural target).
  `SlackParser.gs` is the only file that changes; `issues` and `tickets` can stay here.

`capacityReport()` prints where you actually are.

### Stress tested, not estimated

| what was measured | result |
|---|---|
| batch size | linear, ~0.2 ms/message of compute; 4,000 messages per run (the configured cap) |
| backlog of open issues | sub-linear — 2.1× at 30,000, because `issueTailRows` caps the comparison set |
| Sheets round trips per run | 13, flat — it used to be one per updated row, which at 2,000 updates was 2,012 calls and would have exceeded the 6-minute cap somewhere near 10,000 |

Execution time is no longer the binding constraint; the cell wall above is.

---

## Things that are deliberately true

**It cannot write to Slack.** The token carries eight scopes, all `:history` or `:read`. There
is no send function and no code path that could become one. Acknowledgement is email only.

**The partner status page is built but dormant.** The deployment is Meesho-only, so a partner
following a status link hits a sign-in they cannot pass. Until "Anyone" access is approved, the
acknowledgement email carries the reference, category and state *in the body*, and the link is
only added for `@meesho.com` recipients. When approval lands, change the deployment access
dropdown — no code changes.

**NOVEL is not a category.** It is the classifier refusing to guess, which is why it leads the
queue as **NEEDS A CATEGORY**. Naming one writes a `gold` row into `_exemplars`, and the next
pipeline run — five minutes later — classifies against it. Gold counts for three silver, so one
human label can outvote a partially-matching established class. That is the whole creep loop,
and it needs no deploy and no retraining.

**Devanagari always comes out NOVEL.** BM25 is lexical and its tokeniser is `[a-z0-9]+`, so a
message in Devanagari scores 0.00 against everything. That is measured, not a bug: those
messages route to a human instead of being confidently mislabelled. Widening the token class
would produce matches on shared punctuation and look like it was working. Closing this properly
needs a semantic tier.

**Duplicates are linked, never merged.** Both issues stay in the register with their own
threads. The MX1 case — the same problem posted in two channels 47 seconds apart, then 4
replies on one side and 2 on the other — is why: merging discards one of the forked threads.

**`MERGE_MONEY_CLASSES` is off.** Collapsing the five money dispositions into one measured at
**+17.9 precision points for zero coverage cost**, the largest single lever available. It is off
because it changes how the desk routes work, which is your call, not the code's. Flip
`CFG().mergeMoneyClasses` to `true` and re-run. I would turn it on.

---

## If something looks wrong

| Symptom | Where to look |
|---|---|
| No messages arriving | `channels` tab, `last_error` column. `not_in_channel` means invite the bot |
| Messages but no tickets | `raw_messages` columns R–Y: `gated`, `evidence_decision`, `issue_id` tell you which stage stopped it |
| Everything is NOVEL | the `_exemplars` tab is empty or misnamed — `healthCheck()` says so explicitly |
| Nothing is acknowledged | `INTAKE_NOTIFY` is `off` or `email-dry`; then the `acknowledge_error` column |
| Runs are slow | Executions tab. Over 120 s per run needs attention; the hard cap is 360 s |
| A ticket looks wrong | its `assign_reason` on the message row says exactly why it grouped where it did |

Every stage writes its reasoning next to its output. That is deliberate: a pipeline you cannot
interrogate is one you end up trusting or discarding wholesale, and neither is useful.
