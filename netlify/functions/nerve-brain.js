const { createClient } = require('@supabase/supabase-js');

const headers = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'Content-Type',
};

function getSupabase() {
  return createClient(
    process.env.SUPABASE_URL || 'https://pwdhxarvemcgkhhnvbng.supabase.co',
    process.env.SUPABASE_SERVICE_KEY
  );
}

async function callClaude(prompt, systemPrompt) {
  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) return { error: 'No API key configured' };
  const res = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: 'claude-haiku-4-5-20241022',
      max_tokens: 2048,
      system: systemPrompt || 'Du bist ein persoenlicher KI-Assistent. Antworte auf Deutsch. Sei praezise und handlungsorientiert.',
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
    const { action, ...params } = JSON.parse(event.body);
    const sb = getSupabase();

    // ── KNOWLEDGE VAULT ──
    if (action === 'knowledge_save') {
      const { title, content, category, tags } = params;
      if (!title || !content) return { statusCode: 400, headers, body: JSON.stringify({ error: 'Titel und Inhalt erforderlich' }) };
      const { data, error } = await sb.from('nerve_knowledge').insert({ title, content, category: category || 'general', tags: tags || [] }).select().single();
      if (error) return { statusCode: 500, headers, body: JSON.stringify({ error: error.message }) };
      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    if (action === 'knowledge_list') {
      const { category, search, limit: lim } = params;
      let q = sb.from('nerve_knowledge').select('*').order('created_at', { ascending: false }).limit(lim || 50);
      if (category && category !== 'all') q = q.eq('category', category);
      if (search) q = q.or(`title.ilike.%${search}%,content.ilike.%${search}%`);
      const { data, error } = await q;
      if (error) return { statusCode: 500, headers, body: JSON.stringify({ error: error.message }) };
      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    if (action === 'knowledge_delete') {
      const { id } = params;
      const { error } = await sb.from('nerve_knowledge').delete().eq('id', id);
      if (error) return { statusCode: 500, headers, body: JSON.stringify({ error: error.message }) };
      return { statusCode: 200, headers, body: JSON.stringify({ ok: true }) };
    }

    // ── DAILY 10 QUESTIONS ──
    if (action === 'daily_generate') {
      const today = new Date().toISOString().split('T')[0];
      const { data: existing } = await sb.from('nerve_daily_questions').select('id').eq('question_date', today);
      if (existing && existing.length >= 10) return { statusCode: 200, headers, body: JSON.stringify({ message: 'Fragen bereits generiert', date: today }) };

      // Gather context from knowledge vault
      const { data: recentKnowledge } = await sb.from('nerve_knowledge').select('title,category,content').order('created_at', { ascending: false }).limit(10);
      const { data: recentAnswers } = await sb.from('nerve_daily_questions').select('question,answer,category').order('created_at', { ascending: false }).limit(20);

      const knowledgeContext = (recentKnowledge || []).map(k => `[${k.category}] ${k.title}: ${k.content.substring(0, 200)}`).join('\n');
      const answerContext = (recentAnswers || []).filter(a => a.answer).map(a => `Q: ${a.question} → A: ${a.answer}`).join('\n');

      const prompt = `Generiere genau 10 taegliche Fragen fuer heute (${today}).
Die Fragen sollen:
1. Den Tag leichter und wertvoller machen
2. Konkrete Geldverdien-Moeglichkeiten aufzeigen
3. Auf physikalische Prozesse und Evolution Bezug nehmen
4. Bisheriges Wissen vertiefen und anwenden
5. Selbstkorrektur foerdern

Kontext aus dem Wissensspeicher:
${knowledgeContext || 'Noch kein Wissen gespeichert.'}

Letzte Antworten:
${answerContext || 'Noch keine Antworten.'}

Antworte als JSON-Array mit genau 10 Objekten: [{"question":"...","category":"..."}]
Kategorien: business, gesundheit, wissen, physik, evolution, therapie, kreativitaet, strategie, selbstreflexion, zukunft`;

      const ai = await callClaude(prompt, 'Du generierst taegliche Power-Fragen. Antworte NUR mit validem JSON.');
      let questions;
      try {
        const jsonMatch = ai.text.match(/\[[\s\S]*\]/);
        questions = JSON.parse(jsonMatch ? jsonMatch[0] : ai.text);
      } catch { return { statusCode: 500, headers, body: JSON.stringify({ error: 'KI-Antwort konnte nicht geparst werden', raw: ai.text }) }; }

      // Delete any partial existing questions for today
      await sb.from('nerve_daily_questions').delete().eq('question_date', today);

      const rows = questions.slice(0, 10).map((q, i) => ({
        question_date: today,
        question_number: i + 1,
        question: q.question,
        category: q.category || 'general',
      }));
      const { data, error } = await sb.from('nerve_daily_questions').insert(rows).select();
      if (error) return { statusCode: 500, headers, body: JSON.stringify({ error: error.message }) };
      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    if (action === 'daily_list') {
      const date = params.date || new Date().toISOString().split('T')[0];
      const { data, error } = await sb.from('nerve_daily_questions').select('*').eq('question_date', date).order('question_number');
      if (error) return { statusCode: 500, headers, body: JSON.stringify({ error: error.message }) };
      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    if (action === 'daily_answer') {
      const { id, answer } = params;
      if (!id || !answer) return { statusCode: 400, headers, body: JSON.stringify({ error: 'ID und Antwort erforderlich' }) };

      // Get the question for context
      const { data: q } = await sb.from('nerve_daily_questions').select('question,category').eq('id', id).single();

      // Generate AI insight based on the answer
      const ai = await callClaude(
        `Frage: ${q?.question}\nAntwort: ${answer}\n\nGib ein kurzes, handlungsorientiertes Insight (max 2 Saetze). Beziehe physikalische oder evolutionaere Prinzipien ein wenn moeglich.`,
        'Du bist ein strategischer Berater. Kurz, praegnant, wertvoll.'
      );

      const { data, error } = await sb.from('nerve_daily_questions').update({
        answer,
        answered_at: new Date().toISOString(),
        ai_insight: ai.text,
      }).eq('id', id).select().single();
      if (error) return { statusCode: 500, headers, body: JSON.stringify({ error: error.message }) };
      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    // ── SITUATION ANALYZER ──
    if (action === 'situation_analyze') {
      const { input_text, title, category } = params;
      if (!input_text) return { statusCode: 400, headers, body: JSON.stringify({ error: 'Text erforderlich' }) };

      // Save situation
      const { data: sit, error: sitErr } = await sb.from('nerve_situations').insert({
        title: title || 'Neue Analyse',
        input_text,
        category: category || 'general',
        status: 'researching',
      }).select().single();
      if (sitErr) return { statusCode: 500, headers, body: JSON.stringify({ error: sitErr.message }) };

      // Get relevant knowledge
      const { data: knowledge } = await sb.from('nerve_knowledge').select('title,content,category').limit(15);
      const knowledgeCtx = (knowledge || []).map(k => `[${k.category}] ${k.title}: ${k.content.substring(0, 300)}`).join('\n');

      // Step 1: Research
      const research = await callClaude(
        `Analysiere diesen Text/diese Situation:\n\n"${input_text}"\n\nRelevantes Wissen:\n${knowledgeCtx}\n\nErstelle eine Recherche-Zusammenfassung: Was sind die Kernpunkte? Welche physikalischen Prinzipien, evolutionaeren Muster oder historischen Parallelen sind relevant? Format als JSON: {"kernpunkte":["..."],"physik":"...","evolution":"...","historie":"...","chancen":["..."]}`,
        'Du bist ein Forscher mit Expertise in Physik, Evolution und Geschichte. Antworte NUR mit validem JSON.'
      );

      let researchData;
      try {
        const m = research.text.match(/\{[\s\S]*\}/);
        researchData = JSON.parse(m ? m[0] : research.text);
      } catch { researchData = { raw: research.text }; }

      // Step 2: Plan
      await sb.from('nerve_situations').update({ research: researchData, status: 'planning' }).eq('id', sit.id);

      const plan = await callClaude(
        `Basierend auf dieser Recherche:\n${JSON.stringify(researchData)}\n\nOriginaltext: "${input_text}"\n\nErstelle einen konkreten Aktionsplan. Format als JSON-Array: [{"schritt":1,"aktion":"...","zeitrahmen":"...","erwartetes_ergebnis":"..."}]`,
        'Du bist ein strategischer Planer. Erstelle konkrete, umsetzbare Plaene. NUR JSON.'
      );

      let planData;
      try {
        const m = plan.text.match(/\[[\s\S]*\]/);
        planData = JSON.parse(m ? m[0] : plan.text);
      } catch { planData = [{ raw: plan.text }]; }

      // Step 3: Final analysis and recommendation
      const final = await callClaude(
        `Situation: "${input_text}"\nRecherche: ${JSON.stringify(researchData)}\nPlan: ${JSON.stringify(planData)}\nGesamtes Wissen: ${knowledgeCtx}\n\nGib eine finale Auswertung: Was ist die beste Antwort/Loesung? Fasse ALLES zusammen — Recherche, Plan, Wissen. Schreibe als ob du die kollektive Intelligenz aller gespeicherten Informationen bist. Max 500 Woerter.`,
        'Du bist die Zusammenfassung aller gesammelten Erkenntnisse. Deine Antwort ist die destillierte Weisheit aus Physik, Evolution, Therapie und Praxis.'
      );

      const { data: updated, error: updErr } = await sb.from('nerve_situations').update({
        plan: planData,
        analysis: final.text,
        recommendation: final.text,
        status: 'complete',
      }).eq('id', sit.id).select().single();
      if (updErr) return { statusCode: 500, headers, body: JSON.stringify({ error: updErr.message }) };
      return { statusCode: 200, headers, body: JSON.stringify(updated) };
    }

    if (action === 'situation_list') {
      const { data, error } = await sb.from('nerve_situations').select('id,created_at,title,category,status,input_text').order('created_at', { ascending: false }).limit(30);
      if (error) return { statusCode: 500, headers, body: JSON.stringify({ error: error.message }) };
      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    if (action === 'situation_get') {
      const { id } = params;
      const { data, error } = await sb.from('nerve_situations').select('*').eq('id', id).single();
      if (error) return { statusCode: 500, headers, body: JSON.stringify({ error: error.message }) };
      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    // ── PATIENT INTELLIGENCE ──
    if (action === 'patient_analyze') {
      const { patient_ref, data: patientData } = params;
      if (!patient_ref) return { statusCode: 400, headers, body: JSON.stringify({ error: 'Patient-Referenz erforderlich' }) };

      const ai = await callClaude(
        `Analysiere diese Patientendaten (ethisch, zur Behandlungsoptimierung):\nPatient: ${patient_ref}\nDaten: ${JSON.stringify(patientData)}\n\nFinde Muster, Zusammenhaenge, versteckte Signale. Denke wie ein analytischer Geist, der Daten durchdringt — nicht um zu schaden, sondern um Heilung zu optimieren. Beziehe physikalische Koerperprozesse und evolutionaere Schutzmechanismen ein.\n\nFormat: {"muster":["..."],"versteckte_signale":["..."],"physik_bezug":"...","evolution_bezug":"...","empfehlung":"...","confidence":0.0-1.0}`,
        'Du bist ein ethischer Datenanalyst fuer therapeutische Zwecke. Finde Muster die dem Therapeuten helfen. NUR JSON.'
      );

      let insight;
      try {
        const m = ai.text.match(/\{[\s\S]*\}/);
        insight = JSON.parse(m ? m[0] : ai.text);
      } catch { insight = { raw: ai.text }; }

      const { data, error } = await sb.from('nerve_patient_insights').insert({
        patient_ref,
        insight_type: 'pattern_analysis',
        pattern_data: insight,
        recommendation: insight.empfehlung || '',
        confidence: insight.confidence || 0.5,
      }).select().single();
      if (error) return { statusCode: 500, headers, body: JSON.stringify({ error: error.message }) };
      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    if (action === 'patient_list') {
      const { data, error } = await sb.from('nerve_patient_insights').select('*').order('created_at', { ascending: false }).limit(30);
      if (error) return { statusCode: 500, headers, body: JSON.stringify({ error: error.message }) };
      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    // ── STATS ──
    if (action === 'stats') {
      const [k, q, s, r, p] = await Promise.all([
        sb.from('nerve_knowledge').select('id', { count: 'exact', head: true }),
        sb.from('nerve_daily_questions').select('id', { count: 'exact', head: true }),
        sb.from('nerve_situations').select('id', { count: 'exact', head: true }),
        sb.from('nerve_research').select('id', { count: 'exact', head: true }),
        sb.from('nerve_patient_insights').select('id', { count: 'exact', head: true }),
      ]);
      return {
        statusCode: 200, headers,
        body: JSON.stringify({
          knowledge: k.count || 0,
          questions: q.count || 0,
          situations: s.count || 0,
          research: r.count || 0,
          patients: p.count || 0,
        }),
      };
    }

    return { statusCode: 400, headers, body: JSON.stringify({ error: 'Unbekannte Aktion: ' + action }) };
  } catch (err) {
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};
