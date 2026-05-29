#!/bin/bash
set -e

echo "Setting up sam-bd-pipeline..."

# Create virtual environment if it doesn't exist
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "Created .venv"
fi

# Activate and install dependencies
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
echo "Dependencies installed."

# Copy .env.example if .env doesn't exist yet
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "Created .env from .env.example — fill in your values before running."
else
    echo ".env already exists, skipping."
fi

# Remind about service account
if [ ! -f "secrets/service-account.json" ]; then
    echo ""
    echo "  Place your Google service account JSON at: secrets/service-account.json"
    echo "  Then run: export GOOGLE_SERVICE_ACCOUNT_JSON=\$(cat secrets/service-account.json)"
fi

echo ""
echo "Done. Activate your environment with: source .venv/bin/activate"
