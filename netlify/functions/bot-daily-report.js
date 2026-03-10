// ============================================================
// DAILY REPORT — Sends morning summary via WhatsApp
// Triggered by Netlify scheduled function (cron) or manual call
// ============================================================

const { createClient } = require('@supabase/supabase-js');

const supabase = createClient(
  process.env.SUPABASE_URL || '',
  process.env.SUPABASE_SERVICE_ROLE_KEY || ''
);

const TWILIO_SID = process.env.TWILIO_ACCOUNT_SID || '';
const TWILIO_TOKEN = process.env.TWILIO_AUTH_TOKEN || '';
const TWILIO_FROM = process.env.TWILIO_WHATSAPP_FROM || '';
const OWNER_PHONE = process.env.OWNER_WHATSAPP || '';

// Netlify scheduled function config
exports.config = {
  schedule: '0 9 * * *' // Every day at 09:00 UTC
};

exports.handler = async (event) => {
  if (!OWNER_PHONE) {
    return { statusCode: 200, body: JSON.stringify({ message: 'No owner phone configured' }) };
  }

  try {
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const yesterdayStr = yesterday.toISOString().split('T')[0];

    // Fetch yesterday's analytics
    const { data: analytics } = await supabase
      .from('bot_analytics')
      .select('*')
      .eq('date', yesterdayStr)
      .single();

    // Count new fans from yesterday
    const { count: newFans } = await supabase
      .from('fans')
      .select('*', { count: 'exact', head: true })
      .gte('created_at', yesterday.toISOString())
      .lt('created_at', today.toISOString());

    // Count total fans
    const { count: totalFans } = await supabase
      .from('fans')
      .select('*', { count: 'exact', head: true });

    // Get top fans (most messages yesterday)
    const { data: topFans } = await supabase
      .from('conversations')
      .select('phone, fan_id')
      .eq('direction', 'inbound')
      .gte('created_at', yesterday.toISOString())
      .lt('created_at', today.toISOString());

    const fanCounts = {};
    (topFans || []).forEach(c => {
      fanCounts[c.phone] = (fanCounts[c.phone] || 0) + 1;
    });
    const topFansList = Object.entries(fanCounts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3);

    // Get pending bookings
    const { count: pendingBookings } = await supabase
      .from('bot_bookings')
      .select('*', { count: 'exact', head: true })
      .eq('status', 'new');

    // Get sales from yesterday
    const { data: sales } = await supabase
      .from('bot_sales')
      .select('*')
      .eq('stripe_payment_status', 'paid')
      .gte('created_at', yesterday.toISOString())
      .lt('created_at', today.toISOString());

    const totalRevenue = (sales || []).reduce((sum, s) => sum + (s.price_cents || 0), 0);

    // VIP fans count
    const { count: vipCount } = await supabase
      .from('fans')
      .select('*', { count: 'exact', head: true })
      .gte('vip_score', 80);

    // Build report
    const a = analytics || {};
    const report = `📊 *TTAO BOT — Daily Report*
📅 ${yesterdayStr}

━━━━━━━━━━━━━━━━━━━━
📬 *Messages*
  Eingehend: ${a.total_messages_in || 0}
  Gesendet: ${a.total_messages_out || 0}
  Avg Response: ${a.avg_response_time_ms || 0}ms

👥 *Fans*
  Gesamt: ${totalFans || 0}
  Neu gestern: ${newFans || 0}
  VIPs (80+): ${vipCount || 0}

💰 *Sales*
  Verkäufe: ${(sales || []).length}
  Umsatz: €${(totalRevenue / 100).toFixed(2)}
  Checkout-Links: ${a.checkout_links_sent || 0}

📅 *Bookings*
  Neue Anfragen: ${a.booking_requests || 0}
  Offene Bookings: ${pendingBookings || 0}

🔝 *Top Fans gestern*
${topFansList.length > 0
  ? topFansList.map((f, i) => `  ${i + 1}. ${f[0]} (${f[1]} Nachrichten)`).join('\n')
  : '  Keine Aktivität'}
━━━━━━━━━━━━━━━━━━━━

💡 Tipp: Antworte auf offene Bookings unter thetoysareout.com/bot-admin`;

    // Send via Twilio
    await sendWhatsApp(OWNER_PHONE, report);

    // Update analytics with fan counts
    await supabase.from('bot_analytics').upsert({
      date: yesterdayStr,
      unique_fans: Object.keys(fanCounts).length,
      new_fans: newFans || 0,
      total_sales: (sales || []).length,
      total_revenue_cents: totalRevenue
    }, { onConflict: 'date' });

    return {
      statusCode: 200,
      body: JSON.stringify({ message: 'Daily report sent', date: yesterdayStr })
    };

  } catch (err) {
    console.error('Daily report error:', err);
    return {
      statusCode: 500,
      body: JSON.stringify({ error: 'Report failed', details: err.message })
    };
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
