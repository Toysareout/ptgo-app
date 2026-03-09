// Netlify serverless function: handles PTGO heal session applications
// Saves to Supabase and sends email notification

const { createClient } = require('@supabase/supabase-js');
const nodemailer = require('nodemailer');

const ALLOWED_ORIGINS = [
  'https://thetoysareout.com',
  'https://www.thetoysareout.com',
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

    // Send notification email via SMTP
    if (process.env.SMTP_USER && process.env.SMTP_PASS) {
      try {
        await sendNotification({ name, email, phone, reason, duration, tried });
      } catch (mailErr) {
        console.error('Email notification failed:', mailErr.message);
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

async function sendNotification({ name, email, phone, reason, duration, tried }) {
  const transporter = nodemailer.createTransport({
    host: process.env.SMTP_HOST || 'mail.gmx.net',
    port: parseInt(process.env.SMTP_PORT || '587'),
    secure: false,
    auth: {
      user: process.env.SMTP_USER,
      pass: process.env.SMTP_PASS,
    },
  });

  await transporter.sendMail({
    from: process.env.SMTP_USER,
    to: 'thetoysareout@gmx.de',
    subject: `Neue PTGO Bewerbung von ${name}`,
    text: [
      `Neue Bewerbung eingegangen:`,
      ``,
      `Name: ${name}`,
      `E-Mail: ${email}`,
      `Telefon: ${phone || 'nicht angegeben'}`,
      ``,
      `Was führt hierher: ${reason}`,
      `Dauer des Problems: ${duration || 'nicht angegeben'}`,
      `Bereits versucht: ${tried || 'nicht angegeben'}`,
      ``,
      `— PTGO Method Formular`,
    ].join('\n'),
  });
}
