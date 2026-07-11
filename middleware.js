// Vercel Edge Middleware — GLOBALER Passwortschutz für die gesamte Site.
// Benutzername beliebig / leer, nur das Passwort zählt.
//   ALLE Seiten → Passwort: fuckit2026
//
// Bewusst NICHT gesperrt (sonst brechen Backend & Ressourcen):
//   - API-/Function-Endpunkte (/api/*, /.netlify/functions/*):
//     werden von Stripe/Twilio/Cron ohne Login aufgerufen.
//   - Statische Assets (Bilder/CSS/JS/Fonts/Manifest/…):
//     würden sonst Auth-Prompts auslösen bzw. nicht laden.

const SITE_PASSWORD = 'fuckit2026';
const REALM = 'Protected'; // ASCII-only — Header-tauglich

// Alles matchen AUSSER API-/Function-Routen (die dürfen nie ein 401 bekommen).
export const config = {
  matcher: ['/((?!api/|\\.netlify/).*)'],
};

// Dateiendungen, die ohne Passwort erreichbar bleiben (statische Assets).
const PUBLIC_ASSET = /\.(?:png|jpe?g|gif|svg|ico|webp|avif|css|js|mjs|json|map|txt|xml|woff2?|ttf|otf|eot|mp3|mp4|webm|ogg|pdf|ics|wasm)$/i;

export default function middleware(req) {
  const { pathname } = new URL(req.url);

  // Statische Assets nicht sperren.
  if (PUBLIC_ASSET.test(pathname)) return;

  const auth = req.headers.get('authorization');
  if (auth) {
    const [scheme, encoded] = auth.split(' ');
    if (scheme === 'Basic' && encoded) {
      try {
        const decoded = atob(encoded); // "user:pass"
        const pass = decoded.slice(decoded.indexOf(':') + 1);
        if (pass === SITE_PASSWORD) return; // Zugang frei
      } catch (e) {
        // fällt unten auf 401 zurück
      }
    }
  }
  return new Response('Zugang geschuetzt', {
    status: 401,
    headers: {
      'WWW-Authenticate': `Basic realm="${REALM}", charset="UTF-8"`,
      'Content-Type': 'text/plain; charset=utf-8',
    },
  });
}
