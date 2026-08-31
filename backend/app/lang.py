"""Shared Hinglish/English function-word vocabulary.

── WHY THIS IS ITS OWN MODULE ────────────────────────────────────────────────────────────────
Two subsystems independently discovered they needed the same list, and only one of them had it.

`engine/algo/followups.py` built a 98-word set because a review reproduced 24 wrong answers where
words like `number`, `process`, `transit` and `theek` were each carrying a follow-up node on their
own. Its RULE 1 exists because of that: a word that identifies nothing must never satisfy a match.

`knowledge/store.py` had a 32-word ENGLISH-ONLY list, and paid for it the moment BM25 arrived.
Measured on the real corpus: retrieval got WORSE, 31/40 to 29/40, because Hinglish function words
(`pe`, `hua`, `tha`, `kiya`, `nahi`) appear in only a handful of chunks — the ones carrying
Hinglish trigger TAGS — so IDF concluded they were highly specific. One chunk with eleven
Hinglish tags ("DC pe loss laga but BTS not closed", "pilot ne BTS close nahi kiya loss") then won
five separate golden queries, including "shortage kis hub pe hua tha", where the correct shortage
chunks sat at ranks 2 and 3.

Set-overlap scoring had HIDDEN that: every matched term was worth 1.0, so filler cost little. IDF
did not create the problem, it amplified it — which is the useful thing to understand about
adding IDF to any corpus whose tags are written in the users' own language.

One list, both readers. A word added here for one subsystem is correct for the other by
construction, because the property being asserted is the same in both: it identifies nothing.

── WHAT DOES NOT BELONG HERE ─────────────────────────────────────────────────────────────────
Domain words, however common. `loss`, `payment`, `shipment` and `hub` appear in 30-40% of the
corpus and are still what a question is ABOUT — IDF is the right instrument for down-weighting
those, not a stopword list. Removing them would make the system unable to answer "loss" queries at
all. The test for membership is grammatical, not statistical: could this word ever be the subject
of a captain's question? If yes, it stays out of this file.
"""
from __future__ import annotations

#: Function words: pronouns, possessives, auxiliaries, light verbs, postpositions, vocatives, and
#: the interrogatives. Hinglish first because that is the half everyone forgets — a captain writes
#: "mera payment nahi aaya", and every word except `payment` is in here.
FUNCTION_WORDS = frozenset({
    # ── English ──
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "can", "dear", "did", "do", "does",
    "for", "from", "has", "have", "he", "i", "in", "is", "it", "me", "my", "not", "of", "on",
    "or", "that", "the", "this", "to", "was", "were", "will", "with", "you", "your",
    # ── Hinglish pronouns and possessives ──
    "aapka", "aapki", "apna", "apni", "hamara", "iska", "isme", "mera", "mere", "meri",
    "tumhara", "uska", "wo", "woh", "ye", "yeh",
    # ── auxiliaries and light verbs. These are the ones that cost the most: they are frequent in
    #    queries and rare in the corpus, so IDF rates them as decisive. ──
    "aaya", "aayi", "gaya", "gaye", "gayi", "ho", "hoga", "hua", "hue", "hui", "hai", "hain",
    "kar", "karna", "karo", "karu", "karun", "kiya", "lag", "laga", "lagi", "liya", "diya",
    "raha", "rahi", "tha", "thi",
    # ── postpositions and connectives ──
    "ab", "aur", "bhi", "ka", "ke", "ki", "ko", "koi", "kuch", "mein", "na", "nahi", "nhi",
    "par", "pe", "se", "toh",
    # ── vocatives. A captain addressing you is not naming a topic. ──
    "bhai", "bhaiya", "boss", "bro", "ji", "madam", "mam", "saab", "sahab", "sir",
    # ── interrogatives. They carry the question's GRAMMAR, never its subject. `followups` keeps
    #    these separately too, because there it needs them to score without matching; for
    #    retrieval there is no such distinction and they are simply noise. ──
    "kya", "kyun", "kyu", "kaise", "kab", "kahan", "kitna", "kitne", "kaun", "konsa",
})
