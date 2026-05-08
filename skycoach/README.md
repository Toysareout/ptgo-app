# SkyCoach AI

**KI-Fluganalyse für Gleitschirmpiloten.**

MVP für eine Webanwendung, die IGC-Flugaufzeichnungen analysiert und automatisch Steigwerte, Sinkwerte, Thermiken, Risiko-Scores und Coaching-Hinweise erzeugt.

> ⚠ **Rechtlicher Hinweis:** SkyCoach AI ist ein **Trainings- und Analysewerkzeug. Kein zertifiziertes Fluginstrument.** Die App ersetzt keine Flugausbildung und keine fachliche Beurteilung durch einen Fluglehrer.

---

## Funktionsumfang

**V1 (MVP):**
- Account & Anmeldung (E-Mail + Passwort, Bearer-Token)
- IGC-Upload mit Drag & Drop
- Flugauswertung: Dauer, Strecke, Luftlinie, max/min Höhe, Höhengewinn, Steig-/Sinkwerte (geglättet), Boden- und Spitzengeschwindigkeit
- Thermik-Erkennung (Phasen mit Steigen ≥ 0.5 m/s, ≥ 20 s)
- 0–100 Risiko-Score mit `low / medium / high`-Einstufung
- Strukturierte deutschsprachige Coaching-Hinweise
- Flugtagebuch mit Persistierung in SQLite/PostgreSQL
- Pilotenprofil (Level, Schein, Schirmklasse, Stunden, Region)
- Track-Vorschau als SVG

**V2 (this branch):**
- Open-Meteo-Wetterdaten (Wind, Böen, Richtung, Temperatur am Startpunkt zur Startzeit)
- Wetter fließt in Risiko-Score und Coaching ein
- Pilot-Level-spezifisches Coaching (Schüler bekommt Sicherheitsfokus, XC bekommt Performance-Hebel)
- Schirmklassen-spezifische Warnungen (EN-C/D/CCC in starker Thermik)
- **Stripe-Pro-Subscription** (12 €/Monat) + Free-Tier Rate Limit (3 Analysen/Monat)
- Pytest-Suite (28 Tests: Parser, Analyzer, Personalisierung, Quota)
- Calibration-CLI für die Validierung mit echten IGC-Dateien

## Architektur

```
skycoach/
├── backend/                    FastAPI + SQLAlchemy
│   ├── skycoach/
│   │   ├── igc_parser.py       IGC-Parser (B-Records, Header)
│   │   ├── analyzer.py         Metriken, Thermiken, Risiko-Score, Coaching
│   │   ├── weather.py          Open-Meteo-Lookup
│   │   ├── billing.py          Stripe-Subscription + Quota
│   │   ├── db.py               SQLAlchemy-Modelle
│   │   ├── auth.py             HMAC-signierte Bearer-Token
│   │   └── main.py             FastAPI-App + Routen
│   ├── tests/                  Pytest-Suite (28 Tests)
│   ├── scripts/calibrate.py    CLI für echte IGC-Validierung
│   ├── Dockerfile, fly.toml    Fly.io-Deployment
│   └── requirements.txt
├── frontend/                   React + Vite
│   ├── src/
│   │   ├── App.jsx, api.js
│   │   └── components/
│   ├── vercel.json             Vercel-Deployment
│   └── package.json
└── DEPLOY.md                   Schritt-für-Schritt Production-Setup
```

## Lokale Entwicklung

### Backend

```bash
cd skycoach/backend
pip install -r requirements.txt
./run.sh                        # http://127.0.0.1:8001
```

API-Docs: <http://127.0.0.1:8001/docs>

### Frontend

```bash
cd skycoach/frontend
npm install
npm run dev                     # http://127.0.0.1:5173
```

Vite proxiet `/api` und `/health` automatisch auf `127.0.0.1:8001`.

## Umgebungsvariablen (Backend)

| Variable           | Default                       | Zweck                              |
|--------------------|-------------------------------|------------------------------------|
| `SKYCOACH_DB_URL`  | `sqlite:///./skycoach.db`     | SQLAlchemy-Verbindungs-URL         |
| `SKYCOACH_SECRET`  | `dev-secret-change-me`        | HMAC-Signatur für Bearer-Token     |

In Produktion **müssen** beide gesetzt werden — insbesondere `SKYCOACH_SECRET`.

## API-Endpunkte

| Methode | Pfad                       | Authentifizierung | Zweck                                    |
|---------|----------------------------|-------------------|------------------------------------------|
| GET     | `/health`                  | nein              | Liveness-Check                           |
| POST    | `/api/auth/register`       | nein              | Neues Konto                              |
| POST    | `/api/auth/login`          | nein              | Bearer-Token erhalten                    |
| GET     | `/api/me`                  | ja                | Profil lesen                             |
| PATCH   | `/api/me`                  | ja                | Profil aktualisieren                     |
| POST    | `/api/analyze`             | nein              | IGC analysieren (ohne Speichern)         |
| POST    | `/api/flights`             | ja                | IGC analysieren + speichern              |
| GET     | `/api/flights`             | ja                | Flugtagebuch                             |
| GET     | `/api/flights/{id}`        | ja                | Vollanalyse abrufen                      |
| DELETE  | `/api/flights/{id}`        | ja                | Flug löschen                             |

## Risiko-Score (V1, regelbasiert)

Die Heuristik bewertet einen Flug auf einer 0–100-Skala anhand von:

- **Sinkwerte:** Spitzen unter -3/-5 m/s (Lee, Abwinde)
- **Bodengeschwindigkeit:** ≥ 45/55/65 km/h (Rückenwind-Indikator)
- **Steigwerte:** ≥ 4/6 m/s (turbulente Barträder)
- **Höhenband + Mindesthöhe** als Proxy für Höhenreserve (DEM-Lookup folgt in V2)
- **Sehr kurze Flüge** (< 3 min) als Hinweis auf Startabbrüche

Die Schwellen sind in `analyzer.py` zentral dokumentiert und werden in V2 mit echten Pilot- und Wetterdaten kalibriert.

## Roadmap

| Version | Inhalt |
|---------|--------|
| **V1 — MVP** *(dieses Repo)* | IGC-Upload, regelbasierte Analyse, Coaching, Flugtagebuch |
| V2 | Wetterdaten-Integration, Pilot-Level-Anpassung, Schirmklasse, Tagesrisiko, Spot-Datenbank |
| V3 | Live-Flugmodus, GPS, Vario-Anbindung, Lee-/Rotor-Warnung, Notlandeplatz-Vorschläge |
| V4 | Kameraanalyse, Wolkenanalyse, Stressdaten, Smartwatch, Flugschul-Dashboard |

## Geplante KI-Integration (V2+)

- Claude API für narrativen Coaching-Bericht (über die regelbasierten Hinweise hinaus)
- Pattern-Detection-Modell für gefährliche Flugmuster (basierend auf realen IGC-Korrekturen aus der V1-Testphase)
- Wetter–IGC-Korrelation für personalisierte Flugfenster

## Tests

```bash
cd skycoach/backend
pip install -r requirements.txt pytest
pytest -q
```

28 Tests decken Parser-Edge-Cases, Analyzer-Heuristiken, Pilot-Level-/Wing-Class-Personalisierung, Wetter-Integration und Free-Tier-Quota ab.

## Calibration mit echten IGC-Dateien

```bash
cd skycoach/backend
python -m scripts.calibrate /pfad/zu/igc-ordner --out calibration.csv --json
```

Die CSV-Spalten enthalten Risiko-Score, erkannte Thermiken, Warnungen — vergleiche sie mit der Fluglehrer-Bewertung, dann passe die Schwellen in `analyzer.py` an.

## Deployment

Siehe [DEPLOY.md](./DEPLOY.md). Stack: Fly.io für Backend, Vercel für Frontend, Stripe für Pro-Subscription, Open-Meteo für Wetter (kein API-Key nötig).
