#!/bin/sh
# Load LLM + data credentials into the environment — env-first, baked-file fallback.
# SOURCE this before starting the backend (local dev, systemd, or a GCP startup script):
#     cd backend && . scripts/load_env.sh && uvicorn app.main:app --port 8077
# In production, prefer real env vars / GCP Secret Manager (a set var is never overwritten).
# Run from the backend/ directory, or set PSP_DATA to the data dir.
DATA="${PSP_DATA:-data}"

set_from_file() {
    var="$1"; file="$2"
    eval "cur=\$$var"
    if [ -z "$cur" ] && [ -f "$file" ]; then
        val="$(tr -d '\r\n' < "$file")"
        [ -n "$val" ] && export "$var=$val"
    fi
}

# ── backend/.env FIRST, because that is where the keys actually live ────────────────────────
# This script read only the baked `data/*.txt` files, and `data/anthropic_key.txt` does not
# exist — while `backend/.env` has held a working ANTHROPIC_API_KEY all along. With
# `provider: claude` active, that meant EVERY LLM turn failed: the chat widget rendered one
# error bubble ("I can't reach my reasoning model"), so there were no progress steps to show
# and no answer to type out, and the whole thing read as "no animations, no interactivity".
#
# PARSED, not sourced. `set -a; . .env` was the obvious version and it is wrong: it OVERWRITES
# a variable that is already set, so a real deploy-time env var loses to a checked-in file. This
# file's own header promises the opposite ("a set var is never overwritten"), and in production
# that promise is the whole point — GCP Secret Manager must beat a stale local .env.
# Verified both ways: an absent var gets the file's value, a present one keeps its own.
#
# Only `KEY=value` lines are read; comments, blanks and `export ` prefixes are handled, and
# surrounding quotes are stripped. Anything else in the file is ignored rather than executed,
# which also means a `.env` cannot run commands as a side effect of loading credentials.
ENV_FILE="${PSP_ENV_FILE:-.env}"
if [ -f "$ENV_FILE" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
        line="${line#export }"
        case "$line" in
            ''|\#*) continue ;;
            *=*) ;;
            *) continue ;;
        esac
        k="${line%%=*}"; v="${line#*=}"
        case "$k" in *[!A-Za-z0-9_]*|'') continue ;; esac
        # strip one layer of matching quotes
        case "$v" in \"*\") v="${v#\"}"; v="${v%\"}" ;; \'*\') v="${v#\'}"; v="${v%\'}" ;; esac
        eval "cur=\$$k"
        [ -z "$cur" ] && [ -n "$v" ] && export "$k=$v"
    done < "$ENV_FILE"
fi

set_from_file OPENAI_API_KEY     "$DATA/llm_key.txt"
set_from_file ANTHROPIC_API_KEY  "$DATA/anthropic_key.txt"
set_from_file GEMINI_API_KEY     "$DATA/gemini_key.txt"
set_from_file OPENAI_BASE_URL    "$DATA/llm_base_url.txt"
set_from_file ANTHROPIC_BASE_URL "$DATA/anthropic_base_url.txt"
set_from_file TURSO_DATABASE_URL "$DATA/turso_url.txt"     # external loss DB (else local valmo.db)
set_from_file TURSO_AUTH_TOKEN   "$DATA/turso_token.txt"
# Session signing key. Unset, tokens.py signs with a RANDOM per-process secret and every backend
# restart silently logs everyone out — which during a demo reads as the app being broken. Baked
# into a gitignored file so it survives restarts locally; in production this comes from the
# platform secret store, never from a file in the image.
set_from_file AUTH_SECRET         "$DATA/auth_secret.txt"

# ── Account data provider ────────────────────────────────────────────────────────────────────
# MUST be set here. render.yaml sets it for the deployed image and carries a note that the
# Dockerfile never sources this script — but the converse was never done, so locally it was set
# by hand in one shell, months ago, and survived only as long as that process did.
#
# The failure is silent and total: `captain_context._select_provider()` defaults to `demo`, which
# knows three hand-written seed captains and none of the real partner ids. `_run_turn` checks the
# captain FIRST, before the pre-router, so an unknown one yields an `error` trace event and
# returns — and the widget shows nothing at all. Even "hi" gets no reply, because the greeting
# tier is never reached.
#
# `localdb` reads the real loss-attribution ledger keyed on real partner_id. `demo` for the three
# seed captains, `prism` once a data-lake token exists.
export PSP_DATA_PROVIDER="${PSP_DATA_PROVIDER:-localdb}"

# ── TEMPORARY: Groq bridge (Groq is OpenAI-API-compatible, so no provider code is needed) ──
# NOTE: a Groq stop-gap bridge used to auto-activate here whenever data/groq_key.txt was
# present, silently routing every LLM call to Groq and stamping results 'provisional'. It was
# removed deliberately: an env file that changes which model answers, based on whether a file
# happens to exist, is a footgun — the demo can end up on the wrong model without anyone
# choosing it. Provider selection now lives in exactly one place: config/models.yaml.

# ── the deterministic pre-router ──────────────────────────────────────────────────────────
# PSP_PREROUTER = off | shadow | on, with PSP_PREROUTER_<TIER> overriding per tier.
#
# The global default stays SHADOW because that is what collects the one number the corpus
# cannot give us — which turns captains actually take deterministically. 81.6% of tickets are
# WhatsApp and the repo holds none of its message text, so absorption is unmeasurable offline
# and shadow mode is how it gets measured rather than asserted.
#
# GREETINGS ARE THE EXCEPTION, and they are live. A greeting is a closed whitelist over a fixed
# phrase list: there is nothing shadow mode could teach us about "ok thanks bhai" that the
# 77-phrase golden file has not already settled, and every turn spent in shadow costs the
# measured Rs 4.54 to answer with a model what a dictionary answers exactly. check_router.py
# holds it to zero false positives across all 77 phrasings.
#
# FOLLOW-UPS ARE NOW LIVE TOO, and the reason is that the condition this line used to carry
# could not be met. It read: "flip PSP_PREROUTER_FOLLOWUP=on once the shadow diffs have been
# read." Nothing reads them. `router.route()` writes the withheld reply to
# `trace["shadow_reply"]` and `_persist_trace` saves it, and no script anywhere loads traces back
# to compare shadow against the LLM — so the number shadow mode exists to produce was being
# collected and never reported, indefinitely.
#
# What shadow was costing meanwhile, measured against the real env: `hardstop kya hai`,
# `rto kya hai` and `shortage ka process batao` each produced a correct, sourced, cited Hinglish
# SOP answer, and each returned `None` to the captain, who was then billed the measured Rs 4.54
# for a model to answer the same question less well.
#
# The whitelist itself is settled: check_followups.py holds 83 phrasings across 27 sourced nodes,
# with must-not-fire negatives, a corpus-citation assertion on every source, and a check that no
# authored answer claims a completed payment or escalation. `scripts/check_shadow.py` now reads
# the traces, so the NEXT tier to flip has the evidence this one never got.
export PSP_PREROUTER="${PSP_PREROUTER:-shadow}"
export PSP_PREROUTER_GREETING="${PSP_PREROUTER_GREETING:-on}"
export PSP_PREROUTER_FOLLOWUP="${PSP_PREROUTER_FOLLOWUP:-on}"
# Glossary — the same 27 nodes, reachable on turn one. Live for the same reason greetings are:
# a closed set of UNGATED definitions, true for every captain regardless of their data, so there
# is no per-captain premise that could be false and nothing for shadow to observe. Cold scope is
# glossary + universal only; see followups.glossary_tier for why the per-mechanism nodes stay
# warm-only.
export PSP_PREROUTER_GLOSSARY="${PSP_PREROUTER_GLOSSARY:-on}"
