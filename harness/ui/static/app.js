// dev-loop SPA — vanilla JS, no build step.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}
async function getText(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.text();
}
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(`${url}: ${r.status} ${await r.text()}`);
  return r.json();
}
async function postText(url, text) {
  const r = await fetch(url, {method: "POST", body: text});
  if (!r.ok) throw new Error(`${url}: ${r.status} ${await r.text()}`);
  return r.json();
}

// ----- top-level tab nav -------------------------------------------------

function showTab(name) {
  $$(".tab").forEach(t => t.classList.add("hidden"));
  $(`#tab-${name}`).classList.remove("hidden");
  $$("nav button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  if (name === "run") refreshRunTab();
  if (name === "analyze") refreshAnalyzeTab();
  if (name === "build") refreshBuildOverview();
}

$$("nav button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ----- repo pill ---------------------------------------------------------

let RESOLVED = null;
async function loadConfig() {
  RESOLVED = await getJSON("/api/config");
  $("#repo-pill").textContent = RESOLVED.repo;
}

// ----- onboarding -------------------------------------------------------

let ONBOARDING = null;
// Declared early so onboarding's "Try demo" can launch into the Run tab.
let CURRENT_JOB_ID = null;

async function loadOnboarding() {
  ONBOARDING = await getJSON("/api/onboarding");
  renderOnboarding();
}

function renderOnboarding() {
  const o = ONBOARDING;
  if (!o) return;
  const panel = $("#onboarding-panel");
  const title = $("#onboarding-title");
  const subtitle = $("#onboarding-subtitle");
  const list = $("#onboarding-checklist");
  const demoBtn = $("#onboarding-demo");

  list.innerHTML = o.steps.map(s => `
    <li class="step ${s.done ? "done" : "pending"}">
      <span class="step-mark">${s.done ? "✓" : "•"}</span>
      <span class="step-title">${escapeHtml(s.title)}</span>
      <span class="step-detail muted">${escapeHtml(s.detail || "")}</span>
    </li>`).join("");

  if (o.is_complete) {
    title.textContent = `${o.repo_name} is set up`;
    subtitle.textContent = "All four steps done — jump to Run or Analyze any time.";
    $("#onboarding-init").textContent = "Re-run setup";
    $("#onboarding-init").classList.add("secondary");
  } else if (o.config_exists) {
    title.textContent = `Finish setting up ${o.repo_name}`;
    subtitle.textContent = "A couple more steps and you're ready to run.";
    $("#onboarding-init").classList.remove("secondary");
  } else {
    title.textContent = `Welcome to dev-loop — ${o.repo_name}`;
    subtitle.textContent = "Two clicks and this repo is wired up.";
    $("#onboarding-init").classList.remove("secondary");
  }

  demoBtn.disabled = !o.starter_installed;
  demoBtn.title = o.starter_installed
    ? "Run the bundled hello-dev-loop scenario end-to-end"
    : "Install the starter scenario first";

  // Only show the panel while there's something useful to do or to
  // celebrate; once the user has runs recorded we get out of the way.
  const shouldShow = !o.is_complete || o.run_count === 0;
  panel.classList.toggle("hidden", !shouldShow);
}

async function runOnboardingInit() {
  const installStarter = $("#onboarding-starter").checked;
  $("#onboarding-status").textContent = "setting up…";
  try {
    const res = await postJSON("/api/init", {install_starter: installStarter});
    $("#onboarding-status").textContent = "done ✓";
    await loadConfig();
    await loadOnboarding();
    // Refresh dependent panels if visible.
    if (!$("#builder-config").classList.contains("hidden")) refreshBuildConfig();
    if (!$("#tab-run").classList.contains("hidden")) refreshRunTab();
  } catch (e) {
    $("#onboarding-status").textContent = "error: " + e;
  }
}

async function runOnboardingDemo() {
  if (!ONBOARDING || !ONBOARDING.starter_installed) return;
  $("#onboarding-status").textContent = "launching demo…";
  try {
    const {job_id} = await postJSON("/api/implement", {
      request: "Verify the harness wiring with the bundled starter scenario.",
      provider: "replay",
      replay_scenario: ONBOARDING.starter_scenario,
    });
    CURRENT_JOB_ID = job_id;
    showTab("run");
    pollJob();
    $("#onboarding-status").textContent = "";
  } catch (e) {
    $("#onboarding-status").textContent = "error: " + e;
  }
}

$("#onboarding-init").addEventListener("click", runOnboardingInit);
$("#onboarding-demo").addEventListener("click", runOnboardingDemo);

(async () => {
  try { await loadConfig(); }
  catch (e) { $("#repo-pill").textContent = "error: " + e; return; }
  try { await loadOnboarding(); }
  catch (e) { /* non-fatal */ }
})();

// ----- BUILD tab --------------------------------------------------------

$$(".builder-nav a").forEach(a => a.addEventListener("click", e => {
  e.preventDefault();
  selectBuilder(a.dataset.builder);
}));
$$('[data-jump]').forEach(a => a.addEventListener("click", e => {
  e.preventDefault();
  const [section, target] = a.dataset.jump.split(":");
  selectBuilder(section);
  if (target && section === "playbooks") $("#pb-select").value = target, loadPlaybook(target);
  if (target && section === "schemas") $("#sch-select").value = target, loadSchema(target);
}));

function selectBuilder(name) {
  $$(".builder-nav a").forEach(a => a.classList.toggle("active", a.dataset.builder === name));
  ["overview", "config", "capabilities", "playbooks", "schemas", "scenarios"].forEach(
    n => $(`#builder-${n}`).classList.toggle("hidden", n !== name)
  );
  if (name === "config") refreshBuildConfig();
  if (name === "capabilities") refreshBuildCapabilities();
  if (name === "playbooks") refreshBuildPlaybooks();
  if (name === "schemas") refreshBuildSchemas();
  if (name === "scenarios") refreshBuildScenarios();
}

function refreshBuildOverview() { /* static */ }

async function refreshBuildConfig() {
  $("#cfg-file").textContent = RESOLVED ? RESOLVED.config_file : "";
  const text = await getText("/api/config/raw");
  $("#cfg-textarea").value = text;
  $("#cfg-resolved").textContent = JSON.stringify(RESOLVED, null, 2);
}
$("#cfg-save").addEventListener("click", async () => {
  const text = $("#cfg-textarea").value;
  $("#cfg-status").textContent = "saving…";
  try {
    await postText("/api/config/raw", text);
    await loadConfig();
    $("#cfg-status").textContent = "saved ✓";
    $("#cfg-resolved").textContent = JSON.stringify(RESOLVED, null, 2);
  } catch (e) {
    $("#cfg-status").textContent = "error: " + e;
  }
});
$("#cfg-reload").addEventListener("click", refreshBuildConfig);

async function refreshBuildCapabilities() {
  const tbody = $("#cap-table tbody");
  tbody.innerHTML = "";
  const {capabilities} = await getJSON("/api/capabilities");
  for (const c of capabilities) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><code>${c.name}</code></td>
      <td>${c.category}</td>
      <td>${c.agent_requestable ? "yes" : "—"}</td>
      <td>${c.timeout_seconds}s</td>
      <td><code>${escapeHtml(JSON.stringify(c.forced_params))}</code></td>`;
    tbody.appendChild(tr);
  }
}

async function refreshBuildPlaybooks() {
  const {playbooks} = await getJSON("/api/playbooks");
  const sel = $("#pb-select");
  sel.innerHTML = playbooks.map(p => `<option value="${p}">${p}</option>`).join("");
  if (playbooks.length) await loadPlaybook(playbooks[0]);
}
async function loadPlaybook(name) {
  $("#pb-textarea").value = await getText("/api/playbooks/" + encodeURIComponent(name));
  $("#pb-status").textContent = "";
}
$("#pb-select").addEventListener("change", e => loadPlaybook(e.target.value));
$("#pb-save").addEventListener("click", async () => {
  const name = $("#pb-select").value;
  $("#pb-status").textContent = "saving…";
  try {
    await postText("/api/playbooks/" + encodeURIComponent(name), $("#pb-textarea").value);
    $("#pb-status").textContent = "saved ✓";
  } catch (e) { $("#pb-status").textContent = "error: " + e; }
});

async function refreshBuildSchemas() {
  const {schemas} = await getJSON("/api/schemas");
  const sel = $("#sch-select");
  sel.innerHTML = schemas.map(p => `<option value="${p}">${p}</option>`).join("");
  if (schemas.length) await loadSchema(schemas[0]);
}
async function loadSchema(name) {
  $("#sch-content").textContent = await getText("/api/schemas/" + encodeURIComponent(name));
}
$("#sch-select").addEventListener("change", e => loadSchema(e.target.value));

async function refreshBuildScenarios() {
  const {scenarios} = await getJSON("/api/scenarios");
  const sel = $("#sc-select");
  sel.innerHTML = scenarios.map(s => `<option value="${s.name}">${s.name}</option>`).join("");
  if (scenarios.length) await loadScenario(scenarios[0].name);
}
async function loadScenario(name) {
  const data = await getJSON("/api/scenarios/" + encodeURIComponent(name));
  const detail = $("#sc-detail");
  detail.innerHTML = `
    <p class="muted">${data.path}</p>
    <h3>Files</h3>
    <ul id="sc-files"></ul>
    <h3>Edit</h3>
    <div class="row">
      <select id="sc-file-select">${data.files.filter(f => !f.name.endsWith("/"))
        .map(f => `<option>${f.name}</option>`).join("")}</select>
      <button id="sc-save">Save</button>
      <span id="sc-status"></span>
    </div>
    <textarea id="sc-textarea" rows="14" spellcheck="false"></textarea>`;
  $("#sc-files").innerHTML = data.files.map(f =>
    `<li><code>${f.name}</code> <span class="muted">${f.size != null ? f.size + " B" : ""}</span></li>`
  ).join("");
  async function loadFile(fn) {
    $("#sc-textarea").value = await getText(
      `/api/scenarios/${encodeURIComponent(name)}/file/${encodeURIComponent(fn)}`);
  }
  $("#sc-file-select").addEventListener("change", e => loadFile(e.target.value));
  if (data.files.length) await loadFile(data.files.find(f => !f.name.endsWith("/")).name);
  $("#sc-save").addEventListener("click", async () => {
    const fn = $("#sc-file-select").value;
    $("#sc-status").textContent = "saving…";
    try {
      await postText(
        `/api/scenarios/${encodeURIComponent(name)}/file/${encodeURIComponent(fn)}`,
        $("#sc-textarea").value);
      $("#sc-status").textContent = "saved ✓";
    } catch (e) { $("#sc-status").textContent = "error: " + e; }
  });
}
$("#sc-select").addEventListener("change", e => loadScenario(e.target.value));
$("#sc-new").addEventListener("click", async () => {
  const name = prompt("Scenario name (slug, e.g. encoder-oom-001):");
  if (!name) return;
  const req = prompt("One-paragraph task request:", "Describe the request.");
  if (req == null) return;
  await postJSON("/api/scenarios", {name, task_request: req});
  await refreshBuildScenarios();
  $("#sc-select").value = name;
  loadScenario(name);
});

// ----- RUN tab ----------------------------------------------------------

async function refreshRunTab() {
  await refreshScenariosForLaunch();
  await refreshJobList();
}
async function refreshScenariosForLaunch() {
  const {scenarios} = await getJSON("/api/scenarios");
  $("#impl-scenario").innerHTML = scenarios.map(
    s => `<option value="${s.name}">${s.name}</option>`).join("");
  $("#impl-scenario-row").style.display =
    $("#impl-provider").value === "replay" ? "" : "none";
  const empty = scenarios.length === 0;
  $("#impl-scenario").classList.toggle("hidden", empty);
  $("#impl-scenario-empty").classList.toggle("hidden", !empty);
  // Block launch when in replay mode with no scenarios.
  const replayWithoutScenario =
    $("#impl-provider").value === "replay" && empty;
  $("#impl-go").disabled = replayWithoutScenario;
  $("#impl-go").title = replayWithoutScenario
    ? "Install a scenario first (see message above)" : "";
}

document.addEventListener("click", async (e) => {
  const t = e.target;
  if (t && t.id === "impl-install-starter") {
    e.preventDefault();
    await postJSON("/api/init", {install_starter: true});
    await loadOnboarding();
    await refreshScenariosForLaunch();
  }
});
$("#impl-provider").addEventListener("change", () => {
  $("#impl-scenario-row").style.display =
    $("#impl-provider").value === "replay" ? "" : "none";
});
$("#impl-go").addEventListener("click", async () => {
  const body = {
    request: $("#impl-request").value.trim(),
    provider: $("#impl-provider").value,
  };
  if (!body.request) { alert("write a request first"); return; }
  if (body.provider === "replay") {
    body.replay_scenario = $("#impl-scenario").value;
    if (!body.replay_scenario) {
      alert("install or pick a replay scenario first");
      return;
    }
  }
  const mi = $("#impl-maxiter").value;
  if (mi) body.max_iterations = parseInt(mi, 10);
  const {job_id} = await postJSON("/api/implement", body);
  CURRENT_JOB_ID = job_id;
  pollJob();
});
async function refreshJobList() {
  const {jobs} = await getJSON("/api/runs");
  $("#job-list").innerHTML = jobs.length
    ? jobs.map(j => `
        <li data-id="${j.id}">
          <strong>${j.id}</strong>
          <span class="status-${j.status}">[${j.status}]</span>
          <div class="meta">${j.request.provider} · ${j.request.request.slice(0, 60)}…</div>
        </li>`).join("")
    : '<li class="muted">no jobs yet</li>';
  $$("#job-list li[data-id]").forEach(li =>
    li.addEventListener("click", () => { CURRENT_JOB_ID = li.dataset.id; pollJob(); }));
}
async function pollJob() {
  if (!CURRENT_JOB_ID) return;
  let done = false;
  while (!done) {
    let job;
    try { job = await getJSON("/api/jobs/" + CURRENT_JOB_ID); }
    catch (e) { $("#job-log").textContent = "error: " + e; return; }
    $("#job-meta").innerHTML = `
      <span class="status-${job.status}">${job.status}</span>
      · ${job.request.provider}
      · task_id: ${job.task_id || "(pending)"}
    `;
    $("#job-log").textContent = job.log || "(waiting…)";
    done = ["completed", "failed", "errored"].includes(job.status);
    if (!done) await new Promise(r => setTimeout(r, 800));
    else {
      await refreshJobList();
      // A successful first run flips the onboarding checklist.
      try { await loadOnboarding(); } catch (_) {}
    }
  }
}

// ----- ANALYZE tab ------------------------------------------------------

let CURRENT_TASK = null;

async function refreshAnalyzeTab() {
  const {runs} = await getJSON("/api/runs");
  $("#run-list").innerHTML = runs.length
    ? runs.map(r => `
        <li data-id="${r.task_id}">
          <strong>${r.task_id}</strong>
          <span class="status-${r.final_status || r.status || ""}">[${r.final_status || r.status || ""}]</span>
          <div class="meta">${r.iterations} iter · ${r.updated_at_utc || r.created_at_utc || ""}</div>
        </li>`).join("")
    : '<li class="muted">no runs yet</li>';
  $$("#run-list li[data-id]").forEach(li =>
    li.addEventListener("click", () => selectRun(li.dataset.id)));
}

$$('.subnav button[data-ad]').forEach(b => b.addEventListener("click", () => {
  $$('.subnav button[data-ad]').forEach(x => x.classList.toggle("active", x === b));
  ["report", "iterations", "audit", "raw"].forEach(
    n => $(`#ad-${n}`).classList.toggle("hidden", n !== b.dataset.ad));
}));

async function selectRun(taskId) {
  CURRENT_TASK = taskId;
  $("#analyze-empty").classList.add("hidden");
  $("#analyze-detail").classList.remove("hidden");
  $("#ad-title").textContent = taskId;

  const tm = await getJSON("/api/runs/" + encodeURIComponent(taskId));
  $("#ad-summary").innerHTML = `
    <dl class="kv">
      <dt>status</dt><dd><span class="status-${tm.final_status || tm.status}">${tm.final_status || tm.status}</span></dd>
      <dt>selected iter</dt><dd>${tm.selected_iteration ?? "—"}</dd>
      <dt>created</dt><dd>${tm.created_at_utc || ""}</dd>
      <dt>updated</dt><dd>${tm.updated_at_utc || ""}</dd>
    </dl>`;

  // report
  try {
    const md = await getText("/api/runs/" + encodeURIComponent(taskId) + "/report");
    $("#ad-report").innerHTML = `<div class="md">${renderMarkdown(md)}</div>`;
  } catch (e) { $("#ad-report").textContent = "report unavailable: " + e; }

  // raw report
  try {
    const raw = await getJSON("/api/runs/" + encodeURIComponent(taskId) + "/report.json");
    $("#raw-pre").textContent = JSON.stringify(raw, null, 2);
  } catch (e) { $("#raw-pre").textContent = "no structured report"; }

  // audit
  try {
    $("#audit-pre").textContent = await getText(
      "/api/runs/" + encodeURIComponent(taskId) + "/audit");
  } catch (e) { $("#audit-pre").textContent = ""; }

  // iterations
  await loadIterations(taskId, tm);
}

async function loadIterations(taskId, tm) {
  const root = $("#ad-iterations");
  root.innerHTML = "";
  const total = await iterationCount(taskId);
  for (let i = 1; i <= total; i++) {
    const im = await getJSON(`/api/runs/${encodeURIComponent(taskId)}/iteration/${i}`);
    const att = await getJSON(`/api/runs/${encodeURIComponent(taskId)}/iteration/${i}/attempts`);
    const block = document.createElement("div");
    block.className = "iter";
    block.innerHTML = `
      <h3>Iteration ${i} <span class="muted">— ${im.final_e2e_status || "(no e2e)"}</span></h3>
      <dl class="kv">
        <dt>patch hash</dt><dd><code>${(im.code || {}).patch_hash || "—"}</code></dd>
        <dt>changed files</dt><dd>${(im.code || {}).changed_files?.map(f=>`<code>${f}</code>`).join(", ") || "—"}</dd>
      </dl>
      <div class="row">
        <button class="secondary" data-action="patch" data-iter="${i}">View patch</button>
      </div>
      <div data-iter-patch="${i}"></div>
      <h4>Attempts</h4>
      ${att.attempts.map((a, idx) => `
        <div class="attempt ${a.e2e_status === "passed" ? "pass" : "fail"}">
          <strong>${a.name}</strong>
          <span class="muted">${a.outcome}</span>
          · e2e: <span class="status-${a.e2e_status}">${a.e2e_status || "—"}</span>
          ${a.triage_action ? `· triage → <code>${a.triage_action}</code>` : ""}
          <button class="secondary" data-action="attempt"
            data-iter="${i}" data-attempt="${idx + 1}">drill in</button>
          <div data-attempt-detail="${i}-${idx + 1}"></div>
        </div>`).join("")}
    `;
    root.appendChild(block);
  }
  $$('#ad-iterations button[data-action]').forEach(b => b.addEventListener("click", async () => {
    const i = b.dataset.iter;
    if (b.dataset.action === "patch") {
      const target = $(`[data-iter-patch="${i}"]`);
      if (target.dataset.loaded) { target.innerHTML = ""; target.dataset.loaded = ""; return; }
      const text = await getText(`/api/runs/${encodeURIComponent(taskId)}/iteration/${i}/patch`);
      target.innerHTML = `<pre>${escapeHtml(text)}</pre>`;
      target.dataset.loaded = "1";
    } else if (b.dataset.action === "attempt") {
      const a = b.dataset.attempt;
      const tgt = $(`[data-attempt-detail="${i}-${a}"]`);
      if (tgt.dataset.loaded) { tgt.innerHTML = ""; tgt.dataset.loaded = ""; return; }
      const dump = await getJSON(
        `/api/runs/${encodeURIComponent(taskId)}/iteration/${i}/attempt/${a}`);
      const sections = Object.entries(dump.files || {}).map(([fn, content]) =>
        `<details><summary><code>${fn}</code></summary><pre>${
          escapeHtml(typeof content === "string" ? content : JSON.stringify(content, null, 2))}</pre></details>`
      ).join("");
      tgt.innerHTML = sections;
      tgt.dataset.loaded = "1";
    }
  }));
}

async function iterationCount(taskId) {
  const {runs} = await getJSON("/api/runs");
  return (runs.find(r => r.task_id === taskId) || {}).iterations || 0;
}

// ----- helpers ----------------------------------------------------------

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Tiny Markdown renderer: headers, lists, code blocks, inline code, blockquotes, paragraphs.
function renderMarkdown(src) {
  const lines = src.split(/\r?\n/);
  const out = [];
  let inCode = false;
  let inList = null; // "ul" | "ol"
  function closeList() { if (inList) { out.push(`</${inList}>`); inList = null; } }
  for (let raw of lines) {
    if (raw.startsWith("```")) {
      closeList();
      if (!inCode) { out.push("<pre><code>"); inCode = true; }
      else { out.push("</code></pre>"); inCode = false; }
      continue;
    }
    if (inCode) { out.push(escapeHtml(raw)); continue; }
    let line = raw;
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.+)$/))) {
      closeList();
      out.push(`<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`);
      continue;
    }
    if ((m = line.match(/^>\s?(.*)$/))) {
      closeList();
      out.push(`<blockquote>${inline(m[1])}</blockquote>`); continue;
    }
    if ((m = line.match(/^(\s*)-\s+(.+)$/))) {
      if (inList !== "ul") { closeList(); out.push("<ul>"); inList = "ul"; }
      out.push(`<li>${inline(m[2])}</li>`); continue;
    }
    if ((m = line.match(/^(\s*)\d+\.\s+(.+)$/))) {
      if (inList !== "ol") { closeList(); out.push("<ol>"); inList = "ol"; }
      out.push(`<li>${inline(m[2])}</li>`); continue;
    }
    if (line.trim() === "") { closeList(); out.push(""); continue; }
    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  if (inCode) out.push("</code></pre>");
  return out.join("\n");
}
function inline(s) {
  return escapeHtml(s)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
}

// initial state
showTab("build");
