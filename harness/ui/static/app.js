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

  renderOnboardingDoctor(o.diagnostics);

  // Only show the panel while there's something useful to do or to
  // celebrate; once the user has runs recorded we get out of the way.
  const shouldShow = !o.is_complete || o.run_count === 0;
  panel.classList.toggle("hidden", !shouldShow);
}

// Render the doctor block. `diag` is `{checks: [...], summary: {...}}` or
// nullish (older servers). Mirrors `dev-loop doctor` so the UI and CLI
// agree byte-for-byte on what's broken.
function renderOnboardingDoctor(diag) {
  const wrap = $("#onboarding-doctor-wrap");
  const list = $("#onboarding-doctor-list");
  const summaryEl = $("#onboarding-doctor-summary");
  if (!diag || !Array.isArray(diag.checks)) {
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  const s = diag.summary || {};
  const ok = s.ok || 0, warn = s.warning || 0, err = s.error || 0;
  const tone = err ? "err" : (warn ? "warn" : "ok");
  summaryEl.className = `doctor-pill doctor-pill-${tone}`;
  summaryEl.textContent = err
    ? `${err} error${err === 1 ? "" : "s"}, ${warn} warn, ${ok} ok`
    : warn
      ? `${warn} warning${warn === 1 ? "" : "s"}, ${ok} ok`
      : `all ${ok} check${ok === 1 ? "" : "s"} ok`;
  list.innerHTML = diag.checks.map(c => `
    <li class="doctor-check doctor-${escapeHtml(c.level)}">
      <span class="doctor-mark" aria-hidden="true">${{
        "ok": "✓", "warning": "!", "error": "x",
      }[c.level] || "?"}</span>
      <span class="doctor-body">
        <span class="doctor-label">${escapeHtml(c.label)}</span>
        <span class="doctor-msg">${escapeHtml(c.message)}</span>
        ${c.hint ? `<span class="doctor-hint">hint: ${escapeHtml(c.hint)}</span>` : ""}
      </span>
    </li>`).join("");
  // Auto-open the details when something needs attention so the user
  // doesn't have to hunt for the warning.
  if (err || warn) wrap.open = true;
}

async function refreshOnboardingDoctor() {
  const status = $("#onboarding-doctor-status");
  status.textContent = "checking…";
  try {
    const diag = await getJSON("/api/doctor");
    renderOnboardingDoctor(diag);
    status.textContent = "";
  } catch (e) {
    status.textContent = "error: " + e;
  }
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
$("#onboarding-doctor-refresh").addEventListener("click", refreshOnboardingDoctor);

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

// Config form state. Holds the last text the server gave us (so "Revert"
// goes back to that exact bytes), the current form values, and a debounce
// handle for live validation as the user types.
const CFG_FORM_FIELDS = [
  "default_provider", "runs_dir", "scenarios_dir",
  "sandbox_dir", "clean_workspace_dir", "notes",
  "max_code_iterations", "max_validation_attempts_per_iteration",
  "max_diagnostic_rounds_per_failure", "max_total_wall_clock_minutes",
];
const CFG_INT_FIELDS = new Set([
  "max_code_iterations", "max_validation_attempts_per_iteration",
  "max_diagnostic_rounds_per_failure", "max_total_wall_clock_minutes",
]);
let CFG_SAVED_TEXT = "";        // bytes currently on disk (per last load/save)
let CFG_SAVED_FORM = null;      // {field: value} matching CFG_SAVED_TEXT
let CFG_MODE = "form";          // "form" | "yaml"
let CFG_VALIDATE_TIMER = null;
let CFG_LAST_VALIDATION = null;

async function refreshBuildConfig() {
  $("#cfg-file").textContent = RESOLVED ? RESOLVED.config_file : "";
  const text = await getText("/api/config/raw");
  CFG_SAVED_TEXT = text;
  $("#cfg-textarea").value = text;
  // Ask the server to parse the YAML into a form dict so we don't have to
  // ship a YAML parser to the browser.
  const v = await postText("/api/config/validate", text);
  CFG_SAVED_FORM = v.form || {};
  populateConfigForm(CFG_SAVED_FORM);
  renderCfgValidation(v);
  $("#cfg-resolved").textContent = JSON.stringify(v.resolved, null, 2);
  renderResolvedPretty(v.resolved);
  setCfgDirty(false);
  $("#cfg-status").textContent = "";
}

function populateConfigForm(form) {
  for (const f of CFG_FORM_FIELDS) {
    const el = $(`#cfgf-${f}`);
    if (!el) continue;
    const v = form[f];
    const s = (v === undefined || v === null) ? "" : String(v);
    // If a <select> is given a value that isn't one of its options, inject
    // a transient option so the user can see the actual saved value rather
    // than the browser silently snapping to option[0].
    if (el.tagName === "SELECT" && s &&
        !Array.from(el.options).some(o => o.value === s)) {
      const opt = document.createElement("option");
      opt.value = s; opt.textContent = `${s} (not built-in)`;
      opt.dataset.custom = "1";
      el.appendChild(opt);
    }
    el.value = s;
  }
}

function readConfigForm() {
  const out = {};
  for (const f of CFG_FORM_FIELDS) {
    const el = $(`#cfgf-${f}`);
    if (!el) continue;
    const raw = el.value;
    if (CFG_INT_FIELDS.has(f)) {
      if (raw === "" || raw == null) continue;
      const n = Number(raw);
      out[f] = Number.isFinite(n) ? n : raw;
    } else {
      out[f] = raw;
    }
  }
  return out;
}

function setCfgDirty(dirty) {
  const pill = $("#cfg-dirty-pill");
  if (!pill) return;
  pill.textContent = dirty ? "● unsaved changes" : "";
  pill.classList.toggle("dirty", !!dirty);
}

function currentCfgYaml() {
  return CFG_MODE === "yaml" ? $("#cfg-textarea").value : null;
}

async function validateCfgNow() {
  let payload, text;
  if (CFG_MODE === "yaml") {
    text = $("#cfg-textarea").value;
    payload = await postText("/api/config/validate", text);
  } else {
    const form = readConfigForm();
    payload = await postJSON("/api/config/form", form);
    // The server returns canonical YAML — keep the raw editor in sync so
    // flipping tabs is always a no-op (no surprise reformat).
    if (payload.yaml != null) $("#cfg-textarea").value = payload.yaml;
    text = payload.yaml || "";
  }
  CFG_LAST_VALIDATION = payload;
  renderCfgValidation(payload);
  renderResolvedPretty(payload.resolved);
  $("#cfg-resolved").textContent = JSON.stringify(payload.resolved, null, 2);
  setCfgDirty(text !== CFG_SAVED_TEXT);
  return payload;
}

function scheduleCfgValidate() {
  if (CFG_VALIDATE_TIMER) clearTimeout(CFG_VALIDATE_TIMER);
  CFG_VALIDATE_TIMER = setTimeout(() => {
    validateCfgNow().catch(e => {
      $("#cfg-status").textContent = "validation error: " + e;
    });
  }, 250);
}

function renderCfgValidation(payload) {
  const box = $("#cfg-issues");
  if (!box) return;
  // Clear per-field marks.
  $$(".cfg-field.error, .cfg-field.warn").forEach(el =>
    el.classList.remove("error", "warn"));
  const issues = (payload && payload.issues) || [];
  if (!issues.length) {
    box.classList.add("hidden");
    box.innerHTML = "";
    $("#cfg-save").disabled = false;
    return;
  }
  const items = issues.map(i => {
    const cls = i.level === "error" ? "error" : "warn";
    const fieldHtml = i.field && i.field !== "_root" && i.field !== "_yaml"
      ? `<code class="cfg-issue-field">${escapeHtml(i.field)}</code>`
      : "";
    return `<li class="cfg-issue cfg-issue-${cls}">
      <span class="cfg-issue-mark">${i.level === "error" ? "✗" : "!"}</span>
      ${fieldHtml}<span>${escapeHtml(i.message)}</span>
    </li>`;
  }).join("");
  box.classList.remove("hidden");
  box.innerHTML = `<ul class="cfg-issues-list">${items}</ul>`;
  // Highlight the offending form fields too.
  for (const i of issues) {
    const fname = (i.field || "").split(".").pop();
    const fld = $(`#cfgf-${fname}`);
    if (fld) {
      const wrap = fld.closest(".cfg-field");
      if (wrap) wrap.classList.add(i.level === "error" ? "error" : "warn");
    }
  }
  $("#cfg-save").disabled = issues.some(i => i.level === "error");
}

function renderResolvedPretty(resolved) {
  const root = $("#cfg-resolved-pretty");
  if (!root) return;
  if (!resolved) { root.innerHTML = ""; return; }
  const r = resolved;
  const rows = [
    ["runs dir", r.runs_dir],
    ["scenarios dir", r.scenarios_dir],
    ["sandbox dir", r.sandbox_dir],
    ["clean workspace dir", r.clean_workspace_dir],
    ["default provider", r.default_provider],
  ];
  const policy = r.policy || {};
  const policyRows = [
    ["max code iterations", policy.max_code_iterations],
    ["max validation attempts / iter", policy.max_validation_attempts_per_iteration],
    ["max diagnostic rounds / failure", policy.max_diagnostic_rounds_per_failure],
    ["wall-clock budget", policy.max_total_wall_clock_minutes + " min"],
  ];
  const fmt = rs => rs.map(([k, v]) =>
    `<div class="kv-row"><span class="kv-k">${escapeHtml(k)}</span><span class="kv-v"><code>${escapeHtml(String(v))}</code></span></div>`
  ).join("");
  root.innerHTML = `
    <div class="cfg-resolved-grid">
      <div class="cfg-resolved-col">
        <h4>Paths &amp; provider</h4>${fmt(rows)}
      </div>
      <div class="cfg-resolved-col">
        <h4>Loop policy</h4>${fmt(policyRows)}
      </div>
    </div>
    ${r.notes ? `<p class="muted cfg-resolved-notes"><strong>notes:</strong> ${escapeHtml(r.notes)}</p>` : ""}
  `;
}

// ---- form/yaml mode toggle --------------------------------------------
$$('.cfg-mode-tabs button[data-cfg-mode]').forEach(b => {
  b.addEventListener("click", async () => {
    const mode = b.dataset.cfgMode;
    if (mode === CFG_MODE) return;
    // Before switching, sync the destination pane's state from the source.
    try { await validateCfgNow(); } catch (_) {}
    CFG_MODE = mode;
    $$('.cfg-mode-tabs button[data-cfg-mode]').forEach(x =>
      x.classList.toggle("active", x.dataset.cfgMode === mode));
    $("#cfg-form").classList.toggle("hidden", mode !== "form");
    $("#cfg-yaml-pane").classList.toggle("hidden", mode !== "yaml");
    if (mode === "form" && CFG_LAST_VALIDATION && CFG_LAST_VALIDATION.form) {
      populateConfigForm(CFG_LAST_VALIDATION.form);
    }
  });
});

// ---- form field listeners (debounced live validation) -----------------
CFG_FORM_FIELDS.forEach(f => {
  const el = document.getElementById(`cfgf-${f}`);
  if (!el) return;
  const handler = () => scheduleCfgValidate();
  el.addEventListener("input", handler);
  el.addEventListener("change", handler);
});
const cfgTa = document.getElementById("cfg-textarea");
if (cfgTa) cfgTa.addEventListener("input", scheduleCfgValidate);

// ---- save / revert / test ---------------------------------------------
$("#cfg-save").addEventListener("click", async () => {
  $("#cfg-status").textContent = "saving…";
  try {
    // Always save via canonical YAML so the form-edit and the raw-edit paths
    // end up with the same on-disk shape.
    let payload;
    if (CFG_MODE === "yaml") {
      payload = await postText("/api/config/validate", $("#cfg-textarea").value);
    } else {
      payload = await postJSON("/api/config/form", readConfigForm());
      $("#cfg-textarea").value = payload.yaml || "";
    }
    if (!payload.ok) {
      $("#cfg-status").textContent = "fix errors above to save";
      return;
    }
    const yaml = CFG_MODE === "yaml" ? $("#cfg-textarea").value : payload.yaml;
    await postText("/api/config/raw", yaml);
    CFG_SAVED_TEXT = yaml;
    CFG_SAVED_FORM = payload.form;
    await loadConfig();
    setCfgDirty(false);
    $("#cfg-status").textContent = "saved ✓";
  } catch (e) {
    $("#cfg-status").textContent = "error: " + e;
  }
});

$("#cfg-revert").addEventListener("click", async () => {
  $("#cfg-textarea").value = CFG_SAVED_TEXT;
  if (CFG_SAVED_FORM) populateConfigForm(CFG_SAVED_FORM);
  await validateCfgNow();
  $("#cfg-status").textContent = "reverted";
  setCfgDirty(false);
});

$("#cfg-test").addEventListener("click", async () => {
  $("#cfg-status").textContent = "validating…";
  try {
    const payload = await validateCfgNow();
    if (payload.ok) {
      const warns = (payload.issues || []).filter(i => i.level === "warning").length;
      $("#cfg-status").textContent = warns
        ? `valid ✓ (${warns} warning${warns !== 1 ? "s" : ""})`
        : "valid ✓";
    } else {
      const errs = (payload.issues || []).filter(i => i.level === "error").length;
      $("#cfg-status").textContent = `${errs} error${errs !== 1 ? "s" : ""} — see above`;
    }
  } catch (e) {
    $("#cfg-status").textContent = "error: " + e;
  }
});

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
  const data = await getJSON("/api/playbooks");
  const playbooks = data.playbooks || [];
  const overrideDir = data.override_dir || ".dev-loop/playbooks";
  $("#pb-override-path").textContent = overrideDir + "/";
  const sel = $("#pb-select");
  sel.innerHTML = playbooks.map(p => {
    const tag = p.overridden ? " (overridden)" : "";
    return `<option value="${p.name}">${p.name}${tag}</option>`;
  }).join("");
  if (playbooks.length) await loadPlaybook(playbooks[0].name);
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
    const res = await postText("/api/playbooks/" + encodeURIComponent(name), $("#pb-textarea").value);
    const path = (res && res.written) || "";
    $("#pb-status").textContent = path ? `saved to ${path}` : "saved";
    await refreshBuildPlaybooks();
    $("#pb-select").value = name;
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

// ----- Build > Scenarios (structured form) ------------------------------

// Schema-light: the form mirrors ``harness.scenarios.ScenarioForm``. We
// hold the loaded form plus its on-load snapshot so Revert and the
// "dirty" pill can both work without re-fetching.
let SC_CURRENT = null;      // {name, task_request, task_contract, ..., extras, other_files}
let SC_LOADED_JSON = null;  // stringified snapshot for dirty comparison
let SC_VALIDATE_TIMER = null;
let SC_NAME = null;
const SC_LIST_FIELDS = [
  "task_contract.success_criteria",
  "task_contract.assumptions",
  "task_contract.non_goals",
  "task_contract.likely_components",
  "task_contract.validation_plan",
  "task_contract.ambiguities",
  "implementation_result.expected_validation",
  "implementation_result.risk_notes",
  "implementation_result.claimed_changed_files",
];

async function refreshBuildScenarios() {
  const data = await getJSON("/api/scenarios");
  const scenarios = data.scenarios || [];
  const scenariosDir = data.scenarios_dir || "scenarios/";
  $("#sc-new-name-hint").textContent = scenariosDir + "/";
  const sel = $("#sc-select");
  sel.innerHTML = scenarios.map(
    s => `<option value="${escapeHtml(s.name)}">${escapeHtml(s.name)}</option>`,
  ).join("");
  const empty = scenarios.length === 0;
  $("#sc-empty").classList.toggle("hidden", !empty);
  $("#sc-mode-tabs").classList.toggle("hidden", empty);
  $("#sc-form").classList.toggle("hidden", empty);
  $("#sc-form-actions").classList.toggle("hidden", empty);
  $("#sc-raw-pane").classList.add("hidden");
  $("#sc-run").disabled = empty;
  if (empty) {
    $("#sc-path").textContent = "";
    $("#sc-issues").classList.add("hidden");
    return;
  }
  const want = SC_NAME && scenarios.some(s => s.name === SC_NAME)
    ? SC_NAME : scenarios[0].name;
  sel.value = want;
  await loadScenario(want);
}

async function loadScenario(name) {
  SC_NAME = name;
  const data = await getJSON(
    "/api/scenarios/" + encodeURIComponent(name) + "/form");
  SC_CURRENT = data.form;
  SC_LOADED_JSON = JSON.stringify(SC_CURRENT);
  $("#sc-path").textContent = data.path;
  populateScenarioForm(SC_CURRENT);
  renderScenarioIssues(data.issues || []);
  setScDirty(false);
  $("#sc-status").textContent = "";
  // Default to form view on every load so a switch between scenarios
  // doesn't strand the user in raw-files mode.
  showScMode("form");
}

function populateScenarioForm(form) {
  $("#scf-task_request").value = form.task_request || "";
  const tc = form.task_contract || {};
  $("#scf-goal").value = tc.implementation_goal || "";
  $("#scf-can_start_without_human").checked = tc.can_start_without_human !== false;
  const ir = form.implementation_result || {};
  $("#scf-summary").value = ir.summary || "";
  $("#scf-hypothesis").value = ir.hypothesis || "";
  $("#scf-confidence").value = ir.confidence || "medium";
  const er = form.e2e_result || {};
  $("#scf-e2e-status").value = er.status || "passed";
  $("#scf-e2e-suite").value = er.test_suite || "";
  $("#scf-e2e-duration").value = er.duration_seconds == null ? 1 : er.duration_seconds;
  $("#scf-e2e-first-error").value = er.first_error || "";
  toggleScFirstError(er.status === "failed");

  for (const path of SC_LIST_FIELDS) {
    const items = readScPath(form, path) || [];
    renderScList(path, items);
  }

  const ul = $("#sc-other-files");
  const others = form.other_files || [];
  if (!others.length) {
    ul.innerHTML = `<li class="sc-other-empty">No extra files.</li>`;
  } else {
    ul.innerHTML = others.map(
      f => `<li><code>${escapeHtml(f)}</code></li>`,
    ).join("");
  }
}

function readScPath(obj, dotted) {
  const parts = dotted.split(".");
  let cur = obj;
  for (const p of parts) {
    if (cur == null) return undefined;
    cur = cur[p];
  }
  return cur;
}

function writeScPath(obj, dotted, value) {
  const parts = dotted.split(".");
  let cur = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    if (cur[parts[i]] == null || typeof cur[parts[i]] !== "object") {
      cur[parts[i]] = {};
    }
    cur = cur[parts[i]];
  }
  cur[parts[parts.length - 1]] = value;
}

function renderScList(path, items) {
  const host = document.querySelector(`.sc-list[data-sc-list="${path}"]`);
  if (!host) return;
  host.innerHTML = "";
  items.forEach((val, idx) => host.appendChild(makeScListRow(path, val, idx)));
  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "sc-list-empty";
    empty.textContent = "(empty)";
    host.appendChild(empty);
  }
  const add = document.createElement("button");
  add.type = "button";
  add.className = "sc-list-add";
  add.textContent = "+ add";
  add.addEventListener("click", () => {
    const cur = readScPath(SC_CURRENT, path) || [];
    cur.push("");
    writeScPath(SC_CURRENT, path, cur);
    renderScList(path, cur);
    // Focus the brand-new input so the user can just start typing.
    const inputs = host.querySelectorAll('input[type="text"]');
    if (inputs.length) inputs[inputs.length - 1].focus();
    setScDirty(true);
    scheduleScValidate();
  });
  host.appendChild(add);
}

function makeScListRow(path, value, index) {
  const row = document.createElement("div");
  row.className = "sc-list-row";
  const input = document.createElement("input");
  input.type = "text";
  input.value = value;
  input.addEventListener("input", e => {
    const cur = readScPath(SC_CURRENT, path) || [];
    cur[index] = e.target.value;
    writeScPath(SC_CURRENT, path, cur);
    setScDirty(true);
    scheduleScValidate();
  });
  const del = document.createElement("button");
  del.type = "button";
  del.className = "sc-list-del";
  del.title = "remove";
  del.setAttribute("aria-label", "remove this item");
  del.textContent = "×";
  del.addEventListener("click", () => {
    const cur = readScPath(SC_CURRENT, path) || [];
    cur.splice(index, 1);
    writeScPath(SC_CURRENT, path, cur);
    renderScList(path, cur);
    setScDirty(true);
    scheduleScValidate();
  });
  row.appendChild(input);
  row.appendChild(del);
  return row;
}

function toggleScFirstError(show) {
  $("#scf-first-error-wrap").classList.toggle("hidden", !show);
}

function bindScScalar(id, path, isCheckbox = false) {
  const el = $("#" + id);
  if (!el) return;
  el.addEventListener("input", () => {
    const val = isCheckbox ? el.checked : el.value;
    writeScPath(SC_CURRENT, path, val);
    setScDirty(true);
    scheduleScValidate();
  });
}

function bindScNumber(id, path) {
  const el = $("#" + id);
  if (!el) return;
  el.addEventListener("input", () => {
    const raw = el.value;
    const n = raw === "" ? null : Number(raw);
    writeScPath(SC_CURRENT, path,
      n == null || Number.isNaN(n) ? raw : Math.trunc(n));
    setScDirty(true);
    scheduleScValidate();
  });
}

bindScScalar("scf-task_request", "task_request");
bindScScalar("scf-goal", "task_contract.implementation_goal");
bindScScalar("scf-can_start_without_human", "task_contract.can_start_without_human", true);
bindScScalar("scf-summary", "implementation_result.summary");
bindScScalar("scf-hypothesis", "implementation_result.hypothesis");
bindScScalar("scf-confidence", "implementation_result.confidence");
bindScScalar("scf-e2e-suite", "e2e_result.test_suite");
bindScNumber("scf-e2e-duration", "e2e_result.duration_seconds");
bindScScalar("scf-e2e-first-error", "e2e_result.first_error");

$("#scf-e2e-status").addEventListener("change", () => {
  const v = $("#scf-e2e-status").value;
  writeScPath(SC_CURRENT, "e2e_result.status", v);
  toggleScFirstError(v === "failed");
  setScDirty(true);
  scheduleScValidate();
});

function setScDirty(dirty) {
  const pill = $("#sc-dirty-pill");
  pill.classList.toggle("dirty", dirty);
  pill.textContent = dirty ? "● unsaved changes" : "";
}

function scheduleScValidate() {
  if (SC_VALIDATE_TIMER) clearTimeout(SC_VALIDATE_TIMER);
  SC_VALIDATE_TIMER = setTimeout(validateScNow, 250);
}

async function validateScNow() {
  if (!SC_NAME || !SC_CURRENT) return;
  try {
    const r = await postJSON(
      `/api/scenarios/${encodeURIComponent(SC_NAME)}/validate`,
      {form: SC_CURRENT});
    renderScenarioIssues(r.issues || []);
    $("#sc-save").disabled = !r.ok;
  } catch (e) {
    // Network / 5xx: don't block save, just let the user try and find out.
    $("#sc-save").disabled = false;
  }
}

function renderScenarioIssues(issues) {
  const box = $("#sc-issues");
  $$(".cfg-field", $("#sc-form")).forEach(
    f => f.classList.remove("error", "warn"));
  if (!issues.length) {
    box.classList.add("hidden"); box.innerHTML = "";
    return;
  }
  // Light up each offending field on the form.
  for (const i of issues) {
    const f = document.querySelector(
      `.cfg-field[data-sc-field="${i.field}"]`);
    if (f) f.classList.add(i.level === "error" ? "error" : "warn");
  }
  const errs = issues.filter(i => i.level === "error").length;
  const warns = issues.filter(i => i.level === "warning").length;
  const head = errs
    ? `<strong>${errs} error${errs !== 1 ? "s" : ""}</strong>` +
      (warns ? ` and ${warns} warning${warns !== 1 ? "s" : ""}` : "")
    : `<strong>${warns} warning${warns !== 1 ? "s" : ""}</strong>`;
  box.innerHTML =
    `<div>${head}</div>` +
    `<ul class="cfg-issues-list">${
      issues.map(i => {
        const cls = i.level === "error" ? "cfg-issue-error" : "cfg-issue-warn";
        const mark = i.level === "error" ? "✖" : "⚠";
        const field = i.field && !i.field.startsWith("_")
          ? `<code class="cfg-issue-field">${escapeHtml(i.field)}</code>` : "";
        return `<li class="cfg-issue ${cls}"><span class="cfg-issue-mark">${mark}</span>${field}<span>${escapeHtml(i.message)}</span></li>`;
      }).join("")
    }</ul>`;
  box.classList.remove("hidden");
}

// ---- mode toggle (form vs raw files) ------------------------------------
function showScMode(mode) {
  $$("#sc-mode-tabs button").forEach(
    b => b.classList.toggle("active", b.dataset.scMode === mode));
  $("#sc-form").classList.toggle("hidden", mode !== "form");
  $("#sc-form-actions").classList.toggle("hidden", mode !== "form");
  $("#sc-issues").classList.toggle("hidden",
    mode !== "form" || !$("#sc-issues").innerHTML);
  $("#sc-raw-pane").classList.toggle("hidden", mode !== "raw");
  if (mode === "raw") populateScRawFiles();
}
$$("#sc-mode-tabs button").forEach(
  b => b.addEventListener("click", () => showScMode(b.dataset.scMode)));

async function populateScRawFiles() {
  if (!SC_NAME) return;
  const data = await getJSON("/api/scenarios/" + encodeURIComponent(SC_NAME));
  const files = data.files.filter(f => !f.name.endsWith("/"));
  const sel = $("#sc-file-select");
  const prev = sel.value;
  sel.innerHTML = files.map(
    f => `<option>${escapeHtml(f.name)}</option>`).join("");
  const pick = files.find(f => f.name === prev)
    ? prev
    : (files[0] && files[0].name);
  if (pick) {
    sel.value = pick;
    await loadScRawFile(pick);
  }
}
async function loadScRawFile(fn) {
  $("#sc-textarea").value = await getText(
    `/api/scenarios/${encodeURIComponent(SC_NAME)}/file/${encodeURIComponent(fn)}`);
  $("#sc-raw-status").textContent = "";
}
$("#sc-file-select").addEventListener("change", e => loadScRawFile(e.target.value));
$("#sc-save-raw").addEventListener("click", async () => {
  const fn = $("#sc-file-select").value;
  $("#sc-raw-status").textContent = "saving…";
  try {
    await postText(
      `/api/scenarios/${encodeURIComponent(SC_NAME)}/file/${encodeURIComponent(fn)}`,
      $("#sc-textarea").value);
    $("#sc-raw-status").textContent = "saved ✓";
    toast(`saved ${fn}`);
    // Re-pull the structured projection so the form picks up any
    // hand-edits when the user flips back.
    if (SC_NAME) await loadScenario(SC_NAME);
  } catch (e) { $("#sc-raw-status").textContent = "error: " + e; }
});

// ---- save / revert ------------------------------------------------------
$("#sc-save").addEventListener("click", async () => {
  if (!SC_NAME || !SC_CURRENT) return;
  $("#sc-status").textContent = "saving…";
  try {
    const r = await postJSON(
      `/api/scenarios/${encodeURIComponent(SC_NAME)}/form`,
      {form: SC_CURRENT});
    SC_CURRENT = r.form;
    SC_LOADED_JSON = JSON.stringify(SC_CURRENT);
    populateScenarioForm(SC_CURRENT);
    renderScenarioIssues(r.issues || []);
    setScDirty(false);
    $("#sc-status").textContent = `saved ${(r.written || []).length} file(s) ✓`;
    toast(`saved scenario ${SC_NAME}`);
  } catch (e) {
    // The server returns 400 with a JSON body on validation failure.
    $("#sc-status").textContent = "error: " + e;
  }
});

$("#sc-revert").addEventListener("click", () => {
  if (!SC_LOADED_JSON) return;
  SC_CURRENT = JSON.parse(SC_LOADED_JSON);
  populateScenarioForm(SC_CURRENT);
  setScDirty(false);
  $("#sc-status").textContent = "reverted";
  scheduleScValidate();
});

$("#sc-select").addEventListener("change", async e => {
  if (await confirmScDiscard()) loadScenario(e.target.value);
  else $("#sc-select").value = SC_NAME;
});

async function confirmScDiscard() {
  const pill = $("#sc-dirty-pill");
  if (!pill.classList.contains("dirty")) return true;
  return confirm("Discard unsaved changes to " + SC_NAME + "?");
}

// ---- new-scenario form (no prompt() dialogs) ---------------------------
$("#sc-new-toggle").addEventListener("click", () => {
  const f = $("#sc-new-form");
  const hidden = f.classList.toggle("hidden");
  if (!hidden) {
    $("#sc-new-name").focus();
    $("#sc-new-status").textContent = "";
  }
});
$("#sc-new-cancel").addEventListener("click", () => {
  $("#sc-new-form").classList.add("hidden");
  $("#sc-new-name").value = "";
  $("#sc-new-goal").value = "";
});
$("#sc-new-create").addEventListener("click", async () => {
  const name = $("#sc-new-name").value.trim();
  const goal = $("#sc-new-goal").value.trim();
  const status = $("#sc-new-status");
  if (!/^[A-Za-z0-9_.\-]+$/.test(name)) {
    status.textContent = "name must be letters/digits/-_.";
    return;
  }
  status.textContent = "creating…";
  try {
    await postJSON("/api/scenarios", {
      name,
      task_request: goal
        ? `# ${name}\n\n${goal}\n`
        : `# ${name}\n\nDescribe the request here.\n`,
      implementation_goal: goal || name,
    });
    SC_NAME = name;
    $("#sc-new-form").classList.add("hidden");
    $("#sc-new-name").value = "";
    $("#sc-new-goal").value = "";
    status.textContent = "";
    await refreshBuildScenarios();
    toast(`created scenario ${name}`);
  } catch (e) {
    status.textContent = "error: " + e;
  }
});

$("#sc-empty-overview-link").addEventListener("click", e => {
  e.preventDefault();
  selectBuilder("overview");
});

$("#sc-run").addEventListener("click", () => {
  if (!SC_NAME) return;
  showTab("run");
  $("#impl-provider").value = "replay";
  $("#impl-provider").dispatchEvent(new Event("change"));
  // refreshScenariosForLaunch repopulates the select asynchronously;
  // wait one tick so our value sticks.
  setTimeout(() => {
    const sel = $("#impl-scenario");
    if (sel) {
      sel.value = SC_NAME;
      const goal = (SC_CURRENT && SC_CURRENT.task_contract
        && SC_CURRENT.task_contract.implementation_goal) || "";
      const req = $("#impl-request");
      if (goal && !req.value.trim()) req.value = goal;
    }
    toast("ready to run — fill the request and hit Run /implement");
  }, 150);
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
  await refreshTemplatesStrip();
}

let TEMPLATES_CACHE = null;
let ACTIVE_TEMPLATE_ID = null;

async function refreshTemplatesStrip() {
  const strip = $("#templates-strip");
  if (!strip) return;
  try {
    const {templates} = await getJSON("/api/bundle/templates");
    TEMPLATES_CACHE = templates;
    if (!templates.length) {
      strip.innerHTML = '<div class="muted">no built-in templates installed</div>';
      return;
    }
    strip.innerHTML = templates.map(t => renderTemplateCard(t)).join("");
    $$(".tpl-card", strip).forEach(card => {
      card.addEventListener("click", () => applyTemplateById(card.dataset.tplId));
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          applyTemplateById(card.dataset.tplId);
        }
      });
    });
  } catch (e) {
    strip.innerHTML = `<div class="muted">error loading templates: ${escapeHtml(String(e))}</div>`;
  }
}

function renderTemplateCard(t) {
  const inc = t.includes || {};
  const badges = [];
  if (inc.config) badges.push('<span class="pill">config</span>');
  if (inc.scenarios) badges.push(
    `<span class="pill">${inc.scenarios} scenario${inc.scenarios === 1 ? "" : "s"}</span>`);
  if (inc.playbooks) badges.push(
    `<span class="pill">${inc.playbooks} playbook${inc.playbooks === 1 ? "" : "s"}</span>`);
  const tags = (t.tags || []).join(" · ");
  const active = (t.id === ACTIVE_TEMPLATE_ID) ? " is-active" : "";
  return `
    <button type="button" class="tpl-card${active}"
      role="listitem" data-tpl-id="${escapeAttr(t.id)}"
      aria-label="Apply template: ${escapeAttr(t.title)}">
      <div class="tpl-card-title">${escapeHtml(t.title)}</div>
      <div class="tpl-card-summary">${escapeHtml(t.summary || "")}</div>
      <div class="tpl-card-badges">${badges.join("")}</div>
      ${tags ? `<div class="tpl-card-tags">${escapeHtml(tags)}</div>` : ""}
    </button>`;
}

async function applyTemplateById(id) {
  // Mark the card as active so the user sees which template the
  // preview below maps to.
  ACTIVE_TEMPLATE_ID = id;
  $$(".tpl-card").forEach(c => {
    c.classList.toggle("is-active", c.dataset.tplId === id);
  });
  $("#bundle-preview-status").textContent = "loading template…";
  try {
    const {bundle} = await getJSON(
      `/api/bundle/templates/${encodeURIComponent(id)}`);
    // Pipe the template into the same preview machinery that user
    // uploads use — one importer, no special case.
    $("#bundle-paste").value = JSON.stringify(bundle, null, 2);
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
    const el = $("#bundle-preview");
    if (el) el.scrollIntoView({block: "nearest", behavior: "smooth"});
    toast(`previewing template: ${id}`);
  } catch (e) {
    $("#bundle-preview-status").textContent = "error: " + e;
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
  ACTIVE_TEMPLATE_ID = null;
  $$(".tpl-card").forEach(c => c.classList.remove("is-active"));
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
      <td><input type="checkbox"
        aria-label="Include ${escapeAttr(c.path)}"
        data-bundle-path="${escapeAttr(c.path)}" ${checked} ${disabled}></td>
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
let TRENDS_CACHE = {buckets: [], ungrouped_count: 0, total_runs: 0};
let TRENDS_COLLAPSED = false;
let TRENDS_COMPARE_PICK = null;  // first task_id when shift-clicking a 2nd point

async function refreshAnalyzeTab() {
  const [runsRes, trendsRes] = await Promise.all([
    getJSON("/api/runs"),
    getJSON("/api/runs/trends").catch(() => (
      {buckets: [], ungrouped_count: 0, total_runs: 0})),
  ]);
  RUNS_CACHE = runsRes.runs;
  TRENDS_CACHE = trendsRes;
  renderTrends();
  renderRunList();
}

function renderTrends() {
  const wrap = $("#run-trends");
  if (!wrap) return;
  const eligible = (TRENDS_CACHE.buckets || []).filter(b => b.stats.count >= 2);
  if (!eligible.length) {
    wrap.innerHTML = "";
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  const header = `
    <div class="trends-header">
      <button class="trends-toggle" type="button"
        aria-expanded="${!TRENDS_COLLAPSED}"
        aria-controls="run-trends-body"
        title="Show or hide goal trends">
        <span class="trends-caret">${TRENDS_COLLAPSED ? "▸" : "▾"}</span>
        <span>Trends</span>
        <span class="muted trends-meta">${eligible.length} goal${eligible.length === 1 ? "" : "s"} · ${TRENDS_CACHE.total_runs} runs</span>
      </button>
    </div>`;
  if (TRENDS_COLLAPSED) {
    wrap.innerHTML = header + '<div id="run-trends-body" hidden></div>';
    bindTrendsToggle();
    return;
  }
  const body = eligible.map(renderTrendBucket).join("");
  wrap.innerHTML = header + `<div id="run-trends-body">${body}</div>`;
  bindTrendsToggle();
  bindTrendSparklines();
}

function bindTrendsToggle() {
  const btn = $(".trends-toggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    TRENDS_COLLAPSED = !TRENDS_COLLAPSED;
    renderTrends();
  });
}

function renderTrendBucket(b) {
  const s = b.stats;
  const pct = Math.round(s.pass_rate * 100);
  const pctCls = s.pass_rate >= 0.8 ? "pass"
    : s.pass_rate >= 0.5 ? "warn" : "fail";
  const itersTxt = s.median_iterations != null
    ? `${formatMedian(s.median_iterations)} iter` : "";
  const durTxt = s.median_duration_seconds != null
    ? formatDuration(Math.round(s.median_duration_seconds)) : "";
  const delta = renderTrendDeltas(s);
  const spark = buildSparkline(b);
  const goalText = b.goal || "(no goal)";
  return `
    <div class="trend-row" data-key="${escapeAttr(b.key)}">
      <div class="trend-goal" title="${escapeAttr(b.goal)}">${escapeHtml(truncate(goalText, 80))}</div>
      <div class="trend-stats">
        <span class="pill ${pctCls}" title="${s.pass_count}/${s.count} passed">${pct}% pass</span>
        ${itersTxt ? `<span class="muted">${escapeHtml(itersTxt)}</span>` : ""}
        ${durTxt ? `<span class="muted">${escapeHtml(durTxt)}</span>` : ""}
        ${delta}
      </div>
      <div class="trend-spark">${spark}</div>
    </div>`;
}

function renderTrendDeltas(s) {
  if (s.pass_rate_delta == null && s.iterations_delta == null) return "";
  const parts = [];
  if (s.pass_rate_delta != null) {
    const d = Math.round(s.pass_rate_delta * 100);
    const cls = d > 0 ? "pass" : d < 0 ? "fail" : "";
    const arrow = d > 0 ? "▲" : d < 0 ? "▼" : "·";
    parts.push(
      `<span class="trend-delta ${cls}" title="pass-rate, 2nd half − 1st half">${arrow} ${Math.abs(d)}pp</span>`,
    );
  }
  if (s.iterations_delta != null) {
    // Fewer iterations is better, so flip sense: negative = green.
    const d = s.iterations_delta;
    const cls = d < 0 ? "pass" : d > 0 ? "fail" : "";
    const arrow = d < 0 ? "▼" : d > 0 ? "▲" : "·";
    parts.push(
      `<span class="trend-delta ${cls}" title="median iterations, 2nd half − 1st half">${arrow} ${Math.abs(d).toFixed(1)} iter</span>`,
    );
  }
  return parts.join(" ");
}

function formatMedian(v) {
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

function buildSparkline(b) {
  const pts = b.series;
  const n = pts.length;
  const w = Math.max(60, Math.min(220, 14 * n + 8));
  const h = 28;
  const padX = 6;
  const padY = 4;
  const iters = pts.map(p => p.iterations || 0);
  const maxIter = Math.max(1, ...iters);
  const minIter = Math.min(...iters);
  const range = Math.max(1, maxIter - minIter);
  const xStep = n > 1 ? (w - 2 * padX) / (n - 1) : 0;
  const ys = pts.map(p => {
    const v = p.iterations || 0;
    return h - padY - ((v - minIter) / range) * (h - 2 * padY);
  });
  const xs = pts.map((_, i) => padX + i * xStep);
  let path = "";
  for (let i = 0; i < n; i++) {
    path += (i === 0 ? "M" : "L") + xs[i].toFixed(1) + "," + ys[i].toFixed(1);
  }
  const dots = pts.map((p, i) => {
    const cls = p.status === "passed" ? "pass"
      : p.failed ? "fail" : "other";
    const title = `${p.task_id}\n${p.status || "?"} · ${p.iterations || 0} iter`
      + (p.duration_seconds != null ? ` · ${formatDuration(p.duration_seconds)}` : "")
      + (p.created_at_utc ? `\n${p.created_at_utc}` : "")
      + "\nclick: open · shift-click 2 to compare";
    return `<circle class="spark-dot ${cls}" cx="${xs[i].toFixed(1)}" cy="${ys[i].toFixed(1)}" r="3.2" data-id="${escapeAttr(p.task_id)}" tabindex="0" role="button" aria-label="run ${escapeAttr(p.task_id)} ${escapeAttr(p.status || "")}"><title>${escapeHtml(title)}</title></circle>`;
  }).join("");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${pts.length} runs, oldest to newest">`
    + `<path class="spark-line" d="${path}"/>${dots}</svg>`;
}

function bindTrendSparklines() {
  $$(".spark-dot").forEach(c => {
    c.addEventListener("click", e => onSparkClick(e, c.dataset.id));
    c.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onSparkClick(e, c.dataset.id);
      }
    });
  });
}

function onSparkClick(ev, taskId) {
  if (!taskId) return;
  if (ev.shiftKey) {
    if (!TRENDS_COMPARE_PICK || TRENDS_COMPARE_PICK === taskId) {
      TRENDS_COMPARE_PICK = taskId;
      toast("shift-click another point to compare");
      return;
    }
    const a = TRENDS_COMPARE_PICK;
    TRENDS_COMPARE_PICK = null;
    location.hash = "#/compare/" + encodeURIComponent(a)
      + "/" + encodeURIComponent(taskId);
    return;
  }
  TRENDS_COMPARE_PICK = null;
  selectRun(taskId);
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
      let sel = r.task_id === CURRENT_TASK ? "active" : "";
      // In compare mode each pinned slot gets its own highlight so the
      // user can tell at a glance which is A (blue) and which is B (green).
      let mark = "";
      if (COMPARE_A && r.task_id === COMPARE_A) { sel = "compare-a"; mark = '<span class="compare-mark">A</span>'; }
      if (COMPARE_B && r.task_id === COMPARE_B) { sel = "compare-b"; mark = '<span class="compare-mark">B</span>'; }
      html.push(`
        <li data-id="${escapeAttr(r.task_id)}" class="${sel}">
          <div class="run-row">
            <div class="top">
              ${mark}<span class="pill ${cls}">${escapeHtml(status || "?")}</span>
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
    li.addEventListener("click", () => onRunListClick(li.dataset.id)));
}

function onRunListClick(taskId) {
  // In compare mode the list acts like a two-slot picker rather than
  // a single-selection navigator.
  if (COMPARE_MODE) {
    pickForCompare(taskId);
    return;
  }
  selectRun(taskId);
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
  // Coming back to single-run navigation always exits compare so the
  // two views never fight over the right pane.
  $("#analyze-compare").classList.add("hidden");
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
      <button class="secondary copy-btn" data-compare-to
        title="Pin this as run A and pick a second run to diff against"
        aria-label="Compare this run with another">Compare to…</button>
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
  hero.querySelector("[data-compare-to]").addEventListener("click", () => {
    setCompareMode(true);
    COMPARE_A = tm.task_id || CURRENT_TASK;
    renderRunList();
    updateCompareHint();
    toast("pick a second run to compare with");
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
    let ai = {calls: []};
    try {
      ai = await getJSON(`/api/runs/${encodeURIComponent(taskId)}/iteration/${i}/ai_calls`);
    } catch (_) { /* older runs may not have ai_calls; tolerate gracefully */ }
    iterDocs.push({i, im, att, ai});
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

  for (const {i, im, att, ai} of iterDocs) {
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
    const aiCalls = (ai && ai.calls) || [];
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
      ${aiCalls.length ? `
      <details class="ai-calls">
        <summary>
          AI calls
          <span class="muted">(${aiCalls.length})</span>
          ${aiCalls.some(c => c.returncode != null && c.returncode !== 0)
            ? '<span class="pill fail">non-zero exit</span>' : ""}
          ${aiCalls.some(c => c.synthesized)
            ? '<span class="pill warn">harness fallback</span>' : ""}
        </summary>
        <div class="ai-call-list">
          ${aiCalls.map(c => `
            <div class="ai-call ${c.returncode != null && c.returncode !== 0 ? "fail" : ""} ${c.synthesized ? "synth" : ""}">
              <div class="ai-call-head">
                <code>${escapeHtml(c.name)}</code>
                <span class="pill ${c.synthesized ? "warn" : "info"}">${escapeHtml(c.provider || (c.synthesized ? "harness" : "unknown"))}</span>
                ${c.returncode != null
                  ? `<span class="pill ${c.returncode === 0 ? "pass" : "fail"}">rc ${c.returncode}</span>`
                  : ""}
                ${c.output_type ? `<span class="muted">→ <code>${escapeHtml(c.output_type)}</code></span>` : ""}
                ${c.ts_utc ? `<span class="muted" title="${escapeAttr(c.ts_utc)}">${escapeHtml((c.ts_utc || "").replace("T", " ").replace("Z", ""))}</span>` : ""}
                <button class="secondary" data-action="ai_call"
                  data-iter="${i}" data-name="${escapeAttr(c.name)}">drill in</button>
              </div>
              <div class="ai-call-body" data-ai-call-detail="${i}-${escapeAttr(c.name)}"></div>
            </div>`).join("")}
        </div>
      </details>` : ""}
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
    } else if (b.dataset.action === "ai_call") {
      const name = b.dataset.name;
      const tgt = document.querySelector(
        `[data-ai-call-detail="${i}-${CSS.escape(name)}"]`);
      if (!tgt) return;
      if (tgt.dataset.loaded) {
        tgt.innerHTML = ""; tgt.dataset.loaded = "";
        b.textContent = "drill in";
        return;
      }
      const dump = await getJSON(
        `/api/runs/${encodeURIComponent(taskId)}/iteration/${i}` +
        `/ai_call/${encodeURIComponent(name)}`);
      tgt.innerHTML = renderAiCallDetail(dump);
      tgt.dataset.loaded = "1";
      b.textContent = "hide";
    }
  }));
}

function renderAiCallDetail(dump) {
  if (!dump || dump._error) {
    return `<p class="muted">${escapeHtml((dump && dump._error) || "no detail")}</p>`;
  }
  const meta = dump.metadata || {};
  const parts = [];
  if (Object.keys(meta).length) {
    const rows = [];
    if (meta.provider) rows.push(["provider", meta.provider]);
    if (meta.returncode != null) rows.push(["returncode", String(meta.returncode)]);
    if (meta.ts_utc) rows.push(["ts_utc", meta.ts_utc]);
    if (meta.synthesized) rows.push(["synthesized", "yes"]);
    if (Array.isArray(meta.argv) && meta.argv.length) {
      rows.push(["argv", meta.argv.join(" ")]);
    }
    parts.push(`<table class="ai-call-meta">
      ${rows.map(([k, v]) =>
        `<tr><th>${escapeHtml(k)}</th><td><code>${escapeHtml(v)}</code></td></tr>`).join("")}
    </table>`);
    if (meta.stderr_tail) {
      parts.push(`<details class="file-section" open>
        <summary><strong>stderr (tail)</strong></summary>
        <pre>${escapeHtml(meta.stderr_tail)}</pre>
      </details>`);
    }
  } else {
    parts.push('<p class="muted">no provider metadata recorded</p>');
  }
  if (dump.output) {
    parts.push(`<details class="file-section">
      <summary><strong>output.json</strong></summary>
      <pre>${escapeHtml(JSON.stringify(dump.output, null, 2))}</pre>
    </details>`);
  }
  if (dump.input) {
    parts.push(`<details class="file-section">
      <summary><strong>input.json</strong></summary>
      <pre>${escapeHtml(JSON.stringify(dump.input, null, 2))}</pre>
    </details>`);
  }
  if (dump.raw_provider_log) {
    parts.push(`<details class="file-section">
      <summary><strong>raw_provider_log.jsonl</strong></summary>
      <pre>${escapeHtml(dump.raw_provider_log)}</pre>
    </details>`);
  }
  return parts.join("");
}

// ----- Compare two runs -------------------------------------------------
//
// State lives in three globals so the run-list renderer can highlight
// the picked slots without threading them through every call. The
// compare view is fetched in a single round-trip and rendered
// directly into the right-pane #analyze-compare panel.

let COMPARE_MODE = false;
let COMPARE_A = null;
let COMPARE_B = null;
let COMPARE_DATA = null;

function setCompareMode(on) {
  COMPARE_MODE = !!on;
  $("#compare-mode-toggle").checked = COMPARE_MODE;
  updateCompareHint();
  renderRunList();
  if (!COMPARE_MODE) {
    exitCompareView();
  }
}

function updateCompareHint() {
  const hint = $("#compare-pick-hint");
  if (!COMPARE_MODE) { hint.classList.add("hidden"); return; }
  if (COMPARE_A && !COMPARE_B) {
    hint.textContent = "pick a 2nd run…";
    hint.classList.remove("hidden");
  } else if (!COMPARE_A) {
    hint.textContent = "pick the 1st run…";
    hint.classList.remove("hidden");
  } else {
    hint.classList.add("hidden");
  }
}

function pickForCompare(taskId) {
  // Click the same slot to drop it; otherwise fill A, then B, then
  // when both are full rotate (drop A, B becomes A, new -> B).
  if (taskId === COMPARE_A) { COMPARE_A = null; }
  else if (taskId === COMPARE_B) { COMPARE_B = null; }
  else if (!COMPARE_A) { COMPARE_A = taskId; }
  else if (!COMPARE_B) { COMPARE_B = taskId; }
  else { COMPARE_A = COMPARE_B; COMPARE_B = taskId; }
  updateCompareHint();
  renderRunList();
  if (COMPARE_A && COMPARE_B) {
    openCompareView(COMPARE_A, COMPARE_B);
  } else {
    // Step back to the empty state so the user sees the prompt.
    $("#analyze-detail").classList.add("hidden");
    $("#analyze-compare").classList.add("hidden");
    $("#analyze-empty").classList.remove("hidden");
  }
}

async function openCompareView(a, b) {
  COMPARE_A = a; COMPARE_B = b;
  COMPARE_MODE = true;
  $("#compare-mode-toggle").checked = true;
  // Make sure we're on Analyze; pull the run list so the sidebar
  // highlights the two picks even when arrived via a deep link.
  if ($("#tab-analyze").classList.contains("hidden")) showTab("analyze");
  if (!RUNS_CACHE.length) {
    try { await refreshAnalyzeTab(); } catch (_) {}
  }
  renderRunList();
  updateCompareHint();
  $("#analyze-empty").classList.add("hidden");
  $("#analyze-detail").classList.add("hidden");
  $("#analyze-compare").classList.remove("hidden");
  $("#compare-deltas").innerHTML = '<span class="muted">loading…</span>';
  $("#compare-heroes").innerHTML = "";
  $("#compare-iterations").innerHTML = "";
  $("#compare-files").innerHTML = "";
  $("#compare-audit").innerHTML = "";
  updateLocationHash();
  try {
    COMPARE_DATA = await getJSON(
      `/api/runs/${encodeURIComponent(a)}/compare/${encodeURIComponent(b)}`);
    renderCompare();
  } catch (e) {
    $("#compare-deltas").innerHTML =
      `<span class="muted">error loading compare: ${escapeHtml(String(e))}</span>`;
  }
}

function exitCompareView() {
  COMPARE_A = null; COMPARE_B = null; COMPARE_DATA = null;
  $("#analyze-compare").classList.add("hidden");
  updateCompareHint();
  renderRunList();
  if (CURRENT_TASK) {
    $("#analyze-detail").classList.remove("hidden");
    updateLocationHash();
  } else {
    $("#analyze-empty").classList.remove("hidden");
    if (location.hash.startsWith("#/compare/")) {
      history.replaceState(null, "", "#/");
    }
  }
}

function renderCompare() {
  const d = COMPARE_DATA;
  if (!d) return;
  $("#compare-deltas").innerHTML = renderCompareDeltas(d);
  $("#compare-heroes").innerHTML = `
    ${renderCompareSide("A", d.a)}
    ${renderCompareSide("B", d.b)}
  `;
  $("#compare-iterations").innerHTML = `
    ${renderCompareIterations("A", d.a, d.deltas)}
    ${renderCompareIterations("B", d.b, d.deltas)}
  `;
  $("#compare-files").innerHTML = renderCompareFiles(d.deltas);
  $("#compare-audit").innerHTML = `
    ${renderCompareAudit("A", d.a)}
    ${renderCompareAudit("B", d.b)}
  `;
}

// Pure: builds the delta pill strip from the compare payload. Kept
// outside the DOM so we can test it.
function compareDeltaPills(payload) {
  const d = (payload && payload.deltas) || {};
  if (!d.both_present) {
    return [{label: "one run is missing — partial view", cls: "warn"}];
  }
  const pills = [];
  pills.push(d.same_final_status
    ? {label: "same verdict", cls: "pass"}
    : {label: "verdict differs", cls: "fail"});
  if (d.same_scenario) pills.push({label: "same scenario", cls: ""});
  else pills.push({label: "different scenario", cls: "warn"});
  if (d.same_goal) pills.push({label: "same goal", cls: ""});
  else pills.push({label: "different goal", cls: "warn"});

  const di = d.iteration_count_delta;
  if (di === 0) pills.push({label: "same iteration count", cls: ""});
  else pills.push({
    label: (di > 0 ? "B took +" : "B took ") + di + " iter",
    cls: di < 0 ? "pass" : "warn",
  });

  const ds = d.duration_seconds_delta;
  if (ds != null) {
    if (ds === 0) pills.push({label: "same wall-clock", cls: ""});
    else pills.push({
      label: (ds > 0 ? "B slower by " : "B faster by ")
        + formatDuration(Math.abs(ds)),
      cls: ds < 0 ? "pass" : "warn",
    });
  }
  if (d.first_diverging_iteration != null) {
    pills.push({
      label: "diverge at iter " + d.first_diverging_iteration,
      cls: "warn",
    });
  } else if (d.iteration_status_compared > 0) {
    pills.push({label: "iterations agree", cls: "pass"});
  }
  const af = d.audit_total_delta;
  if (af != null && af !== 0) {
    pills.push({
      label: (af > 0 ? "+" : "") + af + " audit calls (B)",
      cls: af > 0 ? "warn" : "pass",
    });
  }
  return pills;
}

function renderCompareDeltas(payload) {
  return compareDeltaPills(payload).map(
    p => `<span class="pill ${p.cls}">${escapeHtml(p.label)}</span>`,
  ).join("");
}

function renderCompareSide(label, side) {
  if (!side) {
    return `<div class="compare-side side-${label.toLowerCase()} missing">
      <span class="side-label">${escapeHtml(label)}</span>
      <h4>(run not found)</h4>
      <p class="muted">This task id doesn't exist on disk anymore. The
      other side's view still works.</p>
    </div>`;
  }
  const status = side.final_status || side.status || "?";
  const dur = side.duration_seconds != null
    ? formatDuration(side.duration_seconds) : "—";
  const sel = side.selected_iteration;
  return `<div class="compare-side side-${label.toLowerCase()}">
    <span class="side-label">Run ${escapeHtml(label)}</span>
    <h4>
      <span class="pill ${pillClass(status)}">${escapeHtml(status)}</span>
      <code class="task-id">${escapeHtml(side.task_id || "")}</code>
    </h4>
    <div class="kv-row"><span class="kv-k">goal</span>
      <span class="kv-v">${escapeHtml(side.goal || "—")}</span></div>
    ${side.scenario ? `
      <div class="kv-row"><span class="kv-k">scenario</span>
        <span class="kv-v"><code>${escapeHtml(side.scenario)}</code></span></div>
    ` : ""}
    <div class="kv-row"><span class="kv-k">iterations</span>
      <span class="kv-v">${side.iteration_count}${sel != null ? ` (selected ${sel})` : ""}</span></div>
    <div class="kv-row"><span class="kv-k">duration</span>
      <span class="kv-v">${escapeHtml(dur)}</span></div>
    ${side.stop_reason ? `
      <div class="kv-row"><span class="kv-k">stop reason</span>
        <span class="kv-v"><code>${escapeHtml(side.stop_reason)}</code></span></div>
    ` : ""}
    <div class="kv-row"><span class="kv-k">audit calls</span>
      <span class="kv-v">${side.audit ? side.audit.total : 0}</span></div>
    <div class="kv-row"><span class="kv-k">started</span>
      <span class="kv-v"><code>${escapeHtml(side.created_at_utc || "—")}</code></span></div>
  </div>`;
}

function renderCompareIterations(label, side, deltas) {
  if (!side) {
    return `<div class="compare-side side-${label.toLowerCase()} missing">
      <span class="side-label">${escapeHtml(label)}</span>
      <p class="muted">no iterations — run not found</p>
    </div>`;
  }
  const sel = side.selected_iteration;
  // Walk both sides' iteration lists in parallel for divergence marks.
  const counterpart = (
    deltas && deltas.both_present && COMPARE_DATA
      ? (label === "A" ? COMPARE_DATA.b : COMPARE_DATA.a)
      : null
  );
  const divergeFrom = deltas && deltas.first_diverging_iteration;
  const items = side.iterations.map(it => {
    const cls = it.final_e2e_status === "passed" ? "pass"
              : it.final_e2e_status ? "fail" : "";
    const selCls = sel === it.i ? "selected" : "";
    let diverged = "";
    if (counterpart && counterpart.iterations.length >= it.i) {
      const other = counterpart.iterations[it.i - 1];
      if (other && (other.final_e2e_status !== it.final_e2e_status
                    || other.patch_hash !== it.patch_hash)) {
        diverged = " diverged";
      }
    }
    return `<span class="compare-iter ${cls} ${selCls}${diverged}"
      title="iter ${it.i}: ${escapeAttr(it.final_e2e_status || "—")} · ${it.attempts} attempt${it.attempts !== 1 ? "s" : ""}">
      <span class="dot"></span>iter ${it.i}
      <span class="muted">·${escapeHtml(it.final_e2e_status || "—")}</span>
    </span>`;
  }).join("");
  const lastSummary = side.iterations.length
    ? side.iterations[side.iterations.length - 1].summary
    : "";
  return `<div class="compare-side side-${label.toLowerCase()}">
    <span class="side-label">Run ${escapeHtml(label)}</span>
    <div class="compare-iters">${items || '<span class="muted">no iterations</span>'}</div>
    ${divergeFrom && label === "A"
      ? `<div class="iter-summary-line">first diverges at iter ${divergeFrom}</div>`
      : ""}
    ${lastSummary ? `<div class="iter-summary-line">${escapeHtml(lastSummary)}</div>` : ""}
  </div>`;
}

function renderCompareFiles(deltas) {
  if (!deltas || !deltas.both_present) {
    return '<p class="muted">file lists need both runs to be present.</p>';
  }
  const fmtList = (files, emptyLabel) => files.length
    ? `<ul>${files.map(f => `<li><code>${escapeHtml(f)}</code></li>`).join("")}</ul>`
    : `<ul><li class="muted">${escapeHtml(emptyLabel)}</li></ul>`;
  return `
    <div class="compare-file-col only-a">
      <h5>only in A <span class="count">(${deltas.files_only_a.length})</span></h5>
      ${fmtList(deltas.files_only_a, "no files unique to A")}
    </div>
    <div class="compare-file-col both">
      <h5>in both <span class="count">(${deltas.files_both.length})</span></h5>
      ${fmtList(deltas.files_both, "no overlap")}
    </div>
    <div class="compare-file-col only-b">
      <h5>only in B <span class="count">(${deltas.files_only_b.length})</span></h5>
      ${fmtList(deltas.files_only_b, "no files unique to B")}
    </div>`;
}

function renderCompareAudit(label, side) {
  if (!side) {
    return `<div class="compare-side side-${label.toLowerCase()} missing">
      <span class="side-label">${escapeHtml(label)}</span>
      <p class="muted">no audit data — run not found</p>
    </div>`;
  }
  const audit = side.audit || {total: 0, by_status: {}, by_capability: {}};
  const byStatus = Object.entries(audit.by_status || {})
    .sort((a, b) => b[1] - a[1]);
  const byCap = Object.entries(audit.by_capability || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  return `<div class="compare-side side-${label.toLowerCase()}">
    <span class="side-label">Run ${escapeHtml(label)}</span>
    <h4>${audit.total} call${audit.total !== 1 ? "s" : ""}</h4>
    ${byStatus.length ? `<div class="kv-row">
      <span class="kv-k">by status</span>
      <span class="kv-v">${byStatus.map(
        ([s, n]) => `<span class="pill ${s === "ok" ? "pass" : "fail"}">${escapeHtml(s)} ${n}</span>`,
      ).join(" ")}</span></div>` : ""}
    ${byCap.length ? `<div class="kv-row">
      <span class="kv-k">top capabilities</span>
      <span class="kv-v">${byCap.map(
        ([c, n]) => `<code>${escapeHtml(c)}</code> ×${n}`,
      ).join("<br>")}</span></div>` : ""}
  </div>`;
}

// ----- Compare DOM wiring -----------------------------------------------

$("#compare-mode-toggle").addEventListener("change", e => {
  setCompareMode(e.target.checked);
});
$("#compare-exit").addEventListener("click", exitCompareView);
$("#compare-swap").addEventListener("click", () => {
  if (!COMPARE_A || !COMPARE_B) return;
  const a = COMPARE_A; COMPARE_A = COMPARE_B; COMPARE_B = a;
  openCompareView(COMPARE_A, COMPARE_B);
});
$("#compare-copy-link").addEventListener("click", () => {
  if (!COMPARE_A || !COMPARE_B) return;
  const link = location.origin + location.pathname
    + `#/compare/${encodeURIComponent(COMPARE_A)}/${encodeURIComponent(COMPARE_B)}`;
  copyToClipboard(link, "Copied compare link");
});

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
  // Compare view wins so the share link round-trips both runs.
  if (COMPARE_A && COMPARE_B
      && !$("#analyze-compare").classList.contains("hidden")) {
    const target = "#/compare/" + encodeURIComponent(COMPARE_A)
      + "/" + encodeURIComponent(COMPARE_B);
    if (location.hash !== target) history.replaceState(null, "", target);
    return;
  }
  if (!CURRENT_TASK) return;
  const target = "#/run/" + encodeURIComponent(CURRENT_TASK) + "/" + currentSubview();
  if (location.hash !== target) {
    history.replaceState(null, "", target);
  }
}

async function consumeLocationHash() {
  const mCompare = location.hash.match(/^#\/compare\/([^/]+)\/([^/]+)/);
  if (mCompare) {
    const a = decodeURIComponent(mCompare[1]);
    const b = decodeURIComponent(mCompare[2]);
    showTab("analyze");
    await refreshAnalyzeTab().catch(() => {});
    await openCompareView(a, b);
    return true;
  }
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

// ----- Cmd+K command palette --------------------------------------------
//
// One palette indexes every destination the user might want to jump to:
// tabs, builder sections, scenarios (by name + goal), runs (by id + goal +
// status), playbooks, schemas, and a small set of verb-style actions. The
// server gathers the corpus in one round-trip (``/api/palette``); fuzzy-
// matching, ranking, highlighting and keyboard nav all happen here.
//
// The Esc handler in this section also doubles as the universal "cancel
// inline form" key, so a user editing a new scenario can bail without
// reaching for the mouse.

let PALETTE_ITEMS = [];        // raw list from /api/palette
let PALETTE_FILTERED = [];     // {item, score, highlights} ordered for display
let PALETTE_INDEX = 0;         // active row in PALETTE_FILTERED
let PALETTE_OPEN = false;
let PALETTE_LAST_LOAD = 0;     // ms epoch; refresh on open if stale
let G_PREFIX_TIMER = null;     // "g then b/r/a" chord support

async function loadPaletteItems(force = false) {
  // Refresh on a 10s TTL so a freshly-saved scenario shows up without
  // forcing a full reload, but back-to-back palette opens are instant.
  const now = Date.now();
  if (!force && PALETTE_ITEMS.length && now - PALETTE_LAST_LOAD < 10_000) return;
  try {
    const r = await getJSON("/api/palette");
    PALETTE_ITEMS = r.items || [];
    PALETTE_LAST_LOAD = now;
  } catch (e) {
    // Network hiccup — leave the prior corpus in place so the palette
    // still works on the last-known index.
    if (!PALETTE_ITEMS.length) PALETTE_ITEMS = [];
  }
}

function openPalette() {
  if (PALETTE_OPEN) return;
  PALETTE_OPEN = true;
  const back = $("#palette-backdrop");
  back.classList.remove("hidden");
  back.setAttribute("aria-hidden", "false");
  const input = $("#palette-input");
  input.value = "";
  loadPaletteItems().then(() => {
    filterPalette("");
    input.focus();
  });
}

function closePalette() {
  if (!PALETTE_OPEN) return;
  PALETTE_OPEN = false;
  const back = $("#palette-backdrop");
  back.classList.add("hidden");
  back.setAttribute("aria-hidden", "true");
}

// Tiny fuzzy matcher. Returns null if every query char isn't found in
// order, otherwise a score (smaller is better) plus an array of matched
// char indices for highlighting. Word-boundary hits and prefix matches
// score better, mirroring what users expect of a Cmd+K palette.
function fuzzyMatch(query, text) {
  if (!query) return {score: 0, hits: []};
  const q = query.toLowerCase();
  const t = text.toLowerCase();
  let qi = 0, ti = 0, score = 0;
  const hits = [];
  let lastHit = -2;
  let inGap = false;
  while (qi < q.length && ti < t.length) {
    if (q[qi] === t[ti]) {
      // Bonus: hit at start, hit after a word boundary, hit right after the
      // previous hit (contiguous run).
      if (ti === 0) score -= 8;
      else if (/\W|_/.test(t[ti - 1])) score -= 6;
      if (ti === lastHit + 1) score -= 4;
      else if (inGap) score += 1;
      // Match the original (case-preserved) char positions for highlight.
      hits.push(ti);
      lastHit = ti;
      qi++; inGap = false;
    } else {
      inGap = true;
      score += 1;
    }
    ti++;
  }
  if (qi < q.length) return null;
  // Penalise long strings so short, focused titles bubble up.
  score += Math.floor(t.length / 80);
  return {score, hits};
}

function highlight(text, hits) {
  if (!hits || !hits.length) return escapeHtml(text);
  // hits index into the lowercased version; lengths line up 1:1 with the
  // original, so the same offsets work for slicing.
  const out = [];
  let cur = 0;
  for (const h of hits) {
    if (h > cur) out.push(escapeHtml(text.slice(cur, h)));
    out.push(`<mark>${escapeHtml(text.slice(h, h + 1))}</mark>`);
    cur = h + 1;
  }
  if (cur < text.length) out.push(escapeHtml(text.slice(cur)));
  return out.join("");
}

function filterPalette(q) {
  q = (q || "").trim();
  if (!q) {
    // Empty query: surface "useful" defaults — actions first, then tabs,
    // then a few recent runs / scenarios. Keeps the open-no-typing state
    // from looking like an avalanche.
    PALETTE_FILTERED = PALETTE_ITEMS
      .filter(it => ["action", "tab", "builder"].includes(it.kind))
      .map(it => ({item: it, hits: {title: [], subtitle: []}, score: 0}));
  } else {
    const out = [];
    for (const it of PALETTE_ITEMS) {
      const title = fuzzyMatch(q, it.title || "");
      const sub = fuzzyMatch(q, it.subtitle || "");
      const kw = fuzzyMatch(q, it.keywords || "");
      const best = [title, sub, kw].filter(Boolean)
        .sort((a, b) => a.score - b.score)[0];
      if (!best) continue;
      // Subtitle / keyword-only matches score a bit worse than title hits
      // so the "right" item ranks first when the user types its name.
      const score = best.score + (best === title ? 0 : best === sub ? 4 : 8);
      out.push({
        item: it, score,
        hits: {
          title: title ? title.hits : [],
          subtitle: sub ? sub.hits : [],
        },
      });
    }
    out.sort((a, b) => a.score - b.score);
    PALETTE_FILTERED = out.slice(0, 50);
  }
  PALETTE_INDEX = 0;
  renderPalette();
}

function renderPalette() {
  const ul = $("#palette-results");
  const count = $("#palette-count");
  if (!PALETTE_FILTERED.length) {
    ul.innerHTML = '<li class="palette-empty" role="option" aria-disabled="true">No matches. Try a tab, scenario, or run id.</li>';
    count.textContent = "";
    return;
  }
  // Group by .group, preserving order, headers for orientation.
  let lastGroup = null;
  const rows = [];
  PALETTE_FILTERED.forEach((entry, i) => {
    const it = entry.item;
    if (it.group && it.group !== lastGroup) {
      rows.push(`<li class="palette-group-header" role="presentation">${escapeHtml(it.group)}</li>`);
      lastGroup = it.group;
    }
    const active = i === PALETTE_INDEX ? "active" : "";
    const titleHtml = highlight(it.title || "", entry.hits.title);
    const subHtml = highlight(it.subtitle || "", entry.hits.subtitle);
    const status = it.status
      ? `<span class="palette-status ${pillClass(it.status)}">${escapeHtml(it.status)}</span>`
      : "";
    rows.push(`<li class="${active}" role="option"
      aria-selected="${active ? "true" : "false"}" data-idx="${i}">
      <span class="palette-kind">${escapeHtml(it.kind)}</span>
      <span>
        <span class="palette-title">${titleHtml}</span>
        <span class="palette-subtitle">${subHtml}</span>
      </span>
      ${status}
    </li>`);
  });
  ul.innerHTML = rows.join("");
  count.textContent = `${PALETTE_FILTERED.length} match${PALETTE_FILTERED.length !== 1 ? "es" : ""}`;
  scrollActivePaletteRowIntoView();
}

function scrollActivePaletteRowIntoView() {
  const ul = $("#palette-results");
  const row = ul.querySelector("li.active");
  if (row) row.scrollIntoView({block: "nearest"});
}

function movePalette(delta) {
  if (!PALETTE_FILTERED.length) return;
  PALETTE_INDEX = (PALETTE_INDEX + delta + PALETTE_FILTERED.length) % PALETTE_FILTERED.length;
  // Re-render lightly — toggling the active class is enough.
  $$("#palette-results li[data-idx]").forEach(li => {
    const idx = Number(li.dataset.idx);
    li.classList.toggle("active", idx === PALETTE_INDEX);
    li.setAttribute("aria-selected", idx === PALETTE_INDEX ? "true" : "false");
  });
  scrollActivePaletteRowIntoView();
}

function activatePalette(idx) {
  const entry = PALETTE_FILTERED[idx];
  if (!entry) return;
  closePalette();
  routePaletteItem(entry.item);
}

function routePaletteItem(it) {
  switch (it.kind) {
    case "tab":
      showTab(it.id);
      return;
    case "builder":
      showTab("build");
      selectBuilder(it.id);
      return;
    case "subview":
      if (!$("#tab-analyze").classList.contains("hidden") && CURRENT_TASK) {
        selectSubview(it.id);
        updateLocationHash();
      } else {
        showTab("analyze");
        toast("pick a run on the left first");
      }
      return;
    case "scenario":
      showTab("build");
      selectBuilder("scenarios");
      // Wait one tick for the select to populate, then change to ours.
      setTimeout(() => {
        const sel = $("#sc-select");
        if (sel && Array.from(sel.options).some(o => o.value === it.id)) {
          sel.value = it.id;
          loadScenario(it.id);
        }
      }, 100);
      return;
    case "playbook":
      showTab("build");
      selectBuilder("playbooks");
      setTimeout(() => {
        const sel = $("#pb-select");
        if (sel && Array.from(sel.options).some(o => o.value === it.id)) {
          sel.value = it.id; loadPlaybook(it.id);
        }
      }, 100);
      return;
    case "schema":
      showTab("build");
      selectBuilder("schemas");
      setTimeout(() => {
        const sel = $("#sch-select");
        if (sel && Array.from(sel.options).some(o => o.value === it.id)) {
          sel.value = it.id; loadSchema(it.id);
        }
      }, 100);
      return;
    case "run":
      selectRun(it.id);
      return;
    case "action":
      runPaletteAction(it.id);
      return;
    case "template":
      showTab("build");
      selectBuilder("share");
      setTimeout(() => {
        applyTemplateById(it.id).catch(() => {});
      }, 100);
      return;
  }
}

function runPaletteAction(id) {
  if (id === "scenario.new") {
    showTab("build");
    selectBuilder("scenarios");
    setTimeout(() => {
      $("#sc-new-form").classList.remove("hidden");
      $("#sc-new-name").focus();
    }, 100);
    return;
  }
  if (id === "shortcuts.help") {
    openShortcutsHelp();
    return;
  }
  if (id === "trends.show") {
    showTab("analyze");
    refreshAnalyzeTab().then(() => {
      TRENDS_COLLAPSED = false;
      renderTrends();
      const wrap = $("#run-trends");
      if (wrap) wrap.scrollIntoView({block: "nearest"});
      const buckets = (TRENDS_CACHE.buckets || [])
        .filter(b => b.stats.count >= 2);
      toast(buckets.length
        ? `${buckets.length} goal${buckets.length === 1 ? "" : "s"} with 2+ runs`
        : "no goal has 2+ runs yet — kick off another");
    }).catch(() => {});
    return;
  }
  if (id === "compare.runs") {
    showTab("analyze");
    refreshAnalyzeTab().then(() => {
      setCompareMode(true);
      // If we landed here from a single run that's already selected,
      // pre-fill slot A so the user only needs to click the second run.
      if (CURRENT_TASK) {
        COMPARE_A = CURRENT_TASK;
        renderRunList();
        updateCompareHint();
      }
      toast("compare mode — pick two runs on the left");
    }).catch(() => {});
    return;
  }
}

function openShortcutsHelp() {
  const back = $("#shortcuts-backdrop");
  back.classList.remove("hidden");
  back.setAttribute("aria-hidden", "false");
  $("#shortcuts-close").focus();
}
function closeShortcutsHelp() {
  const back = $("#shortcuts-backdrop");
  back.classList.add("hidden");
  back.setAttribute("aria-hidden", "true");
}

// ---- DOM wiring for palette --------------------------------------------

$("#palette-open").addEventListener("click", openPalette);
$("#palette-backdrop").addEventListener("click", e => {
  // Click on the backdrop (outside the card) closes; clicks inside the
  // card bubble up to here but with .palette as the closest ancestor.
  if (e.target.id === "palette-backdrop") closePalette();
});
$("#palette-input").addEventListener("input", e => filterPalette(e.target.value));
$("#palette-input").addEventListener("keydown", e => {
  if (e.key === "ArrowDown") { e.preventDefault(); movePalette(1); }
  else if (e.key === "ArrowUp") { e.preventDefault(); movePalette(-1); }
  else if (e.key === "Enter") { e.preventDefault(); activatePalette(PALETTE_INDEX); }
  else if (e.key === "Escape") { e.preventDefault(); closePalette(); }
  else if (e.key === "Home") { e.preventDefault(); PALETTE_INDEX = 0; renderPalette(); }
  else if (e.key === "End") { e.preventDefault(); PALETTE_INDEX = PALETTE_FILTERED.length - 1; renderPalette(); }
});
$("#palette-results").addEventListener("click", e => {
  const li = e.target.closest("li[data-idx]");
  if (!li) return;
  activatePalette(Number(li.dataset.idx));
});
$("#palette-results").addEventListener("mousemove", e => {
  const li = e.target.closest("li[data-idx]");
  if (!li) return;
  const idx = Number(li.dataset.idx);
  if (idx === PALETTE_INDEX) return;
  PALETTE_INDEX = idx;
  $$("#palette-results li[data-idx]").forEach(x => {
    const xi = Number(x.dataset.idx);
    x.classList.toggle("active", xi === PALETTE_INDEX);
    x.setAttribute("aria-selected", xi === PALETTE_INDEX ? "true" : "false");
  });
});

$("#shortcuts-close").addEventListener("click", closeShortcutsHelp);
$("#shortcuts-backdrop").addEventListener("click", e => {
  if (e.target.id === "shortcuts-backdrop") closeShortcutsHelp();
});

// ---- Global keyboard shortcuts -----------------------------------------
//
// Cmd+K / Ctrl+K  → palette
// Cmd+S / Ctrl+S  → save the focused form (config / scenario / playbook)
// Esc             → close palette · cancel inline form · close help
// ?               → open shortcuts help
// g then b/r/a    → jump tabs (Vim/GitHub style — no modifier)

function isTypingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (el.isContentEditable) return true;
  return false;
}

function activeFormContext() {
  // Map the currently-visible work surface to a "save" handler. This
  // lets Cmd+S do the right thing whether the user is in the config form,
  // scenario form, or playbook editor without us having to track focus.
  if (!$("#tab-build").classList.contains("hidden")) {
    if (!$("#builder-config").classList.contains("hidden")) {
      return {save: () => $("#cfg-save").click(), name: "config"};
    }
    if (!$("#builder-scenarios").classList.contains("hidden")) {
      // In raw-files mode, save the focused file rather than the form.
      if (!$("#sc-raw-pane").classList.contains("hidden")) {
        return {save: () => $("#sc-save-raw").click(), name: "scenario file"};
      }
      return {save: () => $("#sc-save").click(), name: "scenario"};
    }
    if (!$("#builder-playbooks").classList.contains("hidden")) {
      return {save: () => $("#pb-save").click(), name: "playbook"};
    }
  }
  return null;
}

window.addEventListener("keydown", e => {
  // Cmd+K / Ctrl+K — palette. Works from any context, including inside
  // a text input.
  if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey
      && (e.key === "k" || e.key === "K")) {
    e.preventDefault();
    if (PALETTE_OPEN) closePalette();
    else openPalette();
    return;
  }

  // Cmd+S / Ctrl+S — save. Works from inside inputs too, since that's
  // exactly where the user is when they want to save.
  if ((e.metaKey || e.ctrlKey) && !e.shiftKey && !e.altKey
      && (e.key === "s" || e.key === "S")) {
    const ctx = activeFormContext();
    if (ctx) {
      e.preventDefault();
      ctx.save();
    }
    return;
  }

  // Esc — close whichever transient surface is open. The palette/help
  // input handlers above also stop at this branch when they're focused;
  // this catch-all covers inline forms (Esc cancels new-scenario) and
  // a user pressing Esc while focused on a non-input.
  if (e.key === "Escape") {
    if (PALETTE_OPEN) { closePalette(); e.preventDefault(); return; }
    if (!$("#shortcuts-backdrop").classList.contains("hidden")) {
      closeShortcutsHelp(); e.preventDefault(); return;
    }
    if (!$("#sc-new-form").classList.contains("hidden")) {
      $("#sc-new-cancel").click(); e.preventDefault(); return;
    }
    return;
  }

  // ? — shortcuts help. Skip if the user is typing into a text input
  // (where ? is a legitimate character).
  if (e.key === "?" && !e.metaKey && !e.ctrlKey && !e.altKey
      && !isTypingTarget(e.target)) {
    e.preventDefault();
    openShortcutsHelp();
    return;
  }

  // "g then b/r/a" chord. Only when not typing.
  if (isTypingTarget(e.target)) return;
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  if (G_PREFIX_TIMER && (e.key === "b" || e.key === "B")) {
    e.preventDefault(); clearTimeout(G_PREFIX_TIMER); G_PREFIX_TIMER = null;
    showTab("build"); return;
  }
  if (G_PREFIX_TIMER && (e.key === "r" || e.key === "R")) {
    e.preventDefault(); clearTimeout(G_PREFIX_TIMER); G_PREFIX_TIMER = null;
    showTab("run"); return;
  }
  if (G_PREFIX_TIMER && (e.key === "a" || e.key === "A")) {
    e.preventDefault(); clearTimeout(G_PREFIX_TIMER); G_PREFIX_TIMER = null;
    showTab("analyze"); return;
  }
  if (e.key === "g" || e.key === "G") {
    e.preventDefault();
    if (G_PREFIX_TIMER) clearTimeout(G_PREFIX_TIMER);
    G_PREFIX_TIMER = setTimeout(() => { G_PREFIX_TIMER = null; }, 900);
    return;
  }
  // Any other key while a g-prefix is pending cancels the chord.
  if (G_PREFIX_TIMER) {
    clearTimeout(G_PREFIX_TIMER); G_PREFIX_TIMER = null;
  }
});

// initial state
(async () => {
  // If we landed here via a shared link like ``#/run/<task-id>/iterations``
  // jump straight there; otherwise fall back to the Build tab.
  const consumed = await consumeLocationHash().catch(() => false);
  if (!consumed) showTab("build");
  // Warm the palette index in the background so the first Cmd+K is
  // instant — the corpus is small (< 30KB) and we already paid for the
  // server's static-file cost.
  loadPaletteItems().catch(() => {});
})();
