// Adapter: wraps Netlify function handlers for Vercel serverless
module.exports = function adapt(netlifyHandler) {
  return async (req, res) => {
    let body;
    if (Buffer.isBuffer(req.body)) {
      body = req.body.toString('utf-8');
    } else if (typeof req.body === 'string') {
      body = req.body;
    } else if (req.body && typeof req.body === 'object') {
      body = JSON.stringify(req.body);
    } else {
      body = null;
    }

    const event = {
      httpMethod: req.method,
      headers: req.headers,
      body,
      queryStringParameters: req.query,
    };

    try {
      const result = await netlifyHandler(event);
      if (result.headers) {
        for (const [k, v] of Object.entries(result.headers)) {
          res.setHeader(k, v);
        }
      }
      res.status(result.statusCode || 200).send(result.body || '');
    } catch (err) {
      console.error('Handler error:', err);
      res.status(500).json({ error: err.message });
    }
  };
};
