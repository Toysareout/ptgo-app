const { createClient } = require('@supabase/supabase-js');

exports.handler = async (event) => {
  const headers = {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Headers': 'Content-Type',
  };

  if (event.httpMethod === 'OPTIONS') {
    return { statusCode: 204, headers };
  }

  if (event.httpMethod !== 'POST') {
    return { statusCode: 405, headers, body: JSON.stringify({ error: 'Method not allowed' }) };
  }

  try {
    const { password } = JSON.parse(event.body);

    const adminPw = process.env.ADMIN_PASSWORD || 'Toy1';
    if (!password || password !== adminPw) {
      return { statusCode: 401, headers, body: JSON.stringify({ error: 'Falsches Passwort' }) };
    }

    const supabase = createClient(
      process.env.SUPABASE_URL || 'https://pwdhxarvemcgkhhnvbng.supabase.co',
      process.env.SUPABASE_SERVICE_KEY
    );

    const { data, error } = await supabase
      .from('heal_applications')
      .select('*')
      .order('created_at', { ascending: false })
      .limit(50);

    if (error) {
      return { statusCode: 500, headers, body: JSON.stringify({ error: error.message }) };
    }

    return { statusCode: 200, headers, body: JSON.stringify(data) };
  } catch (err) {
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};
