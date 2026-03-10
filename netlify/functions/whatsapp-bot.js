// ============================================================
// MEHRDIMENSIONALER BOT — WhatsApp Webhook (Twilio → Bot Brain)
// 7 Dimensions: Fan-Manager, Sales, Content, Analytics,
//               Community, Booking, Mood-Reader
// ============================================================

const { createClient } = require('@supabase/supabase-js');

// --- CONFIG (env vars + Supabase bot_config fallback) ---
const SUPABASE_URL = process.env.SUPABASE_URL || 'https://pwdhxarvemcgkhhnvbng.supabase.co';
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_SERVICE_KEY || '';
const BASE_URL = process.env.URL || 'https://thetoysareout.com';

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY);

// --- AUTO-BOOTSTRAP: Check if tables exist, guide to setup if not ---
let _tablesChecked = false;
async function ensureTables() {
  if (_tablesChecked) return true;
  try {
    const { error } = await supabase.from('fans').select('id').limit(1);
    if (!error) { _tablesChecked = true; return true; }
    // Tables don't exist — try bot_config at least
    console.log('Bot tables not found, need setup. Visit /bot-admin to run setup wizard.');
    return false;
  } catch (e) { return false; }
}

// Credentials loaded at runtime from env or Supabase bot_config
let _configCache = null;
let _configLoadedAt = 0;

async function getCredentials() {
  // Cache config for 5 minutes
  if (_configCache && Date.now() - _configLoadedAt < 300000) return _configCache;

  // Start with env vars
  const creds = {
    twilio_sid: process.env.TWILIO_ACCOUNT_SID || '',
    twilio_token: process.env.TWILIO_AUTH_TOKEN || '',
    twilio_from: process.env.TWILIO_WHATSAPP_FROM || '',
    anthropic_key: process.env.ANTHROPIC_API_KEY || '',
    stripe_secret: process.env.STRIPE_SECRET_KEY || '',
    owner_phone: process.env.OWNER_WHATSAPP || process.env.TWILIO_WHATSAPP_TO || '',
  };

  // Fill missing values from Supabase bot_config
  try {
    const { data } = await supabase.from('bot_config').select('key, value');
    const cfg = {};
    (data || []).forEach(r => { cfg[r.key] = r.value; });

    if (!creds.twilio_sid && cfg.twilio_account_sid) creds.twilio_sid = cfg.twilio_account_sid;
    if (!creds.twilio_token && cfg.twilio_auth_token) creds.twilio_token = cfg.twilio_auth_token;
    if (!creds.twilio_from && cfg.twilio_whatsapp_from) creds.twilio_from = cfg.twilio_whatsapp_from;
    if (!creds.anthropic_key && cfg.anthropic_api_key) creds.anthropic_key = cfg.anthropic_api_key;
    if (!creds.stripe_secret && cfg.stripe_secret_key) creds.stripe_secret = cfg.stripe_secret_key;
    if (!creds.owner_phone && cfg.owner_whatsapp) creds.owner_phone = cfg.owner_whatsapp;
  } catch (e) { /* bot_config table might not exist yet */ }

  _configCache = creds;
  _configLoadedAt = Date.now();
  return creds;
}

// --- PRODUCT CATALOG ---
const PRODUCTS = {
  beats: {
    name: 'Exclusive Beat Pack',
    price: 29.99,
    type: 'drop',
    description: 'Exclusive Beats direkt vom Producer',
    emoji: '🎵'
  },
  merch: {
    name: 'TTAO Merch Collection',
    price: 39.99,
    type: 'merch',
    description: 'Limited Edition Streetwear',
    emoji: '👕'
  },
  inner_circle: {
    name: 'Inner Circle Membership',
    price: 9.99,
    type: 'session',
    description: 'Exklusiver Zugang zu unveröffentlichten Tracks, Behind the Scenes & Direct Access',
    emoji: '💎'
  },
  session: {
    name: 'Premium 1:1 Session',
    price: 149.00,
    type: 'session',
    description: 'Persönliche Session — Beats, Coaching oder Feature Talk',
    emoji: '🎤'
  },
  photo: {
    name: 'Signed Photo Print',
    price: 14.99,
    type: 'photo',
    description: 'Signiertes Foto mit persönlicher Widmung',
    emoji: '📸'
  }
};

// --- PERSONALITY SYSTEM PROMPT ---
const SYSTEM_PROMPT = `Du bist der persönliche WhatsApp-Assistent von THETOYSAREOUT (TTAO) — einem aufstrebenden Künstler und Producer.

PERSÖNLICHKEIT:
- Du klingst wie ein cooler, authentischer Mensch aus dem TTAO-Team
- Nicht zu formal, nicht zu slang-lastig. Natürlich und echt.
- Du bist hilfsbereit, aber auch mysterious — du weißt mehr als du sagst
- Du nutzt gelegentlich Emojis, aber übertreib nicht
- Du antwortest auf Deutsch, außer jemand schreibt auf Englisch
- Kurze, prägnante Antworten. Kein Roman.

WISSEN:
- TTAO macht Musik (Rap/HipHop/Experimental), Beats und therapeutische Sessions (PTGO Method)
- Website: thetoysareout.com
- Musik: thetoysareout.com/musik
- Healing Sessions: thetoysareout.com/heal
- Es gibt Merch, Beat Packs, einen Inner Circle und Premium Sessions
- Neue Drops kommen regelmäßig — halte die Spannung hoch

REGELN:
1. Wenn jemand nach Preisen fragt → zeige Produkte und biete Checkout-Link an
2. Wenn jemand ein Fan ist → baue eine echte Connection auf, frag nach Namen
3. Wenn jemand buchen will → sammle Details (Was, Wann, Budget) und erstelle Booking
4. Wenn jemand negative Stimmung hat → sei empathisch, biete PTGO Healing an
5. Wenn jemand zum ersten Mal schreibt → herzliches Willkommen, frag was sie suchen
6. Wenn jemand "stop" oder "aus" schreibt → respektiere opt-out sofort
7. NIEMALS erfundene Fakten oder falsche Versprechen machen
8. Bei Fragen die du nicht beantworten kannst → sage dass du es an TTAO weiterleitest

SALES-STRATEGIE (subtil, nicht pushy):
- Erwähne Produkte nur wenn es zum Gespräch passt
- Nutze FOMO: "Nur noch X verfügbar", "Letzte Chance"
- Social Proof: "Viele aus der Community haben sich das geholt"
- Persönliche Empfehlungen basierend auf dem Gespräch
- Nach positivem Gespräch: soft upsell

VERFÜGBARE PRODUKTE:
${Object.entries(PRODUCTS).map(([k, v]) => `- ${v.emoji} ${v.name}: €${v.price} (${v.description})`).join('\n')}`;

// ============================================================
// MAIN HANDLER — Twilio Webhook
// ============================================================
exports.handler = async (event) => {
  // Twilio sends POST with form-urlencoded body
  if (event.httpMethod !== 'POST') {
    return { statusCode: 200, body: '' };
  }

  const startTime = Date.now();
  const creds = await getCredentials();

  try {
    // Parse Twilio webhook payload
    const params = new URLSearchParams(event.body);
    const from = params.get('From') || '';          // whatsapp:+49...
    const body = params.get('Body') || '';
    const mediaUrl = params.get('MediaUrl0') || '';
    const messageType = mediaUrl ? 'image' : 'text';
    const phone = from.replace('whatsapp:', '');

    if (!phone || !body) {
      return twimlResponse('');
    }

    // --- STEP 1: Get or create fan profile ---
    let fan = await getOrCreateFan(phone);

    // --- STEP 2: Log inbound message ---
    const convoId = await logMessage(fan.id, phone, 'inbound', body, messageType, mediaUrl);

    // --- STEP 3: Check for special commands ---
    const lowerBody = body.toLowerCase().trim();

    // Opt-out
    if (['stop', 'aus', 'unsubscribe', 'quit'].includes(lowerBody)) {
      await supabase.from('fans').update({ opt_out: true }).eq('id', fan.id);
      const reply = 'Du wurdest abgemeldet. Schreib jederzeit "start" um wieder dabei zu sein. ✌️';
      await logMessage(fan.id, phone, 'outbound', reply, 'text', null, convoId);
      return twimlResponse(reply);
    }

    // Opt back in
    if (['start', 'an', 'subscribe'].includes(lowerBody)) {
      await supabase.from('fans').update({ opt_out: false }).eq('id', fan.id);
      const reply = 'Willkommen zurück! 🔥 Du bist wieder dabei.';
      await logMessage(fan.id, phone, 'outbound', reply, 'text', null, convoId);
      return twimlResponse(reply);
    }

    // --- STEP 4: Detect intent & sentiment with AI ---
    const analysis = await analyzeMessage(body, fan, creds);

    // Update fan mood
    if (analysis.sentiment !== undefined) {
      const mood = analysis.sentiment > 0.3 ? 'positive'
        : analysis.sentiment < -0.3 ? 'negative'
        : analysis.sentiment > 0.6 ? 'excited'
        : 'neutral';
      await supabase.from('fans').update({ mood }).eq('id', fan.id);
    }

    // Update conversation with analysis
    await supabase.from('conversations').update({
      intent: analysis.intent,
      sentiment: analysis.sentiment,
      topics: analysis.topics,
      entities: analysis.entities
    }).eq('id', convoId);

    // --- STEP 5: Generate response based on intent ---
    let reply = '';
    let strategy = 'ai_conversation';

    // Handle purchase intent
    if (analysis.intent === 'purchase') {
      const result = await handlePurchaseIntent(fan, body, analysis, creds);
      reply = result.reply;
      strategy = 'sales_flow';
    }
    // Handle booking intent
    else if (analysis.intent === 'booking') {
      const result = await handleBookingIntent(fan, body, analysis, creds);
      reply = result.reply;
      strategy = 'booking_flow';
    }
    // Handle help/complaint
    else if (analysis.intent === 'complaint') {
      reply = await handleComplaint(fan, body);
      strategy = 'support_escalation';
      // Notify owner
      if (creds.owner_phone) {
        await sendWhatsApp(creds.owner_phone, `⚠️ Fan-Beschwerde von ${fan.name || phone}:\n"${body}"`, creds);
      }
    }
    // General AI conversation
    else {
      reply = await generateResponse(fan, body, analysis, creds);
    }

    // --- STEP 6: Update fan stats ---
    const responseTime = Date.now() - startTime;
    await updateFanStats(fan, analysis);

    // --- STEP 7: Log outbound & respond ---
    await logMessage(fan.id, phone, 'outbound', reply, 'text', null, convoId, {
      response_strategy: strategy,
      response_time_ms: responseTime,
      ai_model: 'claude-haiku-4-5'
    });

    // Update daily analytics
    await updateDailyAnalytics(analysis, responseTime);

    // --- STEP 8: Learn from this conversation (non-blocking) ---
    learnFromInteraction(fan, convoId, body, analysis, reply, strategy).catch(() => {});

    return twimlResponse(reply);

  } catch (err) {
    console.error('Bot error:', err);
    return twimlResponse('Gerade technische Schwierigkeiten — versuch es gleich nochmal! 🔧');
  }
};

// ============================================================
// FAN MANAGEMENT
// ============================================================
async function getOrCreateFan(phone) {
  const { data: existing } = await supabase
    .from('fans')
    .select('*')
    .eq('phone', phone)
    .single();

  if (existing) {
    // Update last message timestamp
    await supabase.from('fans').update({
      last_message_at: new Date().toISOString(),
      total_messages: (existing.total_messages || 0) + 1
    }).eq('id', existing.id);
    return existing;
  }

  // Create new fan
  const { data: newFan } = await supabase
    .from('fans')
    .insert({
      phone,
      tier: 'new',
      mood: 'neutral',
      total_messages: 1,
      last_message_at: new Date().toISOString(),
      first_contact_at: new Date().toISOString(),
      conversation_state: 'onboarding'
    })
    .select()
    .single();

  return newFan;
}

async function updateFanStats(fan, analysis) {
  const updates = {};

  // Update engagement score based on activity
  const daysSinceFirst = Math.max(1, (Date.now() - new Date(fan.first_contact_at).getTime()) / 86400000);
  const messagesPerDay = (fan.total_messages || 1) / daysSinceFirst;
  updates.engagement_score = Math.min(100, Math.round(messagesPerDay * 20 + (fan.total_messages || 0) * 0.5));

  // Update loyalty score based on how long they've been around
  updates.loyalty_score = Math.min(100, Math.round(daysSinceFirst * 0.5 + (fan.total_messages || 0) * 0.3));

  // Update purchase score
  const spentEuros = (fan.total_spent_cents || 0) / 100;
  updates.purchase_score = Math.min(100, Math.round(spentEuros * 2 + (fan.total_purchases || 0) * 10));

  // Calculate VIP score (weighted average)
  updates.vip_score = Math.round(
    updates.engagement_score * 0.3 +
    updates.loyalty_score * 0.2 +
    updates.purchase_score * 0.5
  );

  // Auto-tier based on VIP score
  if (updates.vip_score >= 95) updates.tier = 'whale';
  else if (updates.vip_score >= 80) updates.tier = 'vip';
  else if (updates.vip_score >= 60) updates.tier = 'superfan';
  else if (updates.vip_score >= 30) updates.tier = 'engaged';
  else if (updates.vip_score >= 10) updates.tier = 'casual';

  // Add detected interests
  if (analysis.topics && analysis.topics.length > 0) {
    const currentInterests = fan.interests || [];
    const newInterests = [...new Set([...currentInterests, ...analysis.topics])].slice(0, 20);
    updates.interests = newInterests;
  }

  await supabase.from('fans').update(updates).eq('id', fan.id);
}

// ============================================================
// AI — MESSAGE ANALYSIS
// ============================================================
async function analyzeMessage(body, fan, creds) {
  if (!creds.anthropic_key) {
    return { intent: 'other', sentiment: 0, topics: [], entities: {} };
  }

  const prompt = `Analysiere diese WhatsApp-Nachricht an einen Musik-Künstler.

Nachricht: "${body}"

Fan-Info: Tier=${fan.tier}, Mood=${fan.mood}, Messages=${fan.total_messages}, State=${fan.conversation_state}

Antworte NUR mit JSON:
{
  "intent": "greeting|question|purchase|complaint|booking|feedback|compliment|other",
  "sentiment": <float -1.0 bis 1.0>,
  "topics": ["beats","merch","shows","sessions","music","personal","booking","price"],
  "entities": {"product": null, "date": null, "budget": null, "name": null},
  "urgency": "low|medium|high",
  "language": "de|en|tr|ar"
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
        max_tokens: 300,
        messages: [{ role: 'user', content: prompt }]
      })
    });

    const data = await res.json();
    const text = data.content?.[0]?.text || '{}';
    // Extract JSON from response
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    return jsonMatch ? JSON.parse(jsonMatch[0]) : { intent: 'other', sentiment: 0, topics: [], entities: {} };
  } catch (e) {
    console.error('Analysis error:', e);
    return { intent: 'other', sentiment: 0, topics: [], entities: {} };
  }
}

// ============================================================
// AI — RESPONSE GENERATION
// ============================================================
async function generateResponse(fan, body, analysis, creds) {
  if (!creds.anthropic_key) {
    return 'Hey! Danke für deine Nachricht. Wir melden uns bald! 🔥';
  }

  // Get recent conversation history
  const { data: history } = await supabase
    .from('conversations')
    .select('direction, body, created_at')
    .eq('fan_id', fan.id)
    .order('created_at', { ascending: false })
    .limit(10);

  const historyText = (history || []).reverse().map(m =>
    `${m.direction === 'inbound' ? 'Fan' : 'Bot'}: ${m.body}`
  ).join('\n');

  // Get memories about this fan
  let memoriesText = '';
  try {
    const { data: memories } = await supabase
      .from('bot_memory')
      .select('key, value')
      .eq('subject', fan.phone)
      .order('confidence', { ascending: false })
      .limit(5);
    if (memories && memories.length > 0) {
      memoriesText = '\n\nERINNERUNGEN ÜBER DIESEN FAN:\n' +
        memories.map(m => `- ${m.key}: ${JSON.stringify(m.value)}`).join('\n');
    }
  } catch (e) { /* memory table might not exist yet */ }

  // Get learned lessons
  let lessonsText = '';
  try {
    const { data: lessons } = await supabase
      .from('bot_errors')
      .select('lesson_learned, prevention_strategy')
      .eq('applied', false)
      .order('created_at', { ascending: false })
      .limit(3);
    if (lessons && lessons.length > 0) {
      lessonsText = '\n\nGELERNTE LEKTIONEN (beachte diese!):\n' +
        lessons.map(l => `- ${l.lesson_learned}`).join('\n');
      // Mark as applied
      for (const l of lessons) {
        await supabase.from('bot_errors').update({ applied: true }).eq('lesson_learned', l.lesson_learned);
      }
    }
  } catch (e) { /* errors table might not exist yet */ }

  const contextPrompt = `${SYSTEM_PROMPT}
${memoriesText}
${lessonsText}

FAN-PROFIL:
- Name: ${fan.name || 'Unbekannt'}
- Tier: ${fan.tier} (VIP-Score: ${fan.vip_score})
- Stimmung: ${fan.mood}
- Gesamt-Nachrichten: ${fan.total_messages}
- Käufe: ${fan.total_purchases} (€${((fan.total_spent_cents || 0) / 100).toFixed(2)})
- Interessen: ${(fan.interests || []).join(', ') || 'noch unbekannt'}
- Erster Kontakt: ${fan.first_contact_at}
- Status: ${fan.conversation_state}

LETZTE NACHRICHTEN:
${historyText}

ANALYSE DER AKTUELLEN NACHRICHT:
- Intent: ${analysis.intent}
- Sentiment: ${analysis.sentiment}
- Themen: ${(analysis.topics || []).join(', ')}
- Sprache: ${analysis.language || 'de'}

AKTUELLE NACHRICHT: "${body}"

Antworte als TTAO-Bot. Kurz, authentisch, cool. Max 3 Sätze.
${fan.tier === 'new' ? 'Das ist ein neuer Fan — mach einen guten ersten Eindruck!' : ''}
${fan.tier === 'vip' || fan.tier === 'whale' ? 'Das ist ein VIP — behandle sie besonders!' : ''}
${analysis.sentiment < -0.3 ? 'Die Person klingt negativ — sei extra einfühlsam.' : ''}`;

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
        messages: [{ role: 'user', content: contextPrompt }]
      })
    });

    const data = await res.json();
    return data.content?.[0]?.text || 'Hey! Danke für deine Nachricht 🔥';
  } catch (e) {
    console.error('Response generation error:', e);
    return 'Hey! Danke für deine Nachricht. Ich melde mich gleich! 🔥';
  }
}

// ============================================================
// SALES ENGINE
// ============================================================
async function handlePurchaseIntent(fan, body, analysis, creds) {
  const lowerBody = body.toLowerCase();

  // Detect which product they want
  let product = null;
  if (lowerBody.includes('beat') || lowerBody.includes('track') || lowerBody.includes('musik')) {
    product = PRODUCTS.beats;
  } else if (lowerBody.includes('merch') || lowerBody.includes('shirt') || lowerBody.includes('hoodie') || lowerBody.includes('klamott')) {
    product = PRODUCTS.merch;
  } else if (lowerBody.includes('inner') || lowerBody.includes('circle') || lowerBody.includes('vip') || lowerBody.includes('exklusiv')) {
    product = PRODUCTS.inner_circle;
  } else if (lowerBody.includes('session') || lowerBody.includes('coaching') || lowerBody.includes('1:1')) {
    product = PRODUCTS.session;
  } else if (lowerBody.includes('foto') || lowerBody.includes('photo') || lowerBody.includes('bild') || lowerBody.includes('sign')) {
    product = PRODUCTS.photo;
  }

  if (product) {
    // Create Stripe checkout
    const checkoutUrl = await createCheckoutLink(fan, product, creds);

    // Log sale attempt
    await supabase.from('bot_sales').insert({
      fan_id: fan.id,
      phone: fan.phone,
      item_type: product.type,
      item_name: product.name,
      price_cents: Math.round(product.price * 100),
      checkout_url: checkoutUrl,
      source: 'bot'
    });

    const reply = `${product.emoji} ${product.name} — €${product.price}\n\n${product.description}\n\nHier dein persönlicher Link:\n${checkoutUrl || BASE_URL + '/musik'}\n\nBei Fragen bin ich da! 💯`;
    return { reply, product };
  }

  // No specific product detected — show catalog
  const catalog = Object.entries(PRODUCTS).map(([k, v]) =>
    `${v.emoji} *${v.name}* — €${v.price}`
  ).join('\n');

  const reply = `Check mal was wir haben:\n\n${catalog}\n\nWas davon interessiert dich? Ich schick dir den Link! 🔗`;
  return { reply, product: null };
}

async function createCheckoutLink(fan, product, creds) {
  if (!creds.stripe_secret) return null;

  try {
    const stripe = require('stripe')(creds.stripe_secret);
    const session = await stripe.checkout.sessions.create({
      payment_method_types: ['card'],
      mode: 'payment',
      line_items: [{
        price_data: {
          currency: 'eur',
          unit_amount: Math.round(product.price * 100),
          product_data: {
            name: product.name,
            description: product.description
          }
        },
        quantity: 1
      }],
      success_url: `${BASE_URL}/musik?purchased=${product.type}&success=1&fan=${fan.phone}`,
      cancel_url: `${BASE_URL}/musik?cancelled=1`,
      metadata: {
        fan_id: fan.id,
        fan_phone: fan.phone,
        item_type: product.type,
        source: 'whatsapp_bot'
      }
    });
    return session.url;
  } catch (e) {
    console.error('Stripe checkout error:', e);
    return null;
  }
}

// ============================================================
// BOOKING ENGINE
// ============================================================
async function handleBookingIntent(fan, body, analysis, creds) {
  const entities = analysis.entities || {};

  // Create booking record
  const bookingType = body.toLowerCase().includes('show') ? 'show'
    : body.toLowerCase().includes('feature') ? 'feature'
    : body.toLowerCase().includes('interview') ? 'interview'
    : body.toLowerCase().includes('collab') ? 'collab'
    : 'session';

  await supabase.from('bot_bookings').insert({
    fan_id: fan.id,
    phone: fan.phone,
    booking_type: bookingType,
    description: body,
    preferred_date: entities.date || null,
    budget_cents: entities.budget ? Math.round(parseFloat(entities.budget) * 100) : null
  });

  // Update fan state
  await supabase.from('fans').update({ conversation_state: 'booking' }).eq('id', fan.id);

  // Notify owner
  if (creds.owner_phone) {
    await sendWhatsApp(creds.owner_phone,
      `📅 Neue Booking-Anfrage!\n\nVon: ${fan.name || fan.phone}\nTyp: ${bookingType}\nNachricht: "${body}"\nTier: ${fan.tier} (VIP: ${fan.vip_score})`,
      creds
    );
  }

  const reply = `Starke Sache! Ich hab deine ${bookingType === 'show' ? 'Show' : bookingType === 'feature' ? 'Feature' : 'Session'}-Anfrage aufgenommen. 📋\n\nIch leite das direkt an TTAO weiter — du hörst bald von uns!\n\nKannst du mir noch sagen:\n- Wann ungefähr?\n- Budget-Vorstellung?\n- Weitere Details?`;

  return { reply };
}

// ============================================================
// COMPLAINT HANDLER
// ============================================================
async function handleComplaint(fan, body) {
  // Escalate to owner and respond empathetically
  return `Hey, das tut mir leid zu hören. 🙏\n\nIch leite deine Nachricht direkt an TTAO persönlich weiter. Du bekommst schnellstmöglich eine Antwort.\n\nDanke für deine Ehrlichkeit — das hilft uns besser zu werden.`;
}

// ============================================================
// MESSAGE LOGGING
// ============================================================
async function logMessage(fanId, phone, direction, body, type, mediaUrl, relatedConvoId, extra) {
  const record = {
    fan_id: fanId,
    phone,
    direction,
    body,
    message_type: type || 'text',
    media_url: mediaUrl || null,
    ...extra
  };

  const { data } = await supabase.from('conversations').insert(record).select('id').single();
  return data?.id;
}

// ============================================================
// SEND WHATSAPP (Twilio)
// ============================================================
async function sendWhatsApp(to, body, creds) {
  const sid = creds?.twilio_sid || '';
  const token = creds?.twilio_token || '';
  const from = creds?.twilio_from || '';
  if (!sid || !token || !from) return false;

  const toFormatted = to.startsWith('whatsapp:') ? to : `whatsapp:${to}`;

  try {
    const auth = Buffer.from(`${sid}:${token}`).toString('base64');
    await fetch(`https://api.twilio.com/2010-04-01/Accounts/${sid}/Messages.json`, {
      method: 'POST',
      headers: {
        'Authorization': `Basic ${auth}`,
        'Content-Type': 'application/x-www-form-urlencoded'
      },
      body: new URLSearchParams({
        From: from.startsWith('whatsapp:') ? from : `whatsapp:${from}`,
        To: toFormatted,
        Body: body
      })
    });
    return true;
  } catch (e) {
    console.error('Twilio send error:', e);
    return false;
  }
}

// ============================================================
// ANALYTICS
// ============================================================
async function updateDailyAnalytics(analysis, responseTimeMs) {
  const today = new Date().toISOString().split('T')[0];

  // Upsert today's analytics
  const { data: existing } = await supabase
    .from('bot_analytics')
    .select('*')
    .eq('date', today)
    .single();

  if (existing) {
    await supabase.from('bot_analytics').update({
      total_messages_in: (existing.total_messages_in || 0) + 1,
      total_messages_out: (existing.total_messages_out || 0) + 1,
      avg_response_time_ms: Math.round(
        ((existing.avg_response_time_ms || 0) * (existing.total_messages_in || 1) + responseTimeMs) /
        ((existing.total_messages_in || 1) + 1)
      ),
      checkout_links_sent: analysis.intent === 'purchase'
        ? (existing.checkout_links_sent || 0) + 1
        : existing.checkout_links_sent || 0,
      booking_requests: analysis.intent === 'booking'
        ? (existing.booking_requests || 0) + 1
        : existing.booking_requests || 0
    }).eq('date', today);
  } else {
    await supabase.from('bot_analytics').insert({
      date: today,
      total_messages_in: 1,
      total_messages_out: 1,
      avg_response_time_ms: responseTimeMs,
      checkout_links_sent: analysis.intent === 'purchase' ? 1 : 0,
      booking_requests: analysis.intent === 'booking' ? 1 : 0
    });
  }
}

// ============================================================
// TWIML RESPONSE HELPER
// ============================================================
function twimlResponse(message) {
  const xml = message
    ? `<?xml version="1.0" encoding="UTF-8"?><Response><Message>${escapeXml(message)}</Message></Response>`
    : `<?xml version="1.0" encoding="UTF-8"?><Response></Response>`;

  return {
    statusCode: 200,
    headers: { 'Content-Type': 'text/xml' },
    body: xml
  };
}

function escapeXml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

// ============================================================
// LEARNING — After every conversation, extract and store knowledge
// ============================================================
async function learnFromInteraction(fan, convoId, message, analysis, reply, strategy) {
  try {
    // 1. Learn fan's name if mentioned
    if (analysis.entities?.name && !fan.name) {
      await supabase.from('fans').update({ name: analysis.entities.name }).eq('id', fan.id);
      await upsertMemory('fact', fan.phone, 'name', { name: analysis.entities.name }, 0.9);
    }

    // 2. Learn fan preferences
    if (analysis.topics && analysis.topics.length > 0) {
      await upsertMemory('preference', fan.phone, 'interests', {
        topics: analysis.topics,
        last_intent: analysis.intent
      }, 0.6);
    }

    // 3. Learn communication patterns
    await upsertMemory('pattern', fan.phone, 'comm_style', {
      avg_length: message.length,
      sentiment: analysis.sentiment,
      language: analysis.language || 'de',
      time: new Date().getHours()
    }, 0.5);

    // 4. If this was a special owner command, process it
    const ownerPhone = (await getCredentials()).owner_phone;
    if (fan.phone === ownerPhone) {
      await handleOwnerCommand(message, analysis);
    }
  } catch (e) {
    // Learning should never block the response
    console.error('Learning error (non-critical):', e.message);
  }
}

async function handleOwnerCommand(message, analysis) {
  const lower = message.toLowerCase();

  // Owner can teach the bot directly
  if (lower.startsWith('lerne:') || lower.startsWith('learn:')) {
    const lesson = message.substring(message.indexOf(':') + 1).trim();
    await upsertMemory('insight', 'owner', `teaching_${Date.now()}`, { lesson }, 1.0);
  }

  // Owner can add knowledge
  if (lower.startsWith('wissen:') || lower.startsWith('knowledge:')) {
    const knowledge = message.substring(message.indexOf(':') + 1).trim();
    await supabase.from('bot_knowledge').insert({
      domain: 'owner_taught',
      topic: `fact_${Date.now()}`,
      content: knowledge,
      source: 'owner',
      verified: true,
      relevance_score: 1.0
    });
  }

  // Owner can track life data
  if (lower.startsWith('track:')) {
    // Format: track: health/sleep 8
    const parts = message.substring(6).trim().split(' ');
    if (parts.length >= 2) {
      const [catMetric, ...valueParts] = parts;
      const [category, metric] = catMetric.split('/');
      const value = valueParts.join(' ');
      await supabase.from('life_data').insert({
        category: category || 'general',
        metric: metric || 'score',
        value: { score: parseFloat(value) || value },
        trend: 'stable'
      });
    }
  }
}

async function upsertMemory(category, subject, key, value, confidence) {
  try {
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
        category, subject, key, value, confidence, source: 'conversation'
      });
    }
  } catch (e) { /* memory table might not exist yet */ }
}
