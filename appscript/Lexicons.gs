/**
 * Lexicons.gs — config/noise.yaml, config/evidence.yaml and the _NOT_AN_ID stoplist, inlined.
 *
 * ── GENERATED, NOT TYPED ────────────────────────────────────────────────────────────────────
 * Apps Script has no YAML parser, so these lists had to move into code. They were emitted
 * mechanically from the three source files rather than transcribed by hand — 262 evidence
 * terms and 89 ack words is exactly the volume where a hand transcription loses one entry and
 * nobody ever notices, because the symptom is one message a week scoring slightly wrong.
 *
 * ── THE yes/no TRAP IS GONE, AND THAT IS WHY EVERY ENTRY IS QUOTED ──────────────────────────
 * YAML 1.1 parses bare `yes`, `no`, `on`, `off`, `y`, `n` as BOOLEANS, so an unquoted `yes` in
 * an ack list reached the Python gate as True and crashed on .lower(). Here every entry is a
 * quoted JS string, so the trap cannot reappear — but keep quoting them when you add more.
 *
 * ── ORDER IS LOAD-BEARING IN TWO PLACES ─────────────────────────────────────────────────────
 *   · noise rules are evaluated top-down, first match wins. mention_only sits BEFORE emoji_only
 *     because both normalise to "" and a bare <@U…> ping was being reported as emoji_only —
 *     a wrong answer to "what did the filter throw away".
 *   · Devanagari terms are APPENDED to their Latin lists, never prepended. Regex alternation is
 *     leftmost-first, so changing the order changes which surface form _hits() reports.
 */

function LEX() {
  if (_cache.lex) return _cache.lex;

  var noise = {
    subtypes: ['channel_join', 'channel_leave'],

    // Evaluated top-down, FIRST MATCH WINS. `exact` matches the WHOLE normalised string —
    // it deliberately does not match "thanks, but the DC is still down".
    rules: [
      { name: 'bare_ack', exact: [
        'ok', 'okk', 'okay', 'k', 'kk', 'done', 'noted', 'ack', 'acknowledged', 'sure', 'yes',
        'yeah', 'yep', 'no', 'nope'
      ] },
      { name: 'thanks', exact: [
        'thanks', 'thank you', 'thankyou', 'thx', 'ty', 'tysm', 'thanks a lot', 'many thanks',
        'thanku'
      ] },
      { name: 'combined_ack', exact: [
        'noted thanks', 'thanks noted', 'ok thanks', 'okay thanks', 'thanks ok', 'ok done',
        'done thanks', 'noted sir', 'ok sir', 'okay sir', 'thanks sir', 'thank you sir',
        'noted bhai', 'ok bhai', 'thanks bhai', 'noted team', 'ok team', 'thanks team',
        'sure thanks', 'ok noted', 'noted ok', 'will do', 'ok will do', 'done sir'
      ] },
      { name: 'plus_one', exact: [
        '+1', '1', 'same', 'same here', 'same issue', 'me too', 'following', 'watching'
      ] },
      { name: 'fyi_only', exact: [
        'fyi', 'fyi.', 'pfa', 'pfb', 'noted fyi', 'for your information'
      ] },
      { name: 'greeting', exact: [
        'hi', 'hii', 'hello', 'hey', 'gm', 'good morning', 'good afternoon', 'good evening',
        'namaste'
      ] },
      { name: 'mention_only', mentionOnly: true },
      { name: 'emoji_only', emojiOnly: true },
      // Generalises the exact lists above: fires when EVERY word is an ack word AND
      // the message is short. `exact` caught 8 of 33 real acks, because real ones are
      // Hinglish and carry an emoji. The word cap is what stops it over-reaching.
      { name: 'ack_phrase', maxWords: 7, allWordsIn: [
        'ok', 'okk', 'okay', 'oky', 'k', 'kk', 'done', 'noted', 'note', 'ack', 'sure', 'true',
        'yeah', 'yep', 'ya', 'haan', 'ji', 'han', 'thanks', 'thank', 'you', 'thankyou', 'thx',
        'ty', 'tysm', 'thanku', 'shukriya', 'dhanyawad', 'sir', 'madam', 'maam', 'bhai',
        'boss', 'team', 'all', 'guys', 'ho', 'gaya', 'gyi', 'hua', 'clear', 'now', 'great',
        'good', 'gm', 'ga', 'ge', 'morning', 'afternoon', 'evening', 'night', 'hi', 'hii',
        'hello', 'hey', 'namaste', 'welcome', 'cool', 'nice', 'perfect', 'super', 'fine',
        'alright', 'right', 'got', 'it', 'will', 'do', 'ok_hand', 'plus', 'one', 'same',
        'here', 'too', 'also', 'understood', 'received', 'recd', 'confirm', 'confirmed',
        'checking', 'check', 'please', 'pls', 'kindly', 'karo', 'kar', 'diya', 'dijiye'
      ] },
    ],

    // Deliberate operational comms — 20-39% of top-level messages, and about half
    // of all attached media. Flagged, never gated.
    weather: [
      'rain', 'rains', 'rainy', 'rainfall', 'raining', 'barish', 'baarish', 'barsat',
      'weather', 'flood', 'flooding', 'waterlogging', 'waterlogged', 'water level',
      'jal bharav', 'paani bhar', 'monsoon', 'storm', 'cyclone', 'heavy shower', 'showers',
      'thunderstorm', 'imd alert', 'orange alert', 'red alert'
    ],
    // The same axis as weather, found by measuring: 18 of 28 misses were planned
    // downtime, holidays, SOP updates, diversions. Deliberately conservative and phrase-based —
    // a real issue wrongly marked informational is never seen by anyone.
    announce: [
      'planned downtime', 'scheduled maintenance', 'maintenance window', 'public holiday',
      'bank holiday', 'holiday hai', 'band rahega', 'bandh rahega', 'road blocked',
      'full diversion', 'route diversion', 'advisory', 'notice for all', 'please note that',
      'dhyan de', 'dhyan den', 'sabhi ko inform', 'for your information only', 'fyi only',
      'no action required', 'updated sop', 'process follow karna', 'daily mis', 'mis report'
    ],
    // An ask beats the weather. A weather callout does not ask for anything; that is
    // what makes it a callout. NOTE this list is SHORTER than evidence.request — do not share one.
    asks: [
      'please', 'pls', 'kindly', 'request', 'requesting', 'need', 'chahiye', 'karo', 'kar do',
      'dijiye', 'karwa do', 'can we', 'can you', 'could you', 'do the needful', 'arrange',
      'reverse', 'resolve', 'release', 'approve', 'help', 'support', 'sort out', 'look into',
      'revert'
    ],
    // An operational topic that is NOT weather. Decides the strict bound.
    competing: [
      'festival', 'manpower', 'absenteeism', 'strike', 'holiday', 'payout', 'payment',
      'invoice', 'login', 'panel', 'vehicle', 'held', 'hardstop', 'shortage', 'loss', 'rto',
      'onboarding', 'pilot', 'otp', 'app', 'tech', 'closure', 'line haul', 'linehaul',
      'load not received', 'dc closure', 'negative payout'
    ],
  };

  // ── evidence.yaml ─────────────────────────────────────────────────────────────────────
  // Devanagari lists stay separate here and are merged below, exactly as load_config does,
  // so the alternation order is visible rather than implied.
  var ev = {
    opsNouns: [
      'payout', 'payment', 'paisa', 'salary', 'vetan', 'invoice', 'bill', 'gst', 'credit note',
      'debit', 'deduction', 'load', 'shipment', 'parcel', 'order', 'bag', 'consignment',
      'waybill', 'awb', 'manifest', 'vehicle', 'gaadi', 'truck', 'trip', 'linehaul',
      'line haul', 'route', 'dispatch', 'pickup', 'delivery', 'hub', 'dc', 'lmdc', 'fmh',
      'warehouse', 'godown', 'centre', 'center', 'facility', 'gate', 'dock', 'panel', 'app',
      'portal', 'dashboard', 'login', 'otp', 'password', 'account', 'id', 'pilot', 'fe',
      'rider', 'driver', 'captain', 'partner', 'executive', 'manpower', 'scan', 'inscan',
      'outscan', 'sorting', 'closure', 'pendency', 'rto', 'rvp', 'cod', 'qc', 'hardstop',
      'shortage', 'loss', 'reversal', 'revocation', 'claim', 'ticket', 'escalation', 'sla',
      'tat', 'onboarding', 'activation', 'deactivation', 'kyc', 'document', 'bank', 'upi',
      'ifsc'
    ],
    problemMarkers: [
      'not working', 'nahi', 'nhi', 'not received', 'not reflecting', 'not generated',
      'not updated', 'missing', 'pending', 'delayed', 'late', 'stuck', 'held', 'blocked',
      'failed', 'failing', 'error', 'issue', 'problem', 'wrong', 'incorrect', 'mismatch',
      'down', 'unable', 'cannot', 'can not', 'can\'t', 'didn\'t', 'doesnt', 'doesn\'t', 'band',
      'ruka', 'atka', 'galat', 'kam', 'nikal', 'gadbad', 'dikkat', 'pareshan', 'samasya',
      'breach', 'short', 'shortfall', 'deducted', 'rejected', 'denied', 'expired', 'crash',
      'hang', 'slow'
    ],
    requestMarkers: [
      'please', 'pls', 'kindly', 'request', 'requesting', 'need', 'chahiye', 'karo', 'kar do',
      'dijiye', 'karwa do', 'can we', 'can you', 'could you', 'would you', 'kya aap',
      'release', 'released', 'do the needful', 'arrange', 'check', 'verify', 'update',
      'resolve', 'release', 'approve', 'share', 'provide', 'help', 'support', 'kab', 'when',
      'why', 'kyun', 'kaise', 'how', 'kitna', 'status', 'revert', 'reply'
    ],
    // '@channel'/'@here' start non-alphanumeric, so they compile as LITERAL patterns
    // with no word boundaries — see Evidence.gs compileTerms_().
    urgencyMarkers: [
      'urgent', 'asap', 'immediately', 'escalate', 'escalation', 'critical', 'priority',
      '@channel', '@here', 'turant', 'jaldi', 'abhi', 'emergency', 'stuck since',
      'since morning', '3rd time', 'again'
    ],
    // A bare '?' is deliberately absent. It was here once, and as a substring it made
    // every question an orphan — "can we go to play arena?" included. A lone question
    // mark is a follow-up only when it is the WHOLE message: a punctuation test in code.
    followupMarkers: [
      'any update', 'anyupdate', 'update please', 'kya hua', 'kuch hua', 'koi update',
      'gentle reminder', 'reminder', 'following up', 'follow up', 'awaiting', 'waiting',
      'still waiting', 'kab tak', 'kab hoga', 'pending hai', 'need update', 'status please',
      'revert please', '3rd time', 'second time', 'dusri baar', 'teesri baar'
    ],
    devOpsNouns: [
      'पेमेंट', 'भुगतान', 'पैसा', 'पैसे', 'ऑर्डर', 'शिपमेंट', 'डिलीवरी', 'पार्सल', 'हब',
      'गाड़ी', 'वाहन', 'अकाउंट', 'खाता', 'पैनल', 'ऐप', 'लॉगिन', 'आईडी', 'कोड', 'टिकट',
      'रिटर्न', 'नुकसान', 'कटौती'
    ],
    devProblemMarkers: [
      'नहीं', 'नही', 'गलत', 'खराब', 'बंद', 'रुका', 'अटका', 'फेल', 'दिक्कत', 'समस्या',
      'परेशानी', 'देरी'
    ],
    devRequestMarkers: [
      'कृपया', 'प्लीज', 'चाहिए', 'करो', 'करें', 'कीजिए', 'दीजिए', 'मदद', 'सहायता', 'जल्दी',
      'कब', 'क्यों'
    ],
  };

  // Devanagari APPENDED, never prepended — alternation is leftmost-first and the reported
  // surface form depends on it.
  ev.opsNouns     = ev.opsNouns.concat(ev.devOpsNouns);
  ev.problemMarkers = ev.problemMarkers.concat(ev.devProblemMarkers);
  ev.requestMarkers = ev.requestMarkers.concat(ev.devRequestMarkers);

  // ── entities.py _NOT_AN_ID ───────────────────────────────────────────────────────────
  // Stops "DC CODE IS MISSING" yielding CODE. Applied to BOTH DC tiers. Only the 3-character
  // entries can actually collide with the token class; the rest are kept because the same set
  // guards the live-ticket extraction path, where longer tokens are matchable.
  var notAnId = [
    'ACCOUNT', 'ALL', 'AM', 'AMOUNT', 'AND', 'AT', 'BE', 'CODE', 'CYCLE', 'DATE', 'DAY',
    'DETAILS', 'FOR', 'HAS', 'HUB', 'ID', 'IN', 'IS', 'ISSUE', 'KINDLY', 'MOBILE', 'MY',
    'NAME', 'NEW', 'NO', 'NOT', 'NUMBER', 'NUMBERS', 'OF', 'OLD', 'ON', 'PAYMENT', 'PENDING',
    'PLEASE', 'PLZ', 'SIR', 'STATUS', 'TEAM', 'THE', 'TO', 'VALMO', 'YES'
  ];

  _cache.lex = { noise: noise, ev: ev, notAnId: notAnId };
  return _cache.lex;
}
