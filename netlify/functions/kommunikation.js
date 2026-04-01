const headers = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'Content-Type',
};

async function callClaude(prompt, systemPrompt, maxTokens = 4000) {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) return { error: 'No ANTHROPIC_API_KEY configured' };

  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: 'claude-haiku-4-5-20241022',
      max_tokens: maxTokens,
      system: systemPrompt,
      messages: [{ role: 'user', content: prompt }],
    }),
  });
  const data = await res.json();
  return { text: data.content?.[0]?.text || data.error?.message || 'Keine Antwort' };
}

exports.handler = async (event) => {
  if (event.httpMethod === 'OPTIONS') return { statusCode: 204, headers };
  if (event.httpMethod !== 'POST') return { statusCode: 405, headers, body: JSON.stringify({ error: 'Method not allowed' }) };

  try {
    const { action, chatText, therapistName, context, analysisData, messageCount } = JSON.parse(event.body);

    // ─── VOSS ANALYSIS ───
    if (action === 'voss_analysis') {
      const systemPrompt = `Du bist ein Elite-Kommunikationscoach, der auf Chris Voss' Verhandlungstechniken spezialisiert ist
(aus "Never Split the Difference"). Du analysierst therapeutische Kommunikation auf Weltklasse-Niveau.
Antworte IMMER als valides JSON. Kein Markdown, kein Text davor oder danach.`;

      const prompt = `Analysiere den folgenden WhatsApp-Chatverlauf. Der Therapeut heißt "${therapistName}".
${context ? `Kontext: ${context}` : ''}

CHATVERLAUF:
${chatText}

Analysiere EXAKT nach diesen 8 Chris Voss Dimensionen und bewerte jede von 0-100:

1. TAKTISCHE EMPATHIE — Zeigt der Therapeut echtes Verständnis für die Welt des Patienten?
2. KALIBRIERTE FRAGEN — Nutzt er "Wie...?" und "Was...?" Fragen statt geschlossener Fragen?
3. SPIEGELUNG (Mirroring) — Wiederholt er Schlüsselwörter des Patienten?
4. LABELING — Benennt er Emotionen mit "Es scheint als ob..." / "Es klingt so als..."?
5. AKKUSATIONS-AUDIT — Nimmt er negative Erwartungen vorweg?
6. NEIN-ORIENTIERTE FRAGEN — Nutzt er Fragen, bei denen "Nein" die gewünschte Antwort ist?
7. LATE-NIGHT-DJ-STIMME — Ist der Ton ruhig, kontrolliert, vertrauensbildend (auch schriftlich erkennbar)?
8. BLACK SWANS — Entdeckt er versteckte Informationen, die alles verändern?

Für JEDE Dimension:
- Score (0-100)
- Was gut gemacht wurde (konkrete Beispiele aus dem Chat)
- Was verbessert werden kann (konkrete Formulierungsvorschläge)
- ELITE-LEVEL Vorschlag: Wie würde ein Weltklasse-Verhandler hier kommunizieren?

Zusätzlich:
- GESAMTSCORE (0-100)
- TOP 3 STÄRKEN
- TOP 3 VERBESSERUNGSPOTENZIALE mit konkreten Formulierungen
- PRICING INSIGHT: Wie kann die Kommunikation den wahrgenommenen Wert maximieren?
- EINZIGARTIGKEIT: Was macht diese Kommunikation einzigartig und was fehlt noch zur Weltklasse?

Antworte als JSON:
{
  "overall_score": <int>,
  "dimensions": {
    "tactical_empathy": {"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"},
    "calibrated_questions": {"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"},
    "mirroring": {"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"},
    "labeling": {"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"},
    "accusation_audit": {"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"},
    "no_oriented": {"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"},
    "late_night_dj": {"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"},
    "black_swan": {"score": <int>, "good": "<text>", "improve": "<text>", "elite": "<text>"}
  },
  "top_strengths": ["<text>", "<text>", "<text>"],
  "top_improvements": ["<text>", "<text>", "<text>"],
  "pricing_insight": "<text>",
  "uniqueness": "<text>",
  "concrete_scripts": ["<formulierung1>", "<formulierung2>", "<formulierung3>", "<formulierung4>", "<formulierung5>"]
}`;

      const result = await callClaude(prompt, systemPrompt, 4000);
      let analysis = {};
      try {
        const jsonMatch = (result.text || '').match(/\{[\s\S]*\}/);
        if (jsonMatch) analysis = JSON.parse(jsonMatch[0]);
      } catch (e) {
        return { statusCode: 200, headers, body: JSON.stringify({ error: 'KI-Antwort konnte nicht geparst werden', raw: (result.text || '').substring(0, 500) }) };
      }
      return { statusCode: 200, headers, body: JSON.stringify(analysis) };
    }

    // ─── MUSK CHECK ───
    if (action === 'musk_check') {
      const systemPrompt = `Du bist Elon Musk. Brutal ehrlich, First-Principles-Denker, 10X-Mindset.
Antworte IMMER als valides JSON. Kein Markdown, kein Text davor oder danach.`;

      const prompt = `Du hast gerade diese Kommunikationsanalyse eines Therapeuten gesehen:

${JSON.stringify(analysisData).substring(0, 3000)}

Der Therapeut hat ${messageCount || 'viele'} WhatsApp-Nachrichten mit Patienten ausgetauscht.

Beantworte BRUTAL EHRLICH aus Elon Musks Perspektive:

1. FIRST PRINCIPLES: Was stimmt an der grundlegenden Annahme nicht? Was würdest du von Grund auf anders machen?
2. 10X THINKING: Wie kann die Kommunikation nicht 10% besser, sondern 10x besser werden?
3. AUTOMATION: Welche Teile der Kommunikation können/sollten automatisiert werden?
4. SCALE: Wie kann dieser Therapeut von 1:1 zu 1:N skalieren ohne Qualitätsverlust?
5. PRICING: Was wäre der Preis, wenn die Ergebnisse 10x besser wären? Wie kommt man dahin?
6. SPEED: Was dauert zu lange? Wo wird Zeit verschwendet?
7. KILLER FEATURE: Was fehlt komplett, das alles verändern würde?
8. FINAL VERDICT: Würde ich investieren? Ja/Nein und warum?

JSON:
{
  "first_principles": "<text>",
  "ten_x_thinking": "<text>",
  "automation": "<text>",
  "scale": "<text>",
  "pricing": "<text>",
  "speed": "<text>",
  "killer_feature": "<text>",
  "final_verdict": "<text>",
  "invest": true/false,
  "one_line": "<Ein Satz der alles zusammenfasst>"
}`;

      const result = await callClaude(prompt, systemPrompt, 2000);
      let musk = {};
      try {
        const jsonMatch = (result.text || '').match(/\{[\s\S]*\}/);
        if (jsonMatch) musk = JSON.parse(jsonMatch[0]);
      } catch (e) {
        return { statusCode: 200, headers, body: JSON.stringify({ error: 'Musk-Check konnte nicht geparst werden' }) };
      }
      return { statusCode: 200, headers, body: JSON.stringify(musk) };
    }

    return { statusCode: 400, headers, body: JSON.stringify({ error: 'Unknown action' }) };
  } catch (err) {
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};
