/**
 * FliegBar – serverseitiger Passwortschutz (Cloudflare Pages Functions).
 *
 * Erzwingt HTTP-Basic-Auth für ALLE Anfragen an die Seite. Das Passwort steht
 * NICHT in dieser Datei und nicht im ausgelieferten HTML, sondern ausschließlich
 * als geschützte Umgebungsvariable `SITE_PASSWORD` im Cloudflare-Dashboard.
 *
 * Benutzername ist beliebig (z. B. "pilot"); geprüft wird nur das Passwort.
 * Ist SITE_PASSWORD nicht gesetzt, bleibt die Seite gesperrt (fail closed).
 */
export const onRequest = async (context) => {
  const { request, env, next } = context;
  const expected = env.SITE_PASSWORD;

  // Fail closed: ohne konfiguriertes Passwort keinen Zugang gewähren.
  if (!expected) {
    return new Response(
      "SITE_PASSWORD ist nicht konfiguriert. Bitte im Cloudflare-Dashboard setzen.",
      { status: 503 }
    );
  }

  const header = request.headers.get("Authorization") || "";
  const [scheme, encoded] = header.split(" ");

  if (scheme === "Basic" && encoded) {
    let decoded = "";
    try { decoded = atob(encoded); } catch (_) { decoded = ""; }
    const sep = decoded.indexOf(":");
    const password = sep >= 0 ? decoded.slice(sep + 1) : "";
    if (safeEqual(password, expected)) {
      return next(); // Passwort korrekt -> App ausliefern
    }
  }

  return new Response("Zugang nur mit Passwort.", {
    status: 401,
    headers: {
      "WWW-Authenticate": 'Basic realm="FliegBar", charset="UTF-8"',
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
};

// Laufzeit-konstanter Vergleich (verhindert Timing-Rückschlüsse auf das Passwort).
function safeEqual(a, b) {
  const enc = new TextEncoder();
  const ba = enc.encode(a);
  const bb = enc.encode(b);
  if (ba.length !== bb.length) return false;
  let diff = 0;
  for (let i = 0; i < ba.length; i++) diff |= ba[i] ^ bb[i];
  return diff === 0;
}
