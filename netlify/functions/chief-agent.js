// ============================================================
// KI-CHEFAGENT — Executive Intelligence for Therapists
// Aggregates patient data from Supabase + Claude AI analysis
// ============================================================

const { createClient } = require('@supabase/supabase-js');

const supabase = createClient(
  process.env.SUPABASE_URL || 'https://pwdhxarvemcgkhhnvbng.supabase.co',
  process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_SERVICE_KEY || ''
);

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

function avg(arr) {
  if (!arr.length) return null;
  return Math.round(arr.reduce((a, b) => a + b, 0) / arr.length * 10) / 10;
}

async function collectPatientData() {
  // Get all patients
  const { data: patients, error: pErr } = await supabase
    .from('patients')
    .select('id, name, phone, email, created_at, therapist_id')
    .order('name');

  if (pErr || !patients || !patients.length) {
    return { patients: [], risk_patients: [], summary: { total_patients: 0, active_7d: 0, total_checkins_7d: 0, avg_score: null, pattern_frequency: {}, outcome_distribution: { better: 0, same: 0, worse: 0 }, risk_count: 0 } };
  }

  const now = new Date();
  const weekAgo = new Date(now - 7 * 24 * 60 * 60 * 1000).toISOString();

  // Get recent check-ins (last 7 days)
  const { data: recentCheckins } = await supabase
    .from('checkins')
    .select('id, patient_id, daily_state, stress, sleep, body, craving, avoidance, pattern_code, pattern_label, action_code, action_label, score, risk_level, local_day, created_at, mental_text, goal_text')
    .gte('created_at', weekAgo)
    .order('created_at', { ascending: false });

  const checkins = recentCheckins || [];

  // Get outcomes (last 7 days)
  const { data: recentOutcomes } = await supabase
    .from('outcomes')
    .select('id, patient_id, checkin_id, rating, outcome_note, created_at')
    .gte('created_at', weekAgo);

  const outcomes = recentOutcomes || [];

  // Aggregate per patient
  const patientData = [];
  const allPatterns = [];
  const allScores = [];
  const allOutcomes = [];
  const riskPatients = [];

  for (const p of patients) {
    const pCheckins = checkins.filter(c => c.patient_id === p.id);
    const pOutcomes = outcomes.filter(o => o.patient_id === p.id);
    const scores = pCheckins.map(c => c.score).filter(s => s != null);
    const patterns = pCheckins.map(c => c.pattern_code).filter(Boolean);

    const avgScore = avg(scores);
    const avgStress = avg(pCheckins.map(c => c.stress).filter(v => v != null));
    const avgSleep = avg(pCheckins.map(c => c.sleep).filter(v => v != null));
    const avgCraving = avg(pCheckins.map(c => c.craving).filter(v => v != null));
    const avgAvoidance = avg(pCheckins.map(c => c.avoidance).filter(v => v != null));

    // Trend
    let scoreTrend = null;
    if (scores.length >= 4) {
      const half = Math.floor(scores.length / 2);
      const older = avg(scores.slice(half));
      const newer = avg(scores.slice(0, half));
      scoreTrend = newer > older + 3 ? 'steigend' : newer < older - 3 ? 'fallend' : 'stabil';
    }

    // Days since last
    let daysSince = null;
    if (pCheckins.length) {
      daysSince = Math.floor((now - new Date(pCheckins[0].created_at)) / (24 * 60 * 60 * 1000));
    }

    const last = pCheckins[0] || null;
    const outcomeRatings = pOutcomes.map(o => o.rating);

    const entry = {
      name: p.name, phone: p.phone,
      checkins_7d: pCheckins.length,
      avg_score: avgScore, score_trend: scoreTrend,
      last_score: last?.score, last_risk: last?.risk_level,
      last_pattern: last?.pattern_label, last_day: last?.local_day,
      days_since: daysSince,
      patterns, outcomes: outcomeRatings,
      stress_avg: avgStress, sleep_avg: avgSleep,
      craving_avg: avgCraving, avoidance_avg: avgAvoidance,
      mental_text: last?.mental_text || '', goal_text: last?.goal_text || '',
    };
    patientData.push(entry);

    allPatterns.push(...patterns);
    allScores.push(...scores);
    allOutcomes.push(...outcomeRatings);

    if ((last && last.risk_level === 'high') || (daysSince != null && daysSince >= 3)) {
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
      total_checkins_7d: checkins.length,
      avg_score: avg(allScores),
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
    const { summary, patients, risk_patients: riskPatients } = data;

    // ── GET RAW DATA (no AI) ──
    if (action === 'get_data') {
      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    // ── EXECUTIVE BRIEFING ──
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

      const prompt = `DATEN:\n- Patienten gesamt: ${summary.total_patients}\n- Aktive (7 Tage): ${summary.active_7d}\n- Check-ins (7 Tage): ${summary.total_checkins_7d}\n- Score Ø: ${summary.avg_score || '–'}\n- Patterns: ${JSON.stringify(summary.pattern_frequency)}\n- Outcomes: ${JSON.stringify(summary.outcome_distribution)}\n- Risiko-Patienten: ${summary.risk_count}\n\nPATIENTEN-DETAILS:\n${patientDetails || 'Keine Daten.'}\n\nRISIKO-ALERTS:\n${riskDetails || 'Keine Risiko-Patienten.'}\n\nErstelle das Executive Briefing.`;

      const result = await callClaude(prompt, systemPrompt, 1500);
      return { statusCode: 200, headers, body: JSON.stringify(result) };
    }

    // ── ASK QUESTION ──
    if (action === 'ask') {
      if (!question) return { statusCode: 400, headers, body: JSON.stringify({ error: 'Keine Frage gestellt' }) };

      let patientInfo = '';
      patients.forEach(p => {
        patientInfo += `- ${p.name}: Score Ø${p.avg_score}, Trend ${p.score_trend || '?'}, Check-ins ${p.checkins_7d}, Pattern: ${p.last_pattern || '–'}, Stress Ø${p.stress_avg}, Schlaf Ø${p.sleep_avg}, Craving Ø${p.craving_avg}, Vermeidung Ø${p.avoidance_avg}, Outcomes: [${p.outcomes.join(',')}], Ziel: "${p.goal_text}"\n`;
      });

      const systemPrompt = `Du bist der KI-CHEFAGENT eines Therapeuten. Du hast Zugang zu allen Patientendaten.
Beantworte die Frage direkt, konkret und auf Deutsch. Kein Smalltalk.
Sei praezise und handlungsorientiert. Maximal 300 Woerter.`;

      const prompt = `ZUSAMMENFASSUNG:\n- Patienten: ${summary.total_patients}, Aktive: ${summary.active_7d}\n- Check-ins (7T): ${summary.total_checkins_7d}, Score Ø: ${summary.avg_score || '–'}\n- Patterns: ${JSON.stringify(summary.pattern_frequency)}\n- Outcomes: ${JSON.stringify(summary.outcome_distribution)}\n\nPATIENTEN:\n${patientInfo || 'Keine Daten.'}\n\nFRAGE DES THERAPEUTEN:\n${question}`;

      const result = await callClaude(prompt, systemPrompt, 800);
      return { statusCode: 200, headers, body: JSON.stringify(result) };
    }

    return { statusCode: 400, headers, body: JSON.stringify({ error: 'Unknown action. Use: get_data, briefing, ask' }) };
  } catch (err) {
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};
