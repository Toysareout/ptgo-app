const adapt = require('./_adapter');
const { handler } = require('../netlify/functions/live-booking');
module.exports = adapt(handler);
