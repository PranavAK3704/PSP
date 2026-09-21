/**
 * Questions.gs — what is missing from a message, and what to ask for it.
 *
 * ── WHY THIS IS NOT DRIVEN BY THE CATEGORY ──────────────────────────────────────────────────
 * The obvious design is "look up the category, ask its questions". The category is wrong about
 * one time in three, and a wrong category asks the wrong questions — which annoys the one
 * person who was trying to help, at the exact moment you wanted their help.
 *
 * So questions hang mainly off what is STRUCTURALLY missing, which the pipeline knows for
 * certain: no DC code, no waybill, no mobile, nothing actionable at all. Those are facts about
 * the text, not guesses about its meaning, and they survive a misclassification intact.
 * Category-specific questions exist too, and are the seasoning rather than the meal.
 *
 * ── WHY NOTHING PARSES THE ANSWER ───────────────────────────────────────────────────────────
 * The reply comes back as prose. It is tempting to extract it into fields; do not. The value is
 * that the ticket HAS the detail, not that the detail sits in slots. It gets attached verbatim
 * and a Kapture agent reads it the way they read every other ticket. Skipping the extraction
 * removes the only genuinely hard problem in this whole idea.
 *
 * The one exception is free: the existing extractors run over the answer anyway, so a DC code
 * or a waybill typed into a reply is picked up without anybody writing a parser.
 */

var QUESTION_HEADER = ['applies_to', 'question', 'active'];

/**
 * Seeds for the _questions tab. Written into the Sheet by setup() so they can be edited by the
 * people who know what a line-haul escalation actually needs — which is not me.
 *
 * `applies_to` is one of:
 *   *                  ask on everything
 *   missing:<kind>     ask when that identifier is absent (dc_code, waybill, mobile, pilot_id)
 *   missing:any        ask when there is nothing actionable at all
 *   <category key>     ask when the classifier chose that category
 */
function SEED_QUESTIONS() {
  return [
    ['missing:dc_code', 'Which DC or hub is this about? The 3-letter code if you have it.', true],
    ['missing:waybill', 'Is there an AWB or waybill number involved?', true],
    ['missing:mobile', 'Which registered mobile number is this for?', true],
    ['missing:any', 'Which centre, person or shipment is this about?', true],

    ['load_planning', 'Which route or lane, and which trucking partner?', true],
    ['load_planning', 'Which sort centre is it feeding from?', true],
    ['capacity_panel_issue', 'Which DCs are affected, and since when?', true],
    ['hardstop_loss', 'Which AWBs, and was evidence already submitted?', true],
    ['shortage_loss', 'Which bag or AWB, and what was short?', true],
    ['cod_shortfall', 'Which date and which centre does the shortfall cover?', true],
    ['cod_pendency', 'How much is pending and since which date?', true],
    ['payment_not_received', 'Which payout cycle, and what amount were you expecting?', true],
    ['technical_issue', 'Which screen or app, and what exactly happens when you try?', true],
    ['consumables_order', 'What was ordered, when, and for which centre?', true],

    ['*', 'Anything else we should know to get this moving?', true]
  ];
}

/** The tab, as [{applies_to, question}] with inactive rows dropped. */
function questionBank_() {
  if (_cache.questions) return _cache.questions;
  var rows = readTabObjects_(CFG().tabs.questions).filter(function (r) {
    return r.question && String(r.active).toLowerCase() !== 'false';
  });
  _cache.questions = rows.length ? rows
    : SEED_QUESTIONS().map(function (q) { return { applies_to: q[0], question: q[1] }; });
  return _cache.questions;
}

/**
 * What to ask about this ticket, most specific first, capped.
 *
 * Capped at five because a DM with nine questions in it does not get answered. If everything
 * is missing, the generic "which centre, person or shipment" earns its place ahead of four
 * narrower ones.
 */
function questionsFor(t, limit) {
  limit = limit || 5;
  var toks = {};
  try { toks = JSON.parse(t.entity_tokens_json || '{}'); } catch (e) { toks = {}; }
  var has = function (k) { return !!(toks[k] && toks[k].length); };
  var flags = [];
  try { flags = JSON.parse(t.flags_json || '[]'); } catch (e) { flags = []; }
  var nothing = flags.indexOf('no_actionable_identifier') >= 0;
  var intent = String(t.intent || '');

  var out = [];
  questionBank_().forEach(function (q) {
    var a = String(q.applies_to || '').trim();
    var take = false;
    if (a === '*') take = true;
    else if (a === 'missing:any') take = nothing;
    else if (a.indexOf('missing:') === 0) take = !has(a.slice(8));
    else take = (a === intent);
    if (take && out.indexOf(q.question) < 0) out.push(q.question);
  });

  // "Which centre, person or shipment is this about?" makes the narrower identifier questions
  // redundant — asking both reads as a form, and a form is the thing we are trying not to send.
  var generic = 'Which centre, person or shipment is this about?';
  if (out.indexOf(generic) >= 0) {
    out = out.filter(function (q) {
      return q === generic || q.indexOf('DC or hub') < 0 && q.indexOf('waybill number') < 0 &&
             q.indexOf('registered mobile') < 0;
    });
  }
  return out.slice(0, limit);
}

/**
 * The message to send the person who raised it.
 *
 * Written to be answerable in one reply from a phone, by somebody who was not trying to file a
 * ticket. It opens by telling them it is already logged — otherwise the first reaction is "why
 * are you making me do paperwork" — and it closes by saying a partial answer is fine, because
 * the alternative to a partial answer is usually no answer.
 */
function askDraft(t) {
  var qs = questionsFor(t);
  if (!qs.length) return null;
  var info = t.intent && t.intent !== 'NOVEL' ? dispositionInfo(t.intent) : null;
  var ref = String(t.idempotency_key || '').slice(0, 8).toUpperCase();
  var what = info ? info.label.toLowerCase() : 'this';

  var lines = [];
  lines.push('Picked this up from the channel and logged it as ' + what +
             ' (ref ' + ref + ') — nothing needed from you to keep it moving.');
  lines.push('');
  lines.push('To get it to the right desk first time, could you add:');
  qs.forEach(function (q) { lines.push('  • ' + q); });
  lines.push('');
  lines.push('Whatever you have is fine — reply here and it gets attached.');
  return { ref: ref, questions: qs, text: lines.join('\n') };
}

/**
 * Record that we asked, and what came back.
 *
 * Until the Slack app is reinstalled with chat:write and im:write, "asking" is an agent copying
 * askDraft() into Slack themselves and pasting the reply back. That is clumsy, and it works
 * today, and it exercises the whole loop so the only thing left to change when the scope lands
 * is who presses send.
 */
function recordAsk(key) {
  var me = requireAgent_();
  return withLock_(function () {
    var rows = readTabObjects_(CFG().tabs.tickets);
    var row = 0, t = null;
    for (var i = 0; i < rows.length; i++) {
      if (String(rows[i].idempotency_key) === String(key)) { row = i + 2; t = rows[i]; break; }
    }
    if (!t) throw new Error('No such ticket.');
    // Store the QUESTIONS, not the whole drafted message. Keeping the greeting meant the
    // filed email quoted itself back at the agent reading it.
    writeDesk_(row, { asked_at: istStamp(Date.now() / 1000),
                      asked_for: questionsFor(t).join(' | ').slice(0, 1000),
                      updated_by: me.email, updated_at: istStamp(Date.now() / 1000) });
    SpreadsheetApp.flush();
    return { ok: true };
  });
}

function recordAnswer(key, answer) {
  var me = requireAgent_();
  var text = String(answer || '').slice(0, 4000);
  return withLock_(function () {
    var row = ticketRow_(key);
    if (!row) throw new Error('No such ticket.');

    // Run the ORDINARY extractors over the reply. "DCs affected: NQS, IQU and PJ2" yields three
    // real hub codes for free, because a DC code in a reply looks exactly like a DC code in the
    // original message. This is the whole of the promised parsing — no new parser, no NLP.
    //
    // It lands in a desk-owned column rather than the pipeline's entity_tokens_json: the
    // pipeline rebuilds that from the source message and would overwrite anything put there,
    // and the flags on the ticket describe the ORIGINAL message, which is still true.
    var reg = DCREG();
    var found = extractEntities(text, reg.registry, reg.denylist);
    var byKind = {};
    found.forEach(function (e) {
      if (!byKind[e.kind]) byKind[e.kind] = [];
      if (byKind[e.kind].indexOf(e.value) < 0) byKind[e.kind].push(e.value);
    });
    var summary = Object.keys(byKind).sort().map(function (k) {
      return k + ': ' + byKind[k].join(', ');
    }).join(' | ');

    writeDesk_(row, { answered_at: istStamp(Date.now() / 1000), answer: text,
                      answer_identifiers: summary,
                      updated_by: me.email, updated_at: istStamp(Date.now() / 1000) });
    SpreadsheetApp.flush();
    return { ok: true, found: summary };
  });
}
