const adapt = require('./_adapter');
const { handler } = require('../netlify/functions/bot-evolve');
module.exports = adapt(handler);
