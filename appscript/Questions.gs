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
 *   missing:any        ask when there is nothing actionable at all — no WHERE
 *   missing:category   ask when no category is trusted — no WHAT
 *   <category key>     ask when the classifier chose that category
 */
function SEED_QUESTIONS() {
  return [
    ['missing:dc_code', 'Which DC or hub is this about? The 3-letter code if you have it.', true],
    ['missing:waybill', 'Is there an AWB or waybill number involved?', true],
    ['missing:mobile', 'Which registered mobile number is this for?', true],
    ['missing:any', 'Which centre, person or shipment is this about?', true],
    ['missing:any', 'Who is affected — which DCs, or which people?', true],
    ['missing:any', 'Anything more on what is actually going wrong?', true],

    // Fires when we placed WHERE but could not place WHAT. Without it, a message that named a
    // DC perfectly and confused the classifier got asked nothing but "anything else?", which
    // is not a question anybody answers.
    ['missing:category', 'What is going wrong exactly — payment, load, an app, something lost?', true],

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
 * How well did we actually identify this message? Returns {band, who, what, why}.
 *
 * ── THE SEAM ────────────────────────────────────────────────────────────────────────────────
 * This is the one function a better model replaces. Everything downstream — whether to ask,
 * what to ask, how the UI labels it — reads `band` and never the internals. Swap the scoring
 * and nothing else moves.
 *
 * ── TWO AXES, NOT ONE ───────────────────────────────────────────────────────────────────────
 * WHO/WHERE and WHAT fail independently. "DC NQS payment nahi aaya" identifies the place
 * perfectly and cannot decide between two categories. "south zone line haul is compromised"
 * names a real problem with nothing to look it up by. Scoring them together would average
 * those into the same middling number and ask both the same questions, which is wrong twice.
 *
 *   band 'clear'   both landed            -> ask nothing, it is actionable as it stands
 *   band 'partial' one landed             -> ask about the half that did not
 *   band 'blank'   neither landed         -> ask the short generic set
 */
function identificationBand(t) {
  var toks = {};
  try { toks = JSON.parse(t.entity_tokens_json || '{}'); } catch (e) { toks = {}; }
  var flags = [];
  try { flags = JSON.parse(t.flags_json || '[]'); } catch (e) { flags = []; }

  // WHO/WHERE: is there anything a human could actually look up?
  var who = flags.indexOf('no_actionable_identifier') < 0;

  // WHAT: did the classifier commit, and commit with room to spare? A category scraped in just
  // over the floor is not a category anybody should ask questions based on.
  var intent = String(t.intent || '');
  var margin = t.intent_margin === '' || t.intent_margin === undefined
    ? null : Number(t.intent_margin);
  var what = !!intent && intent !== 'NOVEL' &&
             (margin === null || margin >= CFG().identify.trustCategoryMargin);

  var band = who && what ? 'clear' : (who || what ? 'partial' : 'blank');
  var why = who
    ? (what ? 'we know where and what'
            : 'we know where, but not what kind of problem it is')
    : (what ? 'we know what kind of problem, but not where or who'
            : 'nothing in it we can look up, and no category we trust');
  return { band: band, who: who, what: what, margin: margin, why: why };
}

/**
 * What to ask about this ticket, most specific first, capped.
 *
 * Capped at five because a DM with nine questions in it does not get answered. If everything
 * is missing, the generic "which centre, person or shipment" earns its place ahead of four
 * narrower ones.
 */
function questionsFor(t, limit) {
  limit = limit || CFG().identify.maxQuestions;
  var id = identificationBand(t);
  if (id.band === 'clear') return [];      // actionable as it stands — do not bother anybody

  var toks = {};
  try { toks = JSON.parse(t.entity_tokens_json || '{}'); } catch (e) { toks = {}; }
  var has = function (k) { return !!(toks[k] && toks[k].length); };
  var intent = String(t.intent || '');

  // Rank by how much the answer would actually narrow things, because the cap decides what
  // gets dropped. Bank order alone put three generic questions ahead of the one targeted
  // question and the cap then threw the targeted one away — the opposite of the intent.
  var RANK = { category: 0, missingCategory: 1, missingAny: 2, missingKind: 3, always: 4 };
  var picked = [];
  questionBank_().forEach(function (q) {
    var a = String(q.applies_to || '').trim();
    var take = false, rank = RANK.always;
    if (a === '*') { take = true; rank = RANK.always; }
    else if (a === 'missing:category') { take = !id.what; rank = RANK.missingCategory; }
    else if (a === 'missing:any') { take = !id.who; rank = RANK.missingAny; }
    else if (a.indexOf('missing:') === 0) {
      take = !id.who && !has(a.slice(8)); rank = RANK.missingKind;
    } else {
      // A category question is only worth asking when the category is TRUSTED. Asking
      // "which AWBs, and was evidence submitted?" off a coin-flip guess is how you burn the
      // goodwill of the one person who bothered to say something.
      take = id.what && a === intent; rank = RANK.category;
    }
    if (take) picked.push({ q: q.question, rank: rank });
  });
  picked.sort(function (x, y) { return x.rank - y.rank; });
  var out = [];
  picked.forEach(function (x) { if (out.indexOf(x.q) < 0) out.push(x.q); });

  // The broad "which centre, person or shipment" subsumes the narrow identifier questions.
  // Asking both reads as a form, and a form is precisely what we are trying not to send.
  var generic = 'Which centre, person or shipment is this about?';
  if (out.indexOf(generic) >= 0) {
    out = out.filter(function (q) {
      return q === generic || (q.indexOf('DC or hub') < 0 && q.indexOf('waybill number') < 0 &&
                               q.indexOf('registered mobile') < 0);
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
  var id = identificationBand(t);
  var qs = questionsFor(t);
  if (!qs.length) return null;
  var info = id.what ? dispositionInfo(t.intent) : null;
  var ref = String(t.idempotency_key || '').slice(0, 8).toUpperCase();

  // What we say we understood has to match what we actually did. Claiming a category we do not
  // trust, and then asking about it, is the fastest way to teach people to ignore this.
  var opener = info
    ? 'Picked this up from the channel and logged it as ' + info.label.toLowerCase() +
      ' (ref ' + ref + ').'
    : 'Picked this up from the channel and logged it (ref ' + ref + ').';

  var lines = [];
  lines.push(opener + ' It is in the queue either way — nothing needed from you to keep it there.');
  lines.push('');
  // Deliberately an invitation. The alternative to a partial answer is almost always no
  // answer, and somebody who felt obliged once will scroll past the next one.
  lines.push('If you have any of this handy it gets to the right desk faster:');
  qs.forEach(function (q) { lines.push('  • ' + q); });
  lines.push('');
  lines.push('No need to chase it up — whatever you already know is plenty.');
  return { ref: ref, questions: qs, band: id.band, why: id.why, text: lines.join('\n') };
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
