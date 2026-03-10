// ============================================================
// STRIPE WEBHOOK — Tracks completed payments from bot sales
// ============================================================

const { createClient } = require('@supabase/supabase-js');

const supabase = createClient(
  process.env.SUPABASE_URL || 'https://pwdhxarvemcgkhhnvbng.supabase.co',
  process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_SERVICE_KEY || ''
);

const STRIPE_SECRET = process.env.STRIPE_SECRET_KEY || '';
const STRIPE_WEBHOOK_SECRET = process.env.STRIPE_WEBHOOK_SECRET || '';
const TWILIO_SID = process.env.TWILIO_ACCOUNT_SID || '';
const TWILIO_TOKEN = process.env.TWILIO_AUTH_TOKEN || '';
const TWILIO_FROM = process.env.TWILIO_WHATSAPP_FROM || '';
const OWNER_PHONE = process.env.OWNER_WHATSAPP || process.env.TWILIO_WHATSAPP_TO || '';

exports.handler = async (event) => {
  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, body: 'Method not allowed' };
  }

  try {
    const stripe = require('stripe')(STRIPE_SECRET);
    let stripeEvent;

    // Verify webhook signature if secret is configured
    if (STRIPE_WEBHOOK_SECRET) {
      const sig = event.headers['stripe-signature'];
      stripeEvent = stripe.webhooks.constructEvent(event.body, sig, STRIPE_WEBHOOK_SECRET);
    } else {
      stripeEvent = JSON.parse(event.body);
    }

    if (stripeEvent.type === 'checkout.session.completed') {
      const session = stripeEvent.data.object;
      const meta = session.metadata || {};

      // Only process bot-originated sales
      if (meta.source === 'whatsapp_bot' && meta.fan_phone) {
        const phone = meta.fan_phone;
        const fanId = meta.fan_id;

        // Update sale record
        await supabase.from('bot_sales').update({
          stripe_session_id: session.id,
          stripe_payment_status: 'paid'
        }).eq('fan_id', fanId).eq('stripe_payment_status', 'pending');

        // Update fan purchase stats
        const { data: fan } = await supabase
          .from('fans')
          .select('total_purchases, total_spent_cents, name')
          .eq('id', fanId)
          .single();

        if (fan) {
          await supabase.from('fans').update({
            total_purchases: (fan.total_purchases || 0) + 1,
            total_spent_cents: (fan.total_spent_cents || 0) + (session.amount_total || 0),
            last_purchase_at: new Date().toISOString()
          }).eq('id', fanId);

          // Send thank you via WhatsApp
          const thankYou = `🙏 Danke für deinen Kauf, ${fan.name || 'Bro'}!\n\nDein Support bedeutet alles. Du bist jetzt offiziell Teil der TTAO-Family! 🔥\n\nBei Fragen bin ich immer hier.`;
          await sendWhatsApp(phone, thankYou);

          // Log the thank you message
          await supabase.from('conversations').insert({
            fan_id: fanId,
            phone,
            direction: 'outbound',
            body: thankYou,
            message_type: 'text',
            response_strategy: 'post_purchase'
          });
        }

        // Notify owner
        if (OWNER_PHONE) {
          const amount = ((session.amount_total || 0) / 100).toFixed(2);
          await sendWhatsApp(OWNER_PHONE,
            `💰 Neue Zahlung via Bot!\n\nFan: ${fan?.name || phone}\nBetrag: €${amount}\nProdukt: ${meta.item_type || 'unknown'}`
          );
        }

        // Update daily analytics
        const today = new Date().toISOString().split('T')[0];
        const { data: analytics } = await supabase
          .from('bot_analytics')
          .select('*')
          .eq('date', today)
          .single();

        if (analytics) {
          await supabase.from('bot_analytics').update({
            total_sales: (analytics.total_sales || 0) + 1,
            total_revenue_cents: (analytics.total_revenue_cents || 0) + (session.amount_total || 0)
          }).eq('date', today);
        }
      }
    }

    return { statusCode: 200, body: JSON.stringify({ received: true }) };

  } catch (err) {
    console.error('Stripe webhook error:', err);
    return { statusCode: 400, body: JSON.stringify({ error: err.message }) };
  }
};

async function sendWhatsApp(to, body) {
  if (!TWILIO_SID || !TWILIO_TOKEN || !TWILIO_FROM) return;

  const toFormatted = to.startsWith('whatsapp:') ? to : `whatsapp:${to}`;
  const auth = Buffer.from(`${TWILIO_SID}:${TWILIO_TOKEN}`).toString('base64');

  await fetch(`https://api.twilio.com/2010-04-01/Accounts/${TWILIO_SID}/Messages.json`, {
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
}
