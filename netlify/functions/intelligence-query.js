const headers = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'Content-Type',
};

async function callClaude(prompt, systemPrompt) {
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
      model: 'claude-sonnet-4-20250514',
      max_tokens: 4096,
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
    const { action, query, signal } = JSON.parse(event.body);

    if (action === 'query') {
      const systemPrompt = `Du bist ein Elite-Intelligence-Analyst in einem Enterprise Command Center.
Du analysierst Markt-, Wettbewerbs-, Finanz- und Operations-Daten.
Antworte auf Deutsch. Sei praezise, strategisch und handlungsorientiert.
Strukturiere deine Antworten mit klaren Abschnitten.
Nutze Daten und Fakten. Gib konkrete Handlungsempfehlungen.
Format: Verwende kurze Absaetze, Aufzaehlungen und klare Ueberschriften.`;

      const result = await callClaude(query, systemPrompt);
      return { statusCode: 200, headers, body: JSON.stringify(result) };
    }

    if (action === 'deep_dive') {
      const systemPrompt = `Du bist ein Senior Intelligence Analyst. Fuehre eine tiefgehende Analyse durch.
Antworte auf Deutsch. Strukturiere deine Analyse in:
1. SIGNAL BEWERTUNG — Was bedeutet dieses Signal?
2. IMPACT ANALYSE — Welche Auswirkungen hat es?
3. RISIKO BEWERTUNG — Welche Risiken entstehen?
4. HANDLUNGSEMPFEHLUNGEN — Was sollte sofort getan werden?
5. MONITORING — Was muss weiter beobachtet werden?
Sei praezise und strategisch.`;

      const prompt = `Analysiere dieses Intelligence Signal im Detail:\n\nKategorie: ${signal.category}\nTitel: ${signal.title}\nBeschreibung: ${signal.description}\nSeverity: ${signal.severity}`;

      const result = await callClaude(prompt, systemPrompt);
      return { statusCode: 200, headers, body: JSON.stringify(result) };
    }

    if (action === 'generate_signals') {
      const systemPrompt = `Du bist ein Enterprise Intelligence System. Generiere aktuelle, realistische Intelligence Signals.
Antworte NUR mit validem JSON Array. Keine Erklaerungen, kein Markdown.
Jedes Signal hat: category (market|competitor|financial|operations), title (kurz, deutsch), description (1-2 Saetze deutsch), severity (critical|high|medium|low), timestamp (ISO string von heute).
Generiere genau 8 diverse Signals.`;

      const result = await callClaude('Generiere 8 aktuelle Intelligence Signals fuer ein Enterprise Dashboard. Mische die Kategorien: Market, Competitor, Financial, Operations. Nutze realistische aktuelle Themen.', systemPrompt);

      let signals = [];
      try {
        const text = result.text || '';
        const jsonMatch = text.match(/\[[\s\S]*\]/);
        if (jsonMatch) signals = JSON.parse(jsonMatch[0]);
      } catch (e) {
        signals = [];
      }

      return { statusCode: 200, headers, body: JSON.stringify({ signals }) };
    }

    return { statusCode: 400, headers, body: JSON.stringify({ error: 'Unknown action' }) };
  } catch (err) {
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};
