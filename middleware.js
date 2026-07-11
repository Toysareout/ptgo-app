// Vercel Edge Middleware — Passwortschutz für geschützte Seiten.
// Benutzername beliebig / leer, nur das Passwort zählt.
//   /alex          → Passwort: 26
//   /skyworthy     → Passwort: flyptgo (SKYWORTHY — Elite Paragliding Decision Cockpit)
//   /mastery-rollo → Passwort: fuckit2026 (Rollo Tomassi — Iron Rules)
// Übrige Site bleibt unberührt.

export const config = {
  matcher: ['/alex', '/alex.html', '/alex-tagesplan.ics', '/skyworthy', '/skyworthy.html', '/mastery-rollo', '/mastery-rollo.html'],
};

const ROUTE_PASSWORDS = [
  { match: (p) => p.startsWith('/alex'), password: '26', realm: 'Blue Electric Life' },
  { match: (p) => p.startsWith('/skyworthy'), password: 'flyptgo', realm: 'SKYWORTHY' },
  { match: (p) => p.startsWith('/mastery-rollo'), password: 'fuckit2026', realm: 'Rollo Tomassi - Iron Rules' },
];

export default function middleware(req) {
  const pathname = new URL(req.url).pathname;
  const route = ROUTE_PASSWORDS.find((r) => r.match(pathname));
  if (!route) return; // nicht geschützt

  const auth = req.headers.get('authorization');
  if (auth) {
    const [scheme, encoded] = auth.split(' ');
    if (scheme === 'Basic' && encoded) {
      try {
        const decoded = atob(encoded); // "user:pass"
        const pass = decoded.slice(decoded.indexOf(':') + 1);
        if (pass === route.password) return; // Zugang frei
      } catch (e) {
        // fällt unten auf 401 zurück
      }
    }
  }
  return new Response(`Zugang geschützt — ${route.realm}`, {
    status: 401,
    headers: {
      'WWW-Authenticate': `Basic realm="${route.realm}", charset="UTF-8"`,
      'Content-Type': 'text/plain; charset=utf-8',
    },
  });
}
