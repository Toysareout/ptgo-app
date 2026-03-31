const headers = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'Content-Type',
};

async function callClaude(prompt, systemPrompt, maxTokens = 2000) {
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

// Supabase helper
async function supabaseQuery(table, query = '') {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY || process.env.SUPABASE_ANON_KEY;
  if (!url || !key) return null;

  const res = await fetch(`${url}/rest/v1/${table}?${query}`, {
    headers: {
      'apikey': key,
      'Authorization': `Bearer ${key}`,
      'Content-Type': 'application/json',
    },
  });
  if (!res.ok) return null;
  return res.json();
}

async function collectPatientData() {
  // Get all patients
  const patients = await supabaseQuery('patients', 'select=id,name,phone,email,created_at&order=name.asc');
  if (!patients || !patients.length) return { patients: [], summary: {} };

  const now = new Date();
  const weekAgo = new Date(now - 7 * 24 * 60 * 60 * 1000).toISOString();

  // Get recent check-ins (last 7 days)
  const recentCheckins = await supabaseQuery('checkins',
    `select=id,patient_id,daily_state,stress,sleep,body,craving,avoidance,pattern_code,pattern_label,action_code,action_label,score,risk_level,local_day,created_at,signals_json,mental_text,goal_text&created_at=gte.${weekAgo}&order=created_at.desc`
  ) || [];

  // Get outcomes
  const recentOutcomes = await supabaseQuery('outcomes',
    `select=id,patient_id,checkin_id,rating,outcome_note,created_at&created_at=gte.${weekAgo}`
  ) || [];

  // Aggregate per patient
  const patientData = [];
  const allPatterns = [];
  const allScores = [];
  const allOutcomes = [];
  const riskPatients = [];

  for (const p of patients) {
    const pCheckins = recentCheckins.filter(c => c.patient_id === p.id);
    const pOutcomes = recentOutcomes.filter(o => o.patient_id === p.id);
    const scores = pCheckins.map(c => c.score).filter(s => s != null);
    const patterns = pCheckins.map(c => c.pattern_code).filter(Boolean);

    const avgScore = scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length * 10) / 10 : null;
    const avgStress = pCheckins.filter(c => c.stress != null).length
      ? Math.round(pCheckins.filter(c => c.stress != null).reduce((a, c) => a + c.stress, 0) / pCheckins.filter(c => c.stress != null).length * 10) / 10 : null;
    const avgSleep = pCheckins.filter(c => c.sleep != null).length
      ? Math.round(pCheckins.filter(c => c.sleep != null).reduce((a, c) => a + c.sleep, 0) / pCheckins.filter(c => c.sleep != null).length * 10) / 10 : null;
    const avgCraving = pCheckins.filter(c => c.craving != null).length
      ? Math.round(pCheckins.filter(c => c.craving != null).reduce((a, c) => a + c.craving, 0) / pCheckins.filter(c => c.craving != null).length * 10) / 10 : null;
    const avgAvoidance = pCheckins.filter(c => c.avoidance != null).length
      ? Math.round(pCheckins.filter(c => c.avoidance != null).reduce((a, c) => a + c.avoidance, 0) / pCheckins.filter(c => c.avoidance != null).length * 10) / 10 : null;

    // Trend
    let scoreTrend = null;
    if (scores.length >= 4) {
      const half = Math.floor(scores.length / 2);
      const older = scores.slice(half).reduce((a, b) => a + b, 0) / (scores.length - half);
      const newer = scores.slice(0, half).reduce((a, b) => a + b, 0) / half;
      scoreTrend = newer > older + 3 ? 'steigend' : newer < older - 3 ? 'fallend' : 'stabil';
    }

    // Days since last check-in
    let daysSince = null;
    if (pCheckins.length) {
      const lastDate = new Date(pCheckins[0].created_at);
      daysSince = Math.floor((now - lastDate) / (24 * 60 * 60 * 1000));
    }

    const lastCheckin = pCheckins[0] || null;
    const outcomeRatings = pOutcomes.map(o => o.rating);

    const entry = {
      name: p.name,
      phone: p.phone,
      checkins_7d: pCheckins.length,
      avg_score: avgScore,
      score_trend: scoreTrend,
      last_score: lastCheckin?.score,
      last_risk: lastCheckin?.risk_level,
      last_pattern: lastCheckin?.pattern_label,
      last_day: lastCheckin?.local_day,
      days_since: daysSince,
      patterns: patterns,
      outcomes: outcomeRatings,
      stress_avg: avgStress,
      sleep_avg: avgSleep,
      craving_avg: avgCraving,
      avoidance_avg: avgAvoidance,
      mental_text: lastCheckin?.mental_text || '',
      goal_text: lastCheckin?.goal_text || '',
    };
    patientData.push(entry);

    allPatterns.push(...patterns);
    allScores.push(...scores);
    allOutcomes.push(...outcomeRatings);

    if ((lastCheckin && lastCheckin.risk_level === 'high') || (daysSince != null && daysSince >= 3)) {
      riskPatients.push(entry);
    }
  }

  // Pattern frequency
  const patternFreq = {};
  allPatterns.forEach(p => patternFreq[p] = (patternFreq[p] || 0) + 1);

  // Outcome distribution
  const outcomeDist = { better: 0, same: 0, worse: 0 };
  allOutcomes.forEach(o => { if (outcomeDist[o] !== undefined) outcomeDist[o]++; });

  return {
    patients: patientData,
    risk_patients: riskPatients,
    summary: {
      total_patients: patients.length,
      active_7d: patientData.filter(p => p.checkins_7d > 0).length,
      total_checkins_7d: recentCheckins.length,
      avg_score: allScores.length ? Math.round(allScores.reduce((a, b) => a + b, 0) / allScores.length * 10) / 10 : null,
      pattern_frequency: patternFreq,
      outcome_distribution: outcomeDist,
      risk_count: riskPatients.length,
    },
  };
}

exports.handler = async (event) => {
  if (event.httpMethod === 'OPTIONS') return { statusCode: 204, headers };
  if (event.httpMethod !== 'POST') return { statusCode: 405, headers, body: JSON.stringify({ error: 'Method not allowed' }) };

  try {
    const { action, question } = JSON.parse(event.body);
    const data = await collectPatientData();
    const summary = data.summary;
    const patients = data.patients;
    const riskPatients = data.risk_patients;

    if (action === 'get_data') {
      // Return raw aggregated data (no AI call)
      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    if (action === 'briefing') {
      let patientDetails = '';
      patients.forEach(p => {
        patientDetails += `- ${p.name}: Score Ø${p.avg_score}, Trend ${p.score_trend || '?'}, Check-ins ${p.checkins_7d}, Pattern: ${p.last_pattern || '–'}, Stress Ø${p.stress_avg}, Schlaf Ø${p.sleep_avg}, Craving Ø${p.craving_avg}, Outcomes: [${p.outcomes.join(',')}], Letzter Check-in: ${p.last_day || '–'} (${p.days_since} Tage her)\n`;
      });

      let riskDetails = '';
      riskPatients.forEach(r => {
        const reasons = [];
        if (r.last_risk === 'high') reasons.push('Hohes Risiko');
        if (r.days_since >= 3) reasons.push(`${r.days_since} Tage inaktiv`);
        riskDetails += `- ${r.name}: Score ${r.last_score}, ${reasons.join(', ')}\n`;
      });

      const systemPrompt = `Du bist der KI-CHEFAGENT — ein Executive Intelligence System fuer einen Therapeuten.
Deine Aufgabe: Liefere ein strategisches Executive Briefing ueber ALLE Patienten.
Schreibe auf Deutsch. Sei direkt, konkret, handlungsorientiert. Kein Smalltalk.
Maximal 500 Woerter.

Strukturiere das Briefing exakt so:

LAGE-UEBERBLICK
2-3 Saetze Gesamtbild der Praxis

SOFORT-HANDLUNGSBEDARF
Welche Patienten brauchen Aufmerksamkeit und warum

POSITIVE ENTWICKLUNGEN
Was laeuft gut

MUSTER & TRENDS
Welche Patterns dominieren, was bedeutet das

EMPFEHLUNGEN
3 konkrete naechste Schritte fuer den Therapeuten`;

      const prompt = `DATEN:
- Patienten gesamt: ${summary.total_patients}
- Aktive (7 Tage): ${summary.active_7d}
- Check-ins (7 Tage): ${summary.total_checkins_7d}
- Score Ø: ${summary.avg_score || '–'}
- Patterns: ${JSON.stringify(summary.pattern_frequency)}
- Outcomes: ${JSON.stringify(summary.outcome_distribution)}
- Risiko-Patienten: ${summary.risk_count}

PATIENTEN-DETAILS:
${patientDetails || 'Keine Daten.'}

RISIKO-ALERTS:
${riskDetails || 'Keine Risiko-Patienten.'}

Erstelle das Executive Briefing.`;

      const result = await callClaude(prompt, systemPrompt, 1500);
      return { statusCode: 200, headers, body: JSON.stringify(result) };
    }

    if (action === 'ask') {
      if (!question) return { statusCode: 400, headers, body: JSON.stringify({ error: 'Keine Frage gestellt' }) };

      let patientInfo = '';
      patients.forEach(p => {
        patientInfo += `- ${p.name}: Score Ø${p.avg_score}, Trend ${p.score_trend || '?'}, Check-ins ${p.checkins_7d}, Pattern: ${p.last_pattern || '–'}, Stress Ø${p.stress_avg}, Schlaf Ø${p.sleep_avg}, Craving Ø${p.craving_avg}, Vermeidung Ø${p.avoidance_avg}, Outcomes: [${p.outcomes.join(',')}], Ziel: "${p.goal_text}"\n`;
      });

      const systemPrompt = `Du bist der KI-CHEFAGENT eines Therapeuten. Du hast Zugang zu allen Patientendaten.
Beantworte die Frage direkt, konkret und auf Deutsch. Kein Smalltalk.
Sei praezise und handlungsorientiert. Maximal 300 Woerter.`;

      const prompt = `ZUSAMMENFASSUNG:
- Patienten: ${summary.total_patients}, Aktive: ${summary.active_7d}
- Check-ins (7T): ${summary.total_checkins_7d}, Score Ø: ${summary.avg_score || '–'}
- Patterns: ${JSON.stringify(summary.pattern_frequency)}
- Outcomes: ${JSON.stringify(summary.outcome_distribution)}

PATIENTEN:
${patientInfo || 'Keine Daten.'}

FRAGE DES THERAPEUTEN:
${question}`;

      const result = await callClaude(prompt, systemPrompt, 800);
      return { statusCode: 200, headers, body: JSON.stringify(result) };
    }

    return { statusCode: 400, headers, body: JSON.stringify({ error: 'Unknown action. Use: get_data, briefing, ask' }) };
  } catch (err) {
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};
