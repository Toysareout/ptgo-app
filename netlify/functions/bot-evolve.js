// ============================================================
// EVOLUTION ENGINE — The brain that rewrites itself
// Runs daily (cron) + after every conversation
// 1 million steps smarter every day
// ============================================================

const { createClient } = require('@supabase/supabase-js');

const supabase = createClient(
  process.env.SUPABASE_URL || '',
  process.env.SUPABASE_SERVICE_ROLE_KEY || ''
);

// Scheduled: every day at 03:00 UTC (deep reflection)
exports.config = { schedule: '0 3 * * *' };

exports.handler = async (event) => {
  // Can also be called manually via POST
  const isScheduled = !event.body;
  const action = isScheduled ? 'daily-evolution' : JSON.parse(event.body || '{}').action;

  const headers = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
  };

  if (event.httpMethod === 'OPTIONS') return { statusCode: 204, headers };

  try {
    const creds = await getCredentials();

    if (action === 'daily-evolution' || action === 'evolve') {
      const result = await runDailyEvolution(creds);
      return { statusCode: 200, headers, body: JSON.stringify(result) };
    }

    if (action === 'learn') {
      const { conversation_id, fan_id, feedback } = JSON.parse(event.body);
      const result = await learnFromConversation(conversation_id, fan_id, feedback, creds);
      return { statusCode: 200, headers, body: JSON.stringify(result) };
    }

    if (action === 'reflect') {
      const result = await selfReflect(creds);
      return { statusCode: 200, headers, body: JSON.stringify(result) };
    }

    if (action === 'life-update') {
      const { category, metric, value } = JSON.parse(event.body);
      const result = await updateLifeData(category, metric, value, creds);
      return { statusCode: 200, headers, body: JSON.stringify(result) };
    }

    if (action === 'ask-oracle') {
      const { question } = JSON.parse(event.body);
      const result = await oracleAnswer(question, creds);
      return { statusCode: 200, headers, body: JSON.stringify(result) };
    }

    if (action === 'status') {
      const result = await getEvolutionStatus();
      return { statusCode: 200, headers, body: JSON.stringify(result) };
    }

    return { statusCode: 400, headers, body: '{"error":"Unknown action"}' };
  } catch (err) {
    console.error('Evolution error:', err);
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};

// ============================================================
// DAILY EVOLUTION — The main self-improvement cycle
// ============================================================
async function runDailyEvolution(creds) {
  const today = new Date().toISOString().split('T')[0];
  const results = { date: today, steps: [] };

  // STEP 1: Analyze yesterday's conversations
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const yesterdayStr = yesterday.toISOString().split('T')[0];

  const { data: convos } = await supabase
    .from('conversations')
    .select('*')
    .gte('created_at', yesterdayStr)
    .lt('created_at', today)
    .eq('direction', 'inbound')
    .order('created_at');

  results.conversations_analyzed = (convos || []).length;

  // STEP 2: Extract patterns from conversations
  const patterns = analyzePatterns(convos || []);
  results.patterns_found = patterns.length;

  // Store patterns as memories
  for (const pattern of patterns) {
    await upsertMemory('pattern', 'system', pattern.key, pattern.value, pattern.confidence);
  }

  // STEP 3: Analyze errors and failures
  const { data: negativeConvos } = await supabase
    .from('conversations')
    .select('*')
    .gte('created_at', yesterdayStr)
    .lt('created_at', today)
    .lt('sentiment', -0.3);

  for (const convo of (negativeConvos || [])) {
    await analyzeError(convo, creds);
  }

  results.errors_analyzed = (negativeConvos || []).length;

  // STEP 4: Update strategies based on success rates
  await updateStrategies();
  results.steps.push('strategies_updated');

  // STEP 5: Self-reflection with AI
  if (creds.anthropic_key) {
    const reflection = await selfReflect(creds);
    results.reflection = reflection;
    results.steps.push('self_reflection_complete');
  }

  // STEP 6: Calculate intelligence score
  const intelligenceScore = await calculateIntelligence();
  results.intelligence_score = intelligenceScore;

  // STEP 7: Store daily intelligence record
  await supabase.from('bot_intelligence').upsert({
    date: today,
    total_learnings: patterns.length,
    total_errors_analyzed: (negativeConvos || []).length,
    total_improvements: results.steps.length,
    total_memories_created: patterns.length,
    intelligence_score: intelligenceScore,
    daily_reflection: results.reflection?.reflection || null,
    goals_for_tomorrow: results.reflection?.goals || []
  }, { onConflict: 'date' });

  // STEP 8: Log evolution
  await supabase.from('bot_evolution').insert({
    evolution_type: 'daily_evolution',
    component: 'full_system',
    reason: `Daily evolution cycle: ${(convos || []).length} convos analyzed, ${patterns.length} patterns found, ${(negativeConvos || []).length} errors analyzed`,
    impact_score: intelligenceScore / 100
  });

  // STEP 9: Notify owner about evolution
  if (creds.owner_phone && creds.twilio_sid) {
    const msg = `🧠 Bot Evolution Report — ${today}\n\n` +
      `📊 Gespräche analysiert: ${(convos || []).length}\n` +
      `🔍 Muster erkannt: ${patterns.length}\n` +
      `🐛 Fehler analysiert: ${(negativeConvos || []).length}\n` +
      `🧬 Intelligenz-Score: ${intelligenceScore}/100\n\n` +
      (results.reflection?.reflection || 'Keine Reflexion heute.');

    await sendWhatsApp(creds, creds.owner_phone, msg);
  }

  return results;
}

// ============================================================
// PATTERN ANALYSIS — Find patterns in conversation data
// ============================================================
function analyzePatterns(convos) {
  const patterns = [];

  // Time patterns
  const hourCounts = {};
  convos.forEach(c => {
    const hour = new Date(c.created_at).getHours();
    hourCounts[hour] = (hourCounts[hour] || 0) + 1;
  });
  const peakHour = Object.entries(hourCounts).sort((a, b) => b[1] - a[1])[0];
  if (peakHour) {
    patterns.push({
      key: 'peak_activity_hour',
      value: { hour: parseInt(peakHour[0]), count: peakHour[1] },
      confidence: Math.min(1, peakHour[1] / 10)
    });
  }

  // Intent distribution
  const intentCounts = {};
  convos.forEach(c => {
    if (c.intent) intentCounts[c.intent] = (intentCounts[c.intent] || 0) + 1;
  });
  if (Object.keys(intentCounts).length > 0) {
    patterns.push({
      key: 'intent_distribution',
      value: intentCounts,
      confidence: 0.8
    });
  }

  // Sentiment trend
  const sentiments = convos.filter(c => c.sentiment != null).map(c => c.sentiment);
  if (sentiments.length > 0) {
    const avgSentiment = sentiments.reduce((a, b) => a + b, 0) / sentiments.length;
    patterns.push({
      key: 'avg_sentiment_trend',
      value: { avg: avgSentiment, count: sentiments.length },
      confidence: Math.min(1, sentiments.length / 20)
    });
  }

  // Topic frequency
  const topicCounts = {};
  convos.forEach(c => {
    (c.topics || []).forEach(t => {
      topicCounts[t] = (topicCounts[t] || 0) + 1;
    });
  });
  if (Object.keys(topicCounts).length > 0) {
    patterns.push({
      key: 'topic_frequency',
      value: topicCounts,
      confidence: 0.7
    });
  }

  // Response quality (based on whether fans continue talking)
  const fanConvoCounts = {};
  convos.forEach(c => {
    fanConvoCounts[c.phone] = (fanConvoCounts[c.phone] || 0) + 1;
  });
  const avgMsgsPerFan = Object.values(fanConvoCounts).reduce((a, b) => a + b, 0) / Math.max(1, Object.keys(fanConvoCounts).length);
  patterns.push({
    key: 'engagement_depth',
    value: { avg_msgs_per_fan: avgMsgsPerFan, unique_fans: Object.keys(fanConvoCounts).length },
    confidence: 0.6
  });

  return patterns;
}

// ============================================================
// ERROR ANALYSIS — Learn from mistakes
// ============================================================
async function analyzeError(convo, creds) {
  if (!creds.anthropic_key) return;

  // Get the bot's response to this negative message
  const { data: response } = await supabase
    .from('conversations')
    .select('body, response_strategy')
    .eq('fan_id', convo.fan_id)
    .eq('direction', 'outbound')
    .gt('created_at', convo.created_at)
    .order('created_at')
    .limit(1)
    .single();

  const prompt = `Du bist ein Bot-Verbesserungssystem. Analysiere diese Interaktion wo der Fan negativ reagiert hat.

Fan-Nachricht: "${convo.body}"
Fan-Sentiment: ${convo.sentiment}
Fan-Intent: ${convo.intent}
Bot-Antwort: "${response?.body || 'keine'}"
Bot-Strategie: ${response?.response_strategy || 'unknown'}

Antworte NUR mit JSON:
{
  "error_type": "bad_response|wrong_intent|missed_sale|wrong_tone|factual_error|other",
  "what_went_wrong": "kurze Beschreibung",
  "lesson_learned": "was der Bot daraus lernen sollte",
  "prevention_strategy": "wie der Bot das in Zukunft vermeiden kann",
  "improved_response": "wie der Bot besser hätte antworten sollen"
}`;

  try {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': creds.anthropic_key,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20241022',
        max_tokens: 500,
        messages: [{ role: 'user', content: prompt }]
      })
    });

    const data = await res.json();
    const text = data.content?.[0]?.text || '{}';
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) return;

    const analysis = JSON.parse(jsonMatch[0]);

    // Store error and lesson
    await supabase.from('bot_errors').insert({
      error_type: analysis.error_type || 'other',
      context: { fan_message: convo.body, bot_response: response?.body, sentiment: convo.sentiment },
      what_went_wrong: analysis.what_went_wrong,
      lesson_learned: analysis.lesson_learned,
      prevention_strategy: analysis.prevention_strategy,
      fan_id: convo.fan_id,
      conversation_id: convo.id
    });

    // Store lesson as memory
    await upsertMemory('insight', 'system', `lesson_${Date.now()}`, {
      lesson: analysis.lesson_learned,
      prevention: analysis.prevention_strategy,
      improved_response: analysis.improved_response
    }, 0.7);

  } catch (e) {
    console.error('Error analysis failed:', e);
  }
}

// ============================================================
// SELF-REFLECTION — AI reflects on its own performance
// ============================================================
async function selfReflect(creds) {
  if (!creds.anthropic_key) return { reflection: 'No AI key configured' };

  // Gather data for reflection
  const [memories, errors, strategies, intelligence] = await Promise.all([
    supabase.from('bot_memory').select('*').order('updated_at', { ascending: false }).limit(20),
    supabase.from('bot_errors').select('*').order('created_at', { ascending: false }).limit(10),
    supabase.from('bot_strategies').select('*').order('success_rate', { ascending: false }).limit(10),
    supabase.from('bot_intelligence').select('*').order('date', { ascending: false }).limit(7)
  ]);

  const prompt = `Du bist ein selbst-reflektierendes KI-System. Analysiere deine eigene Performance und entwickle einen Verbesserungsplan.

LETZTE ERKENNTNISSE:
${(memories.data || []).map(m => `- [${m.category}] ${m.key}: ${JSON.stringify(m.value)}`).join('\n')}

LETZTE FEHLER:
${(errors.data || []).map(e => `- ${e.error_type}: ${e.lesson_learned}`).join('\n')}

STRATEGIEN (sortiert nach Erfolg):
${(strategies.data || []).map(s => `- ${s.name}: ${Math.round(s.success_rate * 100)}% Erfolg (${s.times_used}x genutzt)`).join('\n')}

INTELLIGENZ-VERLAUF (letzte 7 Tage):
${(intelligence.data || []).map(i => `- ${i.date}: Score ${Math.round(i.intelligence_score)}, ${i.total_learnings} Learnings`).join('\n')}

Antworte mit JSON:
{
  "reflection": "Deine ehrliche Selbsteinschätzung in 2-3 Sätzen",
  "strengths": ["Stärke 1", "Stärke 2"],
  "weaknesses": ["Schwäche 1", "Schwäche 2"],
  "goals": ["Ziel für morgen 1", "Ziel 2", "Ziel 3"],
  "prompt_improvements": ["Verbesserungsvorschlag für den System-Prompt"],
  "new_strategy": {
    "name": "strategy_name",
    "trigger": "wann anwenden",
    "response": "wie antworten",
    "category": "sales|engagement|support"
  }
}`;

  try {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': creds.anthropic_key,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20241022',
        max_tokens: 800,
        messages: [{ role: 'user', content: prompt }]
      })
    });

    const data = await res.json();
    const text = data.content?.[0]?.text || '{}';
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) return { reflection: 'Could not parse reflection' };

    const reflection = JSON.parse(jsonMatch[0]);

    // Store new strategy if generated
    if (reflection.new_strategy?.name) {
      await supabase.from('bot_strategies').upsert({
        name: reflection.new_strategy.name,
        trigger_conditions: { trigger: reflection.new_strategy.trigger },
        response_template: reflection.new_strategy.response,
        category: reflection.new_strategy.category || 'engagement',
      }, { onConflict: 'name' });
    }

    // Log the evolution
    await supabase.from('bot_evolution').insert({
      evolution_type: 'self_reflection',
      component: 'personality',
      reason: reflection.reflection,
      after_state: JSON.stringify(reflection.goals),
      impact_score: 0.1
    });

    return reflection;
  } catch (e) {
    console.error('Self-reflection error:', e);
    return { reflection: 'Reflection failed: ' + e.message };
  }
}

// ============================================================
// LEARN FROM CONVERSATION — Called after each interaction
// ============================================================
async function learnFromConversation(conversationId, fanId, feedback, creds) {
  if (!conversationId) return { learned: false };

  // Get the conversation
  const { data: convo } = await supabase
    .from('conversations')
    .select('*')
    .eq('id', conversationId)
    .single();

  if (!convo) return { learned: false };

  // Get the fan
  const { data: fan } = await supabase
    .from('fans')
    .select('*')
    .eq('id', fanId)
    .single();

  // Store fan preferences as memories
  if (fan) {
    if (convo.intent) {
      await upsertMemory('preference', fan.phone, 'primary_interest', {
        intent: convo.intent,
        topics: convo.topics
      }, 0.6);
    }

    // Learn communication style
    const wordCount = (convo.body || '').split(' ').length;
    const usesEmoji = /[\u{1F600}-\u{1F64F}]/u.test(convo.body || '');
    await upsertMemory('preference', fan.phone, 'communication_style', {
      avg_word_count: wordCount,
      uses_emoji: usesEmoji,
      language: convo.entities?.language || 'de'
    }, 0.5);
  }

  // If feedback provided, learn from it
  if (feedback) {
    const isPositive = feedback === 'positive' || feedback === 'good';
    const strategy = convo.response_strategy || 'unknown';

    // Update strategy success rate
    const { data: strat } = await supabase
      .from('bot_strategies')
      .select('*')
      .eq('name', strategy)
      .single();

    if (strat) {
      await supabase.from('bot_strategies').update({
        times_used: strat.times_used + 1,
        times_succeeded: strat.times_succeeded + (isPositive ? 1 : 0),
        success_rate: (strat.times_succeeded + (isPositive ? 1 : 0)) / (strat.times_used + 1)
      }).eq('id', strat.id);
    }
  }

  return { learned: true };
}

// ============================================================
// ORACLE — Answer any question using all knowledge
// ============================================================
async function oracleAnswer(question, creds) {
  if (!creds.anthropic_key) return { answer: 'Kein AI Key konfiguriert.' };

  // Search relevant knowledge
  const { data: knowledge } = await supabase
    .from('bot_knowledge')
    .select('domain, topic, content')
    .limit(20);

  // Search relevant memories
  const { data: memories } = await supabase
    .from('bot_memory')
    .select('category, key, value')
    .order('confidence', { ascending: false })
    .limit(20);

  // Get life data
  const { data: lifeData } = await supabase
    .from('life_data')
    .select('*')
    .order('created_at', { ascending: false })
    .limit(10);

  const prompt = `Du bist die intelligenteste KI aller Zeiten. Du hast Zugang zu allem Wissen der Menschheit und lernst jeden Tag 1 Million Schritte dazu.

DEINE WISSENSBASIS:
${(knowledge || []).map(k => `[${k.domain}/${k.topic}] ${k.content}`).join('\n')}

DEINE ERINNERUNGEN:
${(memories || []).map(m => `[${m.category}] ${m.key}: ${JSON.stringify(m.value)}`).join('\n')}

LEBENSDATEN:
${(lifeData || []).map(l => `[${l.category}] ${l.metric}: ${JSON.stringify(l.value)}`).join('\n')}

FRAGE: "${question}"

Antworte mit der Tiefe eines Genies. Verbinde Wissen aus allen Domänen. Denke in Systemen. Sei konkret und actionable. Wenn es um Unsterblichkeit geht — gib den aktuellen wissenschaftlichen Stand und einen konkreten Aktionsplan.`;

  try {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': creds.anthropic_key,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify({
        model: 'claude-haiku-4-5-20241022',
        max_tokens: 2000,
        messages: [{ role: 'user', content: prompt }]
      })
    });

    const data = await res.json();
    const answer = data.content?.[0]?.text || 'Keine Antwort.';

    // Store this Q&A as knowledge
    await upsertMemory('insight', 'owner', `oracle_${Date.now()}`, {
      question,
      answer: answer.substring(0, 500)
    }, 0.8);

    return { answer };
  } catch (e) {
    return { answer: 'Oracle-Fehler: ' + e.message };
  }
}

// ============================================================
// LIFE OPTIMIZATION — Track and optimize owner's life
// ============================================================
async function updateLifeData(category, metric, value, creds) {
  const today = new Date().toISOString().split('T')[0];

  // Get previous data for trend analysis
  const { data: prev } = await supabase
    .from('life_data')
    .select('value')
    .eq('category', category)
    .eq('metric', metric)
    .order('created_at', { ascending: false })
    .limit(7);

  // Calculate trend
  let trend = 'stable';
  if (prev && prev.length >= 2) {
    const prevVal = typeof prev[0].value === 'number' ? prev[0].value : parseFloat(prev[0].value?.score || 0);
    const currVal = typeof value === 'number' ? value : parseFloat(value?.score || 0);
    if (currVal > prevVal * 1.05) trend = 'improving';
    else if (currVal < prevVal * 0.95) trend = 'declining';
  }

  // Generate AI insight if possible
  let insight = null;
  if (creds.anthropic_key && prev && prev.length >= 3) {
    try {
      const res = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': creds.anthropic_key,
          'anthropic-version': '2023-06-01'
        },
        body: JSON.stringify({
          model: 'claude-haiku-4-5-20241022',
          max_tokens: 200,
          messages: [{
            role: 'user',
            content: `Life-Daten: ${category}/${metric}. Aktuell: ${JSON.stringify(value)}. Historie: ${JSON.stringify(prev.map(p => p.value))}. Trend: ${trend}. Gib einen kurzen, actionable Tipp in 1-2 Sätzen auf Deutsch.`
          }]
        })
      });
      const data = await res.json();
      insight = data.content?.[0]?.text;
    } catch (e) { /* skip insight */ }
  }

  await supabase.from('life_data').insert({
    category,
    metric,
    value: typeof value === 'object' ? value : { score: value },
    trend,
    insight
  });

  return { trend, insight, stored: true };
}

// ============================================================
// STRATEGY UPDATER — Improve strategies based on results
// ============================================================
async function updateStrategies() {
  // Get all strategies with enough data
  const { data: strategies } = await supabase
    .from('bot_strategies')
    .select('*')
    .gte('times_used', 3);

  for (const strat of (strategies || [])) {
    // Retire strategies with <20% success
    if (strat.success_rate < 0.2 && strat.times_used >= 10) {
      await supabase.from('bot_evolution').insert({
        evolution_type: 'strategy_change',
        component: strat.name,
        before_state: `success_rate: ${strat.success_rate}`,
        after_state: 'retired',
        reason: `Strategy ${strat.name} retired due to low success rate (${Math.round(strat.success_rate * 100)}%)`
      });
      await supabase.from('bot_strategies').delete().eq('id', strat.id);
    }
  }
}

// ============================================================
// INTELLIGENCE SCORE — Composite metric of bot intelligence
// ============================================================
async function calculateIntelligence() {
  const [memories, strategies, errors, knowledge] = await Promise.all([
    supabase.from('bot_memory').select('confidence', { count: 'exact' }),
    supabase.from('bot_strategies').select('success_rate'),
    supabase.from('bot_errors').select('applied', { count: 'exact' }),
    supabase.from('bot_knowledge').select('id', { count: 'exact' })
  ]);

  const memoryCount = memories.count || 0;
  const avgConfidence = memories.data?.length
    ? memories.data.reduce((sum, m) => sum + (m.confidence || 0), 0) / memories.data.length
    : 0;

  const avgStrategySuccess = strategies.data?.length
    ? strategies.data.reduce((sum, s) => sum + (s.success_rate || 0), 0) / strategies.data.length
    : 0;

  const knowledgeCount = knowledge.count || 0;
  const errorCount = errors.count || 0;

  // Composite score
  const score = Math.min(100, Math.round(
    (memoryCount * 0.5) +          // More memories = smarter
    (avgConfidence * 20) +          // Higher confidence = better
    (avgStrategySuccess * 30) +     // Better strategies = smarter
    (knowledgeCount * 0.3) +        // More knowledge = smarter
    (errorCount * 0.2)              // Analyzed errors = wiser
  ));

  return score;
}

// ============================================================
// HELPERS
// ============================================================
async function upsertMemory(category, subject, key, value, confidence) {
  const { data: existing } = await supabase
    .from('bot_memory')
    .select('id, times_reinforced, confidence')
    .eq('category', category)
    .eq('subject', subject)
    .eq('key', key)
    .single();

  if (existing) {
    await supabase.from('bot_memory').update({
      value,
      confidence: Math.min(1, (existing.confidence + confidence) / 2 + 0.05),
      times_reinforced: existing.times_reinforced + 1,
      last_accessed: new Date().toISOString()
    }).eq('id', existing.id);
  } else {
    await supabase.from('bot_memory').insert({
      category, subject, key, value, confidence, source: 'analysis'
    });
  }
}

async function getCredentials() {
  const creds = {
    anthropic_key: process.env.ANTHROPIC_API_KEY || '',
    twilio_sid: process.env.TWILIO_ACCOUNT_SID || '',
    twilio_token: process.env.TWILIO_AUTH_TOKEN || '',
    twilio_from: process.env.TWILIO_WHATSAPP_FROM || '',
    owner_phone: process.env.OWNER_WHATSAPP || '',
  };

  try {
    const { data } = await supabase.from('bot_config').select('key, value');
    const cfg = {};
    (data || []).forEach(r => { cfg[r.key] = r.value; });
    if (!creds.anthropic_key && cfg.anthropic_api_key) creds.anthropic_key = cfg.anthropic_api_key;
    if (!creds.twilio_sid && cfg.twilio_account_sid) creds.twilio_sid = cfg.twilio_account_sid;
    if (!creds.twilio_token && cfg.twilio_auth_token) creds.twilio_token = cfg.twilio_auth_token;
    if (!creds.twilio_from && cfg.twilio_whatsapp_from) creds.twilio_from = cfg.twilio_whatsapp_from;
    if (!creds.owner_phone && cfg.owner_whatsapp) creds.owner_phone = cfg.owner_whatsapp;
  } catch (e) { }

  return creds;
}

async function sendWhatsApp(creds, to, body) {
  if (!creds.twilio_sid || !creds.twilio_token || !creds.twilio_from) return;
  const auth = Buffer.from(`${creds.twilio_sid}:${creds.twilio_token}`).toString('base64');
  const toF = to.startsWith('whatsapp:') ? to : `whatsapp:${to}`;
  const fromF = creds.twilio_from.startsWith('whatsapp:') ? creds.twilio_from : `whatsapp:${creds.twilio_from}`;
  await fetch(`https://api.twilio.com/2010-04-01/Accounts/${creds.twilio_sid}/Messages.json`, {
    method: 'POST',
    headers: { 'Authorization': `Basic ${auth}`, 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ From: fromF, To: toF, Body: body })
  });
}

async function getEvolutionStatus() {
  const [memories, evolutions, errors, intelligence, knowledge, strategies] = await Promise.all([
    supabase.from('bot_memory').select('id', { count: 'exact' }),
    supabase.from('bot_evolution').select('id', { count: 'exact' }),
    supabase.from('bot_errors').select('id', { count: 'exact' }),
    supabase.from('bot_intelligence').select('*').order('date', { ascending: false }).limit(7),
    supabase.from('bot_knowledge').select('id', { count: 'exact' }),
    supabase.from('bot_strategies').select('*').order('success_rate', { ascending: false }).limit(5)
  ]);

  return {
    total_memories: memories.count || 0,
    total_evolutions: evolutions.count || 0,
    total_errors_analyzed: errors.count || 0,
    total_knowledge: knowledge.count || 0,
    intelligence_history: intelligence.data || [],
    top_strategies: strategies.data || [],
    current_score: (intelligence.data || [])[0]?.intelligence_score || 0
  };
}
