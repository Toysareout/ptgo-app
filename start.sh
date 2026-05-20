#!/usr/bin/env bash
# PTGO v2 — Start-Skript
# Lokal oder auf dem Server:  ./start.sh
# Installiert Abhängigkeiten (einmalig in .venv) und startet die App.

set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

# Pflicht-Secret in Produktion setzen: export APP_SECRET="..."
export APP_SECRET="${APP_SECRET:-dev-secret-change-me}"
export BASE_URL="${BASE_URL:-http://127.0.0.1:${PORT}}"
export DB_URL="${DB_URL:-sqlite:///./ptgo.db}"

# Virtuelle Umgebung anlegen/aktivieren
if [ ! -d ".venv" ]; then
  echo "→ Erstelle virtuelle Umgebung (.venv) ..."
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "→ Installiere Abhängigkeiten ..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

echo "→ Starte PTGO auf http://${HOST}:${PORT}  (Route: /alex)"
exec uvicorn app:app --host "$HOST" --port "$PORT" "${@:-}"
