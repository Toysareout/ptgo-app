// ============================================================
// BOT CONFIG STORE — Saves/reads API keys from Supabase
// No Netlify env vars needed (except SUPABASE_URL + KEY)
// Keys entered via bot-admin UI → stored in Supabase bot_config
// ============================================================

const { createClient } = require('@supabase/supabase-js');

// These two MUST be set — either as env vars or passed via headers
function getSupabase(event) {
  const url = process.env.SUPABASE_URL || event.headers['x-supabase-url'] || 'https://pwdhxarvemcgkhhnvbng.supabase.co';
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.SUPABASE_SERVICE_KEY || event.headers['x-supabase-key'] || '';
  if (!url || !key) return null;
  return { client: createClient(url, key), url, key };
}

const ALLOWED_ORIGINS = [
  'https://thetoysareout.com',
  'https://www.thetoysareout.com',
  'http://localhost:8888',
  'http://127.0.0.1:8888',
];

// Config keys that can be stored
const VALID_KEYS = [
  'twilio_account_sid', 'twilio_auth_token', 'twilio_whatsapp_from',
  'anthropic_api_key', 'stripe_secret_key', 'stripe_webhook_secret',
  'owner_whatsapp', 'personality', 'auto_reply', 'sales_mode',
  'working_hours', 'vip_thresholds', 'daily_report', 'products'
];

exports.handler = async (event) => {
  const origin = event.headers.origin || '';
  const corsOrigin = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  const headers = {
    'Access-Control-Allow-Origin': corsOrigin,
    'Access-Control-Allow-Headers': 'Content-Type, x-supabase-url, x-supabase-key',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Content-Type': 'application/json'
  };

  if (event.httpMethod === 'OPTIONS') return { statusCode: 204, headers };
  if (event.httpMethod !== 'POST') return { statusCode: 405, headers, body: '{"error":"POST only"}' };

  try {
    const { action, configs } = JSON.parse(event.body || '{}');
    const supa = getSupabase(event);

    if (!supa) {
      return {
        statusCode: 400, headers,
        body: JSON.stringify({ error: 'Supabase nicht verbunden. Bitte SUPABASE_URL und SUPABASE_SERVICE_ROLE_KEY in Netlify setzen, oder über die Header senden.' })
      };
    }

    // ---- SAVE CONFIG ----
    if (action === 'save' && configs) {
      const results = {};

      for (const [key, value] of Object.entries(configs)) {
        if (!VALID_KEYS.includes(key)) {
          results[key] = { status: 'skipped', reason: 'invalid key' };
          continue;
        }

        // Store as JSON value
        const jsonValue = typeof value === 'string' ? JSON.stringify(value) : JSON.stringify(value);

        const { error } = await supa.client
          .from('bot_config')
          .upsert({
            key,
            value: typeof value === 'object' ? value : value,
            updated_at: new Date().toISOString()
          }, { onConflict: 'key' });

        results[key] = error ? { status: 'error', message: error.message } : { status: 'saved' };
      }

      return { statusCode: 200, headers, body: JSON.stringify({ status: 'ok', results }) };
    }

    // ---- LOAD ALL CONFIG ----
    if (action === 'load') {
      const { data, error } = await supa.client
        .from('bot_config')
        .select('key, value, updated_at');

      if (error) {
        return { statusCode: 400, headers, body: JSON.stringify({ error: error.message }) };
      }

      // Build config object, mask sensitive values for display
      const config = {};
      const masked = {};
      (data || []).forEach(row => {
        config[row.key] = row.value;
        // Mask API keys for display
        const val = typeof row.value === 'string' ? row.value : JSON.stringify(row.value);
        if (row.key.includes('key') || row.key.includes('token') || row.key.includes('secret') || row.key.includes('sid')) {
          masked[row.key] = val.length > 8 ? val.slice(0, 4) + '••••' + val.slice(-4) : '••••••••';
        } else {
          masked[row.key] = val;
        }
      });

      return { statusCode: 200, headers, body: JSON.stringify({ config: masked, keys: Object.keys(config) }) };
    }

    // ---- TEST CONNECTION ----
    if (action === 'test') {
      // Test Supabase by checking if bot_config table exists
      const { error } = await supa.client.from('bot_config').select('key').limit(1);

      if (error && error.message.includes('does not exist')) {
        return { statusCode: 200, headers, body: JSON.stringify({ status: 'no_tables', message: 'Supabase verbunden, aber Tabellen fehlen noch.' }) };
      }

      if (error) {
        return { statusCode: 200, headers, body: JSON.stringify({ status: 'error', message: error.message }) };
      }

      return { statusCode: 200, headers, body: JSON.stringify({ status: 'ok', message: 'Supabase verbunden und Tabellen vorhanden!' }) };
    }

    // ---- DELETE CONFIG KEY ----
    if (action === 'delete' && configs) {
      for (const key of Object.keys(configs)) {
        await supa.client.from('bot_config').delete().eq('key', key);
      }
      return { statusCode: 200, headers, body: JSON.stringify({ status: 'ok' }) };
    }

    return { statusCode: 400, headers, body: '{"error":"Unknown action. Use: save, load, test, delete"}' };

  } catch (err) {
    console.error('Config store error:', err);
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};

// ============================================================
// SHARED HELPER — Used by other functions to read config
// Call getConfig(supabaseClient) to get all stored credentials
// ============================================================
module.exports.getConfig = async function getConfig(supabaseClient) {
  try {
    const { data } = await supabaseClient.from('bot_config').select('key, value');
    const config = {};
    (data || []).forEach(row => { config[row.key] = row.value; });
    return config;
  } catch (e) {
    return {};
  }
};
