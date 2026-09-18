# "You have Claude access — why not just make a skill, or run it in Cowork?"

Written for senior ops readers. The short answer is that both approaches call the same model,
so the difference is not intelligence. It is everything around the model.

A skill is an engine with no ignition, no odometer and no seatbelt. It is a very good engine.

---

## The ten differences that matter

| | A skill or a Cowork session | A service |
|---|---|---|
| **What triggers it** | A person remembers, opens a session, and pastes something | A message arriving |
| **Memory across runs** | None. Every run starts blank | A full issue history in one database |
| **Duplicate prevention** | Cannot tell today's issue from yesterday's, because it never saw yesterday | Structurally impossible: the primary key is `(channel_id, message_id)`, so a message loaded twice is one row, not two |
| **Reproducibility** | The same input can give a different answer | The same input gives the same answer. Stages 3–5 are pure functions and 110 tests hold them there |
| **Two people run it** | Two sets of tickets | One register. The second run finds the rows the first wrote |
| **Identity it runs under** | An employee's own Slack access, and their own judgement about what to read | A scoped app account with `groups:history` and nothing else |
| **Audit trail** | A chat log, if somebody kept it | Every decision logged with the rule that made it. Every assignment carries a rule, a confidence and a reason — including the ones that matched nothing |
| **Operator on leave** | Nothing happens | Unaffected |
| **Message → ticket** | Hours, or days | Seconds |
| **Cost shape** | Re-reads everything, every run | Pays once per new message, and nothing at all on a re-run — the cache is keyed on the prompt, so a second pass over unchanged data makes zero API calls |

The duplicate row is the one to dwell on. The problem this project exists to fix is that *the
same problem gets re-raised under fresh tickets* — one hub raised seven tickets over five months
for a single unpaid field executive and got the same canned reply each time. A tool with no
memory cannot fix a memory problem. It is the wrong shape for the job, however good the model
behind it is.

---

## What Cowork is genuinely better at, and should keep doing

This is not a case against Cowork. It is a case about which phase Cowork belongs in.

**Build phase — Cowork wins, and should own it:**

- **Developing the prompts.** Stage 6's classification and closure prompts want a person
  reading real messages and arguing with the output. That is a conversation, not a cron job.
  Every prompt this service ships should be developed in a session first.
- **Deciding the taxonomy.** The disposition set has 14 buckets and a mandatory catch-all.
  Which buckets are right is a judgement about the business, and the fastest way to get it
  wrong is to have a batch job invent it.

**Run phase — Cowork still wins, permanently:**

- **The weekly flag-queue review.** Everything the pipeline is not confident about lands in a
  queue for a person. Reviewing it in Cowork is exactly right, and the `UNMAPPED` volume is how
  the taxonomy grows.
- **Ad-hoc questions.** "How many payout issues did MH hubs raise last month?" is a question
  nobody should have to build a report for. Once the register exists, Cowork answers it in a
  minute — and it can only answer it *because* the register exists.

So the split is not skill-versus-service. It is: **the service accumulates the record; Cowork
is how humans think about the record.** Neither replaces the other, and the service is what
makes the Cowork questions answerable at all.

---

## Two things that happened while building this, which are the actual argument

### 1. A tool reported that Slack attachments were unavailable, and a real decision was made on it

A tool reported that attachments were not available on its route. That was taken as a fact and
an engineering decision was made on top of it. The correction only came because the same call
was re-run an hour later with a different parameter — and attachments were there.

A service pins the format in code, and reviews that decision once. A session re-derives it every
time, and can re-derive it wrong. The pipeline now has a specific defence against exactly this
class of error: `load()` records **attachment coverage as a number** on every run, because the
contract validator cannot detect an attachment-blind export — `attachments: []` with
`has_media: false` satisfies its agreement check, so a lossy export passes green.

### 2. Four regex errors, across two people, in two days — each with a plausible count

Every one produced a believable number that would have passed review:

- `(?i)` applied to the token class, so **"DC landing time" extracted `LAN`** — 8 fictional
  hits out of 18, which looked like *good recall*.
- A separator set that missed how people actually type, so two real codes (`IQU`, `UB1`) were
  invisible because their authors put the bold marker in a different place.
- A digit-bearing heuristic that extracts `342` from **"342 Tids are coming in Hardstop loss"**
  with total confidence. `342` is word-bounded, exactly three characters and digit-bearing.
  Nothing about its shape is wrong.
- The label regex consuming the separator, so the forward scan found **nothing at all** while
  the backward scan worked perfectly — 9 of 19 cases silently empty.

What caught them was fixtures asserting **exact token sets**, an artefact a session does not
have. `len(found) == 3` would have passed for all four.

And the discipline keeps paying. Building this stage, running the fixtures found two more that
reading the spec had not: `FMH` — which is on the *label* list — was also a registry hub code,
so the extractor pulled the label itself out as a code; and `SOP` did the same out of "pilot
onboarding SOP". Both were found by printing tokens, not by thinking harder.

The registry itself makes the point a third time. The rule "only the registry rejects `342`" is
half right: the registry correctly rejects `342`, and it also **contains `SIR`, `RVP`, `TID`,
`ALL` and `OLD` as genuine hub codes**. Membership alone is not evidence. That is not something
you reason your way to — you find it by running a query against 11,723 real codes and reading
what comes back.
