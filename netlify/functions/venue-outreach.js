// ============================================================
// VENUE OUTREACH ENGINE — Find & contact venues that need pianists
// Regions: München, Kitzbühel, Umgebung
// IMPORTANT: Always asks owner before sending any outreach
// ============================================================

const { createClient } = require('@supabase/supabase-js');

const supabase = createClient(
  process.env.SUPABASE_URL || 'https://pwdhxarvemcgkhhnvbng.supabase.co',
  process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_SERVICE_KEY || ''
);

const ALLOWED_ORIGINS = [
  'https://thetoysareout.com',
  'https://www.thetoysareout.com',
  'http://localhost:8888',
];

// ── VENUE CATEGORIES TO TARGET ──
const VENUE_CATEGORIES = [
  { type: 'hotel_luxury', label: 'Luxushotels', keywords: ['hotel', 'resort', 'grand hotel', 'palace'] },
  { type: 'restaurant_fine', label: 'Fine Dining', keywords: ['restaurant', 'gourmet', 'sternerestaurant', 'fine dining'] },
  { type: 'event_location', label: 'Event Locations', keywords: ['eventlocation', 'veranstaltungsort', 'schloss', 'villa'] },
  { type: 'bar_lounge', label: 'Piano Bars & Lounges', keywords: ['piano bar', 'lounge', 'cocktailbar', 'bar'] },
  { type: 'gallery', label: 'Galerien & Museen', keywords: ['galerie', 'museum', 'kunsthalle', 'vernissage'] },
  { type: 'corporate', label: 'Firmen & Agenturen', keywords: ['eventagentur', 'firmenevents', 'corporate events'] },
  { type: 'wedding', label: 'Hochzeitslocations', keywords: ['hochzeit', 'wedding', 'trauung', 'braut'] },
];

// ── REGIONS ──
const REGIONS = [
  { id: 'munich', name: 'München', radius: '30km', searchTerms: ['München', 'Munich', 'Starnberg', 'Grünwald', 'Schwabing'] },
  { id: 'kitzbuehel', name: 'Kitzbühel', radius: '25km', searchTerms: ['Kitzbühel', 'Kirchberg', 'Aurach', 'Jochberg', 'St. Johann in Tirol'] },
  { id: 'rosenheim', name: 'Rosenheim & Umgebung', radius: '30km', searchTerms: ['Rosenheim', 'Bad Aibling', 'Prien', 'Chiemsee', 'Wasserburg'] },
  { id: 'innsbruck', name: 'Innsbruck & Umgebung', radius: '20km', searchTerms: ['Innsbruck', 'Hall in Tirol', 'Seefeld'] },
  { id: 'salzburg', name: 'Salzburg', radius: '20km', searchTerms: ['Salzburg', 'Fuschl', 'Mondsee'] },
];

// ── OUTREACH MESSAGE TEMPLATES (organic, non-spammy) ──
const OUTREACH_TEMPLATES = {
  hotel_luxury: `Sehr geehrte Damen und Herren,

ich bin Pianist und ehemaliger Regensburger Domspatze. Ich biete intime Live-Klavierabende an — "Eine Reise durch mein Leben" — eine Mischung aus Klassik, Emotion und persönlichen Geschichten.

Meine Auftritte passen perfekt in gehobene Hotels: ob als regelmäßiges Abendprogramm, für besondere Anlässe oder exklusive Gästeevents.

Ich würde mich freuen, Ihnen mein Konzept persönlich vorzustellen:
https://thetoysareout.com/live

Mit freundlichen Grüßen`,

  restaurant_fine: `Sehr geehrte Damen und Herren,

stellen Sie sich vor: Ihre Gäste genießen ein exquisites Dinner, begleitet von live Klaviermusik, die unter die Haut geht.

Ich bin Pianist und ehemaliger Regensburger Domspatze. Mein Programm "Eine Reise durch mein Leben" verbindet Klassik mit roher Emotion — perfekt für gehobene Restaurants, die ihren Gästen etwas Besonderes bieten möchten.

Mehr erfahren: https://thetoysareout.com/live

Herzliche Grüße`,

  event_location: `Sehr geehrte Damen und Herren,

suchen Sie musikalische Begleitung für Ihre Events? Als ehemaliger Regensburger Domspatze biete ich Live-Klavierabende an, die Ihre Veranstaltung unvergesslich machen.

Mein Programm "Eine Reise durch mein Leben" lässt sich flexibel an jeden Anlass anpassen — von intimen Abenden bis zu großen Galas.

Alle Details: https://thetoysareout.com/live

Mit besten Grüßen`,

  default: `Sehr geehrte Damen und Herren,

ich bin Pianist und ehemaliger Regensburger Domspatze. Ich biete exklusive Live-Klavierabende an — "Eine Reise durch mein Leben".

Klassik, Emotion und persönliche Geschichten an einem Abend. Ideal für gehobene Anlässe und besondere Locations.

Mehr: https://thetoysareout.com/live

Herzliche Grüße`
};

// ============================================================
// MAIN HANDLER
// ============================================================
exports.handler = async (event) => {
  const origin = event.headers.origin || '';
  const corsOrigin = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  const headers = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': corsOrigin,
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Allow-Methods': 'POST, GET, OPTIONS',
  };

  if (event.httpMethod === 'OPTIONS') return { statusCode: 204, headers };

  try {
    const body = event.body ? JSON.parse(event.body) : {};
    const { action } = body;

    // ── LIST VENUES ──
    if (action === 'list' || event.httpMethod === 'GET') {
      const { data } = await supabase
        .from('venue_leads')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(100);
      return { statusCode: 200, headers, body: JSON.stringify({ venues: data || [] }) };
    }

    // ── ADD VENUE (manual or from AI search) ──
    if (action === 'add') {
      const { name, type, region, email, phone, website, notes } = body;
      const { data } = await supabase
        .from('venue_leads')
        .insert({
          name, venue_type: type || 'other', region: region || 'munich',
          email: email || null, phone: phone || null,
          website: website || null, notes: notes || null,
          status: 'new', outreach_status: 'pending'
        })
        .select()
        .single();
      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    // ── ADD MULTIPLE VENUES (batch from AI research) ──
    if (action === 'add_batch') {
      const { venues } = body;
      if (!venues || !Array.isArray(venues)) {
        return { statusCode: 400, headers, body: '{"error":"venues array required"}' };
      }
      const records = venues.map(v => ({
        name: v.name,
        venue_type: v.type || 'other',
        region: v.region || 'munich',
        email: v.email || null,
        phone: v.phone || null,
        website: v.website || null,
        notes: v.notes || null,
        status: 'new',
        outreach_status: 'pending'
      }));
      const { data } = await supabase.from('venue_leads').insert(records).select();
      return { statusCode: 200, headers, body: JSON.stringify({ added: (data || []).length }) };
    }

    // ── AI RESEARCH — Find venues using Claude ──
    if (action === 'research') {
      const { region, category } = body;
      const creds = await getCredentials();
      if (!creds.anthropic_key) {
        return { statusCode: 400, headers, body: '{"error":"ANTHROPIC_API_KEY needed"}' };
      }

      const regionObj = REGIONS.find(r => r.id === region) || REGIONS[0];
      const catObj = VENUE_CATEGORIES.find(c => c.type === category) || VENUE_CATEGORIES[0];

      const prompt = `Du bist ein Recherche-Assistent. Finde reale ${catObj.label} in der Region ${regionObj.name} (${regionObj.searchTerms.join(', ')}), die einen Live-Pianisten für Events buchen könnten.

Gib mir eine Liste von 10-15 realen Locations. Für jede:
- Name (echter Name der Location)
- Typ (${catObj.type})
- Warum sie einen Pianisten buchen würden
- Geschätzte Kontaktmöglichkeit (Website wenn bekannt)

Antworte NUR mit JSON-Array:
[{"name":"...","type":"${catObj.type}","region":"${region}","reason":"...","website":"...","notes":"..."}]

WICHTIG: Nur reale, existierende Locations. Keine erfundenen.`;

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
      const text = data.content?.[0]?.text || '[]';
      const jsonMatch = text.match(/\[[\s\S]*\]/);
      const venues = jsonMatch ? JSON.parse(jsonMatch[0]) : [];

      return { statusCode: 200, headers, body: JSON.stringify({ venues, region: regionObj.name, category: catObj.label }) };
    }

    // ── GENERATE OUTREACH MESSAGE for a specific venue ──
    if (action === 'generate_message') {
      const { venue_id } = body;
      const { data: venue } = await supabase
        .from('venue_leads')
        .select('*')
        .eq('id', venue_id)
        .single();

      if (!venue) return { statusCode: 404, headers, body: '{"error":"Venue not found"}' };

      const creds = await getCredentials();
      let message = OUTREACH_TEMPLATES[venue.venue_type] || OUTREACH_TEMPLATES.default;

      // If we have AI, personalize the message
      if (creds.anthropic_key) {
        const prompt = `Personalisiere diese Outreach-Nachricht für "${venue.name}" (${venue.venue_type}, Region: ${venue.region}).

Original:
${message}

Notizen über die Location: ${venue.notes || 'keine'}
Website: ${venue.website || 'unbekannt'}

Passe den Einstieg an, damit er sich auf diese spezifische Location bezieht. Halte den Rest gleich. Antworte NUR mit der fertigen Nachricht, nichts anderes. Die Nachricht soll professionell, warm und nicht spammy klingen.`;

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
          message = data.content?.[0]?.text || message;
        } catch (e) { /* use template */ }
      }

      return { statusCode: 200, headers, body: JSON.stringify({ venue, message }) };
    }

    // ── REQUEST APPROVAL — Send owner a WhatsApp for approval before outreach ──
    if (action === 'request_approval') {
      const { venue_id, message } = body;
      const creds = await getCredentials();

      const { data: venue } = await supabase
        .from('venue_leads')
        .select('*')
        .eq('id', venue_id)
        .single();

      if (!venue) return { statusCode: 404, headers, body: '{"error":"Venue not found"}' };

      // Notify owner via WhatsApp
      if (creds.owner_phone) {
        const approval = `🎹 VENUE OUTREACH — Genehmigung erbeten\n\n` +
          `📍 ${venue.name} (${venue.region})\n` +
          `📧 Typ: ${venue.venue_type}\n` +
          `🌐 ${venue.website || 'keine Website'}\n\n` +
          `--- NACHRICHT ---\n${(message || '').substring(0, 500)}\n---\n\n` +
          `Antwort mit:\n` +
          `✅ "ja ${venue.id}" = Senden\n` +
          `❌ "nein ${venue.id}" = Ablehnen\n` +
          `✏️ "edit ${venue.id}" = Bearbeiten`;

        await sendWhatsApp(creds.owner_phone, approval, creds);
      }

      // Update status
      await supabase.from('venue_leads').update({
        outreach_status: 'awaiting_approval',
        outreach_message: message,
        approval_requested_at: new Date().toISOString()
      }).eq('id', venue_id);

      return { statusCode: 200, headers, body: JSON.stringify({ status: 'approval_requested', venue: venue.name }) };
    }

    // ── APPROVE & SEND — Mark approved and send the outreach ──
    if (action === 'approve_send') {
      const { venue_id } = body;
      const { data: venue } = await supabase
        .from('venue_leads')
        .select('*')
        .eq('id', venue_id)
        .single();

      if (!venue) return { statusCode: 404, headers, body: '{"error":"Venue not found"}' };

      // Send email if available
      let sent = false;
      if (venue.email) {
        sent = await sendOutreachEmail(venue, venue.outreach_message);
      }

      // Update status
      await supabase.from('venue_leads').update({
        outreach_status: sent ? 'sent' : 'approved_no_email',
        outreach_sent_at: sent ? new Date().toISOString() : null,
        status: 'contacted'
      }).eq('id', venue_id);

      // Notify owner
      const creds = await getCredentials();
      if (creds.owner_phone) {
        await sendWhatsApp(creds.owner_phone,
          sent
            ? `✅ Outreach an "${venue.name}" gesendet!`
            : `⚠️ "${venue.name}" genehmigt aber keine E-Mail-Adresse hinterlegt. Bitte manuell kontaktieren.`,
          creds
        );
      }

      return { statusCode: 200, headers, body: JSON.stringify({ status: sent ? 'sent' : 'no_email', venue: venue.name }) };
    }

    // ── UPDATE VENUE STATUS ──
    if (action === 'update') {
      const { venue_id, updates } = body;
      const allowed = ['status', 'outreach_status', 'email', 'phone', 'notes', 'website', 'name', 'venue_type', 'region'];
      const clean = {};
      for (const [k, v] of Object.entries(updates || {})) {
        if (allowed.includes(k)) clean[k] = v;
      }
      await supabase.from('venue_leads').update(clean).eq('id', venue_id);
      return { statusCode: 200, headers, body: JSON.stringify({ updated: true }) };
    }

    // ── DELETE VENUE ──
    if (action === 'delete') {
      await supabase.from('venue_leads').delete().eq('id', body.venue_id);
      return { statusCode: 200, headers, body: JSON.stringify({ deleted: true }) };
    }

    // ── STATS ──
    if (action === 'stats') {
      const { data: all } = await supabase.from('venue_leads').select('status, outreach_status, region');
      const stats = {
        total: (all || []).length,
        by_status: {},
        by_region: {},
        by_outreach: {}
      };
      (all || []).forEach(v => {
        stats.by_status[v.status] = (stats.by_status[v.status] || 0) + 1;
        stats.by_region[v.region] = (stats.by_region[v.region] || 0) + 1;
        stats.by_outreach[v.outreach_status] = (stats.by_outreach[v.outreach_status] || 0) + 1;
      });
      return { statusCode: 200, headers, body: JSON.stringify(stats) };
    }

    return { statusCode: 400, headers, body: '{"error":"Unknown action"}' };

  } catch (err) {
    console.error('Venue outreach error:', err);
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};

// ── HELPERS ──

async function getCredentials() {
  const creds = {
    anthropic_key: process.env.ANTHROPIC_API_KEY || '',
    owner_phone: process.env.OWNER_WHATSAPP || process.env.TWILIO_WHATSAPP_TO || '',
    twilio_sid: process.env.TWILIO_ACCOUNT_SID || '',
    twilio_token: process.env.TWILIO_AUTH_TOKEN || '',
    twilio_from: process.env.TWILIO_WHATSAPP_FROM || '',
    smtp_host: process.env.SMTP_HOST || '',
    smtp_port: process.env.SMTP_PORT || '587',
    smtp_user: process.env.SMTP_USER || '',
    smtp_pass: process.env.SMTP_PASS || '',
    smtp_from: process.env.SMTP_FROM || '',
  };

  try {
    const { data } = await supabase.from('bot_config').select('key, value');
    const cfg = {};
    (data || []).forEach(r => { cfg[r.key] = r.value; });
    if (!creds.anthropic_key && cfg.anthropic_api_key) creds.anthropic_key = cfg.anthropic_api_key;
    if (!creds.owner_phone && cfg.owner_whatsapp) creds.owner_phone = cfg.owner_whatsapp;
    if (!creds.twilio_sid && cfg.twilio_account_sid) creds.twilio_sid = cfg.twilio_account_sid;
    if (!creds.twilio_token && cfg.twilio_auth_token) creds.twilio_token = cfg.twilio_auth_token;
    if (!creds.twilio_from && cfg.twilio_whatsapp_from) creds.twilio_from = cfg.twilio_whatsapp_from;
  } catch (e) { /* bot_config might not exist */ }

  return creds;
}

async function sendWhatsApp(to, body, creds) {
  if (!creds.twilio_sid || !creds.twilio_token || !creds.twilio_from) return false;
  const toFormatted = to.startsWith('whatsapp:') ? to : `whatsapp:${to}`;
  const auth = Buffer.from(`${creds.twilio_sid}:${creds.twilio_token}`).toString('base64');

  try {
    await fetch(`https://api.twilio.com/2010-04-01/Accounts/${creds.twilio_sid}/Messages.json`, {
      method: 'POST',
      headers: {
        'Authorization': `Basic ${auth}`,
        'Content-Type': 'application/x-www-form-urlencoded'
      },
      body: new URLSearchParams({
        From: creds.twilio_from.startsWith('whatsapp:') ? creds.twilio_from : `whatsapp:${creds.twilio_from}`,
        To: toFormatted,
        Body: body
      })
    });
    return true;
  } catch (e) { return false; }
}

async function sendOutreachEmail(venue, message) {
  if (!venue.email || !process.env.SMTP_HOST) return false;

  try {
    const nodemailer = require('nodemailer');
    const transporter = nodemailer.createTransport({
      host: process.env.SMTP_HOST,
      port: parseInt(process.env.SMTP_PORT || '587'),
      secure: false,
      auth: {
        user: process.env.SMTP_USER,
        pass: process.env.SMTP_PASS
      }
    });

    await transporter.sendMail({
      from: process.env.SMTP_FROM || process.env.SMTP_USER,
      to: venue.email,
      subject: 'Live Klaviermusik für Ihre Location — Ehemaliger Regensburger Domspatze',
      text: message,
      html: `<div style="font-family:Georgia,serif;max-width:600px;margin:0 auto;padding:40px 20px;color:#333">
        <p style="font-size:11px;letter-spacing:2px;text-transform:uppercase;color:#c9a84c;margin-bottom:24px">Live Piano — Eine Reise durch mein Leben</p>
        ${message.split('\n').map(l => l.trim() ? `<p style="line-height:1.8;margin-bottom:8px">${l}</p>` : '<br>').join('')}
        <hr style="border:none;border-top:1px solid #eee;margin:32px 0">
        <p style="font-size:12px;color:#999">
          <a href="https://thetoysareout.com/live" style="color:#c9a84c">thetoysareout.com/live</a>
        </p>
      </div>`
    });
    return true;
  } catch (e) {
    console.error('Email send error:', e);
    return false;
  }
}
