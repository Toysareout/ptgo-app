const adapt = require('./_adapter');
const { handler } = require('../netlify/functions/bot-broadcast');
module.exports = adapt(handler);
