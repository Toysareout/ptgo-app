/* SKYWORTHY — Elite Paragliding Decision Cockpit
   Single-file web MVP. Real data via Open-Meteo (no API key, CORS-enabled).
   Conservative decision engine: never call "Go" lightly. */
'use strict';

/* ---- Product config (edit these to go commercial) ---------------------------
   Create a yearly 49 € subscription in your Stripe dashboard, generate a
   Payment Link, and paste its URL into stripePaymentLink. Real subscription
   ENFORCEMENT needs a backend (Stripe webhook) — on a static host this page is
   an upsell/checkout entry only. */
const SKYWORTHY_CONFIG = {
  price: '49 €', interval: 'Jahr',
  stripePaymentLink: '' // e.g. 'https://buy.stripe.com/xxxxxxxx'
};

/* ============================================================
   UTILS — geo / wind / time / units / dom
   ============================================================ */
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const round = (v, d = 0) => { const f = 10 ** d; return Math.round(v * f) / f; };
const num = (v, fb = 0) => (typeof v === 'number' && isFinite(v) ? v : fb);
const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

const Geo = {
  toRad: d => d * Math.PI / 180,
  toDeg: r => r * 180 / Math.PI,
  haversineKm(a1, o1, a2, o2) {
    const R = 6371, dLat = Geo.toRad(a2 - a1), dLon = Geo.toRad(o2 - o1);
    const s = Math.sin(dLat / 2) ** 2 + Math.cos(Geo.toRad(a1)) * Math.cos(Geo.toRad(a2)) * Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(s), Math.sqrt(1 - s));
  },
  bearing(a1, o1, a2, o2) {
    const y = Math.sin(Geo.toRad(o2 - o1)) * Math.cos(Geo.toRad(a2));
    const x = Math.cos(Geo.toRad(a1)) * Math.sin(Geo.toRad(a2)) -
              Math.sin(Geo.toRad(a1)) * Math.cos(Geo.toRad(a2)) * Math.cos(Geo.toRad(o2 - o1));
    return (Geo.toDeg(Math.atan2(y, x)) + 360) % 360;
  }
};

const DIRS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
const SECTOR = { N: 0, NNE: 22.5, NE: 45, ENE: 67.5, E: 90, ESE: 112.5, SE: 135, SSE: 157.5, S: 180, SSW: 202.5, SW: 225, WSW: 247.5, W: 270, WNW: 292.5, NW: 315, NNW: 337.5 };
const Wind = {
  toCompass: deg => DIRS[Math.round(((deg % 360) / 22.5)) % 16],
  angDiff(a, b) { let d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d; },
  // does wind FROM `deg` match any of the named ideal directions (±tol)?
  matches(deg, names, tol = 55) {
    if (!names || !names.length) return true;
    return names.some(n => Wind.angDiff(deg, SECTOR[n] ?? 0) <= tol);
  },
  bestMatchScore(deg, names) { // 1 = perfect, 0 = opposite
    if (!names || !names.length) return 0.7;
    const best = Math.min(...names.map(n => Wind.angDiff(deg, SECTOR[n] ?? 0)));
    return clamp(1 - best / 90, 0, 1);
  }
};

const Time = {
  hhmm: iso => (iso || '').slice(11, 16),
  now: () => new Date(),
  ageMin: iso => Math.round((Date.now() - new Date(iso).getTime()) / 60000),
  fmtAge(iso) { const m = Time.ageMin(iso); return m < 1 ? 'gerade eben' : m < 60 ? `vor ${m} min` : `vor ${Math.floor(m / 60)} h`; }
};

/* ============================================================
   STORE — minimal reactive state + localStorage persistence
   ============================================================ */
const PERSIST = ['pilot', 'favorites', 'recent', 'selectedSiteId', 'examStats', 'alerts', 'onboarded', 'simple'];
const Store = {
  state: {
    pilot: {
      name: 'Pilot', level: 'intermediate', license: 'B', wingClass: 'EN-B-low',
      hoursTotal: 80, hoursPerYear: 30, alpineExperience: false, sivExperience: false,
      maxWindKmh: 28, maxGustKmh: 35, maxThermalStrength: 4, riskTolerance: 'medium'
    },
    favorites: [], recent: [], selectedSiteId: 'brauneck', day: 0,
    examStats: { answered: 0, correct: 0, lastDailyDate: null },
    alerts: false, onboarded: false, simple: true, online: navigator.onLine
  },
  subs: [],
  load() {
    try {
      const raw = JSON.parse(localStorage.getItem('skyworthy') || '{}');
      PERSIST.forEach(k => { if (raw[k] !== undefined) this.state[k] = raw[k]; });
    } catch (e) { /* ignore */ }
  },
  save() {
    const out = {}; PERSIST.forEach(k => out[k] = this.state[k]);
    try { localStorage.setItem('skyworthy', JSON.stringify(out)); } catch (e) { /* ignore */ }
  },
  set(patch) { Object.assign(this.state, patch); this.save(); this.emit(); },
  sub(fn) { this.subs.push(fn); },
  emit() { this.subs.forEach(fn => fn(this.state)); }
};

/* ============================================================
   LEARN — persistent learning layer (the real moat: improves with use).
   localStorage-backed models: ForecastRealityHistory, PilotFeedback,
   ModelReliabilityScore, PersonalPilotRiskProfile, LocalPatternMemory.
   (SiteDNA lives in SITE_DNA + siteDNA(); augmented here over time.)
   ============================================================ */
const Learn = {
  data: { v: 1, forecastReality: [], feedback: [], patterns: {}, personal: { gustAdj: 0, windAdj: 0, samples: 0 } },
  load() { try { const raw = JSON.parse(localStorage.getItem('skyworthy.learn') || 'null'); if (raw && raw.v) this.data = Object.assign(this.data, raw); } catch (e) { /* ignore */ } },
  save() { try { localStorage.setItem('skyworthy.learn', JSON.stringify(this.data)); } catch (e) { /* ignore */ } },
  // ForecastRealityHistory: log forecast vs strongest live station (max 1/site/30min)
  logForecastReality(siteId, d, stations) {
    if (!d || !d.best || !stations || !stations.length) return;
    const st = stations.reduce((m, s) => (s.gustKmh > m.gustKmh ? s : m));
    const h = d.best.h, now = Date.now();
    if (this.data.forecastReality.some(x => x.siteId === siteId && now - x.ts < 30 * 60000)) return;
    this.data.forecastReality.push({ ts: now, siteId, fGust: round(h.gustKmh), lGust: st.gustKmh, fWind: round(h.windKmh), lWind: st.windSpeedKmh, devGust: Math.abs(st.gustKmh - h.gustKmh), devWind: Math.abs(st.windSpeedKmh - h.windKmh) });
    if (this.data.forecastReality.length > 400) this.data.forecastReality = this.data.forecastReality.slice(-400);
    this.save();
  },
  // ModelReliabilityScore per site (1 = forecast tends to match reality)
  modelReliability(siteId) {
    const rows = this.data.forecastReality.filter(x => x.siteId === siteId).slice(-20);
    if (rows.length < 3) return { reliability: null, samples: rows.length, avgGustDev: null };
    const avgGustDev = rows.reduce((s, x) => s + x.devGust, 0) / rows.length;
    return { reliability: clamp(1 - avgGustDev / 30, 0.2, 0.99), samples: rows.length, avgGustDev: round(avgGustDev, 1) };
  },
  // PilotFeedback → adapts PersonalPilotRiskProfile + LocalPatternMemory
  recordFeedback(siteId, rating, d) {
    this.data.feedback.push({ ts: Date.now(), siteId, rating, status: d ? d.status : null, gust: d ? round(d.best.h.gustKmh) : null });
    if (this.data.feedback.length > 300) this.data.feedback = this.data.feedback.slice(-300);
    const p = this.data.personal;
    if (rating === 'harder') { p.gustAdj = clamp(p.gustAdj - 1, -10, 4); p.windAdj = clamp(p.windAdj - 0.7, -8, 3); }
    else if (rating === 'easier') { p.gustAdj = clamp(p.gustAdj + 0.5, -10, 4); p.windAdj = clamp(p.windAdj + 0.4, -8, 3); }
    p.samples++;
    if (siteId && d) this.notePattern(siteId, d.status === 'green' ? 'goodDays' : 'toughDays');
    this.save();
  },
  // PersonalPilotRiskProfile: effective limits = profile + learned adjustment
  effectiveLimits(pilot) {
    const p = this.data.personal;
    return { maxGustKmh: clamp(pilot.maxGustKmh + p.gustAdj, 8, 80), maxWindKmh: clamp(pilot.maxWindKmh + p.windAdj, 5, 60), adjusted: p.samples >= 2 && (p.gustAdj !== 0 || p.windAdj !== 0) };
  },
  // LocalPatternMemory
  notePattern(siteId, key) { const m = this.data.patterns[siteId] || (this.data.patterns[siteId] = {}); m[key] = (m[key] || 0) + 1; this.save(); },
  patternMemory(siteId) { return this.data.patterns[siteId] || {}; }
};

// BEST SITE NOW refresh cadence (per spec)
const BEST_SITE_UI_REFRESH_MS = 2 * 1000;
const LIVE_STATION_REFRESH_MS = 2 * 60 * 1000;
const FORECAST_REFRESH_MS = 10 * 60 * 1000;
const MAX_GREEN_DATA_AGE_MINUTES = 20;

/* ============================================================
   DATA — flying sites (Brauneck premium + alpine classics)
   ============================================================ */
const SITES = [
  {
    id: 'brauneck', name: 'Brauneck / Lenggries', region: 'Bayerische Voralpen', country: 'DE',
    lat: 47.6667, lon: 11.5500, elevationMin: 700, elevationMax: 1546,
    flightTypes: ['thermik', 'abgleiter', 'hike', 'xc'], beginnerFriendly: true,
    idealWindDirections: ['N', 'NNE', 'NE'], dangerousWindDirections: ['S', 'SSW', 'SW'], foehnSensitive: true,
    leeRisks: ['Süd/Südwest = Lee + Föhndüse', 'Ost/Nordost je nach Startplatz Lee'],
    valleyWindNotes: 'Talwind aus dem Isartal baut ab Mittag auf — später Westkomponente am Landeplatz.',
    thermalNotes: 'Klassischer Voralpen-Thermikberg, mittags oft böig. Alpenrand-Konvergenz möglich.',
    beginnerNotes: 'Nur bei ruhigem, eindeutig passendem Nordwind. Frühe Starts bevorzugen, keine starken Thermiktage.',
    expertNotes: 'XC möglich bei guter Basis und moderatem Höhenwind. Talwind und Lee strikt beachten.',
    siteRules: ['Bergbahn Brauneckbahn vorhanden', 'Landeplatz Lenggries beachten — Hindernisse/Stromleitung', 'Hängegleiter/Gleitschirm getrennte Startbereiche'],
    emergencyNotes: ['Notlandung: große Wiesen im Isartal', 'Bergrettung 140 / Euronotruf 112'],
    takeoffs: [
      { id: 'bk-nord', name: 'Brauneck Nordstart', lat: 47.6638, lon: 11.5471, elevation: 1480, orientation: ['N', 'NNE', 'NE'], idealWindMinKmh: 6, idealWindMaxKmh: 24, maxGustKmhBeginner: 22, maxGustKmhExpert: 38, difficulty: 'medium', notes: 'Hauptstartplatz, Wiese unterhalb Gipfelbahn.', leeDangerDirections: ['S', 'SW', 'SSW'] },
      { id: 'bk-west', name: 'Brauneck Weststart (Garland)', lat: 47.6601, lon: 11.5402, elevation: 1380, orientation: ['W', 'WNW', 'NW'], idealWindMinKmh: 6, idealWindMaxKmh: 22, maxGustKmhBeginner: 20, maxGustKmhExpert: 34, difficulty: 'medium', notes: 'Bei Westwind & nachmittäglichem Talwind.', leeDangerDirections: ['E', 'SE'] }
    ],
    landings: [{ id: 'bk-lz', name: 'Landeplatz Lenggries', lat: 47.6852, lon: 11.5680, elevation: 700, notes: 'Offizieller LP, auf Talwind/Westkomponente achten.' }]
  },
  {
    id: 'tegelberg', name: 'Tegelberg / Schwangau', region: 'Allgäu', country: 'DE',
    lat: 47.5556, lon: 10.7600, elevationMin: 810, elevationMax: 1720,
    flightTypes: ['thermik', 'abgleiter', 'soaring', 'xc'], beginnerFriendly: true,
    idealWindDirections: ['NE', 'E', 'ENE'], dangerousWindDirections: ['S', 'SW', 'W'], foehnSensitive: true,
    leeRisks: ['Südföhn kritisch', 'Westwind = Lee'],
    valleyWindNotes: 'Talwind vom Forggensee her am Nachmittag.', thermalNotes: 'Sehr guter Thermik- und Soaringberg, populär.',
    beginnerNotes: 'Beliebter Schulungsberg bei ruhigem NE/E. Frühe Starts.', expertNotes: 'Lange Soaringkante, XC ins Allgäu möglich.',
    siteRules: ['Tegelbergbahn vorhanden', 'LP Schwangau beachten'], emergencyNotes: ['Bergrettung 140'],
    takeoffs: [{ id: 'tg-o', name: 'Tegelberg Oststart', lat: 47.5547, lon: 10.7588, elevation: 1670, orientation: ['NE', 'E', 'ENE'], idealWindMinKmh: 5, idealWindMaxKmh: 24, maxGustKmhBeginner: 22, maxGustKmhExpert: 36, difficulty: 'easy', notes: 'Breite Startwiese.', leeDangerDirections: ['S', 'SW', 'W'] }],
    landings: [{ id: 'tg-lz', name: 'LP Schwangau', lat: 47.5703, lon: 10.7430, elevation: 810, notes: 'Großer offizieller Landeplatz.' }]
  },
  {
    id: 'wallberg', name: 'Wallberg / Tegernsee', region: 'Bayerische Voralpen', country: 'DE',
    lat: 47.6667, lon: 11.7667, elevationMin: 740, elevationMax: 1620,
    flightTypes: ['thermik', 'abgleiter', 'hike'], beginnerFriendly: false,
    idealWindDirections: ['NE', 'E', 'N'], dangerousWindDirections: ['S', 'SW', 'W'], foehnSensitive: true,
    leeRisks: ['Föhn aus Süd', 'West = Lee über dem See'],
    valleyWindNotes: 'Seewind-Effekt am Tegernsee nachmittags.', thermalNotes: 'Kräftige Thermik, kann ruppig werden.',
    beginnerNotes: 'Eher fortgeschritten — enger Startbereich, böig.', expertNotes: 'Schöne Hausbergrunde, Vorsicht Lee Richtung See.',
    siteRules: ['Wallbergbahn vorhanden'], emergencyNotes: ['Bergrettung 140'],
    takeoffs: [{ id: 'wb-no', name: 'Wallberg Nordoststart', lat: 47.6701, lon: 11.7642, elevation: 1580, orientation: ['NE', 'E', 'N'], idealWindMinKmh: 6, idealWindMaxKmh: 22, maxGustKmhBeginner: 18, maxGustKmhExpert: 34, difficulty: 'hard', notes: 'Steiler Startbereich.', leeDangerDirections: ['S', 'SW', 'W'] }],
    landings: [{ id: 'wb-lz', name: 'LP Rottach', lat: 47.6900, lon: 11.7600, elevation: 740, notes: 'Auf Seewind achten.' }]
  },
  {
    id: 'koessen', name: 'Kössen / Unterberghorn', region: 'Tirol', country: 'AT',
    lat: 47.6700, lon: 12.4000, elevationMin: 590, elevationMax: 1770,
    flightTypes: ['thermik', 'xc', 'abgleiter', 'soaring'], beginnerFriendly: true,
    idealWindDirections: ['W', 'WNW', 'NW', 'SW'], dangerousWindDirections: ['E', 'NE', 'SE'], foehnSensitive: true,
    leeRisks: ['Ostwind = Lee', 'Südföhn'],
    valleyWindNotes: 'Talwind aus dem Kaisertal/Inntal.', thermalNotes: 'Wettkampf- & XC-Klassiker, gute Thermik.',
    beginnerNotes: 'Großer LP, gut für Anfänger bei ruhigem W.', expertNotes: 'Top XC-Spot, Acro über dem LP.',
    siteRules: ['Unterbergbahn vorhanden', 'Großer LP Kössen'], emergencyNotes: ['Euronotruf 112', 'Bergrettung 140'],
    takeoffs: [{ id: 'ko-w', name: 'Unterberghorn Weststart', lat: 47.6712, lon: 12.4061, elevation: 1660, orientation: ['W', 'WNW', 'NW', 'SW'], idealWindMinKmh: 6, idealWindMaxKmh: 26, maxGustKmhBeginner: 22, maxGustKmhExpert: 40, difficulty: 'medium', notes: 'Bekannter Startplatz.', leeDangerDirections: ['E', 'NE', 'SE'] }],
    landings: [{ id: 'ko-lz', name: 'LP Kössen', lat: 47.6650, lon: 12.3990, elevation: 590, notes: 'Sehr großer offizieller LP.' }]
  },
  {
    id: 'emberger', name: 'Greifenburg / Emberger Alm', region: 'Kärnten', country: 'AT',
    lat: 46.7500, lon: 13.1833, elevationMin: 600, elevationMax: 1900,
    flightTypes: ['thermik', 'xc', 'abgleiter'], beginnerFriendly: true,
    idealWindDirections: ['S', 'SSE', 'SE', 'SSW'], dangerousWindDirections: ['N', 'NW', 'NE'], foehnSensitive: false,
    leeRisks: ['Nordwind = Lee'],
    valleyWindNotes: 'Stabiler Talwind im Drautal.', thermalNotes: 'Sonniger Südhang, sehr verlässliche Thermik, Fluglehrerklassiker.',
    beginnerNotes: 'Eines der anfängerfreundlichsten Gebiete der Alpen.', expertNotes: 'Lange XC-Strecken im Drautal.',
    siteRules: ['Auffahrt zur Emberger Alm', 'Mehrere LP im Tal'], emergencyNotes: ['Euronotruf 112'],
    takeoffs: [{ id: 'em-s', name: 'Emberger Alm Südstart', lat: 46.7560, lon: 13.1790, elevation: 1750, orientation: ['S', 'SSE', 'SE', 'SSW'], idealWindMinKmh: 5, idealWindMaxKmh: 24, maxGustKmhBeginner: 22, maxGustKmhExpert: 38, difficulty: 'easy', notes: 'Weite, einfache Startwiese.', leeDangerDirections: ['N', 'NW', 'NE'] }],
    landings: [{ id: 'em-lz', name: 'LP Greifenburg', lat: 46.7510, lon: 13.1840, elevation: 600, notes: 'Großer Wiesen-LP.' }]
  },
  {
    id: 'stubai', name: 'Elfer / Neustift Stubai', region: 'Tirol', country: 'AT',
    lat: 47.1100, lon: 11.3100, elevationMin: 990, elevationMax: 2080,
    flightTypes: ['thermik', 'xc', 'abgleiter', 'hike'], beginnerFriendly: false,
    idealWindDirections: ['E', 'SE', 'S'], dangerousWindDirections: ['W', 'NW', 'N'], foehnSensitive: true,
    leeRisks: ['Westwind = Lee', 'Föhn im Stubaital'],
    valleyWindNotes: 'Kräftiger Talwind nachmittags — früh kritisch.', thermalNotes: 'Hochalpine Thermik, kann stark werden.',
    beginnerNotes: 'Für Anfänger nur früh & ruhig, sonst zu kräftig.', expertNotes: 'Hochalpines XC, Talwind/Föhn beachten.',
    siteRules: ['Elferbahn vorhanden'], emergencyNotes: ['Euronotruf 112', 'Bergrettung 140'],
    takeoffs: [{ id: 'st-o', name: 'Elfer Oststart', lat: 47.1142, lon: 11.3155, elevation: 2010, orientation: ['E', 'SE', 'S'], idealWindMinKmh: 5, idealWindMaxKmh: 22, maxGustKmhBeginner: 18, maxGustKmhExpert: 36, difficulty: 'hard', notes: 'Hochalpiner Start.', leeDangerDirections: ['W', 'NW', 'N'] }],
    landings: [{ id: 'st-lz', name: 'LP Neustift', lat: 47.1170, lon: 11.3090, elevation: 990, notes: 'Talwind beachten.' }]
  },
  {
    id: 'hochries', name: 'Hochries / Samerberg', region: 'Chiemgau', country: 'DE',
    lat: 47.7470, lon: 12.2470, elevationMin: 600, elevationMax: 1569,
    flightTypes: ['thermik', 'abgleiter', 'soaring', 'xc'], beginnerFriendly: true,
    idealWindDirections: ['N', 'NW', 'NE'], dangerousWindDirections: ['S', 'SW', 'SE'], foehnSensitive: true,
    leeRisks: ['Südföhn', 'SW = Lee'],
    valleyWindNotes: 'Talwind vom Inntal nachmittags.', thermalNotes: 'Verlässlicher Chiemgau-Thermikberg.',
    beginnerNotes: 'Gut für Anfänger bei ruhigem Nord.', expertNotes: 'XC Richtung Chiemgauer Alpen.',
    siteRules: ['Hochriesbahn vorhanden'], emergencyNotes: ['Bergrettung 140'],
    takeoffs: [{ id: 'hr-n', name: 'Hochries Nordstart', lat: 47.7445, lon: 12.2455, elevation: 1530, orientation: ['N', 'NW', 'NE'], idealWindMinKmh: 5, idealWindMaxKmh: 24, maxGustKmhBeginner: 22, maxGustKmhExpert: 36, difficulty: 'easy', notes: 'Breite Wiese.', leeDangerDirections: ['S', 'SW', 'SE'] }],
    landings: [{ id: 'hr-lz', name: 'LP Grainbach', lat: 47.7560, lon: 12.2330, elevation: 670, notes: 'Offizieller LP Samerberg.' }]
  },
  {
    id: 'tegelsee', name: 'Niederhorn / Beatenberg', region: 'Berner Oberland', country: 'CH',
    lat: 46.7000, lon: 7.7700, elevationMin: 560, elevationMax: 1950,
    flightTypes: ['thermik', 'soaring', 'xc', 'abgleiter'], beginnerFriendly: true,
    idealWindDirections: ['NW', 'N', 'W'], dangerousWindDirections: ['SE', 'S', 'E'], foehnSensitive: true,
    leeRisks: ['Südföhn am Thunersee', 'Ostwind = Lee'],
    valleyWindNotes: 'See-/Talwind vom Thunersee am Nachmittag.', thermalNotes: 'Top-Soaringkante über dem Thunersee, sehr beliebt.',
    beginnerNotes: 'Großer LP, gut bei ruhigem NW.', expertNotes: 'XC ins Berner Oberland möglich.',
    siteRules: ['Niederhornbahn vorhanden', 'LP Beatenbucht'], emergencyNotes: ['Euronotruf 112', 'REGA 1414'],
    takeoffs: [{ id: 'nh-nw', name: 'Niederhorn Weststart', lat: 46.7010, lon: 7.7820, elevation: 1900, orientation: ['NW', 'N', 'W'], idealWindMinKmh: 6, idealWindMaxKmh: 24, maxGustKmhBeginner: 22, maxGustKmhExpert: 38, difficulty: 'medium', notes: 'Soaringkante.', leeDangerDirections: ['SE', 'S', 'E'] }],
    landings: [{ id: 'nh-lz', name: 'LP Beatenbucht', lat: 46.6850, lon: 7.7700, elevation: 560, notes: 'Seenah, Talwind beachten.' }]
  },
  {
    id: 'sthilaire', name: 'Saint-Hilaire-du-Touvet', region: 'Isère / Alpes', country: 'FR',
    lat: 45.3060, lon: 5.8870, elevationMin: 270, elevationMax: 1000,
    flightTypes: ['thermik', 'soaring', 'xc', 'abgleiter'], beginnerFriendly: true,
    idealWindDirections: ['W', 'SW', 'NW'], dangerousWindDirections: ['E', 'SE', 'NE'], foehnSensitive: false,
    leeRisks: ['Ostwind = Lee (Hinterland)'],
    valleyWindNotes: 'Talwind im Grésivaudan am Nachmittag.', thermalNotes: 'Weltberühmter Schulungs- & XC-Spot (Coupe Icare).',
    beginnerNotes: 'Sehr anfängerfreundlich bei ruhigem West.', expertNotes: 'Klassische XC-Strecken entlang der Chartreuse.',
    siteRules: ['Standseilbahn/Zufahrt', 'Großer LP Lumbin'], emergencyNotes: ['Euronotruf 112'],
    takeoffs: [{ id: 'sth-w', name: 'Saint-Hilaire Weststart', lat: 45.3055, lon: 5.8885, elevation: 950, orientation: ['W', 'SW', 'NW'], idealWindMinKmh: 5, idealWindMaxKmh: 24, maxGustKmhBeginner: 22, maxGustKmhExpert: 38, difficulty: 'easy', notes: 'Breite Startwiese.', leeDangerDirections: ['E', 'SE', 'NE'] }],
    landings: [{ id: 'sth-lz', name: 'LP Lumbin', lat: 45.3030, lon: 5.8990, elevation: 270, notes: 'Sehr großer offizieller LP.' }]
  },
  {
    id: 'annecy', name: 'Forclaz / Annecy', region: 'Haute-Savoie', country: 'FR',
    lat: 45.8200, lon: 6.2300, elevationMin: 450, elevationMax: 1250,
    flightTypes: ['thermik', 'soaring', 'xc', 'abgleiter'], beginnerFriendly: true,
    idealWindDirections: ['W', 'SW', 'NW'], dangerousWindDirections: ['E', 'NE', 'SE'], foehnSensitive: false,
    leeRisks: ['Ostwind = Lee über dem See'],
    valleyWindNotes: 'Seewind am Lac d’Annecy nachmittags kräftig.', thermalNotes: 'Mekka für Soaring & Acro über dem See.',
    beginnerNotes: 'Riesiger LP Doussard, ideal bei moderatem West.', expertNotes: 'Acro über Wasser, XC in die Aravis.',
    siteRules: ['Auffahrt Col de la Forclaz', 'LP Doussard / Montmin'], emergencyNotes: ['Euronotruf 112'],
    takeoffs: [{ id: 'an-w', name: 'Col de la Forclaz', lat: 45.8210, lon: 6.2360, elevation: 1240, orientation: ['W', 'SW', 'NW'], idealWindMinKmh: 6, idealWindMaxKmh: 26, maxGustKmhBeginner: 22, maxGustKmhExpert: 40, difficulty: 'easy', notes: 'Berühmter Startplatz.', leeDangerDirections: ['E', 'NE', 'SE'] }],
    landings: [{ id: 'an-lz', name: 'LP Doussard', lat: 45.7900, lon: 6.2200, elevation: 450, notes: 'Großer Wiesen-LP am Seeufer.' }]
  },
  {
    id: 'grappa', name: 'Monte Grappa / Bassano', region: 'Venetien', country: 'IT',
    lat: 45.8700, lon: 11.8000, elevationMin: 150, elevationMax: 1600,
    flightTypes: ['thermik', 'xc', 'soaring', 'abgleiter'], beginnerFriendly: true,
    idealWindDirections: ['S', 'SW', 'SE'], dangerousWindDirections: ['N', 'NE', 'NW'], foehnSensitive: false,
    leeRisks: ['Nordwind = Lee (Tramontana)'],
    valleyWindNotes: 'Talwind aus der Po-Ebene, nachmittags zunehmend.', thermalNotes: 'XC-Klassiker, sehr verlässliche Thermik.',
    beginnerNotes: 'Mehrere Startplätze, gut bei ruhigem Süd.', expertNotes: 'Lange XC-Strecken in die Voralpen.',
    siteRules: ['Zufahrt zu den Startplätzen', 'LP Borso/Romano'], emergencyNotes: ['Euronotruf 112'],
    takeoffs: [{ id: 'gr-s', name: 'Monte Grappa Südstart', lat: 45.8680, lon: 11.8050, elevation: 1500, orientation: ['S', 'SW', 'SE'], idealWindMinKmh: 5, idealWindMaxKmh: 24, maxGustKmhBeginner: 22, maxGustKmhExpert: 38, difficulty: 'medium', notes: 'Bekannter Südstart.', leeDangerDirections: ['N', 'NE', 'NW'] }],
    landings: [{ id: 'gr-lz', name: 'LP Borso del Grappa', lat: 45.8350, lon: 11.8000, elevation: 300, notes: 'Großer offizieller LP.' }]
  },
  {
    id: 'gerlitzen', name: 'Gerlitzen / Ossiacher See', region: 'Kärnten', country: 'AT',
    lat: 46.6900, lon: 13.9100, elevationMin: 500, elevationMax: 1900,
    flightTypes: ['thermik', 'xc', 'abgleiter', 'soaring'], beginnerFriendly: true,
    idealWindDirections: ['SE', 'S', 'E'], dangerousWindDirections: ['NW', 'N', 'W'], foehnSensitive: false,
    leeRisks: ['Nordwestwind = Lee'],
    valleyWindNotes: 'See-/Talwind am Ossiacher See.', thermalNotes: 'Sonniger Hang, verlässliche Kärntner Thermik.',
    beginnerNotes: 'Gut für Anfänger bei ruhigem Südost.', expertNotes: 'XC in die Nockberge.',
    siteRules: ['Gerlitzenbahn / Auffahrt', 'LP am See'], emergencyNotes: ['Euronotruf 112'],
    takeoffs: [{ id: 'ge-se', name: 'Gerlitzen Südoststart', lat: 46.6920, lon: 13.9150, elevation: 1850, orientation: ['SE', 'S', 'E'], idealWindMinKmh: 5, idealWindMaxKmh: 24, maxGustKmhBeginner: 22, maxGustKmhExpert: 38, difficulty: 'easy', notes: 'Breite Wiese.', leeDangerDirections: ['NW', 'N', 'W'] }],
    landings: [{ id: 'ge-lz', name: 'LP Annenheim', lat: 46.6700, lon: 13.8950, elevation: 510, notes: 'LP am Ossiacher See.' }]
  }
];
const siteById = id => SITES.find(s => s.id === id) || SITES[0];

/* REMINDERS — safety / exam reminders rotated in the cockpit */
const REMINDERS = [
  { i: '🌬️', t: 'Prüfungs-Reminder', d: 'Bei Föhnverdacht niemals auf lokale Windstille am Start vertrauen.' },
  { i: '📡', t: 'Safety-Reminder', d: 'Livewind schlägt Prognose. Was die Station JETZT misst, zählt mehr als jedes Modell.' },
  { i: '☁️', t: 'Check', d: 'Liegt die Wolkenbasis sicher über dem Startplatz?' },
  { i: '🛬', t: 'Check', d: 'Ist der Landeplatz frei, erreichbar und der Talwind dort bekannt?' },
  { i: '⚠️', t: 'Reminder', d: 'Eine Böe ist gefährlicher als gleichmäßiger Wind — sie löst plötzliche Kappenreaktionen aus.' },
  { i: '🔁', t: 'Reminder', d: 'Zunehmender Höhenwind bei schwachem Bodenwind = mögliche Windscherung über Startniveau.' }
];

/* EXAM QUESTIONS */
const EXAM_QUESTIONS = [
  { id: 'q1', category: 'Wetter', difficulty: 'medium', question: 'Was bedeutet zunehmender Höhenwind bei schwachem Startplatzwind?', options: ['Ideale ruhige Bedingungen', 'Mögliche Windscherung und anspruchsvolle Bedingungen über Startniveau', 'Garantiert keine Thermik', 'Sicheres Zeichen für Abgleiter'], correctAnswerIndex: 1, explanation: 'Schwacher Bodenwind kann täuschen: nimmt der Höhenwind zu, drohen Scherung und turbulente Bedingungen über dem Start.', reminder: 'Höhenwind immer getrennt vom Bodenwind prüfen.' },
  { id: 'q2', category: 'Gefahren', difficulty: 'easy', question: 'Was ist bei Föhnverdacht die sicherste Entscheidung?', options: ['Früh starten, bevor er kommt', 'Nur erfahrene Piloten', 'Nicht fliegen', 'Auf Lee-Seite ausweichen'], correctAnswerIndex: 2, explanation: 'Föhn ist tückisch und kann schlagartig durchbrechen. Die einzig sichere Entscheidung ist: nicht fliegen.', reminder: 'Föhn = No-Go.' },
  { id: 'q3', category: 'Wetter', difficulty: 'easy', question: 'Warum ist eine Böe gefährlicher als gleichmäßiger Wind?', options: ['Sie ist kälter', 'Weil sie plötzliche Anstellwinkel- und Kappenreaktionen auslösen kann', 'Sie kommt immer von hinten', 'Sie ist langsamer'], correctAnswerIndex: 1, explanation: 'Böen ändern Anströmung und Anstellwinkel schlagartig — das kann zu Klappern und Einklappern führen.', reminder: 'Böenfaktor beachten, nicht nur Mittelwind.' },
  { id: 'q4', category: 'Luftraum', difficulty: 'medium', question: 'Wer hat im unkontrollierten Luftraum bei Gegenkurs Vorflug bzw. wie wird ausgewichen?', options: ['Der Höhere weicht ab', 'Beide weichen nach rechts aus', 'Der Schnellere hat Vorflug', 'Der Tiefere weicht ab'], correctAnswerIndex: 1, explanation: 'Bei Begegnung auf Gegenkurs weichen beide nach rechts aus.', reminder: 'Rechts ausweichen bei Frontalbegegnung.' },
  { id: 'q5', category: 'Ausweichregeln', difficulty: 'medium', question: 'Zwei Gleitschirme am selben Hang — wer hat Vorflug?', options: ['Der Schnellere', 'Der mit dem Hang zur Rechten', 'Der Höhere', 'Der Startende'], correctAnswerIndex: 1, explanation: 'Am Hang hat der Pilot Vorflug, der den Hang auf seiner rechten Seite hat; der andere weicht aus.', reminder: 'Hang rechts = Vorflug.' },
  { id: 'q6', category: 'Vorflugcheck', difficulty: 'easy', question: 'Was gehört zwingend zum 5-Punkte-Check vor dem Start?', options: ['Nur der Helm', 'Gurtzeug, Karabiner, Leinen/Kappe, Beschleuniger, Luftraum/Wind', 'Nur die Windrichtung', 'Nur das Vario'], correctAnswerIndex: 1, explanation: 'Der Startcheck umfasst Pilot/Gurtzeug, Verbindung/Karabiner, Schirm/Leinen, Wind/Luftraum und freien Startweg.', reminder: 'Kein Start ohne vollständigen Check.' },
  { id: 'q7', category: 'Start', difficulty: 'medium', question: 'Was tun, wenn die Kappe beim Aufziehen schräg hochkommt?', options: ['Sofort abheben', 'Korrigieren und bei Bedarf abbrechen', 'Schneller laufen', 'Bremse voll ziehen'], correctAnswerIndex: 1, explanation: 'Schräg aufkommende Kappe korrigieren; gelingt das nicht sauber, Start abbrechen. Lieber neu aufziehen als unkontrolliert starten.', reminder: 'Im Zweifel: Start abbrechen.' },
  { id: 'q8', category: 'Landung', difficulty: 'easy', question: 'Wie sollte die Landevolte grundsätzlich angelegt werden?', options: ['Immer mit Rückenwind landen', 'Gegen den Wind, mit klarer Position-/Queranflug-/Endanflug-Struktur', 'Möglichst steil von oben', 'Egal, Hauptsache schnell'], correctAnswerIndex: 1, explanation: 'Gelandet wird gegen den Wind mit geplanter Volte (Gegenanflug, Queranflug, Endanflug).', reminder: 'Immer gegen den Wind landen.' },
  { id: 'q9', category: 'Thermik', difficulty: 'medium', question: 'Was ist eine Inversion und ihre Bedeutung für die Thermik?', options: ['Eine warme Schicht über kühler Luft, die Thermik deckelt', 'Starker Höhenwind', 'Ein Gewitter', 'Eine Wolkenart'], correctAnswerIndex: 0, explanation: 'Bei einer Inversion nimmt die Temperatur mit der Höhe zu — die Thermik wird darunter gedeckelt und endet oft abrupt.', reminder: 'Inversion = Thermikdeckel / Basis begrenzt.' },
  { id: 'q10', category: 'Gefahren', difficulty: 'hard', question: 'Du fliegst auf der windabgewandten (Lee-)Seite eines Grats. Womit musst du rechnen?', options: ['Ruhiger Hangaufwind', 'Rotoren und turbulente Abwinde', 'Garantierte Thermik', 'Keine Besonderheit'], correctAnswerIndex: 1, explanation: 'Im Lee bilden sich Rotoren und Abwinde — eine der häufigsten Unfallursachen. Lee meiden.', reminder: 'Lee = Rotor = Gefahr.' },
  { id: 'q11', category: 'Material', difficulty: 'easy', question: 'Wozu dient der Beschleuniger (Speedbar)?', options: ['Zum schnelleren Steigen', 'Zur Erhöhung der Fluggeschwindigkeit durch Anstellwinkelverringerung', 'Zum Bremsen', 'Zur Rettung'], correctAnswerIndex: 1, explanation: 'Der Beschleuniger verringert den Anstellwinkel und erhöht die Geschwindigkeit — bei Turbulenz erhöht das aber die Klappneigung.', reminder: 'Beschleuniger in Turbulenz vorsichtig dosieren.' },
  { id: 'q12', category: 'Notfälle', difficulty: 'hard', question: 'Wann wird das Rettungsgerät geworfen?', options: ['Bei jeder kleinen Klappe', 'Wenn der Schirm nicht mehr steuerbar ist und die Bodennähe es erfordert', 'Nie über Wasser', 'Nur bei Vollstall'], correctAnswerIndex: 1, explanation: 'Die Rettung wird geworfen, wenn der Schirm nicht mehr beherrschbar ist und keine sichere Lösung in der verbleibenden Höhe möglich ist.', reminder: 'Im Zweifel und bei Bodennähe: werfen.' }
];

/* ============================================================
   PROVIDERS — Open-Meteo (real) + mock live stations
   ============================================================ */
const PRESSURE_LEVELS = [925, 850, 800, 700, 600, 500];
const Providers = {
  buildForecastUrl(site, days = 2) {
    const hourly = [
      'temperature_2m', 'relative_humidity_2m', 'dew_point_2m', 'precipitation', 'precipitation_probability',
      'cloud_cover', 'cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high',
      'wind_speed_10m', 'wind_direction_10m', 'wind_gusts_10m', 'cape', 'freezing_level_height',
      'surface_pressure', 'pressure_msl', 'wind_speed_80m', 'wind_direction_80m', 'wind_speed_120m', 'wind_speed_180m'
    ];
    PRESSURE_LEVELS.forEach(p => {
      hourly.push(`wind_speed_${p}hPa`, `wind_direction_${p}hPa`, `geopotential_height_${p}hPa`, `temperature_${p}hPa`, `relative_humidity_${p}hPa`);
    });
    const q = new URLSearchParams({
      latitude: site.lat, longitude: site.lon, hourly: hourly.join(','),
      wind_speed_unit: 'kmh', timezone: 'auto', forecast_days: String(days), models: 'best_match'
    });
    return `https://api.open-meteo.com/v1/forecast?${q}`;
  },
  async fetchForecast(site) {
    const r = await fetch(Providers.buildForecastUrl(site), { cache: 'no-store' });
    if (!r.ok) throw new Error('Open-Meteo Forecast HTTP ' + r.status);
    return r.json();
  },
  async fetchModels(site) {
    const models = ['icon_seamless', 'ecmwf_ifs025', 'gfs_seamless', 'meteofrance_seamless', 'gem_seamless'];
    const q = new URLSearchParams({
      latitude: site.lat, longitude: site.lon,
      hourly: 'wind_speed_10m,wind_gusts_10m,wind_direction_10m,cloud_cover,precipitation',
      wind_speed_unit: 'kmh', timezone: 'auto', forecast_days: '2', models: models.join(',')
    });
    const r = await fetch(`https://api.open-meteo.com/v1/forecast?${q}`, { cache: 'no-store' });
    if (!r.ok) throw new Error('Models HTTP ' + r.status);
    const j = await r.json();
    return { json: j, models };
  },
  async geocode(name) {
    const q = new URLSearchParams({ name, count: '8', language: 'de', format: 'json' });
    const r = await fetch(`https://geocoding-api.open-meteo.com/v1/search?${q}`);
    if (!r.ok) return [];
    const j = await r.json();
    return j.results || [];
  },
  /* Mock live stations near a site. Demo only — clearly labelled in UI.
     Real integrations (Holfuy/Pioupiou/Windy/Burnair) need API keys / partner access. */
  liveStations(site, agg) {
    const h = agg ? agg.atHour(agg.bestHourIdx) : null;
    const baseSpeed = h ? h.windKmh : 12, baseDir = h ? h.windDir : (SECTOR[site.idealWindDirections[0]] ?? 0), baseGust = h ? h.gustKmh : 20;
    const seed = Math.floor(Date.now() / (2 * 60 * 1000)); // changes every 2 min
    const rng = n => { const x = Math.sin(seed * 99 + n * 17.13) * 43758.5453; return x - Math.floor(x); };
    const defs = [
      { sfx: 'Gipfel', dKm: 0.6, dz: 760, type: 'mountain', rel: 0.92, spf: 1.25 },
      { sfx: 'Startplatz', dKm: 0.2, dz: 740, type: 'takeoff', rel: 0.97, spf: 1.0 },
      { sfx: 'Tal', dKm: 3.1, dz: 80, type: 'valley', rel: 0.8, spf: 0.55 },
      { sfx: 'Nachbargrat', dKm: 6.4, dz: 600, type: 'mountain', rel: 0.7, spf: 1.1 },
      { sfx: 'Flugplatz', dKm: 11.0, dz: 30, type: 'airport', rel: 0.95, spf: 0.7 }
    ];
    return defs.map((d, i) => {
      const wobble = (rng(i) - 0.5);
      const speed = clamp(baseSpeed * d.spf * (1 + wobble * 0.5), 0, 90);
      const gust = clamp(speed * (1.3 + rng(i + 5) * 0.6), speed, 110);
      const dir = (baseDir + (rng(i + 9) - 0.5) * 70 + 360) % 360;
      const bp = Geo.bearing(site.lat, site.lon, site.lat + 0.01 * Math.cos(i), site.lon + 0.01 * Math.sin(i));
      return {
        id: `${site.id}-st${i}`, provider: 'Demo/Mock', name: `${site.name.split(' ')[0]} ${d.sfx}`,
        lat: site.lat + (d.dKm / 111) * Math.cos(i * 1.3), lon: site.lon + (d.dKm / 78) * Math.sin(i * 1.7),
        elevation: site.elevationMin + d.dz, distanceKm: round(d.dKm, 1), bearingToSite: round(bp),
        windDirection: round(dir), windSpeedKmh: round(speed), gustKmh: round(gust),
        temperatureC: h ? round(h.temp - d.dz / 150, 1) : null,
        updatedAt: new Date(Date.now() - rng(i + 2) * 4 * 60000).toISOString(),
        reliabilityScore: d.rel, stationType: d.type
      };
    });
  },
  /* REAL live stations via Pioupiou open API (fair use, CC-BY).
     Returns mapped LiveStation[] within radiusKm, or null on failure/none. */
  async _pioupiouRaw() {
    const now = Date.now();
    if (this._ppCache && now - this._ppCache.ts < 120000) return this._ppCache.data;
    const r = await fetch('https://api.pioupiou.fr/v1/live/all', { cache: 'no-store' });
    if (!r.ok) throw new Error('Pioupiou HTTP ' + r.status);
    const j = await r.json();
    const data = (j && j.data) || [];
    this._ppCache = { ts: now, data };
    return data;
  },
  async fetchPioupiou(site, radiusKm = 60) {
    const data = await this._pioupiouRaw();
    const out = [];
    for (const s of data) {
      const loc = s.location, m = s.measurements;
      if (!loc || !m || loc.latitude == null || loc.longitude == null) continue;
      if (m.wind_speed_avg == null || m.wind_heading == null) continue;
      const ageMin = m.date ? Time.ageMin(m.date) : 9999;
      if (ageMin > 120) continue; // skip stale
      const dist = Geo.haversineKm(site.lat, site.lon, loc.latitude, loc.longitude);
      if (dist > radiusKm) continue;
      out.push({
        id: 'pio-' + s.id, provider: 'Pioupiou', name: (s.meta && s.meta.name) ? s.meta.name : 'Pioupiou ' + s.id,
        lat: loc.latitude, lon: loc.longitude, elevation: null,
        distanceKm: round(dist, 1), bearingToSite: round(Geo.bearing(site.lat, site.lon, loc.latitude, loc.longitude)),
        windDirection: round(m.wind_heading), windSpeedKmh: round(m.wind_speed_avg), gustKmh: round(m.wind_speed_max != null ? m.wind_speed_max : m.wind_speed_avg),
        temperatureC: null, updatedAt: m.date || new Date().toISOString(),
        reliabilityScore: clamp(1 - ageMin / 120, 0.3, 0.97), stationType: 'unknown',
        sourceUrl: 'https://www.pioupiou.fr/fr/' + s.id
      });
    }
    return out.sort((a, b) => a.distanceKm - b.distanceKm).slice(0, 10);
  },
  /* REAL spatial pressure field: sample MSL pressure at the site + 4 neighbours
     (~80 km N/S/E/W) in one Open-Meteo call and compute the horizontal pressure
     gradient and the geostrophic (gradient) wind direction. */
  async fetchPressureField(site) {
    const dLat = 0.72, dLon = 0.72 / Math.max(0.2, Math.cos(Geo.toRad(site.lat)));
    const lats = [site.lat, site.lat + dLat, site.lat - dLat, site.lat, site.lat];
    const lons = [site.lon, site.lon, site.lon, site.lon + dLon, site.lon - dLon];
    const q = new URLSearchParams({ latitude: lats.join(','), longitude: lons.join(','), current: 'pressure_msl', timezone: 'auto' });
    const r = await fetch(`https://api.open-meteo.com/v1/forecast?${q}`, { cache: 'no-store' });
    if (!r.ok) throw new Error('Pressure field HTTP ' + r.status);
    const j = await r.json();
    const arr = Array.isArray(j) ? j : [j];
    const P = arr.map(o => o && o.current ? o.current.pressure_msl : null);
    if (P.some(p => p == null)) return null;
    const distNS = 2 * dLat * 111195;                                  // m
    const distEW = 2 * dLon * 111195 * Math.cos(Geo.toRad(site.lat));  // m
    const dPdN = (P[1] - P[2]) / distNS;  // hPa per m, +N
    const dPdE = (P[3] - P[4]) / distEW;  // hPa per m, +E
    const gradPer100km = Math.sqrt(dPdN * dPdN + dPdE * dPdE) * 100000;
    // direction toward LOWEST pressure (down-gradient), meteorological bearing
    const toLow = (Geo.toDeg(Math.atan2(-dPdE, -dPdN)) + 360) % 360;
    // geostrophic wind: NH low to the left → vector ∝ (dPdN east, -dPdE north); SH reversed
    const sign = site.lat >= 0 ? 1 : -1;
    const vE = sign * dPdN, vN = -sign * dPdE;
    const windTo = (Geo.toDeg(Math.atan2(vE, vN)) + 360) % 360;
    const windFrom = (windTo + 180) % 360;
    return { pMsl: P[0], gradPer100km, toLow, geoWindFrom: windFrom, points: P, fetchedAt: new Date().toISOString() };
  }
};

/* ============================================================
   AGGREGATION ENGINE — parse Open-Meteo into usable structure
   ============================================================ */
function buildAggregation(json, site) {
  const H = json.hourly; if (!H || !H.time) throw new Error('Keine Stundendaten');
  const t = H.time, n = t.length;
  const get = k => H[k] || new Array(n).fill(null);
  const fields = {
    time: t, temp: get('temperature_2m'), rh: get('relative_humidity_2m'), dew: get('dew_point_2m'),
    precip: get('precipitation'), precipP: get('precipitation_probability'),
    cloud: get('cloud_cover'), cloudL: get('cloud_cover_low'), cloudM: get('cloud_cover_mid'), cloudH: get('cloud_cover_high'),
    wind: get('wind_speed_10m'), windDir: get('wind_direction_10m'), gust: get('wind_gusts_10m'),
    cape: get('cape'), frz: get('freezing_level_height'), sp: get('surface_pressure'), pmsl: get('pressure_msl'),
    w80: get('wind_speed_80m'), w120: get('wind_speed_120m'), w180: get('wind_speed_180m'), wd80: get('wind_direction_80m')
  };
  PRESSURE_LEVELS.forEach(p => {
    fields[`w${p}`] = get(`wind_speed_${p}hPa`); fields[`wd${p}`] = get(`wind_direction_${p}hPa`);
    fields[`gh${p}`] = get(`geopotential_height_${p}hPa`); fields[`t${p}`] = get(`temperature_${p}hPa`);
    fields[`rh${p}`] = get(`relative_humidity_${p}hPa`);
  });

  // day grouping by local date (timezone=auto → local ISO)
  const dates = [...new Set(t.map(x => x.slice(0, 10)))];

  const atHour = i => ({
    i, time: t[i], hh: Time.hhmm(t[i]),
    temp: num(fields.temp[i]), dew: num(fields.dew[i]), rh: num(fields.rh[i]),
    precip: num(fields.precip[i]), precipP: num(fields.precipP[i]),
    cloud: num(fields.cloud[i]), cloudL: num(fields.cloudL[i]), cloudM: num(fields.cloudM[i]), cloudH: num(fields.cloudH[i]),
    windKmh: num(fields.wind[i]), windDir: num(fields.windDir[i]), gustKmh: num(fields.gust[i]),
    cape: num(fields.cape[i]), frz: num(fields.frz[i]), pmsl: num(fields.pmsl[i]), sp: num(fields.sp[i]),
    upper: PRESSURE_LEVELS.map(p => ({ p, h: num(fields[`gh${p}`][i]), spd: num(fields[`w${p}`][i]), dir: num(fields[`wd${p}`][i]), temp: num(fields[`t${p}`][i]), rh: num(fields[`rh${p}`][i]) }))
  });

  // wind samples for interpolation (height ASL, speed, dir)
  const windSamples = i => {
    const base = site.elevationMin;
    const arr = [
      { h: base + 10, spd: num(fields.wind[i]), dir: num(fields.windDir[i]) },
      { h: base + 80, spd: num(fields.w80[i]), dir: num(fields.wd80[i]) },
      { h: base + 120, spd: num(fields.w120[i]), dir: num(fields.wd80[i]) },
      { h: base + 180, spd: num(fields.w180[i]), dir: num(fields.wd80[i]) }
    ];
    PRESSURE_LEVELS.forEach(p => {
      const h = num(fields[`gh${p}`][i]); if (h > base + 50) arr.push({ h, spd: num(fields[`w${p}`][i]), dir: num(fields[`wd${p}`][i]) });
    });
    return arr.filter(s => s.spd >= 0).sort((a, b) => a.h - b.h);
  };
  const windAtAlt = (i, targetAlt) => {
    const s = windSamples(i); if (!s.length) return null;
    if (targetAlt <= s[0].h) return s[0];
    if (targetAlt >= s[s.length - 1].h) return s[s.length - 1];
    for (let k = 0; k < s.length - 1; k++) {
      if (targetAlt >= s[k].h && targetAlt <= s[k + 1].h) {
        const f = (targetAlt - s[k].h) / (s[k + 1].h - s[k].h);
        return { h: targetAlt, spd: s[k].spd + f * (s[k + 1].spd - s[k].spd), dir: s[k].dir };
      }
    }
    return s[s.length - 1];
  };

  // cloud base estimate (LCL, m AGL above valley) ≈ 125 * (T - Td)
  const cloudBaseAsl = i => round(site.elevationMin + 125 * Math.max(0, num(fields.temp[i]) - num(fields.dew[i])));

  return {
    site, raw: json, fields, time: t, n, dates, atHour, windSamples, windAtAlt, cloudBaseAsl,
    bestHourIdx: 0, dayIndices: [],
    // daylight indices for a given day offset (0 today, 1 tomorrow), 07–20h local
    daylightIdx(dayOffset) {
      const date = dates[Math.min(dayOffset, dates.length - 1)];
      const out = [];
      for (let i = 0; i < n; i++) {
        if (t[i].slice(0, 10) !== date) continue;
        const hh = +t[i].slice(11, 13); if (hh >= 7 && hh <= 20) out.push(i);
      }
      return out;
    },
    // index of the hour closest to "now" (falls back to first index)
    nowIdx() {
      const now = Date.now(); let best = 0, bestD = Infinity;
      for (let i = 0; i < n; i++) { const d = Math.abs(new Date(t[i]).getTime() - now); if (d < bestD) { bestD = d; best = i; } }
      return best;
    }
  };
}

/* ============================================================
   RISK + FLIGHT DECISION ENGINE — conservative
   ============================================================ */
function scoreHour(agg, i, site, pilot) {
  const h = agg.atHour(i);
  const to = site.takeoffs[0];
  const triggers = []; // {level:'black'|'red', text}
  const why = [];

  const gustFactor = h.windKmh > 1 ? h.gustKmh / h.windKmh : (h.gustKmh > 8 ? 3 : 1);
  const dirOk = Wind.matches(h.windDir, site.idealWindDirections);
  const dirDanger = Wind.matches(h.windDir, site.dangerousWindDirections, 35) ||
                    (to && Wind.matches(h.windDir, to.leeDangerDirections, 35));
  const dirScore = Wind.bestMatchScore(h.windDir, site.idealWindDirections);

  // upper wind / shear / foehn proxies
  const w850 = h.upper.find(u => u.p === 850) || { spd: 0, dir: h.windDir };
  const w700 = h.upper.find(u => u.p === 700) || { spd: 0, dir: h.windDir };
  const shear = Wind.angDiff(h.windDir, w850.dir);
  const cloudBase = agg.cloudBaseAsl(i);
  const baseUnderTakeoff = cloudBase < (to ? to.elevation : site.elevationMax) + 50;

  /* ---- BLACK (lebensgefährlich) ---- */
  if (h.cape > 1800 && h.precip > 0.3) triggers.push({ level: 'black', text: 'Gewitter/Cb-Entwicklung in der Nähe (hohe CAPE + Niederschlag).' });
  if (h.cape > 2200) triggers.push({ level: 'black', text: 'Sehr hohe CAPE — Überentwicklung / Cb-Risiko.' });
  if (site.foehnSensitive && w700.spd > 50 && dirDanger) triggers.push({ level: 'black', text: 'Föhnverdacht: starker Höhenwind aus kritischer Richtung über föhnanfälligem Gebiet.' });
  if (dirDanger && h.windKmh > 22) triggers.push({ level: 'black', text: 'Massives Lee am Startplatz (kräftiger Wind aus Lee-/Gefahrenrichtung).' });
  if (shear > 70 && w850.spd > 35) triggers.push({ level: 'black', text: 'Starke Windscherung zwischen Boden und Höhe.' });
  if (baseUnderTakeoff && h.precip > 0.2) triggers.push({ level: 'black', text: 'Wolkenbasis unter Startplatz + Niederschlag.' });

  /* ---- RED ---- */
  if (h.gustKmh > pilot.maxGustKmh) triggers.push({ level: 'red', text: `Böen ${round(h.gustKmh)} km/h über deinem Limit (${pilot.maxGustKmh}).` });
  if (h.windKmh > pilot.maxWindKmh) triggers.push({ level: 'red', text: `Mittelwind ${round(h.windKmh)} km/h über deinem Limit (${pilot.maxWindKmh}).` });
  if (!dirOk) triggers.push({ level: 'red', text: `Windrichtung ${Wind.toCompass(h.windDir)} passt nicht zum Startplatz.` });
  if (dirDanger && h.windKmh > 12) triggers.push({ level: 'red', text: 'Lee-/Rotorgefahr durch ungünstige Windrichtung.' });
  if (h.precip > 0.3) triggers.push({ level: 'red', text: 'Niederschlag am Startplatz.' });
  if (gustFactor > 2.1 && h.gustKmh > 18) triggers.push({ level: 'red', text: `Hoher Böenfaktor (${round(gustFactor, 1)}×) — ruppig.` });

  /* ---- sub scores 0..100 ---- */
  // wind
  let windScore = 100 * (0.35 + 0.65 * dirScore);
  const lo = to ? to.idealWindMinKmh : 6, hi = to ? to.idealWindMaxKmh : 24;
  if (h.windKmh < lo) windScore -= (lo - h.windKmh) * 3;          // too weak (soaring/thermik dependent)
  if (h.windKmh > hi) windScore -= (h.windKmh - hi) * 4.5;        // too strong
  if (h.windKmh > pilot.maxWindKmh) windScore -= 40;
  windScore -= Math.max(0, gustFactor - 1.4) * 35;
  if (h.gustKmh > pilot.maxGustKmh) windScore -= 45;
  if (dirDanger) windScore -= 35;
  windScore = clamp(windScore, 0, 100);

  // safety
  let safety = 100;
  safety -= clamp(h.cape / 25, 0, 80);
  safety -= clamp(h.precip * 30, 0, 60);
  safety -= clamp((gustFactor - 1.5) * 30, 0, 40);
  if (dirDanger) safety -= 30;
  if (baseUnderTakeoff) safety -= 25;
  if (site.foehnSensitive && w700.spd > 40 && dirDanger) safety -= 35;
  safety -= clamp((shear - 40) * 0.6, 0, 30);
  safety = clamp(safety, 0, 100);

  // thermal
  const baseAboveTO = cloudBase - (to ? to.elevation : site.elevationMax);
  let thermal = 0;
  const hh = +h.time.slice(11, 13);
  const tod = hh >= 11 && hh <= 16 ? 1 : hh >= 9 && hh <= 18 ? 0.7 : 0.3;
  thermal += clamp(h.cape / 12, 0, 45) * tod;
  thermal += clamp(baseAboveTO / 25, 0, 35);
  thermal += clamp((40 - Math.abs(h.cloudL - 25)) / 2, 0, 20); // some cumulus good, overcast/blue less
  if (h.precip > 0.2) thermal -= 30;
  if (h.cloud > 85) thermal -= 20; // overcast = abschattung
  thermal = clamp(thermal, 0, 100);

  // xc
  let xc = thermal * 0.7 + clamp(baseAboveTO / 30, 0, 30);
  if (w700.spd > 40) xc -= (w700.spd - 40) * 1.2; // too windy aloft for relaxed xc
  xc = clamp(xc, 0, 100);

  // overall (safety-weighted, conservative)
  let overall = 0.42 * safety + 0.34 * windScore + 0.14 * thermal + 0.10 * xc;
  overall = clamp(overall, 0, 100);

  // beginner / expert variants
  const beginnerPenalty = (h.gustKmh > 22 ? 30 : 0) + (h.windKmh > 18 ? 20 : 0) + (h.cape > 600 ? 20 : 0) + (gustFactor > 1.7 ? 20 : 0) + (dirDanger ? 30 : 0);
  const beginnerScore = clamp(overall - beginnerPenalty, 0, 100);
  const expertScore = clamp(overall + (dirOk && !dirDanger ? 12 : 0) + (h.cape > 400 && h.cape < 1800 ? 8 : 0) - (h.gustKmh > pilot.maxGustKmh ? 10 : 0), 0, 100);

  return {
    i, h, windScore: round(windScore), safetyScore: round(safety), thermalScore: round(thermal),
    xcScore: round(xc), beginnerScore: round(beginnerScore), expertScore: round(expertScore),
    overallScore: round(overall), triggers, gustFactor, dirOk, dirDanger, cloudBase, baseAboveTO,
    shear, w700, w850, dirScore
  };
}

function statusFromScoreAndTriggers(score, triggers) {
  if (triggers.some(t => t.level === 'black')) return 'black';
  if (triggers.some(t => t.level === 'red')) return 'red';
  if (score >= 75) return 'green';
  if (score >= 60) return 'yellow';
  if (score >= 45) return 'orange';
  return 'red';
}
const STATUS_LABEL = { green: 'GO — passend', yellow: 'GO mit Vorsicht', orange: 'Nur erfahrene Piloten', red: 'NO-GO — nicht empfehlenswert', black: 'NO-GO — lebensgefährlich', gray: 'Keine Daten' };
const STATUS_RANK = { green: 0, yellow: 1, orange: 2, red: 3, black: 4 };
const STATUS_BY_RANK = ['green', 'yellow', 'orange', 'red', 'black'];
const rankMax = (a, b) => STATUS_BY_RANK[Math.max(STATUS_RANK[a], STATUS_RANK[b])];

function recommendFlightType(best, site) {
  const h = best.h;
  if (statusFromScoreAndTriggers(best.overallScore, best.triggers) === 'black') return 'Nicht fliegen';
  if (best.dirDanger || best.triggers.some(t => t.level === 'red')) return 'Nicht fliegen';
  if (best.thermalScore > 65 && best.xcScore > 55) return 'Thermik / XC';
  if (best.thermalScore > 55) return 'Thermikflug';
  if (h.windKmh >= (site.takeoffs[0]?.idealWindMinKmh ?? 6) + 6 && h.windKmh <= (site.takeoffs[0]?.idealWindMaxKmh ?? 24) && best.dirOk) return 'Soaring';
  if (best.overallScore > 55) return 'Abgleiter';
  return 'Groundhandling / Nicht fliegen';
}

function calculateFlightDecision(agg, stations, site, pilot, dayOffset, modelConsensus) {
  const idx = agg.daylightIdx(dayOffset);
  if (!idx.length) return { status: 'gray', label: STATUS_LABEL.gray, summary: 'Keine Tagesdaten verfügbar.', overallScore: 0, topRisks: [], why: [], whatToCheckOnSite: [], modelConflicts: [], decisiveStations: [], hours: [] };

  const hours = idx.map(i => scoreHour(agg, i, site, pilot));
  // best hour = highest overall among non-red/black; else least bad
  const safe = hours.filter(s => !s.triggers.some(t => t.level === 'black' || t.level === 'red'));
  const pool = safe.length ? safe : hours;
  const best = pool.reduce((a, b) => (b.overallScore > a.overallScore ? b : a));
  agg.bestHourIdx = best.i;

  // best window: contiguous hours around best with status <= yellow
  const okIdxs = hours.filter(s => STATUS_RANK[statusFromScoreAndTriggers(s.overallScore, s.triggers)] <= 1).map(s => s.i);
  let winStart = best.h.hh, winEnd = best.h.hh;
  if (okIdxs.length) {
    const sorted = okIdxs.slice().sort((a, b) => a - b);
    // contiguous run containing best.i
    let lo = best.i, hi = best.i;
    while (sorted.includes(lo - 1)) lo--;
    while (sorted.includes(hi + 1)) hi++;
    winStart = agg.atHour(lo).hh; winEnd = agg.atHour(hi + 1 <= idx[idx.length - 1] ? hi : hi).hh;
    winEnd = agg.atHour(Math.min(hi + 1, idx[idx.length - 1])).hh;
  }

  // latest safe start: first time of day status becomes orange/red/black
  let latestSafe = null;
  for (const s of hours) {
    const st = statusFromScoreAndTriggers(s.overallScore, s.triggers);
    if (STATUS_RANK[st] <= 1) latestSafe = s.h.hh; else if (latestSafe) break;
  }

  // decisive station = highest reliability * (mountain/takeoff weighted)
  const decisive = (stations || []).slice().sort((a, b) =>
    (b.reliabilityScore * (b.stationType === 'takeoff' ? 1.2 : b.stationType === 'mountain' ? 1.1 : 1)) -
    (a.reliabilityScore * (a.stationType === 'takeoff' ? 1.2 : a.stationType === 'mountain' ? 1.1 : 1))
  ).slice(0, 2);

  // live vs forecast conflict
  const liveConflicts = [];
  if (decisive[0] && best) {
    const fc = best.h.windKmh, lv = decisive[0].windSpeedKmh;
    if (lv > fc * 1.8 && lv > 15) liveConflicts.push({ level: 'black', text: `Livewind (${lv} km/h, ${decisive[0].name}) deutlich stärker als Prognose (${round(fc)} km/h).` });
    else if (Math.abs(lv - fc) > 12) liveConflicts.push({ level: 'red', text: `Livewind weicht stark von Prognose ab (${lv} vs ${round(fc)} km/h).` });
    if (decisive[0].gustKmh > pilot.maxGustKmh * 1.5) liveConflicts.push({ level: 'black', text: `Liveböen extrem über Limit (${decisive[0].gustKmh} km/h).` });
  }

  const allTriggers = [...best.triggers, ...liveConflicts];
  let status = statusFromScoreAndTriggers(best.overallScore, allTriggers);

  // model conflict → reduce confidence, can push to red
  const modelConflicts = (modelConsensus && modelConsensus.conflicts) ? modelConsensus.conflicts : [];
  let confidence = modelConsensus ? modelConsensus.agreement : 70;
  // data quality from forecast model run age handled elsewhere; here from station age
  const dataQuality = clamp(100 - (decisive[0] ? Time.ageMin(decisive[0].updatedAt) * 1.5 : 0), 30, 100);
  if (modelConflicts.length >= 2 && STATUS_RANK[status] < STATUS_RANK.red) { status = 'red'; }

  // DAY-HAZARD CAP — a calm morning must never make the whole day read GREEN
  // if a red/black hazard develops later (e.g. afternoon thunderstorm / build-up).
  const hourStatus = hours.map(s => ({ i: s.i, hh: s.h.hh, st: statusFromScoreAndTriggers(s.overallScore, s.triggers) }));
  const firstHazard = hourStatus.find(x => STATUS_RANK[x.st] >= STATUS_RANK.red);
  if (firstHazard) {
    if (best.i >= firstHazard.i) status = rankMax(status, firstHazard.st); // only flyable time is during/after the hazard
    else if (firstHazard.st === 'black') status = rankMax(status, 'orange'); // safe early window, dangerous later
    else status = rankMax(status, 'yellow');
  }

  // topRisks: highest day storm even if morning ok
  const stormHour = hours.find(s => s.h.cape > 1500 || s.h.precip > 1);
  const topRisks = [];
  allTriggers.forEach(t => topRisks.push((t.level === 'black' ? '🟣 ' : '🔴 ') + t.text));
  if (stormHour && !allTriggers.some(t => /Gewitter|Cb/.test(t.text))) topRisks.push(`🟠 Spätere Überentwicklung möglich (ab ~${stormHour.h.hh}, CAPE ${round(stormHour.h.cape)}).`);
  if (best.gustFactor > 1.6) topRisks.push(`🟡 Böenfaktor ~${round(best.gustFactor, 1)}× — mit Turbulenz rechnen.`);
  if (best.baseAboveTO < 300) topRisks.push(`🟠 Wolkenbasis nur ~${round(best.baseAboveTO)} m über Start.`);
  modelConflicts.forEach(c => topRisks.push('🟠 ' + c));
  if (!topRisks.length) topRisks.push('🟢 Keine dominanten Risiken in den Modelldaten erkennbar — trotzdem vor Ort prüfen.');

  // flight type must respect the FINAL (possibly overridden) status
  const flightType = (status === 'red' || status === 'black') ? 'Nicht fliegen' : recommendFlightType(best, site);

  const why = [];
  why.push(`Windrichtung ${Wind.toCompass(best.h.windDir)} ${best.dirOk ? 'passt' : 'passt NICHT'} (ideal: ${site.idealWindDirections.join('/')}).`);
  why.push(`Bodenwind ${round(best.h.windKmh)} km/h, Böen ${round(best.h.gustKmh)} km/h (dein Limit ${pilot.maxWindKmh}/${pilot.maxGustKmh}).`);
  why.push(`Höhenwind 700 hPa ~${round(best.w700.spd)} km/h, Scherung ${round(best.shear)}°.`);
  why.push(`CAPE ${round(best.h.cape)} J/kg, Wolkenbasis ~${round(best.cloudBase)} m (~${round(best.baseAboveTO)} m über Start).`);

  const checks = [
    'Windsack & Streamer am Start mit Prognose abgleichen.',
    'Höhenwind/Scherung über Startniveau beobachten (Wolkenzug).',
    site.foehnSensitive ? 'Föhnzeichen prüfen: Föhnfische, Fernsicht, warmer böiger Wind.' : 'Wolkenbasis sicher über Start?',
    'Landeplatz-Talwind & Erreichbarkeit checken.',
    'Andere Piloten / Schulbetrieb beobachten.'
  ];

  return {
    overallScore: best.overallScore, safetyScore: best.safetyScore, windScore: best.windScore,
    thermalScore: best.thermalScore, xcScore: best.xcScore, beginnerScore: best.beginnerScore,
    expertScore: best.expertScore, confidenceScore: round(confidence), dataQualityScore: round(dataQuality),
    status, label: STATUS_LABEL[status],
    summary: buildSummary(status, site, best, flightType),
    bestStartTime: `${winStart}–${winEnd}`, latestSafeStartTime: latestSafe,
    bestTakeoff: pickBestTakeoff(site, best.h).name,
    recommendedFlightType: flightType,
    topRisks, why, whatToCheckOnSite: checks, modelConflicts, decisiveStations: decisive,
    best, hours, probabilities: estimateProbabilities(best)
  };
}

function pickBestTakeoff(site, h) {
  return site.takeoffs
    .map(to => ({ to, score: Wind.bestMatchScore(h.windDir, to.orientation) - (Wind.matches(h.windDir, to.leeDangerDirections, 35) ? 1 : 0) }))
    .sort((a, b) => b.score - a.score)[0].to;
}
function estimateProbabilities(best) {
  const thermal = best.thermalScore, relaxed = clamp(100 - Math.abs(best.h.windKmh - 12) * 4 - Math.max(0, best.gustFactor - 1.4) * 40, 0, 100);
  const sporty = clamp(best.h.windKmh * 2 + (best.gustFactor - 1) * 60 + best.h.cape / 20, 0, 100);
  return { thermal: round(thermal), relaxed: round(relaxed), sporty: round(sporty) };
}
function buildSummary(status, site, best, ft) {
  const h = best.h;
  if (status === 'black') return `Lebensgefährliche Indikatoren an ${site.name}. Heute nicht fliegen.`;
  if (status === 'red') return `${site.name} heute nicht empfehlenswert — ${best.dirOk ? 'Bedingungen außerhalb sicherer Grenzen' : 'Windrichtung passt nicht'}.`;
  if (status === 'orange') return `${site.name} nur für erfahrene Piloten. ${ft}. Aufmerksam bleiben, Bedingungen grenzwertig.`;
  if (status === 'yellow') return `${site.name} fliegbar mit Vorsicht. Empfehlung: ${ft}. Auf Entwicklung achten.`;
  return `${site.name} sieht passend aus. Empfehlung: ${ft}. Daten relativ stabil — trotzdem vor Ort prüfen.`;
}

/* model consensus */
function buildModelConsensus(modelsRes, dayOffset) {
  const { json, models } = modelsRes; const H = json.hourly; if (!H) return null;
  const t = H.time, dates = [...new Set(t.map(x => x.slice(0, 10)))];
  const date = dates[Math.min(dayOffset, dates.length - 1)];
  // pick local 13:00 of target day as reference hour
  let ref = t.findIndex(x => x.slice(0, 10) === date && x.slice(11, 13) === '13');
  if (ref < 0) ref = t.findIndex(x => x.slice(0, 10) === date);
  const rows = models.map(m => ({
    model: m,
    wind: num(H[`wind_speed_10m_${m}`]?.[ref]), gust: num(H[`wind_gusts_10m_${m}`]?.[ref]),
    dir: num(H[`wind_direction_10m_${m}`]?.[ref]), cloud: num(H[`cloud_cover_${m}`]?.[ref]), precip: num(H[`precipitation_${m}`]?.[ref])
  })).filter(r => r.wind > 0 || r.cloud >= 0);
  if (!rows.length) return null;
  const winds = rows.map(r => r.wind), gusts = rows.map(r => r.gust);
  const wMin = Math.min(...winds), wMax = Math.max(...winds), wAvg = winds.reduce((a, b) => a + b, 0) / winds.length;
  const spread = wMax - wMin;
  const dirs = rows.map(r => r.dir);
  const dirSpread = Math.max(...dirs.map(d => Math.min(...dirs.map(e => Wind.angDiff(d, e) === 0 ? 0 : Wind.angDiff(d, e))))) || 0;
  const maxDirDiff = (() => { let m = 0; for (let a of dirs) for (let b of dirs) m = Math.max(m, Wind.angDiff(a, b)); return m; })();
  const conflicts = [];
  if (spread > 14) conflicts.push(`Modelle uneinig beim Wind (${round(wMin)}–${round(wMax)} km/h um 13:00).`);
  if (maxDirDiff > 80 && wAvg > 12) conflicts.push(`Windrichtung zwischen Modellen um bis zu ${round(maxDirDiff)}° verschieden.`);
  const precs = rows.map(r => r.precip);
  if (Math.max(...precs) > 0.5 && Math.min(...precs) < 0.1) conflicts.push('Niederschlag nur in einem Teil der Modelle.');
  const agreement = clamp(100 - spread * 3 - maxDirDiff * 0.4, 20, 99);
  return { rows, refTime: t[ref], wMin, wMax, wAvg, spread, maxDirDiff, conflicts, agreement: round(agreement) };
}

/* ============================================================
   EXPLAINABLE INTELLIGENCE — beginner/expert layers, green-blockers,
   forecast-vs-reality, contextual exam, dual decision, site DNA.
   Additive: consumes the existing decision engine, no second engine.
   ============================================================ */
const RISK_EXPLANATIONS = {
  lee: { factor: 'Lee', beginnerText: 'Der Wind kommt ungünstig über den Berg. Dahinter kann die Luft stark verwirbelt sein.', expertText: 'Lee-Risiko durch Windrichtung relativ zur Startplatzexposition — Rotoren und turbulente Rückseitenströmung möglich.', dangerTranslation: 'Nicht starten, nur weil es am Startplatz kurz ruhig aussieht.', simpleAnalogy: 'Wie Wasserwirbel hinter einem Stein im Fluss.' },
  gusts: { factor: 'Böen', beginnerText: 'Der Wind ist nicht gleichmäßig. Plötzliche Stöße können den Schirm stark bewegen.', expertText: 'Erhöhter Böenfaktor — aktive Kappenreaktionen, Pitch-Impulse und Klapprisiko.', dangerTranslation: 'Wenn die Böen über deinem Limit sind, ist das kein Übungstag.', simpleAnalogy: 'Wie Seitenwind beim Autofahren: nicht der Schnitt zählt, sondern der plötzliche Schlag.' },
  foehn: { factor: 'Föhn', beginnerText: 'Föhn kann gefährliche Turbulenz erzeugen, auch wenn es unten ruhig aussieht.', expertText: 'Föhnindikation — Rotoren, starke Höhenströmung, Druckgradient und Leewellen.', dangerTranslation: 'Bei Föhnverdacht nicht fliegen.', simpleAnalogy: 'Wie ein unsichtbarer Sturm über den Bergen.' },
  thermal: { factor: 'Thermik', beginnerText: 'Warme Luft steigt auf. Das kann tragen, aber auch unruhig werden.', expertText: 'Thermikstärke, Ablösefrequenz, Basis, Labilität und Überentwicklungsrisiko bewerten.', dangerTranslation: 'Starke Thermik ist für Anfänger oft zu sportlich.', simpleAnalogy: 'Wie unsichtbare Aufzüge in der Luft.' },
  cloudbase: { factor: 'Wolkenbasis', beginnerText: 'Liegen die Wolken tiefer als der Startplatz, drohen Sichtprobleme.', expertText: 'Cloudbase nahe/unter Startniveau reduziert Sicht, Orientierung und Sicherheitsmargen.', dangerTranslation: 'Nicht in Wolken fliegen.', simpleAnalogy: 'Wie Autofahren im Nebel ohne klare Straße.' },
  shear: { factor: 'Windscherung', beginnerText: 'In verschiedenen Höhen weht der Wind unterschiedlich stark/aus anderer Richtung.', expertText: 'Richtungs-/Geschwindigkeitsscherung Boden↔Höhe — anspruchsvolles Handling, Klappneigung.', dangerTranslation: 'Mit ruppigem Übergang und Turbulenz rechnen.', simpleAnalogy: 'Wie zwei Flüsse, die übereinander in verschiedene Richtungen fließen.' },
  storm: { factor: 'Gewitter', beginnerText: 'Gewitter erzeugen extreme, unberechenbare Auf- und Abwinde.', expertText: 'Cb-/Überentwicklungsrisiko — Gust-Fronten, Saugwirkung, Hagel, Blitz.', dangerTranslation: 'Bei Gewitterneigung am Boden bleiben.', simpleAnalogy: 'Wie eine riesige Waschmaschine in der Atmosphäre.' },
  rain: { factor: 'Niederschlag', beginnerText: 'Nasse Schirme verlieren Leistung und reagieren unberechenbar.', expertText: 'Durchnässung verändert Profil/Sackflugneigung; Schauer bringen Böenfronten.', dangerTranslation: 'Regen und Gleitschirm passen nicht zusammen.', simpleAnalogy: 'Wie Reifen ohne Profil bei Nässe.' }
};
function explainRiskFactor(factor) {
  return RISK_EXPLANATIONS[factor] || { factor, beginnerText: 'Dieser Faktor beeinflusst deine Flugsicherheit.', expertText: 'Sicherheitsrelevanter meteorologischer Einflussfaktor.', dangerTranslation: 'Bei Unsicherheit konservativ entscheiden.', simpleAnalogy: 'Unklare Luft ist wie unklare Straße: langsamer, vorsichtiger oder gar nicht.' };
}
function riskKey(text) {
  const t = (text || '').toLowerCase();
  if (/lee/.test(t)) return 'lee';
  if (/böen|boen|böe|gust/.test(t)) return 'gusts';
  if (/föhn|foehn/.test(t)) return 'foehn';
  if (/thermik|überentw|cape/.test(t)) return 'thermal';
  if (/basis|wolken/.test(t)) return 'cloudbase';
  if (/scherung|shear/.test(t)) return 'shear';
  if (/gewitter|\bcb\b|gewitterrisiko/.test(t)) return 'storm';
  if (/regen|niederschlag/.test(t)) return 'rain';
  return null;
}
// what is preventing a green rating — current vs required, in plain words
function buildGreenBlockers(d, site, pilot) {
  const b = [], h = d.best.h;
  const hasRisk = re => d.topRisks.some(r => re.test(r.toLowerCase()));
  if (h.gustKmh > pilot.maxGustKmh) b.push({ factor: 'Böen', currentValue: `${round(h.gustKmh)} km/h`, requiredValue: `< ${pilot.maxGustKmh} km/h (dein Profil)`, severity: 'high', explanation: 'Die Böen überschreiten dein persönliches Limit. Grün ist nicht verantwortbar.' });
  if (Wind.matches(h.windDir, site.dangerousWindDirections, 35)) b.push({ factor: 'Windrichtung', currentValue: Wind.toCompass(h.windDir), requiredValue: `nicht ${site.dangerousWindDirections.join(', ')}`, severity: 'critical', explanation: 'Diese Windrichtung kann am Fluggebiet Lee oder Rotoren erzeugen.' });
  if (hasRisk(/gewitter|cb|überentw/)) b.push({ factor: 'Gewitterrisiko', currentValue: `CAPE ${round(h.cape)} J/kg`, requiredValue: 'keine Cb-Neigung', severity: 'critical', explanation: 'Gewitterrisiko ist ein harter Sicherheitsblocker.' });
  if (site.foehnSensitive && hasRisk(/föhn|foehn/)) b.push({ factor: 'Föhn', currentValue: 'Föhnverdacht', requiredValue: 'kein Föhnverdacht', severity: 'critical', explanation: 'Föhn kann extreme Turbulenz erzeugen, obwohl es am Start ruhig wirkt.' });
  if (d.best.baseAboveTO < 200) b.push({ factor: 'Wolkenbasis', currentValue: `~${round(d.best.baseAboveTO)} m über Start`, requiredValue: '> 200 m über Start', severity: 'medium', explanation: 'Die Basis ist zu nah am Startplatz — Sicht und Marge reduziert.' });
  const age = Data.forecast.fetchedAt ? Time.ageMin(Data.forecast.fetchedAt) : 0;
  if (age > 20) b.push({ factor: 'Datenalter', currentValue: `${round(age)} min alt`, requiredValue: '< 20 min', severity: 'medium', explanation: 'Die Daten sind zu alt für eine grüne Entscheidung.' });
  return b;
}
// forecast vs the strongest live station
function buildForecastReality(d, stations) {
  if (!stations || !stations.length) return [];
  const st = stations.reduce((m, s) => (s.gustKmh > m.gustKmh ? s : m));
  const out = [], h = d.best.h;
  const gd = ((st.gustKmh - h.gustKmh) / Math.max(h.gustKmh, 1)) * 100;
  if (gd > 25) out.push({ parameter: 'Böen', forecastValue: `${round(h.gustKmh)} km/h`, liveValue: `${st.gustKmh} km/h (${st.name})`, deviationPercent: Math.round(gd), severity: gd > 50 ? 'critical' : 'high', interpretation: 'Der Livewind ist deutlich böiger als die Prognose — Modellvertrauen sinkt.' });
  const wd = ((st.windSpeedKmh - h.windKmh) / Math.max(h.windKmh, 1)) * 100;
  if (wd > 30) out.push({ parameter: 'Wind', forecastValue: `${round(h.windKmh)} km/h`, liveValue: `${st.windSpeedKmh} km/h (${st.name})`, deviationPercent: Math.round(wd), severity: wd > 60 ? 'critical' : 'high', interpretation: 'Die Realität ist windiger als die Vorhersage — vor Ort besonders kritisch prüfen.' });
  return out;
}
// risk-triggered quiz prompt
function contextualExam(topRisks) {
  const has = re => topRisks.some(r => re.test(r.toLowerCase()));
  if (has(/lee/)) return { triggerRisk: 'Lee', question: 'Warum ist Lee beim Gleitschirmfliegen gefährlich?', options: ['Weil die Luft dort meist besonders ruhig ist', 'Weil hinter Hindernissen Rotoren und Turbulenzen entstehen können', 'Weil dort immer mehr Thermik ist', 'Weil der Schirm dort schneller steigt'], correctAnswerIndex: 1, explanation: 'Im Lee entstehen hinter Bergen verwirbelte Luftbereiche — Klapper und Kontrollverlust werden begünstigt.' };
  if (has(/föhn|foehn/)) return { triggerRisk: 'Föhn', question: 'Was ist bei Föhnverdacht die sicherste Entscheidung?', options: ['Nur kurz starten', 'Weiter oben testen', 'Nicht fliegen', 'Nur mit kleinem Schirm fliegen'], correctAnswerIndex: 2, explanation: 'Föhn kann extreme Turbulenzen und Rotoren erzeugen. Bei Föhnverdacht ist Nichtfliegen die sichere Entscheidung.' };
  if (has(/böen|boen|böe/)) return { triggerRisk: 'Böen', question: 'Warum sind Böen gefährlicher als gleichmäßiger Wind?', options: ['Weil sie plötzlich Kappenreaktionen auslösen können', 'Weil sie immer von vorne kommen', 'Weil sie nur am Boden auftreten', 'Weil sie Thermik verhindern'], correctAnswerIndex: 0, explanation: 'Böen verändern plötzlich Anstellwinkel und Druck im Schirm — starke Pitchbewegungen oder Klapper entstehen.' };
  if (has(/gewitter|cb|überentw/)) return { triggerRisk: 'Gewitter', question: 'Wie verhältst du dich bei aufziehender Überentwicklung?', options: ['Schnell noch hochkreisen', 'Frühzeitig landen und Abstand zur Wolke halten', 'Unter die Wolke fliegen', 'Abwarten in der Luft'], correctAnswerIndex: 1, explanation: 'Cumulonimben erzeugen Saugwirkung und Gust-Fronten. Rechtzeitig landen und großen Abstand halten.' };
  return { triggerRisk: 'Allgemeiner Check', question: 'Was solltest du vor jedem Flug zusätzlich zur App prüfen?', options: ['Nur die Farbe der Ampel', 'Livewind, Wolken, Start, Landeplatz und lokale Piloteninfos', 'Nur die Temperatur', 'Nur die Schirmfarbe'], correctAnswerIndex: 1, explanation: 'Eine App ersetzt nie die eigene Wetterprüfung, den Blick in den Himmel und lokale Erfahrung.' };
}
// rich site DNA (explicit for known sites, otherwise derived from site fields)
const SITE_DNA = {
  brauneck: {
    commonTraps: ['Talwind nimmt nachmittags oft deutlich zu', 'Föhn wird am Startplatz unterschätzt', 'Lee bei südwestlicher Höhenströmung', 'Thermik kann im Tagesverlauf sportlich werden'],
    localWindSystems: ['Talwind aus dem Isartal', 'Alpenrand-Konvergenz möglich', 'Höhenwind oft stärker als Startplatzwind'],
    beginnerMistakes: ['Nur Startplatzwind prüfen', 'Höhenwind ignorieren', 'Zu spät starten', 'Böenfaktor unterschätzen', 'Föhnzeichen nicht ernst nehmen'],
    expertOpportunities: ['Früher Thermikeinstieg bei moderatem Höhenwind', 'XC bei hoher Basis und guter Modellübereinstimmung', 'Hike & Fly im stabilen Morgenfenster'],
    escapeRoutes: ['Frühzeitig Richtung Landeplatz orientieren', 'Bei zunehmendem Talwind keine späte Hangnähe erzwingen'],
    bestSeason: ['Frühling', 'Sommer', 'Herbst'],
    redFlagPatterns: ['Südwestlicher Höhenwind', 'Föhnverdacht', 'Böen deutlich stärker als Prognose', 'Cb-Entwicklung über den Bergen']
  }
};
function siteDNA(site) {
  if (SITE_DNA[site.id]) return SITE_DNA[site.id];
  return {
    commonTraps: [...(site.leeRisks || []), site.valleyWindNotes].filter(Boolean),
    localWindSystems: [site.valleyWindNotes, site.foehnSensitive ? 'Föhnempfindlich — Höhenwind kann stärker sein als am Start' : 'Höhenwind kann vom Startplatzwind abweichen'].filter(Boolean),
    beginnerMistakes: ['Nur Startplatzwind prüfen', 'Höhenwind ignorieren', 'Böenfaktor unterschätzen', site.foehnSensitive ? 'Föhnzeichen nicht ernst nehmen' : 'Talwind-Zunahme unterschätzen'],
    expertOpportunities: [site.expertNotes].filter(Boolean),
    escapeRoutes: ['Frühzeitig Richtung Landeplatz orientieren', (site.landings && site.landings[0]) ? `Landeplatz ${site.landings[0].name} im Blick behalten` : 'Erreichbarkeit des Landeplatzes sichern'],
    bestSeason: ['Frühling', 'Sommer', 'Herbst'],
    redFlagPatterns: [...(site.dangerousWindDirections || []).map(x => `Wind aus ${x}`), ...(site.foehnSensitive ? ['Föhnverdacht'] : []), 'Böen deutlich stärker als Prognose']
  };
}
// beginner vs expert presets + dual decision
const PRESET_BEGINNER = { name: 'Anfänger', level: 'beginner', wingClass: 'EN-A', maxWindKmh: 15, maxGustKmh: 22, maxThermalStrength: 1.5, hoursTotal: 20, riskTolerance: 'low', alpineExperience: false, sivExperience: false };
const PRESET_EXPERT = { name: 'Experte', level: 'expert', wingClass: 'EN-C', maxWindKmh: 25, maxGustKmh: 35, maxThermalStrength: 4, hoursTotal: 600, riskTolerance: 'high', alpineExperience: true, sivExperience: true };
function dualDecision() {
  const agg = Data.forecast.agg; if (!agg) return null;
  const site = siteById(Store.state.selectedSiteId), cons = Data.models.consensus, day = Store.state.day, st = Data.stations.list;
  return { beginner: calculateFlightDecision(agg, st, site, PRESET_BEGINNER, day, cons), expert: calculateFlightDecision(agg, st, site, PRESET_EXPERT, day, cons) };
}

/* ---- TRUST ENGINE: how dependable is this decision? ---- */
function calculateTrust({ stations, consensus, mismatches, dataAgeMin }) {
  const dataFreshness = clamp(100 - dataAgeMin * 4, 0, 100);
  const modelAgreement = consensus ? consensus.agreement : 55;
  const liveScore = stations && stations.length ? clamp(55 + stations.length * 12, 0, 100) : 25;
  const realityScore = mismatches && mismatches.length ? clamp(100 - mismatches.reduce((s, m) => s + m.deviationPercent, 0), 0, 100) : (stations && stations.length ? 100 : 70);
  const confidence = Math.round(dataFreshness * 0.25 + modelAgreement * 0.30 + liveScore * 0.20 + realityScore * 0.25);
  const decisive = [], conflicting = [], missing = [], stale = [];
  if (modelAgreement >= 70) decisive.push('Wettermodelle weitgehend einig'); else if (modelAgreement < 45) conflicting.push('Modelle widersprechen sich');
  if (stations && stations.length) decisive.push(`${stations.length} Live-Station(en) im Umkreis`); else missing.push('Keine Live-Stationen im Umkreis');
  if (mismatches && mismatches.length) conflicting.push('Livewind weicht von Prognose ab');
  if (dataAgeMin > MAX_GREEN_DATA_AGE_MINUTES) stale.push(`Daten ${round(dataAgeMin)} min alt`);
  const label = confidence >= 85 ? 'Sehr hohe Datenqualität' : confidence >= 70 ? 'Gute Datenqualität' : confidence >= 55 ? 'Mittlere Datenqualität' : confidence >= 40 ? 'Unsicher' : 'Nicht belastbar';
  return {
    confidencePercent: confidence, dataFreshnessScore: round(dataFreshness), modelAgreementScore: round(modelAgreement),
    liveStationScore: round(liveScore), forecastRealityScore: round(realityScore),
    decisiveSignals: decisive, conflictingSignals: conflicting, missingData: missing, staleDataWarnings: stale, finalTrustLabel: label,
    trustExplanationBeginner: confidence >= 70 ? 'Die Daten sind solide — prüf trotzdem Himmel und Livewind vor dem Start.' : 'Die Daten sind heute weniger verlässlich — sei besonders vorsichtig und entscheide konservativ.',
    trustExplanationExpert: `Konfidenz ${confidence}% — Frische ${round(dataFreshness)}, Modellkonsens ${round(modelAgreement)}, Live ${round(liveScore)}, Realität ${round(realityScore)}.`
  };
}

/* ---- BEST SITE NOW / MORNING ENGINE ---- */
const STATUS_ORDER = { green: 0, yellow: 1, orange: 2, red: 3, black: 4, gray: 5, neutral: 6 };
function worseStatus(a, b) { return STATUS_ORDER[a] >= STATUS_ORDER[b] ? a : b; }
function parseWindow(s) { if (!s || !/\d/.test(s)) return { from: '—', to: '—' }; const m = String(s).split('–'); return { from: (m[0] || '').trim(), to: (m[1] || m[0] || '').trim() }; }
function flightDuration(ft, level) {
  const map = { 'Abgleiter': ['10–20 min', '15–30 min'], 'Soaring': ['30–60 min', '1–2 h'], 'Thermikflug': ['30–90 min', '1–3 h'], 'Thermik / XC': ['1–2 h', '2–5 h'], 'XC': ['1–2 h', '3–6 h'], 'Hike & Fly': ['30–60 min', '1–3 h'], 'Nicht fliegen': ['—', '—'] };
  const e = map[ft] || ['30–60 min', '1–2 h']; return level === 'expert' ? e[1] : e[0];
}
function siteDnaScore(site, d) {
  let s = 100;
  if (Wind.matches(d.best.h.windDir, site.dangerousWindDirections, 35)) s -= 50;
  if (site.foehnSensitive && d.topRisks.some(r => /föhn/i.test(r))) s -= 40;
  if (d.best.baseAboveTO < 200) s -= 25;
  return clamp(s, 0, 100);
}
function nextCriticalChange(agg, site, pilot) {
  const idx = agg.daylightIdx(Store.state.day); if (!idx.length) return undefined;
  const lim = Learn.effectiveLimits(pilot);
  for (const i of idx) {
    const h = agg.atHour(i);
    if (h.cape > 1500) return { time: h.hh, reason: `Überentwicklung/Gewitterneigung (CAPE ${round(h.cape)})` };
    if (h.gustKmh > lim.maxGustKmh) return { time: h.hh, reason: `Böen steigen über dein Limit (~${round(h.gustKmh)} km/h)` };
    if (Wind.matches(h.windDir, site.dangerousWindDirections, 35)) return { time: h.hh, reason: `Wind dreht in Lee-Richtung (${Wind.toCompass(h.windDir)})` };
    if (h.precipP > 40) return { time: h.hh, reason: `Niederschlagsrisiko steigt (${round(h.precipP)}%)` };
  }
  return undefined;
}
function buildWhyBest(d, site, stations) {
  const w = [], h = d.best.h, to = site.takeoffs[0] || {};
  if (!Wind.matches(h.windDir, site.dangerousWindDirections, 35) && Wind.bestMatchScore(h.windDir, to.orientation || []) > 0.5) w.push('Windrichtung passt zum Startplatz');
  if (h.gustKmh <= Store.state.pilot.maxGustKmh) w.push(`Böen im Rahmen (~${round(h.gustKmh)} km/h)`);
  if (d.best.baseAboveTO > 200) w.push(`Wolkenbasis ~${round(d.best.baseAboveTO)} m über Start`);
  if (stations && stations.length && !buildForecastReality(d, stations).length) w.push('Livewind bestätigt die Prognose');
  if (/–/.test(d.bestStartTime || '') && +String(d.bestStartTime).slice(0, 2) < 11) w.push('Talwind morgens noch schwach');
  return w.slice(0, 4);
}
function rankNearbyFlyingSites({ userLocation, radiusKm, sites, bundle, pilotProfile }) {
  const out = [];
  for (const site of sites) {
    const dist = Geo.haversineKm(userLocation.lat, userLocation.lon, site.lat, site.lon);
    if (dist > radiusKm) continue;
    const b = bundle[site.id]; if (!b || !b.agg) continue;
    const stations = b.stations || [];
    const lim = Learn.effectiveLimits(pilotProfile);
    const pilot = Object.assign({}, pilotProfile, { maxGustKmh: lim.maxGustKmh, maxWindKmh: lim.maxWindKmh });
    const d = calculateFlightDecision(b.agg, stations, site, pilot, Store.state.day, b.consensus || null);
    if (!d.best) continue;
    const beg = calculateFlightDecision(b.agg, stations, site, PRESET_BEGINNER, Store.state.day, b.consensus || null);
    const exp = calculateFlightDecision(b.agg, stations, site, PRESET_EXPERT, Store.state.day, b.consensus || null);
    const dataAge = b.fetchedAt ? Time.ageMin(b.fetchedAt) : 99;
    const mism = buildForecastReality(d, stations);
    const trust = calculateTrust({ stations, consensus: b.consensus || null, mismatches: mism, dataAgeMin: dataAge });
    const dnaS = siteDnaScore(site, d);
    const realityS = mism.length ? clamp(100 - mism.reduce((s, m) => s + m.deviationPercent, 0), 0, 100) : (stations.length ? 100 : 60);
    const distS = clamp(100 - dist / radiusKm * 100, 0, 100);
    let composite = Math.round(d.safetyScore * 0.45 + d.windScore * 0.25 + dnaS * 0.15 + realityS * 0.10 + distS * 0.05);
    let status = d.status;
    if (dataAge > MAX_GREEN_DATA_AGE_MINUTES && status === 'green') status = 'yellow';
    if (!stations.length && status === 'green') status = 'yellow';
    if (trust.confidencePercent < 60 && status === 'green') status = 'yellow';
    if (trust.confidencePercent < 40) status = worseStatus(status, 'orange');
    if (mism.some(m => m.severity === 'critical')) { status = worseStatus(status, 'orange'); composite -= 20; }
    out.push({
      siteId: site.id, siteName: site.name, distanceKm: round(dist, 1), status, score: clamp(composite, 0, 100),
      beginnerStatus: beg.status, expertStatus: exp.status, bestTakeoff: d.bestTakeoff || (site.takeoffs[0] || {}).name,
      bestStartWindow: parseWindow(d.bestStartTime), expectedFlightType: d.recommendedFlightType,
      expectedFlightDuration: { beginner: flightDuration(d.recommendedFlightType, 'beginner'), expert: flightDuration(d.recommendedFlightType, 'expert') },
      whyBest: buildWhyBest(d, site, stations), risks: d.topRisks.slice(0, 3).map(r => r.replace(/^[^\p{L}]+/u, '').trim()),
      whyNotGreen: status !== 'green' ? buildGreenBlockers(d, site, pilot).map(x => `${x.factor}: ${x.currentValue} (Soll ${x.requiredValue})`) : undefined,
      nextCriticalChange: nextCriticalChange(b.agg, site, pilotProfile), confidencePercent: trust.confidencePercent, dataAgeMinutes: round(dataAge), trust
    });
  }
  out.sort((a, b) => STATUS_ORDER[a.status] - STATUS_ORDER[b.status] || b.score - a.score);
  const best = out[0] || null;
  let globalWarning;
  if (!out.length) globalWarning = 'Keine Fluggebiete mit Daten im Radius gefunden.';
  else if (out.every(s => s.status === 'red' || s.status === 'black')) globalWarning = 'Heute im ganzen Umkreis kritisch — wahrscheinlich kein Flugtag.';
  return { radiusKm, updatedAt: new Date().toISOString(), userLocation, bestSite: best, rankedSites: out, globalWarning };
}

/* ============================================================
   PRESSURE ENGINE — classify the high/low situation + flying impact
   ============================================================ */
function analyzePressure(agg, field, site) {
  const i = agg.nowIdx(), h = agg.atHour(i);
  const pmsl = h.pmsl || (field && field.pMsl) || 1013;
  // tendency: change over the next ~12 h (positive = building high, negative = approaching low)
  const jl = Math.min(agg.n - 1, i + 12), je = Math.min(agg.n - 1, i + 3);
  const trend12 = round((agg.atHour(jl).pmsl || pmsl) - pmsl, 1);
  const trend3 = round((agg.atHour(je).pmsl || pmsl) - pmsl, 1);
  // 500 hPa pattern (ridge vs trough)
  const gh500 = (h.upper.find(u => u.p === 500) || {}).h || 0;
  const ridgeTrough = gh500 ? (gh500 > 5650 ? { k: 'ridge', t: `Höhenrücken (500 hPa ~${round(gh500)} m) → Hochdruckeinfluss in der Höhe, absinkende Luft.` }
    : gh500 < 5500 ? { k: 'trough', t: `Höhentrog (500 hPa ~${round(gh500)} m) → Hebung, labiler, oft Schauer/Gewitter.` }
    : { k: 'flat', t: `Flache Höhenströmung (500 hPa ~${round(gh500)} m).` }) : null;

  const pClass = pmsl >= 1018 ? 'hoch' : pmsl <= 1008 ? 'tief' : 'uebergang';
  const grad = field ? field.gradPer100km : null;
  const gradCat = grad == null ? null : grad < 1 ? { k: 'schwach', c: 'green' } : grad < 2 ? { k: 'mäßig', c: 'yellow' } : grad < 3.5 ? { k: 'kräftig', c: 'orange' } : { k: 'stürmisch', c: 'red' };

  const tendIcon = trend12 > 1.5 ? '📈' : trend12 < -1.5 ? '📉' : '➖';
  const tendText = trend12 > 1.5 ? `Druck steigt (+${trend12} hPa/12 h) → Wetterberuhigung, Hochdruck baut auf/verstärkt sich.`
    : trend12 < -1.5 ? `Druck fällt (${trend12} hPa/12 h) → Tief/Front nähert sich, Wind & Wolken nehmen zu.`
    : 'Druck nahezu konstant → Lage stabil, keine schnelle Änderung.';

  const effects = [], risks = [];
  if (pClass === 'hoch') {
    effects.push(['🌤️', 'Stabile Schichtung', 'Absinkende Luft (Subsidenz) → oft ruhiges, beständiges Flugwetter. Gut für Anfänger.']);
    effects.push(['🧱', 'Inversion / Deckel', 'Absink-Inversion deckelt die Thermik → niedrige Basis, häufig Dunst/Hochnebel am Morgen.']);
    effects.push(['🍃', 'Schwacher Gradientwind', 'Thermik- und Talwinde dominieren über den synoptischen Wind.']);
    if (site.foehnSensitive) risks.push('🟠 Bei kräftigem Hoch + Druckunterschied über den Alpen: Föhngefahr — Höhenwind & Föhnzeichen prüfen.');
    if (gh500 && gh500 > 5750) effects.push(['🅾️', 'Blockierendes Hoch / Omega', 'Mehrtägig stabil und gleichförmig — ideal planbar, aber Thermik oft schwach/blau.']);
  } else if (pClass === 'tief') {
    effects.push(['🌧️', 'Labile Schichtung', 'Hebung → Quellwolken, Schauer und Gewitter wahrscheinlich. Überentwicklung möglich.']);
    effects.push(['💨', 'Kräftiger Gradientwind', 'Enge Isobaren → starker, böiger Wind. Häufig No-Go, v. a. an Lee-Hängen.']);
    effects.push(['🌬️', 'Fronten', 'Kaltfront = Böenfront + Winddreher; Warmfront = Aufgleiten + Dauerregen. Frontdurchgang meiden.']);
    risks.push('🔴 Tiefdruck dominiert: erhöhte Gefahr durch Wind, Böen, Fronten und Gewitter.');
  } else {
    effects.push(['⚖️', 'Übergangslage', 'Zwischen Hoch und Tief — Gradient, Fronten und Trend genau beobachten.']);
  }
  if (gradCat && (gradCat.k === 'kräftig' || gradCat.k === 'stürmisch')) risks.push(`🔴 Druckgradient ${gradCat.k} (${round(grad,1)} hPa/100 km) → kräftiger Höhen-/Gradientwind.`);
  if (ridgeTrough && ridgeTrough.k === 'trough') risks.push('🟠 Höhentrog → labil, Schauer-/Gewitterneigung erhöht.');
  if (trend12 < -3) risks.push('🟠 Deutlich fallender Druck → markante Wetterverschlechterung/Front im Anmarsch.');

  return {
    pmsl: round(pmsl), pClass,
    label: pClass === 'hoch' ? 'Hochdruck dominiert' : pClass === 'tief' ? 'Tiefdruck dominiert' : 'Übergangslage',
    statusColor: pClass === 'hoch' ? 'green' : pClass === 'tief' ? 'red' : 'yellow',
    trend12, trend3, tendIcon, tendText,
    grad: grad != null ? round(grad, 1) : null, gradCat,
    geoWindFrom: field ? round(field.geoWindFrom) : null, toLow: field ? round(field.toLow) : null,
    gh500: round(gh500), ridgeTrough, effects, risks, hemisphere: site.lat >= 0 ? 'Nordhalbkugel' : 'Südhalbkugel'
  };
}

/* ============================================================
   DATA LAYER — fetch + cache + auto refresh (React-Query-like)
   ============================================================ */
const Data = {
  forecast: { json: null, agg: null, fetchedAt: null, error: null, loading: false },
  models: { res: null, consensus: null, fetchedAt: null, error: null },
  stations: { list: [], fetchedAt: null, source: 'Demo' },
  pressure: { field: null, fetchedAt: null, error: null },
  bestNow: { result: null, loading: false, error: null, radiusKm: 50, bundle: {}, userLocation: null, fetchedAt: null, liveAt: null },
  timers: {},
  async loadForecast(force) {
    const site = siteById(Store.state.selectedSiteId);
    this.forecast.loading = true; render();
    try {
      const json = await Providers.fetchForecast(site);
      this.forecast.json = json;
      this.forecast.agg = buildAggregation(json, site);
      this.forecast.fetchedAt = new Date().toISOString();
      this.forecast.error = null;
    } catch (e) {
      this.forecast.error = e.message || 'Fehler';
    } finally {
      this.forecast.loading = false;
    }
    await this.refreshStations();
    this.loadModels();
    this.loadPressureField();
    try { Learn.logForecastReality(site.id, this.decision(), this.stations.list); } catch (e) { /* ignore */ }
    maybeAlert();
    render();
  },
  // BEST SITE NOW: scan all sites in radius, fetch per-site weather + live, rank
  async loadBestSiteNow() {
    this.bestNow.loading = true; if (currentScreen === 'morning') renderMorning();
    if (!this.bestNow.userLocation) this.bestNow.userLocation = await getUserLocation();
    const loc = this.bestNow.userLocation, radius = this.bestNow.radiusKm;
    const inR = SITES.map(s => ({ s, d: Geo.haversineKm(loc.lat, loc.lon, s.lat, s.lon) })).filter(x => x.d <= radius).sort((a, b) => a.d - b.d).slice(0, 8);
    const bundle = {};
    await Promise.all(inR.map(async ({ s }) => {
      try {
        const json = await Providers.fetchForecast(s); const agg = buildAggregation(json, s);
        let st = []; try { st = (await Providers.fetchPioupiou(s, 60)) || []; } catch (e) { st = []; }
        if (!st.length) st = Providers.liveStations(s, agg);
        bundle[s.id] = { agg, fetchedAt: new Date().toISOString(), stations: st };
      } catch (e) { /* skip site */ }
    }));
    this.bestNow.bundle = bundle; this.bestNow.fetchedAt = new Date().toISOString(); this.bestNow.liveAt = new Date().toISOString();
    this.recomputeBestNow(); this.bestNow.loading = false;
    if (currentScreen === 'morning') renderMorning();
  },
  async reloadBestStations() {
    const b = this.bestNow.bundle;
    await Promise.all(Object.keys(b).map(async id => {
      const site = siteById(id);
      try { let st = (await Providers.fetchPioupiou(site, 60)) || []; if (!st.length) st = Providers.liveStations(site, b[id].agg); b[id].stations = st; } catch (e) { /* keep */ }
    }));
    this.bestNow.liveAt = new Date().toISOString(); this.recomputeBestNow();
    if (currentScreen === 'morning') renderMorning();
  },
  recomputeBestNow() {
    const loc = this.bestNow.userLocation; if (!loc) return;
    this.bestNow.result = rankNearbyFlyingSites({ userLocation: loc, radiusKm: this.bestNow.radiusKm, sites: SITES, bundle: this.bestNow.bundle, pilotProfile: Store.state.pilot });
  },
  async loadPressureField() {
    const site = siteById(Store.state.selectedSiteId);
    try { this.pressure.field = await Providers.fetchPressureField(site); this.pressure.error = null; }
    catch (e) { this.pressure.field = null; this.pressure.error = e.message; }
    this.pressure.fetchedAt = new Date().toISOString();
    render();
  },
  async loadModels() {
    const site = siteById(Store.state.selectedSiteId);
    try {
      const res = await Providers.fetchModels(site);
      this.models.res = res;
      this.models.consensus = buildModelConsensus(res, Store.state.day);
      this.models.fetchedAt = new Date().toISOString();
      this.models.error = null;
    } catch (e) { this.models.error = e.message; }
    render();
  },
  async refreshStations() {
    const site = siteById(Store.state.selectedSiteId);
    let real = null;
    try { real = await Providers.fetchPioupiou(site, 60); } catch (e) { real = null; }
    if (real && real.length) { this.stations.list = real; this.stations.source = 'Pioupiou'; }
    else { this.stations.list = Providers.liveStations(site, this.forecast.agg); this.stations.source = 'Demo'; }
    this.stations.fetchedAt = new Date().toISOString();
  },
  decision() {
    if (!this.forecast.agg) return null;
    const site = siteById(Store.state.selectedSiteId);
    return calculateFlightDecision(this.forecast.agg, this.stations.list, site, Store.state.pilot, Store.state.day, this.models.consensus);
  },
  startAutoRefresh() {
    clearInterval(this.timers.weather); clearInterval(this.timers.live);
    this.timers.weather = setInterval(() => { if (navigator.onLine) this.loadForecast(); }, 10 * 60 * 1000);
    this.timers.live = setInterval(async () => { await this.refreshStations(); if (this.models.res) this.models.consensus = buildModelConsensus(this.models.res, Store.state.day); render(); }, 2 * 60 * 1000);
  },
  isStale() {
    if (!this.forecast.fetchedAt) return false;
    return Time.ageMin(this.forecast.fetchedAt) > 20;
  }
};

/* GPS with graceful fallback to the selected site */
function getUserLocation() {
  return new Promise(res => {
    const fallback = (src) => { const s = siteById(Store.state.selectedSiteId); res({ lat: s.lat, lon: s.lon, source: src }); };
    if (!navigator.geolocation) return fallback('Gebiet (kein GPS)');
    navigator.geolocation.getCurrentPosition(
      p => res({ lat: p.coords.latitude, lon: p.coords.longitude, source: 'GPS' }),
      () => fallback('Gebiet (GPS verweigert)'),
      { timeout: 8000, maximumAge: 300000 }
    );
  });
}
let _morningTimers = { ui: null, live: null, fc: null };
function startMorningTimers() {
  stopMorningTimers();
  _morningTimers.ui = setInterval(() => { if (currentScreen === 'morning') { Data.recomputeBestNow(); renderMorning(); } }, BEST_SITE_UI_REFRESH_MS);
  _morningTimers.live = setInterval(() => { if (navigator.onLine && Object.keys(Data.bestNow.bundle).length) Data.reloadBestStations(); }, LIVE_STATION_REFRESH_MS);
  _morningTimers.fc = setInterval(() => { if (navigator.onLine) Data.loadBestSiteNow(); }, FORECAST_REFRESH_MS);
}
function stopMorningTimers() { Object.values(_morningTimers).forEach(t => t && clearInterval(t)); _morningTimers = { ui: null, live: null, fc: null }; }
function setBestRadius(km) { Data.bestNow.radiusKm = km; Data.loadBestSiteNow(); }
function refreshBestLocation() { Data.bestNow.userLocation = null; Data.loadBestSiteNow(); }
function recordFlightFeedback(siteId, rating) {
  Learn.recordFeedback(siteId, rating, Data.decision());
  const el = $('#fbResult'); if (el) { const lim = Learn.effectiveLimits(Store.state.pilot); el.innerHTML = `<div class="explain">✅ Danke! SKYWORTHY lernt mit. ${Learn.data.personal.samples >= 2 && lim.adjusted ? `Dein persönliches Böen-Limit ist jetzt ~${round(lim.maxGustKmh)} km/h.` : ''}</div>`; }
}

/* ============================================================
   UI / RENDER
   ============================================================ */
const sIc = { green: '🟢', yellow: '🟡', orange: '🟠', red: '🔴', black: '🟣', gray: '⚪' };
function statusClass(s) { return 's-' + s; }

function render() {
  renderTopbar();
  const map = {
    cockpit: renderCockpit, sites: renderSites, detail: renderDetail, live: renderLive,
    wind: renderWind, thermal: renderThermal, cloud: renderCloud, models: renderModels,
    profile: renderProfile, exam: renderExam, pro: renderPro, pressure: renderPressure, route: renderRoute,
    windows: renderFlightWindows, compare: renderCompare, morning: renderMorning, more: renderMore
  };
  const cur = currentScreen;
  try { (map[cur] || renderCockpit)(); } catch (e) { console.error(e); const el = $('#screen-' + cur); if (el) el.innerHTML = `<div class="card">Render-Fehler: ${esc(e.message)}</div>`; }
}

function renderTopbar() {
  const dot = $('#conndot'), txt = $('#conntxt');
  if (!navigator.onLine) { dot.className = 'statusdot off'; txt.textContent = 'offline'; }
  else if (Data.isStale()) { dot.className = 'statusdot stale'; txt.textContent = 'veraltet'; }
  else { dot.className = 'statusdot'; txt.textContent = Data.forecast.fetchedAt ? Time.fmtAge(Data.forecast.fetchedAt) : 'live'; }
}

/* ---------- COCKPIT ---------- */
function renderCockpit() {
  const el = $('#screen-cockpit'); const site = siteById(Store.state.selectedSiteId);
  if (Data.forecast.loading && !Data.forecast.agg) { el.innerHTML = loadingCard('Wetterdaten werden geladen…'); return; }
  if (Data.forecast.error && !Data.forecast.agg) { el.innerHTML = errorCard(Data.forecast.error); return; }
  const d = Data.decision(); if (!d) { el.innerHTML = loadingCard('Berechne Flugentscheidung…'); return; }
  if (!d.best) { el.innerHTML = `<div class="card">Für den gewählten Tag liegen keine Tagesdaten vor. Wechsle den Tag oben oder tippe „Aktualisieren".</div>`; return; }
  const sc = statusClass(d.status);
  const rem = REMINDERS[new Date().getDate() % REMINDERS.length];
  const fav = Store.state.favorites.includes(site.id);
  const live = d.decisiveStations[0];
  const simple = Store.state.simple;
  const verdict = { green: 'FLIEG', yellow: 'FLIEG – vorsichtig', orange: 'NUR EXPERTEN', red: 'WARTE', black: 'BLEIB AM BODEN' }[d.status] || '—';
  const confV = round((d.confidenceScore + d.dataQualityScore) / 2);
  const confLabel = confV >= 75 ? 'sehr sicher' : confV >= 58 ? 'ziemlich sicher' : confV >= 42 ? 'mäßig sicher' : 'unsicher – Modelle uneinig';
  const briefing = buildBriefing(d, site);
  window.__briefingText = briefing.text;
  const blockers = buildGreenBlockers(d, site, Store.state.pilot);
  const mismatches = buildForecastReality(d, Data.stations.list);
  const explainKeys = [...new Set(d.topRisks.map(r => riskKey(r)).filter(Boolean))].slice(0, 3);
  const sevColor = { critical: 'black', high: 'red', medium: 'orange', low: 'yellow' };
  const blockersCard = blockers.length ? `<div class="card s-orange"><div class="h" style="margin-top:0">Was fehlt für Grün?</div>
    ${blockers.map(b => `<div class="risk"><div class="ic">${sIc[sevColor[b.severity]] || '🟠'}</div><div class="tx"><b>${esc(b.factor)}: ${esc(b.currentValue)}</b>Soll: ${esc(b.requiredValue)} — ${esc(b.explanation)}</div></div>`).join('')}</div>` : '';
  const realityCard = mismatches.length ? `<div class="card s-red"><div class="h" style="margin-top:0">Prognose vs. Realität</div>
    ${mismatches.map(m => `<div class="risk"><div class="ic">${m.severity === 'critical' ? '🔴' : '🟠'}</div><div class="tx"><b>${esc(m.parameter)}: +${m.deviationPercent}%</b>Prognose ${esc(m.forecastValue)} → Live ${esc(m.liveValue)}. ${esc(m.interpretation)}</div></div>`).join('')}</div>` : '';
  const explainCard = explainKeys.length ? `<div class="card"><div class="h" style="margin-top:0">Verstehen — was bedeutet das?</div>
    ${explainKeys.map(k => { const e = explainRiskFactor(k); return `<div class="risk"><div class="ic">💡</div><div class="tx"><b>${esc(e.factor)}</b>${esc(simple ? e.beginnerText : e.expertText)}<div class="small dim" style="margin-top:4px">🧠 ${esc(e.simpleAnalogy)} · ⚠️ ${esc(e.dangerTranslation)}</div></div></div>`; }).join('')}</div>` : '';

  const advanced = `
  <div class="grid c3">
    ${kpi('Wind Start', round(d.best.h.windKmh), 'km/h', `${Wind.toCompass(d.best.h.windDir)} · Böen ${round(d.best.h.gustKmh)}`)}
    ${kpi('Basis ü. Start', round(d.best.baseAboveTO), 'm', `Basis ${round(d.best.cloudBase)} m`)}
    ${kpi('CAPE', round(d.best.h.cape), 'J/kg', d.best.h.cape > 1200 ? 'erhöht' : 'moderat')}
  </div>
  <div class="grid c2">
    ${miniScore('Anfänger', d.beginnerScore)}
    ${miniScore('Experte', d.expertScore)}
    ${miniScore('Sicherheit', d.safetyScore)}
    ${miniScore('Thermik', d.thermalScore)}
    ${miniScore('Datenqualität', d.dataQualityScore)}
    ${miniScore('Konsens', d.confidenceScore)}
  </div>
  <div class="card">
    <div class="h" style="margin-top:0">Wahrscheinlichkeiten</div>
    ${probBar('Haltbare Thermik', d.probabilities.thermal)}
    ${probBar('Entspannter Flug', d.probabilities.relaxed)}
    ${probBar('Sportliche Bedingungen', d.probabilities.sporty)}
  </div>
  <div class="dim small" style="text-align:center;margin-top:4px">
    Wetter: ${Data.forecast.fetchedAt ? Time.fmtAge(Data.forecast.fetchedAt) : '—'} · Modelle: ${Data.models.fetchedAt ? Time.fmtAge(Data.models.fetchedAt) : '—'} · Live: ${Data.stations.fetchedAt ? Time.fmtAge(Data.stations.fetchedAt) : '—'}
  </div>`;

  el.innerHTML = `
  ${Data.isStale() ? banner('orange', '⚠️ Daten älter als 20 Minuten — bitte aktualisieren.') : ''}
  ${!navigator.onLine ? banner('red', '📵 Offline — angezeigte Daten sind möglicherweise veraltet.') : ''}

  <div class="seg" style="justify-content:flex-end;margin:2px 0 8px">
    <button class="${simple ? 'on' : ''}" onclick="setSimple(true)">Einfach</button>
    <button class="${!simple ? 'on' : ''}" onclick="setSimple(false)">Profi</button>
  </div>

  <div class="hero card statusborder ${sc}" style="text-align:center">
    <div class="ring" style="background:var(--c)"></div>
    <div class="muted small" style="letter-spacing:3px;text-transform:uppercase;font-weight:800">Flugentscheidung · ${esc(site.name)}</div>
    <div class="glabel" style="color:var(--c);margin-top:8px;font-size:clamp(40px,13vw,66px);line-height:1.02;font-weight:900">${esc(verdict)}</div>
    <div class="muted" style="margin-top:6px">${sIc[d.status]} ${esc(d.recommendedFlightType)} · Sicherheit: <b style="color:var(--c)">${esc(confLabel)}</b></div>
    <div class="meta" style="margin-top:16px">
      <div>Startfenster<b>${esc(d.bestStartTime || '—')}</b></div>
      <div>Bester Start<b>${esc(d.bestTakeoff || '—')}</b></div>
      ${d.latestSafeStartTime ? `<div>Spätestens<b>${esc(d.latestSafeStartTime)}</b></div>` : `<div>Flugart<b>${esc(d.recommendedFlightType)}</b></div>`}
    </div>
  </div>

  <div class="card">
    <div class="h" style="margin-top:0;display:flex;align-items:center;gap:8px">🎧 Dein Fluglehrer
      <button class="btn sec" id="speakBtn" style="margin-left:auto;width:auto;padding:7px 14px" onclick="speakBriefing(this)">▶ Vorlesen</button>
    </div>
    <div style="font-size:15px;line-height:1.65">${esc(briefing.text)}</div>
  </div>

  ${simple ? `<div class="card ${sc}"><div class="risk"><div class="ic">${(d.topRisks[0] || '🟢 ').slice(0, 2)}</div><div class="tx"><b>Hauptgefahr</b>${esc((d.topRisks[0] || 'Keine dominante Gefahr — trotzdem vor Ort prüfen.').slice(2).trim())}</div></div></div>`
    : `<div class="card ${sc}">
    <div class="h" style="margin-top:0">Warum? — Top-Risiken</div>
    ${d.topRisks.slice(0, 4).map(r => `<div class="risk"><div class="ic">${r.slice(0, 2)}</div><div class="tx">${esc(r.slice(2).trim())}</div></div>`).join('')}
  </div>
  ${blockersCard}
  ${realityCard}
  ${explainCard}`}

  <div class="seg" style="overflow-x:auto;flex-wrap:nowrap">
    <button onclick="go('wind')">🌬️ Wind</button>
    <button onclick="go('thermal')">🔥 Thermik</button>
    <button onclick="go('pressure')">🎚️ Druck</button>
    <button onclick="go('cloud')">☁️ Wolken</button>
    <button onclick="go('route')">🛰️ Flugweg 3D</button>
    <button onclick="go('detail')">📋 Gebiet</button>
    <button onclick="go('models')">🧮 Modelle</button>
  </div>

  ${live ? `<div class="card"><div class="h" style="margin-top:0">Live-Wind (ausschlaggebend)</div>
    <div class="sitecard"><div class="gp ${sc}">${windArrow(live.windDirection)}</div>
    <div class="info"><b>${esc(live.name)}</b><div>${live.windSpeedKmh} km/h · Böen ${live.gustKmh} · ${Wind.toCompass(live.windDirection)}${live.elevation != null ? ' · ' + live.elevation + ' m' : ''}</div></div>
    <div class="r">${Time.fmtAge(live.updatedAt)}<br><span class="dim">${esc(Data.stations.source)}</span></div></div></div>` : ''}

  ${webcamCard(site)}

  ${simple ? '' : advanced}
  ${simple ? '' : `<div class="reminder"><div class="i">${rem.i}</div><div><b>${esc(rem.t)}</b><br>${esc(rem.d)}</div></div>`}

  <div class="row" style="margin-top:12px">
    <button class="btn sec" onclick="go('detail')">Gebiet-Details</button>
    <button class="btn sec" onclick="toggleFav('${site.id}')">${fav ? '★ Favorit' : '☆ Favorit'}</button>
  </div>`;
}

/* plain-language mentor briefing built from the decision */
function buildBriefing(d, site) {
  const h = d.best.h, comp = Wind.toCompass(h.windDir);
  const windWord = h.windKmh < 8 ? 'schwacher' : h.windKmh < 18 ? 'mäßiger' : h.windKmh < 28 ? 'kräftiger' : 'starker';
  const gusty = h.gustKmh > h.windKmh * 1.5 + 6;
  const s = [];
  s.push({
    green: `Heute ist ein guter Flugtag an ${site.name}.`,
    yellow: `Heute geht es an ${site.name}, aber mit Vorsicht.`,
    orange: `Anspruchsvoll heute — nur für erfahrene Piloten.`,
    red: `Heute bleibst du besser am Boden und wartest.`,
    black: `Heute ist ein klarer No-Go-Tag.`
  }[d.status]);
  s.push(`Es weht ${windWord} Wind aus ${comp} mit etwa ${round(h.windKmh)} km/h${gusty ? `, böig bis ${round(h.gustKmh)}` : ''}.`);
  if (h.cape > 200) {
    const base = round(d.best.cloudBase), above = round(d.best.baseAboveTO);
    s.push(`Thermik ist zu erwarten, die Basis liegt bei rund ${base} Metern${above > 0 ? ` — etwa ${above} Meter über dem Start` : ''}.`);
  } else {
    s.push(`Viel Thermik ist nicht zu erwarten — eher ein ruhiger Soaring- oder Abgleiter-Tag.`);
  }
  if (d.status !== 'black' && d.status !== 'red') {
    s.push(`Dein bestes Startfenster ist ${d.bestStartTime || '—'}${d.latestSafeStartTime ? `, spätestens ${d.latestSafeStartTime}` : ''}. Empfohlen: ${d.recommendedFlightType} an ${d.bestTakeoff || site.takeoffs[0].name}.`);
  }
  const r0 = d.topRisks && d.topRisks[0] ? d.topRisks[0].replace(/^[^\p{L}]+/u, '').trim() : '';
  if (r0 && d.status !== 'green') s.push(`Achte besonders auf: ${r0}.`);
  s.push(d.status === 'green' ? 'Genieß den Flug — und prüf vor dem Start nochmal den Livewind.' : 'Im Zweifel gilt immer: nicht starten.');
  return { sentences: s, text: s.join(' ') };
}
let _speaking = false;
function speakBriefing(btn) {
  const reset = () => { _speaking = false; const b = document.getElementById('speakBtn'); if (b) b.textContent = '▶ Vorlesen'; };
  if (typeof window.speechSynthesis === 'undefined') { if (btn) btn.textContent = 'Stimme nicht verfügbar'; return; }
  if (_speaking) { window.speechSynthesis.cancel(); reset(); return; }
  const text = window.__briefingText || ''; if (!text) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = 'de-DE'; u.rate = 0.98; u.pitch = 1.0;
  const vs = window.speechSynthesis.getVoices(); const de = vs.find(v => /^de(-|_|$)/i.test(v.lang)); if (de) u.voice = de;
  u.onend = reset; u.onerror = reset;
  _speaking = true; if (btn) btn.textContent = '⏹ Stopp';
  window.speechSynthesis.speak(u);
}

/* ---------- MEHR (hub) ---------- */
function renderMore() {
  const el = $('#screen-more');
  const items = [
    ['wind', '🌬️', 'Wind-Intelligenz', 'Höhenwind, Gradient, Scherung, Föhn, Windprofil'],
    ['thermal', '🔥', 'Thermik & Emagram', 'Thermikfenster, Basis, CAPE, Sounding'],
    ['pressure', '🎚️', 'Druck (Hoch/Tief)', 'Live-Lage, Gradient + komplettes Druckwissen'],
    ['cloud', '☁️', 'Wolken & Gewitter', 'Bewölkung, Niederschlag, Radar, Gewitterrisiko'],
    ['windows', '🪟', 'Flugfenster', 'Tagesfenster mit Status & Anfänger/Experten-Rat'],
    ['compare', '⚖️', 'Anfänger vs. Experte', 'Dieselbe Lage aus zwei Perspektiven'],
    ['models', '🧮', 'Modellvergleich', 'ICON · ECMWF · GFS · AROME · GEM'],
    ['route', '🛰️', 'Flugweg (3D)', 'Idealer Weg im 3D-Gelände, animiert'],
    ['detail', '📋', 'Gebiet-Details', 'Startplätze, Landeplätze, Gebiets-DNA'],
    ['profile', '👤', 'Pilotenprofil', 'Level, Limits, Warnungen, Datenquellen'],
    ['pro', '⭐', 'SKYWORTHY Pro', 'Alle Elite-Features · 49 €/Jahr']
  ];
  el.innerHTML = `
  <div class="h" style="margin-top:6px">Mehr</div>
  <div class="grid c2">
    ${items.map(it => `<div class="card" style="cursor:pointer;margin-bottom:0" onclick="go('${it[0]}')">
      <div style="font-size:26px">${it[1]}</div>
      <b style="display:block;margin-top:6px">${esc(it[2])}</b>
      <div class="small muted" style="margin-top:3px">${esc(it[3])}</div>
    </div>`).join('')}
  </div>
  <div class="dim small" style="text-align:center;margin-top:14px">SKYWORTHY · Elite Paragliding Decision Cockpit</div>`;
}

/* ---------- MORNING / BEST SITE NOW (killer feature) ---------- */
function ampelWord(s) { return { green: 'Grün', yellow: 'Gelb', orange: 'Orange', red: 'Rot', black: 'No-Go', gray: '—' }[s] || s; }
function renderMorning() {
  const el = $('#screen-morning'); if (!el) return;
  const bn = Data.bestNow, r = bn.result;
  const radii = [25, 50, 75, 100];
  const head = `
  <div class="h" style="margin-top:6px">Wohin soll ich jetzt fahren?</div>
  <div class="seg">${radii.map(km => `<button class="${bn.radiusKm === km ? 'on' : ''}" onclick="setBestRadius(${km})">${km} km</button>`).join('')}</div>
  <div class="row" style="margin-bottom:10px">
    <button class="btn sec" onclick="refreshBestLocation()">📍 Standort & Scan</button>
    <div class="pill" style="align-self:center">${bn.userLocation ? esc(bn.userLocation.source) : 'kein Standort'}</div>
  </div>`;
  if (bn.loading && !r) { el.innerHTML = head + loadingCard('Umkreis wird gescannt — Wetter, Livewind & Gebiete…'); return; }
  if (!r) { el.innerHTML = head + `<div class="card"><b>Bereit.</b><div class="small muted" style="margin-top:6px">Tippe „📍 Standort & Scan", um die beste Flugoption im ${bn.radiusKm}-km-Umkreis zu finden.</div><button class="btn" style="margin-top:12px" onclick="refreshBestLocation()">Jetzt scannen</button></div>`; return; }
  const ages = `<div class="dim small" style="text-align:center;margin-top:6px">Ranking aktualisiert vor ${bn.result ? Time.fmtAge(bn.result.updatedAt) : '—'} · Livewind vor ${bn.liveAt ? Time.fmtAge(bn.liveAt) : '—'} · Forecast vor ${bn.fetchedAt ? Time.fmtAge(bn.fetchedAt) : '—'}</div>`;
  if (!r.bestSite) { el.innerHTML = head + banner('orange', r.globalWarning || 'Keine Option gefunden.') + ages; return; }
  const b = r.bestSite, sc = 's-' + b.status;
  const drive = b.status === 'red' || b.status === 'black'
    ? `Heute besser nicht fahren — ${esc(b.siteName)} ist die „beste" Option, aber ${ampelWord(b.status)}.`
    : `Fahr zu ${esc(b.siteName)} / ${esc(b.bestTakeoff || '—')}.`;
  const shortCard = `
  <div class="hero card statusborder ${sc}">
    <div class="ring" style="background:var(--c)"></div>
    <div class="muted small" style="letter-spacing:2px;text-transform:uppercase;font-weight:800">Beste Flugoption jetzt</div>
    <div class="glabel" style="color:var(--c);margin-top:6px;font-size:clamp(26px,7vw,38px);line-height:1.1">${esc(drive)}</div>
    <div class="muted" style="margin-top:8px">${b.status !== 'red' && b.status !== 'black' ? `Beste Zeit: <b style="color:var(--c)">${esc(b.bestStartWindow.from)}–${esc(b.bestStartWindow.to)}</b> · ` : ''}${b.distanceKm} km</div>
    <div class="meta" style="margin-top:12px">
      <div>Anfänger<b>${sIc[b.beginnerStatus]} ${ampelWord(b.beginnerStatus)}</b></div>
      <div>Experte<b>${sIc[b.expertStatus]} ${ampelWord(b.expertStatus)}</b></div>
      <div>Confidence<b>${b.confidencePercent}%</b></div>
      <div>Daten<b>${b.dataAgeMinutes} min</b></div>
    </div>
    ${b.whyBest.length ? `<div class="small" style="margin-top:12px;text-align:left"><b style="color:var(--c)">Warum:</b> ${esc(b.whyBest.join(' · '))}.</div>` : ''}
    ${b.nextCriticalChange ? `<div class="small" style="margin-top:6px;text-align:left;color:var(--orange)"><b>Achtung:</b> ab ${esc(b.nextCriticalChange.time)} ${esc(b.nextCriticalChange.reason)}.</div>` : ''}
    <div class="small dim" style="margin-top:6px;text-align:left">Vertrauen: ${esc(b.trust.finalTrustLabel)} (${b.trust.confidencePercent}%)${b.trust.conflictingSignals.length ? ' · ⚠️ ' + esc(b.trust.conflictingSignals[0]) : ''}</div>
    <div class="row" style="margin-top:12px">
      <button class="btn" onclick="selectSite('${b.siteId}', true)">Details ansehen</button>
      <a class="btn sec" href="https://www.windy.com/webcams?${siteById(b.siteId).lat.toFixed(4)},${siteById(b.siteId).lon.toFixed(4)},11" target="_blank" rel="noopener" style="text-decoration:none;text-align:center">📷 Webcams</a>
    </div>
  </div>`;
  const others = r.rankedSites.slice(1, 6).map(s => `<div class="card s-${s.status}" style="cursor:pointer" onclick="selectSite('${s.siteId}', true)">
    <div style="display:flex;align-items:center;gap:10px">
      <div class="gp s-${s.status}" style="min-width:auto;padding:6px 10px;border-radius:9px;font-weight:800">${sIc[s.status]} ${s.score}</div>
      <div style="flex:1"><b>${esc(s.siteName)}</b><div class="small muted">${s.distanceKm} km · ${esc(s.bestTakeoff || '—')} · ${esc(s.expectedFlightType)}</div></div>
      <div class="r small">${b.status !== 'red' ? esc(s.bestStartWindow.from) : ''}<br><span class="dim">A:${sIc[s.beginnerStatus]} E:${sIc[s.expertStatus]}</span></div>
    </div>
    ${s.whyBest && s.whyBest.length ? `<div class="small dim" style="margin-top:6px">${esc(s.whyBest.slice(0, 2).join(' · '))}</div>` : ''}
  </div>`).join('');
  const fb = `<div class="card"><div class="h" style="margin-top:0">🪂 Geflogen? SKYWORTHY lernt mit</div>
    <div class="small muted">War es so wie angekündigt? Dein Feedback verbessert die Einschätzung für dieses Gebiet.</div>
    <div class="seg" style="margin-top:8px">
      <button onclick="recordFlightFeedback('${b.siteId}','easier')">🟢 ruhiger</button>
      <button onclick="recordFlightFeedback('${b.siteId}','as_expected')">⚪ wie erwartet</button>
      <button onclick="recordFlightFeedback('${b.siteId}','harder')">🔴 ruppiger</button>
    </div><div id="fbResult"></div></div>`;
  el.innerHTML = head + (r.globalWarning ? banner('orange', r.globalWarning) : '') + shortCard +
    (others ? `<div class="h">Weitere Optionen</div>${others}` : '') + fb + ages +
    `<div class="dim small" style="text-align:center">Killer-Frage beantwortet: wohin, wann, für wen, wie sicher, was kippt. Livewind & Himmel vor Ort entscheiden.</div>`;
}

/* ---------- FLIGHT WINDOWS ---------- */
function buildFlightWindows(agg, site, pilot, day) {
  const idx = agg.daylightIdx(day); if (!idx.length) return [];
  const clsOf = i => {
    const h = agg.atHour(i);
    const lee = Wind.matches(h.windDir, site.dangerousWindDirections, 35);
    const storm = h.cape > 1500 || h.precipP > 50;
    if (storm || lee || h.gustKmh > pilot.maxGustKmh + 10) return 'red';
    if (h.gustKmh > pilot.maxGustKmh || h.windKmh > pilot.maxWindKmh || h.precipP > 30) return 'orange';
    if (h.gustKmh > pilot.maxGustKmh * 0.8 || h.cape > 900 || h.windKmh > pilot.maxWindKmh * 0.85) return 'yellow';
    return 'green';
  };
  const rank = { green: 0, yellow: 1, orange: 2, red: 3 };
  const wins = []; let cur = null;
  idx.forEach(i => {
    const st = clsOf(i), h = agg.atHour(i);
    if (!cur || cur.st !== st) { if (cur) wins.push(cur); cur = { st, startI: i, endI: i, peakGust: h.gustKmh, peakCape: h.cape, dir: h.windDir }; }
    else { cur.endI = i; cur.peakGust = Math.max(cur.peakGust, h.gustKmh); cur.peakCape = Math.max(cur.peakCape, h.cape); }
  });
  if (cur) wins.push(cur);
  return wins.map(w => {
    const sH = agg.atHour(w.startI), eH = agg.atHour(w.endI);
    const ft = w.st === 'red' ? 'Nicht fliegen' : w.peakCape > 1200 ? 'Thermikflug / XC' : w.peakCape > 350 ? 'Thermikflug' : 'Abgleiter / Soaring';
    const reason = w.st === 'green' ? 'Wind & Böen im Rahmen, ruhige Bedingungen.'
      : w.st === 'yellow' ? 'Bedingungen werden aktiver — aufmerksam fliegen.'
      : w.st === 'orange' ? `Kräftig: Böen bis ~${round(w.peakGust)} km/h${w.peakCape > 900 ? ', Thermik aktiv' : ''}.`
      : `Kritisch: ${Wind.matches(w.dir, site.dangerousWindDirections, 35) ? 'Lee-Richtung' : w.peakCape > 1500 ? 'Überentwicklung' : 'Böen über Limit'}.`;
    return { start: sH.hh, end: eH.hh, status: w.st, reason, flightType: ft,
      beginnerAdvice: w.st === 'green' ? 'Gutes Fenster für Anfänger, wenn der Startplatzwind sauber ansteht.' : w.st === 'yellow' ? 'Nur fliegen, wenn du aktiv steuern kannst und die Lage klar passt.' : 'Für Anfänger nicht geeignet — lieber ein ruhigeres Fenster wählen.',
      expertAdvice: w.st === 'red' ? 'Auch für Erfahrene heikel — Lage genau prüfen, im Zweifel No-Go.' : w.st === 'orange' ? 'Machbar bei kontrollierbarem Wind und ohne Überentwicklung.' : 'Gutes Fenster für Thermik/XC.' };
  });
}
function renderFlightWindows() {
  const el = $('#screen-windows'); const agg = Data.forecast.agg; const site = siteById(Store.state.selectedSiteId);
  if (!agg) { el.innerHTML = loadingCard('Flugfenster…'); return; }
  const wins = buildFlightWindows(agg, site, Store.state.pilot, Store.state.day);
  el.innerHTML = `
  <div class="h" style="margin-top:6px">Flugfenster — ${esc(site.name)}</div>
  <div class="small dim" style="margin:-4px 4px 10px">Tagesverlauf in Fenstern, bewertet für dein Profil (Böen-Limit ${Store.state.pilot.maxGustKmh} km/h). Wechsle Tag oben um.</div>
  ${wins.length ? wins.map(w => `<div class="card s-${w.status}">
    <div style="display:flex;align-items:center;gap:10px"><div class="gp s-${w.status}" style="min-width:auto;padding:4px 10px;border-radius:8px;font-weight:800">${w.start}–${w.end}</div>
      <div><b>${sIc[w.status]} ${esc(w.flightType)}</b><div class="small muted">${esc(w.reason)}</div></div></div>
    <div class="risk" style="margin-top:8px"><div class="ic">🟢</div><div class="tx"><b>Anfänger</b>${esc(w.beginnerAdvice)}</div></div>
    <div class="risk"><div class="ic">🚀</div><div class="tx"><b>Experte</b>${esc(w.expertAdvice)}</div></div>
  </div>`).join('') : '<div class="card small muted">Keine Tagesdaten verfügbar.</div>'}
  <div class="dim small" style="text-align:center">Fenster sind eine Orientierung — Livewind & Himmel vor Ort entscheiden.</div>`;
}

/* ---------- BEGINNER vs EXPERT COMPARISON ---------- */
function renderCompare() {
  const el = $('#screen-compare'); const dual = dualDecision();
  if (!dual) { el.innerHTML = loadingCard('Vergleich…'); return; }
  const col = (title, icon, d) => `<div class="card s-${d.status}">
    <div class="small muted" style="font-weight:800;letter-spacing:1px">${icon} ${esc(title)}</div>
    <div class="glabel" style="color:var(--c);font-size:30px;margin-top:4px">${sIc[d.status]} ${d.overallScore}</div>
    <div class="muted small" style="font-weight:700">${esc(d.label)}</div>
    <hr class="sep">
    <div class="small"><b>Empfehlung:</b> ${esc(d.recommendedFlightType)}</div>
    <div class="small" style="margin-top:3px"><b>Startfenster:</b> ${esc(d.bestStartTime || '—')}</div>
    <div class="small" style="margin-top:3px"><b>Top-Risiko:</b> ${esc((d.topRisks[0] || '—').replace(/^[^\p{L}]+/u, '').trim())}</div>
  </div>`;
  el.innerHTML = `
  <div class="h" style="margin-top:6px">Anfänger vs. Experte</div>
  <div class="small dim" style="margin:-4px 4px 10px">Dieselbe Wetterlage, zwei Profile (EN-A, 22 km/h-Böen vs. EN-C, 35 km/h-Böen). So siehst du, warum „fliegbar" relativ ist.</div>
  <div class="grid c2">
    ${col('Anfänger', '🟢', dual.beginner)}
    ${col('Experte', '🚀', dual.expert)}
  </div>
  <div class="card"><div class="h" style="margin-top:0">Der Unterschied</div>
    <div class="risk"><div class="ic">💡</div><div class="tx">${dual.beginner.status === dual.expert.status
      ? esc('Beide Profile kommen heute zur selben Einschätzung — die Lage ist eindeutig.')
      : esc(`Für Anfänger „${dual.beginner.label}", für Experten „${dual.expert.label}". Erfahrung, Schirmklasse und höhere Limits verschieben die Grenze — nicht der Himmel ändert sich, sondern wer ihn sicher nutzen kann.`)}</div></div>
  </div>`;
}

/* ---------- NEARBY SITES ---------- */
let userPos = null;
function renderSites() {
  const el = $('#screen-sites');
  el.innerHTML = `
  <div class="h" style="margin-top:6px">Fluggebiet finden</div>
  <input class="input" id="siteSearch" placeholder="🔎 Name oder Region suchen…" value="" />
  <div class="seg" id="filterSeg">
    ${['alle', 'anfänger', 'thermik', 'soaring', 'xc', 'hike', 'heute fliegbar', 'favoriten'].map(f => `<button data-f="${f}" class="${f === 'alle' ? 'on' : ''}">${f}</button>`).join('')}
  </div>
  <div class="row" style="margin-bottom:10px">
    <button class="btn sec" id="geoBtn">📍 In meiner Nähe</button>
    <select class="input" id="radiusSel" style="margin-top:0;max-width:130px">
      <option value="0">Radius: alle</option><option value="50">50 km</option><option value="100" selected>100 km</option><option value="200">200 km</option>
    </select>
  </div>
  <div id="sitesMap" class="map" style="margin-bottom:12px"></div>
  <div id="sitesList"></div>`;

  $('#siteSearch').addEventListener('input', renderSitesList);
  $$('#filterSeg button').forEach(b => b.addEventListener('click', () => { $$('#filterSeg button').forEach(x => x.classList.remove('on')); b.classList.add('on'); renderSitesList(); }));
  $('#radiusSel').addEventListener('change', renderSitesList);
  $('#geoBtn').addEventListener('click', () => {
    if (!navigator.geolocation) return alert('Geolocation nicht verfügbar.');
    $('#geoBtn').textContent = '… ortet';
    navigator.geolocation.getCurrentPosition(
      p => { userPos = { lat: p.coords.latitude, lon: p.coords.longitude }; $('#geoBtn').textContent = '📍 Standort aktiv'; renderSitesList(); initSitesMap(); },
      () => { $('#geoBtn').textContent = '📍 In meiner Nähe'; alert('Standort nicht verfügbar.'); }
    );
  });
  renderSitesList(); initSitesMap();
}
function activeFilter() { const b = $('#filterSeg button.on'); return b ? b.dataset.f : 'alle'; }
function decoratedSites() {
  const q = ($('#siteSearch')?.value || '').toLowerCase().trim();
  const radius = +($('#radiusSel')?.value || 0);
  let list = SITES.map(s => {
    const dist = userPos ? round(Geo.haversineKm(userPos.lat, userPos.lon, s.lat, s.lon), 0) : null;
    return { s, dist };
  });
  if (q) list = list.filter(({ s }) => (s.name + ' ' + s.region + ' ' + s.country).toLowerCase().includes(q));
  if (radius && userPos) list = list.filter(x => x.dist <= radius);
  const f = activeFilter();
  if (f === 'anfänger') list = list.filter(x => x.s.beginnerFriendly);
  else if (['thermik', 'soaring', 'xc', 'hike'].includes(f)) list = list.filter(x => x.s.flightTypes.includes(f));
  else if (f === 'favoriten') list = list.filter(x => Store.state.favorites.includes(x.s.id));
  if (userPos) list.sort((a, b) => a.dist - b.dist);
  return { list, f };
}
function quickStatusFor(site) {
  // lightweight: only the selected site has full data; others get a heuristic from idealdirs unknown → gray
  if (site.id === Store.state.selectedSiteId && Data.forecast.agg) {
    const d = Data.decision(); return d ? d.status : 'gray';
  }
  return 'gray';
}
function renderSitesList() {
  const wrap = $('#sitesList'); if (!wrap) return;
  const { list, f } = decoratedSites();
  if (!list.length) { wrap.innerHTML = `<div class="card dim small">Keine Gebiete gefunden${f === 'favoriten' ? ' — noch keine Favoriten markiert.' : '.'}</div>`; return; }
  wrap.innerHTML = list.map(({ s, dist }) => {
    const st = quickStatusFor(s); const sc = statusClass(st);
    const isSel = s.id === Store.state.selectedSiteId;
    let bestTime = '—';
    if (isSel && Data.forecast.agg) { const d = Data.decision(); if (d) bestTime = d.bestStartTime; }
    return `<div class="card" style="margin-bottom:8px">
      <div class="sitecard" onclick="selectSite('${s.id}')">
        <div class="gp ${sc}">${sIc[st]}</div>
        <div class="info"><b>${esc(s.name)}</b>
          <div>${esc(s.region)} · ${esc(s.country)} · ${s.elevationMin}–${s.elevationMax} m</div>
          <div>${s.flightTypes.map(t => `<span class="tag">${t}</span>`).join('')}</div>
        </div>
        <div class="r">${dist != null ? `${dist} km<br>` : ''}${isSel ? `Fenster<br><b>${bestTime}</b>` : '<span class="dim">tippen</span>'}</div>
      </div>
      <div class="row" style="margin-top:8px">
        <button class="btn sec small" onclick="selectSite('${s.id}',true)">Cockpit</button>
        <button class="btn sec small" onclick="selectSite('${s.id}');go('detail')">Details</button>
        <span class="fav ${Store.state.favorites.includes(s.id) ? 'on' : ''}" onclick="toggleFav('${s.id}')" style="align-self:center;padding:0 8px">★</span>
      </div>
    </div>`;
  }).join('');
}
let _sitesMap = null, _liveMap = null, _cloudMap = null;
function initSitesMap() {
  if (typeof L === 'undefined') { const m = $('#sitesMap'); if (m) m.innerHTML = '<div class="loading small">Karte offline nicht verfügbar.</div>'; return; }
  const elm = $('#sitesMap'); if (!elm) return;
  if (_sitesMap) { _sitesMap.remove(); _sitesMap = null; }
  _sitesMap = L.map(elm, { attributionControl: false, zoomControl: true });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 17 }).addTo(_sitesMap);
  const pts = [];
  SITES.forEach(s => {
    const st = quickStatusFor(s);
    const m = L.circleMarker([s.lat, s.lon], { radius: 9, color: statusColor(st), fillColor: statusColor(st), fillOpacity: .85, weight: 2 }).addTo(_sitesMap);
    m.bindPopup(`<b>${esc(s.name)}</b><br>${esc(s.region)}`); m.on('click', () => selectSite(s.id));
    pts.push([s.lat, s.lon]);
  });
  if (userPos) { L.marker([userPos.lat, userPos.lon]).addTo(_sitesMap).bindPopup('Dein Standort'); pts.push([userPos.lat, userPos.lon]); }
  _sitesMap.fitBounds(pts, { padding: [30, 30] });
}
function statusColor(s) { return { green: '#22e08a', yellow: '#ffd23f', orange: '#ff9d2e', red: '#ff4d5e', black: '#b026ff', gray: '#5d6878' }[s]; }

/* ---------- SITE DETAIL ---------- */
function renderDetail() {
  const el = $('#screen-detail'); const site = siteById(Store.state.selectedSiteId);
  const d = Data.decision();
  el.innerHTML = `
  <div class="h" style="margin-top:6px">${esc(site.name)}</div>
  <div class="card">
    <div class="small muted">${esc(site.region)} · ${esc(site.country)} · ${site.elevationMin}–${site.elevationMax} m</div>
    <div style="margin-top:8px">${site.flightTypes.map(t => `<span class="tag">${t}</span>`).join('')} ${site.foehnSensitive ? '<span class="tag" style="color:var(--orange)">föhnanfällig</span>' : ''} ${site.beginnerFriendly ? '<span class="tag" style="color:var(--green)">anfängergeeignet</span>' : '<span class="tag" style="color:var(--orange)">fortgeschritten</span>'}</div>
    <hr class="sep">
    <div class="small"><b>Ideale Windrichtung:</b> ${site.idealWindDirections.join(', ')}</div>
    <div class="small" style="color:var(--red);margin-top:4px"><b>Gefährlich / Lee:</b> ${site.dangerousWindDirections.join(', ')}</div>
    <div class="small muted" style="margin-top:8px">${esc(site.thermalNotes)}</div>
    <div class="small muted" style="margin-top:4px"><b>Talwind:</b> ${esc(site.valleyWindNotes)}</div>
  </div>

  ${webcamCard(site)}

  ${d ? `<div class="card ${statusClass(d.status)}"><div class="row">
    <div><div class="kpi"><div class="lbl">Heute</div><div class="val" style="color:var(--c)">${sIc[d.status]} ${d.overallScore}</div><div class="sub">${esc(d.recommendedFlightType)}</div></div></div>
    <div><div class="kpi"><div class="lbl">Anfänger</div><div class="val">${d.beginnerScore}</div></div></div>
    <div><div class="kpi"><div class="lbl">Experte</div><div class="val">${d.expertScore}</div></div></div>
  </div></div>` : ''}

  <div class="h">Startplätze</div>
  ${site.takeoffs.map(to => {
    let m = '';
    if (d) { const ok = Wind.matches(d.best.h.windDir, to.orientation); const lee = Wind.matches(d.best.h.windDir, to.leeDangerDirections, 35); m = lee ? '🔴 Lee jetzt' : ok ? '🟢 Richtung passt' : '🟡 Richtung grenzwertig'; }
    return `<div class="card">
      <div style="display:flex;justify-content:space-between"><b>${esc(to.name)}</b><span class="pill">${to.difficulty}</span></div>
      <div class="small muted" style="margin-top:6px">${esc(to.notes)}</div>
      <div class="grid c3" style="margin-top:10px">
        ${kpi('Ausrichtung', to.orientation.join('/'), '', '')}
        ${kpi('Ideal-Wind', `${to.idealWindMinKmh}–${to.idealWindMaxKmh}`, 'km/h', '')}
        ${kpi('Höhe', to.elevation, 'm', '')}
      </div>
      <div class="small" style="margin-top:8px">Max-Böe Anfänger <b>${to.maxGustKmhBeginner}</b> · Experte <b>${to.maxGustKmhExpert}</b> km/h</div>
      <div class="small" style="color:var(--red);margin-top:4px">Lee/Gefahr: ${to.leeDangerDirections.join(', ')}</div>
      ${m ? `<div class="small" style="margin-top:6px;font-weight:700">${m}</div>` : ''}
    </div>`;
  }).join('')}

  <div class="h">Landeplätze</div>
  ${site.landings.map(lz => `<div class="card"><b>${esc(lz.name)}</b><div class="small muted" style="margin-top:4px">${esc(lz.notes)} · ${lz.elevation} m</div></div>`).join('')}

  <div class="h">Lokale Gefahren</div>
  <div class="card">${site.leeRisks.map(r => `<div class="risk"><div class="ic">⚠️</div><div class="tx">${esc(r)}</div></div>`).join('')}</div>

  <div class="h">Anfänger</div><div class="card small muted">${esc(site.beginnerNotes)}</div>
  <div class="h">Experten</div><div class="card small muted">${esc(site.expertNotes)}</div>
  <div class="h">Regeln & Notfall</div>
  <div class="card">${site.siteRules.map(r => `<div class="risk"><div class="ic">📋</div><div class="tx">${esc(r)}</div></div>`).join('')}
    ${site.emergencyNotes.map(r => `<div class="risk"><div class="ic">🚨</div><div class="tx">${esc(r)}</div></div>`).join('')}</div>

  ${(() => { const dna = siteDNA(site); const blk = (icon, title, arr) => arr && arr.length ? `<div class="card"><div class="h" style="margin-top:0">${icon} ${esc(title)}</div>${arr.map(x => `<div class="risk"><div class="ic">${icon}</div><div class="tx">${esc(x)}</div></div>`).join('')}</div>` : '';
    return `<div class="h">Gebiets-DNA</div>
    ${blk('🪤', 'Typische Fallen', dna.commonTraps)}
    ${blk('🌬️', 'Lokale Windsysteme', dna.localWindSystems)}
    ${blk('🟢', 'Häufige Anfängerfehler', dna.beginnerMistakes)}
    ${blk('🚀', 'Chancen für Experten', dna.expertOpportunities)}
    ${blk('🪂', 'Rückzug / Escape', dna.escapeRoutes)}
    ${blk('🚩', 'Red Flags', dna.redFlagPatterns)}
    <div class="card small muted">Beste Saison: ${dna.bestSeason.join(' · ')}</div>`; })()}`;
}

/* ---------- LIVE STATIONS ---------- */
function renderLive() {
  const el = $('#screen-live'); const site = siteById(Store.state.selectedSiteId);
  const list = Data.stations.list; const d = Data.decision();
  el.innerHTML = `
  <div class="h" style="margin-top:6px">Live-Stationen — ${esc(site.name)}</div>
  ${Data.stations.source === 'Pioupiou'
    ? banner('green', '✓ Echte Live-Daten von Pioupiou (CC-BY, fair use). Weitere Quellen (Holfuy, Windy, Burnair) brauchen API-Keys — siehe Profil → Info.')
    : banner('yellow', 'ℹ️ Keine Pioupiou-Station im Umkreis (60 km) — Anzeige simuliert (Demo). Echte Quellen (Holfuy, Windy, Burnair) brauchen API-Keys — siehe Profil → Info.')}
  <div id="liveMap" class="map" style="margin-bottom:12px"></div>
  ${d && d.decisiveStations[0] ? `<div class="card ${statusClass(d.status)}"><div class="small muted">Ausschlaggebend</div><b>${esc(d.decisiveStations[0].name)}</b>
    <div class="small">Prognose ${round(d.best.h.windKmh)} km/h vs. Live ${d.decisiveStations[0].windSpeedKmh} km/h ${Math.abs(d.decisiveStations[0].windSpeedKmh - d.best.h.windKmh) > 10 ? '⚠️ Abweichung' : '✓'}</div></div>` : ''}
  ${list.map(s => {
    const ageOk = Time.ageMin(s.updatedAt) < 15;
    return `<div class="card"><div class="sitecard">
      <div class="gp s-${s.gustKmh > Store.state.pilot.maxGustKmh ? 'red' : s.windSpeedKmh < 5 ? 'gray' : 'green'}">${windArrow(s.windDirection)}</div>
      <div class="info"><b>${esc(s.name)}</b><div>${s.windSpeedKmh} km/h · Böen ${s.gustKmh} · ${Wind.toCompass(s.windDirection)} (${s.windDirection}°)</div>
      <div>${s.elevation != null ? s.elevation + ' m · ' : ''}${s.distanceKm} km · ${stationTypeLabel(s.stationType)}${s.sourceUrl ? ` · <a href="${s.sourceUrl}" target="_blank" rel="noopener">Quelle</a>` : ''}</div></div>
      <div class="r" style="color:${ageOk ? 'var(--muted)' : 'var(--orange)'}">${Time.fmtAge(s.updatedAt)}<br><span class="dim">Zuverl. ${round(s.reliabilityScore * 100)}%</span></div>
    </div></div>`;
  }).join('')}`;
  initLiveMap();
}
function stationTypeLabel(t) { return { takeoff: 'Startplatz', valley: 'Tal', mountain: 'Gipfel', airport: 'Flugplatz', landing: 'Landeplatz', unknown: '—' }[t] || t; }
function initLiveMap() {
  if (typeof L === 'undefined') { const m = $('#liveMap'); if (m) m.innerHTML = '<div class="loading small">Karte offline nicht verfügbar.</div>'; return; }
  const elm = $('#liveMap'); if (!elm) return; const site = siteById(Store.state.selectedSiteId);
  if (_liveMap) { _liveMap.remove(); _liveMap = null; }
  _liveMap = L.map(elm, { attributionControl: false });
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 16 }).addTo(_liveMap);
  const pts = [[site.lat, site.lon]];
  L.marker([site.lat, site.lon]).addTo(_liveMap).bindPopup('<b>' + esc(site.name) + '</b>');
  Data.stations.list.forEach(s => {
    const col = s.gustKmh > Store.state.pilot.maxGustKmh ? '#ff4d5e' : s.windSpeedKmh < 5 ? '#5d6878' : '#22e08a';
    const ic = L.divIcon({ className: '', html: `<div style="font-size:22px;transform:rotate(${s.windDirection + 180}deg);color:${col};text-shadow:0 0 3px #000">↑</div>`, iconSize: [22, 22] });
    L.marker([s.lat, s.lon], { icon: ic }).addTo(_liveMap).bindPopup(`<b>${esc(s.name)}</b><br>${s.windSpeedKmh} km/h, Böen ${s.gustKmh}<br>${Wind.toCompass(s.windDirection)}`);
    pts.push([s.lat, s.lon]);
  });
  _liveMap.fitBounds(pts, { padding: [30, 30] });
}

/* ---------- WIND INTELLIGENCE ---------- */
function renderWind() {
  const el = $('#screen-wind'); const agg = Data.forecast.agg; const site = siteById(Store.state.selectedSiteId);
  if (!agg) { el.innerHTML = loadingCard('Winddaten…'); return; }
  const i = agg.bestHourIdx; const h = agg.atHour(i);
  // gradient surface→1500
  const w0 = agg.windAtAlt(i, site.elevationMin + 10), wHi = agg.windAtAlt(i, site.elevationMin + 1000);
  const gradient = wHi && w0 ? round(wHi.spd - w0.spd) : 0;
  const gustFactor = h.windKmh > 1 ? round(h.gustKmh / h.windKmh, 1) : '—';
  const w700 = h.upper.find(u => u.p === 700) || { spd: 0, dir: 0 };
  const shear = round(Wind.angDiff(h.windDir, (h.upper.find(u => u.p === 850) || { dir: h.windDir }).dir));
  const foehn = site.foehnSensitive && w700.spd > 40 && Wind.matches(h.windDir, site.dangerousWindDirections, 40);
  el.innerHTML = `
  <div class="h" style="margin-top:6px">Wind-Intelligenz · ${h.hh} Uhr</div>
  <div class="grid c3">
    ${kpi('Start', round(h.windKmh), 'km/h', Wind.toCompass(h.windDir))}
    ${kpi('Böen', round(h.gustKmh), 'km/h', 'Faktor ' + gustFactor + '×')}
    ${kpi('Gradient', (gradient > 0 ? '+' : '') + gradient, 'km/h', 'Boden→+1000m')}
  </div>
  <div class="card"><div class="h" style="margin-top:0">Winddiagramm · Höhe × Stunde</div>
    ${windGrid(agg, site, Store.state.day)}
    <div class="small dim" style="margin-top:8px">Farbe = Stärke, Pfeil = Richtung (zeigt mit dem Wind). Interpoliert aus Boden-, 80/120/180 m- & Druckflächenwinden (Open-Meteo). Seitlich scrollen.</div>
  </div>
  <div class="card"><div class="h" style="margin-top:0">Windprofil (Bestzeit)</div>
    ${windProfileSVG(agg, i, site)}
  </div>
  <div class="card">
    ${riskRow(foehn ? '🟣' : '🟢', 'Föhn', foehn ? 'Föhnverdacht: starker Höhenwind aus Süd-/Gefahrenrichtung über föhnanfälligem Gebiet.' : 'Keine eindeutige Föhnsignatur in den Modelldaten.')}
    ${riskRow(shear > 60 ? '🔴' : shear > 35 ? '🟡' : '🟢', 'Windscherung', `${shear}° Richtungsdifferenz Boden↔850 hPa.`)}
    ${riskRow(Wind.matches(h.windDir, site.dangerousWindDirections, 35) ? '🔴' : '🟢', 'Lee-Gefahr', Wind.matches(h.windDir, site.dangerousWindDirections, 35) ? `Wind aus ${Wind.toCompass(h.windDir)} → Lee an ${site.name}.` : 'Anströmung aktuell nicht aus Lee-Richtung.')}
    ${riskRow(gustFactor !== '—' && gustFactor > 1.6 ? '🟡' : '🟢', 'Böenfaktor', `Böe/Mittelwind = ${gustFactor}×.`)}
    ${riskRow('🌬️', 'Talwind', esc(site.valleyWindNotes))}
  </div>`;
}
// own meteo-parapente-style altitude × hour wind grid (free, from Open-Meteo)
function windColor(s) { return s < 10 ? '#22e08a' : s < 18 ? '#7fe0a0' : s < 25 ? '#ffd23f' : s < 35 ? '#ff9d2e' : s < 45 ? '#ff4d5e' : '#b026ff'; }
function windGrid(agg, site, day) {
  const idx = agg.daylightIdx(day); if (!idx.length) return '<div class="small dim">Keine Tagesdaten.</div>';
  const top = 4000, bottom = Math.max(1000, Math.floor(site.elevationMin / 500) * 500);
  const alts = []; for (let a = top; a >= bottom; a -= 500) alts.push(a);
  const CW = 44;
  const header = `<div style="display:flex;position:sticky;top:0"><div style="min-width:40px"></div>${idx.map(i => `<div style="min-width:${CW}px;text-align:center;font-size:11px;color:var(--muted);font-weight:700">${agg.atHour(i).hh.slice(0, 2)}</div>`).join('')}</div>`;
  const rows = alts.map(a => {
    const cells = idx.map(i => {
      const w = agg.windAtAlt(i, a);
      if (!w) return `<div style="min-width:${CW}px"></div>`;
      const c = windColor(w.spd);
      return `<div style="min-width:${CW}px;text-align:center;padding:2px 0"><span style="display:inline-block;color:${c};transform:rotate(${(w.dir + 180) % 360}deg);font-size:15px;line-height:1;font-weight:800">↑</span><div style="font-size:10px;color:${c};font-weight:700;line-height:1.1">${round(w.spd)}</div></div>`;
    }).join('');
    return `<div style="display:flex;align-items:center;border-top:1px solid var(--line)"><div style="min-width:40px;font-size:10px;color:var(--muted);text-align:right;padding-right:6px">${a}</div>${cells}</div>`;
  }).join('');
  return `<div style="overflow-x:auto;-webkit-overflow-scrolling:touch;margin:0 -4px">${header}${rows}</div>`;
}

/* ---------- THERMAL INTELLIGENCE ---------- */
function renderThermal() {
  const el = $('#screen-thermal'); const agg = Data.forecast.agg; const site = siteById(Store.state.selectedSiteId);
  if (!agg) { el.innerHTML = loadingCard('Thermikdaten…'); return; }
  const idx = agg.daylightIdx(Store.state.day);
  const capeArr = idx.map(i => ({ i, hh: agg.atHour(i).hh, cape: agg.atHour(i).cape, base: agg.cloudBaseAsl(i), cloudL: agg.atHour(i).cloudL }));
  const active = capeArr.filter(x => x.cape > 150);
  const start = active.length ? active[0].hh : '—', end = active.length ? active[active.length - 1].hh : '—';
  const peak = capeArr.reduce((a, b) => b.cape > a.cape ? b : a, capeArr[0]);
  const to = site.takeoffs[0];
  const baseAbove = round(peak.base - to.elevation);
  const strength = clamp(peak.cape / 350 + 0.5, 0.5, 6);
  const blue = peak.cloudL < 12 && peak.cape > 200;
  el.innerHTML = `
  <div class="h" style="margin-top:6px">Thermik-Intelligenz</div>
  <div class="grid c2">
    ${kpi('Thermikbeginn', start, '', 'CAPE > 150')}
    ${kpi('Thermikende', end, '', '')}
    ${kpi('Peak-Steigen', '~' + round(strength, 1), 'm/s', 'aus CAPE geschätzt')}
    ${kpi('Basis (Peak)', round(peak.base), 'm', `~${baseAbove} m über Start`)}
  </div>
  <div class="card">
    ${riskRow(blue ? '🟦' : '☁️', 'Thermik-Typ', blue ? 'Tendenz Blauthermik (wenig tiefe Bewölkung) — schwerer zu finden.' : 'Cumulus-Thermik wahrscheinlich — Wolken als Marker nutzen.')}
    ${riskRow(baseAbove < 300 ? '🟠' : '🟢', 'Basis über Start', `${baseAbove} m Spielraum über dem Startplatz.`)}
    ${riskRow(peak.cape > 1500 ? '🔴' : peak.cape > 800 ? '🟡' : '🟢', 'Überentwicklung', `Peak-CAPE ${round(peak.cape)} J/kg${peak.cape > 1500 ? ' — Cb/Gewitter möglich.' : '.'}`)}
    ${riskRow('🛫', 'XC-Potenzial', `XC-Score ${Data.decision()?.xcScore ?? '—'}/100 — ${esc(site.expertNotes)}`)}
  </div>
  <div class="card"><div class="h" style="margin-top:0">Emagram / Sounding (${agg.atHour(peak.i).hh} Uhr)</div>
    ${emagramSVG(agg, peak.i, site)}
  </div>
  <div class="card"><div class="h" style="margin-top:0">CAPE-Tagesverlauf</div>
    ${capeTimelineSVG(agg, Store.state.day)}
    <div class="small dim" style="margin-top:8px">Steigwerte/Basis sind Schätzungen aus CAPE & Taupunktspread (LCL). Inversion/CIN aus Open-Meteo nicht direkt verfügbar.</div>
  </div>`;
}

/* ---------- CLOUD / STORM ---------- */
function renderCloud() {
  const el = $('#screen-cloud'); const agg = Data.forecast.agg;
  if (!agg) { el.innerHTML = loadingCard('Wolkendaten…'); return; }
  const i = agg.bestHourIdx; const h = agg.atHour(i);
  const idx = agg.daylightIdx(Store.state.day);
  const stormHour = idx.map(x => agg.atHour(x)).find(x => x.cape > 1500 || x.precip > 1);
  const thunder = h.cape > 1500 && h.precip > 0.2;
  el.innerHTML = `
  <div class="h" style="margin-top:6px">Wolken & Gewitter · ${h.hh} Uhr</div>
  <div class="grid c3">
    ${kpi('Tief', round(h.cloudL), '%', 'low')}
    ${kpi('Mittel', round(h.cloudM), '%', 'mid')}
    ${kpi('Hoch', round(h.cloudH), '%', 'high')}
  </div>
  <div class="grid c2">
    ${kpi('Niederschlag', round(h.precip, 1), 'mm', (h.precipP || 0) + '% Wahrsch.')}
    ${kpi('Wolkenbasis', round(agg.cloudBaseAsl(i)), 'm', 'ASL (LCL)')}
  </div>
  <div class="card">
    ${riskRow(thunder ? '🟣' : stormHour ? '🟠' : '🟢', 'Gewitterrisiko', thunder ? 'Akutes Gewitter-/Cb-Risiko jetzt.' : stormHour ? `Spätere Überentwicklung möglich ab ~${stormHour.hh}.` : 'Kein signifikantes Gewittersignal.')}
    ${riskRow(h.precip > 0.3 ? '🔴' : (h.precipP > 50 ? '🟡' : '🟢'), 'Niederschlag', `${round(h.precip, 1)} mm, ${h.precipP || 0}% Wahrscheinlichkeit.`)}
    ${riskRow(h.cloud > 85 ? '🟠' : '🟢', 'Abschattung', `Gesamtbedeckung ${round(h.cloud)}% ${h.cloud > 85 ? '— Thermik gedämpft.' : ''}`)}
    ${riskRow('❄️', 'Nullgradgrenze', `~${round(h.frz)} m ASL.`)}
  </div>
  <div class="card"><div class="h" style="margin-top:0">Niederschlagsradar (RainViewer)</div>
    <div id="cloudMap" class="map"></div>
    <div class="small dim" style="margin-top:8px">Live-Radar-Kacheln © RainViewer. Blitzdaten (Blitzortung) und METAR/TAF siehe Profil → Info.</div>
  </div>`;
  initCloudMap();
}
async function initCloudMap() {
  if (typeof L === 'undefined') { const m = $('#cloudMap'); if (m) m.innerHTML = '<div class="loading small">Karte offline nicht verfügbar.</div>'; return; }
  const elm = $('#cloudMap'); if (!elm) return; const site = siteById(Store.state.selectedSiteId);
  if (_cloudMap) { _cloudMap.remove(); _cloudMap = null; }
  _cloudMap = L.map(elm, { attributionControl: false }).setView([site.lat, site.lon], 8);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 12 }).addTo(_cloudMap);
  L.marker([site.lat, site.lon]).addTo(_cloudMap);
  try {
    const r = await fetch('https://api.rainviewer.com/public/weather-maps.json'); const j = await r.json();
    const past = j.radar && j.radar.past; if (past && past.length) {
      const fr = past[past.length - 1];
      L.tileLayer(`${j.host}${fr.path}/256/{z}/{x}/{y}/4/1_1.png`, { opacity: 0.6, maxZoom: 12 }).addTo(_cloudMap);
    }
  } catch (e) { /* radar optional */ }
}

/* ---------- MODEL COMPARISON ---------- */
function renderModels() {
  const el = $('#screen-models'); const c = Data.models.consensus;
  if (Data.models.error && !c) { el.innerHTML = errorCard('Modellvergleich: ' + Data.models.error); return; }
  if (!c) { el.innerHTML = loadingCard('Modelle werden verglichen…'); return; }
  const NAMES = { icon_seamless: 'ICON (DWD)', ecmwf_ifs025: 'ECMWF', gfs_seamless: 'GFS', meteofrance_seamless: 'AROME/ARPEGE', gem_seamless: 'GEM' };
  el.innerHTML = `
  <div class="h" style="margin-top:6px">Modellvergleich · ${Time.hhmm(c.refTime)} Uhr</div>
  <div class="grid c2">
    ${kpi('Konsens', c.agreement, '/100', c.agreement > 75 ? 'hohe Einigkeit' : c.agreement > 50 ? 'mäßig' : 'uneinig')}
    ${kpi('Wind-Spanne', round(c.wMin) + '–' + round(c.wMax), 'km/h', 'Ø ' + round(c.wAvg))}
  </div>
  <div class="card">
    <table><thead><tr><th>Modell</th><th>Wind</th><th>Böen</th><th>Richtung</th><th>Bew.</th></tr></thead><tbody>
    ${c.rows.map(r => `<tr><td>${NAMES[r.model] || r.model}</td><td><b>${round(r.wind)}</b></td><td>${round(r.gust)}</td><td>${Wind.toCompass(r.dir)}</td><td>${round(r.cloud)}%</td></tr>`).join('')}
    </tbody></table>
  </div>
  <div class="card"><div class="h" style="margin-top:0">${c.conflicts.length ? 'Widersprüche' : 'Konsens'}</div>
    ${c.conflicts.length ? c.conflicts.map(x => `<div class="risk"><div class="ic">🟠</div><div class="tx">${esc(x)}</div></div>`).join('')
      : '<div class="risk"><div class="ic">🟢</div><div class="tx">Modelle weitgehend einig — höhere Prognosesicherheit.</div></div>'}
    <div class="small dim" style="margin-top:8px">Quellen via Open-Meteo: ICON-D2/EU, ECMWF IFS, GFS, Météo-France (AROME/ARPEGE), GEM. Bei starkem Widerspruch entscheidet die App konservativer.</div>
  </div>`;
}

/* ---------- PILOT PROFILE ---------- */
function renderProfile() {
  const el = $('#screen-profile'); const p = Store.state.pilot;
  const presets = { beginner: { maxWindKmh: 18, maxGustKmh: 22, maxThermalStrength: 2, level: 'beginner', wingClass: 'EN-A' }, intermediate: { maxWindKmh: 28, maxGustKmh: 35, maxThermalStrength: 4, level: 'intermediate', wingClass: 'EN-B-low' }, expert: { maxWindKmh: 38, maxGustKmh: 50, maxThermalStrength: 6, level: 'expert', wingClass: 'EN-C' } };
  el.innerHTML = `
  <div class="h" style="margin-top:6px">Pilotenprofil</div>
  <div class="seg">${Object.keys(presets).map(k => `<button onclick='applyPreset(${JSON.stringify(presets[k])})'>${k}</button>`).join('')}</div>
  <div class="card">
    <label class="fld"><span>Name</span><input class="input" id="p-name" value="${esc(p.name)}"></label>
    <div class="row">
      <label class="fld"><span>Level</span><select class="input" id="p-level">${['student', 'beginner', 'intermediate', 'advanced', 'expert', 'competition'].map(x => `<option ${p.level === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
      <label class="fld"><span>Schirmklasse</span><select class="input" id="p-wing">${['EN-A', 'EN-B-low', 'EN-B-high', 'EN-C', 'EN-D', 'CCC'].map(x => `<option ${p.wingClass === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
    </div>
    <div class="row">
      <label class="fld"><span>Max. Wind (km/h)</span><input class="input" id="p-maxwind" type="number" value="${p.maxWindKmh}"></label>
      <label class="fld"><span>Max. Böe (km/h)</span><input class="input" id="p-maxgust" type="number" value="${p.maxGustKmh}"></label>
    </div>
    <div class="row">
      <label class="fld"><span>Flugstunden</span><input class="input" id="p-hours" type="number" value="${p.hoursTotal}"></label>
      <label class="fld"><span>Risikotoleranz</span><select class="input" id="p-risk">${['low', 'medium', 'high'].map(x => `<option ${p.riskTolerance === x ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
    </div>
    <div class="chk"><input type="checkbox" id="p-alpine" ${p.alpineExperience ? 'checked' : ''}><label for="p-alpine">Alpinerfahrung</label></div>
    <div class="chk"><input type="checkbox" id="p-siv" ${p.sivExperience ? 'checked' : ''}><label for="p-siv">SIV-Erfahrung</label></div>
    <button class="btn" onclick="savePilot()">Profil speichern</button>
  </div>
  <div class="h">Warnungen</div>
  <div class="card">
    <div class="chk"><input type="checkbox" id="p-alerts" ${Store.state.alerts ? 'checked' : ''} onchange="toggleAlerts(this.checked)"><label for="p-alerts">Föhn-/No-Go-Browser-Warnungen aktivieren</label></div>
    <div class="small dim" style="margin-top:6px">Warnt bei rot/schwarz für das gewählte Gebiet, während die App geöffnet ist. Echte Hintergrund-Push brauchen einen Server.</div>
  </div>
  <div class="h">Datenquellen & API-Keys</div>
  <div class="card small muted" style="line-height:1.6">
    <b>Direkt integriert (kostenlos, kein Key):</b> Open-Meteo (Forecast, Höhenwind, CAPE, Wolken, Niederschlag), Open-Meteo Modelle (ICON, ECMWF, GFS, AROME, GEM), Open-Meteo Geocoding, RainViewer Radar, OpenStreetMap Karten.<br><br>
    <b>API-Key erforderlich:</b> Windy, Meteoblue, MeteoSwiss (kommerziell), GeoSphere/ZAMG (teils offen), Holfuy (Station-Key), WeatherFlow/Tempest.<br><br>
    <b>Partner-/Pro-Zugang:</b> TopMeteo, MeteoParapente, XC Skies, Burnair, Windfinder (kommerziell).<br><br>
    <b>Nur manuell/legal:</b> Pioupiou (offene API, fair use), DHV-Wetter, Bergfex, Vereinsstationen, Webcams — nur mit Erlaubnis, keine Scraper.<br><br>
    <b>Luftraum/NOTAM/METAR:</b> AviationWeather.gov (METAR/TAF offen), openAIP (Key). Blitzdaten: Blitzortung (Mitglied/fair use).<br><br>
    <b>Neue Station hinzufügen:</b> in <code>Providers.liveStations</code> bzw. einen echten Provider nach dem <code>WeatherProvider</code>-Muster ergänzen (id, fetchLiveStations) und im Aggregator registrieren.
  </div>`;
}

/* ---------- EXAM TRAINER ---------- */
let examState = { mode: 'daily', cat: 'alle', current: null, answered: false };
function renderExam() {
  const el = $('#screen-exam'); const st = Store.state.examStats;
  const cats = ['alle', ...new Set(EXAM_QUESTIONS.map(q => q.category))];
  if (!examState.current) examState.current = pickQuestion();
  const q = examState.current;
  const d = Data.decision(); _ctxExam = d ? contextualExam(d.topRisks) : null;
  const ctxCard = _ctxExam ? `<div class="card s-orange">
    <div class="small muted" style="font-weight:700">🎯 Passend zu heute · ${esc(_ctxExam.triggerRisk)}</div>
    <div style="font-size:15px;font-weight:700;margin:8px 0 12px">${esc(_ctxExam.question)}</div>
    <div id="ctxOpts">${_ctxExam.options.map((o, k) => `<button class="opt" data-k="${k}" onclick="answerContext(${k})">${esc(o)}</button>`).join('')}</div>
    <div id="ctxExplain"></div></div>` : '';
  el.innerHTML = `
  <div class="h" style="margin-top:6px">ExamTrainer</div>
  <div class="grid c3">
    ${kpi('Beantwortet', st.answered, '', '')}
    ${kpi('Richtig', st.correct, '', st.answered ? round(st.correct / st.answered * 100) + '%' : '')}
    ${kpi('Quote', st.answered ? round(st.correct / st.answered * 100) : 0, '%', '')}
  </div>
  ${ctxCard}
  <div class="reminder" style="margin-bottom:12px"><div class="i">📅</div><div><b>Tägliche Mini-Frage</b><br>Wiederhole regelmäßig Prüfungswissen — just for fun und für die Sicherheit.</div></div>
  <div class="seg" id="catSeg">${cats.map(c => `<button data-c="${c}" class="${examState.cat === c ? 'on' : ''}">${c}</button>`).join('')}</div>
  <div class="card">
    <div class="small muted">${esc(q.category)} · ${q.difficulty}</div>
    <div style="font-size:16px;font-weight:700;margin:10px 0 14px">${esc(q.question)}</div>
    <div id="opts">${q.options.map((o, k) => `<button class="opt" data-k="${k}" onclick="answerExam(${k})">${esc(o)}</button>`).join('')}</div>
    <div id="examExplain"></div>
  </div>`;
  $$('#catSeg button').forEach(b => b.addEventListener('click', () => { examState.cat = b.dataset.c; examState.current = pickQuestion(); examState.answered = false; renderExam(); }));
}
function pickQuestion() {
  let pool = EXAM_QUESTIONS;
  if (examState.cat !== 'alle') pool = pool.filter(q => q.category === examState.cat);
  if (!pool.length) pool = EXAM_QUESTIONS;
  return pool[Math.floor(Math.random() * pool.length)];
}
function answerExam(k) {
  if (examState.answered) return; examState.answered = true;
  const q = examState.current; const correct = q.correctAnswerIndex;
  $$('#opts .opt').forEach(b => { const kk = +b.dataset.k; if (kk === correct) b.classList.add('correct'); else if (kk === k) b.classList.add('wrong'); b.disabled = true; });
  const st = Store.state.examStats; st.answered++; if (k === correct) st.correct++; Store.set({ examStats: st });
  $('#examExplain').innerHTML = `<div class="explain"><b>${k === correct ? '✅ Richtig!' : '❌ Nicht ganz.'}</b><br>${esc(q.explanation)}${q.reminder ? `<br><br>🧠 <i>${esc(q.reminder)}</i>` : ''}
    <button class="btn" style="margin-top:12px" onclick="nextExam()">Nächste Frage →</button></div>`;
}
function nextExam() { examState.current = pickQuestion(); examState.answered = false; renderExam(); }
let _ctxExam = null;
function answerContext(k) {
  if (!_ctxExam) return; const correct = _ctxExam.correctAnswerIndex;
  $$('#ctxOpts .opt').forEach(b => { const kk = +b.dataset.k; if (kk === correct) b.classList.add('correct'); else if (kk === k) b.classList.add('wrong'); b.disabled = true; });
  const ex = $('#ctxExplain'); if (ex) ex.innerHTML = `<div class="explain"><b>${k === correct ? '✅ Richtig!' : '❌ Nicht ganz.'}</b><br>${esc(_ctxExam.explanation)}</div>`;
}

/* ---------- PRO / PRICING ---------- */
function renderPro() {
  const el = $('#screen-pro'); const c = SKYWORTHY_CONFIG;
  const features = [
    ['🛩️', 'Konservative Go/No-Go-Engine', 'Ampel grün–schwarz, pilotenindividuell, mit Tages-Gefahren-Sperre.'],
    ['📡', 'Echte Live-Stationen', 'Pioupiou-Live-Wind im Umkreis, Prognose-vs-Realität, ausschlaggebende Station.'],
    ['🌬️', 'Wind- & Höhenprofil', 'Interpolierter Höhenwind, Gradient, Scherung, Föhn-Check, Windprofil-Chart.'],
    ['🔥', 'Thermik + Emagram', 'Emagram/Sounding, CAPE-Verlauf, Basis, Blauthermik, XC-Potenzial.'],
    ['🧮', 'Modellvergleich', 'ICON · ECMWF · GFS · AROME · GEM — Konsens & Widersprüche.'],
    ['🗺️', 'Fluggebiete & Karten', 'Suche, Radius, Karte, Startplatz-Logik, Brauneck-Premium-Daten.'],
    ['🔔', 'Föhn-/Go-Alerts', 'Browser-Warnung bei kritischen Bedingungen.'],
    ['🎓', 'ExamTrainer', 'Prüfungsfragen & Safety-Reminder.']
  ];
  const link = c.stripePaymentLink;
  el.innerHTML = `
  <div class="h" style="margin-top:6px">SKYWORTHY Pro</div>
  <div class="hero card statusborder s-green">
    <div class="ring" style="background:var(--c)"></div>
    <div class="glabel" style="color:var(--c)">${c.price}<small style="font-size:18px;color:var(--muted)"> / ${c.interval}</small></div>
    <div class="gsum">Alle Elite-Features. Jederzeit kündbar. Günstiger als 15 Wetterseiten zu vergleichen — und sicherer.</div>
  </div>
  <div class="card">
    ${features.map(f => `<div class="risk"><div class="ic">${f[0]}</div><div class="tx"><b>${esc(f[1])}</b>${esc(f[2])}</div></div>`).join('')}
  </div>
  ${link
    ? `<a class="btn" href="${esc(link)}" target="_blank" rel="noopener" style="display:block;text-align:center;text-decoration:none">Jetzt für ${c.price}/${c.interval} freischalten →</a>`
    : `<div class="card s-yellow"><b style="color:var(--c)">Checkout noch nicht aktiv</b><div class="small muted" style="margin-top:6px">Erstelle in Stripe ein ${c.price}/${c.interval}-Abo, generiere einen <b>Payment Link</b> und trage ihn in <code>SKYWORTHY_CONFIG.stripePaymentLink</code> ein. Für erzwungene Lizenzprüfung die App über Vercel/Netlify mit Stripe-Webhook ausliefern.</div></div>`}
  <div class="disclaimer" style="border:0">Preis & Leistungsumfang sind ein Vorschlag — anpassbar in <code>SKYWORTHY_CONFIG</code>.</div>`;
}

/* ---------- PRESSURE INTELLIGENCE (Hoch/Tief) ---------- */
function renderPressure() {
  const el = $('#screen-pressure'); const agg = Data.forecast.agg; const site = siteById(Store.state.selectedSiteId);
  if (!agg) { el.innerHTML = loadingCard('Druckdaten…'); return; }
  const p = analyzePressure(agg, Data.pressure.field, site);
  const sc = 's-' + p.statusColor;
  const gaugePct = clamp((p.pmsl - 980) / (1045 - 980) * 100, 0, 100);
  el.innerHTML = `
  <div class="h" style="margin-top:6px">Druck-Intelligenz — ${esc(site.name)}</div>

  <div class="hero card statusborder ${sc}">
    <div class="ring" style="background:var(--c)"></div>
    <div class="scorebadge"><div class="n" style="color:var(--c)">${p.pmsl}</div><div class="t">hPa MSL</div></div>
    <div style="display:flex;align-items:center;gap:8px"><span class="dot"></span>
      <span class="muted small" style="font-weight:700;letter-spacing:1px">${p.pClass === 'hoch' ? '🅗 HOCH' : p.pClass === 'tief' ? '🅣 TIEF' : '⚖ ÜBERGANG'}</span></div>
    <div class="glabel" style="color:var(--c);margin-top:6px;font-size:30px">${esc(p.label)}</div>
    <div class="gsum">${p.tendIcon} ${esc(p.tendText)}</div>
    <div class="bar" style="margin-top:14px"><i style="width:${gaugePct}%"></i></div>
    <div class="small dim" style="display:flex;justify-content:space-between;margin-top:3px"><span>980 (Sturmtief)</span><span>1013</span><span>1045 (kräftiges Hoch)</span></div>
  </div>

  <div class="grid c2">
    ${kpi('Druckgradient', p.grad != null ? p.grad : '—', 'hPa/100km', p.gradCat ? p.gradCat.k + ' (≈ Windstärke)' : 'lokal nicht verfügbar')}
    ${kpi('Gradientwind', p.geoWindFrom != null ? Wind.toCompass(p.geoWindFrom) : '—', '', p.geoWindFrom != null ? 'aus ' + p.geoWindFrom + '° (geostroph.)' : '')}
    ${kpi('Tendenz 3 h', (p.trend3 > 0 ? '+' : '') + p.trend3, 'hPa', 'Barometer-Trend')}
    ${kpi('500 hPa', p.gh500 || '—', 'm', p.ridgeTrough ? ({ ridge: 'Höhenrücken', trough: 'Höhentrog', flat: 'flach' }[p.ridgeTrough.k]) : '')}
  </div>

  ${p.gradCat ? `<div class="card ${'s-' + p.gradCat.c}"><div class="risk"><div class="ic">🧭</div><div class="tx"><b>Isobaren & Wind (real berechnet)</b>
    Druck fällt Richtung ${Wind.toCompass(p.toLow)} (${p.toLow}°). Auf der ${p.hemisphere} weht der Gradientwind nahezu parallel zu den Isobaren — Tief zur Linken (Buys-Ballot). Geschätzter Höhenwind aus <b>${Wind.toCompass(p.geoWindFrom)}</b>. Enge Isobaren = ${p.gradCat.k}er Wind.</div></div></div>` : banner('yellow', 'ℹ️ Lokaler Druckgradient gerade nicht verfügbar — wird beim nächsten Refresh berechnet.')}

  ${p.ridgeTrough ? `<div class="card"><div class="risk"><div class="ic">${p.ridgeTrough.k === 'ridge' ? '⛰️' : p.ridgeTrough.k === 'trough' ? '🕳️' : '〰️'}</div><div class="tx"><b>Höhenwetterlage (500 hPa)</b>${esc(p.ridgeTrough.t)}</div></div></div>` : ''}

  <div class="card ${sc}"><div class="h" style="margin-top:0">Auswirkung aufs Fliegen — jetzt</div>
    ${p.effects.map(e => `<div class="risk"><div class="ic">${e[0]}</div><div class="tx"><b>${esc(e[1])}</b>${esc(e[2])}</div></div>`).join('')}
    ${p.risks.map(r => `<div class="risk"><div class="ic">${r.slice(0, 2)}</div><div class="tx">${esc(r.slice(2).trim())}</div></div>`).join('')}
  </div>

  <div class="h">Druck verstehen — das komplette Wissen</div>
  <div class="small dim" style="margin:-4px 4px 8px">Wer Hoch & Tief wirklich versteht, fliegt sicherer. Tippe zum Aufklappen.</div>
  ${pressureLessons()}

  <div class="dim small" style="text-align:center;margin-top:10px">Druckfeld: ${Data.pressure.fetchedAt ? Time.fmtAge(Data.pressure.fetchedAt) : '—'} · Quelle Open-Meteo (pressure_msl, 5-Punkt-Gitter).</div>`;
}

function lesson(icon, title, html) {
  return `<details class="card" style="padding:0"><summary style="list-style:none;cursor:pointer;padding:14px 16px;font-weight:700;display:flex;align-items:center;gap:10px"><span style="font-size:18px">${icon}</span>${esc(title)}<span style="margin-left:auto;color:var(--dim)">▾</span></summary><div style="padding:0 16px 16px;font-size:13.5px;line-height:1.6;color:var(--muted)">${html}</div></details>`;
}
function pressureLessons() {
  return [
    lesson('📏', 'Luftdruck — die Grundlage', `Der Luftdruck ist das Gewicht der Luftsäule über dir, gemessen in <b>Hektopascal (hPa)</b>. Standard auf Meereshöhe: <b>1013,25 hPa</b>. Damit Orte vergleichbar sind, wird auf Meeresniveau reduziert (<b>MSL / QFF</b>). Mit der Höhe fällt der Druck (~1 hPa pro 8 m unten). Aus Druckunterschieden entsteht <b>Wind</b> — das ist der Schlüssel.`),
    lesson('🅗', 'Hochdruck (Antizyklone)', `Absinkende Luft, am Boden auseinanderströmend. Rotation: <b>Nordhalbkugel im Uhrzeigersinn</b>, Südhalbkugel gegen den Uhrzeigersinn. Folgen: <ul><li><b>Subsidenz-Inversion</b> → deckelt die Thermik, niedrige Basis, Dunst/Hochnebel.</li><li>Meist <b>schwacher Wind</b> → Thermik- & Talwinde dominieren.</li><li>Beständig, oft mehrere Tage (planbar).</li><li>Sommer: Blauthermik & Hitze; Winter: Hochnebel, Frost, Inversion.</li></ul>Fürs Fliegen: ruhig & anfängerfreundlich, aber Thermik oft schwach und gedeckelt.`),
    lesson('🅣', 'Tiefdruck (Zyklone)', `Aufsteigende Luft, am Boden zusammenströmend (Konvergenz). Rotation: <b>Nordhalbkugel gegen den Uhrzeigersinn</b>, Süden im Uhrzeigersinn. Folgen: <ul><li><b>Hebung</b> → Wolken, Schauer, Gewitter, Überentwicklung.</li><li>Enge Isobaren → <b>kräftiger, böiger Wind</b>.</li><li>Bringt <b>Fronten</b> (siehe unten).</li></ul>Fürs Fliegen: meist anspruchsvoll bis No-Go. Sturmtief = nicht fliegen.`),
    lesson('🧭', 'Vom Druck zum Wind — Gradient & Coriolis', `Die <b>Druckgradientkraft</b> zeigt vom Hoch zum Tief und treibt den Wind an: <b>enge Isobaren = starker Wind</b>. Die <b>Corioliskraft</b> (Erdrotation) lenkt ab — rechts auf der Nord-, links auf der Südhalbkugel. Im Gleichgewicht weht der <b>geostrophische Wind parallel zu den Isobaren</b>. <b>Buys-Ballot-Regel:</b> Stehst du (Nordhalbkugel) mit dem Rücken zum Wind, liegt das Tief links. Am Boden bremst die Reibung → der Wind dreht etwas zum Tief hin. SKYWORTHY berechnet den Gradienten oben aus echten Nachbar-Druckwerten.`),
    lesson('🌬️', 'Fronten — Warm, Kalt, Okklusion', `Grenzflächen zwischen Luftmassen, an ein Tief gebunden: <ul><li><b>Warmfront:</b> warme Luft gleitet auf → hohe Cirren, dann tiefere Schichtwolken, lang anhaltender Regen, Wind dreht. Vorlaufend.</li><li><b>Kaltfront:</b> Kaltluft schiebt sich unter Warmluft → <b>Böenfront</b>, Schauer/Gewitter, markanter Winddreher (Nord-H.: rechtsdrehend), danach Aufklaren mit Quellwolken. Gefährlich!</li><li><b>Okklusion:</b> Kaltfront holt Warmfront ein — Mischung aus beidem.</li></ul>Fürs Fliegen: <b>Frontdurchgang konsequent meiden</b> — plötzliche Böen & Winddreher.`),
    lesson('⛰️', 'Föhn — der Druck-Trick der Berge', `Föhn entsteht durch einen <b>Druckunterschied quer über das Gebirge</b> (z. B. Süd > Nord über den Alpen). Luft wird übers Gebirge gepresst, fällt auf der Lee-Seite ab, erwärmt sich, wird trocken, böig und stark. Zeichen: Föhnfische (Lentikularis), extreme Fernsicht, warmer böiger Wind, Föhnmauer am Kamm. <b>Föhnverdacht = nicht fliegen.</b> Lokale Windstille am Start täuscht — der Föhn kann schlagartig durchbrechen.`),
    lesson('🧱', 'Inversion & Stabilität', `Normalerweise nimmt die Temperatur mit der Höhe ab. Bei einer <b>Inversion</b> ist eine Schicht oben wärmer als unten — sie wirkt wie ein Deckel und <b>stoppt die Thermik</b> abrupt (Basis begrenzt). Typisch unter Hochdruck (Absink-Inversion) oder morgens (Bodeninversion). Im Emagram (Thermik-Tab) als Knick im Temperaturprofil sichtbar. <b>Stabil</b> (Hoch) = ruhig, schwache Thermik; <b>labil</b> (Tief/Trog) = kräftige Thermik, aber Überentwicklung.`),
    lesson('🗺️', 'Großwetterlagen weltweit', `<ul><li><b>Omega-/Blocking-Hoch:</b> stabiles Hoch, von Tiefs flankiert (Form wie Ω) → tagelang beständig.</li><li><b>Höhentrog/-rücken (500 hPa):</b> Trog = labil & wechselhaft, Rücken = stabil & sonnig.</li><li><b>Genuatief / Vb-Lage:</b> Tief über Oberitalien, zieht nordostwärts → Stau & Dauerregen am Alpenostrand.</li><li><b>Land-/Seewind & Talwind:</b> lokale, thermisch getriebene Druckunterschiede — überlagern den synoptischen Wind bei schwachem Gradient.</li><li><b>Passat/ITCZ (Tropen), Roaring Forties (Südhalbkugel):</b> die globale Druckverteilung steuert die großen Windgürtel.</li></ul>`),
    lesson('🌍', 'Nord- vs. Südhalbkugel', `Die Corioliskraft kehrt sich um: Auf der <b>Südhalbkugel</b> rotieren Tiefs im Uhrzeigersinn, Hochs gegen den Uhrzeigersinn — und die Buys-Ballot-Regel ist gespiegelt (Tief zur Rechten, wenn der Wind im Rücken steht). SKYWORTHY berücksichtigt die Hemisphäre des gewählten Gebiets automatisch bei der Gradientwind-Richtung.`),
    lesson('🎒', 'Vorflug-Routine des Piloten', `<ol><li><b>Barometer-Trend:</b> steigend = Beruhigung, fallend = Verschlechterung/Front.</li><li><b>Isobarenabstand:</b> eng = viel Wind → kritisch.</li><li><b>Front in Sicht?</b> Timing & Durchgang meiden.</li><li><b>Föhn?</b> Druckdifferenz übers Gebirge + Höhenwind prüfen.</li><li><b>Inversion/Basis:</b> reicht der Spielraum über dem Start?</li></ol>Merke: <b>Livewind schlägt Prognose</b> — und im Zweifel nicht fliegen.`)
  ].join('');
}

/* ---------- FLUGWEG 3D (Google-Earth-Stil) ---------- */
// great-circle move helper: from {lat,lon} by dist(m) on bearing(deg)
function geoMove(lat, lon, dist, brg) {
  const R = 6371000, d = dist / R, b = Geo.toRad(brg), la = Geo.toRad(lat), lo = Geo.toRad(lon);
  const la2 = Math.asin(Math.sin(la) * Math.cos(d) + Math.cos(la) * Math.sin(d) * Math.cos(b));
  const lo2 = lo + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(la), Math.cos(d) - Math.sin(la) * Math.sin(la2));
  return { lat: Geo.toDeg(la2), lon: Geo.toDeg(lo2) };
}
// Build a schematic IDEAL flight path as [lon,lat,alt] points from the current decision.
function buildIdealPath(site, d) {
  const to = site.takeoffs[0], lz = site.landings[0] || { lat: site.lat, lon: site.lon, elevation: site.elevationMin };
  const startAlt = to.elevation;
  const base = clamp(d ? d.best.cloudBase : startAlt + 500, startAlt + 200, startAlt + 2500);
  const ft = d ? d.recommendedFlightType : 'Soaring';
  const idealCenter = SECTOR[site.idealWindDirections[0]] != null ? SECTOR[site.idealWindDirections[0]] : 0;
  const ridge = (idealCenter + 90) % 360;      // ridge runs ~perpendicular to ideal wind
  const windFrom = d ? d.best.h.windDir : idealCenter;
  const cape = d ? d.best.h.cape : 0;
  const strength = clamp(cape / 350 + 0.6, 0.4, 6);   // estimated climb m/s
  const pts = []; const thermals = []; const add = (p, alt) => pts.push({ lat: p.lat, lon: p.lon, alt });
  add(to, startAlt);

  if (/Nicht|Groundhandling/.test(ft)) {
    const steps = 6; for (let i = 1; i <= steps; i++) { const f = i / steps; add({ lat: to.lat + (lz.lat - to.lat) * f, lon: to.lon + (lz.lon - to.lon) * f }, startAlt + (lz.elevation - startAlt) * f); }
    return { coords: pts.map(p => [p.lon, p.lat, p.alt]), base, ft, strength, thermals, schematic: true };
  }

  let cur = { lat: to.lat, lon: to.lon }, alt = startAlt + 25;
  if (/Soaring|Thermik|XC|Abgleiter/.test(ft)) {
    const legs = /Abgleiter/.test(ft) ? 2 : 6, gain = (base - startAlt) / (legs + 4);
    for (let i = 0; i < legs; i++) {
      const dir = i % 2 === 0 ? ridge : (ridge + 180) % 360;
      cur = geoMove(cur.lat, cur.lon, 420, dir);
      alt = Math.min(base - 120, alt + gain); add(cur, alt);
    }
  }
  if (/Thermik|XC/.test(ft) && cape > 120) {
    const r = 110, c = geoMove(cur.lat, cur.lon, r, windFrom);
    thermals.push({ lon: c.lon, lat: c.lat, base, ground: alt, strength });
    for (let a = 0; a <= 900; a += 60) { const p = geoMove(c.lat, c.lon, r, a); alt = Math.min(base, alt + (base - alt) * 0.08 + 12); add(p, alt); }
    cur = pts[pts.length - 1]; cur = { lat: cur.lat, lon: cur.lon };
  } else if (cape > 200) {
    thermals.push({ lon: cur.lon, lat: cur.lat, base, ground: alt, strength });
  }
  if (/XC/.test(ft)) {
    const downwind = (windFrom + 180) % 360, p = geoMove(cur.lat, cur.lon, 3500, downwind);
    // a second thermal mid-glide for the XC leg
    if (cape > 150) { const mid = geoMove(cur.lat, cur.lon, 1800, downwind); thermals.push({ lon: mid.lon, lat: mid.lat, base, ground: base - 350, strength: strength * 0.85 }); }
    alt = base - 250; add(p, alt); cur = p;
  }
  const steps = 8; const a0 = alt;
  for (let i = 1; i <= steps; i++) { const f = i / steps; add({ lat: cur.lat + (lz.lat - cur.lat) * f, lon: cur.lon + (lz.lon - cur.lon) * f }, a0 + (lz.elevation - a0) * f); }
  return { coords: pts.map(p => [p.lon, p.lat, p.alt]), base, ft, strength, thermals, schematic: true };
}

function renderRoute() {
  const el = $('#screen-route'); const site = siteById(Store.state.selectedSiteId); const d = Data.decision();
  if (!d) { el.innerHTML = loadingCard('Flugweg wird berechnet…'); return; }
  const path = buildIdealPath(site, d); const sc = statusClass(d.status);
  window.__briefingText = buildBriefing(d, site).text;
  el.innerHTML = `
  <div class="h" style="margin-top:6px">Idealer Flugweg (3D) — ${esc(site.name)}</div>
  ${banner(d.status === 'red' || d.status === 'black' ? 'red' : 'cyan', d.status === 'red' || d.status === 'black'
    ? '⛔ Heute NO-GO — der gezeigte Weg ist nur eine schematische Referenz, nicht fliegen.'
    : `▶ Tippe „VORFLUG STARTEN" für den Ego-Vorflug aus der Pilotenperspektive — mit gesprochenem Briefing.`)}
  <div id="routeMap" style="height:62vh;min-height:360px;border-radius:16px;overflow:hidden;border:1px solid var(--line);background:var(--card)"></div>
  <div class="card" style="margin-top:12px">
    <div class="row">
      ${kpi('Flugart', '', '', '')}
    </div>
    <div class="risk"><div class="ic">🟢</div><div class="tx"><b>Start</b>${esc(site.takeoffs[0].name)} · ${site.takeoffs[0].elevation} m</div></div>
    <div class="risk"><div class="ic">☁️</div><div class="tx"><b>Arbeitshöhe / Basis</b>~${round(path.base)} m — Kreisen bis knapp unter die Wolkenbasis.</div></div>
    <div class="risk"><div class="ic">🔴</div><div class="tx"><b>Landeplatz</b>${esc((site.landings[0] || {}).name || '—')}${site.landings[0] ? ' · ' + site.landings[0].elevation + ' m' : ''}</div></div>
    <div class="risk"><div class="ic">🛫</div><div class="tx"><b>Empfehlung</b>${esc(d.recommendedFlightType)} · Startfenster ${esc(d.bestStartTime)}</div></div>
  </div>
  <div class="dim small" style="text-align:center">3D-Gelände © Esri (Satellit) & Terrarium/AWS (Höhen). Weg = schematische Empfehlung, kein Flugplan. Aktualisiert sich mit jedem Wetter-Refresh.</div>`;
  setTimeout(() => init3DRoute(site, path, d), 30);
}

let _routeMap = null, _routeAnim = null, _fpvAnim = null, _routeCoords = null, _routeGlider = null, _routeMode = 'orbit', _routeTimer = null, _thermalMarkers = [], _routePath = null;
function destroyRoute() {
  if (_routeAnim) { cancelAnimationFrame(_routeAnim); _routeAnim = null; }
  if (_fpvAnim) { cancelAnimationFrame(_fpvAnim); _fpvAnim = null; }
  if (_routeTimer) { clearInterval(_routeTimer); _routeTimer = null; }
  clearThermalMarkers();
  try { if (window.speechSynthesis) window.speechSynthesis.cancel(); } catch (e) { /* ignore */ }
  _routeMode = 'orbit'; _routeCoords = null; _routeGlider = null; _routePath = null;
  if (_routeMap) { try { _routeMap.remove(); } catch (e) { /* ignore */ } _routeMap = null; }
}
function init3DRoute(site, path, d) {
  const host = $('#routeMap'); if (!host) return;
  const coords = path.coords;
  if (typeof maplibregl === 'undefined') { return init2DRoute(host, site, path); }
  destroyRoute();
  const lons = coords.map(c => c[0]), lats = coords.map(c => c[1]);
  const center = [(Math.min(...lons) + Math.max(...lons)) / 2, (Math.min(...lats) + Math.max(...lats)) / 2];
  try {
    _routeMap = new maplibregl.Map({
      container: host, center, zoom: 13.2, pitch: 70, bearing: 20, antialias: true,
      style: {
        version: 8,
        sources: {
          sat: { type: 'raster', tiles: ['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'], tileSize: 256, attribution: 'Imagery © Esri' },
          dem: { type: 'raster-dem', tiles: ['https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png'], encoding: 'terrarium', tileSize: 256, maxzoom: 14 }
        },
        layers: [{ id: 'sat', type: 'raster', source: 'sat' }],
        terrain: { source: 'dem', exaggeration: 1.5 }
      }
    });
  } catch (e) { return init2DRoute(host, site, path); }
  _routeMap.on('error', () => { /* tile errors are non-fatal */ });
  _routeMap.on('load', () => {
    try { _routeMap.setSky({ 'sky-color': '#0a1626', 'horizon-color': '#16314a', 'fog-color': '#0a0e18', 'fog-ground-blend': 0.5 }); } catch (e) { /* older */ }
    _routeMap.addSource('path', { type: 'geojson', data: { type: 'Feature', geometry: { type: 'LineString', coordinates: coords.map(c => [c[0], c[1]]) } } });
    _routeMap.addLayer({ id: 'path-glow', type: 'line', source: 'path', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#2de2e6', 'line-width': 8, 'line-opacity': 0.25, 'line-blur': 4 } });
    _routeMap.addLayer({ id: 'path', type: 'line', source: 'path', layout: { 'line-cap': 'round', 'line-join': 'round' }, paint: { 'line-color': '#2de2e6', 'line-width': 3.5 } });
    const dot = (c) => { const e = document.createElement('div'); e.style.cssText = `width:14px;height:14px;border-radius:50%;background:${c};border:2px solid #fff;box-shadow:0 0 8px ${c}`; return e; };
    new maplibregl.Marker({ element: dot('#22e08a') }).setLngLat([coords[0][0], coords[0][1]]).addTo(_routeMap);
    new maplibregl.Marker({ element: dot('#ff4d5e') }).setLngLat([coords[coords.length - 1][0], coords[coords.length - 1][1]]).addTo(_routeMap);
    const ge = document.createElement('div'); ge.textContent = '🪂'; ge.style.cssText = 'font-size:24px;filter:drop-shadow(0 0 4px #000)';
    const glider = new maplibregl.Marker({ element: ge }).setLngLat([coords[0][0], coords[0][1]]).addTo(_routeMap);
    // fit then animate
    const b = coords.reduce((bb, c) => bb.extend([c[0], c[1]]), new maplibregl.LngLatBounds([coords[0][0], coords[0][1]], [coords[0][0], coords[0][1]]));
    _routeMap.fitBounds(b, { padding: 60, pitch: 70, bearing: 20, duration: 1200 });
    _routeCoords = coords; _routeGlider = glider; _routePath = path;
    host.style.position = 'relative';
    buildRouteOverlay(host);
    renderThermalMarkers(path.thermals);
    animateRoute();
    // live re-sync to current conditions every 2 s (route, thermals, altitudes)
    if (_routeTimer) clearInterval(_routeTimer);
    _routeTimer = setInterval(refreshRouteLive, 2000);
  });
}
// rebuild route + thermals from the latest decision without recreating the map
function refreshRouteLive() {
  if (!_routeMap) return;
  const site = siteById(Store.state.selectedSiteId); const d = Data.decision(); if (!d) return;
  const path = buildIdealPath(site, d); _routePath = path; _routeCoords = path.coords;
  try {
    const src = _routeMap.getSource('path');
    if (src) src.setData({ type: 'Feature', geometry: { type: 'LineString', coordinates: path.coords.map(c => [c[0], c[1]]) } });
  } catch (e) { /* ignore */ }
  renderThermalMarkers(path.thermals);
  const sub = document.getElementById('hudSub'); if (sub) sub.textContent = `${d.recommendedFlightType} · Basis ${round(path.base)} m · ${Time.fmtAge(Data.forecast.fetchedAt || new Date().toISOString())}`;
}
// rising-air columns along the route, sized/coloured by estimated climb
function clearThermalMarkers() { (_thermalMarkers || []).forEach(m => { try { m.remove(); } catch (e) { /* ignore */ } }); _thermalMarkers = []; }
function renderThermalMarkers(thermals) {
  if (!_routeMap) return; clearThermalMarkers();
  (thermals || []).forEach(t => {
    const col = t.strength >= 3 ? '#ff9d2e' : t.strength >= 1.5 ? '#ffd23e' : '#7fe8c0';
    const el = document.createElement('div');
    el.style.cssText = 'pointer-events:none;text-align:center';
    el.innerHTML = `<div style="width:30px;height:30px;border-radius:50%;border:2px solid ${col};box-shadow:0 0 14px ${col};display:grid;place-items:center;animation:thpulse 2s ease-in-out infinite;background:radial-gradient(circle,${col}44,transparent 70%)"><span style="color:${col};font-size:15px">↑</span></div><div style="color:${col};font-weight:800;font-size:11px;text-shadow:0 0 4px #000;margin-top:2px">+${t.strength.toFixed(1)} m/s</div>`;
    const m = new maplibregl.Marker({ element: el }).setLngLat([t.lon, t.lat]).addTo(_routeMap);
    _thermalMarkers.push(m);
  });
}
// distance index helper along the route (constant-speed)
function _routeSegments(coords) {
  const seg = []; let total = 0;
  for (let i = 1; i < coords.length; i++) { const dkm = Geo.haversineKm(coords[i - 1][1], coords[i - 1][0], coords[i][1], coords[i][0]); seg.push({ from: i - 1, len: dkm }); total += dkm; }
  return { seg, total };
}
function _posAt(coords, seg, dist) {
  let acc = 0, idx = 0, f = 0;
  for (const s of seg) { if (acc + s.len >= dist) { idx = s.from; f = s.len ? (dist - acc) / s.len : 0; break; } acc += s.len; idx = s.from + 1; }
  const a = coords[Math.min(idx, coords.length - 1)], b = coords[Math.min(idx + 1, coords.length - 1)];
  return { lng: a[0] + (b[0] - a[0]) * f, lat: a[1] + (b[1] - a[1]) * f, alt: a[2] + (b[2] - a[2]) * f };
}
function animateRoute() {
  const LOOP = 48000; let t0 = performance.now();
  const step = (now) => {
    if (_routeMode !== 'fpv' && _routeCoords && _routeGlider) {
      const { seg, total } = _routeSegments(_routeCoords);
      const p = ((now - t0) % LOOP) / LOOP * total;
      const c = _posAt(_routeCoords, seg, p);
      _routeGlider.setLngLat([c.lng, c.lat]);
      if (_routeMap) { try { _routeMap.setBearing((_routeMap.getBearing() + 0.06) % 360); } catch (e) { /* ignore */ } }
    }
    _routeAnim = requestAnimationFrame(step);
  };
  _routeAnim = requestAnimationFrame(step);
}
// Futuristic HUD overlay + launch / stop controls
function buildRouteOverlay(host) {
  const old = host.querySelector('#routeOverlay'); if (old) old.remove();
  const ov = document.createElement('div'); ov.id = 'routeOverlay';
  ov.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:5;font-family:ui-monospace,SFMono-Regular,Menlo,monospace';
  ov.innerHTML = `
    <div id="ovLaunch" style="position:absolute;left:0;right:0;bottom:18px;display:flex;justify-content:center">
      <button id="vorflugBtn" style="pointer-events:auto;border:1px solid #2de2e6;background:rgba(8,18,28,.72);color:#2de2e6;font-weight:800;letter-spacing:2px;padding:13px 22px;border-radius:12px;font-size:14px;cursor:pointer;backdrop-filter:blur(6px);box-shadow:0 0 18px rgba(45,226,230,.45),inset 0 0 12px rgba(45,226,230,.15)">▶ VORFLUG STARTEN</button>
    </div>
    <div id="ovHud" style="display:none;position:absolute;inset:0">
      <div style="position:absolute;top:10px;left:10px;width:22px;height:22px;border-left:2px solid #2de2e6;border-top:2px solid #2de2e6;opacity:.8"></div>
      <div style="position:absolute;top:10px;right:10px;width:22px;height:22px;border-right:2px solid #2de2e6;border-top:2px solid #2de2e6;opacity:.8"></div>
      <div style="position:absolute;bottom:10px;left:10px;width:22px;height:22px;border-left:2px solid #2de2e6;border-bottom:2px solid #2de2e6;opacity:.8"></div>
      <div style="position:absolute;bottom:10px;right:10px;width:22px;height:22px;border-right:2px solid #2de2e6;border-bottom:2px solid #2de2e6;opacity:.8"></div>
      <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:34px;height:34px;border:1px solid rgba(45,226,230,.6);border-radius:50%;box-shadow:0 0 8px rgba(45,226,230,.5)"><div style="position:absolute;top:50%;left:50%;width:6px;height:6px;background:#2de2e6;border-radius:50%;transform:translate(-50%,-50%)"></div></div>
      <div style="position:absolute;top:14px;left:50%;transform:translateX(-50%);display:flex;gap:16px;background:rgba(6,12,20,.6);border:1px solid rgba(45,226,230,.35);border-radius:10px;padding:8px 16px;backdrop-filter:blur(6px)">
        <div style="text-align:center"><div style="font-size:9px;color:#7fd8dc;letter-spacing:1px">HÖHE</div><div id="hudAlt" style="font-size:17px;font-weight:800;color:#eaf6ff">– m</div></div>
        <div style="text-align:center"><div style="font-size:9px;color:#7fd8dc;letter-spacing:1px">SPEED</div><div id="hudSpd" style="font-size:17px;font-weight:800;color:#eaf6ff">– km/h</div></div>
        <div style="text-align:center"><div style="font-size:9px;color:#7fd8dc;letter-spacing:1px">VARIO</div><div id="hudVario" style="font-size:17px;font-weight:800;color:#7fd8dc">0.0</div></div>
        <div style="text-align:center"><div style="font-size:9px;color:#7fd8dc;letter-spacing:1px">PHASE</div><div id="hudPhase" style="font-size:17px;font-weight:800;color:#2de2e6">START</div></div>
      </div>
      <div id="hudSub" style="position:absolute;top:74px;left:50%;transform:translateX(-50%);font-size:10px;color:#7fd8dc;letter-spacing:1px;text-shadow:0 0 4px #000"></div>
      <button id="fpvStop" style="pointer-events:auto;position:absolute;top:14px;right:14px;border:1px solid #ff4d5e;background:rgba(8,18,28,.7);color:#ff8a93;font-weight:800;padding:7px 12px;border-radius:9px;cursor:pointer;font-size:12px">⏹ STOPP</button>
      <div style="position:absolute;left:14px;right:14px;bottom:16px">
        <div style="display:flex;justify-content:space-between;font-size:10px;color:#7fd8dc;letter-spacing:1px;margin-bottom:4px"><span>VORFLUG · schematisch</span><span id="hudPct">0%</span></div>
        <div style="height:4px;background:rgba(45,226,230,.18);border-radius:3px;overflow:hidden"><div id="hudProg" style="height:100%;width:0;background:#2de2e6;box-shadow:0 0 8px #2de2e6"></div></div>
      </div>
    </div>`;
  host.appendChild(ov);
  const vb = ov.querySelector('#vorflugBtn'); if (vb) vb.addEventListener('click', startFPV);
  const sb = ov.querySelector('#fpvStop'); if (sb) sb.addEventListener('click', stopFPV);
}
function startFPV() {
  if (!_routeMap || !_routeCoords || _routeMode === 'fpv') return;
  _routeMode = 'fpv';
  const ov = document.getElementById('routeOverlay');
  if (ov) { const l = ov.querySelector('#ovLaunch'), h = ov.querySelector('#ovHud'); if (l) l.style.display = 'none'; if (h) h.style.display = 'block'; }
  if (_routeGlider && _routeGlider.getElement) { try { _routeGlider.getElement().style.display = 'none'; } catch (e) { /* ignore */ } }
  try { if (window.speechSynthesis && window.__briefingText) speakBriefing(null); } catch (e) { /* ignore */ }
  const DUR = 17000; const t0 = performance.now();
  const hud = { alt: document.getElementById('hudAlt'), spd: document.getElementById('hudSpd'), phase: document.getElementById('hudPhase'), vario: document.getElementById('hudVario'), prog: document.getElementById('hudProg'), pct: document.getElementById('hudPct') };
  let lastPos = null, lastT = null, lastAlt = null, spd = 32, vario = 0;
  const step = (now) => {
    if (_routeMode !== 'fpv') return;
    const coords = _routeCoords; const { seg, total } = _routeSegments(coords);  // live route
    const pr = clamp((now - t0) / DUR, 0, 1);
    const dist = pr * total;
    const cur = _posAt(coords, seg, dist);
    const ahead = _posAt(coords, seg, Math.min(total, dist + Math.max(0.06, total * 0.05)));
    try {
      const cam = _routeMap.getFreeCameraOptions();
      cam.position = maplibregl.MercatorCoordinate.fromLngLat([cur.lng, cur.lat], cur.alt + 6);
      cam.lookAtPoint([ahead.lng, ahead.lat]);
      _routeMap.setFreeCameraOptions(cam);
    } catch (e) {
      try { const brg = Geo.bearing(cur.lat, cur.lng, ahead.lat, ahead.lng); _routeMap.jumpTo({ center: [cur.lng, cur.lat], bearing: brg, pitch: 85, zoom: 15 }); } catch (e2) { /* ignore */ }
    }
    if (lastPos && lastT) {
      const dt = (now - lastT) / 1000;
      if (dt > 0) { const dk = Geo.haversineKm(lastPos.lat, lastPos.lng, cur.lat, cur.lng); spd = spd * 0.85 + clamp(dk / dt * 3600, 0, 90) * 0.15; vario = vario * 0.8 + ((cur.alt - lastAlt) / dt) * 0.2; }
    }
    lastPos = cur; lastT = now; lastAlt = cur.alt;
    const climbing = vario > 0.3;
    const phase = pr < 0.07 ? 'START' : pr > 0.92 ? 'LANDUNG' : climbing ? 'STEIGEN' : 'GLEITEN';
    if (hud.alt) hud.alt.textContent = round(cur.alt) + ' m';
    if (hud.spd) hud.spd.textContent = round(spd) + ' km/h';
    if (hud.phase) { hud.phase.textContent = phase; hud.phase.style.color = climbing ? '#ff9d2e' : '#2de2e6'; }
    if (hud.vario) { hud.vario.textContent = (vario >= 0 ? '+' : '') + vario.toFixed(1); hud.vario.style.color = climbing ? '#ff9d2e' : '#7fd8dc'; }
    if (hud.prog) hud.prog.style.width = (pr * 100) + '%';
    if (hud.pct) hud.pct.textContent = round(pr * 100) + '%';
    if (pr >= 1) { stopFPV(); return; }
    _fpvAnim = requestAnimationFrame(step);
  };
  _fpvAnim = requestAnimationFrame(step);
}
function stopFPV() {
  if (_fpvAnim) { cancelAnimationFrame(_fpvAnim); _fpvAnim = null; }
  _routeMode = 'orbit';
  try { if (window.speechSynthesis) window.speechSynthesis.cancel(); } catch (e) { /* ignore */ }
  const ov = document.getElementById('routeOverlay');
  if (ov) { const l = ov.querySelector('#ovLaunch'), h = ov.querySelector('#ovHud'); if (l) l.style.display = 'flex'; if (h) h.style.display = 'none'; }
  if (_routeGlider && _routeGlider.getElement) { try { _routeGlider.getElement().style.display = ''; } catch (e) { /* ignore */ } }
  if (_routeMap && _routeCoords) {
    try {
      const coords = _routeCoords;
      const b = coords.reduce((bb, c) => bb.extend([c[0], c[1]]), new maplibregl.LngLatBounds([coords[0][0], coords[0][1]], [coords[0][0], coords[0][1]]));
      _routeMap.easeTo({ pitch: 70, bearing: 20, duration: 700 });
      setTimeout(() => { try { if (_routeMap) _routeMap.fitBounds(b, { padding: 60, pitch: 70, duration: 900 }); } catch (e) { /* ignore */ } }, 720);
    } catch (e) { /* ignore */ }
  }
}
function init2DRoute(host, site, path) {
  if (typeof L === 'undefined') { host.innerHTML = '<div class="loading small">3D-/Kartenansicht hier nicht verfügbar (WebGL/Netz). Pfad-Eckdaten siehe unten.</div>'; return; }
  host.innerHTML = ''; const m = L.map(host, { attributionControl: false });
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxZoom: 17 }).addTo(m);
  const latlngs = path.coords.map(c => [c[1], c[0]]);
  const line = L.polyline(latlngs, { color: '#2de2e6', weight: 4 }).addTo(m);
  L.circleMarker(latlngs[0], { radius: 7, color: '#22e08a', fillOpacity: 1 }).addTo(m);
  L.circleMarker(latlngs[latlngs.length - 1], { radius: 7, color: '#ff4d5e', fillOpacity: 1 }).addTo(m);
  m.fitBounds(line.getBounds(), { padding: [40, 40] });
  host.insertAdjacentHTML('afterbegin', '');
}

/* föhn / no-go browser alert (foreground; true background push needs a server) */
let _lastAlertKey = null;
function maybeAlert() {
  if (!Store.state.alerts || typeof Notification === 'undefined' || Notification.permission !== 'granted') return;
  const d = Data.decision(); if (!d) return;
  const site = siteById(Store.state.selectedSiteId);
  if (d.status === 'black' || d.status === 'red') {
    const key = site.id + d.status + Store.state.day;
    if (key === _lastAlertKey) return; _lastAlertKey = key;
    try { new Notification(`SKYWORTHY: ${sIc[d.status]} ${site.name}`, { body: d.summary, tag: 'skyworthy-' + site.id }); } catch (e) { /* ignore */ }
  }
}

/* ---------- small render helpers ---------- */
function kpi(lbl, val, unit, sub) { return `<div class="kpi"><div class="lbl">${esc(lbl)}</div><div class="val">${val}<small> ${unit}</small></div>${sub ? `<div class="sub">${esc(sub)}</div>` : ''}</div>`; }
function miniScore(lbl, v) { const c = v >= 70 ? 'green' : v >= 50 ? 'yellow' : v >= 35 ? 'orange' : 'red'; return `<div class="kpi s-${c}"><div class="lbl">${esc(lbl)}</div><div class="val" style="color:var(--c)">${v}</div><div class="bar" style="margin-top:6px"><i style="width:${v}%"></i></div></div>`; }
function probBar(lbl, v) { const c = v >= 60 ? 'green' : v >= 40 ? 'yellow' : 'orange'; return `<div style="margin:8px 0"><div style="display:flex;justify-content:space-between" class="small"><span>${esc(lbl)}</span><b>${v}%</b></div><div class="bar s-${c}" style="margin-top:4px"><i style="width:${v}%"></i></div></div>`; }
function riskRow(ic, title, txt) { return `<div class="risk"><div class="ic">${ic}</div><div class="tx"><b>${esc(title)}</b>${esc(txt)}</div></div>`; }
function windArrow(deg) { return `<span class="windarrow" style="transform:rotate(${(deg + 180) % 360}deg)">↑</span>`; }
function loadingCard(t) { return `<div class="card loading"><span class="spin"></span>${esc(t)}</div>`; }
function errorCard(t) { return `<div class="card s-red"><b style="color:var(--c)">Fehler beim Laden</b><div class="small muted" style="margin-top:6px">${esc(t)}</div><button class="btn" style="margin-top:12px" onclick="Data.loadForecast(true)">Erneut versuchen</button></div>`; }
function banner(s, t) { return `<div class="banner s-${s}">${esc(t)}</div>`; }
// live webcams near a site (coordinate-deep-link — embedding frames needs an API key)
function webcamCard(site) {
  const lat = site.lat.toFixed(4), lon = site.lon.toFixed(4);
  const windy = `https://www.windy.com/webcams?${lat},${lon},11`;
  const meteo = `https://www.meteoblue.com/de/wetter/webcams/${lat}N${lon}E`;
  return `<div class="card"><div class="h" style="margin-top:0">📷 Live-Webcams · ${esc(site.name)}</div>
    <div class="small muted">Echte Live-Bilder aus der Umgebung — vor dem Losfahren der Blick zum Himmel.</div>
    <div class="row" style="margin-top:10px">
      <a class="btn sec" href="${windy}" target="_blank" rel="noopener" style="text-decoration:none;text-align:center">Windy Cams</a>
      <a class="btn sec" href="${meteo}" target="_blank" rel="noopener" style="text-decoration:none;text-align:center">meteoblue Cams</a>
    </div></div>`;
}

/* ============================================================
   PREMIUM CHARTS — inline SVG (no chart lib needed)
   ============================================================ */
const dewFromRh = (T, rh) => { rh = clamp(rh, 1, 100); const g = Math.log(rh / 100) + 17.625 * T / (243.04 + T); return 243.04 * g / (17.625 - g); };

// vertical atmospheric profile: temperature + dewpoint vs altitude (emagram-style)
function emagramSVG(agg, i, site) {
  const h = agg.atHour(i);
  const surf = { h: site.elevationMin, t: h.temp, td: h.dew };
  const pts = [surf];
  h.upper.forEach(u => { if (u.h > site.elevationMin + 50 && u.temp > -80) pts.push({ h: u.h, t: u.temp, td: dewFromRh(u.temp, u.rh || 30) }); });
  pts.sort((a, b) => a.h - b.h);
  if (pts.length < 2) return '<div class="small dim">Kein Profil verfügbar.</div>';
  const W = 320, H = 250, pad = 34;
  const hMin = pts[0].h, hMax = pts[pts.length - 1].h;
  const allT = pts.flatMap(p => [p.t, p.td]); const tMin = Math.floor(Math.min(...allT) - 2), tMax = Math.ceil(Math.max(...allT) + 2);
  const x = t => pad + (t - tMin) / (tMax - tMin) * (W - pad - 8);
  const y = hh => H - pad - (hh - hMin) / (hMax - hMin) * (H - pad - 12);
  const line = (key, col) => pts.map((p, k) => `${k ? 'L' : 'M'}${round(x(p[key]), 1)},${round(y(p.h), 1)}`).join(' ');
  const cloudBase = agg.cloudBaseAsl(i);
  const grid = [];
  for (let gh = Math.ceil(hMin / 1000) * 1000; gh < hMax; gh += 1000) grid.push(`<line x1="${pad}" y1="${round(y(gh),1)}" x2="${W-8}" y2="${round(y(gh),1)}" stroke="#1f2838"/><text x="${pad-4}" y="${round(y(gh),1)+3}" fill="#5d6878" font-size="9" text-anchor="end">${gh}</text>`);
  for (let gt = Math.ceil(tMin / 5) * 5; gt < tMax; gt += 5) grid.push(`<line x1="${round(x(gt),1)}" y1="12" x2="${round(x(gt),1)}" y2="${H-pad}" stroke="#141c2a"/><text x="${round(x(gt),1)}" y="${H-pad+12}" fill="#5d6878" font-size="9" text-anchor="middle">${gt}°</text>`);
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block">
    ${grid.join('')}
    ${cloudBase > hMin && cloudBase < hMax ? `<line x1="${pad}" y1="${round(y(cloudBase),1)}" x2="${W-8}" y2="${round(y(cloudBase),1)}" stroke="#2de2e6" stroke-dasharray="4 3"/><text x="${W-10}" y="${round(y(cloudBase),1)-4}" fill="#2de2e6" font-size="9" text-anchor="end">Basis ~${round(cloudBase)} m</text>` : ''}
    <path d="${line('td','#3aa0ff')}" fill="none" stroke="#3aa0ff" stroke-width="2.2"/>
    <path d="${line('t','#ff4d5e')}" fill="none" stroke="#ff4d5e" stroke-width="2.2"/>
    ${pts.map(p => `<circle cx="${round(x(p.t),1)}" cy="${round(y(p.h),1)}" r="2.4" fill="#ff4d5e"/><circle cx="${round(x(p.td),1)}" cy="${round(y(p.h),1)}" r="2.4" fill="#3aa0ff"/>`).join('')}
  </svg>
  <div class="small dim" style="margin-top:6px"><span style="color:#ff4d5e">━</span> Temperatur · <span style="color:#3aa0ff">━</span> Taupunkt · enge Spreizung = Wolken/Basis. Höhe in m ASL.</div>`;
}

// wind speed + direction vs altitude
function windProfileSVG(agg, i, site) {
  const targets = []; for (let a = site.elevationMin; a <= site.elevationMin + 4500; a += 500) targets.push(a);
  const rows = targets.map(a => ({ a, w: agg.windAtAlt(i, a) })).filter(r => r.w);
  if (rows.length < 2) return '';
  const W = 320, H = 230, pad = 30;
  const aMin = rows[0].a, aMax = rows[rows.length - 1].a;
  const sMax = Math.max(20, Math.ceil(Math.max(...rows.map(r => r.w.spd)) / 10) * 10);
  const x = s => pad + s / sMax * (W - pad - 26);
  const y = a => H - pad - (a - aMin) / (aMax - aMin) * (H - pad - 14);
  const path = rows.map((r, k) => `${k ? 'L' : 'M'}${round(x(r.w.spd),1)},${round(y(r.a),1)}`).join(' ');
  const grid = [];
  for (let s = 10; s <= sMax; s += 10) grid.push(`<line x1="${round(x(s),1)}" y1="12" x2="${round(x(s),1)}" y2="${H-pad}" stroke="#141c2a"/><text x="${round(x(s),1)}" y="${H-pad+12}" fill="#5d6878" font-size="9" text-anchor="middle">${s}</text>`);
  for (let a = Math.ceil(aMin/1000)*1000; a < aMax; a += 1000) grid.push(`<line x1="${pad}" y1="${round(y(a),1)}" x2="${W-22}" y2="${round(y(a),1)}" stroke="#1f2838"/><text x="${pad-3}" y="${round(y(a),1)+3}" fill="#5d6878" font-size="9" text-anchor="end">${a}</text>`);
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block">
    ${grid.join('')}
    <path d="${path}" fill="none" stroke="#2de2e6" stroke-width="2.4"/>
    ${rows.map(r => `<g transform="translate(${round(x(r.w.spd),1)},${round(y(r.a),1)})"><circle r="2.4" fill="#2de2e6"/><text x="8" y="-4" fill="#8a96ab" font-size="9" transform="rotate(${(r.w.dir+180)%360})"></text></g>`).join('')}
    ${rows.map(r => `<text x="${round(x(r.w.spd),1)+6}" y="${round(y(r.a),1)+3}" fill="#8a96ab" font-size="8">${Wind.toCompass(r.w.dir)}</text>`).join('')}
  </svg>
  <div class="small dim" style="margin-top:6px">Windgeschwindigkeit (km/h, x) über Höhe (m ASL, y), mit Richtung je Stufe.</div>`;
}

// CAPE area chart across the day
function capeTimelineSVG(agg, dayOffset) {
  const idx = agg.daylightIdx(dayOffset); if (!idx.length) return '';
  const rows = idx.map(i => ({ hh: agg.atHour(i).hh, cape: agg.atHour(i).cape }));
  const W = 320, H = 150, pad = 28;
  const cMax = Math.max(500, Math.ceil(Math.max(...rows.map(r => r.cape)) / 250) * 250);
  const x = k => pad + k / (rows.length - 1) * (W - pad - 8);
  const y = c => H - pad - c / cMax * (H - pad - 12);
  const path = rows.map((r, k) => `${k ? 'L' : 'M'}${round(x(k),1)},${round(y(r.cape),1)}`).join(' ');
  const area = `M${pad},${H-pad} ${rows.map((r, k) => `L${round(x(k),1)},${round(y(r.cape),1)}`).join(' ')} L${round(x(rows.length-1),1)},${H-pad} Z`;
  const grid = [];
  for (let c = 0; c <= cMax; c += cMax / 2) grid.push(`<line x1="${pad}" y1="${round(y(c),1)}" x2="${W-8}" y2="${round(y(c),1)}" stroke="#1f2838"/><text x="${pad-3}" y="${round(y(c),1)+3}" fill="#5d6878" font-size="9" text-anchor="end">${round(c)}</text>`);
  const labels = rows.map((r, k) => k % 3 === 0 ? `<text x="${round(x(k),1)}" y="${H-pad+12}" fill="#5d6878" font-size="9" text-anchor="middle">${r.hh}</text>` : '').join('');
  const thr = 1500;
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block">
    ${grid.join('')}
    ${thr < cMax ? `<line x1="${pad}" y1="${round(y(thr),1)}" x2="${W-8}" y2="${round(y(thr),1)}" stroke="#ff9d2e" stroke-dasharray="4 3"/><text x="${W-10}" y="${round(y(thr),1)-3}" fill="#ff9d2e" font-size="9" text-anchor="end">Cb-Schwelle</text>` : ''}
    <path d="${area}" fill="rgba(45,226,230,.14)"/>
    <path d="${path}" fill="none" stroke="#2de2e6" stroke-width="2.2"/>
    ${labels}
  </svg>
  <div class="small dim" style="margin-top:6px">CAPE (J/kg) im Tagesverlauf. Über der Cb-Schwelle wächst das Gewitter-/Überentwicklungsrisiko.</div>`;
}

/* ============================================================
   ROUTER + ACTIONS + INIT
   ============================================================ */
const PRIMARY = ['morning', 'cockpit', 'sites', 'live', 'exam', 'more'];
let currentScreen = 'morning';
let lastPrimary = 'morning';
function go(screen) {
  if (currentScreen === 'route' && screen !== 'route') destroyRoute();
  if (currentScreen === 'morning' && screen !== 'morning') stopMorningTimers();
  currentScreen = screen;
  if (PRIMARY.includes(screen)) lastPrimary = screen;
  $$('.screen').forEach(s => s.classList.remove('on'));
  $('#screen-' + screen).classList.add('on');
  // highlight matching primary tab; sub-screens highlight "Mehr"
  const navTarget = PRIMARY.includes(screen) ? screen : 'more';
  $$('.tab').forEach(t => t.classList.toggle('on', t.dataset.screen === navTarget));
  const back = $('#backBtn'); if (back) back.style.display = PRIMARY.includes(screen) ? 'none' : 'grid';
  if (screen === 'morning') { startMorningTimers(); if (!Data.bestNow.result && !Data.bestNow.loading) Data.loadBestSiteNow(); }
  window.scrollTo(0, 0);
  render();
}
function goBack() { go(currentScreen === 'detail' ? 'cockpit' : lastPrimary === 'more' ? 'more' : lastPrimary); }
function setSimple(on) { Store.set({ simple: !!on }); render(); }
function selectSite(id, toCockpit) {
  const recent = [id, ...Store.state.recent.filter(x => x !== id)].slice(0, 8);
  Store.set({ selectedSiteId: id, recent });
  $('#siteSelect').value = id;
  Data.loadForecast(true);
  if (toCockpit) go('cockpit'); else render();
}
function toggleFav(id) {
  const fav = Store.state.favorites.includes(id) ? Store.state.favorites.filter(x => x !== id) : [...Store.state.favorites, id];
  Store.set({ favorites: fav }); render();
}
function applyPreset(p) { Store.set({ pilot: { ...Store.state.pilot, ...p } }); Data.recomputeBestNow(); render(); }
function savePilot() {
  const g = id => $('#' + id);
  Store.set({ pilot: {
    ...Store.state.pilot,
    name: g('p-name').value || 'Pilot', level: g('p-level').value, wingClass: g('p-wing').value,
    maxWindKmh: clamp(+g('p-maxwind').value || 28, 5, 60), maxGustKmh: clamp(+g('p-maxgust').value || 35, 5, 80),
    hoursTotal: +g('p-hours').value || 0, riskTolerance: g('p-risk').value,
    alpineExperience: g('p-alpine').checked, sivExperience: g('p-siv').checked
  } });
  Data.recomputeBestNow();
  render();
  const btn = $('#screen-profile .btn'); if (btn) { btn.textContent = '✓ Gespeichert'; setTimeout(() => btn.textContent = 'Profil speichern', 1500); }
}
// expose for inline handlers
function toggleAlerts(on) {
  if (on && typeof Notification !== 'undefined' && Notification.permission !== 'granted') {
    Notification.requestPermission().then(p => { Store.set({ alerts: p === 'granted' }); maybeAlert(); render(); });
    return;
  }
  Store.set({ alerts: !!on }); if (on) maybeAlert();
}
Object.assign(window, { go, goBack, setSimple, selectSite, toggleFav, applyPreset, savePilot, answerExam, nextExam, answerContext, toggleAlerts, speakBriefing, setBestRadius, refreshBestLocation, recordFlightFeedback, Data });

function buildSiteSelect() {
  const sel = $('#siteSelect');
  sel.innerHTML = SITES.map(s => `<option value="${s.id}">${esc(s.name)} · ${esc(s.region)}</option>`).join('');
  sel.value = Store.state.selectedSiteId;
  sel.addEventListener('change', () => selectSite(sel.value));
}

function init() {
  Store.load();
  Learn.load();
  buildSiteSelect();
  // tabs
  $$('.tab').forEach(t => t.addEventListener('click', () => go(t.dataset.screen)));
  // day toggle
  $$('.daytoggle button').forEach(b => b.addEventListener('click', () => {
    $$('.daytoggle button').forEach(x => x.classList.remove('on')); b.classList.add('on');
    Store.set({ day: +b.dataset.day });
    if (Data.models.res) Data.models.consensus = buildModelConsensus(Data.models.res, Store.state.day);
    Data.recomputeBestNow();
    render();
  }));
  // back button (sub-screens)
  const back = $('#backBtn'); if (back) back.addEventListener('click', goBack);
  // refresh + pull-to-refresh
  $('#refreshBtn').addEventListener('click', () => Data.loadForecast(true));
  let touchStartY = 0;
  window.addEventListener('touchstart', e => { touchStartY = e.touches[0].clientY; }, { passive: true });
  window.addEventListener('touchend', e => {
    if (window.scrollY <= 0 && e.changedTouches[0].clientY - touchStartY > 110) Data.loadForecast(true);
  }, { passive: true });
  // online/offline
  window.addEventListener('online', () => { Store.set({ online: true }); Data.loadForecast(true); });
  window.addEventListener('offline', () => { Store.set({ online: false }); renderTopbar(); });
  // periodic topbar age update
  setInterval(renderTopbar, 30 * 1000);
  // keep passive data screens visibly live (ages, recomputed decision from cached data)
  setInterval(() => {
    if (['cockpit', 'detail', 'wind', 'thermal', 'cloud', 'pressure', 'models'].includes(currentScreen)) render();
  }, 20 * 1000);

  Data.startAutoRefresh();
  Data.loadForecast(true);
  render();
  if (currentScreen === 'morning') { startMorningTimers(); Data.loadBestSiteNow(); }
  if (!Store.state.onboarded) showOnboarding();
}

/* one-time, one-tap setup: pick experience level → sets sensible limits */
function showOnboarding() {
  const presets = {
    beginner: { maxWindKmh: 18, maxGustKmh: 22, maxThermalStrength: 2, level: 'beginner', wingClass: 'EN-A', riskTolerance: 'low' },
    intermediate: { maxWindKmh: 28, maxGustKmh: 35, maxThermalStrength: 4, level: 'intermediate', wingClass: 'EN-B-low', riskTolerance: 'medium' },
    expert: { maxWindKmh: 38, maxGustKmh: 50, maxThermalStrength: 6, level: 'expert', wingClass: 'EN-C', riskTolerance: 'high' }
  };
  const ov = document.createElement('div');
  ov.id = 'onboard';
  ov.style.cssText = 'position:fixed;inset:0;z-index:150;display:grid;place-items:center;padding:24px;background:radial-gradient(900px 500px at 50% -10%,#0e1626,#05070d 75%)';
  ov.innerHTML = `<div style="width:100%;max-width:380px;text-align:center">
    <div style="font-size:24px;letter-spacing:5px;font-weight:900">WILLKOMMEN</div>
    <div class="small" style="color:var(--cyan);letter-spacing:2px;text-transform:uppercase;margin:6px 0 22px">Wie erfahren bist du?</div>
    <button class="btn" data-lvl="beginner" style="margin-bottom:10px">🟢 Anfänger / Schüler<br><span style="font-weight:500;font-size:12px;opacity:.8">vorsichtige Limits, einfache Tage</span></button>
    <button class="btn" data-lvl="intermediate" style="margin-bottom:10px">🟡 Fortgeschritten<br><span style="font-weight:500;font-size:12px;opacity:.8">ausgewogene Limits</span></button>
    <button class="btn" data-lvl="expert" style="margin-bottom:10px">🟠 Experte<br><span style="font-weight:500;font-size:12px;opacity:.8">höhere Limits, sportliche Tage</span></button>
    <div class="small dim" id="ob-skip" style="margin-top:14px;cursor:pointer;text-decoration:underline">später / überspringen</div>
    <div class="small dim" style="margin-top:14px;line-height:1.5">Du kannst alles jederzeit im Profil ändern. SKYWORTHY entscheidet bewusst konservativ — im Zweifel: nicht fliegen.</div>
  </div>`;
  document.body.appendChild(ov);
  const done = () => { Store.set({ onboarded: true }); ov.remove(); render(); };
  ov.querySelectorAll('button[data-lvl]').forEach(b => b.addEventListener('click', () => { applyPreset(presets[b.dataset.lvl]); done(); }));
  ov.querySelector('#ob-skip').addEventListener('click', done);
}
// On GitHub Pages the password gate calls startSkyworthy() after unlock (body.locked).
// On an ungated copy (e.g. Vercel behind middleware) auto-start instead.
window.startSkyworthy = () => { if (!window.__skyworthyStarted) { window.__skyworthyStarted = true; init(); } };
if (!(document.body && document.body.classList.contains('locked'))) {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', window.startSkyworthy);
  else window.startSkyworthy();
}
