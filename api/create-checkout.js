const adapt = require('./_adapter');
const { handler } = require('../netlify/functions/create-checkout');
module.exports = adapt(handler);
