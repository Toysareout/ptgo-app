const adapt = require('./_adapter');
const { handler } = require('../netlify/functions/nerve-brain');
module.exports = adapt(handler);
