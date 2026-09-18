/**
 * Emit.gs — an issue becomes a ticket draft. This file produces THE PRODUCT.
 *
 * ── IDENTITY IS DERIVED FROM THE SOURCE, NEVER GENERATED ────────────────────────────────────
 * A ticket's idempotency key is sha256 of (source_system, channel_id, anchor_message_id). Two
 * consequences worth stating: re-running the pipeline over the same messages produces the same
 * keys and therefore creates nothing new, and every message that joined an issue maps to the
 * same key because the key is built from the ANCHOR. Nothing counts rows, nothing keeps a
 * sequence, nothing can drift.
 *
 * ── SUPPRESSED IS STILL WRITTEN ─────────────────────────────────────────────────────────────
 * A suppressed draft is written to the tickets tab with its reason and simply not sent onward.
 * "What did the pipeline decide not to raise, and why" has to be answerable — that question is
 * how you find out the informational rule is over-reaching, and you cannot ask it of rows that
 * were never written.
 */

var ACTIONABLE = ['dc_code', 'kapture_id', 'waybill', 'mobile', 'pilot_id', 'email'];

/** One required identifier per disposition. Absence is a FLAG, not a rejection — a partner who
 *  forgot their waybill still has a real problem, and the flag is what tells the desk to ask
 *  for it instead of guessing. */
function REQUIRED_BY_DISPOSITION() {
  return {
    payment_not_received:   ['mobile'],
    payment_reconciliation: ['mobile'],
    hardstop_loss:          ['waybill'],
    shortage_loss:          ['waybill'],
    cod_shortfall:          ['dc_code'],
    cod_pendency:           ['dc_code'],
    load_planning:          ['dc_code'],
    capacity_panel_issue:   ['dc_code'],
    invoice_request:        ['dc_code']
  };
}

/**
 * sha256(source_system \0 channel_id \0 message_id), 64 lowercase hex.
 *
 * source_system comes from the message's OWN row, defaulting to 'slack' only when it is
 * genuinely absent — a WhatsApp message must never mint a Slack-shaped key, or the same
 * conversation arriving on two surfaces would collide into one ticket.
 */
function idempotencyKey(sourceSystem, channelId, messageId) {
  var NUL = String.fromCharCode(0);
  return sha256Hex(String(sourceSystem) + NUL + String(channelId) + NUL + String(messageId));
}

// Four alternatives: user mention, broadcast, usergroup, then <url|label>. Only the LAST has a
// capture group, so mentions collapse to a space and a link becomes its URL — the label is
// discarded because a title reading "Check the panel please" loses where the panel is.
function _emitMarkupRe_() {
  return /<@[UW][A-Z0-9]+(?:\|[^>]*)?>|<!(?:channel|here|everyone)>|<!subteam\^[A-Z0-9]+(?:\|[^>]*)?>|<([^>|]+)(?:\|[^>]*)?>/g;
}

function plain_(text) {
  return String(text || '')
    .replace(_emitMarkupRe_(), function (m, g1) { return g1 || ' '; })
    .replace(/\s+/g, ' ').trim();
}

/**
 * A one-line title: the first sentence of the de-marked-up text, prefixed with [DC] if known.
 *
 * Lengths are counted in CODE POINTS, so an emoji costs one character rather than two and a
 * title does not get truncated mid-surrogate — which renders as a replacement glyph.
 *
 * The DC prefix is applied AFTER truncation, deliberately: the hub code is the single most
 * useful thing in a queue view and must never be the part that gets cut.
 */
function makeTitle(text, dcCode, limit) {
  limit = limit === undefined ? 90 : limit;
  var body = plain_(text);
  if (!body) body = '(no text — see attachment)';

  // The lookbehind keeps the terminating . ! or ? on the title. \n never fires here because
  // plain_ already collapsed newlines — it is kept so the expression matches the source.
  var first = String(body.split(/(?<=[.!?])\s+|\n/)[0] || '').trim() || body;

  var chars = cp_(first);
  if (chars.length > limit) {
    var head = chars.slice(0, limit).join('');
    var sp = head.lastIndexOf(' ');
    var cut = sp >= 0 ? head.slice(0, sp) : head;       // no space at all: keep the whole head
    first = (cut || head).replace(/[,;:\-]+$/, '') + '…';
  }
  return dcCode ? '[' + dcCode + '] ' + first : first;
}

/** The body an agent reads first. Always carries an empty second line, then identifiers, then
 *  the reply count, then the permalink — so the shape is scannable without reading it. */
function makeDescription(text, permalink, replyCount, tokensByKind) {
  var lines = [plain_(text) || '(no text — the content is in an attachment)', ''];
  var kinds = Object.keys(tokensByKind || {}).sort();
  if (kinds.length) {
    var parts = [];
    kinds.forEach(function (k) {
      var v = tokensByKind[k];
      if (v && v.length) parts.push(k + '=' + v.join(', '));
    });
    lines.push('Identifiers found: ' + parts.join('; '));
  }
  lines.push('Thread replies: ' + (replyCount || 0));
  if (permalink) lines.push('Source: ' + permalink);
  return lines.join('\n');
}

function VALIDATORS() {
  return {
    mobile:     /^[6-9][0-9]{9}$/,
    kapture_id: /^[0-9]{12,13}$/,
    waybill:    /^(?:VL[0-9]{13}|VLR[0-9]{12})$/,
    pilot_id:   /^[0-9]{8}$/,
    email:      /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/
  };
}

/**
 * Every identifier with its validation verdict.
 *
 * kapture_id carries `exists: null`, NOT false. There is no Kapture API reachable from here, so
 * "not found" is not a fact we have — and a false there would be a confident wrong answer that
 * makes a real ticket id look fabricated. null is the honest one. It must serialise as JSON
 * null, which is why it is null and not undefined: JSON.stringify silently DROPS undefined
 * properties, and the field would vanish from the payload rather than reading as unknown.
 */
function typedEntities(tokensByKind, registry, denylist) {
  var out = {}, V = VALIDATORS();
  Object.keys(tokensByKind || {}).sort().forEach(function (kind) {
    var recs = [];
    (tokensByKind[kind] || []).forEach(function (v) {
      var r = { value: v };
      if (kind === 'dc_code') {
        r.in_registry = registry.has(v);
        r.denylisted = denylist.has(v);
        r.valid = String(v).length === 3 && /^[0-9A-Za-z]+$/.test(v) && v === String(v).toUpperCase();
      } else {
        r.valid = V[kind] ? V[kind].test(String(v)) : true;
      }
      if (kind === 'kapture_id') r.exists = null;
      recs.push(r);
    });
    out[kind] = recs;
  });
  return out;
}

/**
 * Every check that failed, as stable strings. Eight shapes, and the order is deterministic so
 * two runs of the same issue diff cleanly rather than reshuffling.
 */
function runChecks(draftFields, entities, knownDispositions) {
  var flags = [];

  Object.keys(entities || {}).sort().forEach(function (kind) {
    (entities[kind] || []).forEach(function (r) {
      if (r.valid === false) flags.push('malformed_' + kind + ':' + r.value);
      if (kind === 'dc_code') {
        // if/elif: a denylisted code never ALSO gets the registry flag. Two flags for one
        // problem reads as two problems.
        if (r.denylisted) flags.push('dc_code_denylisted:' + r.value);
        else if (!r.in_registry) flags.push('dc_code_not_in_registry:' + r.value);
      }
    });
  });

  var disp = draftFields.disposition;
  if (disp && disp !== 'NOVEL') {
    if (knownDispositions && knownDispositions.size && !knownDispositions.has(disp)) {
      flags.push('disposition_unknown:' + disp);
    }
    var need = REQUIRED_BY_DISPOSITION()[disp] || [];
    need.forEach(function (k) {
      if (!entities[k] || !entities[k].length) {
        flags.push('missing_required_' + k + '_for_' + disp);
      }
    });
  } else if (disp === 'NOVEL') {
    flags.push('disposition_novel_needs_human');
  }

  var anyActionable = ACTIONABLE.some(function (k) { return entities[k] && entities[k].length; });
  if (!anyActionable) flags.push('no_actionable_identifier');

  // 0 replies is falsy and must NOT raise this — with no replies there is nothing to have
  // responded to, and flagging it would bury the real cases.
  if ((draftFields.first_response_latency_s === null ||
       draftFields.first_response_latency_s === undefined ||
       draftFields.first_response_latency_s === '') && draftFields.reply_count) {
    flags.push('replies_exist_but_no_qualifying_first_response');
  }
  return flags;
}

/**
 * Should this draft be withheld from the desk, and why?
 *
 * First match wins, so the precedence is informational > duplicate > no-identifier. An
 * informational duplicate is reported as informational, which is the more useful of the two
 * facts.
 *
 * The second reason says "counted_into", NOT "duplicate". The earlier wording read as thrown
 * away, which is exactly what the recurrence signal cannot afford — the whole point is that the
 * other ticket's occurrence_count includes this one.
 */
function suppressionFor(issue, tokensByKind, requireIdentifier) {
  if (issue.informational) {
    return ['informational — a weather or operational callout, deliberate comms rather ' +
            'than an issue. Raising it produces a register nobody trusts.'];
  }
  if (issue.duplicate_of) {
    return ['counted_into ' + issue.duplicate_of + ' — the same issue raised again. That ' +
            "ticket's occurrence_count includes this one; nothing is discarded."];
  }
  if (requireIdentifier) {
    var any = ACTIONABLE.some(function (k) {
      return tokensByKind[k] && tokensByKind[k].length;
    });
    if (!any) {
      return ['no actionable identifier — no DC code, mobile, waybill or ticket id, so ' +
              'nobody who was not in the conversation can act on it.'];
    }
  }
  return [null];
}

/**
 * Build the ticket draft for one issue.
 *
 * `issue` carries the register row; `raisings` is every top-level message assigned to it, plus
 * every top-level message of any issue linked to it as a duplicate, already sorted by `at`.
 *
 * occurrence_count counts TOP-LEVEL raisings, never thread replies. A 40-reply thread is one
 * occurrence; the same problem raised again five months later is two. That distinction is the
 * recurrence signal, and conflating it with reply volume destroys it.
 */
function buildDraft(issue, tokensByKind, raisings, registry, denylist, knownDispositions) {
  var typed = typedEntities(tokensByKind, registry, denylist);
  var sup = suppressionFor(issue, tokensByKind, CFG().requireIdentifier);
  var occ = raisings || [];

  var flags = runChecks({
    disposition: issue.intent,
    reply_count: issue.reply_count,
    first_response_latency_s: issue.first_response_latency_s
  }, typed, knownDispositions);

  return {
    idempotency_key: idempotencyKey(issue.source_system || 'slack',
                                    issue.anchor_channel_id, issue.anchor_message_id),
    source_system: issue.source_system || 'slack',
    source_id: issue.anchor_channel_id + '/' + issue.anchor_message_id,
    source_permalink: issue.permalink || null,
    title: makeTitle(issue.anchor_text, issue.dc_code),
    description: makeDescription(issue.anchor_text, issue.permalink,
                                 issue.reply_count || 0, tokensByKind),
    raiser: issue.raiser_name || issue.raiser_id,
    dc_code: issue.dc_code || null,
    intent: issue.intent || null,
    entity_tokens: typed,
    flags: flags,
    kapture_ticket_ids: tokensByKind.kapture_id || [],
    reply_count: issue.reply_count || 0,
    first_response_latency_s: (issue.first_response_latency_s === '' ? null
                                : issue.first_response_latency_s),
    state: issue.state || 'NEW',
    duplicate_of: issue.duplicate_of || null,
    suppressed: sup[0] !== null,
    suppressed_reason: sup[0],
    occurrence_count: Math.max(1, occ.length),
    occurrences: occ,
    first_raised_at: occ.length ? occ[0].at : null,
    last_raised_at: occ.length ? occ[occ.length - 1].at : null
  };
}
