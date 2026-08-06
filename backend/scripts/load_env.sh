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
set_from_file KAPTURE_URL        "$DATA/kapture_url.txt"   # read-only audit-evidence browse
set_from_file KAPTURE_EMAIL      "$DATA/kapture_email.txt"
set_from_file KAPTURE_PASSWORD   "$DATA/kapture_password.txt"

# ── TEMPORARY: Groq bridge (Groq is OpenAI-API-compatible, so no provider code is needed) ──
# Drop a key in data/groq_key.txt, run `python scripts/setup_groq.py` once to pick + verify a model,
# then sourcing this file routes every LLM call to Groq. Delete groq_key.txt to switch back.
# A key already present in the environment always wins (production/Secret Manager is never clobbered).
if [ -z "$OPENAI_BASE_URL" ] && [ -f "$DATA/groq_key.txt" ]; then
    groq_key="$(tr -d '\r\n' < "$DATA/groq_key.txt")"
    if [ -n "$groq_key" ]; then
        export OPENAI_API_KEY="$groq_key"
        export OPENAI_BASE_URL="https://api.groq.com/openai/v1/chat/completions"
        export LLM_PROVIDER="openai"
        if [ -f "$DATA/groq_model.txt" ]; then
            groq_model="$(tr -d '\r\n' < "$DATA/groq_model.txt")"
            [ -n "$groq_model" ] && export LLM_MODEL_FAST="$groq_model" && export LLM_MODEL_DEEP="$groq_model"
        fi
        echo "LLM -> Groq (${LLM_MODEL_DEEP:-model not set: run scripts/setup_groq.py})" >&2
    fi
fi
