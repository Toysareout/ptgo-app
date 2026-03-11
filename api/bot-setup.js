const adapt = require('./_adapter');
const { handler } = require('../netlify/functions/bot-setup');
module.exports = adapt(handler);
