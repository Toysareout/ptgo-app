const adapt = require('./_adapter');
const { handler } = require('../netlify/functions/venue-outreach');
module.exports = adapt(handler);
