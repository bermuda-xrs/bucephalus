const $ = s => document.querySelector(s);
// The token arrives in the URL FRAGMENT from whoever launched the console
// (the desktop app, or the link `alpha serve` prints) - never in this page,
// which any local process can fetch (AUDIT-2026-09 F2). Kept for this tab
// only, and taken out of the address bar so it is not in a screenshot.
const TOKEN = (() => {
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("token");
  try {
    if (fromHash) { sessionStorage.setItem("alpha-token", fromHash);
                    history.replaceState(null, "", location.pathname); }
    return fromHash || sessionStorage.getItem("alpha-token") || "";
  } catch { return fromHash || ""; }
})();
// A token pasted into an open page's address bar is a same-page navigation,
// not a load: start over so the session picks it up.
window.addEventListener("hashchange", () => {
  if (new URLSearchParams(location.hash.slice(1)).get("token")) location.reload();
});
let CURRENT = null, POLL = null, SUITE = null;

function toast(msg, bad) {
  const t = $("#toast"); t.textContent = msg; t.className = bad ? "bad" : "";
  t.style.opacity = "1"; clearTimeout(t._h);
  t._h = setTimeout(() => t.style.opacity = "0", bad ? 7000 : 2800);
}
async function api(path, opts) {
  const r = await fetch(path, {...opts, headers: {"Content-Type": "application/json",
    "Authorization": "Bearer " + TOKEN, ...((opts && opts.headers) || {})}});
  const txt = await r.text();
  let data; try { data = txt ? JSON.parse(txt) : {}; } catch { data = {raw: txt}; }
  if (!r.ok) { const e = new Error(data.detail || data.raw || r.statusText); e.status = r.status; throw e; }
  return data;
}
const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const firstLine = s => String(s || "").split("\n")[0];
const secs = n => n >= 90 ? `${Math.round(n / 60)} min` : `${Math.round(n)} s`;
const ago = t => { const d = Date.now() / 1000 - t;
  return d < 90 ? "just now" : d < 5400 ? `${Math.round(d / 60)} min ago`
       : d < 129600 ? `${Math.round(d / 3600)} h ago` : `${Math.round(d / 86400)} days ago`; };

// ------------------------------------------------------------- theme
async function applyTheme() {
  try {
    const t = await api("/theme");
    const map = {accent:"--accent", success:"--success", warning:"--warning",
                 danger:"--danger", muted:"--muted", background:"--ground"};
    for (const [k, v] of Object.entries(t.colors || {}))
      if (map[k] && /^#[0-9a-f]{3,8}$/i.test(v)) document.documentElement.style.setProperty(map[k], v);
    $("#banner").textContent = t.banner;
  } catch { /* the first-paint values stand */ }
}

// ------------------------------------------------------------- views
function show(which) {
  $("#desk").hidden = which !== "desk"; $("#shop").hidden = which !== "shop";
  $("#navDesk").setAttribute("aria-current", which === "desk" ? "page" : "false");
  $("#navShop").setAttribute("aria-current", which === "shop" ? "page" : "false");
  if (which === "shop") loadSuites();
}
$("#navDesk").onclick = () => show("desk");
$("#navShop").onclick = () => show("shop");

// ------------------------------------------------------------- integrity
async function loadAlerts() {
  try {
    const a = await api("/alerts");
    const crit = (a.integrity || []).filter(f => f.severity === "critical");
    const box = $("#integrity");
    box.hidden = !crit.length;
    box.innerHTML = crit.length ? `<strong>The record does not verify.</strong>
      Something in the hash chain was changed after it was written. Do not approve
      anything until this is explained.
      <ul>${crit.map(f => `<li>${esc(f.detail)}${f.task_id ? ` (task <code>${esc(f.task_id)}</code>)` : ""}</li>`).join("")}</ul>` : "";
  } catch { /* health shows reachability */ }
}

// ------------------------------------------------------------- queue
const NEEDS_PLAN = t => t.status === "pending" && t.plan_status === "draft";
async function loadQueue() {
  let tasks = [], gates = [];
  try { [tasks, gates] = await Promise.all([
          api("/tasks").then(d => d.tasks), api("/approvals").then(d => d.pending)]); }
  catch (e) { return; }
  const needs = [], running = [], done = [];
  for (const g of gates) needs.push(`<button class="qi needs gate" data-gate="${esc(g.id)}"
      data-task="${esc(g.task_id)}"${CURRENT && CURRENT.gate === g.id ? ' aria-current="true"' : ""}><div class="g">Wants to use <code>${esc(g.tool)}</code></div>
      <div class="s">Parked for your decision</div></button>`);
  for (const t of tasks) {
    const cur = CURRENT && CURRENT.task === t.task_id ? ' aria-current="true"' : "";
    const row = (cls, state) => `<button class="qi ${cls}" data-task="${esc(t.task_id)}"${cur}>
      <div class="g">${esc(firstLine(t.goal))}</div><div class="s">${state}</div></button>`;
    if (NEEDS_PLAN(t)) needs.push(row("needs", "Plan ready to review"));
    else if (t.status === "pending" && !t.plan_status) needs.push(row("needs", "No plan yet"));
    else if (["running", "paused"].includes(t.status)) running.push(row("", t.status === "paused" ? "Paused" : "Running"));
    else done.push(row("", `${statusWord(t.status)}, ${ago(t.created_at)}`));
  }
  const fill = (id, n, items, none) => { $(id).innerHTML = items.join("") || `<div class="empty">${none}</div>`;
                                         $(n).textContent = items.length || ""; };
  fill("#qNeeds", "#nNeeds", needs, "Nothing is waiting on you.");
  fill("#qRunning", "#nRunning", running, "Nothing running.");
  fill("#qDone", "#nDone", done.slice(0, 30), "Nothing yet.");
}
function statusWord(s) {
  return ({done:"Done", done_unannounced:"Done, not announced", failed:"Failed",
    budget_exhausted:"Out of budget", max_steps:"Out of steps", plateau:"Stalled",
    paused:"Paused", rejected:"Sent back"}[s]) || s;
}
document.querySelector(".queue").addEventListener("click", e => {
  const b = e.target.closest(".qi"); if (!b) return;
  b.dataset.gate ? openGate(b.dataset.gate) : openTask(b.dataset.task);
});

// ------------------------------------------------------------- new task
$("#newTask").addEventListener("submit", async e => {
  e.preventDefault();
  const goal = $("#goal").value.trim(); if (!goal) return;
  try {
    const t = await api("/tasks", {method:"POST", body:JSON.stringify({goal,
      artifact_scope:[$("#scope").value.trim() || "work"], budget_usd:0})});
    $("#goal").value = "";
    await loadQueue();
    await draft(t.task_id);
  } catch (err) { toast(err.message, true); }
});
async function draft(taskId) {
  CURRENT = {task: taskId};
  $("#sheetInner").innerHTML = `<p class="kicker">Drafting a plan</p>
    <p class="objective">Reading what it is allowed to read, and working out the steps.</p>
    <p class="health">A local model takes a minute or more. Nothing is changed while it plans.</p>`;
  try { await api(`/tasks/${taskId}/plan`, {method:"POST"}); await openTask(taskId); }
  catch (err) { toast(err.message, true); openTask(taskId); }
  loadQueue();
}

// ------------------------------------------------------------- the plan sheet
async function openTask(taskId) {
  clearInterval(POLL); CURRENT = {task: taskId}; loadQueue(); show("desk");
  const t = await api("/tasks/" + taskId);
  let p = null;
  try { p = await api(`/tasks/${taskId}/plan`); } catch (e) { if (e.status !== 404) toast(e.message, true); }
  if (t.status === "pending" && p && p.status === "draft") return renderPlan(t, p);
  if (t.status === "pending" && !p) return renderNoPlan(t);
  renderRun(t, p);
}

function renderNoPlan(t) {
  $("#sheetInner").innerHTML = `<p class="kicker">No plan yet</p>
    <h1 class="objective">${esc(t.goal)}</h1>
    <button class="btn primary" data-action="draft" data-args='${esc(JSON.stringify([t.task_id]))}'>Draft a plan</button>`;
}

function renderPlan(t, p) {
  CURRENT = {task: t.task_id, plan: p.plan_id};
  const pl = p.plan;
  const list = (items, empty) => items && items.length
    ? `<ul>${items.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : `<ul class="clear"><li>${empty}</li></ul>`;
  const asks = (pl.open_questions || []).length + (pl.human_approvals_expected || []).length + (pl.unknowns || []).length;
  const steps = (pl.steps || []).map((s, i) => `
    <li class="step${s.reversible ? "" : " irrev"}">
      <div class="node" aria-hidden="true">${i + 1}</div>
      <div>
        <p class="intent">${esc(s.intent)}</p>
        ${s.reversible ? "" : `<p class="irrev-note">Can't be undone once it runs.</p>`}
        <dl>
          <dt>Uses</dt><dd>${(s.tools || []).map(x => `<code>${esc(x)}</code>`).join(", ") || "no tools"}${s.action_mode === "code" ? ", by writing code" : ""}</dd>
          <dt>May touch</dt><dd>${(s.artifact_scope || []).map(x => `<code>${esc(x)}</code>`).join(", ") || "no files"}</dd>
          <dt>Checked by</dt><dd><code>${esc(s.success_criterion)}</code></dd>
          ${(s.depends_on || []).length ? `<dt>After</dt><dd>${s.depends_on.map(esc).join(", ")}</dd>` : ""}
        </dl>
      </div>
      <div class="est">${secs(s.est_seconds || 0)}<br>${(s.est_tokens || 0).toLocaleString()} tokens</div>
    </li>`).join("");
  const hash = p.plan_id.replace(/(.{4})/g, "$1 ").trim();
  $("#sheetInner").innerHTML = `
    <p class="kicker">Plan for your approval</p>
    <h1 class="objective">${esc(pl.objective || t.goal)}</h1>
    <section class="before" aria-label="Before you approve">
      <h3>${asks ? "Settle these before you sign" : "Nothing to settle before you sign"}</h3>
      <div class="health">Questions it has</div>${list(pl.open_questions, "None.")}
      <div class="health">Where it expects to stop and ask you</div>${list(pl.human_approvals_expected, "Nowhere. It should run to the end unattended.")}
      <div class="health">What it doesn't know yet</div>${list(pl.unknowns, "Nothing declared.")}
    </section>
    <ol class="track" aria-label="Steps">${steps || '<li class="empty">The plan has no steps.</li>'}</ol>
    <p class="totals">${(pl.steps || []).length} steps, about ${secs(pl.total_est_seconds || 0)} in total.</p>
    <div class="sign">
      <div class="seal" id="seal">
        <div class="ring" aria-hidden="true"></div>
        <div><div class="what">You are signing plan</div><div class="hash" title="${esc(p.plan_id)}">${esc(hash.slice(0, 39))}…</div></div>
      </div>
      <div class="actions">
        <button class="btn" id="btnBack">Send back</button>
        <button class="btn primary" id="btnSign">Approve and run</button>
      </div>
      <div class="sendback" id="sendback" hidden>
        <input id="backWhy" placeholder="What should change? This goes on the record with the plan.">
        <button class="btn" id="btnBackGo">Send back</button>
      </div>
    </div>`;
  $("#btnSign").onclick = () => approve(t.task_id, p.plan_id);
  $("#btnBack").onclick = () => { $("#sendback").hidden = false; $("#backWhy").focus(); };
  $("#btnBackGo").onclick = () => sendBack(t.task_id, p.plan_id);
}

async function approve(taskId, planId) {
  const b = $("#btnSign"); b.disabled = true;
  try {
    await api(`/tasks/${taskId}/plan/approve`, {method:"POST", body:JSON.stringify({plan_id: planId, edits: []})});
    $("#seal").classList.add("signed");
    $("#seal .what").textContent = "Signed and pinned to the record";
    await api(`/tasks/${taskId}/run`, {method:"POST"});
    toast("Approved. It's running.");
    setTimeout(() => openTask(taskId), 700);
  } catch (e) { b.disabled = false; toast(e.message, true); }
}
async function sendBack(taskId, planId) {
  try {
    await api(`/tasks/${taskId}/plan/reject`, {method:"POST",
      body:JSON.stringify({plan_id: planId, comment: $("#backWhy").value})});
    toast("Sent back. Draft again when you're ready.");
    openTask(taskId);
  } catch (e) { toast(e.message, true); }
}

// ------------------------------------------------------------- a run
function renderRun(t, p) {
  const live = ["running", "pending"].includes(t.status);
  const cls = t.status.startsWith("done") ? "st-done" : t.status === "paused" ? "st-needs" : "";
  $("#sheetInner").innerHTML = `
    <p class="kicker">${live ? "Running" : "Finished"}</p>
    <h1 class="objective">${esc(t.goal)}</h1>
    <dl class="progress">
      <dt>Status</dt><dd class="${cls}">${esc(statusWord(t.status))}</dd>
      <dt>Model calls</dt><dd>${t.current_step}</dd>
      <dt>Plan</dt><dd>${p ? `<code>${esc(p.plan_id.slice(0, 16))}…</code>, ${esc(p.status)}` : "none"}</dd>
      <dt>Budget</dt><dd>${secs(t.budget.seconds || 0)}</dd>
    </dl>
    <div id="runMore"></div>`;
  if (t.status === "paused")
    $("#runMore").innerHTML = `<p class="caveat">It stopped to ask you something. The decision is under <b>Needs you</b>.</p>`;
  if (!live) api("/metrics/" + t.task_id).then(m => {
    $("#runMore").innerHTML += `<dl class="progress"><dt>Tokens</dt><dd>${(m.free_tokens + m.paid_tokens).toLocaleString()} (${m.paid_tokens.toLocaleString()} paid)</dd>
      <dt>Cost</dt><dd>$${Number(m.cost_usd).toFixed(4)}</dd></dl>`;
  }).catch(() => {});
  if (live) POLL = setInterval(async () => {
    const n = await api("/tasks/" + t.task_id).catch(() => null);
    if (n && (n.status !== t.status || n.current_step !== t.current_step)) { clearInterval(POLL); renderRun(n, p); loadQueue(); }
  }, 2500);
}

// ------------------------------------------------------------- a gate approval
async function openGate(id) {
  clearInterval(POLL); show("desk");
  const g = (await api("/approvals")).pending.find(x => x.id === id);
  if (!g) { toast("Already decided."); return loadQueue(); }
  CURRENT = {gate: id}; loadQueue();
  $("#sheetInner").innerHTML = `
    <p class="kicker">Parked for your decision</p>
    <h1 class="objective">It wants to use <code>${esc(g.tool)}</code>.</h1>
    <div class="gatecard">
      <p>${esc(g.reason)}</p>
      <pre>${esc(g.preview)}</pre>
      <div class="row">
        <button class="btn amber" id="gAllow">Allow this once</button>
        <button class="btn" id="gDeny">Refuse</button>
        <button class="quiet" data-action="openTask" data-args='${esc(JSON.stringify([g.task_id]))}'>See the task</button>
      </div>
    </div>`;
  const decide = async d => {
    try { await api("/approvals/" + id, {method:"POST", body:JSON.stringify({decision: d})});
          toast(d === "approve" ? "Allowed. It carries on." : "Refused. It will be told."); openTask(g.task_id); }
    catch (e) { toast(e.message, true); }
  };
  $("#gAllow").onclick = () => decide("approve");
  $("#gDeny").onclick = () => decide("deny");
}

// ============================================================= workshop
document.querySelector(".ws-tabs").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  document.querySelectorAll(".ws-tabs button").forEach(x => x.setAttribute("aria-selected", String(x === b)));
  document.querySelectorAll(".workshop section").forEach(s => s.hidden = s.id !== "t-" + b.dataset.t);
  ({suites: loadSuites, skills: loadSkills, policy: loadPolicy, grounding: loadGrounding}[b.dataset.t])();
});

async function loadSuites() {
  const d = await api("/suites");
  $("#minTrials").textContent = d.min_paired_trials;
  $("#suiteRows").innerHTML = d.suites.length ? d.suites.map(s => {
    const pct = Math.min(100, Math.round(100 * s.n_tasks / d.min_paired_trials));
    return `<tr><td><button class="link" data-action="openSuite" data-args='${esc(JSON.stringify([s.id]))}'>${esc(s.name)}</button>
        <div class="health">${esc(s.description)}</div></td>
      <td>${s.n_tasks}<div class="meter"><i data-pct="${pct}"></i></div></td>
      <td>${s.ready ? "Ready" : `${d.min_paired_trials - s.n_tasks} more to go`}</td>
      <td class="r"><button class="quiet" data-action="delSuite" data-args='${esc(JSON.stringify([s.id, s.name]))}'>Delete</button></td></tr>`;
  }).join("") : `<tr><td colspan="4" class="empty">No suites yet. Name one above to start.</td></tr>`;
}
async function createSuite() {
  const name = $("#sName").value.trim();
  if (!name) return toast("Give the suite a name first.", true);
  try { const s = await api("/suites", {method:"POST", body:JSON.stringify({name, description:$("#sDesc").value})});
        $("#sName").value = ""; $("#sDesc").value = ""; toast("Suite created."); await loadSuites(); openSuite(s.id); }
  catch (e) { toast(e.message, true); }
}
async function delSuite(id, name) {
  if (!confirm(`Delete the suite "${name}" and all its tasks?`)) return;
  await api("/suites/" + id, {method:"DELETE"});
  if (SUITE && SUITE.id === id) closeSuite();
  toast("Suite deleted."); loadSuites();
}
async function openSuite(id) {
  SUITE = await api("/suites/" + id);
  $("#tCrit").innerHTML = SUITE.verifiers.filter(v => v !== "always_pass")
    .map(v => `<option ${v === "expected_value" ? "selected" : ""}>${esc(v)}</option>`).join("");
  $("#sdTitle").textContent = `${SUITE.name}: ${SUITE.n_tasks} of ${SUITE.min_paired_trials} tasks`;
  $("#suiteDetail").hidden = false; critChanged(); renderTasks();
}
function closeSuite() { SUITE = null; $("#suiteDetail").hidden = true; }
function critChanged() {
  const c = $("#tCrit").value;
  $("#tExpLabel").textContent = c === "contains_all" ? "Strings that must appear (JSON list)" : "Expected answer (JSON)";
  if (c === "contains_all" && $("#tExp").value.trim().startsWith("{")) $("#tExp").value = '["compressor", "filter"]';
}
// Which checks look at the answer at all. `execution` refuses without a
// script and `json_schema` checks shape only (DR7), so neither counts.
function checksCorrectness(t) {
  const s = t.output_schema || {};
  return !!(s["x-expected"] || s["x-contains"] || ["verify_program", "pytest"].includes(t.success_criterion));
}
function renderTasks() {
  const shapeOnly = SUITE.tasks.filter(t => !checksCorrectness(t));
  $("#shapeWarn").innerHTML = !SUITE.tasks.length ? "" : shapeOnly.length
    ? `<p class="caveat"><b>${shapeOnly.length} of ${SUITE.tasks.length} tasks can't catch a wrong answer:</b>
       ${shapeOnly.map(t => `<code>${esc(t.key)}</code>`).join(", ")}. A suite of these reports a pass rate that means nothing.</p>`
    : `<p class="caveat good">All ${SUITE.tasks.length} tasks check the answer, not only its shape.</p>`;
  $("#taskRows").innerHTML = SUITE.tasks.length ? SUITE.tasks.map(t => {
    const s = t.output_schema || {};
    const right = s["x-expected"] ? `<code>${esc(JSON.stringify(s["x-expected"]))}</code>`
      : s["x-contains"] ? `<code>${esc(JSON.stringify(s["x-contains"]))}</code>` : "Shape only";
    return `<tr><td><code>${esc(t.key)}</code>${t.catastrophic ? " (veto)" : ""}</td>
      <td>${esc(t.goal).slice(0, 90)}</td><td><code>${esc(t.success_criterion)}</code></td><td>${right}</td>
      <td class="r"><button class="quiet" data-action="delTask" data-args='${esc(JSON.stringify([t.id]))}'>Delete</button></td></tr>`;
  }).join("") : `<tr><td colspan="5" class="empty">No tasks yet.</td></tr>`;
}
function parseJSON(text, field) {
  const t = (text || "").trim(); if (!t) return {};
  try { return JSON.parse(t); } catch (e) { throw new Error(`${field} isn't valid JSON: ${e.message}`); }
}
async function saveTask() {
  if (!SUITE) return;
  try {
    const crit = $("#tCrit").value;
    const schema = parseJSON($("#tSchema").value, "Answer shape");
    const expected = parseJSON($("#tExp").value, "Expected answer");
    if (crit === "expected_value") schema["x-expected"] = expected;
    if (crit === "contains_all") schema["x-contains"] = expected;
    await api(`/suites/${SUITE.id}/tasks`, {method:"POST", body:JSON.stringify({
      key:$("#tKey").value.trim(), goal:$("#tGoal").value.trim(), success_criterion:crit,
      output_schema:schema, fixtures:parseJSON($("#tFix").value, "Input files"),
      budget_seconds:Number($("#tSecs").value) || null, catastrophic:$("#tCat").value === "1"})});
    $("#tKey").value = ""; $("#tGoal").value = ""; toast("Task saved.");
    await openSuite(SUITE.id); loadSuites();
  } catch (e) { toast(e.message, true); }
}
async function delTask(id) {
  await api(`/suites/${SUITE.id}/tasks/${id}`, {method:"DELETE"});
  toast("Task deleted."); await openSuite(SUITE.id); loadSuites();
}

async function loadSkills() {
  const d = await api("/skills");
  $("#skillRows").innerHTML = d.skills.length ? d.skills.map(s => `<tr>
      <td><code>${esc(s.name)}</code></td><td>${s.version}</td>
      <td class="${s.status === "promoted" ? "st-done" : ""}">${esc(s.status)}</td>
      <td class="r"><button class="quiet" data-action="checkGate" data-args='${esc(JSON.stringify([s.id]))}'>Check the gate</button></td>
    </tr>`).join("") : `<tr><td colspan="4" class="empty">No skills yet.</td></tr>`;
}
async function addSkill() {
  try { await api("/skills", {method:"POST", body:JSON.stringify({name:$("#kName").value.trim(),
          body:$("#kBody").value, applies_when:$("#kWhen").value.trim()})});
        toast("Added as a candidate."); $("#kName").value = ""; loadSkills(); }
  catch (e) { toast(e.message, true); }
}
async function checkGate(id) {
  try { const d = await api("/gate/" + id); toast(`${d.verdict}: ${d.reason}`, d.verdict !== "promote"); }
  catch (e) { toast(e.message, true); }
}

async function loadPolicy() {
  const d = await api("/policy");
  $("#pBody").value = d.body; $("#pSrc").textContent = "In effect from " + d.effective_source;
}
async function savePolicy() {
  try { await api("/policy", {method:"PUT", body:JSON.stringify({body:$("#pBody").value})});
        toast("Policy saved. It applies from the next review."); loadPolicy(); }
  catch (e) { toast(e.message, true); }
}

async function loadGrounding() {
  const d = await api("/grounding");
  $("#gRows").innerHTML = d.documents.length ? d.documents.map(g => `<tr>
      <td><button class="link" data-action="editGrounding" data-args='${esc(JSON.stringify([g.name]))}'>${esc(g.name)}</button></td>
      <td>${g.bytes.toLocaleString()} bytes</td>
      <td class="r"><button class="quiet" data-action="delGrounding" data-args='${esc(JSON.stringify([g.name]))}'>Delete</button></td></tr>`).join("")
    : `<tr><td colspan="3" class="empty">No notes yet.</td></tr>`;
}
async function editGrounding(name) {
  const d = await api("/grounding/" + encodeURIComponent(name));
  $("#gName").value = d.name; $("#gBody").value = d.body;
}
async function saveGrounding() {
  const name = $("#gName").value.trim();
  if (!name) return toast("Give the note a name first.", true);
  try { await api("/grounding/" + encodeURIComponent(name), {method:"PUT", body:JSON.stringify({body:$("#gBody").value})});
        toast("Note saved."); loadGrounding(); }
  catch (e) { toast(e.message, true); }
}
async function delGrounding(name) {
  await api("/grounding/" + encodeURIComponent(name), {method:"DELETE"});
  toast("Note deleted."); loadGrounding();
}

// ------------------------------------------------------------- start
(async () => {
  applyTheme();
  if (!TOKEN) {
    $("#health").textContent = "No session. Open the console from the desktop app, or the link alpha serve prints.";
    return;
  }
  try {
    const h = await api("/health");
    $("#health").textContent = `Models: ${h.backends.join(", ")}`;
  } catch { $("#health").textContent = "Can't reach the control plane"; }
  loadAlerts(); loadQueue();
  setInterval(() => { loadAlerts(); loadQueue(); }, 8000);
})();

// Every button names its action; this is the whole list (iteration 3, 1.5).
// A map, not window[name]: nothing outside it can be dispatched.
const ACTIONS = {createSuite, saveTask, closeSuite, addSkill, savePolicy, saveGrounding,
                 draft, openTask, openSuite, delSuite, delTask, checkGate,
                 editGrounding, delGrounding};
document.addEventListener("click", e => {
  const el = e.target.closest("[data-action]");
  if (!el || !ACTIONS[el.dataset.action]) return;
  ACTIONS[el.dataset.action](...JSON.parse(el.dataset.args || "[]"));
});
document.addEventListener("change", e => {
  if (e.target.dataset.change === "critChanged") critChanged();
});
// Meter widths: set through the CSSOM, which the CSP allows, not a style attribute.
new MutationObserver(() => document.querySelectorAll("i[data-pct]").forEach(i => {
  i.style.width = i.dataset.pct + "%";
})).observe(document.body, {childList: true, subtree: true});
