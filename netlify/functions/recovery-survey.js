// Netlify serverless function: handles PTGO Flight Recovery survey submissions
// Saves to Supabase (if configured) and emails a notification.
// Phase 1 discovery — pilots self-report body load after flying.

const nodemailer = require('nodemailer');

const ALLOWED_ORIGINS = [
  'https://ptgo.de',
  'https://www.ptgo.de',
  'https://recovery.ptgo.de',
  'https://app.ptgo.de',
  'https://thetoysareout.com',
  'https://www.thetoysareout.com',
  'http://localhost:8888',
  'http://127.0.0.1:8888',
];

const NOTIFY_EMAIL = process.env.RECOVERY_NOTIFY_EMAIL || 'thetoysareout@gmx.de';

const labelList = (arr) => (Array.isArray(arr) && arr.length ? arr.join(', ') : '-');

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
    const data = JSON.parse(event.body || '{}');
    const {
      name = '',
      contact = '',
      disciplines = [],
      regions = [],
      onset = '',
      severity = '',
      current_solution = '',
      verbatim = '',
      wants_followup = false,
      source = '',
    } = data;

    // Minimal validation — research form, keep friction low. Need at least one signal.
    if (!regions.length && !verbatim && !disciplines.length) {
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Bitte mindestens eine Angabe machen.' }) };
    }

    const summary = [
      `Name: ${name || '(anonym)'}`,
      `Kontakt: ${contact || '-'}`,
      `Disziplin: ${labelList(disciplines)}`,
      `Betroffene Bereiche: ${labelList(regions)}`,
      `Beschwerden ab wann: ${onset || '-'}`,
      `Stärke (0-10): ${severity || '-'}`,
      `Aktuelle Lösung: ${current_solution || '-'}`,
      `In eigenen Worten: ${verbatim || '-'}`,
      `Rückmeldung erwünscht: ${wants_followup ? 'JA' : 'nein'}`,
      `Quelle: ${source || '-'}`,
    ].join('\n');

    // Save to Supabase if available (reuses existing bot_bookings table)
    if (process.env.SUPABASE_URL && process.env.SUPABASE_SERVICE_KEY) {
      try {
        const { createClient } = require('@supabase/supabase-js');
        const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);
        await supabase.from('bot_bookings').insert({
          phone: contact || null,
          booking_type: 'recovery_survey',
          description: summary,
          status: 'new',
        });
      } catch (dbErr) {
        console.error('Supabase save failed:', dbErr.message);
      }
    }

    // Email notification
    let emailSent = false;
    if (process.env.SMTP_USER && process.env.SMTP_PASS) {
      try {
        const transporter = nodemailer.createTransport({
          host: process.env.SMTP_HOST || 'mail.gmx.net',
          port: parseInt(process.env.SMTP_PORT || '587'),
          secure: false,
          auth: { user: process.env.SMTP_USER, pass: process.env.SMTP_PASS },
        });

        await transporter.sendMail({
          from: process.env.SMTP_USER,
          to: NOTIFY_EMAIL,
          subject: `🪂 Flight Recovery Check — ${name || 'anonym'}${wants_followup ? ' (Rückmeldung erwünscht!)' : ''}`,
          text: [
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
            '🪂 NEUER FLIGHT RECOVERY CHECK',
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
            '',
            summary,
            '',
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
            wants_followup ? '→ Pilot wünscht Rückmeldung. Innerhalb 24h antworten!' : '→ Reiner Forschungs-Datenpunkt.',
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
          ].join('\n'),
        });
        emailSent = true;
      } catch (mailErr) {
        console.error('Email notification failed:', mailErr.message);
      }
    }

    return { statusCode: 200, headers, body: JSON.stringify({ success: true, emailSent }) };
  } catch (err) {
    console.error('Recovery survey error:', err.message);
    return { statusCode: 500, headers, body: JSON.stringify({ error: 'Übermittlung fehlgeschlagen' }) };
  }
};
