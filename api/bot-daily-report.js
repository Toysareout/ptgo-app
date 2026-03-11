const adapt = require('./_adapter');
const { handler } = require('../netlify/functions/bot-daily-report');
module.exports = adapt(handler);
