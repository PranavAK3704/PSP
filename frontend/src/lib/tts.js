/* ── The TTS seam ─────────────────────────────────────────────────────────────────────────────

   One interface, so swapping the voice engine is a config change rather than a rewrite of the
   widget. `useVoice.js` no longer knows what is producing the audio.

   ── WHY THE SEAM AND NOT A VENDOR ────────────────────────────────────────────────────────────
   Cost is not the deciding factor at this volume, so it should not drive the decision. Measured
   from the real ledger: 711 replies on record with a mean of 162 characters, which over the 3,827
   answerable tickets is ~620k characters a month, ~1M with follow-ups at the measured 1.6x ratio,
   and ~2.4M if every web-form ticket were served. Every serious vendor's entry tier covers that.

   What DOES decide it is whether the voice is intelligible to a captain in a noisy DC, in Hindi,
   saying `VL0084554575054` and `LZ5` and `hardstop` correctly — and that is a listening trial,
   not a spec sheet. So this file builds the seam and implements the free default. Picking the
   engine is a decision with a human in it.

   ── WHAT A VENDOR ADAPTER INHERITS FOR FREE ──────────────────────────────────────────────────
   The domain lexicon (`SPOKEN` in useVoice.js) is applied to the text BEFORE it reaches an
   adapter, so the AWB grouping, hub-code spelling and rupee expansion are done once and every
   engine gets them. The phrase splitting and the spoken-length budget are also upstream. An
   adapter therefore implements exactly two things: turn these strings into audio, and stop.

   ── NO NETWORK HERE ──────────────────────────────────────────────────────────────────────────
   The vendor entries below are deliberately inert. They describe what each would need and then
   decline, and `select()` falls back to the browser — a page that silently made calls to an
   external speech API would be a data-egress path nobody signed off on. Wiring one up is a
   deliberate act, not a config typo. */

/** Every adapter implements this shape.
 *  speak(chunks, opts) — `chunks` are lexicon-processed, budget-bounded strings, spoken in order.
 *    opts: { lang, rate, onPhraseStart(i), onDone(), onError() }
 *  cancel()            — stop immediately and fire nothing further.
 *  available()         — false means select() will fall back.
 */

const browser = {
  name: "browser",
  available: () => typeof window !== "undefined" && !!window.speechSynthesis,
  /** Best voice for a BCP-47 tag: exact match, then the language half, then whatever the
   *  platform defaults to. A missing Hindi voice is common on desktop Chrome and must degrade
   *  to *something* rather than silence. */
  _voice(lang) {
    const vs = window.speechSynthesis.getVoices?.() || [];
    const p = String(lang || "").toLowerCase();
    return vs.find((v) => (v.lang || "").toLowerCase() === p)
        || vs.find((v) => (v.lang || "").toLowerCase().startsWith(p.slice(0, 2)))
        || null;
  },
  speak(chunks, { lang, rate = 0.92, onPhraseStart, onDone, onError } = {}) {
    const syn = window.speechSynthesis;
    syn.cancel();
    if (!chunks.length) { onDone?.(); return; }
    const voice = this._voice(lang);
    chunks.forEach((text, i) => {
      const u = new SpeechSynthesisUtterance(String(text).slice(0, 400));
      u.lang = lang;
      if (voice) u.voice = voice;
      u.rate = rate;
      u.onstart = () => onPhraseStart?.(i);
      if (i === chunks.length - 1) {
        u.onend = () => onDone?.();
        u.onerror = () => (onError || onDone)?.();
      }
      syn.speak(u);
    });
  },
  cancel() { try { window.speechSynthesis?.cancel(); } catch { /* not supported */ } },
};

/* Declared, not implemented — each line is what the adapter would need, so the next person does
   not have to rediscover it. All three are HTTP: request audio per chunk, queue and play it,
   report phrase boundaries off the audio element's `ended`. */
const unimplemented = (name, needs) => ({
  name,
  needs,
  available: () => false,
  speak(_chunks, { onDone } = {}) { onDone?.(); },
  cancel() {},
});

export const ADAPTERS = {
  browser,
  // Indic-first; the obvious first trial for Hindi + the regional languages.
  sarvam: unimplemented("sarvam", "API key, endpoint, per-language voice ids, audio queueing"),
  // The AI services team already runs this WITH trained pronunciation for our domain terms —
  // which is the one asset worth taking rather than rebuilding.
  elevenlabs: unimplemented("elevenlabs", "their existing key + voice id; reuse their lexicon"),
  google: unimplemented("google", "GCP credentials, voice names per locale, SSML for the lexicon"),
  azure: unimplemented("azure", "Speech resource key + region, SSML, neural voice per locale"),
};

/** The configured adapter, or the browser if it is unset, unknown, or not yet implemented. */
export function select(name) {
  const want = name || (typeof import.meta !== "undefined" && import.meta.env?.VITE_TTS_ENGINE)
               || "browser";
  const a = ADAPTERS[want];
  if (a && a.available()) return a;
  if (a && a !== browser) {
    // Loud, once, and then working audio. A silent widget because someone set an env var to a
    // vendor nobody wired up is a worse failure than the free voice.
    console.warn(`[tts] adapter "${want}" is declared but not implemented `
                 + `(needs: ${a.needs}); falling back to the browser voice.`);
  }
  return browser;
}
