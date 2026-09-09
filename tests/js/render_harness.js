/* Renders the dashboard in a minimal DOM stub and asserts the cost cards get
 * populated.
 *
 * This exists because of a real bug: /api/summary and /api/meta are fetched
 * concurrently, and the cost renderer used to bail out unless meta had already
 * arrived and set a flag. In a browser summary almost always wins, so all four
 * cost cards rendered as em dashes - while every server-side test and every
 * curl check passed, because the fault was purely in the client's ordering.
 *
 * The harness therefore resolves /api/summary BEFORE /api/meta on purpose.
 *
 *   node tests/js/render_harness.js [path/to/dashboard.js]
 *
 * Exits non-zero if any cost card is empty. Driven by tests/test_dashboard_render.py.
 */
const fs = require("fs");
const path = require("path");

const jsPath = process.argv[2] || path.join(__dirname, "..", "..", "dashboard", "js", "dashboard.js");
const js = fs.readFileSync(jsPath, "utf8");

// ---------------------------------------------------------------- DOM stub
function makeEl(id) {
  return {
    id, textContent: "", innerHTML: "", hidden: false, value: "",
    classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); },
                 toggle(c, on) { on ? this._s.add(c) : this._s.delete(c); },
                 contains(c) { return this._s.has(c); } },
    style: {}, setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    addEventListener() {}, appendChild() {}, getBoundingClientRect() {
      return { width: 100, height: 20, left: 0, top: 0 };
    },
    parentNode: null, disabled: false, clientWidth: 600,
  };
}
const els = {};
const doc = {
  getElementById(id) { return (els[id] = els[id] || makeEl(id)); },
  querySelector() { return makeEl("q"); },
  querySelectorAll() { return []; },
  createElement(t) { return makeEl(t); },
  createElementNS(ns, t) { return makeEl(t); },
  addEventListener() {},
  readyState: "complete",
  body: makeEl("body"),
  documentElement: makeEl("html"),
};

// -------------------------------------------------------------- API stubs
const SUMMARY = {
  prompts: 418, human_prompts: 418, input_tokens: 84528, output_tokens: 8400662,
  cache_tokens: 2035000000, cache_read_input_tokens: 2003427806,
  cache_creation_input_tokens: 35000000, total_tokens: 2043000000,
  projects: 26, first_date: "2026-07-22", last_date: "2026-09-09",
  cost: {
    input_cost_inr: 37.19, cached_input_cost_inr: 107887.53,
    output_cost_inr: 18525.48, total_cost_inr: 126450.20,
    input_cost_usd: 0.42, cached_input_cost_usd: 1226.0,
    output_cost_usd: 210.5, total_cost_usd: 1436.93,
    currency: "INR", usd_to_inr: 88.0, cache_write_ttl: "5m",
    pricing_as_of: "2026-09-09", priced: true,
    unpriced_tokens: 0, unpriced_models: [],
  },
};
const META = {
  tracker_version: "1.0.0", total_interactions: 418, store_prompt_text: true,
  pricing_enabled: true, usd_to_inr: 88.0, pricing_as_of: "2026-09-09",
  page_size: 25, auto_refresh_seconds: 0, platform: "Windows",
};

// Deliberate ordering: summary lands first, meta second - the real-world race.
const DELAYS = { "/api/summary": 0, "/api/projects": 0, "/api/usage": 0,
                 "/api/prompts": 0, "/api/filters": 0, "/api/meta": 30 };

function payloadFor(p) {
  if (p.startsWith("/api/summary")) return SUMMARY;
  if (p.startsWith("/api/meta")) return META;
  if (p.startsWith("/api/projects")) return { rows: [] };
  if (p.startsWith("/api/usage")) return { rows: [] };
  if (p.startsWith("/api/prompts")) return { rows: [], total: 0, limit: 25, page: 1, pages: 1 };
  if (p.startsWith("/api/filters")) return { projects: [], models: [], years: [] };
  return {};
}

global.document = doc;
global.window = { innerWidth: 1200, addEventListener() {}, matchMedia: () => ({ matches: false }) };
global.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
global.URLSearchParams = URLSearchParams;
global.setInterval = () => 0;
global.clearInterval = () => {};
global.setTimeout = setTimeout;
global.clearTimeout = clearTimeout;
global.fetch = function (url) {
  const p = String(url).split("?")[0];
  const key = Object.keys(DELAYS).find((k) => p.startsWith(k));
  const delay = key ? DELAYS[key] : 0;
  return new Promise((resolve) => {
    setTimeout(() => resolve({
      ok: true, status: 200, json: () => Promise.resolve(payloadFor(p)),
    }), delay);
  });
};

// ------------------------------------------------------------------- run
eval(js);

setTimeout(() => {
  const read = (id) => (els[id] ? els[id].textContent : "<never rendered>");
  const cards = els["cost-cards"];
  console.log("\n=== cost cards after load (summary resolved before meta) ===");
  console.log("  section hidden        :", cards ? cards.hidden : "n/a");
  const vals = {
    "Input Tokens Cost": read("c-cost-input"),
    "Cached Input Tokens Cost": read("c-cost-cached"),
    "Output Tokens Cost": read("c-cost-output"),
    "Total Cost": read("c-cost-total"),
  };
  let bad = 0;
  for (const [label, v] of Object.entries(vals)) {
    const empty = !v || v === "—" || v === "<never rendered>";
    if (empty) bad++;
    console.log("  " + (empty ? "[EMPTY] " : "[OK]    ") + label.padEnd(26) + " " + v);
  }
  console.log("\n  note:", els["cost-note"] ? els["cost-note"].innerHTML.slice(0, 90) : "-");
  console.log(bad ? "\nBUG REPRODUCED: " + bad + " card(s) empty" : "\nALL COST CARDS POPULATED");
  process.exit(bad ? 1 : 0);
}, 200);
