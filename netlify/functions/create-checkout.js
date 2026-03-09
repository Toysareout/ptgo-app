// Netlify serverless function: creates a Stripe Checkout session
// Supports drops, tickets, merch, and premium sessions

const stripe = require('stripe')(process.env.STRIPE_SECRET_KEY);

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
    const { item_type, item_name, price, quantity = 1 } = JSON.parse(event.body);

    if (!item_type || !item_name || !price) {
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Missing fields' }) };
    }

    // Validate price range per type
    const limits = {
      drop: { min: 5, max: 100 },
      ticket: { min: 3, max: 50 },
      merch: { min: 10, max: 200 },
      session: { min: 100, max: 10000 },
      photo: { min: 1, max: 15 },
    };
    const limit = limits[item_type] || { min: 1, max: 10000 };
    const cents = Math.round(price * 100);

    if (cents < limit.min * 100 || cents > limit.max * 100) {
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Invalid price' }) };
    }

    const session = await stripe.checkout.sessions.create({
      payment_method_types: ['card'],
      mode: 'payment',
      line_items: [{
        price_data: {
          currency: 'eur',
          unit_amount: cents,
          product_data: {
            name: item_name,
            description: `THETOYSAREOUT — ${item_type.toUpperCase()}`,
          },
        },
        quantity,
      }],
      success_url: `${corsOrigin}?purchased=${item_type}&success=1`,
      cancel_url: `${corsOrigin}?cancelled=1`,
      metadata: {
        item_type,
        item_name,
      },
    });

    return {
      statusCode: 200,
      headers,
      body: JSON.stringify({ url: session.url }),
    };
  } catch (err) {
    console.error('Stripe error:', err.message);
    return {
      statusCode: 500,
      headers,
      body: JSON.stringify({ error: 'Checkout failed' }),
    };
  }
};
