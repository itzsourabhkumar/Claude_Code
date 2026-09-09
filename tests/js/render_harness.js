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
// Table bodies are reached via querySelector, not by id, so give those a
// stable identity and record what gets written into them.
const tableBodies = {};

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
    // Real elements expose these; renderPromptTable/renderProjectTable call
    // them on a tbody to bind click handlers after writing innerHTML.
    querySelector() { return makeEl("child"); },
    querySelectorAll() { return []; },
    parentNode: null, disabled: false, clientWidth: 600,
  };
}
const els = {};
const doc = {
  getElementById(id) { return (els[id] = els[id] || makeEl(id)); },
  querySelector(sel) {
    if (sel === "#project-table tbody") {
      return (tableBodies.project = tableBodies.project || makeEl("project-tbody"));
    }
    if (sel === "#prompt-table tbody") {
      return (tableBodies.prompt = tableBodies.prompt || makeEl("prompt-tbody"));
    }
    return makeEl("q");
  },
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
const PROJECTS = { rows: [
  { project: "datalake_llm", prompts: 99, input_tokens: 32812, output_tokens: 1564570,
    cache_tokens: 521813237, total_tokens: 523410619, pct: 26.6,
    input_cost_inr: 14.44, cached_input_cost_inr: 27183.02, output_cost_inr: 3442.05,
    total_cost_inr: 30639.51, total_cost_usd: 348.18, priced: true, unpriced_tokens: 0 },
  { project: "unpriced_proj", prompts: 2, input_tokens: 10, output_tokens: 20,
    cache_tokens: 30, total_tokens: 60, pct: 0.1,
    input_cost_inr: null, cached_input_cost_inr: null, output_cost_inr: null,
    total_cost_inr: null, total_cost_usd: null, priced: false, unpriced_tokens: 60 },
]};

const PROMPTS = { total: 2, limit: 25, offset: 0, page: 1, pages: 1, rows: [
  { id: "a1", timestamp: "2026-09-09T10:00:00+05:30", project: "datalake_llm",
    git_branch: "main", model: "claude-opus-5", prompt: "hello world",
    input_tokens: 12, output_tokens: 340, cache_tokens: 51234, total_tokens: 51586,
    total_cost_inr: 30.21, priced: true },
  { id: "a2", timestamp: "2026-09-09T09:00:00+05:30", project: "unpriced_proj",
    git_branch: "main", model: null, prompt: "interrupted",
    input_tokens: null, output_tokens: null, cache_tokens: null, total_tokens: null,
    total_cost_inr: null, priced: false },
]};

const USAGE = { rows: [
  { date: "2026-09-09", prompts: 2, input_tokens: 100, output_tokens: 200,
    cache_tokens: 300, total_tokens: 600, total_cost_inr: 12.34, total_cost_usd: 0.14 },
]};

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
  if (p.startsWith("/api/projects")) return PROJECTS;
  if (p.startsWith("/api/usage")) return USAGE;
  if (p.startsWith("/api/prompts")) return PROMPTS;
  if (p.startsWith("/api/filters")) {
    return { projects: ["datalake_llm"], models: ["claude-opus-5"], years: [2026] };
  }
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
function dump(label, e) {
  const text = e && e.stack ? e.stack.split(String.fromCharCode(10)).slice(0, 6).join(String.fromCharCode(10)) : String(e);
  console.log(String.fromCharCode(10) + "!!! " + label + ": " + text);
}
process.on("unhandledRejection", (e) => dump("UNHANDLED REJECTION", e));
process.on("uncaughtException", (e) => dump("UNCAUGHT", e));
eval(js);

setTimeout(() => {
  const read = (id) => (els[id] ? els[id].textContent : "<never rendered>");
  console.log("  BANNER:", els["banner"] ? (els["banner"].textContent || "(empty)") : "(no banner el)");
  console.log("  c-prompts rendered:", els["c-prompts"] ? els["c-prompts"].textContent : "<never>");
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
  // ---------------------------------------------------------------- tables
  console.log("\n=== project summary table ===");
  const pHtml = tableBodies.project ? tableBodies.project.innerHTML : "";
  const pCells = (pHtml.match(/<td[^>]*cost-col/g) || []).length;
  const pOk = pCells === 8;  // 2 rows x 4 cost cells
  if (!pOk) bad++;
  console.log("  " + (pOk ? "[OK]    " : "[FAIL]  ") +
              "cost cells rendered: " + pCells + " (expected 8 = 2 rows x 4)");
  const pricedShown = pHtml.includes("30,639") || pHtml.includes("₹30.6K") ||
                      /₹3[0-9.]*K|₹30,639|₹3\.1L/.test(pHtml);
  if (!pricedShown) bad++;
  console.log("  " + (pricedShown ? "[OK]    " : "[FAIL]  ") +
              "priced project shows a rupee figure");
  const unpricedDash = (pHtml.match(/—/g) || []).length >= 4;
  if (!unpricedDash) bad++;
  console.log("  " + (unpricedDash ? "[OK]    " : "[FAIL]  ") +
              "unpriced project shows em dashes, not zeros");
  const noZeroCost = !/cost-col[^>]*>₹0\.00</.test(pHtml);
  if (!noZeroCost) bad++;
  console.log("  " + (noZeroCost ? "[OK]    " : "[FAIL]  ") +
              "unpriced project is not rendered as ₹0.00");

  console.log("\n=== prompt history table ===");
  const hHtml = tableBodies.prompt ? tableBodies.prompt.innerHTML : "";
  const hCells = (hHtml.match(/<td[^>]*cost-col/g) || []).length;
  const hOk = hCells === 2;  // 1 cost cell per row
  if (!hOk) bad++;
  console.log("  " + (hOk ? "[OK]    " : "[FAIL]  ") +
              "cost cells rendered: " + hCells + " (expected 2 = 1 per row)");
  const hPriced = /₹30\.21|₹30/.test(hHtml);
  if (!hPriced) bad++;
  console.log("  " + (hPriced ? "[OK]    " : "[FAIL]  ") + "priced interaction shows its cost");
  const hDash = hHtml.includes("—");
  if (!hDash) bad++;
  console.log("  " + (hDash ? "[OK]    " : "[FAIL]  ") + "unpriced interaction shows an em dash");

  // Header and body must agree, or the table visually skews.
  const html = fs.readFileSync(
    path.join(__dirname, "..", "..", "dashboard", "index.html"), "utf8");
  function headerCount(id) {
    return (html.split('id="' + id + '"')[1].split("</thead>")[0].match(/<th[ >]/g) || []).length;
  }
  console.log("\n=== header / body alignment ===");
  const pRowCells = (pHtml.split("</tr>")[0].match(/<td[ >]/g) || []).length;
  const hRowCells = (hHtml.split("</tr>")[0].match(/<td[ >]/g) || []).length;
  const pAlign = pRowCells === headerCount("project-table");
  const hAlign = hRowCells === headerCount("prompt-table");
  if (!pAlign) bad++;
  if (!hAlign) bad++;
  console.log("  " + (pAlign ? "[OK]    " : "[FAIL]  ") + "project row " + pRowCells +
              " cells vs " + headerCount("project-table") + " headers");
  console.log("  " + (hAlign ? "[OK]    " : "[FAIL]  ") + "history row " + hRowCells +
              " cells vs " + headerCount("prompt-table") + " headers");

  console.log(bad ? "\nFAILURES: " + bad : "\nALL COST SURFACES RENDER CORRECTLY");
  process.exit(bad ? 1 : 0);
}, 200);
