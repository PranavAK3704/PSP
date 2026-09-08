#!/usr/bin/env node
'use strict';
/**
 * validate.js — the contract gate for Slack export NDJSON.
 *
 *   node validate.js ./export/valmo-lm-ams [--schema ./schema.json]
 *
 * Exits 1 on any ERROR. Warnings do not fail the run but are printed.
 * Run this before any file is handed over. If it fails, the file is not a
 * deliverable.
 *
 * Accepts schema_version "1" (25 keys) and "1.1" (25 + has_media).
 */

const fs = require('fs');
const path = require('path');

const IST_OFFSET_MS = 5.5 * 3600 * 1000;
const TOL = 1e-6;

const V11 = JSON.parse(fs.readFileSync(
  process.argv.includes('--schema')
    ? process.argv[process.argv.indexOf('--schema') + 1]
    : path.join(__dirname, 'schema.json'), 'utf8')).keys;
const V1 = V11.filter((k) => k !== 'has_media');

const dir = process.argv[2];
if (!dir) { console.error('usage: node validate.js <dir> [--schema schema.json]'); process.exit(2); }

const errors = [];
const warnings = [];
const err = (f, l, m) => errors.push(`${f}:${l}  ${m}`);
const warn = (m) => warnings.push(m);

// ---- helpers ----------------------------------------------------------
const MID = /^\d{10}\.\d{6}$/;
const ISO_IST = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+05:30$/;
const PERMALINK = /^https:\/\/[a-z0-9-]+\.slack\.com\/archives\/[A-Z0-9]+\/p\d{16}$/;

function istParts(tsStr) {
  const n = Number(tsStr);
  if (!Number.isFinite(n)) return null;          // malformed key: report, don't throw
  const d = new Date(n * 1000 + IST_OFFSET_MS);
  if (Number.isNaN(d.getTime())) return null;
  const iso = d.toISOString();
  return { date: iso.slice(0, 10), stamp: iso.slice(0, 19) + '+05:30' };
}

// ---- load -------------------------------------------------------------
const files = fs.readdirSync(dir)
  .filter((f) => /\.(ndjson|jsonl)$/.test(f) && !f.endsWith('.tmp'))
  .sort();
if (!files.length) { console.error(`no .ndjson/.jsonl files in ${dir}`); process.exit(2); }

const all = [];          // { rec, file, line }
const byKey = new Map(); // channel_id + '\0' + message_id -> "file:line"

for (const f of files) {
  const dayFromName = (f.match(/(\d{4}-\d{2}-\d{2})/) || [])[1] || null;
  if (!dayFromName) warn(`${f}: filename has no YYYY-MM-DD; day-placement check skipped`);

  const lines = fs.readFileSync(path.join(dir, f), 'utf8').split('\n');
  lines.forEach((raw, i) => {
    const ln = i + 1;
    if (!raw.trim()) {
      if (i !== lines.length - 1) err(f, ln, 'blank line inside file');
      return;
    }
    let rec;
    try { rec = JSON.parse(raw); }
    catch (e) { return err(f, ln, `not valid JSON: ${e.message}`); }
    if (rec === null || typeof rec !== 'object' || Array.isArray(rec)) {
      return err(f, ln, 'record is not a JSON object');
    }
    all.push({ rec, file: f, line: ln, dayFromName });
  });
}

// ---- per-record checks ------------------------------------------------
let joinCount = 0, mentionMarkupCount = 0, mediaCount = 0;

for (const { rec, file: f, line: ln, dayFromName } of all) {
  const sv = rec.schema_version;
  const expect = sv === '1' ? V1 : V11;
  if (sv !== '1' && sv !== '1.1') err(f, ln, `schema_version must be "1" or "1.1", got ${JSON.stringify(sv)}`);

  // 1. exact key set
  const got = Object.keys(rec);
  const missing = expect.filter((k) => !(k in rec));
  const extra = got.filter((k) => !expect.includes(k));
  if (missing.length) err(f, ln, `missing keys: ${missing.join(', ')}`);
  if (extra.length) err(f, ln, `unexpected keys: ${extra.join(', ')} (reconcile the field map)`);

  // 2. message_id — the primary key. Must be a STRING, never float-cast.
  const mid = rec.message_id;
  if (typeof mid !== 'string') {
    err(f, ln, `message_id must be a string, got ${typeof mid} (${JSON.stringify(mid)}) — float cast destroys the key`);
    continue;
  }
  if (!MID.test(mid)) err(f, ln, `message_id "${mid}" does not match ^\\d{10}\\.\\d{6}$`);

  // 3. uniqueness on (channel_id, message_id)
  const key = `${rec.channel_id}\u0000${mid}`;
  if (byKey.has(key)) err(f, ln, `duplicate (channel_id, message_id) — first seen at ${byKey.get(key)}`);
  else byKey.set(key, `${f}:${ln}`);

  // 4. ts_epoch full precision, no truncation
  if (typeof rec.ts_epoch !== 'number') {
    err(f, ln, `ts_epoch must be a number, got ${typeof rec.ts_epoch}`);
  } else {
    const drift = Math.abs(rec.ts_epoch - parseFloat(mid));
    if (drift > TOL) err(f, ln, `ts_epoch drift ${drift.toFixed(9)} vs message_id — Math.floor() truncation?`);
  }

  // 5. ts_iso: ISO 8601 with +05:30, consistent with message_id
  // 6. record sits in the right day file
  // Both derive from message_id, so they are skipped when the key is unparseable.
  const parts = istParts(mid);
  if (!parts) {
    err(f, ln, 'message_id unparseable as a timestamp — ts_iso and day-placement checks skipped');
  } else {
    if (typeof rec.ts_iso !== 'string' || !ISO_IST.test(rec.ts_iso)) {
      err(f, ln, `ts_iso must be ISO 8601 with +05:30 offset (got ${JSON.stringify(rec.ts_iso)}) — UTC "Z" is NOT accepted`);
    } else if (rec.ts_iso !== parts.stamp) {
      err(f, ln, `ts_iso ${rec.ts_iso} disagrees with message_id (expected ${parts.stamp})`);
    }
    if (dayFromName && parts.date !== dayFromName) {
      err(f, ln, `record IST day is ${parts.date} but filed under ${dayFromName}`);
    }
  }

  // 7. thread semantics: parents carry null, replies carry the parent's id
  if (rec.thread_ref !== null && typeof rec.thread_ref !== 'string') {
    err(f, ln, `thread_ref must be null or a message_id string`);
  }
  if (rec.thread_ref === mid) {
    err(f, ln, `thread_ref equals own message_id — parents must carry null, not their own ts`);
  }
  if (typeof rec.is_thread_parent !== 'boolean') err(f, ln, 'is_thread_parent must be boolean');
  if (rec.is_thread_parent === true && rec.thread_ref !== null) {
    err(f, ln, 'is_thread_parent true requires thread_ref null');
  }
  if (typeof rec.reply_count !== 'number') err(f, ln, 'reply_count must be a number');
  if (rec.is_thread_parent === true && rec.reply_count === 0) {
    warn(`${f}:${ln} is_thread_parent true but reply_count 0`);
  }

  // 8. attachments / has_media agreement
  if (!Array.isArray(rec.attachments)) err(f, ln, 'attachments must be an array');
  else {
    if (rec.attachments.length) mediaCount++;
    for (const a of rec.attachments) {
      if (!a || typeof a !== 'object' || !a.id) err(f, ln, 'attachment entry missing id');
    }
  }
  if (sv === '1.1') {
    if (typeof rec.has_media !== 'boolean') err(f, ln, 'has_media must be boolean');
    else if (rec.has_media !== (Array.isArray(rec.attachments) && rec.attachments.length > 0)) {
      err(f, ln, `has_media ${rec.has_media} disagrees with attachments length ${(rec.attachments || []).length}`);
    }
  }

  // 9. shape of the rest
  if (!Array.isArray(rec.reactions)) err(f, ln, 'reactions must be an array');
  if (!Array.isArray(rec.mentions)) err(f, ln, 'mentions must be an array');
  if (!Array.isArray(rec.subteam_mentions)) err(f, ln, 'subteam_mentions must be an array');
  if (typeof rec.channel_mention !== 'boolean') err(f, ln, 'channel_mention must be boolean');
  if (typeof rec.author_is_bot !== 'boolean') err(f, ln, 'author_is_bot must be boolean');
  if (typeof rec.text !== 'string') err(f, ln, 'text must be a string (empty string, never null)');
  if (!(rec.subtype === null || typeof rec.subtype === 'string')) err(f, ln, 'subtype must be null or a string');
  if (rec.subtype === 'channel_join' || rec.subtype === 'channel_leave') joinCount++;

  // 10. permalink
  if (rec.permalink !== null && !PERMALINK.test(String(rec.permalink))) {
    err(f, ln, `permalink malformed: ${JSON.stringify(rec.permalink)}`);
  } else if (typeof rec.permalink === 'string') {
    const tail = rec.permalink.split('/p')[1];
    if (tail !== mid.replace('.', '')) err(f, ln, `permalink ts ${tail} disagrees with message_id ${mid}`);
  }

  // 11. markup preservation signal
  if (/<@[UW][A-Z0-9]+/.test(rec.text) || /<!(channel|here|everyone)>/.test(rec.text)) mentionMarkupCount++;

  // 12. mentions must not contradict the text
  if (Array.isArray(rec.mentions)) {
    const inText = new Set([...String(rec.text).matchAll(/<@([UW][A-Z0-9]+)(?:\|[^>]*)?>/g)].map((m) => m[1]));
    for (const u of rec.mentions) if (!inText.has(u)) err(f, ln, `mentions contains ${u} not present in text`);
    for (const u of inText) if (!rec.mentions.includes(u)) err(f, ln, `text mentions ${u} but mentions[] omits it`);
  }
}

// ---- corpus-level checks ---------------------------------------------
for (const { rec, file: f, line: ln } of all) {
  if (typeof rec.message_id !== 'string' || rec.thread_ref === null || typeof rec.thread_ref !== 'string') continue;
  if (!byKey.has(`${rec.channel_id}\u0000${rec.thread_ref}`)) {
    err(f, ln, `orphan thread_ref ${rec.thread_ref} — parent not present in this corpus`);
  }
}

if (all.length && joinCount === 0) {
  warn('zero channel_join/channel_leave records across the corpus — was the export filtered upstream? "send everything" means joins too');
}
if (all.length && mentionMarkupCount === 0) {
  warn('no <@U…> or <!channel> markup found in any text — was the text prettified? markup must be preserved verbatim');
}

// ---- report -----------------------------------------------------------
const byChannel = {};
for (const { rec } of all) {
  const c = rec.channel_name || rec.channel_id;
  byChannel[c] = byChannel[c] || { n: 0, replies: 0, media: 0, joins: 0, emptyWithFiles: 0 };
  byChannel[c].n++;
  if (rec.thread_ref && !rec.is_thread_parent) byChannel[c].replies++;
  if (Array.isArray(rec.attachments) && rec.attachments.length) byChannel[c].media++;
  if (rec.subtype === 'channel_join' || rec.subtype === 'channel_leave') byChannel[c].joins++;
  if (!String(rec.text || '').trim() && Array.isArray(rec.attachments) && rec.attachments.length) byChannel[c].emptyWithFiles++;
}

console.log(`\nfiles: ${files.length}   records: ${all.length}   media-bearing: ${mediaCount}`);
for (const [c, s] of Object.entries(byChannel)) {
  console.log(`  ${c}: ${s.n} records, ${s.replies} replies, ${s.media} w/files, ${s.joins} joins, ${s.emptyWithFiles} empty-text-with-files`);
}

if (warnings.length) {
  console.log(`\nWARNINGS (${warnings.length}):`);
  for (const w of warnings.slice(0, 20)) console.log(`  ! ${w}`);
  if (warnings.length > 20) console.log(`  ... ${warnings.length - 20} more`);
}

if (errors.length) {
  console.log(`\nERRORS (${errors.length}):`);
  for (const e of errors.slice(0, 40)) console.log(`  x ${e}`);
  if (errors.length > 40) console.log(`  ... ${errors.length - 40} more`);
  console.log('\nFAIL — this is not a deliverable.\n');
  process.exit(1);
}

console.log('\nPASS — conforms to the contract.\n');
