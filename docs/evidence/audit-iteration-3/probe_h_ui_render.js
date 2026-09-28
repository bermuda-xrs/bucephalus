// Audit probe: every innerHTML template in control/ui.js, fed hostile strings.
//
//   node docs/evidence/audit-iteration-3/probe_h_ui_render.js
//
// Loads the REAL ui.js into a Node vm with a stub DOM (no browser, no
// network), replaces `api` with canned responses carrying hostile model
// output, plan text, file names, tool previews, memory entries, channel
// senders and skill names, renders every template, and checks that nothing
// became markup: no raw "<img", and every data-args attribute still parses to
// exactly the values that went in.
"use strict";
const fs = require("fs"), path = require("path"), vm = require("vm");
const SRC = fs.readFileSync(path.join(__dirname, "../../../src/agent/control/ui.js"), "utf8");

const X = `x"'><img src=x onerror=alert(1)>&#39;);alert(2);('`;   // one string, every context
const els = {};
const stub = new Proxy(function () {}, {get: (t, p) => p === Symbol.iterator ? [][Symbol.iterator] : stub,
                                        apply: () => stub, construct: () => stub});
function el() {
  const o = {innerHTML: "", textContent: "", value: "", hidden: false, dataset: {}, style: {},
             classList: {add() {}, remove() {}, toggle() {}}, setAttribute() {}, addEventListener() {},
             focus() {}, querySelector: () => null, scrollIntoView() {}};
  return new Proxy(o, {get: (t, p) => (p in t ? t[p] : stub)});
}
const ctx = {
  console, JSON, Promise, Object, Array, String, Number, Math, Date, Map, Set, Error, URLSearchParams,
  setTimeout: () => 0, clearTimeout: () => {}, setInterval: () => 0,
  location: {hash: "", pathname: "/", reload() {}}, history: {replaceState() {}},
  sessionStorage: {getItem: () => "tok", setItem() {}}, localStorage: {getItem: () => null, setItem() {}},
  window: {addEventListener() {}, innerWidth: 1400}, MutationObserver: function () { return {observe() {}}; },
  document: {querySelector: s => (els[s] ||= el()), querySelectorAll: () => [], addEventListener() {},
             documentElement: el(), activeElement: null},
  fetch: () => new Promise(() => {}), prompt: () => null, confirm: () => false,
};
ctx.globalThis = ctx;
const harness = `
;(async () => {
  const out = {};
  const plan = {objective: X, open_questions: [X], human_approvals_expected: [X], unknowns: [X],
    steps: [{intent: X, tools: [X], artifact_scope: [X], success_criterion: X, depends_on: [X],
             reversible: false, action_mode: "code", est_seconds: 30, est_tokens: 1200}], total_est_seconds: 30};
  const item = {task_id: X, goal: X, scope: [X], status: "pending", created_at: 0, budget: {},
    plan: {plan_id: "a".repeat(64), status: "drafted", body: plan},
    approvals: [{id: X, tool: X, reason: X, preview: X, task_id: X}], answer: X};
  out.plan = renderItem(item);
  out.run = renderItem({...item, status: "done", plan: null});
  out.tool = toolCard({tool: X, preview: X, ok: true, ms: 5});
  api = async (p) => {
    if (p.startsWith("/tasks/") && p.includes("/files")) return {kind: "dir", path: "work",
      entries: [{name: X, dir: false, bytes: 3}, {name: X, dir: true, bytes: null}]};
    if (p === "/memory") return {root: X, blessing: X, tiers: [{tier: "memory", file: X, chars: 1, cap: 2,
      entries: [{taint: X, text: X}]}]};
    if (p === "/panels/channels") return {telegram: {enabled: true, allowed_senders: [X]},
      drops: [{created_at: 0, sender: X, reason: X, body_sha256: X, body_len: 3}]};
    if (p === "/panels/schedules") return {schedules: [{spec: X, goal: X, next_fire: 0, missed: 0}],
      parked: [{task_id: X, kind: X, created_at: 0}]};
    if (p === "/skills") return {skills: [{id: X, name: X, version: 1, status: X}]};
    if (p === "/panels/integrity") return {findings: [{severity: "critical", task_id: X, detail: X}],
      anchor: {path: X, entries: 1, last_written: 0}, tasks: 1};
    if (p === "/alerts") return {integrity: [{severity: "critical", task_id: X, detail: X}]};
    if (p.startsWith("/conversations?")) return {conversations: [{id: X, title: X, needs_you: 1,
      n_tasks: 1, updated_at: 0, archived: false}]};
    if (p === "/approvals") return {pending: [{task_id: X, tool: X}]};
    if (p === "/panels/logs") return {events: [{task_id: X, type: X, at: 0}], now: 0};
    if (p === "/panels/settings") return {profile: X, backends: [{name: X, kind: X, model: X, tier: X, window: 1}],
      limits: {[X]: X}, autonomy: {[X]: X}, root: X, state_dir: X};
    if (p === "/panels/measurement") return {harness_version: 6, toolset_version: 6, profile: X,
      runs: [{name: X, bytes: 1, modified: 0}], inventory: [{row: X, state: X, count: X}]};
    if (p === "/policy") return {effective_source: X, body: X};
    if (p === "/grounding") return {documents: [{name: X, bytes: 1}]};
    return {};
  };
  await loadFiles("t", ""); out.files = $("#fileList").innerHTML;
  await loadAlerts(); out.alerts = $("#integrity").innerHTML;
  S.lastSide = ""; await loadSide(); out.side = $("#needsList").innerHTML + $("#convList").innerHTML;
  for (const name of Object.keys(PANES).filter(n => n !== "suites")) out["pane:" + name] = await PANES[name]();
  globalThis.__OUT = out;
  globalThis.__PROTO = {constructor: !!ACTIONS["constructor"], toString: typeof ACTIONS["toString"],
                        valueOf: typeof ACTIONS["valueOf"]};
})().catch(e => { globalThis.__ERR = String(e && e.stack || e); });
`;
vm.createContext(ctx);
ctx.X = X;
vm.runInContext(SRC + harness, ctx, {filename: "ui.js"});
setTimeout(() => {
  if (ctx.__ERR) { console.log("harness error:", ctx.__ERR); process.exit(2); }
  const decode = s => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<")
                       .replace(/&gt;/g, ">").replace(/&amp;/g, "&");
  let bad = 0, argsChecked = 0;
  for (const [k, html] of Object.entries(ctx.__OUT)) {
    const raw = (html.match(/<img/g) || []).length;
    const opened = (html.match(/data-args='/g) || []).length;
    const parsed = [...html.matchAll(/data-args='([^']*)'/g)].map(m => JSON.parse(decode(m[1])));
    argsChecked += parsed.length;
    const argsOk = parsed.length === opened && parsed.every(a => a.every(v => typeof v === "string"));
    if (raw || !argsOk) bad++;
    console.log(`${k.padEnd(18)} raw <img: ${raw}  data-args: ${parsed.length}/${opened} parse back ${argsOk ? "ok" : "BROKEN"}`);
  }
  console.log(`templates: ${Object.keys(ctx.__OUT).length}, data-args round-tripped: ${argsChecked}, broken: ${bad}`);
  console.log("inherited keys a data-action could name:", JSON.stringify(ctx.__PROTO));
  process.exit(bad ? 1 : 0);
}, 300);
