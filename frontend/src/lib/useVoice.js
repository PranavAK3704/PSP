import { useCallback, useEffect, useRef, useState } from "react";
import { select as selectTTS } from "./tts.js";

/* ── Voice, as one hook both surfaces share ────────────────────────────────────────────────────

   The mechanism already existed and was stranded: a working hands-free loop lived inside
   `pages/CaptainPanel.jsx` — the internal test bench — while the captain's own support widget had
   no voice code at all. Same shape as the case strip and the monitoring nudge: built where it
   could be demoed, not where the person it was for would find it.

   Extracted rather than reimplemented, so there is one place where speech behaves and one place
   to swap the engine.

   ── WHY THE AUDIENCE DECIDES THE DESIGN ──────────────────────────────────────────────────────
   From the partner-support working session: 100% of DCs prefer support in Hindi or a regional
   language, 41% of captains never raise a ticket at all, and the Karnataka adoption pilot
   activated 16.3% against the 61% needed — with the finding that "willingness wasn't the
   constraint, access was". People who had already agreed to move off their AM still could not log
   in or navigate.

   So voice is not a convenience feature here. Typing is the barrier, and reading a Hinglish
   paragraph is the second barrier. This hook exists to remove both.

   ── DELIBERATELY HALF-DUPLEX ─────────────────────────────────────────────────────────────────
   The mic is closed while speaking. Full duplex sounds better and feeds the synthesised voice
   straight back into the recogniser, which produces a conversation with itself. Barge-in is
   handled explicitly instead: `stopSpeaking()` cuts the utterance and hands the turn back.

   ── AND WHAT THIS IS NOT ─────────────────────────────────────────────────────────────────────
   Browser speech (Web Speech API). Free, zero dependency, and good enough to prove the
   interaction — but it will mispronounce every AWB, hub code and domain term, because no engine
   knows them without a lexicon. `speechText()` below is where that lexicon lands, and it is the
   one piece worth taking from the AI services team rather than rebuilding: they have already paid
   for the pronunciation work. Swapping in Sarvam/Deepgram + ElevenLabs replaces the two bodies
   below and nothing else. ── */

export const VOICE_LANGS = [
  ["hi-IN", "हिंदी"], ["en-IN", "English"], ["mr-IN", "मराठी"], ["ta-IN", "தமிழ்"],
  ["te-IN", "తెలుగు"], ["kn-IN", "ಕನ್ನಡ"], ["ml-IN", "മലയാളം"], ["bn-IN", "বাংলা"],
];

const LANG_KEY = "valmo.voiceLang";
const READ_KEY = "valmo.readAloud";

/* ── How much of a reply to actually SPEAK ────────────────────────────────────────────────────
   Measured across 711 real replies on record, and the distribution is bimodal: 294 are ~9 chars
   ("theek hai" acknowledgements) and 306 are real answers of 200-320 chars. Median 207, p90 328,
   max 1009. At the ~14 chars/sec this voice runs at, that is a median of 15 seconds, a p90 of 23,
   and a worst case of SEVENTY-TWO SECONDS of synthesised Hindi with no way to skim.

   Nobody listens to 72 seconds to find out whether their debit was reversed. So the spoken path
   is not the written one read out: it speaks the opening phrases and then says the rest is on
   screen. The written reply is untouched and complete — this only bounds the audio.

   Phrase-aligned, not character-truncated: cutting mid-sentence is worse than not speaking, and
   `phrases()` already splits on sentence punctuation including the Devanagari danda. */
const SPOKEN_BUDGET = 240;      // ≈17s — just past the median answer, well inside p90
const SPOKEN_MIN_PARTS = 1;     // always speak at least one whole phrase, even a long one

/* Only Hindi and English are written here on purpose. A wrong sentence in Tamil or Bengali is
   worse than an English one a captain can still place, and translating these eight is exactly
   the localisation work the AI services team already owns. Falls back to English, never to a
   guess. */
const MORE_BELOW = {
  "hi-IN": "Poora jawab neeche likha hai.",
  "en-IN": "The full answer is written below.",
};

/* ── The domain lexicon, first pass ───────────────────────────────────────────────────────────
   Every engine reads `VL0084554575054` as number soup, "hardstop" as two unrelated words, `LZ5`
   as "ell zed five" and `DOH` as a syllable — and those are exactly the tokens our answers are
   made of. Grouped digits with commas force a pause between them, which is the difference between
   a captain recognising their own AWB and hearing fifteen random numbers.

   This is a starting set generated from the terms that actually appear in replies, not a guess at
   what might. It is also where the AI services team's work should replace mine. */
const SPOKEN = [
  // AWBs: letters first, then digits in groups so a pause lands between them — that is the
  // difference between a captain recognising their own AWB and hearing fifteen random numbers.
  // Grouped from the RIGHT so a 13-digit body never leaves a single orphan digit at the end
  // ("…7505, 4" read as a stray "four" and sounded like a mistake).
  [/\b(VLR?)(\d{8,16})\b/g, (_, pre, digits) => {
    const g = [];
    for (let i = digits.length; i > 0; i -= 4) g.unshift(digits.slice(Math.max(0, i - 4), i));
    if (g.length > 1 && g[0].length <= 2) { g[1] = g[0] + g[1]; g.shift(); }
    return `${pre.split("").join(" ")}, ${g.join(", ")}`;
  }],
  // Hub codes: three letters read as letters, not as a word.
  [/\b([A-Z]{2,4}\d?)\b(?=\s*(hub|DC|centre|center)?)/g, (m) =>
    (m.length <= 4 && /^[A-Z]+\d?$/.test(m)) ? m.split("").join(" ") : m],
  // Domain terms an engine has never heard.
  [/\bhardstop\b/gi, "hard stop"],
  [/\bDOH\b/g, "D O H"],
  [/\bRTO\b/g, "R T O"],
  [/\bRVP\b/g, "R V P"],
  [/\bCOD\b/g, "C O D"],
  [/\bQC\b/g, "Q C"],
  [/\bAWB\b/g, "A W B"],
  [/\bCCTV\b/g, "C C T V"],
  [/\bFE\b/g, "F E"],
  [/\bSOP\b/g, "S O P"],
  [/\bCPS\b/g, "C P S"],
  [/\bUTR\b/g, "U T R"],
  // Rupees: say the word, and keep the digits grouped so the pause lands correctly.
  [/₹\s?([\d,]+)/g, (_, n) => `${n} rupees`],
];

/** Strip markup and apply the lexicon. Exported so a harness can score it without a browser. */
export function speechText(t) {
  let s = String(t || "")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/https?:\/\/\S+/g, " link ")
    .replace(/[#*_`>|]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  for (const [re, rep] of SPOKEN) s = s.replace(re, rep);
  return s.replace(/\s+/g, " ").trim();
}

/** Split a reply into speakable phrases, so the UI can highlight what is being said. */
export function phrases(t) {
  return String(t || "")
    .split(/(?<=[.!?।])\s+|\n+/)
    .map((p) => p.trim())
    .filter((p) => p.length > 1);
}

export function useVoice() {
  const [lang, setLangState] = useState(() => {
    try { return localStorage.getItem(LANG_KEY) || "hi-IN"; } catch { return "hi-IN"; }
  });
  // ── DEFAULTS ON, and the audience is the whole argument ─────────────────────────────────
  // This read `=== "1"`, i.e. off unless the captain had already found the toggle. That is the
  // wrong way round for this specific user group. From the partner-support working session:
  // 100% of DCs prefer Hindi or a regional language, 41% of captains never raise a ticket at
  // all, and the Karnataka adoption pilot activated 16.3% against the 61% it needed — with the
  // finding that "willingness wasn't the constraint, access was".
  //
  // If reading a Hinglish paragraph is the barrier, then putting the fix behind a control the
  // captain must first notice, understand and press reproduces that pilot exactly: the help
  // exists, and the people who need it most do not reach it.
  //
  // Absent is therefore ON; only an explicit "0" turns it off, and `setReadAloud` writes that
  // the moment they press the toggle — so a captain who does not want audio says so once.
  const [readAloud, setReadAloudState] = useState(() => {
    try { return localStorage.getItem(READ_KEY) !== "0"; } catch { return true; }
  });
  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [heard, setHeard] = useState("");
  const [spokenIdx, setSpokenIdx] = useState(-1);   // which phrase is being said, for karaoke
  const [denied, setDenied] = useState(false);

  const recRef = useRef(null);
  const levelRef = useRef(0);        // 0..1 live mic amplitude — read per frame by the waveform
  const audioRef = useRef(null);
  const onFinalRef = useRef(null);   // set per start() so the closure always has the live handler

  const supported = typeof window !== "undefined" &&
    !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  // Asked of the adapter, not of `window.speechSynthesis` — a vendor engine is available on
  // platforms where the browser voice is not, and vice versa.
  const canSpeak = typeof window !== "undefined" && selectTTS().available();

  const setLang = useCallback((v) => {
    setLangState(v);
    try { localStorage.setItem(LANG_KEY, v); } catch { /* private mode */ }
  }, []);
  const setReadAloud = useCallback((v) => {
    setReadAloudState(v);
    try { localStorage.setItem(READ_KEY, v ? "1" : "0"); } catch { /* private mode */ }
    if (!v) selectTTS().cancel();
  }, []);

  /* ── the mic meter. Optional: without it the waveform falls back to a idle shimmer, so a
     denied getUserMedia degrades the visual and never the function. ── */
  const startMeter = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const Ctx = window.AudioContext || window.webkitAudioContext;
      const ctx = new Ctx();
      const an = ctx.createAnalyser();
      an.fftSize = 512;
      ctx.createMediaStreamSource(stream).connect(an);
      const buf = new Uint8Array(an.frequencyBinCount);
      let raf;
      const tick = () => {
        an.getByteTimeDomainData(buf);
        let sum = 0;
        for (let i = 0; i < buf.length; i++) { const v = (buf[i] - 128) / 128; sum += v * v; }
        levelRef.current = Math.min(1, Math.sqrt(sum / buf.length) * 3.2);
        raf = requestAnimationFrame(tick);
      };
      tick();
      audioRef.current = { stop: () => {
        cancelAnimationFrame(raf);
        stream.getTracks().forEach((t) => t.stop());
        ctx.close().catch(() => {});
      } };
    } catch { /* meter is decoration; recognition still works */ }
  }, []);

  const stopMeter = useCallback(() => {
    try { audioRef.current?.stop?.(); } catch { /* noop */ }
    audioRef.current = null;
    levelRef.current = 0;
  }, []);

  /** Start listening. `onFinal(text)` fires once, with what was heard. */
  const start = useCallback((onFinal) => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) return false;
    selectTTS().cancel();          // half-duplex: never let our own audio into the mic
    onFinalRef.current = onFinal;
    try { recRef.current?.stop(); } catch { /* noop */ }
    const rec = new SR();
    rec.lang = lang;
    rec.interimResults = true;
    rec.continuous = false;
    let finalText = "";
    rec.onstart = () => { setListening(true); setHeard(""); setDenied(false); };
    rec.onresult = (e) => {
      let interim = "";
      for (const r of e.results) {
        if (r.isFinal) finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      setHeard(finalText || interim);
    };
    rec.onerror = (e) => {
      if (e?.error === "not-allowed" || e?.error === "service-not-allowed") setDenied(true);
    };
    rec.onend = () => {
      setListening(false);
      stopMeter();
      const t = finalText.trim();
      // Keep the transcript visible on an empty result so a captain can see it heard nothing,
      // rather than the panel silently returning to rest as though they had not spoken.
      if (t) { setHeard(""); onFinalRef.current?.(t); }
    };
    recRef.current = rec;
    startMeter();
    try { rec.start(); } catch { /* already running */ }
    return true;
  }, [lang, startMeter, stopMeter]);

  const stop = useCallback(() => {
    try { recRef.current?.stop(); } catch { /* noop */ }
    setListening(false);
    stopMeter();
  }, [stopMeter]);

  /** Speak a reply, phrase by phrase, reporting which phrase is live for the karaoke highlight. */
  const speak = useCallback((text, onDone) => {
    if (!readAloud || !selectTTS().available()) { onDone?.(); return; }
    const parts = phrases(text);
    if (!parts.length) { onDone?.(); return; }
    setSpeaking(true);
    // Take whole phrases until the budget is spent — see SPOKEN_BUDGET.
    const spoken = [];
    let budget = SPOKEN_BUDGET;
    for (const part of parts) {
      if (spoken.length >= SPOKEN_MIN_PARTS && budget - part.length < 0) break;
      spoken.push(part);
      budget -= part.length;
    }
    const truncated = spoken.length < parts.length;
    const utterances = truncated
      ? [...spoken, MORE_BELOW[lang] || MORE_BELOW["en-IN"]]
      : spoken;

    // The LEXICON IS APPLIED HERE, above the seam, so every engine inherits it — AWBs grouped,
    // hub codes spelled, rupees expanded. An adapter receives finished strings and only has to
    // turn them into audio.
    const chunks = utterances.map((part) => speechText(part));
    const pointerIdx = truncated ? chunks.length - 1 : -1;
    selectTTS().speak(chunks, {
      lang,
      // Slower than default. Numbers and amounts are the payload of almost every answer, and
      // default rate runs them together.
      rate: 0.92,
      // The pointer sentence is ours, not the answer's, so it must not light up a phrase in the
      // transcript — the karaoke highlight would land on text that is not being said.
      onPhraseStart: (i) => setSpokenIdx(i === pointerIdx ? -1 : i),
      onDone: () => { setSpeaking(false); setSpokenIdx(-1); onDone?.(); },
    });
  }, [lang, readAloud]);

  const stopSpeaking = useCallback(() => {
    selectTTS().cancel();
    setSpeaking(false);
    setSpokenIdx(-1);
  }, []);

  // Never leave a mic open or an utterance queued behind an unmount.
  useEffect(() => () => {
    try { recRef.current?.stop(); } catch { /* noop */ }
    selectTTS().cancel();
    try { audioRef.current?.stop?.(); } catch { /* noop */ }
  }, []);

  return { lang, setLang, readAloud, setReadAloud, listening, speaking, heard, spokenIdx,
           denied, supported, canSpeak, levelRef, start, stop, speak, stopSpeaking };
}
