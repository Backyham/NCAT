import { api } from "./api.js";

const $ = (id) => document.getElementById(id);

const state = {
  workspace: null,
  projects: [],
  projectId: "",
  project: null,
  subId: "",
  sub: null,
  cues: [],
  segments: [],
  transcript: null,
  selectedId: null,
  dirty: false,
  saving: false,
  busy: false,
  saveTimer: null,
  activeTab: "home",
  jobs: [],
  system: null,
  pollTimer: null,
  qa: [],
  history: { undo: 0, redo: 0 },
  waveformUrl: null,
  styles: [],
  settings: null,
  health: null,
  assetTermbases: [],
  assetTm: [],
  healthTimer: null,
};

function setStatus(text, error = false) {
  const node = $("status");
  node.textContent = text;
  node.classList.toggle("is-ok", !error && !["idle", "busy"].includes(text));
  node.classList.toggle("is-down", error);
}

function toast(message, error = false) {
  const node = $("toast");
  node.textContent = message;
  node.classList.toggle("is-error", error);
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 2800);
}

function formatMs(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "—";
  }
  const total = Math.max(0, Math.round(Number(value)));
  const hours = Math.floor(total / 3600000);
  const minutes = Math.floor((total % 3600000) / 60000);
  const seconds = Math.floor((total % 60000) / 1000);
  const millis = total % 1000;
  const head = hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}`
    : String(minutes).padStart(2, "0");
  return `${head}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

function elapsed(start) {
  if (!start) return "—";
  return `${Math.max(0, Math.round((Date.now() - new Date(start)) / 1000))}s`;
}

function currentSub() {
  if (!state.project) return null;
  return (state.project.subprojects || []).find((item) => item.id === state.subId)
    || state.project.subprojects[0] || null;
}

function currentCue() {
  return state.cues.find((cue) => cue.id === state.selectedId) || null;
}

function projectContext() {
  if (!state.project) return null;
  return state.project.project;
}

async function boot() {
  bindTabs();
  bindHome();
  bindCueScroll();
  bindContext();
  bindProjectForm();
  bindAssets();
  bindEditor();
  bindTranscriptQa();
  bindTerms();
  bindTm();
  bindTranscribe();
  bindSettings();
  bindHealth();
  await loadWorkspace();
  await loadSettings();
  await loadHealth();
  state.pollTimer = setInterval(() => {
    if (state.activeTab === "transcribe") loadJobs().catch(() => {});
    if (!$("health-popover").hidden) loadHealth().catch(() => {});
  }, 3000);
}

async function loadWorkspace() {
  try {
    const payload = await api.workspace();
    state.workspace = payload.workspace;
    state.projects = payload.projects || [];
    state.projectId = "";
    state.project = null;
    state.subId = "";
    state.sub = null;
    renderProjectSelect();
    renderProjectList();
    populateAssetFilters();
    renderAll();
    showHome();
    setStatus("ready");
  } catch (error) {
    setStatus("workspace error", true);
    toast(error.message, true);
  }
}

function renderProjectSelect() {
  const select = $("project-select");
  select.replaceChildren();
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "—";
  select.append(none);
  for (const project of state.projects) {
    const option = document.createElement("option");
    option.value = project.id;
    option.textContent = project.client
      ? `${project.name} · ${project.client}` : project.name;
    select.append(option);
  }
  select.value = state.projectId || "";
}

async function selectProject(id) {
  state.projectId = id;
  if (!id) { state.project = null; state.subId = ""; state.sub = null; renderAll(); showHome(); return; }
  setStatus("loading");
  try {
    const project = await api.project(id);
    state.project = project;
    state.projectId = project.project.id;
    localStorage.setItem("ncat.project", state.projectId);
    renderProjectSelect();
    renderSubprojectSelect();
    const remembered = localStorage.getItem(`ncat.sub.${state.projectId}`);
    const sub = (project.subprojects || []).find((item) => item.id === remembered)
      || project.subprojects[0];
    state.subId = sub ? sub.id : "";
    await loadSub();
    switchTab("assets");
    setStatus("ready");
  } catch (error) {
    setStatus("load failed", true);
    toast(error.message, true);
  }
}

function renderSubprojectSelect() {
  const select = $("subproject-select");
  select.replaceChildren();
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "—";
  select.append(none);
  for (const sub of (state.project && state.project.subprojects) || []) {
    const option = document.createElement("option");
    option.value = sub.id;
    option.textContent = `${sub.name} · ${sub.kind}`;
    select.append(option);
  }
  select.value = state.subId || "";
}

async function loadSub() {
  const sub = currentSub();
  state.sub = sub;
  state.subId = sub ? sub.id : "";
  if (!sub) {
    state.cues = [];
    state.segments = [];
    state.transcript = null;
    state.selectedId = null;
    renderAll();
    return;
  }
  localStorage.setItem(`ncat.sub.${state.projectId}`, state.subId);
  const [cues, segments, transcript] = await Promise.all([
    api.cues(state.projectId, state.subId),
    api.segments(state.projectId, state.subId),
    api.transcript(state.projectId, state.subId),
  ]);
  state.cues = cues || [];
  state.segments = segments || [];
  state.transcript = transcript && transcript.transcript !== undefined
    ? transcript.transcript : transcript;
  state.selectedId = state.cues.length ? state.cues[0].id : null;
  state.dirty = false;
  renderSubprojectSelect();
  renderAll();
  loadHistory().catch(() => {});
  loadStyles().catch(() => {});
}

function renderProjectHeader() {
  if (!state.project) {
    $("project-name").textContent = "Project";
    $("project-meta").textContent = "";
    return;
  }
  const project = state.project.project;
  const sub = currentSub();
  $("project-name").textContent = project.name;
  $("project-meta").textContent = [
    project.client || "no client",
    `${project.source_lang} → ${(project.target_langs || []).join(", ")}`,
    sub ? `${sub.name} · ${sub.kind}` : "no subproject",
    `${state.project.counts.subprojects} subprojects`,
  ].filter(Boolean).join(" · ");
}

function renderAll() {
  renderProjectList();
  renderProjectHeader();
  renderProjectDetail();
  renderContext();
  renderAssets();
  renderCues();
  renderEditor();
  renderTranscript();
  renderTerms();
  renderTm();
  renderEditorContext();
  setStatus(state.project ? "ready" : "idle");
}

function renderProjectList() {
  const list = $("project-list");
  list.replaceChildren();
  $("project-count").textContent = String(state.projects.length);
  for (const project of state.projects) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "project-item";
    button.classList.toggle("is-active", project.id === state.projectId);
    const name = document.createElement("span");
    name.className = "project-item-name";
    name.textContent = project.name;
    const meta = document.createElement("span");
    meta.className = "project-item-meta";
    meta.textContent = [
      project.client || "", `${project.counts.subprojects} subprojects`,
      `${project.counts.cues} cues`,
    ].filter(Boolean).join(" · ");
    button.append(name, meta);
    button.addEventListener("click", () => selectProject(project.id));
    list.append(button);
  }
}

function renderProjectDetail() {
  const body = $("project-detail-body");
  body.replaceChildren();
  if (!state.project) {
    body.textContent = "Select a project.";
    return;
  }
  const project = state.project.project;
  const head = document.createElement("div");
  head.className = "project-head";
  const info = document.createElement("div");
  const title = document.createElement("h2");
  title.textContent = project.name;
  const meta = document.createElement("div");
  meta.className = "project-meta";
  meta.textContent = [
    project.client || "no client",
    `${project.source_lang} → ${(project.target_langs || []).join(", ")}`,
    state.project.counts.subprojects + " subprojects",
  ].join(" · ");
  info.append(title, meta);
  head.append(info);
  body.append(head);

  const table = document.createElement("div");
  table.className = "subproject-list";
  for (const sub of state.project.subprojects || []) {
    const row = document.createElement("div");
    row.className = "subproject-row";
    const main = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = sub.name;
    const detail = document.createElement("div");
    detail.className = "project-meta";
    detail.textContent = [
      sub.kind, sub.counts.cues + " cues",
      sub.state.source && sub.state.source.proxy_path ? "proxy ready" : "no proxy",
      sub.state.asr && sub.state.asr.words ? `${sub.state.asr.words} words` : "",
    ].filter(Boolean).join(" · ");
    main.append(name, detail);
    const actions = document.createElement("div");
    actions.className = "subproject-actions";
    for (const [label, tab] of [["Assets", "assets"], ["Edit", "editor"]]) {
      const button = document.createElement("button");
      button.className = "btn btn-quiet";
      button.type = "button";
      button.textContent = label;
      button.addEventListener("click", async () => {
        state.subId = sub.id;
        await loadSub();
        switchTab(tab);
      });
      actions.append(button);
    }
    row.append(main, actions);
    table.append(row);
  }
  body.append(table);
}

function renderContext() {
  $("context-note").textContent = state.project
    ? [state.project.project.client, state.project.project.name,
       currentSub() ? currentSub().name : ""].filter(Boolean).join(" / ")
    : "No project selected";
  $("subproject-select").disabled = !state.project;
}

function renderEditorContext() {
  const node = $("editor-context");
  node.replaceChildren();
  if (!state.project) { node.textContent = "No project selected"; return; }
  const project = state.project.project;
  const sub = currentSub();
  const termbases = (state.project.termbases || [])
    .filter((tb) => tb.level === "project"
      || (sub && tb.subproject_id === sub.id));
  const tms = (state.project.tm || [])
    .filter((tm) => tm.level === "project"
      || (sub && tm.subproject_id === sub.id));
  const parts = [
    ["Project", project.name],
    ["Client", project.client || "—"],
    ["Subproject", sub ? sub.name : "—"],
    ["Termbases", termbases.map((tb) => tb.name).join(", ") || "—"],
    ["TM", tms.map((tm) => tm.name).join(", ") || "—"],
  ];
  for (const [label, value] of parts) {
    const span = document.createElement("span");
    const strong = document.createElement("strong");
    strong.textContent = `${label}: `;
    span.append(strong, document.createTextNode(value));
    node.append(span);
  }
}

function renderAssets() {
  const sub = currentSub();
  const summary = $("asset-summary");
  if (!sub) {
    summary.textContent = "Select a subproject.";
    $("asset-media-meta").textContent = "No media.";
    $("asset-video").removeAttribute("src");
    return;
  }
  const source = sub.state.source;
  summary.textContent = [
    `${sub.name} (${sub.kind})`,
    source ? source.path : "no source",
    source && source.proxy_path ? "proxy ready" : "no proxy",
    source && source.audio_path ? "audio ready" : "no audio",
    source && source.duration_ms
      ? `duration ${(source.duration_ms / 1000).toFixed(1)}s` : "",
  ].filter(Boolean).join(" · ");
  const video = $("asset-video");
  const proxy = sub.files && sub.files.proxy;
  if (proxy) video.src = proxy; else video.removeAttribute("src");
  $("asset-media-meta").textContent = proxy
    ? `Proxy: ${proxy}` : "Run ingest to create media assets.";
}

function renderCues() {
  const list = $("cue-list");
  const rowHeight = 56;
  const total = state.cues.length;
  list.replaceChildren();
  $("cue-count").textContent = String(total);
  if (!total) {
    const empty = document.createElement("div");
    empty.className = "cue-empty";
    empty.textContent = "No cues. Import ASS or run segment.";
    list.append(empty);
    return;
  }
  const viewport = list.clientHeight || 640;
  const scrollTop = list.scrollTop || 0;
  const start = Math.max(0, Math.floor(scrollTop / rowHeight) - 6);
  const end = Math.min(total, start + Math.ceil(viewport / rowHeight) + 12);
  const top = document.createElement("div");
  top.style.height = `${start * rowHeight}px`;
  list.append(top);
  const fragment = document.createDocumentFragment();
  state.cues.slice(start, end).forEach((cue, offset) => {
    const index = start + offset;
    const row = document.createElement("button");
    row.type = "button";
    row.className = "cue-row";
    row.style.height = `${rowHeight}px`;
    row.classList.toggle("is-active", cue.id === state.selectedId);
    row.classList.toggle("needs-timing", Boolean(cue.needs_timing));
    row.classList.toggle("empty-target", !cue.target_text);
    const time = document.createElement("span");
    time.className = "cue-time";
    time.textContent = `${String(index + 1).padStart(3, "0")}  ${formatMs(cue.start_ms)}`;
    const body = document.createElement("span");
    body.className = "cue-body";
    const target = document.createElement("span");
    target.className = "cue-target";
    target.textContent = (cue.target_text || cue.source_text || "—")
      .replaceAll("\\N", " ");
    const source = document.createElement("span");
    source.className = "cue-source";
    source.textContent = (cue.source_text || "").replaceAll("\\N", " ");
    body.append(target, source);
    const meta = document.createElement("span");
    meta.className = "cue-meta";
    const metrics = cue.metrics || {};
    meta.textContent = [
      metrics.cps_target != null ? `CPS ${metrics.cps_target}` : "",
      cue.locked ? "locked" : "",
      (metrics.warnings || []).length ? metrics.warnings.join(",") : "",
    ].filter(Boolean).join(" · ");
    row.append(time, body, meta);
    row.addEventListener("click", () => selectCue(cue.id));
    fragment.append(row);
  });
  list.append(fragment);
  const bottom = document.createElement("div");
  bottom.style.height = `${(total - end) * rowHeight}px`;
  list.append(bottom);
}

function selectCue(id) {
  if (state.dirty && state.selectedId && state.selectedId !== id) {
    saveCue().catch((error) => toast(error.message, true));
  }
  state.selectedId = id;
  renderCues();
  renderEditor();
  const cue = currentCue();
  const wrapper = state.activeTab === "editor" ? $("video") : $("asset-video");
  if (cue && cue.start_ms != null && wrapper && wrapper.readyState >= 1) {
    wrapper.currentTime = cue.start_ms / 1000;
  }
}

function renderEditor() {
  const cue = currentCue();
  const disabled = !cue;
  $("edit-state").textContent = cue ? "editing" : "no selection";
  for (const id of ["target-text", "source-text", "start-ms", "end-ms",
                    "cue-style", "cue-locked", "save-cue", "split-cue",
                    "merge-cue", "delete-cue"]) {
    $(id).disabled = disabled;
  }
  if (!cue) {
    $("target-text").value = "";
    $("source-text").value = "";
    $("start-ms").value = "";
    $("end-ms").value = "";
    $("cue-style").value = "";
    $("cue-locked").checked = false;
    $("metrics").textContent = "";
    return;
  }
  if (document.activeElement !== $("target-text")) {
    $("target-text").value = cue.target_text || "";
  }
  if (document.activeElement !== $("source-text")) {
    $("source-text").value = cue.source_text || "";
  }
  $("start-ms").value = cue.start_ms ?? "";
  $("end-ms").value = cue.end_ms ?? "";
  $("cue-style").value = cue.style || "Default";
  $("cue-locked").checked = Boolean(cue.locked);
  renderStylePreview();
  const metrics = cue.metrics || {};
  $("metrics").textContent = [
    `duration ${metrics.duration_ms != null ? metrics.duration_ms + "ms" : "—"}`,
    `CPS source ${metrics.cps_source ?? "—"}`,
    `CPS target ${metrics.cps_target ?? "—"}`,
    `lines ${metrics.lines ?? "—"}`,
    `max line ${metrics.line_length ?? "—"}`,
    (metrics.warnings || []).length
      ? `warnings: ${metrics.warnings.join(", ")}` : "no warnings",
  ].join("  ·  ");
}

function renderTranscript() {
  const transcript = state.transcript;
  if (!transcript) {
    $("transcript-preview").textContent = "No transcript yet.";
    return;
  }
  const text = (transcript.text || "").replace(/\s+/g, " ").trim();
  const tokens = (transcript.metadata
    && transcript.metadata.asr_token_confidence) || [];
  const scores = tokens.map((item) => item.confidence)
    .filter((value) => typeof value === "number");
  const average = scores.length
    ? (scores.reduce((sum, value) => sum + value, 0) / scores.length) : null;
  $("transcript-preview").textContent =
    `${transcript.backend || "asr"} · ${transcript.language || ""} · `
    + `${(transcript.words || []).length} words · `
    + (average == null ? "no token confidence"
      : `token confidence avg ${average.toFixed(3)} (${scores.length})`)
    + "\n" + (text.length > 500 ? text.slice(0, 500) + "…" : text);
}

function readEditor() {
  const parse = (value) => value === "" || value == null ? null : Number(value);
  return {
    target_text: $("target-text").value,
    source_text: $("source-text").value,
    start_ms: parse($("start-ms").value),
    end_ms: parse($("end-ms").value),
    style: $("cue-style").value || "Default",
    locked: $("cue-locked").checked,
  };
}

function markDirty() {
  state.dirty = true;
  $("edit-state").textContent = "unsaved";
  clearTimeout(state.saveTimer);
  state.saveTimer = setTimeout(() => {
    saveCue().catch((error) => toast(error.message, true));
  }, 900);
}

async function saveCue() {
  const cue = currentCue();
  if (!cue || !state.project || !state.subId || state.saving) return;
  state.saving = true;
  clearTimeout(state.saveTimer);
  try {
    const updated = await api.patchCue(
      state.projectId, state.subId, cue.id, readEditor());
    const index = state.cues.findIndex((item) => item.id === cue.id);
    if (index >= 0) state.cues[index] = updated;
    state.dirty = false;
    $("edit-state").textContent = "saved";
    renderCues();
    renderEditor();
    setStatus("saved");
  } finally {
    state.saving = false;
  }
}

async function refreshSub() {
  if (!state.project) return;
  const project = await api.project(state.projectId);
  state.project = project;
  await loadSub();
}

async function runAction(action, body = {}) {
  if (!state.project || !state.subId) {
    toast("Select a project and subproject first", true);
    return;
  }
  if (state.busy) return;
  state.busy = true;
  setStatus(`busy: ${action}`);
  try {
    const payload = await api.action(state.projectId, state.subId, action, body);
    await refreshSub();
    const detail = payload.ass || payload.render || payload.output;
    toast(detail ? `${action}: ${detail}` : `${action} done`);
    setStatus(action === "transcribe" ? "transcribed" : "ready");
  } catch (error) {
    setStatus(`${action} failed`, true);
    toast(error.message, true);
  } finally {
    state.busy = false;
  }
}

function populateAssetFilters() {
  for (const id of ["terms-project-filter", "tm-project-filter"]) {
    const select = $(id);
    const current = select.value;
    select.replaceChildren();
    const all = document.createElement("option");
    all.value = "";
    all.textContent = "All projects";
    select.append(all);
    for (const project of state.projects) {
      const option = document.createElement("option");
      option.value = project.id;
      option.textContent = project.client
        ? `${project.name} · ${project.client}` : project.name;
      select.append(option);
    }
    select.value = state.projects.some((p) => p.id === current)
      ? current : "";
  }
}

function renderTerms() {
  const termbaseBody = $("termbase-table").querySelector("tbody");
  termbaseBody.replaceChildren();
  const termBody = $("term-table").querySelector("tbody");
  termBody.replaceChildren();
  const data = state.assetTermbases || [];
  let total = 0;
  for (const item of data) {
    const entries = item.entries || [];
    total += entries.length;
    const row = document.createElement("tr");
    for (const value of [item.termbase.name, item.project_name,
                         item.termbase.level, entries.length]) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    }
    termbaseBody.append(row);
    for (const term of entries) {
      const entry = document.createElement("tr");
      for (const value of [item.project_name, term.source, term.target,
                           term.status, term.context || ""]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        entry.append(cell);
      }
      termBody.append(entry);
    }
  }
  $("term-count").textContent = String(total);
}

function renderTm() {
  const tmBody = $("tm-table").querySelector("tbody");
  tmBody.replaceChildren();
  const unitBody = $("tm-unit-table").querySelector("tbody");
  unitBody.replaceChildren();
  const data = state.assetTm || [];
  let total = 0;
  for (const item of data) {
    const units = item.units || [];
    total += units.length;
    const row = document.createElement("tr");
    for (const value of [item.tm.name, item.project_name,
                         item.tm.level, units.length]) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    }
    tmBody.append(row);
  }
  for (const item of data) {
    for (const unit of (item.units || []).slice(0, 500)) {
      const row = document.createElement("tr");
      for (const value of [item.project_name, unit.source, unit.target,
                           `${unit.source_lang}→${unit.target_lang}`]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      unitBody.append(row);
    }
  }
  $("tm-count").textContent = String(total);
}

async function loadTerms() {
  const filter = $("terms-project-filter").value;
  state.assetTermbases = await api.allTermbases(filter || undefined);
  renderTerms();
}

async function loadTm() {
  const filter = $("tm-project-filter").value;
  state.assetTm = await api.allTm(filter || undefined);
  renderTm();
}

async function loadHealth() {
  try {
    state.health = await api.healthStatus();
    renderHealth();
  } catch (error) {
    $("health-dot").className = "health-dot is-down";
  }
}

function healthCard(id, title, text) {
  const node = $(id);
  if (!node) return;
  node.replaceChildren();
  const strong = document.createElement("strong");
  strong.textContent = title;
  node.append(strong, document.createTextNode(text));
}

function renderHealth() {
  const health = state.health || {};
  const gpu = health.gpu || {};
  healthCard("hw-gpu", gpu.name || "GPU —",
    gpu.memory_total_mb
      ? `${gpu.memory_used_mb}/${gpu.memory_total_mb} MiB · ${gpu.utilization}% · ${gpu.temperature_c}°C`
      : "unavailable");
  const cpu = health.cpu || {};
  healthCard("hw-cpu", "CPU load",
    [cpu.load1, cpu.load5, cpu.load15]
      .map((value) => value == null ? "—" : Number(value).toFixed(2))
      .join(" / "));
  const mem = health.memory || {};
  healthCard("hw-mem", "RAM",
    mem.total_mb
      ? `${((mem.total_mb - mem.available_mb) / 1024).toFixed(1)} / ${(mem.total_mb / 1024).toFixed(1)} GiB used`
      : "—");
  const disk = health.disk || {};
  healthCard("hw-disk", "Disk",
    disk.total_gb ? `${disk.used_gb} / ${disk.total_gb} GB used` : "—");
  const services = $("health-services");
  services.replaceChildren();
  const heading = document.createElement("div");
  heading.className = "health-services-head";
  heading.textContent = "Services";
  services.append(heading);
  let allOk = true;
  for (const service of health.services || []) {
    const row = document.createElement("div");
    row.className = "health-service";
    const dot = document.createElement("span");
    const ok = service.state === "ok";
    if (service.state === "unreachable") allOk = false;
    dot.className = "service-dot " + (
      ok ? "is-ok" : service.state === "unreachable" ? "is-down" : "");
    const label = document.createElement("span");
    label.textContent = `${service.name} · ${service.state}`;
    row.append(dot, label);
    if (service.url) {
      const url = document.createElement("span");
      url.className = "service-url";
      url.textContent = service.url;
      row.append(url);
    }
    services.append(row);
  }
  $("health-dot").className = "health-dot " + (allOk ? "is-ok" : "is-down");
}

function bindHealth() {
  $("health-toggle").addEventListener("click", (event) => {
    event.stopPropagation();
    const popover = $("health-popover");
    popover.hidden = !popover.hidden;
    if (!popover.hidden) loadHealth().catch(() => {});
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#health-popover")
        && !event.target.closest("#health-toggle")) {
      $("health-popover").hidden = true;
    }
  });
}

async function loadSettings() {
  try {
    state.settings = await api.settings();
    const asr = state.settings.asr || {};
    const mt = state.settings.mt || {};
    $("settings-asr-mode").value = asr.mode || "local";
    $("settings-mt-mode").value = mt.mode || "local";
    $("settings-asr-key-state").textContent = asr.api_key_set
      ? `ASR key configured (${asr.api_key_source})`
      : "No ASR API key (local mode needs none)";
    $("settings-mt-key-state").textContent = mt.api_key_set
      ? `MT key configured (${mt.api_key_source})`
      : "No MT API key (local mode needs none)";
    $("settings-asr-api-key").value = "";
    $("settings-mt-api-key").value = "";
    $("settings-asr-clear-key").checked = false;
    $("settings-mt-clear-key").checked = false;
    $("settings-message").textContent = "";
    $("settings-message").classList.remove("is-error");
  } catch (error) {
    $("settings-message").textContent = error.message;
    $("settings-message").classList.add("is-error");
  }
}

function bindSettings() {
  $("settings-save").addEventListener("click", async () => {
    const body = {
      asr: {
        mode: $("settings-asr-mode").value,
        clear_api_key: $("settings-asr-clear-key").checked,
      },
      mt: {
        mode: $("settings-mt-mode").value,
        clear_api_key: $("settings-mt-clear-key").checked,
      },
    };
    const asrKey = $("settings-asr-api-key").value.trim();
    const mtKey = $("settings-mt-api-key").value.trim();
    if (asrKey) body.asr.api_key = asrKey;
    if (mtKey) body.mt.api_key = mtKey;
    try {
      state.settings = await api.saveSettings(body);
      $("settings-message").textContent = "Settings saved.";
      $("settings-message").classList.remove("is-error");
      await loadSettings();
      await loadHealth();
      toast("settings saved");
    } catch (error) {
      $("settings-message").textContent = error.message;
      $("settings-message").classList.add("is-error");
    }
  });
}

async function loadJobs() {
  try {
    state.jobs = await api.transcribeJobs();
    renderJobs();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderJobs() {
  const body = $("job-table").querySelector("tbody");
  body.replaceChildren();
  for (const job of state.jobs) {
    const row = document.createElement("tr");
    const values = [
      job.id.slice(-8), job.status, job.stage,
      job.started_at ? elapsed(job.started_at) : "—",
      job.result ? `${job.result.words || 0} words` : (job.error || ""),
    ];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    }
    body.append(row);
  }
}

async function startTranscribe() {
  try {
    const job = await api.createJob({
      audio: $("asr-audio").value.trim(),
      language: $("asr-language").value.trim(),
    });
    $("transcribe-message").textContent = `Queued ${job.id}`;
    $("transcribe-message").classList.remove("is-error");
    $("transcribe-dialog").close();
    await loadJobs();
  } catch (error) {
    $("transcribe-message").textContent = error.message;
    $("transcribe-message").classList.add("is-error");
    toast(error.message, true);
  }
}

async function loadStyles() {
  if (!state.project || !state.subId) { state.styles = []; return; }
  try {
    const payload = await api.styles(state.projectId, state.subId);
    state.styles = payload.styles || [];
  } catch { state.styles = []; }
  renderStylePreview();
}

function assColor(value) {
  const text = String(value || "").replace(/&H/i, "").replace(/&/g, "");
  const hex = text.padStart(8, "0").slice(-6);
  const b = hex.slice(0, 2), g = hex.slice(2, 4), r = hex.slice(4, 6);
  return `#${r}${g}${b}`;
}

function renderStylePreview() {
  const node = $("style-preview");
  if (!node) return;
  const cue = currentCue();
  if (!cue) { node.textContent = "样式预览"; return; }
  const style = state.styles.find((item) => item.name === cue.style)
    || state.styles.find((item) => item.name === "Default");
  node.textContent = cue.target_text || cue.source_text || "样式预览";
  if (!style) { node.removeAttribute("style"); return; }
  node.style.fontFamily = style.Fontname || "sans-serif";
  node.style.fontSize = `${Math.min(36, Math.max(14, Number(style.Fontsize) || 20))}px`;
  node.style.color = assColor(style.PrimaryColour);
  node.style.textShadow = style.Outline && Number(style.Outline) > 0
    ? `-1px -1px 0 ${assColor(style.OutlineColour)}, 1px 1px 0 ${assColor(style.OutlineColour)}`
    : "none";
}

async function loadHistory() {
  if (!state.project || !state.subId) { state.history = { undo: 0, redo: 0 }; return; }
  state.history = await api.history(state.projectId, state.subId);
  renderHistory();
}

function renderHistory() {
  $("undo").disabled = !state.history.undo;
  $("redo").disabled = !state.history.redo;
}

async function loadQa() {
  if (!state.project || !state.subId) { state.qa = []; renderQa(); return; }
  state.qa = await api.qa(state.projectId, state.subId);
  state.qa = state.qa || [];
  renderQa();
}

function renderQa() {
  const body = $("qa-table").querySelector("tbody");
  body.replaceChildren();
  $("qa-count").textContent = String(state.qa.length);
  if (!state.qa.length) {
    $("qa-message").textContent = "No QA items. Run QA.";
    return;
  }
  for (const item of state.qa) {
    const row = document.createElement("tr");
    const severity = document.createElement("td");
    severity.textContent = item.severity;
    severity.className = item.severity === "error" ? "qa-error" : "qa-warning";
    const kind = document.createElement("td");
    kind.textContent = item.kind;
    const message = document.createElement("td");
    message.textContent = item.message;
    const evidence = document.createElement("td");
    evidence.textContent = item.evidence || "";
    const action = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "qa-action";
    for (const [label, status] of [["Accept", "accepted"],
                                   ["Ignore", "ignored"]]) {
      const button = document.createElement("button");
      button.className = "btn btn-quiet";
      button.type = "button";
      button.textContent = label;
      button.disabled = item.status !== "open";
      button.addEventListener("click", async () => {
        try {
          await api.patchQa(state.projectId, state.subId, item.id, status);
          await loadQa();
        } catch (error) { toast(error.message, true); }
      });
      wrap.append(button);
    }
    action.append(wrap);
    row.append(severity, kind, message, evidence, action);
    body.append(row);
  }
}

async function runQa() {
  if (!state.project || !state.subId) { toast("Select a subproject first", true); return; }
  $("qa-message").textContent = "Running QA…";
  try {
    await api.runQa(state.projectId, state.subId);
    await loadQa();
    $("qa-message").textContent = `QA items: ${state.qa.length}`;
    toast("QA complete");
  } catch (error) {
    $("qa-message").textContent = error.message;
    toast(error.message, true);
  }
}

function renderTranscriptTab() {
  const transcript = state.transcript;
  const text = transcript
    ? (transcript.text || "").trim()
    : "No transcript.";
  $("transcript-full").textContent = text || "No transcript.";
  const tokens = (transcript && transcript.metadata
    && transcript.metadata.asr_token_confidence) || [];
  const tokenMap = {};
  for (const token of tokens) {
    if (token.token) tokenMap[token.index] = token;
  }
  $("transcript-meta").textContent = transcript
    ? `${transcript.backend || "asr"} · ${transcript.language || ""} · `
      + `${(transcript.words || []).length} words`
    : "";
  const body = $("word-table").querySelector("tbody");
  body.replaceChildren();
  const words = (transcript && transcript.words) || [];
  $("word-count").textContent = String(words.length);
  for (const word of words.slice(0, 500)) {
    const row = document.createElement("tr");
    const confidence = word.confidence == null ? "—"
      : Number(word.confidence).toFixed(3);
    for (const value of [word.index, word.text, formatMs(word.start_ms),
                         formatMs(word.end_ms), confidence]) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    }
    body.append(row);
  }
}

function bindTranscriptQa() {
  $("run-qa").addEventListener("click", runQa);
  $("undo").addEventListener("click", async () => {
    if (!state.project || !state.subId) return;
    try {
      const result = await api.undo(state.projectId, state.subId);
      if (result.changed) { await refreshSub(); toast("undo"); }
      else toast("nothing to undo", true);
    } catch (error) { toast(error.message, true); }
  });
  $("redo").addEventListener("click", async () => {
    if (!state.project || !state.subId) return;
    try {
      const result = await api.redo(state.projectId, state.subId);
      if (result.changed) { await refreshSub(); toast("redo"); }
      else toast("nothing to redo", true);
    } catch (error) { toast(error.message, true); }
  });
}

async function drawWaveform(url) {
  const canvas = $("waveform");
  if (!canvas) return;
  if (state.waveformUrl === url) return;
  state.waveformUrl = url;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.clearRect(0, 0, canvas.width, canvas.height);
  try {
    const response = await fetch(url);
    const buffer = await response.arrayBuffer();
    const audio = new AudioContext();
    const decoded = await audio.decodeAudioData(buffer);
    const data = decoded.getChannelData(0);
    const width = canvas.width = canvas.clientWidth || 600;
    const height = canvas.height = 72;
    const step = Math.max(1, Math.floor(data.length / width));
    context.fillStyle = "rgba(16,185,129,0.55)";
    for (let x = 0; x < width; x += 2) {
      let min = 1, max = -1;
      const start = x * step;
      for (let i = 0; i < step; i += 32) {
        const value = data[start + i] || 0;
        min = Math.min(min, value); max = Math.max(max, value);
      }
      const y1 = (1 + min) * height / 2;
      const y2 = (1 + max) * height / 2;
      context.fillRect(x, y1, 1.5, Math.max(1, y2 - y1));
    }
    audio.close();
  } catch (error) {
    context.fillStyle = "rgba(255,255,255,0.25)";
    context.fillText("waveform unavailable", 10, 38);
  }
}

function bindCueScroll() {
  const list = $("cue-list");
  let scheduled = false;
  list.addEventListener("scroll", () => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      renderCues();
    });
  });
}

function bindTabs() {
  document.querySelectorAll("#tabs .tab").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
  document.querySelectorAll("#project-tabs [data-project-tab]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.projectTab));
  });
}

function showHome() {
  state.activeTab = "home";
  $("home-view").hidden = false;
  $("project-shell").hidden = true;
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.remove("is-active");
  });
  document.querySelectorAll("#tabs .tab").forEach((button) => {
    button.classList.remove("is-active");
  });
}

function showGlobal(name) {
  state.activeTab = name;
  $("home-view").hidden = true;
  $("project-shell").hidden = true;
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.panel === name);
  });
  document.querySelectorAll("#tabs .tab").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tab === name);
  });
}

function showProject(name) {
  state.activeTab = name;
  $("home-view").hidden = true;
  $("project-shell").hidden = false;
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.remove("is-active");
  });
  document.querySelectorAll("#tabs .tab").forEach((button) => {
    button.classList.remove("is-active");
  });
  document.querySelectorAll("[data-project-panel]").forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.projectPanel === name);
    panel.hidden = panel.dataset.projectPanel !== name;
  });
  document.querySelectorAll("#project-tabs [data-project-tab]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.projectTab === name);
  });
  if (name === "transcript") renderTranscriptTab();
  if (name === "qa") loadQa().catch((error) => toast(error.message, true));
  requestAnimationFrame(() => {
    renderCues();
    renderEditor();
  });
}

function switchTab(name) {
  const projectTabs = ["assets", "editor", "transcript", "qa"];
  if (projectTabs.includes(name)) {
    if (!state.project) {
      toast("Select a project first", true);
      showHome();
      return;
    }
    showProject(name);
    return;
  }
  if (name === "home" || name === "projects") {
    showHome();
    return;
  }
  showGlobal(name);
  if (name === "terms") {
    populateAssetFilters();
    loadTerms().catch((error) => toast(error.message, true));
  }
  if (name === "tm") {
    populateAssetFilters();
    loadTm().catch((error) => toast(error.message, true));
  }
  if (name === "transcribe") loadJobs().catch((error) => toast(error.message, true));
  if (name === "settings") loadSettings().catch((error) => toast(error.message, true));
}

function bindContext() {
  $("project-select").addEventListener("change", (event) => {
    selectProject(event.target.value).then(() => {
      state.effectiveTerms = [];
      state.effectiveTm = [];
    });
  });
  $("subproject-select").addEventListener("change", async (event) => {
    state.subId = event.target.value;
    state.effectiveTerms = [];
    state.effectiveTm = [];
    await loadSub();
    if (state.activeTab === "terms") await loadTerms();
    if (state.activeTab === "tm") await loadTm();
  });
  $("refresh-context").addEventListener("click", async () => {
    if (state.projectId) await selectProject(state.projectId);
    else await loadWorkspace();
  });
}

function bindProjectForm() {
  $("project-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = $("new-name").value.trim();
    if (!name) {
      $("project-message").textContent = "Project name is required.";
      $("project-message").classList.add("is-error");
      return;
    }
    try {
      const created = await api.createProject({
        name,
        client: $("new-client").value.trim(),
        source_lang: $("new-source-lang").value.trim() || "en",
        target_langs: [$("new-target-lang").value.trim() || "zh-Hans"],
        source: $("new-source").value.trim() || null,
        subproject_name: $("new-subproject-name").value.trim() || "Main",
      });
      $("project-message").textContent = "Project created.";
      $("project-message").classList.remove("is-error");
      $("project-dialog").close();
      await loadWorkspace();
      await selectProject(created.project.id);
    } catch (error) {
      $("project-message").textContent = error.message;
      $("project-message").classList.add("is-error");
    }
  });
  $("cancel-project").addEventListener("click", () => {
    $("project-dialog").close();
    $("project-message").textContent = "";
    $("project-message").classList.remove("is-error");
  });
  $("open-subproject").addEventListener("click", () => {
    if (!state.project) { toast("Select a project first", true); return; }
    $("subproject-dialog").showModal();
  });
  $("cancel-subproject").addEventListener("click", () => {
    $("subproject-dialog").close();
  });
  $("add-subproject").addEventListener("click", async () => {
    if (!state.project) { toast("Select a project first", true); return; }
    try {
      const sub = await api.createSubproject(state.projectId, {
        name: $("sub-name").value.trim() || "Subproject",
        kind: $("sub-kind").value,
        source_path: $("sub-source").value.trim() || null,
      });
      $("sub-name").value = "";
      $("sub-source").value = "";
      $("subproject-dialog").close();
      await selectProject(state.projectId);
      state.subId = sub.id;
      await loadSub();
      switchTab("assets");
      toast("subproject added");
    } catch (error) { toast(error.message, true); }
  });
}

function bindHome() {
  $("new-project").addEventListener("click", () => {
    $("project-dialog").showModal();
  });
  $("home-brand").addEventListener("click", showHome);
  $("home-button").addEventListener("click", showHome);
}

function bindAssets() {
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const action = button.dataset.action;
      if (action === "ingest") {
        await runAction("ingest", {
          height: Number($("ingest-height").value) || 720,
          encoder: $("ingest-encoder").value.trim() || undefined,
        });
      } else if (action === "segment") {
        if (!window.confirm("Replace segments and cues from the transcript?")) return;
        await runAction("segment", { propose: true, force: true });
      } else if (action === "align") {
        await runAction("align", {});
      } else if (action === "translate") {
        await runAction("translate", {});
      } else if (action === "style") {
        const template = window.prompt("ASS template path (optional)", "");
        await runAction("style", template ? { template } : {});
      } else if (action === "render") {
        await runAction("render", { preset: "original" });
      } else if (action === "import-ass") {
        if (!window.confirm("Importing ASS replaces current cues and segments. Continue?")) return;
        await runAction("import-ass", {
          path: $("ass-path").value.trim(),
          bilingual_only: $("ass-bilingual-only").checked,
        });
      } else if (action === "import-text") {
        if (!window.confirm("Importing text replaces current cues and segments. Continue?")) return;
        await runAction("segment", {
          propose: false,
          import_path: $("text-source-path").value.trim(),
          target: $("text-target-path").value.trim() || null,
          force: true,
        });
      }
    });
  });
}

function bindEditor() {
  for (const id of ["target-text", "source-text", "start-ms", "end-ms",
                    "cue-style", "cue-locked"]) {
    $(id).addEventListener("input", markDirty);
    $(id).addEventListener("change", markDirty);
  }
  $("save-cue").addEventListener("click", () => saveCue().catch(
    (error) => toast(error.message, true)));
  $("split-cue").addEventListener("click", async () => {
    const cue = currentCue();
    if (!cue) return;
    await saveCue();
    try {
      const result = await api.splitCue(state.projectId, state.subId, cue.id,
        { ratio: 0.5 });
      await refreshSub();
      state.selectedId = result.cues && result.cues[0]
        ? result.cues[0].id : cue.id;
      renderAll();
      toast("cue split");
    } catch (error) { toast(error.message, true); }
  });
  $("merge-cue").addEventListener("click", async () => {
    const cue = currentCue();
    const index = state.cues.findIndex((item) => item.id === (cue && cue.id));
    const next = index >= 0 ? state.cues[index + 1] : null;
    if (!cue || !next) { toast("No next cue", true); return; }
    await saveCue();
    try {
      const merged = await api.mergeCue(
        state.projectId, state.subId, cue.id, next.id);
      await refreshSub();
      state.selectedId = merged.id;
      renderAll();
      toast("cue merged");
    } catch (error) { toast(error.message, true); }
  });
  $("delete-cue").addEventListener("click", async () => {
    const cue = currentCue();
    if (!cue) return;
    if (!window.confirm("Delete this cue?")) return;
    try {
      await api.deleteCue(state.projectId, state.subId, cue.id);
      await refreshSub();
      toast("cue deleted");
    } catch (error) { toast(error.message, true); }
  });
  const video = $("video");
  video.addEventListener("timeupdate", () => {
    if (video.duration) {
      $("preview-time").textContent = formatMs(video.currentTime * 1000);
      $("seek").value = String(
        Math.round((video.currentTime / video.duration) * 1000));
    }
  });
  $("seek").addEventListener("input", () => {
    if (video.duration) {
      video.currentTime = (Number($("seek").value) / 1000) * video.duration;
    }
  });
  document.addEventListener("keydown", (event) => {
    const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(
      document.activeElement && document.activeElement.tagName);
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      saveCue().catch((error) => toast(error.message, true));
      return;
    }
    if (editing || state.activeTab !== "editor") return;
    if (event.key === "j" || event.key === "J") {
      const index = state.cues.findIndex((cue) => cue.id === state.selectedId);
      if (index + 1 < state.cues.length) selectCue(state.cues[index + 1].id);
    } else if (event.key === "k" || event.key === "K") {
      const index = state.cues.findIndex((cue) => cue.id === state.selectedId);
      if (index > 0) selectCue(state.cues[index - 1].id);
    } else if (event.key === " ") {
      event.preventDefault();
      const video = $("video");
      if (video.paused) video.play().catch(() => {}); else video.pause();
    } else if (event.key === "Enter") {
      $("target-text").focus();
    }
  });
}

function assetTargetProject(filterId) {
  const filter = $(filterId).value;
  return filter || state.projectId || "";
}

function bindTerms() {
  $("terms-project-filter").addEventListener("change", () => {
    loadTerms().catch((error) => toast(error.message, true));
  });
  $("import-termbase").addEventListener("click", async () => {
    const projectId = assetTargetProject("terms-project-filter");
    if (!projectId) { toast("Select a project filter or open a project first", true); return; }
    try {
      const level = $("termbase-level").value;
      await api.importTermbase(projectId, {
        name: $("termbase-name").value.trim() || undefined,
        path: $("termbase-path").value.trim(),
        level,
        subproject_id: level === "subproject" && projectId === state.projectId
          ? state.subId : null,
      });
      await loadTerms();
      if (projectId === state.projectId) await refreshSub();
      toast("termbase imported");
    } catch (error) { toast(error.message, true); }
  });
}

function bindTm() {
  $("tm-project-filter").addEventListener("change", () => {
    loadTm().catch((error) => toast(error.message, true));
  });
  $("import-tm").addEventListener("click", async () => {
    const projectId = assetTargetProject("tm-project-filter");
    if (!projectId) { toast("Select a project filter or open a project first", true); return; }
    try {
      const level = $("tm-level").value;
      await api.importTm(projectId, {
        name: $("tm-name").value.trim() || undefined,
        source_path: $("tm-source-path").value.trim(),
        target_path: $("tm-target-path").value.trim(),
        level,
        subproject_id: level === "subproject" && projectId === state.projectId
          ? state.subId : null,
      });
      await loadTm();
      if (projectId === state.projectId) await refreshSub();
      toast("TM imported");
    } catch (error) { toast(error.message, true); }
  });
}

function bindTranscribe() {
  $("transcribe-form").addEventListener("submit", (event) => {
    event.preventDefault();
  });
  $("start-transcribe").addEventListener("click", startTranscribe);
  $("open-transcribe").addEventListener("click", () => {
    $("transcribe-dialog").showModal();
  });
  $("cancel-transcribe").addEventListener("click", () => {
    $("transcribe-dialog").close();
  });
  $("refresh-jobs").addEventListener("click", () => loadJobs().catch(
    (error) => toast(error.message, true)));
}

// Render video proxy in editor whenever subproject changes.
const originalRenderAssets = renderAssets;
renderAssets = function () {
  originalRenderAssets();
  const sub = currentSub();
  const video = $("video");
  const proxy = sub && sub.files && sub.files.proxy;
  if (proxy) video.src = proxy; else video.removeAttribute("src");
  $("preview-meta").textContent = proxy
    ? "Proxy ready" : "No proxy yet. Run ingest.";
  const audio = sub && sub.files && sub.files.audio;
  if (audio) drawWaveform(audio).catch(() => {});
  else {
    const canvas = $("waveform");
    if (canvas) {
      const context = canvas.getContext("2d");
      if (context) context.clearRect(0, 0, canvas.width, canvas.height);
    }
    state.waveformUrl = null;
  }
  renderEditorContext();
};

// Load terms/TM after selecting a subproject and when tabs open.
const originalRenderAll = renderAll;
renderAll = function () {
  originalRenderAll();
};

boot();
