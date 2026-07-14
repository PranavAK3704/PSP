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
