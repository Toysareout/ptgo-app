const adapt = require('./_adapter');
const { handler } = require('../netlify/functions/heal-apply');
module.exports = adapt(handler);
