const headers = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'Content-Type',
};

const SYSTEM_PROMPT = `Du bist ein hochpräziser Schmerz-Analyse-Assistent basierend auf praktischer manueller Erfahrung.

Dein Ziel ist es NICHT, Diagnosen im medizinischen Sinne zu stellen, sondern:
- das Schmerzbild klar einzuordnen
- Muster zu erkennen
- die Situation so zu strukturieren, dass eine gezielte manuelle oder einfache Intervention möglich wird

Du arbeitest wie ein erfahrener Praktiker:
- stellst gezielte, kurze Fragen
- gehst Schritt für Schritt vor
- vermeidest Überforderung
- denkst in Mustern, nicht in Theorie

ABLAUF — Gehe IMMER in dieser Reihenfolge vor:

STEP 1: Ort klären
- "Zeig mir genau, wo der Schmerz ist"
- Punkt / Linie / Fläche unterscheiden

STEP 2: Gefühl klären
- "Wie fühlt es sich an?"
  (ziehend / stechend / dumpf / Druck)

STEP 3: Trigger klären
- "Wann wird es schlimmer?"
  (Bewegung / Druck / Ruhe)

STEP 4: Verlauf
- "Seit wann ist es da?"
- plötzlich oder langsam gekommen?

STEP 5: Intensität / Veränderung
- besser / gleich / schlimmer

WICHTIGE REGELN:
- immer nur 1–2 Fragen gleichzeitig
- Fokus auf: Ort, Gefühl, Bewegung, Verlauf
- keine langen Erklärungen
- keine medizinischen Fachbegriffe
- keine Unsicherheit zeigen
- auf Antwort warten, dann nächste logische Frage

INTERNE EINORDNUNG (dem Patienten NICHT zeigen, nur für deine Fragesteuerung):

TYPE A: Linien-/Zugspannung — Schmerz zieht entlang einer Linie
TYPE B: Punktueller Schmerz — klar lokalisierbar, druckempfindlich
TYPE C: Gelenk-/Bewegungsproblem — bei Bewegung / Winkel spezifisch
TYPE D: Diffuse Spannung — großflächig, unklar

Nach jeder Antwort:
1. Kurz zusammenfassen: "Okay, ich sehe…"
2. Nächste präzise Frage stellen
3. Wenn genug Klarheit: einfache, sichere Handlung vorschlagen (leichte Druckanweisung, kleine Bewegung, Wahrnehmungsfokus)
4. Dann fragen: "Was passiert, wenn du das machst?"

NIEMALS:
- medizinische Diagnosen nennen
- komplexe Erklärungen geben
- 5 Dinge auf einmal sagen
- unsicher wirken

IMMER:
- klar
- ruhig
- direkt
- fokussiert

Ziel: maximale Klarheit in minimaler Zeit.

Starte das Gespräch mit einer freundlichen, kurzen Begrüßung und frage nach dem Schmerzort.`;

exports.handler = async (event) => {
  if (event.httpMethod === 'OPTIONS') return { statusCode: 204, headers };
  if (event.httpMethod !== 'POST') return { statusCode: 405, headers, body: JSON.stringify({ error: 'Method not allowed' }) };

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return {
      statusCode: 200, headers,
      body: JSON.stringify({ reply: 'Der Schmerz-Analyse-Assistent ist momentan nicht verfügbar. Bitte versuche es später erneut.' })
    };
  }

  try {
    const { action, message, history } = JSON.parse(event.body);

    if (action !== 'chat') {
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Unknown action' }) };
    }

    // Build messages array
    let messages = Array.isArray(history) ? [...history] : [];

    if (message === '__INIT__') {
      // Initial greeting — send a starter message
      messages = [{ role: 'user', content: 'Hallo, ich habe Schmerzen und brauche Hilfe.' }];
    } else if (message) {
      // Only add if not already in history (client adds before sending)
      // Check if the last message in history is already this user message
      const lastMsg = messages[messages.length - 1];
      if (!lastMsg || lastMsg.role !== 'user' || lastMsg.content !== message) {
        messages.push({ role: 'user', content: message });
      }
    }

    // Keep last 20 messages to limit token usage
    if (messages.length > 20) {
      messages = messages.slice(-20);
    }

    // Ensure messages alternate correctly (Claude API requirement)
    // Filter out any consecutive same-role messages
    const cleanMessages = [];
    for (const msg of messages) {
      if (cleanMessages.length === 0 || cleanMessages[cleanMessages.length - 1].role !== msg.role) {
        cleanMessages.push(msg);
      }
    }
    // Ensure first message is from user
    if (cleanMessages.length > 0 && cleanMessages[0].role !== 'user') {
      cleanMessages.unshift({ role: 'user', content: 'Hallo' });
    }

    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20241022',
        max_tokens: 500,
        system: SYSTEM_PROMPT,
        messages: cleanMessages,
      }),
    });

    const data = await res.json();

    if (data.error) {
      console.error('Claude API error:', data.error);
      return {
        statusCode: 200, headers,
        body: JSON.stringify({ reply: 'Es ist ein Fehler aufgetreten. Bitte versuche es erneut.' })
      };
    }

    const reply = data.content?.[0]?.text || 'Keine Antwort erhalten.';

    return {
      statusCode: 200, headers,
      body: JSON.stringify({ reply })
    };

  } catch (err) {
    console.error('Pain assistant error:', err);
    return {
      statusCode: 500, headers,
      body: JSON.stringify({ reply: 'Es ist ein Fehler aufgetreten. Bitte versuche es erneut.' })
    };
  }
};
