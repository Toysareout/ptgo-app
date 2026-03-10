// ============================================================
// BROADCAST ENGINE — Send targeted messages to fan segments
// ============================================================

const { createClient } = require('@supabase/supabase-js');

const supabase = createClient(
  process.env.SUPABASE_URL || 'https://pwdhxarvemcgkhhnvbng.supabase.co',
  process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_SERVICE_KEY || ''
);

const TWILIO_SID = process.env.TWILIO_ACCOUNT_SID || '';
const TWILIO_TOKEN = process.env.TWILIO_AUTH_TOKEN || '';
const TWILIO_FROM = process.env.TWILIO_WHATSAPP_FROM || '';

const ALLOWED_ORIGINS = [
  'https://thetoysareout.com',
  'https://www.thetoysareout.com',
  'http://localhost:8888',
];

exports.handler = async (event) => {
  const origin = event.headers.origin || '';
  const corsOrigin = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  const headers = {
    'Access-Control-Allow-Origin': corsOrigin,
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
  };

  if (event.httpMethod === 'OPTIONS') return { statusCode: 204, headers };
  if (event.httpMethod !== 'POST') return { statusCode: 405, headers, body: '{"error":"Method not allowed"}' };

  try {
    const { action, broadcast_id, title, body: msgBody, target_tiers, target_interests, media_url } = JSON.parse(event.body);

    // CREATE a new broadcast
    if (action === 'create') {
      const { data: broadcast } = await supabase
        .from('broadcasts')
        .insert({
          title: title || 'Untitled',
          body: msgBody,
          media_url: media_url || null,
          target_tier: target_tiers || ['new', 'casual', 'engaged', 'superfan', 'vip', 'whale'],
          target_interests: target_interests || null,
          status: 'draft'
        })
        .select()
        .single();

      return { statusCode: 200, headers, body: JSON.stringify(broadcast) };
    }

    // SEND a broadcast
    if (action === 'send' && broadcast_id) {
      const { data: broadcast } = await supabase
        .from('broadcasts')
        .select('*')
        .eq('id', broadcast_id)
        .single();

      if (!broadcast) return { statusCode: 404, headers, body: '{"error":"Broadcast not found"}' };
      if (broadcast.status === 'sent') return { statusCode: 400, headers, body: '{"error":"Already sent"}' };

      // Get target fans
      let query = supabase.from('fans').select('id, phone, name, tier').eq('opt_out', false);

      if (broadcast.target_tier && broadcast.target_tier.length > 0) {
        query = query.in('tier', broadcast.target_tier);
      }

      const { data: fans } = await query;

      if (!fans || fans.length === 0) {
        return { statusCode: 200, headers, body: JSON.stringify({ message: 'No fans to target', sent: 0 }) };
      }

      // Mark as sending
      await supabase.from('broadcasts').update({
        status: 'sending',
        total_recipients: fans.length
      }).eq('id', broadcast_id);

      // Send to all fans (with rate limiting)
      let delivered = 0;
      for (const fan of fans) {
        // Personalize message
        const personalMsg = broadcast.body
          .replace('{name}', fan.name || 'Bro')
          .replace('{tier}', fan.tier);

        const sent = await sendWhatsApp(fan.phone, personalMsg);
        if (sent) delivered++;

        // Log outbound message
        await supabase.from('conversations').insert({
          fan_id: fan.id,
          phone: fan.phone,
          direction: 'outbound',
          body: personalMsg,
          message_type: 'text',
          response_strategy: 'broadcast'
        });

        // Rate limit: 1 message per second (Twilio limit)
        await new Promise(r => setTimeout(r, 1100));
      }

      // Mark as sent
      await supabase.from('broadcasts').update({
        status: 'sent',
        sent_at: new Date().toISOString(),
        total_delivered: delivered
      }).eq('id', broadcast_id);

      return {
        statusCode: 200,
        headers,
        body: JSON.stringify({ message: 'Broadcast sent', total: fans.length, delivered })
      };
    }

    // LIST broadcasts
    if (action === 'list') {
      const { data } = await supabase
        .from('broadcasts')
        .select('*')
        .order('created_at', { ascending: false })
        .limit(20);

      return { statusCode: 200, headers, body: JSON.stringify(data) };
    }

    return { statusCode: 400, headers, body: '{"error":"Unknown action"}' };

  } catch (err) {
    console.error('Broadcast error:', err);
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};

async function sendWhatsApp(to, body) {
  if (!TWILIO_SID || !TWILIO_TOKEN || !TWILIO_FROM) return false;

  const toFormatted = to.startsWith('whatsapp:') ? to : `whatsapp:${to}`;
  const auth = Buffer.from(`${TWILIO_SID}:${TWILIO_TOKEN}`).toString('base64');

  try {
    const res = await fetch(`https://api.twilio.com/2010-04-01/Accounts/${TWILIO_SID}/Messages.json`, {
      method: 'POST',
      headers: {
        'Authorization': `Basic ${auth}`,
        'Content-Type': 'application/x-www-form-urlencoded'
      },
      body: new URLSearchParams({
        From: TWILIO_FROM.startsWith('whatsapp:') ? TWILIO_FROM : `whatsapp:${TWILIO_FROM}`,
        To: toFormatted,
        Body: body
      })
    });
    return res.ok;
  } catch (e) {
    console.error('Send error:', e);
    return false;
  }
}
