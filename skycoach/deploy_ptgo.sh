#!/usr/bin/env bash
#
# Deploy SkyCoach AI als Sub-Mount unter /skycoach in der bestehenden PTGO-App.
#
# Auf dem Server (z.B. /opt/ptgo) ausführen:
#
#     cd /opt/ptgo
#     git pull
#     ./skycoach/deploy_ptgo.sh
#
# Voraussetzungen auf dem Server:
#   - Node.js >= 18 + npm        (für den Frontend-Build)
#   - Python >= 3.9 + pip        (sollte schon da sein für PTGO)
#   - Schreibrechte im Repo + auf den systemd-Service
#
# Was passiert:
#   1. Backend-Dependencies installieren (fastapi/sqlalchemy sind eh schon da,
#      neu nur stripe + ggf. python-multipart)
#   2. Frontend mit base="/skycoach/" bauen → skycoach/frontend/dist/
#   3. systemd-Service neu starten, damit der mount in app.py geladen wird
#
# Nicht-destruktiv: berührt PTGO-Daten / -Config in keiner Weise.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "Repo root: $ROOT"

cd "$ROOT/skycoach/backend"
echo "[1/3] Installiere Backend-Dependencies …"
pip install --quiet -r requirements.txt

cd "$ROOT/skycoach/frontend"
echo "[2/3] Baue Frontend für /skycoach/ …"
if [ ! -d node_modules ]; then
  npm ci --silent || npm install --silent
fi
SKYCOACH_BASE=/skycoach/ npm run build

echo "[3/3] systemd-Service neu starten …"
SERVICE="${PTGO_SERVICE:-ptgo}"
if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
  sudo systemctl restart "$SERVICE"
  echo "Service '$SERVICE' restartet."
else
  echo "Hinweis: systemd-Service '$SERVICE' nicht aktiv — bitte manuell restarten."
  echo "         (Setze PTGO_SERVICE=<name> falls dein Unit anders heißt.)"
fi

echo
echo "Fertig. Healthcheck:"
echo "  curl -s https://app.ptgo.de/skycoach/health"
echo
echo "Optional Stripe aktivieren — als root in der systemd-Unit setzen:"
echo "  STRIPE_SECRET_KEY=sk_live_…"
echo "  STRIPE_PRICE_ID=price_…"
echo "  STRIPE_WEBHOOK_SECRET=whsec_…"
echo "und Webhook-Endpoint anlegen: https://app.ptgo.de/skycoach/api/billing/webhook"
