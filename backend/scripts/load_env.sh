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

set_from_file OPENAI_API_KEY     "$DATA/llm_key.txt"
set_from_file ANTHROPIC_API_KEY  "$DATA/anthropic_key.txt"
set_from_file GEMINI_API_KEY     "$DATA/gemini_key.txt"
set_from_file OPENAI_BASE_URL    "$DATA/llm_base_url.txt"
set_from_file ANTHROPIC_BASE_URL "$DATA/anthropic_base_url.txt"
set_from_file TURSO_DATABASE_URL "$DATA/turso_url.txt"     # external loss DB (else local valmo.db)
set_from_file TURSO_AUTH_TOKEN   "$DATA/turso_token.txt"

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
# Follow-ups stay in shadow deliberately. Their whitelist is settled (check_followups.py, 38
# phrasings, 22 sourced nodes) but WHICH follow-ups captains actually ask is not — that is
# precisely what shadow reports from live traffic. Flip PSP_PREROUTER_FOLLOWUP=on once the
# shadow diffs have been read.
export PSP_PREROUTER="${PSP_PREROUTER:-shadow}"
export PSP_PREROUTER_GREETING="${PSP_PREROUTER_GREETING:-on}"
