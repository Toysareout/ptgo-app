const adapt = require('./_adapter');
const { handler } = require('../netlify/functions/get-applications');
module.exports = adapt(handler);
