# SkyCoach AI

**KI-Fluganalyse für Gleitschirmpiloten.**

MVP für eine Webanwendung, die IGC-Flugaufzeichnungen analysiert und automatisch Steigwerte, Sinkwerte, Thermiken, Risiko-Scores und Coaching-Hinweise erzeugt.

> ⚠ **Rechtlicher Hinweis:** SkyCoach AI ist ein **Trainings- und Analysewerkzeug. Kein zertifiziertes Fluginstrument.** Die App ersetzt keine Flugausbildung und keine fachliche Beurteilung durch einen Fluglehrer.

---

## Funktionsumfang (V1 – MVP)

- Account & Anmeldung (E-Mail + Passwort, JWT-Token)
- IGC-Upload mit Drag & Drop
- Flugauswertung:
  - Flugzeit, Strecke, Luftlinie
  - Maximale & minimale Höhe, Höhengewinn
  - Steig-/Sinkwerte (geglättet, GPS-Jitter-resistent)
  - Boden- und Spitzengeschwindigkeit
  - Thermik-Erkennung (Phasen mit anhaltendem Steigen ≥ 0.5 m/s)
  - 0–100 Risiko-Score mit `low / medium / high`-Einstufung
- KI-Coaching-Hinweise (regelbasiert in V1, ML/Claude-Integration in V2)
- Flugtagebuch mit Persistierung in SQLite/PostgreSQL
- Pilotenprofil (Level, Schein, Schirmklasse, Stunden, Region)
- Track-Vorschau als SVG (kein Map-Library nötig in V1)

## Architektur

```
skycoach/
├── backend/                    FastAPI + SQLAlchemy
│   ├── skycoach/
│   │   ├── igc_parser.py       IGC-Parser (B-Records, Header)
│   │   ├── analyzer.py         Metriken, Thermiken, Risiko-Score, Coaching
│   │   ├── db.py               SQLAlchemy-Modelle
│   │   ├── auth.py             HMAC-signierte Bearer-Token
│   │   └── main.py             FastAPI-App + Routen
│   ├── requirements.txt
│   └── run.sh
└── frontend/                   React + Vite
    ├── src/
    │   ├── App.jsx
    │   ├── api.js
    │   └── components/
    └── package.json
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

Smoke-Test des Parsers + Analyzers + aller HTTP-Endpunkte wurde manuell ausgeführt.
Ein dediziertes pytest-Setup folgt, sobald echte IGC-Testdateien verfügbar sind.
