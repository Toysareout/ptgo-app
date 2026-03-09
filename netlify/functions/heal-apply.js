// Netlify serverless function: handles PTGO heal session applications
// Saves to Supabase and sends email notification

const { createClient } = require('@supabase/supabase-js');

const ALLOWED_ORIGINS = [
  'https://thetoysareout.de',
  'https://www.thetoysareout.de',
  'http://localhost:8888',
  'http://127.0.0.1:8888',
];

exports.handler = async (event) => {
  const origin = event.headers.origin || '';
  const corsOrigin = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];

  const headers = {
    'Access-Control-Allow-Origin': corsOrigin,
    'Access-Control-Allow-Headers': 'Content-Type',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
  };

  if (event.httpMethod === 'OPTIONS') {
    return { statusCode: 204, headers };
  }

  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, headers, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  try {
    const { name, email, phone, reason, duration, tried, portal_entry, source } = JSON.parse(event.body);

    if (!name || !email || !reason) {
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Missing required fields' }) };
    }

    // Basic email validation
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Invalid email' }) };
    }

    const supabase = createClient(
      process.env.SUPABASE_URL,
      process.env.SUPABASE_SERVICE_KEY
    );

    const { data, error } = await supabase.from('heal_applications').insert({
      name,
      email,
      phone: phone || null,
      reason,
      duration: duration || null,
      tried: tried || null,
      portal_entry: portal_entry || 'direct',
      source: source || 'direct',
      status: 'new',
    }).select().single();

    if (error) {
      console.error('Supabase error:', error.message);
      return { statusCode: 500, headers, body: JSON.stringify({ error: 'Save failed' }) };
    }

    // Send notification email via SMTP (if configured)
    if (process.env.SMTP_HOST) {
      try {
        await sendNotification({ name, email, reason, duration, tried });
      } catch (mailErr) {
        console.error('Email notification failed:', mailErr.message);
        // Don't fail the request if email fails
      }
    }

    return {
      statusCode: 200,
      headers,
      body: JSON.stringify({ success: true, id: data.id }),
    };
  } catch (err) {
    console.error('Application error:', err.message);
    return { statusCode: 500, headers, body: JSON.stringify({ error: 'Application failed' }) };
  }
};

// Simple SMTP notification (no external dependency, uses fetch to mailgun/sendgrid if configured)
async function sendNotification({ name, email, reason, duration, tried }) {
  // Uses Supabase Edge Function or webhook for notifications
  // This is a placeholder — wire up your preferred email service
  console.log(`New PTGO application from ${name} (${email})`);
}
