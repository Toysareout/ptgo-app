const adaptRaw = require('./_raw-adapter');
const { handler } = require('../netlify/functions/bot-stripe-webhook');
module.exports = adaptRaw(handler);
