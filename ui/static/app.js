// DocTest UI: plain JavaScript, no libraries. Shows Phase 1 scenarios and Phase 2 test cases; never runs SQL.
"use strict";

const $ = (id) => document.getElementById(id);
let S = { files: [], scenarios: [], tests: { tests: [], path: "", error: null }, job: null, demo: false };
const ui = { open: new Set(), scenFilter: "", catFilter: "", polling: null };
const JOB_UI = { phase1: { prog: "prog-phase1", msg: "msg-phase1" }, phase2: { prog: "prog-phase2", msg: "msg-phase2" } };

// ------------------------------------------------------------------ helpers

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmtSize(n) {
  return n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
}
function setMsg(id, text, ok = false) {
  const el = $(id);
  el.textContent = text || "";
  el.classList.toggle("ok", !!ok);
}
async function api(method, url, body) {
  const res = await fetch(url, body ? { method, body } : { method });
  let data = {};
  try { data = await res.json(); } catch (_) { /* not JSON */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status}).`);
  return data;
}

// Clipboard: navigator.clipboard needs https or localhost; fall back to a hidden textarea otherwise.
async function copyText(text, btn) {
  let ok = false;
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); ok = true; }
  } catch (_) { ok = false; }
  if (!ok) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { ok = document.execCommand("copy"); } catch (_) { ok = false; }
    ta.remove();
  }
  if (btn) {
    const old = btn.dataset.label || btn.textContent;
    btn.dataset.label = old;
    btn.textContent = ok ? "Copied" : "Copy failed";
    btn.classList.toggle("done", ok);
    setTimeout(() => { btn.textContent = old; btn.classList.remove("done"); }, 1200);
  }
}
function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
const pretty = (o) => JSON.stringify(o, null, 2);

// ------------------------------------------------------------------ data helpers

const testList = () => (Array.isArray(S.tests.tests) ? S.tests.tests : []);
const scenarioIds = () => new Set(S.scenarios.map((c) => c.id));
function testsBySid() {
  const m = new Map();
  testList().forEach((t, i) => {
    const sid = t && t.scenario_id;
    if (!m.has(sid)) m.set(sid, []);
    m.get(sid).push(i);
  });
  return m;
}
function asList(v) {
  if (v == null) return [];
  return Array.isArray(v) ? v : [v];
}
function stmt(s) {
  const t = String(s).trim().replace(/;+\s*$/, "");
  return t ? `${t};` : "";
}
function cellText(v) {
  if (v === null || v === undefined) return "NULL";
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}
function expectComment(e) {
  if (!e || typeof e !== "object") return "";
  if (e.type !== "rows") return `expect: ${JSON.stringify(e)}`;
  const cols = asList(e.columns), rows = asList(e.rows);
  let s;
  if (!rows.length) s = "no rows";
  else if (rows.length === 1 && cols.length) s = asList(rows[0]).map((v, i) => `${cols[i] ?? `col${i + 1}`} = ${cellText(v)}`).join(", ");
  else s = `${rows.length} rows: ` + rows.slice(0, 3).map((r) => `(${asList(r).map(cellText).join(", ")})`).join(", ") + (rows.length > 3 ? ", ..." : "");
  return `expect: ${s}${e.ordered ? " (order matters)" : ""}`;
}
function sqlScript(t) {
  const test = (t && t.test) || {};
  const lines = [`-- ${t.scenario_id ?? "unknown scenario"} (${t.category ?? "uncategorized"})`, "BEGIN;"];
  asList(test.config_sql).forEach((s) => stmt(s) && lines.push(stmt(s)));
  asList(test.setup_sql).forEach((s) => stmt(s) && lines.push(stmt(s)));
  asList(test.steps).forEach((st) => {
    const sql = stmt(st && st.sql);
    if (sql) lines.push(`${sql}   -- ${expectComment(st.expect).replace(/\n/g, " ")}`);
  });
  lines.push("ROLLBACK;");
  return lines.join("\n");
}

// ------------------------------------------------------------------ rendering

function render() {
  renderTop(); renderFiles(); renderScenarios(); renderTests(); renderSummary(); renderButtons();
}

function renderTop() {
  $("status").textContent = S.job ? `status: running · ${S.job.message}` : S.demo ? "status: demo" : "status: idle";
  $("notice").hidden = !S.demo;
}

function renderFiles() {
  $("file-list").innerHTML = S.files.map((f) => `
    <li class="item file-row">
      <span class="file-name">${esc(f.name)}</span>
      <span class="file-meta">${fmtSize(f.size)} · ${esc(f.role)}</span>
      <button type="button" class="remove" data-remove="${esc(f.name)}" aria-label="Remove ${esc(f.name)}" title="Remove">×</button>
    </li>`).join("");
  $("files-empty").hidden = S.files.length > 0;
}

function scenarioHtml(c, idx, bySid) {
  const s = c.scenario || {};
  const open = ui.open.has(`s:${idx}`);
  const amb = asList(s.ambiguities);
  const hasTest = bySid.has(c.id);
  const src = c.source && c.source.file ? `${esc(c.source.file)}${c.source.page ? ` · p.${esc(c.source.page)}` : ""}` : "";
  const tags = [
    src ? `<span class="tag">${src}</span>` : "",
    `<span class="tag conf-${esc(c.confidence_label)}">confidence ${esc(c.confidence_label)}${s.confidence != null ? ` (${esc(s.confidence)})` : ""}</span>`,
    amb.length ? `<span class="tag amb">ambiguous</span>` : "",
    hasTest ? "" : `<span class="tag notest">no test yet</span>`,
  ].join("");
  let details = "";
  if (open) {
    const ev = c.evidence.length ? c.evidence.map((e) => `<div class="quote">“${esc(e.span)}”
        <cite>${esc(e.file || "")}${e.page ? `, page ${esc(e.page)}` : ""} · chunk ${esc(e.chunk)}</cite></div>`).join("")
      : "<p class='muted'>No evidence quote.</p>";
    const labelOf = new Map(S.scenarios.map((x) => [x.id, x.label]));
    const rel = asList(s.related_scenarios);
    details = `<div class="details" data-stop>
      <h4>Evidence</h4>${ev}
      <h4>Ambiguities</h4>${amb.length ? `<ul>${amb.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>` : "<p class='muted'>None</p>"}
      <h4>Related scenarios</h4><p>${rel.length ? rel.map((r) => `${esc(labelOf.get(r) || "")} ${esc(r)}`.trim()).join(", ") : "None"}</p>
      ${c.requirement && c.requirement.requirement_text ? `<h4>Requirement</h4><p>${esc(c.requirement.requirement_text)}</p>` : ""}
    </div>`;
  }
  return `<li class="item clickable" data-scen-idx="${idx}" data-sid="${esc(c.id)}">
    <div class="card-head"><span class="label">${esc(c.label)}</span><span class="realid">${esc(c.id)}</span>
      <button type="button" class="copy" data-copy-scen="${idx}" title="Copy scenario JSON">⧉ Copy</button></div>
    <p class="scen-text">${esc(c.text)}</p>
    <div class="tags">${tags}</div>${details}</li>`;
}

function renderScenarios() {
  const q = ui.scenFilter.trim().toLowerCase();
  const bySid = testsBySid();
  const html = S.scenarios.map((c, i) => (!q || (c.text + JSON.stringify(c.scenario)).toLowerCase().includes(q)) ? scenarioHtml(c, i, bySid) : "").join("");
  $("scen-list").innerHTML = html;
  const shown = (html.match(/data-scen-idx=/g) || []).length;
  $("scen-empty").hidden = S.scenarios.length > 0;
  $("scen-count").textContent = S.scenarios.length ? (q ? `${shown} of ${S.scenarios.length}` : `${S.scenarios.length}`) : "";
}

function expectHtml(e) {
  if (!e || typeof e !== "object" || e.type !== "rows") {
    return `<pre class="raw">${esc(pretty(e ?? null))}</pre>`;  // unknown expect type: show it as-is
  }
  const cols = asList(e.columns), rows = asList(e.rows);
  const notes = [rows.length ? "" : "expects no rows", e.ordered ? "order matters" : ""].filter(Boolean);
  let table = "";
  if (rows.length) {
    const width = Math.max(cols.length, ...rows.map((r) => asList(r).length));
    const head = Array.from({ length: width }, (_, i) => `<th>${esc(cols[i] ?? `col${i + 1}`)}</th>`).join("");
    const body = rows.map((r) => `<tr>${Array.from({ length: width }, (_, i) => `<td>${esc(cellText(asList(r)[i]))}</td>`).join("")}</tr>`).join("");
    table = `<div class="expect-wrap"><table class="expect-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }
  return `${table}${notes.length ? `<p class="expect-note">${notes.join(" · ")}</p>` : ""}`;
}

function sqlBlock(sql, key) {
  return `<div class="sql-block"><pre>${esc(sql)}</pre><button type="button" class="copy" data-copy-sql="${esc(key)}">⧉ Copy</button></div>`;
}

function testHtml(t, i, known) {
  const test = (t && t.test) || {};
  const steps = asList(test.steps);
  const open = ui.open.has(`t:${i}`);
  const unknown = !known.has(t && t.scenario_id);
  let details = "";
  if (open) {
    const cfg = asList(test.config_sql).filter((s) => String(s).trim());
    const setup = asList(test.setup_sql).filter((s) => String(s).trim());
    details = `<div class="details" data-stop>
      ${cfg.length ? `<h4>Config SQL</h4>${sqlBlock(cfg.map(stmt).join("\n"), `${i}:config`)}` : ""}
      ${setup.length ? `<h4>Setup SQL</h4>${sqlBlock(setup.map(stmt).join("\n"), `${i}:setup`)}` : ""}
      ${steps.map((st, k) => `<div class="tstep"><div class="step-title">Step ${k + 1}</div>
          ${sqlBlock(stmt((st && st.sql) || ""), `${i}:step:${k}`)}
          ${expectHtml(st && st.expect)}</div>`).join("")}
      <div class="card-actions"><button type="button" class="btn-secondary" data-copy-script="${i}">Copy SQL script</button></div>
    </div>`;
  }
  return `<li class="item clickable" data-test-idx="${i}" data-sid="${esc(t && t.scenario_id)}">
    <div class="card-head"><span class="label">T${i + 1}</span><span class="realid">${esc(t && t.scenario_id)}</span>
      <span class="tag">${esc((t && t.category) || "uncategorized")}</span>
      ${unknown ? `<span class="tag unknown">unknown scenario</span>` : ""}
      <button type="button" class="copy" data-copy-test="${i}" title="Copy test case JSON">⧉ Copy</button></div>
    <div class="steps-n">${steps.length} step${steps.length === 1 ? "" : "s"}</div>${details}</li>`;
}

function renderTests() {
  const tests = testList();
  const known = scenarioIds();
  const cats = [...new Set(tests.map((t) => (t && t.category) || "uncategorized"))].sort();
  const sel = $("cat-filter");
  if (!cats.includes(ui.catFilter)) ui.catFilter = "";
  sel.innerHTML = `<option value="">All categories</option>` + cats.map((c) => `<option value="${esc(c)}"${c === ui.catFilter ? " selected" : ""}>${esc(c)}</option>`).join("");
  const html = tests.map((t, i) => (!ui.catFilter || ((t && t.category) || "uncategorized") === ui.catFilter) ? testHtml(t, i, known) : "").join("");
  $("test-list").innerHTML = html;
  setMsg("tests-error", S.tests.error || "");
  $("test-empty").hidden = tests.length > 0 || !!S.tests.error;
  $("test-count").textContent = tests.length ? `${tests.length}` : "";
  $("tests-path").textContent = S.tests.path ? `File: ${S.tests.path}` : "";
}

function renderSummary() {
  const n = S.scenarios.length;
  const bySid = testsBySid();
  const withTests = S.scenarios.filter((c) => bySid.has(c.id)).length;
  const pct = n ? Math.round((100 * withTests) / n) : 0;
  $("summary").innerHTML = `<b>${n}</b> scenario${n === 1 ? "" : "s"} &nbsp;·&nbsp; <b>${withTests}</b> with tests &nbsp;·&nbsp; coverage <b>${n ? `${pct}%` : "–"}</b>`;
}

function renderButtons() {
  const busy = !!S.job;
  $("btn-phase1").disabled = busy || S.files.length === 0;
  $("btn-phase2").disabled = busy || S.scenarios.length === 0;
  $("btn-reload").disabled = busy;
  $("btn-demo").disabled = busy;
  $("btn-reset").disabled = busy;
  $("btn-copy-scen").disabled = $("btn-dl-scen").disabled = S.scenarios.length === 0;
  $("btn-copy-tests").disabled = $("btn-dl-tests").disabled = testList().length === 0;
  for (const [kind, ids] of Object.entries(JOB_UI)) {
    const el = $(ids.prog);
    const active = busy && S.job.kind === kind;
    el.hidden = !active;
    if (active) el.textContent = S.job.message;
  }
}

// ------------------------------------------------------------------ linking

function clearHl() { document.querySelectorAll(".item.hl").forEach((el) => el.classList.remove("hl")); }
function scrollIntoColumn(container, el) {
  if (!el) return;
  const c = container.getBoundingClientRect(), r = el.getBoundingClientRect();
  if (r.top < c.top || r.bottom > c.bottom) container.scrollTop += r.top - c.top - 8;
}
function highlight(sid, from) {
  clearHl();
  if (sid === undefined || sid === null) return;
  const sel = `[data-sid="${CSS.escape(String(sid))}"]`;
  const scen = document.querySelectorAll(`#scen-list ${sel}`);
  const tests = document.querySelectorAll(`#test-list ${sel}`);
  scen.forEach((el) => el.classList.add("hl"));
  tests.forEach((el) => el.classList.add("hl"));
  if (from === "scen") scrollIntoColumn($("test-body"), tests[0]);
  else scrollIntoColumn($("scen-body"), scen[0]);
}

// ------------------------------------------------------------------ jobs

async function refresh() {
  try { S = await api("GET", "/api/state"); } catch (_) { $("status").textContent = "status: server not reachable"; return; }
  render();
}

async function startJob(kind, url) {
  setMsg(JOB_UI[kind].msg, "");
  try {
    const { job_id } = await api("POST", url);
    await refresh();
    poll(job_id, kind);
  } catch (e) { setMsg(JOB_UI[kind].msg, e.message); }
}

function poll(jobId, kind) {
  clearInterval(ui.polling);
  ui.polling = setInterval(async () => {
    let job;
    try { job = await api("GET", `/api/jobs/${jobId}`); } catch (_) { return; }
    if (job.status === "running") { S.job = job; renderTop(); renderButtons(); return; }
    clearInterval(ui.polling);
    ui.polling = null;
    await refresh();
    if (job.status === "failed") setMsg(JOB_UI[kind].msg, job.error || job.message);
    else setMsg(JOB_UI[kind].msg, job.message, true);
  }, 1000);
}

// ------------------------------------------------------------------ events

async function upload(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const fd = new FormData();
  files.forEach((f) => fd.append("files", f, f.name));
  setMsg("msg-phase1", "Uploading...", true);
  try {
    const res = await fetch("/api/upload", { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    if (data.files) S.files = data.files;
    setMsg("msg-phase1", (data.errors || []).join(" ") || (res.ok ? "" : data.error || "Upload failed."));
    renderFiles(); renderButtons();
  } catch (_) { setMsg("msg-phase1", "Upload failed: the server is not reachable."); }
}

function setupEvents() {
  const dz = $("dropzone");
  $("btn-choose").addEventListener("click", () => $("file-input").click());
  $("file-input").addEventListener("change", (e) => { upload(e.target.files); e.target.value = ""; });
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); upload(e.dataTransfer.files); });
  dz.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("file-input").click(); } });

  $("file-list").addEventListener("click", async (e) => {
    const name = e.target.dataset && e.target.dataset.remove;
    if (!name) return;
    try { S.files = (await api("DELETE", `/api/files/${encodeURIComponent(name)}`)).files; renderFiles(); renderButtons(); }
    catch (err) { setMsg("msg-phase1", err.message); }
  });

  $("btn-phase1").addEventListener("click", () => startJob("phase1", "/api/phase1"));
  $("btn-phase2").addEventListener("click", () => startJob("phase2", "/api/phase2"));
  $("btn-reload").addEventListener("click", async () => {
    try {
      S.tests = await api("GET", "/api/tests?reload=1");
      S.demo = false;
      renderTop(); renderTests(); renderScenarios(); renderSummary(); renderButtons();
      setMsg("msg-phase2", S.tests.error ? "" : `Reloaded ${testList().length} test cases.`, true);
    } catch (e) { setMsg("msg-phase2", e.message); }
  });

  $("btn-demo").addEventListener("click", async () => {
    try { S = await api("POST", "/api/demo"); ui.open.clear(); render(); } catch (e) { setMsg("msg-phase1", e.message); }
  });
  $("btn-reset").addEventListener("click", async () => {
    if (!window.confirm("Clear uploaded files and scenarios? The test file is not deleted.")) return;
    try { S = await api("POST", "/api/reset"); ui.open.clear(); ["msg-phase1", "msg-phase2"].forEach((m) => setMsg(m, "")); render(); }
    catch (e) { setMsg("msg-phase1", e.message); }
  });

  $("scen-filter").addEventListener("input", (e) => { ui.scenFilter = e.target.value; renderScenarios(); });
  $("cat-filter").addEventListener("change", (e) => { ui.catFilter = e.target.value; renderTests(); });

  $("btn-copy-scen").addEventListener("click", (e) => copyText(pretty(S.scenarios.map((c) => c.scenario)), e.currentTarget));
  $("btn-dl-scen").addEventListener("click", () => download("scenarios.json", pretty(S.scenarios.map((c) => c.scenario))));
  $("btn-copy-tests").addEventListener("click", (e) => copyText(pretty(testList()), e.currentTarget));
  $("btn-dl-tests").addEventListener("click", () => download("all_tests.json", pretty(testList())));

  // card clicks: copy buttons first, then expand / collapse (clicks inside open details do not collapse)
  $("scen-list").addEventListener("click", (e) => {
    const b = e.target.closest("[data-copy-scen]");
    if (b) { copyText(pretty(S.scenarios[+b.dataset.copyScen].scenario), b); return; }
    if (e.target.closest("[data-stop]")) return;
    const card = e.target.closest("[data-scen-idx]");
    if (!card) return;
    const key = `s:${card.dataset.scenIdx}`;
    ui.open.has(key) ? ui.open.delete(key) : ui.open.add(key);
    renderScenarios();
    highlight(card.dataset.sid, "scen");
  });
  $("test-list").addEventListener("click", (e) => {
    const tests = testList();
    let b = e.target.closest("[data-copy-test]");
    if (b) { copyText(pretty(tests[+b.dataset.copyTest]), b); return; }
    b = e.target.closest("[data-copy-script]");
    if (b) { copyText(sqlScript(tests[+b.dataset.copyScript]), b); return; }
    b = e.target.closest("[data-copy-sql]");
    if (b) { copyText(b.previousElementSibling.textContent, b); return; }
    if (e.target.closest("[data-stop]")) return;
    const card = e.target.closest("[data-test-idx]");
    if (!card) return;
    const key = `t:${card.dataset.testIdx}`;
    ui.open.has(key) ? ui.open.delete(key) : ui.open.add(key);
    renderTests();
    highlight(card.dataset.sid, "test");
  });

  // hover linking (matched by scenario_id, never by position)
  let lastScen = null, lastTest = null;
  $("scen-list").addEventListener("mouseover", (e) => {
    const card = e.target.closest("[data-scen-idx]");
    if (card && card.dataset.sid !== lastScen) { lastScen = card.dataset.sid; highlight(lastScen, "scen"); }
  });
  $("test-list").addEventListener("mouseover", (e) => {
    const card = e.target.closest("[data-test-idx]");
    if (card && card.dataset.sid !== lastTest) { lastTest = card.dataset.sid; highlight(lastTest, "test"); }
  });
  $("scen-list").addEventListener("mouseleave", () => { lastScen = null; clearHl(); });
  $("test-list").addEventListener("mouseleave", () => { lastTest = null; clearHl(); });
}

(async function init() {
  setupEvents();
  await refresh();
  if (S.job) poll(S.job.id, S.job.kind);  // a job was already running before the page loaded
})();
