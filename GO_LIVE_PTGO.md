# GO LIVE — ptgo.de (Flight Recovery)

> Alles im Code ist fertig und auf `ptgo.de` eingestellt. Es fehlt **nur** noch dieser eine Schritt:
> die Domain `ptgo.de` mit deiner Netlify-Seite verbinden. ~2 Minuten. Danach läuft `ptgo.de/check`.

---

## SCHRITT 1 — Domain in Netlify hinzufügen

1. Netlify öffnen → deine Seite (die `thetoysareout.com` ausliefert)
2. **Domain management** → **Add a domain** → `ptgo.de` eingeben → bestätigen
3. Netlify fragt evtl. „is this your domain?" → **Yes / Add domain**

Netlify zeigt dir dann an, welche DNS-Einträge du setzen musst. Die Standardwerte stehen unten.

---

## SCHRITT 2 — DNS beim Domain-Anbieter setzen

Bei dem Anbieter, wo `ptgo.de` registriert ist (z. B. IONOS, Strato, GoDaddy …):

**Für die nackte Domain `ptgo.de` (Apex):**

| Typ | Name / Host | Wert |
|-----|-------------|------|
| `A` | `@` (oder leer) | `75.2.60.5` |

**Für `www.ptgo.de`:**

| Typ | Name / Host | Wert |
|-----|-------------|------|
| `CNAME` | `www` | `<dein-netlify-name>.netlify.app` |

> `<dein-netlify-name>` steht in Netlify oben (z. B. `toysareout.netlify.app`).
> **Wichtig:** `app.ptgo.de` (deine PTGO-App) NICHT anfassen — die bleibt auf ihrem eigenen Server. Apex `ptgo.de` und `app.ptgo.de` stören sich nicht.

DNS-Änderungen brauchen 5 Min bis wenige Stunden. Netlify stellt das SSL-Zertifikat danach **automatisch** aus (Status „Netlify managed certificate").

---

## SCHRITT 3 — Env-Variablen prüfen (einmalig)

Damit die Umfrage-Antworten bei dir ankommen, müssen in Netlify (**Site settings → Environment variables**) gesetzt sein — dieselben, die deine Buchungen schon nutzen:

- `SMTP_USER`, `SMTP_PASS` (für die E-Mail-Benachrichtigung)
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (für die Speicherung)
- optional `RECOVERY_NOTIFY_EMAIL` (wohin die Mail geht; Standard: `thetoysareout@gmx.de`)

Wenn deine Live-Piano-Buchungen funktionieren, sind diese Werte schon da — dann ist nichts zu tun.

---

## SCHRITT 4 — Testen (im echten Browser, nicht im Chat-Viewer)

1. `https://ptgo.de/check` öffnen (Safari/Chrome)
2. Ein paar Chips antippen, Schieber ziehen, **Absenden**
3. Prüfen: kommt eine E-Mail an `thetoysareout@gmx.de`? Steht ein Eintrag in Supabase (`bot_bookings`, `booking_type = recovery_survey`)?

Funktioniert beides → **live.** Falls nicht: die Antwort geht trotzdem nicht verloren (Mail-Fallback im Formular).

---

## DIE LINKS — bereit zum Verteilen

| Zweck | URL | QR |
|-------|-----|----|
| **Piloten-Check** (an Fluglehrer) | `ptgo.de/check` | `public/qr-check.png` |
| Verkaufsseite | `ptgo.de/flight` | `public/qr-flight.png` |
| Discovery-Feldblatt (für dich) | `ptgo.de/discovery` | — |

---

## NACHRICHT AN DEN FLUGLEHRER (zum Kopieren)

> Hey [Name], ich entwickle gerade ein Recovery-System speziell für Piloten — gegen die Körperbelastung nach SIV, harten Landungen, Strecke und Hike-and-Fly. Bevor ich irgendwas verkaufe, will ich verstehen, was eure Leute wirklich erleben. Magst du diesen 2-Minuten-Check an deine Piloten schicken? Anonym möglich, reine Forschung, kein Verkauf: **ptgo.de/check**

---

## Falls beim DNS etwas hakt

Sag mir, **welcher Anbieter** `ptgo.de` verwaltet (IONOS / Strato / GoDaddy / …) und **wie dein Netlify-Seitenname** lautet (`*.netlify.app`) — dann gebe ich dir die exakten Felder für genau dieses Anbieter-Menü.
