// Dynamic router: replaces individual wrapper files to stay under Vercel's 12-function limit
const adapt = require('./_adapter');
const adaptRaw = require('./_raw-adapter');

// Endpoints that need raw body (Stripe signature verification, Twilio form-urlencoded)
const RAW_ENDPOINTS = new Set(['whatsapp-bot', 'bot-stripe-webhook']);

module.exports = async (req, res) => {
  // Extract function name from URL path: /api/bot-broadcast -> bot-broadcast
  const parts = req.url.split('?')[0].split('/').filter(Boolean);
  // parts: ['api', 'function-name'] or just ['function-name']
  const funcName = parts[parts.length - 1];

  if (!funcName || funcName.startsWith('_')) {
    return res.status(404).json({ error: 'Not found' });
  }

  let handler;
  try {
    handler = require(`../netlify/functions/${funcName}`).handler;
  } catch (e) {
    return res.status(404).json({ error: `Function ${funcName} not found` });
  }

  if (RAW_ENDPOINTS.has(funcName)) {
    const wrapped = adaptRaw(handler);
    return wrapped(req, res);
  } else {
    const wrapped = adapt(handler);
    return wrapped(req, res);
  }
};
