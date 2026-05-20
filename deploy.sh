#!/usr/bin/env bash
# PTGO v2 — Deploy-Skript für den Produktionsserver (uvicorn hinter Nginx, systemd).
# Auf dem Server ausführen:  ./deploy.sh
# Holt den neuesten main-Stand, installiert Abhängigkeiten und startet den Dienst neu.

set -euo pipefail
cd "$(dirname "$0")"

BRANCH="${BRANCH:-main}"
SERVICE="${SERVICE:-ptgo}"          # Name des systemd-Service (anpassen falls anders)
PORT="${PORT:-8000}"

echo "→ Hole neuesten Stand (origin/${BRANCH}) ..."
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull origin "$BRANCH"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
echo "→ Installiere Abhängigkeiten ..."
pip install --quiet -r requirements.txt

echo "→ Starte Dienst neu: ${SERVICE} ..."
if systemctl list-units --type=service --all 2>/dev/null | grep -q "${SERVICE}.service"; then
  sudo systemctl restart "$SERVICE"
  sleep 2
  curl -sf -o /dev/null "http://127.0.0.1:${PORT}/health" \
    && echo "✓ Deploy erfolgreich — /alex ist live." \
    || echo "⚠ Dienst neu gestartet, aber /health antwortet nicht. Logs prüfen: journalctl -u ${SERVICE} -n 50"
else
  echo "⚠ systemd-Service '${SERVICE}' nicht gefunden."
  echo "  Setze den korrekten Namen:  SERVICE=<name> ./deploy.sh"
  echo "  Oder starte manuell:  ./start.sh"
fi
