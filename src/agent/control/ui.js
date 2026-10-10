"use strict";

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

const OPEN = Object.fromEntries(new URLSearchParams(location.hash.slice(1)));
const TOKEN = (() => {
  const fromHash = OPEN.token;
  try {
    if (fromHash) sessionStorage.setItem("alpha-token", fromHash);
    if (location.hash) history.replaceState(null, "", location.pathname);
    return fromHash || sessionStorage.getItem("alpha-token") || "";
  } catch { return fromHash || ""; }
})();
window.addEventListener("hashchange", () => {
  if (new URLSearchParams(location.hash.slice(1)).get("token")) location.reload();
});

let RETURN_FOCUS = null;           // where focus goes back when a dialog closes
const S = {conv: null, thread: null, loose: null, archived: false, streams: new Map(), trails: new Map(),
           drafting: new Set(), answers: new Map(), filesTask: null, filesPath: "", pane: "memory",
           settings: null, lastSide: ""};

function toast(msg, bad) {
  const t = $("#toast"); t.textContent = msg; t.className = "show" + (bad ? " bad" : "");
  clearTimeout(t._h); t._h = setTimeout(() => { t.className = ""; }, bad ? 7000 : 2800);
}
async function api(path, opts) {
  const r = await fetch(path, {...opts, headers: {"Content-Type": "application/json",
    "Authorization": "Bearer " + TOKEN, ...((opts && opts.headers) || {})}});
  const txt = await r.text();
  let data; try { data = txt ? JSON.parse(txt) : {}; } catch { data = {raw: txt}; }
  if (!r.ok) { const e = new Error(data.detail || data.raw || r.statusText); e.status = r.status; throw e; }
  return data;
}
const post = (path, body) => api(path, {method: "POST", body: JSON.stringify(body || {})});
const esc = s => String(s ?? "").replace(/[&<>"']/g, c =>
  ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const args = (...xs) => `data-args='${esc(JSON.stringify(xs))}'`;
const firstLine = s => String(s || "").split("\n")[0];
const secs = n => n >= 90 ? `${Math.round(n / 60)} min` : `${Math.round(n)} s`;
const ktok = n => n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n || 0);
const ago = t => { const d = Date.now() / 1000 - t;
  return d < 90 ? "just now" : d < 5400 ? `${Math.round(d / 60)} min ago`
       : d < 129600 ? `${Math.round(d / 3600)} h ago` : `${Math.round(d / 86400)} days ago`; };
const FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
setInterval(() => { const f = FRAMES[Math.floor(Date.now() / 90) % FRAMES.length];
  $$(".spin").forEach(s => { s.textContent = f; }); }, 90);

function statusWord(s) {
  return ({pending: "Waiting", running: "Running", done: "Done", done_unannounced: "Done, not announced",
    failed: "Failed", budget_exhausted: "Out of budget", max_steps: "Out of steps", plateau: "Stalled",
    paused: "Paused", rejected: "Sent back"}[s]) || s;
}
const TERMINAL = s => !["pending", "running", "paused"].includes(s);

async function applyTheme() {
  try {
    const t = await api("/theme");
    const map = {accent: "--theme-accent", success: "--theme-success", warning: "--theme-warning",
                 background: "--theme-background"};
    for (const [k, v] of Object.entries(t.colors || {}))
      if (map[k] && /^#[0-9a-f]{3,8}$/i.test(v)) document.documentElement.style.setProperty(map[k], v);
    $("#banner").textContent = t.banner || "";
  } catch { /* the first-paint values stand */ }
}
function setTheme(mode) {
  document.documentElement.dataset.theme = mode;
  try { localStorage.setItem("alpha-theme", mode); } catch { /* per-viewer only */ }
}
function toggleTheme() { setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light"); }
try { const m = localStorage.getItem("alpha-theme"); if (m) document.documentElement.dataset.theme = m; } catch { /* dark */ }

async function loadAlerts() {
  try {
    const a = await api("/alerts");
    const crit = (a.integrity || []).filter(f => f.severity === "critical");
    const box = $("#integrity");
    box.hidden = !crit.length;
    box.innerHTML = crit.length ? `<strong>The record does not verify.</strong>
      Something in the hash chain was changed after it was written. Do not approve
      anything until this is explained.
      <ul>${crit.map(f => `<li>${esc(f.detail)}${f.task_id ? ` (task <code>${esc(f.task_id)}</code>)` : ""}</li>`).join("")}</ul>
      <button class="link" data-action="openPane" ${args("integrity")}>Open Integrity</button>` : "";
  } catch { /* health shows reachability */ }
}

async function loadSide() {
  let convs = [], gates = [];
  try {
    [convs, gates] = await Promise.all([
      api(`/conversations?archived=${S.archived}&q=${encodeURIComponent($("#convSearch").value)}`)
        .then(d => d.conversations),
      api("/approvals").then(d => d.pending)]);
  } catch (e) { $("#health").textContent = "Can't reach the control plane"; return; }
  const sig = JSON.stringify([convs, gates, S.conv]);
  if (sig === S.lastSide) return;
  S.lastSide = sig;
  notifyNeeds(gates, convs);
  const needs = [];
  for (const g of gates) needs.push(`<button class="side-item needs-item" data-action="openTaskOf" ${args(g.task_id)}>
      <span class="t">Wants to use <code>${esc(g.tool)}</code></span><span class="s">Parked for your decision</span></button>`);
  for (const c of convs.filter(c => c.needs_you)) needs.push(`<button class="side-item needs-item" data-action="selectConversation" ${args(c.id)}>
      <span class="t">${esc(c.title)}</span><span class="s">${c.needs_you} to sign or decide</span></button>`);
  $("#needsList").innerHTML = needs.join("") || `<div class="empty">Nothing is waiting on you.</div>`;
  $("#nNeeds").textContent = needs.length || "";
  document.title = (needs.length ? `(${needs.length}) ` : "") + "alpha test 1";
  const item = c => `<button class="side-item" data-action="selectConversation" ${args(c.id)}
      ${c.id === S.conv ? 'aria-current="true"' : ""}><span class="t">${c.pinned ? '<span class="pin" aria-label="Pinned">●</span> ' : ""}${esc(c.title)}</span>
      <span class="s">${c.n_tasks} ${c.n_tasks === 1 ? "task" : "tasks"}, ${ago(c.updated_at)}</span></button>`;
  const groups = [];
  for (const c of convs) {
    const g = c.pinned ? "Pinned" : dayGroup(c.updated_at);
    if (!groups.length || groups[groups.length - 1][0] !== g) groups.push([g, []]);
    groups[groups.length - 1][1].push(item(c));
  }
  $("#convList").innerHTML = (groups.map(([g, xs]) => `<h3 class="group">${g}</h3>${xs.join("")}`).join("")
    || `<div class="empty">${S.archived ? "Nothing archived." : "No conversations yet."}</div>`)
    + `<button class="side-item" data-action="openLooseList"><span class="s">Tasks started elsewhere</span></button>`;
  $("#archToggle").textContent = S.archived ? "Show active" : "Show archived";
}
function dayGroup(t) {
  const d = new Date(t * 1000), now = new Date();
  const days = Math.round((new Date(now.toDateString()) - new Date(d.toDateString())) / 86400000);
  return days <= 0 ? "Today" : days === 1 ? "Yesterday" : days < 7 ? "This week" : "Earlier";
}
const SEEN = {needs: null, done: new Set()};
function notifyNeeds(gates, convs) {
  const keys = new Set([...gates.map(g => "g" + g.id), ...convs.filter(c => c.needs_you).map(c => "c" + c.id + ":" + c.needs_you)]);
  const fresh = SEEN.needs ? [...keys].filter(k => !SEEN.needs.has(k)) : [];
  SEEN.needs = keys;
  if (fresh.length) notify("Something needs you", `${fresh.length} new: a plan to sign or a decision to make.`);
}
function notify(title, body) {
  try {
    if (!document.hidden || !("Notification" in window) || Notification.permission !== "granted") return;
    const n = new Notification(title, {body, tag: "alpha"});
    n.onclick = () => { window.focus(); n.close(); };
  } catch { /* the title count still shows it */ }
}
async function enableNotifications() {
  if (!("Notification" in window)) return toast("This browser can't show notifications.", true);
  const p = await Notification.requestPermission();
  toast(p === "granted" ? "You'll be told when something needs you or a task finishes, while this tab is in the background."
                        : "Notifications are off. The tab title still counts what needs you.", p !== "granted");
}
function toggleArchived() { S.archived = !S.archived; S.lastSide = ""; loadSide(); }
let searchTimer = null;
$("#convSearch").addEventListener("input", () => { clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { S.lastSide = ""; loadSide(); }, 200); });

async function newConversation() {
  closeStreams(); S.conv = null; S.thread = null; S.loose = null;
  $("#convTitle").textContent = "New conversation";
  renderWelcome(); S.lastSide = ""; loadSide(); updateRing(null); $("#ask").focus();
  closeSideOnPhone();
}
function renderWelcome() {
  $("#items").innerHTML = `<div class="welcome"><h2>Plan once, walk away, get told.</h2>
    <p>Describe what you want done. It drafts a plan without touching anything; you settle its
    questions and sign it. Only then does it run, and it comes back to you when it needs a decision.</p>
    <p>A follow-up in the same conversation is a new task, with its own plan.</p></div>`;
}
async function selectConversation(id) {
  if (id !== S.conv) closeStreams();
  S.conv = id; S.loose = null; S.lastSide = "";
  await refreshThread(true); loadSide(); closeSideOnPhone();
}
async function refreshThread(scroll) {
  if (!S.conv) return;
  let th;
  try { th = await api("/conversations/" + S.conv); } catch (e) { toast(e.message, true); return; }
  S.thread = th;
  $("#convTitle").textContent = th.conversation.title;
  $("#archBtn").textContent = th.conversation.archived ? "Unarchive" : "Archive";
  $("#pinBtn").textContent = th.conversation.pinned ? "Unpin" : "Pin";
  $("#convSpent").textContent = th.items.length ? `$${th.spent.usd.toFixed(4)}, ${secs(th.spent.seconds)}` : "";
  for (const it of th.items) {
    if (TERMINAL(it.status) && S.trails.has(it.task_id) && !SEEN.done.has(it.task_id)) {
      SEEN.done.add(it.task_id);
      notify("A task finished", `${statusWord(it.status)}: ${firstLine(it.goal)}`);
    }
  }
  renderItems(th.items);
  const last = th.items[th.items.length - 1];
  updateRing(last ? last.task_id : null);
  if (last && S.filesTask !== last.task_id) loadFiles(last.task_id, "");
  for (const it of th.items) if (["running", "pending", "paused"].includes(it.status) && it.plan && it.plan.status === "approved") openStream(it.task_id);
  if (scroll) $("#items").scrollTop = $("#items").scrollHeight;
}
async function renameConversation() {
  if (!S.conv) return toast("Start a conversation first.");
  const t = prompt("Rename this conversation", $("#convTitle").textContent);
  if (t === null || !t.trim()) return;
  await api("/conversations/" + S.conv, {method: "PATCH", body: JSON.stringify({title: t})});
  refreshThread(); S.lastSide = ""; loadSide();
}
async function pinConversation() {
  if (!S.conv || !S.thread) return toast("Start a conversation first.");
  await api("/conversations/" + S.conv, {method: "PATCH", body: JSON.stringify({pinned: !S.thread.conversation.pinned})});
  S.lastSide = ""; loadSide(); refreshThread();
}
async function download(path, name) {
  const r = await fetch(path, {headers: {"Authorization": "Bearer " + TOKEN}});
  if (!r.ok) return toast(`Export failed: ${r.status} ${r.statusText}`, true);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
function exportConversation(format) {
  if (!S.conv) return toast("Open a conversation to export it.", true);
  const f = format === "json" ? "json" : "md";
  download(`/conversations/${S.conv}/export?format=${f}`, `${$("#convTitle").textContent.slice(0, 60).replace(/[^\w.-]+/g, "-")}.${f}`);
}
async function archiveConversation() {
  if (!S.conv || !S.thread) return;
  const now = !S.thread.conversation.archived;
  await api("/conversations/" + S.conv, {method: "PATCH", body: JSON.stringify({archived: now})});
  toast(now ? "Archived. It is under Show archived." : "Back in the list.");
  S.lastSide = ""; loadSide(); refreshThread();
}

function renderItems(items) {
  if (!items.length) return renderWelcome();
  $("#items").innerHTML = items.map(renderItem).join("");
}
function renderItem(it) {
  return `<article class="exchange" id="t-${esc(it.task_id)}" aria-label="${esc(firstLine(it.goal))}">
    <div class="ask-bubble">${esc(it.goal)}</div>
    <div class="ask-meta">${it.scope && it.scope.length ? `may touch <code>${it.scope.map(esc).join(", ")}</code>, ` : "no files, "}${ago(it.created_at)}
      ${TERMINAL(it.status) || (it.plan && it.plan.status === "rejected") ? `<button class="link" data-action="askAgain" ${args(it.task_id)}>Ask again</button>
      <button class="link" data-action="editAsk" ${args(it.task_id)}>Edit and ask</button>` : ""}</div>
    ${stateCard(it)}
    ${(it.approvals || []).map(approvalCard).join("")}
  </article>`;
}
function stateCard(it) {
  const p = it.plan;
  if (S.drafting.has(it.task_id)) return `<div class="card drafting"><span class="spin"></span>
      <div>Drafting a plan. It reads only what it may read, and changes nothing while it plans.
      A local model takes a minute or more.</div></div>`;
  if (it.status === "pending" && !p) return `<div class="card"><h3>No plan yet</h3>
      <button class="btn primary" data-action="draft" ${args(it.task_id)}>Draft a plan</button></div>`;
  if (it.status === "pending" && p && p.status === "drafted") return planCard(it);
  if (p && p.status === "rejected" && it.status === "pending") return `<div class="card"><h3>Sent back</h3>
      <p class="note">The plan was sent back with your comment on the record.</p>
      <button class="btn" data-action="draft" ${args(it.task_id)}>Draft again</button></div>`;
  return runCard(it);
}
function planCard(it) {
  const pl = it.plan.body, id = it.plan.plan_id;
  const list = (xs, lbl) => xs && xs.length ? `<div class="lbl">${lbl}</div><ul>${xs.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
  const settle = list(pl.open_questions, "Questions it has") + list(pl.human_approvals_expected, "Where it expects to stop and ask you")
               + list(pl.unknowns, "What it doesn't know yet");
  const steps = (pl.steps || []).map((s, i) => `
    <li class="${s.reversible === false ? "irrev" : ""}">
      <div class="node" aria-hidden="true">${i + 1}</div>
      <div>
        <p class="intent">${esc(s.intent)}</p>
        ${s.reversible === false ? `<p class="irrev-note">Can't be undone once it runs.</p>` : ""}
        <dl>
          <dt>Uses</dt><dd>${(s.tools || []).map(x => `<code>${esc(x)}</code>`).join(", ") || "no tools"}${s.action_mode === "code" ? ", by writing code" : ""}</dd>
          <dt>May touch</dt><dd>${(s.artifact_scope || []).map(x => `<code>${esc(x)}</code>`).join(", ") || "no files"}</dd>
          <dt>Checked by</dt><dd><code>${esc(s.success_criterion)}</code></dd>
          ${(s.depends_on || []).length ? `<dt>After step</dt><dd>${s.depends_on.map(esc).join(", ")}</dd>` : ""}
        </dl>
      </div>
      <div class="est">${secs(s.est_seconds || 0)}<br>${ktok(s.est_tokens || 0)} tokens</div>
    </li>`).join("");
  const hash = id.replace(/(.{4})/g, "$1 ").trim();
  return `<section class="card plan" aria-label="Plan for your signature">
    <h3>Plan for your signature</h3>
    <p class="objective">${esc(pl.objective || it.goal)}</p>
    ${settle ? `<div class="settle"><h4>Settle these before you sign</h4>${settle}</div>`
             : `<div class="settle clear">Nothing to settle. It should run to the end unattended.</div>`}
    <ol class="track" aria-label="Steps">${steps || '<li class="empty">The plan has no steps.</li>'}</ol>
    <p class="totals">${(pl.steps || []).length} steps, about ${secs(pl.total_est_seconds || 0)} in total.</p>
    <div class="sign">
      <div class="seal" id="seal-${esc(it.task_id)}">
        <div class="seal-ring" aria-hidden="true"></div>
        <div><div class="what">You are signing plan</div><div class="hash" title="${esc(id)}">${esc(hash.slice(0, 39))}…</div></div>
      </div>
      <div class="row">
        <button class="btn" data-action="showSendBack" ${args(it.task_id)}>Send back</button>
        <button class="btn primary" data-action="sign" ${args(it.task_id, id)}>Sign and run</button>
      </div>
      <div class="sendback" id="back-${esc(it.task_id)}" hidden>
        <input id="why-${esc(it.task_id)}" placeholder="What should change? This goes on the record with the plan." aria-label="What should change">
        <button class="btn" data-action="sendBack" ${args(it.task_id, id)}>Send back</button>
      </div>
    </div>
  </section>`;
}
function runCard(it) {
  const b = it.budget || {}, live = ["running", "pending"].includes(it.status);
  const pct = b.usd ? Math.min(100, Math.round(100 * (b.spent_usd || 0) / b.usd)) : 0;
  const cls = it.status.startsWith("done") ? "st-done" : it.status === "paused" ? "st-needs" : "";
  return `<section class="card run" aria-label="Run">
    <div class="status"><span><b class="${cls}">${esc(statusWord(it.status))}</b></span>
      <span>$${Number(b.spent_usd || 0).toFixed(4)} of $${Number(b.usd || 0).toFixed(2)}</span>
      <span>${secs(b.spent_seconds || 0)} of ${secs(b.seconds || 0)}</span>
      <span id="steps-${esc(it.task_id)}">${trail(it.task_id).step ? `step ${trail(it.task_id).step}` : ""}</span></div>
    <div class="budget" aria-hidden="true"><i data-pct="${pct}"></i></div>
    <p class="prefill" id="pre-${esc(it.task_id)}" ${trail(it.task_id).waiting ? "" : "hidden"}><span class="spin"></span> Reading its prompt before the first word. On a CPU this is most of a turn.</p>
    <div class="live" id="live-${esc(it.task_id)}" aria-live="off">${esc(trail(it.task_id).live)}</div>
    <div class="tools" id="tools-${esc(it.task_id)}">${trail(it.task_id).tools.map(toolCard).join("")}</div>
    ${it.status === "running" ? `<div class="row"><button class="btn" data-action="stopTask" ${args(it.task_id)}>Pause</button></div>` : ""}
    ${it.status === "paused" && !(it.approvals || []).length ? `<p class="note">Paused. It carries on from where it left off when you resume it.</p>
      <div class="row"><button class="btn primary" data-action="resumeTask" ${args(it.task_id)}>Resume</button></div>` : ""}
    ${!live && !it.answer && TERMINAL(it.status) ? `<p class="note">It finished without an answer.</p>` : ""}
  </section>
  ${it.answer ? answerCard(it) : ""}`;
}
function answerCard(it) {
  S.answers.set(it.task_id, it.answer);
  const html = renderMarkdown(it.answer).replace(/<pre class="code">/g,
    '<pre class="code"><button class="copy quiet" data-action="copyCode">Copy</button>');
  return `<section class="card answer" aria-label="Answer"><div class="answer-head"><h3>Answer</h3>
    <button class="quiet" data-action="copyAnswer" ${args(it.task_id)}>Copy</button></div>
    <div class="md">${html}</div></section>`;
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied."); }
  catch { toast("The clipboard refused. Select the text instead.", true); }
}
function copyAnswer(taskId) { copyText(S.answers.get(taskId) || ""); }
function copyCode(el) { const c = el.closest("pre").querySelector("code"); copyText(c ? c.textContent : ""); }
async function stopTask(taskId) {
  try { await post(`/tasks/${taskId}/pause`); toast("Pausing. It finishes the step it is on, then pauses."); afterChange(); }
  catch (e) { toast(e.message, true); }
}
async function resumeTask(taskId) {
  try { await post(`/tasks/${taskId}/run`); toast("Resumed."); await afterChange(); openStream(taskId); }
  catch (e) { toast(e.message, true); }
}
function goalOf(taskId) {
  const it = ((S.thread && S.thread.items) || []).find(x => x.task_id === taskId);
  return it ? it.goal : "";
}
function askAgain(taskId) { send(goalOf(taskId)); }
function editAsk(taskId) { const a = $("#ask"); a.value = goalOf(taskId); a.focus(); a.setSelectionRange(a.value.length, a.value.length); }
function approvalCard(g) {
  return `<section class="card approval" aria-label="Decision needed">
    <h3>It wants to use <code>${esc(g.tool)}</code> and is waiting for you</h3>
    <p>${esc(g.reason)}</p><pre>${esc(g.preview)}</pre>
    <div class="row">
      <button class="btn amber" data-action="decide" ${args(g.id, "approve", g.task_id)}>Allow this once</button>
      <button class="btn" data-action="decide" ${args(g.id, "deny", g.task_id)}>Refuse</button>
    </div></section>`;
}

$("#composer").addEventListener("submit", e => { e.preventDefault(); send(); });
$("#ask").addEventListener("keydown", e => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); send(); }
});
const SLASH = {
  "/new": ["Start a new conversation", () => newConversation()],
  "/export": ["Download this conversation as Markdown", () => exportConversation("md")],
  "/json": ["Download this conversation as JSON", () => exportConversation("json")],
  "/pin": ["Pin or unpin this conversation", () => pinConversation()],
  "/theme": ["Switch light or dark", () => toggleTheme()],
  "/notify": ["Turn on notifications", () => enableNotifications()],
  "/help": ["Open the command palette", () => openPalette()],
};
async function send(given) {
  const text = (typeof given === "string" ? given : $("#ask").value).trim();
  if (!text) return $("#ask").focus();
  if (/^\/\w+$/.test(text)) {
    const c = SLASH[text.toLowerCase()];
    if (!c) return toast(`No command ${text}. /help lists them; anything else is sent as a request.`, true);
    $("#ask").value = ""; return c[1]();
  }
  try {
    if (!S.conv) { const c = await post("/conversations", {}); S.conv = c.id; }
    const usd = $("#budUsd").value;
    const m = await post(`/conversations/${S.conv}/messages`, {text,
      artifact_scope: [$("#scope").value.trim() || "work"], budget_usd: usd === "" ? null : Number(usd)});
    if (typeof given !== "string") $("#ask").value = "";
    S.lastSide = ""; loadSide();
    await draft(m.task_id);
  } catch (err) { toast(err.message, true); }
}
async function draft(taskId) {
  S.drafting.add(taskId);
  await refreshThread(true);
  try { await post(`/tasks/${taskId}/plan`); }
  catch (err) { toast(err.message, true); }
  S.drafting.delete(taskId);
  await refreshThread(true); S.lastSide = ""; loadSide();
}
function showSendBack(taskId) { $("#back-" + taskId).hidden = false; $("#why-" + taskId).focus(); }
async function sign(taskId, planId) {
  try {
    await post(`/tasks/${taskId}/plan/approve`, {plan_id: planId, edits: []});
    const seal = $("#seal-" + taskId);
    if (seal) { seal.classList.add("signed"); seal.querySelector(".what").textContent = "Signed and pinned to the record"; }
    await post(`/tasks/${taskId}/run`);
    toast("Signed. It's running.");
    setTimeout(async () => { await afterChange(); openStream(taskId); }, 650);
  } catch (e) { toast(e.message, true); }
}
async function sendBack(taskId, planId) {
  try {
    await post(`/tasks/${taskId}/plan/reject`, {plan_id: planId, comment: $("#why-" + taskId).value});
    toast("Sent back. Draft again when you're ready."); afterChange();
  } catch (e) { toast(e.message, true); }
}
async function decide(id, d, taskId) {
  try {
    await post("/approvals/" + id, {decision: d});
    try { await post(`/tasks/${taskId}/run`); } catch (e) { if (e.status !== 409) throw e; }
    toast(d === "approve" ? "Allowed once. It carries on." : "Refused. It carries on without it.");
    afterChange(); if (S.conv) setTimeout(() => openStream(taskId), 400);
  } catch (e) { toast(e.message, true); }
}
async function afterChange() {
  S.lastSide = ""; loadSide();
  if (S.conv) return refreshThread();
  if (S.loose) return openTaskAlone(S.loose);
}

async function openTaskOf(taskId) {
  const t = await api("/tasks/" + taskId).catch(e => { toast(e.message, true); return null; });
  if (!t) return;
  if (t.conversation_id) { await selectConversation(t.conversation_id);
    const el = $("#t-" + taskId); if (el) el.scrollIntoView({block: "center"}); return; }
  openTaskAlone(taskId);
}
async function looseItem(taskId) {
  const [t, gates] = await Promise.all([api("/tasks/" + taskId), api("/approvals?task_id=" + taskId)]);
  let plan = null;
  try { const p = await api(`/tasks/${taskId}/plan`); plan = {plan_id: p.plan_id, status: p.status, body: p.plan}; }
  catch (e) { if (e.status !== 404) toast(e.message, true); }
  return {task_id: taskId, goal: t.goal, scope: [], status: t.status, created_at: Date.now() / 1000,
          budget: t.budget, plan, approvals: gates.pending, answer: t.answer_shown};
}
async function openTaskAlone(taskId) {
  closeStreams(); S.conv = null; S.loose = taskId;
  const it = await looseItem(taskId);
  $("#convTitle").textContent = firstLine(it.goal);
  $("#items").innerHTML = renderItem(it);
  updateRing(taskId); loadFiles(taskId, "");
  if (it.status === "running") openStream(taskId);
  closeSideOnPhone();
}
async function openLooseList() {
  closeStreams(); S.conv = null; S.loose = null;
  const d = await api("/tasks");
  const loose = [];
  for (const t of d.tasks) loose.push(`<button class="side-item" data-action="openTaskAlone" ${args(t.task_id)}>
      <span class="t">${esc(firstLine(t.goal))}</span><span class="s">${esc(statusWord(t.status))}, ${ago(t.created_at)}</span></button>`);
  $("#convTitle").textContent = "Tasks started elsewhere";
  $("#items").innerHTML = `<div class="card"><h3>From the CLI, the TUI, schedules and channels</h3>${loose.join("") || '<div class="empty">None.</div>'}</div>`;
  closeSideOnPhone();
}

function closeStreams() { for (const c of S.streams.values()) c.abort(); S.streams.clear(); }
async function openStream(taskId) {
  if (S.streams.has(taskId)) return;
  const ctl = new AbortController(); S.streams.set(taskId, ctl);
  S.trails.set(taskId, {tools: [], live: "", waiting: false, step: null});   // the replay rebuilds it
  const tools = $("#tools-" + taskId); if (tools) tools.innerHTML = "";
  try {
    const r = await fetch(`/tasks/${taskId}/stream`, {headers: {"Authorization": "Bearer " + TOKEN}, signal: ctl.signal});
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i); buf = buf.slice(i + 2);
        const ev = {};
        for (const line of block.split("\n")) {
          const k = line.indexOf(": "); if (k < 0) continue;
          ev[line.slice(0, k)] = line.slice(k + 2);
        }
        if (ev.event) onEvent(taskId, ev.event, JSON.parse(ev.data || "{}"));
      }
    }
  } catch (e) { if (e.name !== "AbortError") toast("Live output stopped: " + e.message, true); }
  S.streams.delete(taskId);
}
function trail(taskId) {
  if (!S.trails.has(taskId)) S.trails.set(taskId, {tools: [], live: "", waiting: false, step: null});
  return S.trails.get(taskId);
}
function toolCard(t) {
  const res = t.ok === undefined ? `<span class="spin"></span>` : `${t.ok ? "done" : "failed"}, ${t.ms} ms`;
  return `<div class="tool ${t.ok === undefined ? "" : t.ok ? "ok" : "bad"}">
    <span class="name">${esc(t.tool)}</span><span class="pv" title="${esc(t.preview)}">${esc(t.preview)}</span>
    <span class="res">${res}</span></div>`;
}
function onEvent(taskId, name, d) {
  const tr = trail(taskId);
  const live = $("#live-" + taskId), pre = $("#pre-" + taskId), tools = $("#tools-" + taskId);
  const redrawTools = () => { if (tools) tools.innerHTML = tr.tools.map(toolCard).join(""); };
  if (name === "live.waiting") { tr.waiting = true; if (pre) pre.hidden = false; }
  else if (name === "live.token") {
    tr.waiting = false; if (pre) pre.hidden = true;
    tr.live = (tr.live + (d.text || "")).slice(-6000);
    if (live) { live.textContent = tr.live; live.scrollTop = live.scrollHeight; }
  } else if (name === "live.step") {
    tr.waiting = false; if (pre) pre.hidden = true;
    tr.step = d.step + 1;
    const s = $("#steps-" + taskId); if (s) s.textContent = `step ${tr.step}`;
    updateRing(taskId);
  } else if (name === "tool.called") {
    tr.tools.push({key: d.key || "", tool: d.tool, preview: d.preview || ""});
    tr.live = ""; if (live) live.textContent = "";
    redrawTools();
  } else if (name === "tool.returned") {
    const t = [...tr.tools].reverse().find(x => x.key === (d.key || "") && x.ok === undefined);
    if (t) { t.ok = !!d.ok; t.ms = d.wall_ms; redrawTools(); }
  } else if (name === "delegation.opened") {
    tr.tools.push({key: "", tool: "delegated", preview: d.child_task_id || d.goal || "a sub-task", ok: true, ms: 0});
    redrawTools();
  } else if (name === "task.status" && TERMINAL(d.status || "")) {
    setTimeout(afterChange, 300);
  } else if (name === "task.status" || name === "budget.updated") {
    clearTimeout(onEvent._t); onEvent._t = setTimeout(afterChange, 1500);
  }
}

async function updateRing(taskId) {
  let m;
  try { m = await api("/context" + (taskId ? "?task_id=" + taskId : "")); } catch { return; }
  const used = Math.min(100, 100 * m.used / m.window), ceil = 100 * m.ceiling / m.window;
  const u = $("#ringUsed");
  u.style.strokeDasharray = `${used} 100`;
  u.classList.toggle("near", m.used >= m.ceiling * 0.8);
  $("#ringCeiling").setAttribute("transform", `rotate(${ceil * 3.6} 18 18)`);
  $("#ringLabel").textContent = `${ktok(m.used)} of ${ktok(m.ceiling)}`;
  $("#ringWrap").title = `About ${m.used.toLocaleString()} tokens in use (estimated). It compacts at the ceiling,
${m.ceiling.toLocaleString()} tokens (${Math.round(m.fill_ratio * 100)}% of the ${m.window.toLocaleString()}-token window). The tick marks the ceiling.`;
}

function toggleFiles() { $("#app").classList.toggle("files-closed"); }
function toggleSide() { $("#app").classList.toggle("side-open"); }
function closeSideOnPhone() { $("#app").classList.remove("side-open"); }
async function loadFiles(taskId, path) {
  S.filesTask = taskId;
  let d;
  try { d = await api(`/tasks/${taskId}/files?path=${encodeURIComponent(path)}`); }
  catch (e) {
    $("#filePath").textContent = e.status === 404 ? "Nothing here yet." : e.message;
    $("#fileList").innerHTML = ""; return;
  }
  if (d.kind === "dir") {
    S.filesPath = d.path;
    $("#filePath").textContent = d.path + "/";
    const up = d.path.includes("/") ? `<button class="file" data-action="openFile" ${args(d.path.split("/").slice(0, -1).join("/"))}>..</button>` : "";
    $("#fileList").innerHTML = up + (d.entries.map(e => `<button class="file" data-action="openFile" ${args(d.path + "/" + e.name)}>
        <span>${esc(e.name)}${e.dir ? "/" : ""}</span><span class="sz">${e.dir ? "" : ktok(e.bytes) + "B"}</span></button>`).join("")
        || `<div class="empty">Empty so far.</div>`);
  } else {
    $$(".file").forEach(f => f.setAttribute("aria-current", String(f.dataset.args === JSON.stringify([d.path]))));
    const pv = $("#filePreview");
    pv.classList.toggle("md", d.text !== undefined && isMarkdownFile(d.path));
    if (d.text !== undefined && isMarkdownFile(d.path)) pv.innerHTML = renderMarkdown(d.text);
    else pv.textContent = d.text !== undefined ? d.text
      : d.too_large ? `Too large to preview (${d.bytes.toLocaleString()} bytes).`
      : `Binary file, ${d.bytes.toLocaleString()} bytes.`;
  }
}
function isMarkdownFile(path) { return /\.(md|markdown)$/i.test(path || ""); }
function openFile(path) { if (S.filesTask) loadFiles(S.filesTask, path); }
async function upload(file) {
  if (!S.filesTask) return toast("Pick a task first: files go into a task's workspace.", true);
  if (file.size > 10 * 1024 * 1024) return toast("Larger than 10 MB; not added.", true);
  const b64 = await new Promise((ok, bad) => { const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",", 2)[1] || ""); r.onerror = bad; r.readAsDataURL(file); });
  try {
    const dir = S.filesPath || "work";
    const out = await post(`/tasks/${S.filesTask}/files`, {path: `${dir}/${file.name}`, content_b64: b64});
    toast(`Added ${out.path}, marked untrusted.`); loadFiles(S.filesTask, dir);
  } catch (e) { toast(e.message, true); }
}
const drop = $("#drop");
["dragenter", "dragover"].forEach(t => drop.addEventListener(t, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach(t => drop.addEventListener(t, () => drop.classList.remove("over")));
drop.addEventListener("drop", e => { e.preventDefault(); [...e.dataTransfer.files].forEach(upload); });
drop.addEventListener("paste", e => { [...(e.clipboardData || {}).files || []].forEach(upload); });

function openControl(pane) {
  if ($("#control").hidden) RETURN_FOCUS = document.activeElement;
  $("#control").hidden = false; openPane(pane || S.pane);
}
function closeControl() { $("#control").hidden = true; restoreFocus(); }
function restoreFocus() { const r = RETURN_FOCUS; RETURN_FOCUS = null; if (r && r.focus && r.offsetParent !== null) r.focus(); }
async function openPane(name) {
  if ($("#control").hidden) RETURN_FOCUS = document.activeElement;
  S.pane = name; $("#control").hidden = false;
  $$(".cc-nav button").forEach(b => b.setAttribute("aria-current", String(b.dataset.pane === name)));
  const body = $("#ccBody");
  body.innerHTML = `<p class="note"><span class="spin"></span> Loading</p>`;
  try { body.innerHTML = await PANES[name](); } catch (e) { body.innerHTML = `<p class="caveat">${esc(e.message)}</p>`; }
  body.focus();
  if (name === "suites") loadSuites();
}
$(".cc-nav").addEventListener("click", e => { const b = e.target.closest("button[data-pane]"); if (b) openPane(b.dataset.pane); });

const PANES = {
  async memory() {
    const m = await api("/memory");
    const state = {intact: "Blessed. It matches what you last accepted.",
                   tampered: "Changed since you last accepted it. Read it, then accept it if the change is yours.",
                   unrecorded: "Never accepted. Read it, then accept it."}[m.blessing] || m.blessing;
    return `<h3>Memory</h3><p class="lede">What the agent carries into every session. It is in the prompt, so every
      line here shapes what it does. Stored in <code>${esc(m.root)}</code>.</p>
      <p class="${m.blessing === "intact" ? "caveat good" : "caveat"}">${esc(state)}</p>
      ${m.tiers.map(t => `<h4>${esc(t.file)}, ${t.chars} of ${t.cap} characters</h4>
        ${t.entries.map(e => `<div class="entry"><span class="taint">${esc(e.taint)}</span> ${esc(e.text)}
          <button class="quiet" data-action="forgetMemory" ${args(e.text)}>Forget</button></div>`).join("")
          || '<div class="empty">Empty.</div>'}`).join("")}
      <h4>Accept what is here</h4><p class="note">Same as <code>alpha memory --accept</code>. Forget removes a
      line and accepts what is left, like <code>alpha memory --forget</code>.</p>
      <button class="btn" data-action="acceptMemory">Accept this memory</button>`;
  },
  async schedules() {
    const d = await api("/panels/schedules");
    return `<h3>Schedules</h3><p class="lede">Each firing creates a task that still needs its plan signed.
      Missed occurrences are recorded, never run late.</p>
      <table><tr><th>When</th><th>Goal</th><th>Next</th><th class="r">Missed</th><th></th></tr>
      ${d.schedules.map(s => `<tr><td><code>${esc(s.spec)}</code></td><td>${esc(firstLine(s.goal))}</td>
        <td>${new Date(s.next_fire * 1000).toLocaleString()}</td><td class="r">${s.missed}</td>
        <td class="r"><button class="quiet" data-action="removeSchedule" ${args(s.id)}>Remove</button></td></tr>`).join("")
        || '<tr><td colspan="5" class="empty">No schedules yet.</td></tr>'}</table>
      <h4>Add a schedule</h4>
      <div class="grid2"><label class="field">When (cron: minute hour day month weekday)<input id="schSpec" placeholder="0 9 * * 1"></label>
      <label class="field">Workspace<input id="schScope" value="work"></label></div>
      <label class="field">Goal<textarea id="schGoal" placeholder="Summarise what changed in work/ this week."></textarea></label>
      <button class="btn primary" data-action="addSchedule">Add schedule</button>
      <h4>Parked runs</h4>${d.parked.map(n => `<div class="entry"><code>${esc(n.task_id)}</code> ${esc(n.kind)}, ${ago(n.created_at)}</div>`).join("")
        || '<div class="empty">None.</div>'}`;
  },
  async channels() {
    const d = await api("/panels/channels");
    return `<h3>Channels</h3><p class="lede">Telegram is ${d.telegram.enabled ? "on" : "off"}. An empty allowlist
      means nobody, not everybody.</p>
      <h4>Allowed senders</h4>${d.telegram.allowed_senders.map(s => `<code>${esc(s)}</code>`).join(", ") || '<span class="empty">Nobody.</span>'}
      <h4>Dropped messages</h4><p class="note">From senders not on the list. Only a hash and a length are kept, never the text.</p>
      <table><tr><th>When</th><th>Sender</th><th>Why</th><th>Hash</th><th class="r">Length</th></tr>
      ${d.drops.map(x => `<tr><td>${ago(x.created_at)}</td><td><code>${esc(x.sender)}</code></td><td>${esc(x.reason)}</td>
        <td><code>${esc(String(x.body_sha256).slice(0, 12))}</code></td><td class="r">${x.body_len}</td></tr>`).join("")
        || '<tr><td colspan="5" class="empty">None.</td></tr>'}</table>`;
  },
  async skills() {
    const d = await api("/skills");
    return `<h3>Skills</h3><p class="lede">A skill starts as a candidate. Only the gate can promote it, after enough paired trials.</p>
      <table><tr><th>Name</th><th>Version</th><th>Status</th><th></th></tr>
      ${d.skills.map(s => `<tr><td><button class="link" data-action="viewSkill" ${args(s.id)}><code>${esc(s.name)}</code></button></td><td>${s.version}</td>
        <td class="${s.status === "promoted" ? "st-done" : ""}">${esc(s.status)}</td>
        <td class="r"><button class="quiet" data-action="checkGate" ${args(s.id)}>Check the gate</button></td></tr>`).join("")
        || '<tr><td colspan="4" class="empty">No skills yet.</td></tr>'}</table>
      <div id="skillBody"></div>
      <h4>Add a candidate</h4>
      <div class="grid2"><label class="field">Name<input id="kName"></label>
      <label class="field">Applies when<input id="kWhen"></label></div>
      <label class="field">Body<textarea id="kBody"># skill name

What to do, in the words a model reads.</textarea></label>
      <button class="btn primary" data-action="addSkill">Add as a candidate</button>`;
  },
  async suites() {
    return `<h3>Eval suites</h3><p class="lede">The tasks a skill is measured against. The gate needs
      <span id="minTrials">30</span> paired trials, so a suite needs at least that many tasks.</p>
      <table><tr><th>Suite</th><th>Tasks</th><th>Ready</th><th></th></tr><tbody id="suiteRows"></tbody></table>
      <h4>New suite</h4><div class="grid2"><label class="field">Name<input id="sName" placeholder="tps-analysis"></label>
      <label class="field">What it measures<input id="sDesc"></label></div>
      <button class="btn primary" data-action="createSuite">Create suite</button>
      <div id="suiteDetail" hidden>
        <h4 id="sdTitle"></h4><div id="shapeWarn"></div>
        <table><tr><th>Key</th><th>Goal</th><th>Check</th><th>Right answer</th><th></th></tr><tbody id="taskRows"></tbody></table>
        <h4>Add a task</h4>
        <div class="grid2"><label class="field">Key<input id="tKey"></label>
        <label class="field">Check<select id="tCrit" data-change="critChanged"></select></label></div>
        <label class="field">Goal<textarea id="tGoal"></textarea></label>
        <div class="grid2"><label class="field"><span id="tExpLabel">Expected answer (JSON)</span><textarea id="tExp">{"value": 42}</textarea></label>
        <label class="field">Answer shape (JSON Schema)<textarea id="tSchema"></textarea></label></div>
        <div class="grid2"><label class="field">Input files (JSON: name to contents)<textarea id="tFix"></textarea></label>
        <div><label class="field">Time budget, seconds<input id="tSecs" type="number" min="1"></label>
        <label class="field">A failure here vetoes the suite<select id="tCat"><option value="0">No</option><option value="1">Yes</option></select></label></div></div>
        <div class="row"><button class="btn primary" data-action="saveTask">Save task</button>
        <button class="btn" data-action="closeSuite">Close</button></div>
      </div>`;
  },
  async policy() {
    const p = await api("/policy");
    return `<h3>Reviewer policy</h3><p class="lede">What the reviewing model may approve on its own. Default is to
      refuse. In effect from <code>${esc(p.effective_source)}</code>.</p>
      <label class="field">Policy<textarea id="pBody" class="tall">${esc(p.body)}</textarea></label>
      <button class="btn primary" data-action="savePolicy">Save policy</button>`;
  },
  async integrity() {
    const d = await api("/panels/integrity");
    const crit = d.findings.filter(f => f.severity === "critical");
    return `<h3>Integrity</h3><p class="lede">Every task's record is a hash chain, checkpointed to an anchor kept
      outside the database. All ${d.tasks} are re-verified whenever the record changes, and at least every 30 seconds.</p>
      ${crit.length ? `<div class="integrity"><strong>${crit.length} record${crit.length > 1 ? "s do" : " does"} not verify.</strong>
        Do not approve anything until this is explained.</div>`
        : `<p class="caveat good">Every chain verifies.</p>`}
      <table><tr><th>Severity</th><th>Task</th><th>Finding</th></tr>
      ${d.findings.map(f => `<tr class="${f.severity === "critical" ? "integrity-row" : ""}"><td>${esc(f.severity)}</td>
        <td><code>${esc(f.task_id || "")}</code></td><td>${esc(f.detail)}</td></tr>`).join("")
        || '<tr><td colspan="3" class="empty">No findings.</td></tr>'}</table>
      <h4>Anchor</h4><dl class="kv"><dt>File</dt><dd><code>${esc(d.anchor.path)}</code></dd>
      <dt>Checkpoints</dt><dd>${d.anchor.entries}</dd>
      <dt>Last written</dt><dd>${d.anchor.last_written ? new Date(d.anchor.last_written * 1000).toLocaleString() : "never"}</dd></dl>`;
  },
  async measurement() {
    const d = await api("/panels/measurement");
    return `<h3>Measurement</h3><p class="lede">What produced the numbers: versions, the profile, the run records,
      and which paths have ever run for real in this database.</p>
      <dl class="kv"><dt>Harness version</dt><dd>${d.harness_version}</dd><dt>Toolset version</dt><dd>${d.toolset_version}</dd>
      <dt>Profile</dt><dd>${esc(d.profile)}</dd></dl>
      <h4>Has it ever run here?</h4>${d.inventory ? `<table><tr><th>Path</th><th>State</th><th class="r">Count</th></tr>
        ${d.inventory.map(r => `<tr><td>${esc(r.row || r.name || "")}</td><td>${esc(r.state || "")}</td><td class="r">${esc(r.count ?? "")}</td></tr>`).join("")}</table>`
        : '<p class="note">The inventory needs bench/ beside the source; an installed copy has none.</p>'}
      <h4>Run records</h4><table>${d.runs.map(r => `<tr><td><code>${esc(r.name)}</code></td><td class="r">${ktok(r.bytes)}B</td>
        <td>${ago(r.modified)}</td></tr>`).join("") || '<tr><td class="empty">None yet.</td></tr>'}</table>`;
  },
  async settings() {
    const d = await api("/panels/settings");
    return `<h3>Settings</h3><p class="lede">Read from the config this console was started with. Change them there;
      the console does not rewrite your config.</p>
      <h4>Appearance</h4><button class="btn" data-action="toggleTheme">Switch to ${document.documentElement.dataset.theme === "light" ? "dark" : "light"}</button>
      <h4>Notifications</h4><p class="note">A desktop notification when something needs you or a watched task finishes,
      only while this tab is in the background. ${"Notification" in window ? `Now: ${esc(Notification.permission)}.` : "Not available here."}</p>
      <button class="btn" data-action="enableNotifications">Turn on notifications</button>
      <h4>Models</h4><table><tr><th>Name</th><th>Kind</th><th>Model</th><th>Trust</th><th class="r">Window</th></tr>
      ${d.backends.map(b => `<tr><td>${esc(b.name)}</td><td>${esc(b.kind)}</td><td><code>${esc(b.model)}</code></td>
        <td>${esc(b.tier)}</td><td class="r">${ktok(b.window)}</td></tr>`).join("")}</table>
      <h4>Profile and limits</h4><dl class="kv"><dt>Profile</dt><dd>${esc(d.profile)}</dd>
      ${Object.entries(d.limits).map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      <h4>Autonomy</h4><dl class="kv">${Object.entries(d.autonomy).map(([k, v]) => `<dt>${esc(k.replace(/_/g, " "))}</dt><dd>${esc(v)}</dd>`).join("")}</dl>
      <h4>Where state lives</h4><dl class="kv"><dt>Project</dt><dd><code>${esc(d.root)}</code></dd>
      <dt>Database and records</dt><dd><code>${esc(d.state_dir)}</code></dd></dl>`;
  },
  async logs() {
    const d = await api("/panels/logs");
    return `<h3>Logs</h3><p class="lede">The newest events on every task's record. The same events, in full, are
      under each task.</p>
      <table><tr><th>When</th><th>Task</th><th>Event</th></tr>
      ${d.events.map(e => `<tr><td>${ago(e.at)}</td><td><code>${esc(String(e.task_id).slice(0, 14))}</code></td><td><code>${esc(e.type)}</code></td></tr>`).join("")}</table>`;
  },
};

async function forgetMemory(text) {
  if (!confirm(`Forget this line?\n\n${text}`)) return;
  try { const r = await post("/memory/forget", {text}); toast(`Forgot ${r.removed.length} line${r.removed.length === 1 ? "" : "s"}.`); openPane("memory"); }
  catch (e) { toast(e.message, true); }
}
async function addSchedule() {
  try { await post("/schedules", {spec: $("#schSpec").value.trim(), goal: $("#schGoal").value.trim(),
                                  scope: $("#schScope").value.trim() || "work"});
        toast("Scheduled. Each firing drafts a task whose plan you still sign."); openPane("schedules"); }
  catch (e) { toast(e.message, true); }
}
async function removeSchedule(id) {
  try { await api("/schedules/" + id, {method: "DELETE"}); toast("Removed."); openPane("schedules"); }
  catch (e) { toast(e.message, true); }
}
async function viewSkill(id) {
  try { const s = await api("/skills/" + id);
        $("#skillBody").innerHTML = `<h4>${esc(s.name)}, version ${s.version}, ${esc(s.status)}</h4>
          <p class="note">Applies when: ${esc((s.contract || {}).applies_when || "unstated")}</p>
          <div class="md skill-md">${renderMarkdown(s.body)}</div>`;
        $("#skillBody").scrollIntoView({block: "nearest"}); }
  catch (e) { toast(e.message, true); }
}
let MODELS = null;
async function loadModels() {
  try { MODELS = await api("/models"); } catch { return; }
  const ex = MODELS.executor, pick = $("#modelPick"), btn = $("#runModel");
  if (!ex) return;
  const srv = MODELS.servers.find(s => s.endpoint === ex.endpoint);
  const names = ex.local && srv && srv.up ? srv.installed.map(m => m.name) : [];
  if (!names.includes(ex.model)) names.unshift(ex.model);
  pick.innerHTML = names.map(n => `<option ${n === ex.model ? "selected" : ""}>${esc(n)}</option>`).join("");
  pick.disabled = !ex.local || !(srv && srv.up);
  pick.title = !ex.local ? "Not a local model; set in the config" : srv && srv.up ? "The model the next task runs on"
             : "The model server is not running; Run model starts it";
  btn.hidden = !ex.local || !srv || !srv.managed;
  const loaded = srv && srv.loaded.some(m => m.name === pick.value);
  btn.textContent = !srv || !srv.up ? "Start and run" : loaded ? "Running" : "Run model";
  btn.classList.toggle("on", !!loaded);
  btn.title = loaded ? "Loaded in memory and kept warm for 30 minutes" : "Load this model so the first step does not wait for it";
  $("#modelWarn").textContent = ex.warning || ""; $("#modelWarn").hidden = !ex.warning;
}
async function runModel() {
  const btn = $("#runModel"), model = $("#modelPick").value;
  btn.disabled = true; btn.innerHTML = `<span class="spin"></span> Loading`;
  try { const r = await post("/models/run", {model}); toast(r.warning || `${model} is loaded (${r.seconds} s).`, !!r.warning); }
  catch (e) { toast(e.message, true); }
  btn.disabled = false; loadModels();
}
$("#modelPick").addEventListener("change", async e => {
  const model = e.target.value;
  try { await post("/models/select", {model}); toast(`New tasks run on ${model}.`); }
  catch (err) { toast(err.message, true); }
  loadModels();
});
async function acceptMemory() {
  try { const r = await post("/memory/accept"); toast(`Accepted (was ${r.before}).`); openPane("memory"); }
  catch (e) { toast(e.message, true); }
}

let SUITE = null;
async function loadSuites() {
  const d = await api("/suites");
  $("#minTrials").textContent = d.min_paired_trials;
  $("#suiteRows").innerHTML = d.suites.length ? d.suites.map(s => {
    const pct = Math.min(100, Math.round(100 * s.n_tasks / d.min_paired_trials));
    return `<tr><td><button class="link" data-action="openSuite" ${args(s.id)}>${esc(s.name)}</button>
        <div class="note">${esc(s.description)}</div></td>
      <td>${s.n_tasks}<div class="meter"><i data-pct="${pct}"></i></div></td>
      <td>${s.ready ? "Ready" : `${d.min_paired_trials - s.n_tasks} more to go`}</td>
      <td class="r"><button class="quiet" data-action="delSuite" ${args(s.id, s.name)}>Delete</button></td></tr>`;
  }).join("") : `<tr><td colspan="4" class="empty">No suites yet. Name one below to start.</td></tr>`;
}
async function createSuite() {
  const name = $("#sName").value.trim();
  if (!name) return toast("Give the suite a name first.", true);
  try { const s = await post("/suites", {name, description: $("#sDesc").value});
        $("#sName").value = ""; $("#sDesc").value = ""; toast("Suite created."); await loadSuites(); openSuite(s.id); }
  catch (e) { toast(e.message, true); }
}
async function delSuite(id, name) {
  if (!confirm(`Delete the suite "${name}" and all its tasks?`)) return;
  await api("/suites/" + id, {method: "DELETE"});
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
      <td class="r"><button class="quiet" data-action="delTask" ${args(t.id)}>Delete</button></td></tr>`;
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
    await post(`/suites/${SUITE.id}/tasks`, {
      key: $("#tKey").value.trim(), goal: $("#tGoal").value.trim(), success_criterion: crit,
      output_schema: schema, fixtures: parseJSON($("#tFix").value, "Input files"),
      budget_seconds: Number($("#tSecs").value) || null, catastrophic: $("#tCat").value === "1"});
    $("#tKey").value = ""; $("#tGoal").value = ""; toast("Task saved.");
    await openSuite(SUITE.id); loadSuites();
  } catch (e) { toast(e.message, true); }
}
async function delTask(id) {
  await api(`/suites/${SUITE.id}/tasks/${id}`, {method: "DELETE"});
  toast("Task deleted."); await openSuite(SUITE.id); loadSuites();
}
async function addSkill() {
  try { await post("/skills", {name: $("#kName").value.trim(), body: $("#kBody").value, applies_when: $("#kWhen").value.trim()});
        toast("Added as a candidate."); openPane("skills"); }
  catch (e) { toast(e.message, true); }
}
async function checkGate(id) {
  try { const d = await api("/gate/" + id); toast(`${d.verdict}: ${d.reason}`, d.verdict !== "promote"); }
  catch (e) { toast(e.message, true); }
}
async function savePolicy() {
  try { await api("/policy", {method: "PUT", body: JSON.stringify({body: $("#pBody").value})});
        toast("Policy saved. It applies from the next review."); openPane("policy"); }
  catch (e) { toast(e.message, true); }
}
const COMMANDS = [
  ["New conversation", "newConversation"], ["Search conversations", "focusSearch"],
  ["Show or hide archived conversations", "toggleArchived"], ["Tasks started elsewhere", "openLooseList"],
  ["Show or hide the file panel", "toggleFiles"], ["Switch light or dark", "toggleTheme"],
  ["Pin or unpin this conversation", "pinConversation"], ["Export this conversation as Markdown", "exportConversation", "md"],
  ["Export this conversation as JSON", "exportConversation", "json"], ["Turn on notifications", "enableNotifications"],
  ["Open Control Center", "openControl"], ["Memory", "openPane", "memory"], ["Schedules", "openPane", "schedules"],
  ["Channels", "openPane", "channels"], ["Skills", "openPane", "skills"], ["Eval suites", "openPane", "suites"],
  ["Reviewer policy", "openPane", "policy"], ["Integrity", "openPane", "integrity"],
  ["Measurement", "openPane", "measurement"], ["Settings", "openPane", "settings"], ["Logs", "openPane", "logs"],
];
let PAL = [], PAL_I = 0;
function openPalette() {
  if ($("#palette").hidden) RETURN_FOCUS = document.activeElement;
  $("#palette").hidden = false; $("#palInput").value = ""; renderPalette(); $("#palInput").focus();
}
function closePalette() { $("#palette").hidden = true; restoreFocus(); }
function renderPalette() {
  const q = $("#palInput").value.toLowerCase().trim();
  PAL = COMMANDS.filter(c => !q || c[0].toLowerCase().includes(q));
  PAL_I = Math.min(PAL_I, Math.max(0, PAL.length - 1));
  $("#palList").innerHTML = PAL.map((c, i) => `<li role="option" id="pal-${i}" aria-selected="${i === PAL_I}"
      data-pal="${i}">${esc(c[0])}</li>`).join("") || '<li class="empty">No command matches.</li>';
  $("#palInput").setAttribute("aria-activedescendant", PAL.length ? "pal-" + PAL_I : "");
}
function runPalette(i) {
  const c = PAL[i]; if (!c) return;
  closePalette(); ACTIONS[c[1]](...c.slice(2));
}
$("#palInput").addEventListener("input", () => { PAL_I = 0; renderPalette(); });
$("#palInput").addEventListener("keydown", e => {
  if (e.key === "ArrowDown") { PAL_I = Math.min(PAL.length - 1, PAL_I + 1); renderPalette(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { PAL_I = Math.max(0, PAL_I - 1); renderPalette(); e.preventDefault(); }
  else if (e.key === "Enter") { runPalette(PAL_I); e.preventDefault(); }
});
$("#palList").addEventListener("click", e => { const li = e.target.closest("[data-pal]"); if (li) runPalette(Number(li.dataset.pal)); });
function focusSearch() { $("#app").classList.add("side-open"); $("#convSearch").focus(); }

function trapTab(e) {
  const box = !$("#palette").hidden ? $("#palette") : !$("#control").hidden ? $("#control") : null;
  if (!box || e.key !== "Tab") return;
  const f = [...box.querySelectorAll("button, input, textarea, select, [tabindex]:not([tabindex='-1'])")]
    .filter(x => !x.disabled && x.offsetParent !== null);
  if (!f.length) return;
  const first = f[0], last = f[f.length - 1];
  if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}
document.addEventListener("keydown", e => {
  trapTab(e);
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); }
  else if (e.key === "Escape") {
    if (!$("#palette").hidden) closePalette();
    else if (!$("#control").hidden) closeControl();
    else closeSideOnPhone();
  }
});
for (const id of ["#palette", "#control"]) $(id).addEventListener("click", e => {
  if (e.target === $(id)) { $(id).hidden = true; restoreFocus(); }
});

const ACTIONS = {newConversation, selectConversation, renameConversation, archiveConversation,
  toggleArchived, openLooseList, openTaskAlone, openTaskOf, draft, showSendBack, sign, sendBack, decide,
  toggleFiles, toggleSide, openFile, openControl, closeControl, openPane, openPalette, focusSearch,
  toggleTheme, acceptMemory, createSuite, saveTask, closeSuite, addSkill, savePolicy,
  openSuite, delSuite, delTask, checkGate, pinConversation, exportConversation, enableNotifications,
  runModel, stopTask, resumeTask, askAgain, editAsk, copyAnswer, copyCode, forgetMemory, addSchedule, removeSchedule, viewSkill};
document.addEventListener("click", e => {
  const el = e.target.closest("[data-action]");
  if (!el || !ACTIONS[el.dataset.action]) return;
  ACTIONS[el.dataset.action](...(el.dataset.args ? JSON.parse(el.dataset.args) : [el]));
});
document.addEventListener("change", e => {
  if (e.target.dataset.change === "critChanged") critChanged();
});
new MutationObserver(() => document.querySelectorAll("i[data-pct]").forEach(i => {
  i.style.width = i.dataset.pct + "%";
})).observe(document.body, {childList: true, subtree: true});

(async () => {
  applyTheme(); renderWelcome();
  if (window.innerWidth < 1100) $("#app").classList.add("files-closed");   // a drawer there
  if (!TOKEN) {
    $("#health").textContent = "No session. Open the console from the desktop app, or the link alpha serve prints.";
    return;
  }
  try {
    const [h, st] = await Promise.all([api("/health"), api("/panels/settings")]);
    S.settings = st;
    const exec = st.backends.find(b => b.name === "cloud") || st.backends[0];
    $("#health").textContent = `Connected. ${h.backends.length} models configured.`;
    $("#chipProfile").textContent = `Profile: ${st.profile}`;
    if ($("#budUsd").value === "") $("#budUsd").placeholder = String(st.limits.default_task_budget_usd);
  } catch { $("#health").textContent = "Can't reach the control plane"; }
  loadAlerts(); loadModels(); await loadSide(); updateRing(null);
  if (OPEN.theme === "light" || OPEN.theme === "dark") setTheme(OPEN.theme);
  if (OPEN.task) openTaskOf(OPEN.task);
  else if (OPEN.conv) selectConversation(OPEN.conv);
  else if (OPEN.conv !== "") {
    const first = $("#convList [data-action=selectConversation]");
    if (first) selectConversation(JSON.parse(first.dataset.args)[0]);
  }
  if (OPEN.pane && PANES[OPEN.pane]) openPane(OPEN.pane);
  setInterval(loadModels, 20000);
  setInterval(() => { loadAlerts(); loadSide(); if (S.conv && !S.streams.size && !S.drafting.size) refreshThread(); }, 6000);
})();
