const adaptRaw = require('./_raw-adapter');
const { handler } = require('../netlify/functions/whatsapp-bot');
module.exports = adaptRaw(handler);
