#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=".env"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE — copy .env.example, fill in your values, then re-run."
    exit 1
fi

# Unset any ambient env vars so the local .env file wins unconditionally
unset ANTHROPIC_API_KEY ANTHROPIC_BASE_URL \
      DIGEST_RECIPIENT_EMAIL DIGEST_SENDER_EMAIL \
      SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD

set -a
source "$ENV_FILE"
set +a

# Use the project venv if it exists, otherwise fall back to system python3
if [[ -f ".venv/bin/python" ]]; then
    PYTHON=".venv/bin/python"
else
    PYTHON="python3"
fi

echo "Running digest with $PYTHON..."
"$PYTHON" digest.py
