/* SKYWORTHY self-test — integration harness (run: node skyworthy/selftest.js)
   Mocks Open-Meteo / Pioupiou / models / pressure, drives the real async load
   flows and renders every screen + key interactions, asserting no errors.
   Exits non-zero on any failure so it can gate releases. */
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const APP = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8')
  .replace('if (!(document.body', 'if (false && !(document.body')   // disable auto-start
  + '\n;globalThis.__T = { Store, Learn, Data, SITES, render, go, currentScreen: () => currentScreen, buildAggregation, calculateFlightDecision };';

/* ---- realistic mocked payloads ---- */
const N = 48;
const T = [];
const base = new Date(); base.setHours(0, 0, 0, 0);
for (let i = 0; i < N; i++) T.push(new Date(base.getTime() + i * 3600000).toISOString().slice(0, 16));
function diurnal(i, lo, hi) { const h = i % 24; const f = Math.max(0, Math.sin((h - 6) / 12 * Math.PI)); return lo + (hi - lo) * f; }
function forecastJSON() {
  const H = { time: T };
  const set = (k, fn) => H[k] = T.map((_, i) => fn(i));
  set('temperature_2m', i => round(diurnal(i, 4, 18)));
  set('relative_humidity_2m', i => 60);
  set('dew_point_2m', i => 5);
  set('precipitation', i => 0);
  set('precipitation_probability', i => 5);
  set('cloud_cover', i => 30); set('cloud_cover_low', i => 10); set('cloud_cover_mid', i => 15); set('cloud_cover_high', i => 20);
  set('wind_speed_10m', i => round(diurnal(i, 6, 16)));
  set('wind_direction_10m', i => 40);
  set('wind_gusts_10m', i => round(diurnal(i, 9, 24)));
  set('cape', i => round(diurnal(i, 30, 700)));
  set('freezing_level_height', i => 3200);
  set('surface_pressure', i => 955);
  set('pressure_msl', i => 1018);
  set('wind_speed_80m', i => round(diurnal(i, 10, 22)));
  set('wind_direction_80m', i => 45);
  set('wind_speed_120m', i => round(diurnal(i, 12, 26)));
  set('wind_speed_180m', i => round(diurnal(i, 14, 30)));
  [925, 850, 800, 700, 600, 500].forEach((p, k) => {
    set(`wind_speed_${p}hPa`, i => 16 + k * 6);
    set(`wind_direction_${p}hPa`, i => 45 + k * 6);
    set(`geopotential_height_${p}hPa`, i => [800, 1500, 2000, 3000, 4200, 5600][k]);
    set(`temperature_${p}hPa`, i => 6 - k * 6);
    set(`relative_humidity_${p}hPa`, i => 55 - k * 6);
  });
  return { hourly: H };
  function round(x) { return Math.round(x); }
}
function modelsJSON() {
  const models = ['icon_seamless', 'ecmwf_ifs025', 'gfs_seamless', 'meteofrance_seamless', 'gem_seamless'];
  const H = { time: T };
  models.forEach((m, k) => {
    H[`wind_speed_10m_${m}`] = T.map((_, i) => 12 + k);
    H[`wind_gusts_10m_${m}`] = T.map((_, i) => 20 + k * 2);
    H[`wind_direction_10m_${m}`] = T.map(() => 40 + k * 3);
    H[`cloud_cover_${m}`] = T.map(() => 30);
    H[`precipitation_${m}`] = T.map(() => 0);
  });
  return { hourly: H };
}
function pioupiouJSON(lat, lon) {
  const now = new Date().toISOString();
  return { data: [
    { id: 101, meta: { name: 'Testberg Gipfel' }, location: { latitude: lat + 0.01, longitude: lon + 0.01 }, measurements: { date: now, wind_heading: 45, wind_speed_avg: 14, wind_speed_max: 22 } },
    { id: 102, meta: { name: 'Testtal' }, location: { latitude: lat - 0.02, longitude: lon - 0.01 }, measurements: { date: now, wind_heading: 50, wind_speed_avg: 9, wind_speed_max: 15 } }
  ] };
}
function mockFetch(url) {
  const u = String(url);
  let body;
  if (u.includes('api.pioupiou.fr')) body = pioupiouJSON(47.66, 11.50);
  else if (u.includes('current=pressure_msl')) { const lats = (new URL(u)).searchParams.get('latitude').split(','); body = lats.map(() => ({ current: { pressure_msl: 1016 + Math.random() * 4 } })); }
  else if (u.includes('models=')) body = modelsJSON();
  else if (u.includes('geocoding-api')) body = { results: [] };
  else body = forecastJSON();
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
}

/* ---- DOM + env shim ---- */
const elCache = {};
function mkEl() {
  const e = { innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, children: [],
    classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, toggle(c, on) { if (on === undefined) on = !this._s.has(c); on ? this._s.add(c) : this._s.delete(c); }, contains(c) { return this._s.has(c); } },
    addEventListener() {}, appendChild(x) { this.children.push(x); }, removeChild() {}, remove() {}, setAttribute() {}, getAttribute() { return null; },
    insertAdjacentHTML() {}, focus() {}, setView() { return e; }, getElement() { return e; } };
  e.querySelector = () => mkEl();
  e.querySelectorAll = () => [];
  return e;
}
const doc = {
  addEventListener() {}, readyState: 'complete',
  body: { classList: { contains: () => true }, appendChild() {}, querySelector: () => null, querySelectorAll: () => [] },
  createElement() { return mkEl(); },
  querySelector(sel) { return elCache[sel] || (elCache[sel] = mkEl()); },
  querySelectorAll() { return []; },
  getElementById(id) { return elCache['#' + id] || (elCache['#' + id] = mkEl()); }
};
const store = {};
const ctx = {
  console, Math, Date, JSON, isFinite, parseInt, parseFloat, isNaN, URLSearchParams, URL, Promise, Set, Array, Object, String, Number,
  setTimeout: (fn) => setTimeout(fn, 0), clearTimeout, setInterval: () => 0, clearInterval: () => {},
  performance: { now: () => Date.now() },
  navigator: { onLine: true, geolocation: { getCurrentPosition: (ok) => ok({ coords: { latitude: 47.66, longitude: 11.50 } }) } },
  localStorage: { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; }, removeItem: k => { delete store[k]; } },
  window: { addEventListener() {}, scrollTo() {}, scrollY: 0, location: { href: '' }, speechSynthesis: undefined },
  document: doc, fetch: mockFetch, maplibregl: undefined, L: undefined, requestAnimationFrame: () => 0, cancelAnimationFrame: () => {}
};
ctx.window.document = doc; ctx.globalThis = ctx;
// preset persisted state so onboarding is skipped and a site is selected
store['skyworthy'] = JSON.stringify({ onboarded: true, selectedSiteId: 'brauneck', simple: true });
vm.createContext(ctx);
vm.runInContext(APP, ctx);

/* ---- run ---- */
const A = ctx.__T;
let fails = 0, checks = 0;
function ok(name, cond, detail) { checks++; if (!cond) { fails++; console.log('  ✗ ' + name + (detail ? ' — ' + detail : '')); } else console.log('  ✓ ' + name); }

const SCREENS = ['morning', 'cockpit', 'sites', 'detail', 'live', 'wind', 'thermal', 'cloud', 'pressure', 'models', 'route', 'windows', 'compare', 'why', 'trust', 'feedback', 'profile', 'exam', 'pro', 'more'];

(async () => {
  A.Store.load(); A.Learn.load();
  console.log('— async data flows —');
  await A.Data.loadForecast(true);
  await A.Data.loadModels();
  await A.Data.loadPressureField();
  await A.Data.loadBestSiteNow();
  ok('forecast aggregated', !!A.Data.forecast.agg, 'no agg');
  const d = A.Data.decision();
  ok('decision has best hour', !!(d && d.best), 'no best');
  ok('decision status valid', d && ['green', 'yellow', 'orange', 'red', 'black', 'gray'].includes(d.status), d && d.status);
  ok('live stations loaded (Pioupiou)', A.Data.stations.list.length > 0 && A.Data.stations.source === 'Pioupiou', A.Data.stations.source);
  ok('model consensus built', !!A.Data.models.consensus, 'none');
  ok('pressure field built', !!A.Data.pressure.field, 'none');
  ok('best-site result', !!(A.Data.bestNow.result && A.Data.bestNow.result.rankedSites.length), 'empty');
  ok('best site has window+takeoff', !!(A.Data.bestNow.result.bestSite && A.Data.bestNow.result.bestSite.bestTakeoff), 'missing');

  console.log('— render every screen —');
  for (const s of SCREENS) {
    try { A.go(s); } catch (e) { fails++; checks++; console.log('  ✗ ' + s + ' THREW ' + e.message); continue; }
    const html = (elCache['#screen-' + s] || {}).innerHTML || '';
    ok(s, html.length > 25 && !/Render-Fehler/.test(html), /Render-Fehler/.test(html) ? html.slice(0, 120) : 'len ' + html.length);
  }

  console.log('— interactions —');
  try { A.Store.set({ simple: false }); A.go('cockpit'); ok('profi mode renders', !/Render-Fehler/.test((elCache['#screen-cockpit'] || {}).innerHTML || '')); A.Store.set({ simple: true }); } catch (e) { fails++; checks++; console.log('  ✗ profi ' + e.message); }
  try { A.Data.bestNow.radiusKm = 75; A.Data.recomputeBestNow(); A.go('morning'); ok('radius recompute', true); } catch (e) { fails++; checks++; console.log('  ✗ radius ' + e.message); }
  try { const before = A.Learn.data.feedback.length; A.Learn.recordFullFeedback({ siteId: 'brauneck', forecastTime: new Date().toISOString(), wasFlyable: true, actualWindFeeling: 'stronger', actualGustFeeling: 'much_stronger', thermalFeeling: 'as_forecast', turbulenceFeeling: 'sporty', appRecommendationWasHelpful: true, wouldFlyAgain: true }); ok('feedback stored + learns', A.Learn.data.feedback.length === before + 1 && A.Learn.data.personal.samples > 0); } catch (e) { fails++; checks++; console.log('  ✗ feedback ' + e.message); }
  try { A.Data.recomputeBestNow(); ok('recompute after learning', !!A.Data.bestNow.result); } catch (e) { fails++; checks++; console.log('  ✗ recompute ' + e.message); }

  console.log(`\n${fails ? '✗ FAIL' : '✓ PASS'} — ${checks - fails}/${checks} checks`);
  process.exit(fails ? 1 : 0);
})().catch(e => { console.log('FATAL', e.stack); process.exit(1); });
