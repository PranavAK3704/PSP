# Authoring Guide — teaching the Valmo Advocate engine (no code)

For the SOP / stage-0 authors. You teach the engine in **plain language**; a senior approves before
anything goes live. Nothing here needs code.

---

## The one mental model to hold

You author two kinds of things. They do different jobs:

| | **Domain Brain** | **SOP** |
|---|---|---|
| What it is | *How to think about a whole queue* | *One specific rule / policy* |
| Answers | What clues to gather → how they map to one identifier → how to decide (reverse / inform / escalate) → what to ask the captain if something's missing | "In situation X, the rule is Y, up to ₹Z, else escalate to team T" |
| Author it when | You're defining or refining how an entire domain is handled (losses, payments, FE-ID…) | You're adding or updating a single procedure/policy |

**How the bot uses both at runtime:**
1. Captain sends a message → the engine identifies the **domain**.
2. It applies that domain's approved **Brain** — gathers the signals, asks *only* for what it genuinely can't work out, decides partner-first.
3. It pulls the relevant **SOPs** for the specifics of that case.
4. It replies — grounded, partner-first, in the captain's language.

> **Brain = the reasoning. SOP = the rulebook it reasons with.** You need both: a Brain with no SOPs has no specifics; SOPs with no Brain have no one to apply them well.

---

## Authoring a Domain Brain (step by step)

1. **Support Command → Authoring Studio → "Domain Brain".**
2. **Set the domain** this brain governs (losses, payments, fe_id, …). *(The studio will suggest one from your text; just confirm it.)*
3. **Write the walkthrough in plain language** — exactly how *you* work the queue. A good one covers:
   - the **clues** you look for (amount, payment cycle, hub code, a debit-note number, an AWB, a screenshot…),
   - **how those clues resolve** to one identifier ("hub + debit number → the AWB"),
   - **how you decide** — when you *reverse*, when you *inform & educate*, when you *escalate*, and to whom,
   - what you'd **ask the captain** if a key detail is missing.
4. **Compile.** The machine structures your paragraph into signals → derivations → lookups → decision → "ask-if-missing", live.
5. **Read the gap chips.** Amber = "should fix"; **red = blocking**. They mean *the engine won't know what to ask or how to decide* until you fill them. Edit the fields right there.
6. **Queue changes** (saves a draft) → a **senior approves & go live**. The moment it's approved, the engine follows it.

**SOP mode** is the same flow: paste a plain-language SOP → Compile → clear the gaps → queue → a senior approves → it enters the knowledge the bot searches.

---

## The rules of the road

- **You can't ship a half-baked brain.** "Approve & go live" stays disabled until the **red (blocking) gaps** are cleared. That's the safety net, not a bug.
- **Always say what to ask the captain.** The single most common gap is an SOP that knows the rule but never says what detail to request. Fill it — that's what turns "please raise a ticket" into a real resolution.
- **Check the Authored Library before you start.** One Brain per domain. If a Brain/SOP for your topic already exists, **open and edit it** — don't write a second one. *(An automatic duplicate-catcher is coming; until then, eyeball the library.)*
- **Write like you're briefing a new teammate.** Concrete beats clever. "If a facility inscan exists, the loss isn't the partner's fault → reverse" is perfect.
- **Approvals go through a senior.** Authors propose; an approver makes it live. You'll see the status pill flip to **approved · live**.

## How to confirm it worked
- **Authored Library** (bottom of the Studio) shows every Brain + SOP with a **draft / approved** pill.
- **Test it live**: go to the **Captain Panel**, play a captain with that kind of issue, and watch the engine follow your brain — it should ask only for the detail it truly needs and decide the way you wrote it.

---

## A worked example — Losses (already live, use it as your template)
> *"When a captain says a debit is wrong, I look at the amount + payment cycle, or the hub code + debit-note number, to find the AWB. Then I check the loss record — if there's a facility inscan or the attribution changed, I reverse it; if it's genuinely their fault I explain how to avoid it next time; if I can't find the AWB, I escalate to Losses & Debits and ask for the AWB or hub + note number."*

That paragraph compiles into the full Losses Brain. Yours for Payments / FE-ID should read just like it.
