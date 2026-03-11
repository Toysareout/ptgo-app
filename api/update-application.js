const adapt = require('./_adapter');
const { handler } = require('../netlify/functions/update-application');
module.exports = adapt(handler);
