# GO LIVE — recovery.ptgo.de (Flight Recovery) · Domain bei Wix

> Alles im Code ist fertig und auf `recovery.ptgo.de` eingestellt.
> Es fehlt **nur** ein einziger DNS-Eintrag bei Wix + die Domain in Netlify. ~3 Minuten.
> Wir nehmen bewusst die **Subdomain** `recovery.ptgo.de` — bei Wix ist das ein simpler, kugelsicherer CNAME (der Root-Eintrag ist bei Wix oft gesperrt/zickig).

---

## SCHRITT 1 — Domain in Netlify hinzufügen

1. Netlify → deine Seite (die `thetoysareout.com` ausliefert)
2. **Domain management** → **Add a domain** → `recovery.ptgo.de` eingeben → bestätigen
3. Netlify merkt sich die Subdomain und stellt später automatisch das SSL-Zertifikat aus.
4. Notiere deinen Netlify-Seitennamen — die `*.netlify.app`-Adresse (steht oben im Dashboard, z. B. `toysareout.netlify.app`). Die brauchst du gleich.

---

## SCHRITT 2 — EIN CNAME-Eintrag bei Wix

1. Wix-Account öffnen: **wix.com/account/domains**
2. Auf `ptgo.de` klicken → **Manage DNS Records** (ggf. unter „⋯" / Advanced)
3. Im Bereich **CNAME (Aliases)** → **+ Add Record**:

| Feld | Wert |
|------|------|
| **Host Name** | `recovery` |
| **Value / Points to** | `<dein-netlify-name>.netlify.app` |
| **TTL** | Standard lassen |

4. **Speichern.**

> ✅ Den bestehenden `app`-Eintrag (deine PTGO-App auf `app.ptgo.de`) NICHT anfassen.
> Du fügst nur einen neuen `recovery`-Eintrag hinzu — nichts wird überschrieben.

DNS braucht 5 Min bis wenige Stunden. Danach zeigt `recovery.ptgo.de` auf Netlify.

---

## SCHRITT 3 — Env-Variablen prüfen (einmalig)

In Netlify (**Site settings → Environment variables**) — dieselben, die deine Buchungen nutzen:

- `SMTP_USER`, `SMTP_PASS` (E-Mail-Benachrichtigung)
- `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (Speicherung)
- optional `RECOVERY_NOTIFY_EMAIL` (Standard: `thetoysareout@gmx.de`)

Wenn deine Live-Piano-Buchungen funktionieren, sind die Werte schon da → nichts zu tun.

---

## SCHRITT 4 — Testen (echter Browser)

1. `https://recovery.ptgo.de/check` öffnen (Safari/Chrome, nicht der Chat-Viewer)
2. Chips antippen, Schieber ziehen, **Absenden**
3. Prüfen: E-Mail an `thetoysareout@gmx.de`? Eintrag in Supabase (`bot_bookings`, `booking_type = recovery_survey`)?

Beides da → **live.** (Falls nicht: der Mail-Fallback im Formular fängt es auf, nichts geht verloren.)

---

## DIE LINKS — bereit zum Verteilen

| Zweck | URL | QR |
|-------|-----|----|
| **Piloten-Check** (an Fluglehrer) | `recovery.ptgo.de/check` | `public/qr-check.png` |
| Verkaufsseite | `recovery.ptgo.de/flight` | `public/qr-flight.png` |
| Discovery-Feldblatt (für dich) | `recovery.ptgo.de/discovery` | — |

---

## NACHRICHT AN DEN FLUGLEHRER (zum Kopieren)

> Hey [Name], ich entwickle gerade ein Recovery-System speziell für Piloten — gegen die Körperbelastung nach SIV, harten Landungen, Strecke und Hike-and-Fly. Bevor ich irgendwas verkaufe, will ich verstehen, was eure Leute wirklich erleben. Magst du diesen 2-Minuten-Check an deine Piloten schicken? Anonym möglich, reine Forschung, kein Verkauf: **recovery.ptgo.de/check**

---

## Falls bei Wix etwas hakt

- Findest du **„Manage DNS Records"** nicht? Dann ist die Domain evtl. nur „connected", nicht voll bei Wix verwaltet — sag mir, was du im Domains-Menü siehst.
- Kennst du deinen **`*.netlify.app`-Namen** nicht? Steht in Netlify ganz oben auf der Seiten-Übersicht. Schick ihn mir, dann gebe ich dir den exakten Wert für das Wix-Feld.
