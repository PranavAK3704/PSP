# Intent detection in Indic / code-mixed text — what exists, and what it means for us

The question was: has this been solved deterministically, and is it on GitHub?

**Short answer: the resources exist, the exact use case does not, and nothing solves it with
rules.** Every serious implementation is a trained model. But that is not the same as "not
deterministic", and the difference is the whole point — see the reframe at the bottom.

---

## The measured answer to "can rules do it?"

The obvious rule-based fix for Devanagari is script conversion: romanise it, then let the
existing lexical matcher work. `indic-transliteration` does this with pure lookup tables — no
model, no training, fully deterministic.

**It fails. 0 of 4.**

| Input | Rule-based romanisation | BM25 result | Truth |
|---|---|---|---|
| मेरा पेमेंट नहीं आया | `mera pememta nahim aya` | `load_planning` ✗ | payment_not_received |
| कैप्टन पैनल काम नहीं कर रहा | `kaiptana painala kama nahim kara raha` | NOVEL ✗ | capacity_panel_issue |
| मेरा हार्डस्टॉप लग गया है | `mera hardastapa laga gaya hai` | NOVEL ✗ | hardstop_loss |
| पैसा नहीं आया | `paisa nahim aya` | `shortage_loss` ✗ | payment_not_received |

The reason is worth understanding, because it generalises. The problem is not script conversion.
It is that **Hindi speakers write English words phonetically in Devanagari** — पेमेंट *is*
"payment" — and a rule-based romaniser produces `pememta`, a spelling no human has ever typed.
Rules can convert characters. They cannot recover what a word was supposed to be.

For comparison, on the same first query the frozen encoder scores **cosine 0.937** against
`payment_not_received`. That is the gap between "rules" and "a model", measured on our data.

---

## The resources that exist

### Directly usable for us

- **[AI4Bharat/IndicXlit](https://github.com/AI4Bharat/IndicXlit)** — transliteration for 21
  Indic languages, both directions, ~11M params, trained on the
  [Aksharantar](https://arxiv.org/pdf/2205.03018) corpus (26M word pairs). This is the *trained*
  version of the experiment above, and it would produce `payment` where the rule-based tool
  produces `pememta`. Small enough to run alongside the encoder. Worth testing as a
  normalisation step in front of BM25 — it could make the cheap lexical tier work on Devanagari.
- **[L3Cube HingBERT / HingCorpus](https://github.com/l3cubepune/code-mixed-nlp)** — BERT models
  pre-trained on **romanised** code-mixed Hindi-English, the register that actually appears on
  WhatsApp. ([paper](https://arxiv.org/pdf/2204.08398)) The most domain-appropriate encoder
  family for our text; a candidate to A/B against BGE-M3.
- **multilingual-e5-large** — on the
  [IndicRAGSuite](https://arxiv.org/pdf/2506.01615) retrieval benchmark, BGE-M3 leads in 8 of 13
  languages but **e5-large wins on Hindi specifically (0.52)**. Since Hindi is our language,
  this is a cheap A/B worth running before the semantic tier is fixed in place.

### Useful, but not as training data

- **[alexa/massive](https://github.com/alexa/massive)** — 1M utterances, 51 languages, 60
  intents, includes Hindi and 6 other Indic languages. **Wrong domain** — it is voice-assistant
  text ("set an alarm"), native script, nothing like a logistics support message. Its value to
  us is as an *encoder benchmark*: use it to pick the encoder, not to train the classifier.
- **[google-research-datasets/Hinglish-TOP](https://github.com/google-research-datasets/Hinglish-TOP-Dataset)**
  — 10K human-annotated romanised code-switched utterances, plus 170K generated. Domain is TOPv2
  (alarms, reminders, navigation), so again not our intents. **The technique is the takeaway:**
  CST5 generates code-switched data from English data. We have 1,814 English-ish partner
  messages and almost no Hinglish ones — that is exactly the gap CST5-style augmentation fills,
  and it would give the semantic tier Hinglish exemplars without anyone labelling more tickets.
- **[COMI-LINGUA](https://arxiv.org/pdf/2503.21670)** — expert-annotated large-scale Hindi-English
  code-mixing dataset, multitask.

### Catalogues

- **[AI4Bharat/indicnlp_catalog](https://github.com/AI4Bharat/indicnlp_catalog)** and
  **[lingo-iitgn/awesome-code-mixing](https://github.com/lingo-iitgn/awesome-code-mixing)** —
  exhaustive indexes. The second lists the intent-specific papers.

### What does not exist

No public repository combines **code-mixed Hinglish + support tickets + logistics**. Searched
and confirmed. The well-funded academic tasks on Hinglish are hate-speech, sarcasm and offence
detection; intent classification on code-mixed *support* text is thin, and the
domain-and-language intersection we need is empty.

That is not a reason to be discouraged. It means the **taxonomy and the labelled exemplars are
the proprietary asset** — nobody else has 1,814 Valmo partner messages mapped to Valmo
dispositions, and no public model will ever have them. The encoder is a commodity we download.

---

## The reframe that matters: deterministic ≠ rule-based

These get conflated, and the distinction decides the architecture.

| | Rule-based | Deterministic |
|---|---|---|
| Means | a human wrote the logic | same input → same output, always |
| Devanagari | **fails**, measured above | — |
| Frozen encoder + pinned index + fixed threshold | not rule-based | **yes — fully deterministic** |
| Reproducible, diffable, explainable, replayable | yes | yes |
| Needs an API, a key, or a network call | no | **no** |

A pinned encoder is a lookup table with 568M entries that somebody else computed. Running it is
inference, not training; the weights never change; the same message produces the same vector and
the same neighbours forever, on any machine, offline.

**So the goal — "solve everything almost deterministically" — is achievable.** Just not with
regexes. What it rules out is a remote model whose weights change under you without notice; it
does not rule out a frozen model you hold on disk.

---

## The implementation detail that would have cost us most of the gain

Cross-lingual similarity is **systematically lower** than same-language similarity, measured on
our own corpus with an index containing zero Hinglish:

| Query → index | Median top-1 cosine | p25 |
|---|---|---|
| English → English | 0.759 | 0.716 |
| **Hinglish/Devanagari → English** | **0.663** | 0.610 |

A single global floor tuned on English (0.75) therefore **silently rejects most cross-lingual
matches** — it looks like "the encoder does not help on Hinglish" when it is really "the encoder
was never allowed to answer".

Measured on 79 real Hinglish/Devanagari messages against an English-only index — exactly the
WhatsApp situation:

| Semantic floor | Precision | Coverage | Correct per 100 |
|---|---|---|---|
| BM25 only — today | 71.6% | 84.8% | 60.8 |
| hybrid, floor 0.75 (English-tuned) | 72.5% | 87.3% | 63.3 |
| **hybrid, floor 0.50 (script-aware)** | **73.4%** | **100%** | **73.4** |

**+12.6 correct labels per 100 Hinglish messages**, and higher precision, purely from letting the
floor depend on the script of the incoming message. The tier must carry **two calibrated floors,
not one.**

## What this changes in the plan

Nothing about the direction, two things about the detail:

1. **Before fixing the semantic tier in place, A/B three encoders** on our own held-out set:
   BGE-M3 (current), multilingual-e5-large (better on Hindi in the published benchmark), and a
   HingBERT variant (trained on the exact register). Half a day, and it decides a component we
   will live with.
2. **Test IndicXlit as a normaliser in front of BM25.** If a trained transliterator turns
   पेमेंट into `payment`, the free lexical tier starts working on Devanagari too, and less
   traffic reaches the encoder at all.
