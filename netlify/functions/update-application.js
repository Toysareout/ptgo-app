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
    const { password, id, status, notes } = JSON.parse(event.body);

    const adminPw = process.env.ADMIN_PASSWORD || 'Toy1';
    if (!password || password !== adminPw) {
      return { statusCode: 401, headers, body: JSON.stringify({ error: 'Falsches Passwort' }) };
    }

    if (!id) {
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Missing application id' }) };
    }

    const validStatuses = ['new', 'contacted', 'call_scheduled', 'booked', 'completed'];
    if (status && !validStatuses.includes(status)) {
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Invalid status. Must be one of: ' + validStatuses.join(', ') }) };
    }

    const supabase = createClient(
      process.env.SUPABASE_URL || 'https://pwdhxarvemcgkhhnvbng.supabase.co',
      process.env.SUPABASE_SERVICE_KEY
    );

    const updates = {};
    if (status) updates.status = status;
    if (notes !== undefined) updates.notes = notes;

    if (Object.keys(updates).length === 0) {
      return { statusCode: 400, headers, body: JSON.stringify({ error: 'Nothing to update. Provide status and/or notes.' }) };
    }

    const { data, error } = await supabase
      .from('heal_applications')
      .update(updates)
      .eq('id', id)
      .select()
      .single();

    if (error) {
      return { statusCode: 500, headers, body: JSON.stringify({ error: error.message }) };
    }

    return { statusCode: 200, headers, body: JSON.stringify(data) };
  } catch (err) {
    return { statusCode: 500, headers, body: JSON.stringify({ error: err.message }) };
  }
};
