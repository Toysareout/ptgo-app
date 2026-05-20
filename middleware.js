// Vercel Edge Middleware — Passwortschutz nur für die Alex-Seite.
// Passwort: 26 (Benutzername beliebig / leer). Übrige Site bleibt unberührt.

export const config = {
  matcher: ['/alex', '/alex.html', '/alex-tagesplan.ics'],
};

const PASSWORD = '26';

export default function middleware(req) {
  const auth = req.headers.get('authorization');
  if (auth) {
    const [scheme, encoded] = auth.split(' ');
    if (scheme === 'Basic' && encoded) {
      try {
        const decoded = atob(encoded); // "user:pass"
        const pass = decoded.slice(decoded.indexOf(':') + 1);
        if (pass === PASSWORD) return; // Zugang frei
      } catch (e) {
        // fällt unten auf 401 zurück
      }
    }
  }
  return new Response('Zugang geschützt — Blue Electric Life', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="Blue Electric Life", charset="UTF-8"',
      'Content-Type': 'text/plain; charset=utf-8',
    },
  });
}
