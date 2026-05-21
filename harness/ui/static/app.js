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
  ["overview", "config", "capabilities", "playbooks", "schemas", "scenarios", "share"].forEach(
    n => $(`#builder-${n}`).classList.toggle("hidden", n !== name)
  );
  if (name === "config") refreshBuildConfig();
  if (name === "capabilities") refreshBuildCapabilities();
  if (name === "playbooks") refreshBuildPlaybooks();
  if (name === "schemas") refreshBuildSchemas();
  if (name === "scenarios") refreshBuildScenarios();
  if (name === "share") refreshBuildShare();
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

// ----- Share & reuse (config bundles) -----------------------------------

let BUNDLE_PREVIEW = null;   // {changes, totals, source, note, bundle}
let BUNDLE_INCLUDE = null;   // Set<string> of display paths to apply

async function refreshBuildShare() {
  // Pre-populate the export summary so the user sees what they'd ship.
  $("#bundle-export-status").textContent = "loading…";
  try {
    const r = await fetch("/api/bundle/export");
    if (!r.ok) throw new Error(r.status);
    const bundle = await r.json();
    LAST_BUNDLE = bundle;
    const cfgYes = bundle.config && bundle.config.yaml ? "yes" : "no";
    const summary = [
      `<li><code>.dev-loop/config.yaml</code>: ${cfgYes}</li>`,
      `<li><strong>${bundle.scenarios.length}</strong> scenario${bundle.scenarios.length !== 1 ? "s" : ""}</li>`,
      `<li><strong>${bundle.playbooks.length}</strong> playbook${bundle.playbooks.length !== 1 ? "s" : ""}</li>`,
    ];
    $("#bundle-export-summary").innerHTML = summary.join("");
    $("#bundle-export-status").textContent = "";
  } catch (e) {
    $("#bundle-export-status").textContent = "error: " + e;
  }
}

let LAST_BUNDLE = null;

$("#bundle-download").addEventListener("click", async () => {
  $("#bundle-export-status").textContent = "downloading…";
  try {
    const note = $("#bundle-note").value || "";
    // Hit the export endpoint directly (sets Content-Disposition for a
    // sensible save-as filename). If a note is provided we build the
    // download via Blob so we can attach it.
    let blob, filename;
    if (note) {
      const r = await fetch("/api/bundle/export");
      const bundle = await r.json();
      bundle.note = note;
      const text = JSON.stringify(bundle, null, 2) + "\n";
      blob = new Blob([text], {type: "application/json"});
      const repoName = (RESOLVED && RESOLVED.repo ? RESOLVED.repo.split(/[\\/]/).pop() : "repo");
      filename = `${repoName}-dev-loop-bundle.json`;
    } else {
      const r = await fetch("/api/bundle/export");
      blob = await r.blob();
      const cd = r.headers.get("Content-Disposition") || "";
      const m = cd.match(/filename="([^"]+)"/);
      filename = m ? m[1] : "dev-loop-bundle.json";
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 0);
    $("#bundle-export-status").textContent = "downloaded ✓";
  } catch (e) {
    $("#bundle-export-status").textContent = "error: " + e;
  }
});

$("#bundle-copy").addEventListener("click", async () => {
  try {
    const r = await fetch("/api/bundle/export");
    const text = await r.text();
    copyToClipboard(text, "Copied bundle JSON");
  } catch (e) {
    $("#bundle-export-status").textContent = "error: " + e;
  }
});

$("#bundle-file-input").addEventListener("change", async (e) => {
  const f = e.target.files && e.target.files[0];
  if (!f) return;
  const text = await f.text();
  $("#bundle-paste").value = text;
});

$("#bundle-clear").addEventListener("click", () => {
  $("#bundle-paste").value = "";
  $("#bundle-file-input").value = "";
  $("#bundle-preview").classList.add("hidden");
  $("#bundle-preview-status").textContent = "";
  $("#bundle-apply-report").innerHTML = "";
  BUNDLE_PREVIEW = null;
});

$("#bundle-preview-btn").addEventListener("click", async () => {
  const text = $("#bundle-paste").value.trim();
  if (!text) {
    $("#bundle-preview-status").textContent = "paste a bundle first";
    return;
  }
  let bundle;
  try { bundle = JSON.parse(text); }
  catch (e) {
    $("#bundle-preview-status").textContent = "invalid JSON: " + e.message;
    return;
  }
  $("#bundle-preview-status").textContent = "previewing…";
  try {
    const res = await fetch("/api/bundle/preview", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({bundle}),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({error: res.statusText}));
      $("#bundle-preview-status").textContent = "error: " + (err.error || res.status);
      return;
    }
    const preview = await res.json();
    BUNDLE_PREVIEW = {...preview, bundle};
    BUNDLE_INCLUDE = new Set(preview.changes
      .filter(c => c.status !== "identical")
      .map(c => c.path));
    renderBundlePreview();
    $("#bundle-preview-status").textContent = "";
  } catch (e) {
    $("#bundle-preview-status").textContent = "error: " + e;
  }
});

function renderBundlePreview() {
  const p = BUNDLE_PREVIEW;
  if (!p) { $("#bundle-preview").classList.add("hidden"); return; }
  $("#bundle-preview").classList.remove("hidden");
  const src = p.source || {};
  const srcBits = [];
  if (src.repo_name) srcBits.push(`from <strong>${escapeHtml(src.repo_name)}</strong>`);
  if (p.note) srcBits.push(`note: <em>${escapeHtml(p.note)}</em>`);
  $("#bundle-preview-source").innerHTML = srcBits.join(" · ") || "&nbsp;";
  const t = p.totals;
  $("#bundle-preview-totals").innerHTML = `
    <span class="pill pass">${t.new} new</span>
    <span class="pill fail">${t.conflict} conflict</span>
    <span class="pill">${t.identical} identical</span>
  `;
  const rows = p.changes.map(c => {
    const checked = BUNDLE_INCLUDE.has(c.path) ? "checked" : "";
    const disabled = c.status === "identical" ? "disabled" : "";
    const cls = c.status === "new" ? "pass"
              : c.status === "conflict" ? "fail" : "";
    return `<tr class="status-${c.status}">
      <td><input type="checkbox" data-bundle-path="${escapeAttr(c.path)}" ${checked} ${disabled}></td>
      <td><span class="pill ${cls}">${escapeHtml(c.status)}</span></td>
      <td><code>${escapeHtml(c.kind)}</code></td>
      <td><code>${escapeHtml(c.path)}</code></td>
    </tr>`;
  }).join("");
  $("#bundle-changes").innerHTML = rows ||
    '<tr><td colspan="4" class="muted">bundle has no items to apply</td></tr>';
  $$('#bundle-changes input[type=checkbox]').forEach(cb => {
    cb.addEventListener("change", () => {
      const path = cb.dataset.bundlePath;
      if (cb.checked) BUNDLE_INCLUDE.add(path);
      else BUNDLE_INCLUDE.delete(path);
    });
  });
}

$("#bundle-toggle-all").addEventListener("change", e => {
  if (!BUNDLE_PREVIEW) return;
  const want = e.target.checked;
  for (const c of BUNDLE_PREVIEW.changes) {
    if (c.status === "identical") continue;
    if (want) BUNDLE_INCLUDE.add(c.path);
    else BUNDLE_INCLUDE.delete(c.path);
  }
  renderBundlePreview();
});

$("#bundle-apply").addEventListener("click", async () => {
  if (!BUNDLE_PREVIEW) return;
  const include = Array.from(BUNDLE_INCLUDE);
  if (!include.length) {
    $("#bundle-apply-status").textContent = "nothing selected";
    return;
  }
  const on_conflict = $("#bundle-on-conflict").value;
  $("#bundle-apply-status").textContent = "applying…";
  try {
    const r = await fetch("/api/bundle/import", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        bundle: BUNDLE_PREVIEW.bundle,
        on_conflict, include,
      }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({error: r.statusText}));
      $("#bundle-apply-status").textContent = "error: " + (err.error || r.status);
      return;
    }
    const report = await r.json();
    $("#bundle-apply-status").textContent = "done ✓";
    const totals = Object.entries(report.totals)
      .map(([k, v]) => `<span class="pill">${escapeHtml(String(v))} ${escapeHtml(k)}</span>`)
      .join(" ");
    const rows = report.actions.map(a =>
      `<tr><td><code>${escapeHtml(a.action)}</code></td><td><code>${escapeHtml(a.path)}</code></td></tr>`
    ).join("");
    $("#bundle-apply-report").innerHTML = `
      <h4>Applied</h4>
      <div>${totals}</div>
      <table class="bundle-table">
        <thead><tr><th>action</th><th>path</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
    // Refresh dependent views so the user sees the imported config /
    // scenarios immediately without a page reload.
    await loadConfig();
    await loadOnboarding().catch(() => {});
  } catch (e) {
    $("#bundle-apply-status").textContent = "error: " + e;
  }
});

// Drag-and-drop a bundle JSON file onto the textarea.
(function bundleDragAndDrop() {
  const ta = $("#bundle-paste");
  if (!ta) return;
  ta.addEventListener("dragover", e => { e.preventDefault(); ta.classList.add("drop"); });
  ta.addEventListener("dragleave", () => ta.classList.remove("drop"));
  ta.addEventListener("drop", async e => {
    e.preventDefault();
    ta.classList.remove("drop");
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (!f) return;
    ta.value = await f.text();
  });
})();

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
let RUNS_CACHE = [];
let RUN_FILTER = "";

async function refreshAnalyzeTab() {
  const {runs} = await getJSON("/api/runs");
  RUNS_CACHE = runs;
  renderRunList();
}

function renderRunList() {
  const all = RUNS_CACHE;
  const q = RUN_FILTER.trim().toLowerCase();
  const filtered = q
    ? all.filter(r =>
        (r.task_id || "").toLowerCase().includes(q) ||
        (r.goal || "").toLowerCase().includes(q) ||
        (r.final_status || r.status || "").toLowerCase().includes(q))
    : all;

  const tally = $("#run-tally");
  if (!all.length) {
    tally.innerHTML = "";
  } else {
    const pass = all.filter(r => r.final_status === "passed").length;
    const fail = all.filter(r => isFailed(r.final_status || r.status)).length;
    const other = all.length - pass - fail;
    tally.innerHTML = `
      <span class="pill pass">${pass} passed</span>
      <span class="pill fail">${fail} failed</span>
      ${other ? `<span class="pill">${other} other</span>` : ""}
      ${q ? `<span class="muted">· showing ${filtered.length}/${all.length}</span>` : ""}
    `;
  }

  const list = $("#run-list");
  if (!filtered.length) {
    list.innerHTML = all.length
      ? '<li class="muted">no runs match the filter</li>'
      : '<li class="muted">no runs yet — kick one off in the Run tab.</li>';
    return;
  }

  // Group by UTC day (YYYY-MM-DD) for at-a-glance scanning.
  const groups = new Map();
  for (const r of filtered) {
    const day = (r.created_at_utc || "").slice(0, 10) || "—";
    if (!groups.has(day)) groups.set(day, []);
    groups.get(day).push(r);
  }
  const html = [];
  for (const [day, rs] of groups) {
    html.push(`<li class="day-header">${escapeHtml(day)}</li>`);
    for (const r of rs) {
      const status = r.final_status || r.status || "";
      const cls = pillClass(status);
      const dur = r.duration_seconds != null ? formatDuration(r.duration_seconds) : "";
      const goal = r.goal ? escapeHtml(truncate(r.goal, 110)) : '<span class="muted">(no contract)</span>';
      const sel = r.task_id === CURRENT_TASK ? "active" : "";
      html.push(`
        <li data-id="${escapeAttr(r.task_id)}" class="${sel}">
          <div class="run-row">
            <div class="top">
              <span class="pill ${cls}">${escapeHtml(status || "?")}</span>
              <strong>${escapeHtml(r.task_id)}</strong>
            </div>
            <div class="goal">${goal}</div>
            <div class="meta">
              ${r.iterations} iter
              ${dur ? "· " + dur : ""}
              ${r.created_at_utc ? "· " + escapeHtml(r.created_at_utc.slice(11, 19)) : ""}
            </div>
          </div>
        </li>`);
    }
  }
  list.innerHTML = html.join("");
  $$("#run-list li[data-id]").forEach(li =>
    li.addEventListener("click", () => selectRun(li.dataset.id)));
}

$("#run-filter").addEventListener("input", e => {
  RUN_FILTER = e.target.value || "";
  renderRunList();
});

$$('.subnav button[data-ad]').forEach(b => b.addEventListener("click", () => {
  selectSubview(b.dataset.ad);
  updateLocationHash();
}));

function selectSubview(name) {
  $$('.subnav button[data-ad]').forEach(x =>
    x.classList.toggle("active", x.dataset.ad === name));
  ["report", "iterations", "audit", "raw"].forEach(
    n => $(`#ad-${n}`).classList.toggle("hidden", n !== name));
}

function currentSubview() {
  const b = $$('.subnav button[data-ad].active')[0];
  return b ? b.dataset.ad : "report";
}

async function selectRun(taskId) {
  CURRENT_TASK = taskId;
  // Make sure we're on the Analyze tab even if linked from elsewhere.
  if ($("#tab-analyze").classList.contains("hidden")) showTab("analyze");
  $("#analyze-empty").classList.add("hidden");
  $("#analyze-detail").classList.remove("hidden");
  renderRunList();
  updateLocationHash();

  const tm = await getJSON("/api/runs/" + encodeURIComponent(taskId));
  const runMeta = RUNS_CACHE.find(r => r.task_id === taskId) || {};
  renderHero(tm, runMeta);

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
    const auditText = await getText(
      "/api/runs/" + encodeURIComponent(taskId) + "/audit");
    renderAuditLog(auditText);
  } catch (e) { renderAuditLog(""); }

  // iterations
  await loadIterations(taskId, tm, runMeta);
}

function renderHero(tm, runMeta) {
  const hero = $("#ad-hero");
  const status = tm.final_status || tm.status || "?";
  const cls = heroClass(status);
  const dur = runMeta.duration_seconds != null
    ? formatDuration(runMeta.duration_seconds) : null;
  const goal = (tm.task_contract || {}).implementation_goal
    || runMeta.goal || "(no task contract recorded)";
  const sel = tm.selected_iteration;
  const itersTotal = runMeta.iterations || 0;
  hero.className = "run-hero " + cls;
  hero.innerHTML = `
    <div class="verdict">
      <span class="big ${cls}">${escapeHtml(status.toUpperCase())}</span>
      <h2><code class="task-id">${escapeHtml(tm.task_id || "")}</code></h2>
      <span class="chips">
        ${sel != null ? `<span class="pill info">iter ${sel} selected</span>` : ""}
        ${itersTotal ? `<span class="pill">${itersTotal} iteration${itersTotal !== 1 ? "s" : ""}</span>` : ""}
        ${dur ? `<span class="pill">${escapeHtml(dur)}</span>` : ""}
      </span>
    </div>
    <div class="goal-line">
      <span class="label">Goal</span>${escapeHtml(goal)}
    </div>
    <div class="actions">
      <button class="secondary copy-btn" data-copy-task>Copy task id</button>
      <button class="secondary copy-btn" data-copy-link>Copy share link</button>
      <button class="secondary copy-btn" data-jump-iter>Jump to iterations</button>
    </div>`;
  hero.querySelector("[data-copy-task]").addEventListener("click",
    () => copyToClipboard(tm.task_id || "", "Copied task id"));
  hero.querySelector("[data-copy-link]").addEventListener("click",
    () => copyToClipboard(shareLink(), "Copied share link"));
  hero.querySelector("[data-jump-iter]").addEventListener("click", () => {
    selectSubview("iterations");
    updateLocationHash();
    $("#ad-iterations").scrollIntoView({behavior: "smooth", block: "start"});
  });
}

function heroClass(status) {
  if (status === "passed") return "pass";
  if (isFailed(status)) return "fail";
  if (status === "running" || status === "queued") return "warn";
  return "";
}

function pillClass(status) {
  if (status === "passed") return "pass";
  if (isFailed(status)) return "fail";
  if (status === "running" || status === "queued") return "warn";
  return "";
}

function isFailed(status) {
  return typeof status === "string" && status.startsWith("failed");
}

async function loadIterations(taskId, tm, runMeta) {
  const root = $("#ad-iterations");
  root.innerHTML = "";
  const total = runMeta.iterations
    || (tm.iterations && tm.iterations.length)
    || 0;
  if (!total) {
    root.innerHTML = '<p class="muted">No iterations were recorded for this run.</p>';
    return;
  }

  // Tiny clickable timeline up top.
  const sel = tm.selected_iteration;
  const iterDocs = [];
  for (let i = 1; i <= total; i++) {
    const im = await getJSON(`/api/runs/${encodeURIComponent(taskId)}/iteration/${i}`);
    const att = await getJSON(`/api/runs/${encodeURIComponent(taskId)}/iteration/${i}/attempts`);
    iterDocs.push({i, im, att});
  }

  const timeline = document.createElement("div");
  timeline.className = "timeline";
  timeline.innerHTML = iterDocs.map(({i, im}) => {
    const s = im.final_e2e_status === "passed" ? "pass" :
              im.final_e2e_status ? "fail" : "";
    const isSel = sel === i ? "selected" : "";
    return `<span class="step ${s} ${isSel}" data-jump-iter="${i}">
      <span class="dot"></span>iter ${i}
      ${sel === i ? '<span class="muted">·★</span>' : ""}
    </span>${i < iterDocs.length ? '<span class="arrow">→</span>' : ""}`;
  }).join("");
  root.appendChild(timeline);
  timeline.querySelectorAll("[data-jump-iter]").forEach(el =>
    el.addEventListener("click", () => {
      const i = el.dataset.jumpIter;
      const tgt = root.querySelector(`[data-iter-block="${i}"]`);
      if (tgt) tgt.scrollIntoView({behavior: "smooth", block: "start"});
    }));

  for (const {i, im, att} of iterDocs) {
    const block = document.createElement("div");
    block.className = "iter";
    block.setAttribute("data-iter-block", String(i));
    const final = im.final_e2e_status;
    const finalCls = final === "passed" ? "pass" : final ? "fail" : "";
    const isSel = sel === i;
    const summary = (im.agent_output || {}).summary || im.agent_summary || "";
    const hypothesis = (im.agent_output || {}).hypothesis || "";
    const changed = (im.code || {}).changed_files || [];
    const hash = (im.code || {}).patch_hash || "";
    block.innerHTML = `
      <h3>
        Iteration ${i}
        ${final ? `<span class="pill ${finalCls}">e2e: ${escapeHtml(final)}</span>` : ""}
        ${isSel ? '<span class="pill info">selected</span>' : ""}
      </h3>
      ${summary ? `<div class="iter-summary">${escapeHtml(summary)}</div>` : ""}
      ${hypothesis ? `<div class="muted"><em>${escapeHtml(hypothesis)}</em></div>` : ""}
      <div class="changed-files">
        ${changed.length
          ? changed.map(f => `<code>${escapeHtml(f)}</code>`).join("")
          : '<span class="muted">no files changed</span>'}
      </div>
      <div class="row">
        ${hash ? `<span class="muted" title="patch hash">hash <code>${escapeHtml(hash.slice(0, 10))}</code></span>` : ""}
        <button class="secondary" data-action="patch" data-iter="${i}">View patch</button>
        <span class="muted">${att.attempts.length} attempt${att.attempts.length !== 1 ? "s" : ""}</span>
      </div>
      <div data-iter-patch="${i}"></div>
      <div class="attempts-wrap">
        ${att.attempts.map((a, idx) => `
          <div class="attempt ${a.e2e_status === "passed" ? "pass" : "fail"}">
            <strong>${escapeHtml(a.name)}</strong>
            <span class="pill ${a.e2e_status === "passed" ? "pass" : a.e2e_status ? "fail" : ""}">
              e2e: ${escapeHtml(a.e2e_status || "—")}
            </span>
            <span class="muted">outcome: ${escapeHtml(a.outcome || "—")}</span>
            ${a.triage_action ? `<span class="pill warn">triage → ${escapeHtml(a.triage_action)}</span>` : ""}
            <button class="secondary" data-action="attempt"
              data-iter="${i}" data-attempt="${idx + 1}">drill in</button>
            <div class="attempt-body" data-attempt-detail="${i}-${idx + 1}"></div>
          </div>`).join("")}
      </div>
    `;
    root.appendChild(block);
  }
  $$('#ad-iterations button[data-action]').forEach(b => b.addEventListener("click", async () => {
    const i = b.dataset.iter;
    if (b.dataset.action === "patch") {
      const target = $(`[data-iter-patch="${i}"]`);
      if (target.dataset.loaded) {
        target.innerHTML = ""; target.dataset.loaded = "";
        b.textContent = "View patch";
        return;
      }
      const text = await getText(`/api/runs/${encodeURIComponent(taskId)}/iteration/${i}/patch`);
      target.innerHTML = renderDiff(text);
      target.dataset.loaded = "1";
      b.textContent = "Hide patch";
    } else if (b.dataset.action === "attempt") {
      const a = b.dataset.attempt;
      const tgt = $(`[data-attempt-detail="${i}-${a}"]`);
      if (tgt.dataset.loaded) {
        tgt.innerHTML = ""; tgt.dataset.loaded = "";
        b.textContent = "drill in";
        return;
      }
      const dump = await getJSON(
        `/api/runs/${encodeURIComponent(taskId)}/iteration/${i}/attempt/${a}`);
      const entries = Object.entries(dump.files || {});
      tgt.innerHTML = entries.length
        ? entries.map(([fn, content]) =>
            `<details class="file-section"><summary><code>${escapeHtml(fn)}</code></summary>
              <pre>${escapeHtml(
                typeof content === "string"
                  ? content : JSON.stringify(content, null, 2))}</pre>
            </details>`).join("")
        : '<p class="muted">no artifacts for this attempt</p>';
      tgt.dataset.loaded = "1";
      b.textContent = "hide";
    }
  }));
}

// Colorize a unified diff. Splits on lines and tags +/-/@@/file headers.
function renderDiff(text) {
  if (!text || !text.trim()) {
    return '<div class="diff"><div class="empty">no patch — this iteration changed no tracked files</div></div>';
  }
  const out = [];
  for (const raw of text.split(/\r?\n/)) {
    let cls = "";
    if (raw.startsWith("+++") || raw.startsWith("---") || raw.startsWith("diff ") || raw.startsWith("index ")) {
      cls = "meta";
    } else if (raw.startsWith("@@")) {
      cls = "hunk";
    } else if (raw.startsWith("+")) {
      cls = "add";
    } else if (raw.startsWith("-")) {
      cls = "del";
    }
    out.push(`<span class="line ${cls}">${escapeHtml(raw) || "&nbsp;"}</span>`);
  }
  return `<div class="diff"><pre>${out.join("")}</pre></div>`;
}

// Parse a JSONL audit log into a scannable table.
let AUDIT_ENTRIES = [];
function renderAuditLog(text) {
  AUDIT_ENTRIES = [];
  if (text) {
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) continue;
      try { AUDIT_ENTRIES.push(JSON.parse(line)); }
      catch (_) { AUDIT_ENTRIES.push({_raw: line}); }
    }
  }
  const filter = $("#audit-filter");
  if (filter) filter.value = "";
  renderAuditTable();
}
function renderAuditTable() {
  const q = ($("#audit-filter")?.value || "").trim().toLowerCase();
  const entries = q
    ? AUDIT_ENTRIES.filter(e =>
        ((e.capability || "") + " " + (e.status || "") + " " +
         (e.error || "") + " " + JSON.stringify(e.params || {}))
          .toLowerCase().includes(q))
    : AUDIT_ENTRIES;
  $("#audit-count").textContent = AUDIT_ENTRIES.length
    ? `${entries.length} / ${AUDIT_ENTRIES.length} call${AUDIT_ENTRIES.length !== 1 ? "s" : ""}`
    : "no audit entries yet";
  const wrap = $("#audit-table-wrap");
  if (!entries.length) {
    wrap.innerHTML = AUDIT_ENTRIES.length
      ? '<p class="muted">No entries match the filter.</p>'
      : '<p class="muted">No capability invocations were recorded.</p>';
    return;
  }
  const rows = entries.map(e => {
    if (e._raw) {
      return `<tr class="row-error"><td colspan="4"><code>${escapeHtml(e._raw)}</code></td></tr>`;
    }
    const ok = e.status === "ok" && !e.error;
    const params = e.params && Object.keys(e.params).length
      ? `<details><summary class="muted">params</summary><pre>${escapeHtml(JSON.stringify(e.params, null, 2))}</pre></details>`
      : "";
    return `<tr class="${ok ? "row-ok" : "row-error"}">
      <td class="ts">${escapeHtml(e.ts_utc || "")}</td>
      <td><span class="cap">${escapeHtml(e.capability || "?")}</span>
        ${e.from_agent ? '<span class="from-agent">· from agent</span>' : ""}</td>
      <td><span class="pill ${ok ? "pass" : "fail"}">${escapeHtml(e.status || "?")}</span>
        ${e.error ? `<div class="muted">${escapeHtml(e.error)}</div>` : ""}</td>
      <td>${params}</td>
    </tr>`;
  }).join("");
  wrap.innerHTML = `
    <table class="audit-table">
      <thead><tr><th>time</th><th>capability</th><th>status</th><th>params</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}
document.addEventListener("input", e => {
  if (e.target && e.target.id === "audit-filter") renderAuditTable();
});

// ----- helpers: clipboard, hash-routing, formatting --------------------

function copyToClipboard(text, msg) {
  const fallback = () => {
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); } catch (_) {}
    document.body.removeChild(ta);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => toast(msg || "Copied"),
                                             () => { fallback(); toast(msg || "Copied"); });
  } else {
    fallback();
    toast(msg || "Copied");
  }
}

let TOAST_TIMER = null;
function toast(msg) {
  let el = $(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  requestAnimationFrame(() => el.classList.add("show"));
  if (TOAST_TIMER) clearTimeout(TOAST_TIMER);
  TOAST_TIMER = setTimeout(() => el.classList.remove("show"), 1400);
}

function shareLink() {
  return location.origin + location.pathname + "#/run/" +
    encodeURIComponent(CURRENT_TASK || "") + "/" + currentSubview();
}

function updateLocationHash() {
  if (!CURRENT_TASK) return;
  const target = "#/run/" + encodeURIComponent(CURRENT_TASK) + "/" + currentSubview();
  if (location.hash !== target) {
    history.replaceState(null, "", target);
  }
}

async function consumeLocationHash() {
  const m = location.hash.match(/^#\/run\/([^/]+)(?:\/([a-z]+))?/);
  if (!m) return false;
  const taskId = decodeURIComponent(m[1]);
  const sub = m[2];
  showTab("analyze");
  // Make sure the runs list is populated before we try to highlight.
  await refreshAnalyzeTab();
  if (sub) selectSubview(sub);
  await selectRun(taskId);
  return true;
}
window.addEventListener("hashchange", consumeLocationHash);

function truncate(s, n) {
  if (!s) return "";
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}
function formatDuration(sec) {
  if (sec < 1) return "<1s";
  if (sec < 60) return sec + "s";
  if (sec < 3600) return Math.floor(sec / 60) + "m " + (sec % 60) + "s";
  return Math.floor(sec / 3600) + "h " + Math.floor((sec % 3600) / 60) + "m";
}
function escapeAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
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
(async () => {
  // If we landed here via a shared link like ``#/run/<task-id>/iterations``
  // jump straight there; otherwise fall back to the Build tab.
  const consumed = await consumeLocationHash().catch(() => false);
  if (!consumed) showTab("build");
})();
