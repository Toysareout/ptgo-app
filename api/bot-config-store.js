const adapt = require('./_adapter');
const { handler } = require('../netlify/functions/bot-config-store');
module.exports = adapt(handler);
