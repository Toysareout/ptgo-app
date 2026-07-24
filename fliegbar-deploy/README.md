# FliegBar – passwortgeschütztes Deployment

Dieser Ordner ist ein **eigenständiges Deploy-Paket** für die FliegBar-App.
Es enthält **nur** die App – die restliche Codebasis (`app.py` usw.) wird hier
bewusst **nicht** mit veröffentlicht.

```
fliegbar-deploy/
├── index.html            # Die Live-App (echte Open-Meteo-Daten)
├── functions/
│   └── _middleware.js     # Serverseitiger Passwortschutz (HTTP Basic Auth)
└── README.md              # Diese Anleitung
```

## Wie der Passwortschutz funktioniert

- Der Schutz läuft **serverseitig** über eine Cloudflare Pages Function.
- Das Passwort steht **nur** als Umgebungsvariable `SITE_PASSWORD` im
  Cloudflare-Dashboard – **niemals im Code oder im ausgelieferten HTML**.
- Ohne korrektes Passwort liefert der Server `401` und zeigt die App gar nicht
  erst aus. Das ist echter Zugangsschutz, keine Attrappe im Browser.

## Deploy auf Cloudflare Pages (kostenlos, empfohlen)

1. Auf <https://dash.cloudflare.com> anmelden → **Workers & Pages** →
   **Create** → **Pages** → **Connect to Git**.
2. Repo `Toysareout/ptgo-app` wählen, Branch
   `claude/fliegbar-paragliding-weather-psxtl5` (oder nach Merge `main`).
3. Build-Einstellungen:
   - **Framework preset:** None
   - **Build command:** *(leer lassen)*
   - **Build output directory:** `fliegbar-deploy`
   - **Root directory:** *(leer lassen)*
4. **Environment variables** → Variable hinzufügen:
   - **Name:** `SITE_PASSWORD`
   - **Value:** *(das Passwort, das dir Claude im Chat genannt hat)*
   - Als **Secret/Encrypted** markieren.
5. **Save and Deploy.** Nach ~1 Minute bekommst du eine URL wie
   `https://fliegbar-xyz.pages.dev`.
6. Aufruf im Browser → Login-Dialog erscheint. Benutzername beliebig
   (z. B. `pilot`), Passwort = `SITE_PASSWORD`.

### Passwort ändern
Einfach im Dashboard unter **Settings → Environment variables** den Wert von
`SITE_PASSWORD` ändern und neu deployen. Es gibt **keine weitere Stelle**, an
der das Passwort gepflegt werden müsste.

## Alternative: Netlify

Netlifys eingebauter Passwortschutz ist ein kostenpflichtiges Feature (Pro).
Kostenlos geht es mit einer Edge Function analog zu `functions/_middleware.js`
(Ordner `netlify/edge-functions/`). Bei Bedarf baue ich das ebenfalls fertig.

---
*Hinweis: `index.html` ist eine Kopie der Live-Version aus `../fliegbar.html`.
Wird die App aktualisiert, diese Datei mit der Quelle synchron halten.*
