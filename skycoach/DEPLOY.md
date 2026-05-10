# SkyCoach AI — Deployment Guide

## Architektur

- **Backend (FastAPI)** → Fly.io, Region `fra` (Frankfurt)
- **Frontend (React/Vite)** → Vercel
- **Datenbank** → Postgres bei Fly.io oder externes Managed-Postgres
- **Payments** → Stripe (Subscription, Webhook → `/api/billing/webhook`)
- **Wetter** → Open-Meteo (kein Key nötig)

## 1. Backend deployen (Fly.io)

```bash
cd skycoach/backend
fly auth login
fly launch --no-deploy --copy-config         # nur beim ersten Mal
fly postgres create --name skycoach-pg
fly postgres attach skycoach-pg              # setzt DATABASE_URL
fly secrets set \
  SKYCOACH_SECRET="$(openssl rand -hex 32)" \
  SKYCOACH_DB_URL="$DATABASE_URL"
fly deploy
```

Stripe-Secrets nachreichen, sobald die Stripe-Produktseite eingerichtet ist:

```bash
fly secrets set \
  STRIPE_SECRET_KEY=sk_live_... \
  STRIPE_PRICE_ID=price_... \
  STRIPE_WEBHOOK_SECRET=whsec_...
```

Healthcheck: `https://skycoach-api.fly.dev/health` muss `{"status":"ok"}` liefern.

## 2. Stripe einrichten

1. Stripe-Dashboard → **Products** → "SkyCoach Pro", monthly recurring 12 €
2. Price-ID kopieren → `STRIPE_PRICE_ID`
3. **Developers → Webhooks** → neuer Endpoint: `https://skycoach-api.fly.dev/api/billing/webhook`
   - Events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`
4. Signing-Secret kopieren → `STRIPE_WEBHOOK_SECRET`
5. Test mit `stripe trigger checkout.session.completed`

## 3. Frontend deployen (Vercel)

```bash
cd skycoach/frontend
vercel link
vercel --prod
```

Die `vercel.json` proxiet `/api/*` → Fly-Backend. Sobald die Fly-App-URL feststeht, ggf. die Domain in `vercel.json` anpassen.

Eigene Domain (z.B. `app.skycoach.ai`):

```bash
vercel domains add app.skycoach.ai
```

## 4. Smoke-Test im Production-Setup

```bash
curl https://skycoach-api.fly.dev/health
curl -X POST https://skycoach-api.fly.dev/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"smoke@test.de","password":"smoketest123"}'
```

Frontend: Login, IGC hochladen, Pro-Upgrade-Flow durchklicken (Stripe-Test-Karte 4242 4242 4242 4242).

## 5. Calibration-Run mit echten IGC-Dateien

```bash
cd skycoach/backend
python -m scripts.calibrate /path/to/igc-folder --out calibration.csv --json
```

Die CSV im Spreadsheet öffnen, Risiko-Score-Spalte mit der Fluglehrer-Einschätzung vergleichen, Auffälligkeiten in einer Notiz festhalten — daraus folgen die V2-Schwellenanpassungen in `analyzer.py`.

## Umgebungsvariablen — Übersicht

| Variable | Pflicht | Zweck |
|---|---|---|
| `SKYCOACH_SECRET` | ja (prod) | HMAC-Signatur Bearer-Token |
| `SKYCOACH_DB_URL` | ja (prod) | Postgres-URL |
| `STRIPE_SECRET_KEY` | optional | Pro-Subscription |
| `STRIPE_PRICE_ID` | optional | Stripe-Price |
| `STRIPE_WEBHOOK_SECRET` | optional | Webhook-Validierung |
| `SKYCOACH_FREE_MONTHLY_ANALYSES` | optional | Default `3` |
