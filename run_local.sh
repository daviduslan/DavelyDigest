#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=".env.local"

if [[ ! -f "$ENV_FILE" ]]; then
    echo "Missing $ENV_FILE — copy .env.example, fill in your values, then re-run."
    exit 1
fi

# Load env vars without exporting them to subshells beyond this script
set -a
# shellcheck source=.env.local
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
