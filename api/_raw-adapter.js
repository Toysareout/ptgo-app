// Raw body adapter for Vercel: used by endpoints that need raw body
// (Stripe webhooks with signature verification, Twilio form-urlencoded)
module.exports = function adaptRaw(netlifyHandler) {
  const fn = async (req, res) => {
    const body = await new Promise((resolve, reject) => {
      let data = '';
      req.on('data', chunk => { data += chunk; });
      req.on('end', () => resolve(data));
      req.on('error', reject);
    });

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

  fn.config = { api: { bodyParser: false } };
  return fn;
};
