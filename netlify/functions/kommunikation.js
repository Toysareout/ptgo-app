const headers = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'Content-Type',
};

async function callClaude(prompt, systemPrompt, maxTokens = 4000) {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) return { error: 'ANTHROPIC_API_KEY ist nicht als Environment Variable in Vercel konfiguriert. Gehe zu Vercel → Settings → Environment Variables und füge ANTHROPIC_API_KEY hinzu.' };

  try {
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

    if (!res.ok) {
      const errBody = await res.text();
      return { error: `Anthropic API HTTP ${res.status}: ${errBody.substring(0, 300)}` };
    }

    const data = await res.json();

    if (data.error) {
      return { error: `Anthropic API: ${data.error.message || JSON.stringify(data.error)}` };
    }

    const text = data.content?.[0]?.text;
    if (!text) {
      return { error: 'Anthropic API: Keine Antwort erhalten', raw: JSON.stringify(data).substring(0, 500) };
    }

    return { text };
  } catch (e) {
    return { error: `Netzwerk/Fetch-Fehler: ${e.message}` };
  }
}

exports.handler = async (event) => {
  if (event.httpMethod === 'OPTIONS') return { statusCode: 204, headers };
  if (event.httpMethod !== 'POST') return { statusCode: 405, headers, body: JSON.stringify({ error: 'Method not allowed' }) };

  try {
    const { action, chatText, therapistName, context, analysisData, messageCount } = JSON.parse(event.body || '{}');

    // ─── TEST / HEALTH CHECK ───
    if (action === 'test') {
      const hasKey = !!process.env.ANTHROPIC_API_KEY;
      return {
        statusCode: 200, headers,
        body: JSON.stringify({
          status: 'ok',
          function: 'kommunikation',
          anthropic_key_configured: hasKey,
          node_version: process.version,
          timestamp: new Date().toISOString(),
        }),
      };
    }

    // ─── VOSS ANALYSIS ───
    if (action === 'voss_analysis') {
      if (!chatText) return { statusCode: 200, headers, body: JSON.stringify({ error: 'Kein Chat-Text erhalten' }) };

      const systemPrompt = `Du bist ein Elite-Kommunikationscoach, spezialisiert auf Chris Voss' Verhandlungstechniken ("Never Split the Difference"). Analysiere therapeutische Kommunikation auf Weltklasse-Niveau. Antworte IMMER NUR mit validem JSON. Kein Markdown, kein Text davor oder danach. Kein \`\`\`json Block.`;

      const prompt = `Analysiere diesen WhatsApp-Chatverlauf. Der Therapeut heißt "${therapistName || 'unbekannt'}".
${context ? `Kontext: ${context}` : ''}

CHATVERLAUF:
${chatText.substring(0, 6000)}

Bewerte nach 8 Chris Voss Dimensionen (je 0-100):
1. TAKTISCHE EMPATHIE — Echtes Verständnis für die Welt des Patienten?
2. KALIBRIERTE FRAGEN — "Wie/Was"-Fragen statt geschlossener?
3. SPIEGELUNG — Schlüsselwörter des Patienten wiederholt?
4. LABELING — Emotionen benannt ("Es scheint als ob...")?
5. AKKUSATIONS-AUDIT — Negative Erwartungen vorweggenommen?
6. NEIN-ORIENTIERTE FRAGEN — Fragen wo "Nein" gewünscht ist?
7. LATE-NIGHT-DJ — Ruhiger, vertrauensbildender Ton?
8. BLACK SWANS — Versteckte Informationen entdeckt?

Pro Dimension: score (0-100), good (was gut war), improve (was besser geht), elite (Weltklasse-Vorschlag).

Antworte NUR mit diesem JSON:
{"overall_score":0,"dimensions":{"tactical_empathy":{"score":0,"good":"","improve":"","elite":""},"calibrated_questions":{"score":0,"good":"","improve":"","elite":""},"mirroring":{"score":0,"good":"","improve":"","elite":""},"labeling":{"score":0,"good":"","improve":"","elite":""},"accusation_audit":{"score":0,"good":"","improve":"","elite":""},"no_oriented":{"score":0,"good":"","improve":"","elite":""},"late_night_dj":{"score":0,"good":"","improve":"","elite":""},"black_swan":{"score":0,"good":"","improve":"","elite":""}},"top_strengths":["","",""],"top_improvements":["","",""],"pricing_insight":"","uniqueness":"","concrete_scripts":["","","",""]}`;

      const result = await callClaude(prompt, systemPrompt, 4000);

      // If Claude call failed, pass error through
      if (result.error) {
        return { statusCode: 200, headers, body: JSON.stringify({ error: result.error, raw: result.raw || null }) };
      }

      // Parse JSON from response
      let analysis = {};
      try {
        // Try direct parse first
        analysis = JSON.parse(result.text);
      } catch (e1) {
        // Try extracting JSON object
        try {
          const jsonMatch = result.text.match(/\{[\s\S]*\}/);
          if (jsonMatch) {
            analysis = JSON.parse(jsonMatch[0]);
          } else {
            return { statusCode: 200, headers, body: JSON.stringify({ error: 'KI-Antwort enthält kein JSON', raw: result.text.substring(0, 500) }) };
          }
        } catch (e2) {
          return { statusCode: 200, headers, body: JSON.stringify({ error: 'JSON-Parsing fehlgeschlagen: ' + e2.message, raw: result.text.substring(0, 500) }) };
        }
      }

      // Validate we got actual data
      if (!analysis.overall_score && !analysis.dimensions) {
        return { statusCode: 200, headers, body: JSON.stringify({ error: 'KI-Antwort unvollständig', raw: JSON.stringify(analysis).substring(0, 500) }) };
      }

      return { statusCode: 200, headers, body: JSON.stringify(analysis) };
    }

    // ─── MUSK CHECK ───
    if (action === 'musk_check') {
      if (!analysisData || analysisData.error) {
        return { statusCode: 200, headers, body: JSON.stringify({ error: 'Keine Voss-Analyse vorhanden für Musk-Check' }) };
      }

      const systemPrompt = `Du bist Elon Musk. Brutal ehrlich, First-Principles-Denker, 10X-Mindset. Antworte NUR mit validem JSON. Kein Markdown, kein \`\`\`json Block.`;

      const prompt = `Kommunikationsanalyse eines Therapeuten:

${JSON.stringify(analysisData).substring(0, 3000)}

${messageCount || 'Viele'} WhatsApp-Nachrichten mit Patienten.

Beantworte als Elon Musk:
1. FIRST PRINCIPLES: Was grundlegend anders machen?
2. 10X THINKING: Nicht 10% besser, 10x besser?
3. AUTOMATION: Was automatisieren?
4. SCALE: Von 1:1 zu 1:N ohne Qualitätsverlust?
5. PRICING: Preis bei 10x besseren Ergebnissen?
6. SPEED: Was dauert zu lange?
7. KILLER FEATURE: Was fehlt komplett?
8. FINAL VERDICT: Investieren ja/nein?

NUR dieses JSON:
{"first_principles":"","ten_x_thinking":"","automation":"","scale":"","pricing":"","speed":"","killer_feature":"","final_verdict":"","invest":false,"one_line":""}`;

      const result = await callClaude(prompt, systemPrompt, 2000);
      if (result.error) {
        return { statusCode: 200, headers, body: JSON.stringify({ error: result.error }) };
      }

      let musk = {};
      try {
        musk = JSON.parse(result.text);
      } catch (e1) {
        try {
          const jsonMatch = result.text.match(/\{[\s\S]*\}/);
          if (jsonMatch) musk = JSON.parse(jsonMatch[0]);
          else return { statusCode: 200, headers, body: JSON.stringify({ error: 'Musk-Check JSON nicht gefunden', raw: result.text.substring(0, 300) }) };
        } catch (e2) {
          return { statusCode: 200, headers, body: JSON.stringify({ error: 'Musk-Check Parsing fehlgeschlagen', raw: result.text.substring(0, 300) }) };
        }
      }
      return { statusCode: 200, headers, body: JSON.stringify(musk) };
    }

    return { statusCode: 400, headers, body: JSON.stringify({ error: 'Unbekannte Aktion: ' + action }) };
  } catch (err) {
    return { statusCode: 500, headers, body: JSON.stringify({ error: 'Server-Fehler: ' + err.message, stack: err.stack?.substring(0, 300) }) };
  }
};
