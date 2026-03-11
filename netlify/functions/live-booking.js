// Netlify serverless function: handles Live Piano booking requests
// Sends email notification to thetoysareout@gmx.de

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
    const { name, email, phone, occasion, date, location, guests, message, audience_wish } = JSON.parse(event.body);

    if (!name || !email || !occasion || !date || !location) {
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Fehlende Pflichtfelder' }) };
    }

    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Ungültige E-Mail' }) };
    }

    // Save to Supabase if available
    if (process.env.SUPABASE_URL && process.env.SUPABASE_SERVICE_KEY) {
      try {
        const { createClient } = require('@supabase/supabase-js');
        const supabase = createClient(process.env.SUPABASE_URL, process.env.SUPABASE_SERVICE_KEY);
        await supabase.from('bot_bookings').insert({
          phone: phone || null,
          booking_type: 'live_piano',
          description: [
            `Name: ${name}`,
            `Email: ${email}`,
            `Anlass: ${occasion}`,
            `Datum: ${date}`,
            `Ort: ${location}`,
            `Gäste: ${guests || 'k.A.'}`,
            `Publikumswunsch: ${audience_wish || '-'}`,
            `Nachricht: ${message || '-'}`,
          ].join('\n'),
          preferred_date: date || null,
          status: 'new',
        });
      } catch (dbErr) {
        console.error('Supabase save failed:', dbErr.message);
      }
    }

    // Send email notification
    let emailSent = false;
    if (process.env.SMTP_USER && process.env.SMTP_PASS) {
      try {
        const transporter = nodemailer.createTransport({
          host: process.env.SMTP_HOST || 'mail.gmx.net',
          port: parseInt(process.env.SMTP_PORT || '587'),
          secure: false,
          auth: {
            user: process.env.SMTP_USER,
            pass: process.env.SMTP_PASS,
          },
        });

        const occasionLabels = {
          private_dinner: 'Privates Dinner',
          birthday: 'Geburtstag / Jubiläum',
          corporate: 'Firmenevent / Gala',
          hotel: 'Hotel / Restaurant',
          vernissage: 'Vernissage / Ausstellung',
          wedding: 'Hochzeit',
          charity: 'Charity Event',
          other: 'Anderer Anlass',
        };

        await transporter.sendMail({
          from: process.env.SMTP_USER,
          to: 'thetoysareout@gmx.de',
          subject: `🎹 Neue Live Piano Anfrage — ${occasionLabels[occasion] || occasion} am ${date}`,
          text: [
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
            '🎹 NEUE LIVE PIANO ANFRAGE',
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
            '',
            `Name:     ${name}`,
            `E-Mail:   ${email}`,
            `Telefon:  ${phone || 'nicht angegeben'}`,
            '',
            `Anlass:   ${occasionLabels[occasion] || occasion}`,
            `Datum:    ${date}`,
            `Ort:      ${location}`,
            `Gäste:    ${guests || 'nicht angegeben'}`,
            '',
            `Publikumswunsch: ${audience_wish || 'keiner'}`,
            '',
            `Nachricht:`,
            message || '(keine Nachricht)',
            '',
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
            'Antworte innerhalb von 24h!',
            '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━',
          ].join('\n'),
        });
        emailSent = true;
      } catch (mailErr) {
        console.error('Email notification failed:', mailErr.message);
      }
    }

    // Send WhatsApp notification via Twilio
    if (process.env.TWILIO_ACCOUNT_SID && process.env.TWILIO_AUTH_TOKEN && process.env.TWILIO_WHATSAPP_TO) {
      try {
        const sid = process.env.TWILIO_ACCOUNT_SID;
        const token = process.env.TWILIO_AUTH_TOKEN;
        const from = process.env.TWILIO_WHATSAPP_FROM;
        const to = process.env.TWILIO_WHATSAPP_TO;

        const msg = `🎹 Neue Live Piano Anfrage\n\nName: ${name}\nE-Mail: ${email}\nTelefon: ${phone || '-'}\nAnlass: ${occasion}\nDatum: ${date}\nOrt: ${location}\nGäste: ${guests || '-'}`;

        const params = new URLSearchParams();
        params.append('From', `whatsapp:${from}`);
        params.append('To', `whatsapp:${to}`);
        params.append('Body', msg);

        await fetch(`https://api.twilio.com/2010-04-01/Accounts/${sid}/Messages.json`, {
          method: 'POST',
          headers: {
            'Authorization': 'Basic ' + Buffer.from(`${sid}:${token}`).toString('base64'),
            'Content-Type': 'application/x-www-form-urlencoded',
          },
          body: params.toString(),
        });
      } catch (waErr) {
        console.error('WhatsApp notification failed:', waErr.message);
      }
    }

    return {
      statusCode: 200,
      headers,
      body: JSON.stringify({ success: true, emailSent }),
    };
  } catch (err) {
    console.error('Booking error:', err.message);
    return { statusCode: 500, headers, body: JSON.stringify({ error: 'Anfrage fehlgeschlagen' }) };
  }
};
