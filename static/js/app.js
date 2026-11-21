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
  screen: "home",
  sidebarOpen: false,
  sidebarPinned: false,
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
  suggest: null,
  stylePayload: null,
  providers: [],
  currentProvider: null,
  providerDialogEditing: false,
  activeProviders: { asr: "", mt: "" },
  providerModels: {},
  providerModelNames: {},
  providerModelTags: {},
  lastModelValue: {},
  clearKeys: {},
  customProviderId: "",
  customService: "",
  customModelPrevious: "",
  styleName: "",
  styleFilledFor: "",
  projectView: "overview",
  listMode: "flat",
  subOpen: false,
  overview: null,
  board: null,
  expanded: new Set(),
  collapsedCategories: new Set(),
  editingCard: null,
};

const PROJECT_TABS = ["overview", "import", "transcribe", "mt", "text", "subtitle", "export", "transcript", "styles", "qa"];
const GLOBAL_TABS = ["terms", "tm", "transcribe", "settings"];
let routingReady = false;


const ACTION_LABELS = {
  ingest: "转码", segment: "分段", align: "对齐", translate: "机器翻译",
  style: "导出 ASS", render: "渲染", "import-text": "导入文本",
  "import-ass": "导入 ASS",
};
const JOB_STATUS = {
  queued: "排队", running: "运行中", succeeded: "成功", failed: "失败",
  paused: "暂停", cancelled: "取消",
};
const JOB_STAGE = {
  intake: "入库", transcode: "转码", transcribe: "转写", align: "对齐",
  segment: "分段", translate: "翻译", style: "导出", render: "渲染",
};
const QA_KIND = {
  empty_target: "空译", source_equals_target: "源文等于译文",
  number_mismatch: "数字不一致", forbidden_term: "禁译词",
  term_missing: "术语缺失", missing_timing: "缺时间轴",
  duration_too_short: "时长过短", target_cps: "译文语速超限",
  source_cps: "原文语速超限", line_too_long: "行过长",
  too_many_lines: "行数过多", timing_overlap: "时间重叠",
};
const QA_SEVERITY = { error: "错误", warning: "警告", note: "提示" };
const SEGMENT_STATUS = {
  empty: "空", mt_suggested: "机器翻译建议", tm_suggested: "记忆库建议",
  translated: "已翻译", reviewed: "已审阅", approved: "已确认",
  rejected: "已退回",
};
const KIND_LABEL = { video: "视频", text: "文本" };
const LEVEL_LABEL = { project: "项目", subproject: "子项目" };
const AUDIT_LABEL = {
  status_change: "状态变更", tm_writeback: "写入记忆库",
  auto_propagate: "自动传播", mt_suggestion: "机器翻译建议",
  style_update: "样式更新", cue_edit: "行编辑",
};

function actionLabel(name) { return ACTION_LABELS[name] || name; }
function jobStatus(value) { return JOB_STATUS[value] || value; }
function jobStage(value) { return JOB_STAGE[value] || value || "—"; }
function qaKind(value) { return QA_KIND[value] || value; }
function qaSeverity(value) { return QA_SEVERITY[value] || value; }
function kindLabel(value) { return KIND_LABEL[value] || value; }
function levelLabel(value) { return LEVEL_LABEL[value] || value; }
function auditLabel(value) { return AUDIT_LABEL[value] || value; }
function qaMessage(item) {
  const kind = qaKind(item.kind);
  const scope = item.segment_id ? "（分段）" : item.cue_id ? "（字幕行）" : "";
  return `${kind}${scope}：${item.message || ""}`;
}

function setStatus(text, error = false) {
  const node = $("status");
  node.textContent = text;
  node.classList.toggle("is-ok", !error && !["空闲", "处理中"].includes(text));
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

/* --------------------------------------------------- ASS tag-aware editing
 *
 * Cue 文本是 ASS 原文，含 {\bord1.6} 这类覆盖标签。这些标签决定排版，
 * 所以编辑器把它们渲染成不可编辑的 chip：只有正文可改，标签跟着走。
 * 与 Aegisub 的 Translation Assistant 同一思路（解析标签并跳过）。
 */

const ASS_TAG = /\{[^}]*\}/g;
const ASS_BREAK = /(\\[Nnh])/;

function tagSpans(text) {
  const value = String(text || "");
  const items = [];
  let position = 0;
  for (const match of value.matchAll(ASS_TAG)) {
    if (match.index > position) pushTextSpans(items, value.slice(position, match.index));
    items.push({ kind: "tag", value: match[0] });
    position = match.index + match[0].length;
  }
  if (position < value.length) pushTextSpans(items, value.slice(position));
  return items;
}

function pushTextSpans(items, value) {
  for (const piece of value.split(ASS_BREAK)) {
    if (!piece) continue;
    const kind = ["\\N", "\\n", "\\h"].includes(piece) ? "break" : "text";
    items.push({ kind, value: piece });
  }
}

function visibleText(text) {
  return tagSpans(text)
    .filter((item) => item.kind !== "tag")
    .map((item) => (item.kind === "text" ? item.value
      : item.value === "\\h" ? " " : "\n"))
    .join("");
}

function tagSignature(text) {
  return (String(text || "").match(ASS_TAG) || []).join("\u0000");
}

function chipLabel(value) {
  const inner = value.slice(1, -1);
  return inner.length > 16 ? `{${inner.slice(0, 14)}…}` : value;
}

function renderTagEditor(node, text) {
  node.replaceChildren();
  for (const item of tagSpans(text)) {
    if (item.kind === "text") {
      node.append(document.createTextNode(item.value));
      continue;
    }
    const chip = document.createElement("span");
    chip.contentEditable = "false";
    chip.dataset.value = item.value;
    chip.title = item.value;
    if (item.kind === "tag") {
      chip.className = "tag-chip";
      chip.textContent = chipLabel(item.value);
    } else {
      chip.className = "tag-break";
      chip.textContent = item.value === "\\h" ? "␣" : "↵";
    }
    node.append(chip);
  }
}

function readTagEditor(node) {
  let value = "";
  const walk = (parent) => {
    for (const child of parent.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) {
        value += child.nodeValue.replace(/\u00a0/g, " ");
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        if (child.dataset && child.dataset.value) {
          value += child.dataset.value;
        } else if (child.tagName === "BR") {
          value += "\\N";
        } else {
          walk(child);
        }
      }
    }
  };
  walk(node);
  return value;
}

function insertBreak(node) {
  const chip = document.createElement("span");
  chip.contentEditable = "false";
  chip.className = "tag-break";
  chip.dataset.value = "\\N";
  chip.textContent = "↵";
  chip.title = "\\N (line break)";
  const selection = window.getSelection();
  if (!selection || !selection.rangeCount) return;
  const range = selection.getRangeAt(0);
  range.deleteContents();
  range.insertNode(chip);
  range.setStartAfter(chip);
  range.collapse(true);
  selection.removeAllRanges();
  selection.addRange(range);
  node.dispatchEvent(new Event("input", { bubbles: true }));
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

function parseRoute(hash) {
  const raw = String(hash || "").replace(/^#/, "");
  const [path, query = ""] = raw.split("?");
  const parts = path.split("/").filter(Boolean)
    .map((part) => {
      try { return decodeURIComponent(part); } catch { return part; }
    });
  const params = new URLSearchParams(query);
  if (!parts.length) return { kind: "home" };
  if (parts[0] === "project" && parts[1]) {
    if (parts[2] === "sub" && parts[3]) {
      const tab = PROJECT_TABS.includes(parts[4]) ? parts[4] : "overview";
      return { kind: "project", projectId: parts[1], subId: parts[3],
               tab, view: "overview" };
    }
    const view = ["overview", "board", "list"].includes(parts[2])
      ? parts[2] : "overview";
    return {
      kind: "project", projectId: parts[1], view, subId: "",
      mode: params.get("mode") === "tree" ? "tree" : "flat",
    };
  }
  if (GLOBAL_TABS.includes(parts[0])) {
    return { kind: "global", tab: parts[0] };
  }
  return { kind: "home" };
}

function buildRoute() {
  if (state.screen === "global" && GLOBAL_TABS.includes(state.activeTab)) {
    return `#/${state.activeTab}`;
  }
  if (state.screen === "project" && state.projectId) {
    if (state.subOpen && state.subId) {
      return `#/project/${encodeURIComponent(state.projectId)}/sub/`
        + `${encodeURIComponent(state.subId)}/${state.activeTab || "overview"}`;
    }
    const view = ["overview", "board", "list"].includes(state.projectView)
      ? state.projectView : "overview";
    const query = view === "list" && state.listMode === "tree"
      ? "?mode=tree" : "";
    return `#/project/${encodeURIComponent(state.projectId)}/${view}${query}`;
  }
  return "#/";
}

function syncRoute() {
  if (!routingReady) return;
  const route = buildRoute();
  if (window.location.hash !== route) {
    window.history.replaceState(null, "", route);
  }
}

async function applyRoute(route) {
  if (route.kind === "project") {
    if (route.mode) state.listMode = route.mode;
    await selectProject(route.projectId, {
      subId: route.subId, view: route.view, tab: route.tab,
    });
    return;
  }
  if (route.kind === "global") {
    switchTab(route.tab);
    return;
  }
  showHome();
}

async function boot() {
  const initialRoute = parseRoute(window.location.hash);
  initSidebar();
  window.addEventListener("hashchange", () => {
    if (!routingReady) return;
    applyRoute(parseRoute(window.location.hash))
      .catch((error) => toast(error.message, true));
  });
  bindTabs();
  bindHome();
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
  $("style-save").addEventListener("click", () => {
    saveStyle().catch((error) => toast(error.message, true));
  });
  $("tm-search").addEventListener("click", () => searchTm());
  $("tm-export").addEventListener("click", () => {
    window.open("/api/v1/tm/export.tmx", "_blank");
  });
  document.querySelectorAll("#project-view-switch [data-project-view]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        setProjectView(button.dataset.projectView);
      });
    });
  $("tm-query").addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); searchTm(); }
  });
  await loadWorkspace();
  routingReady = true;
  if (initialRoute.kind === "home") syncRoute();
  else await applyRoute(initialRoute);
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
    state.subOpen = false;
    renderProjectList();
    populateAssetFilters();
    renderAll();
    showHome();
    setStatus("就绪");
  } catch (error) {
    setStatus("工作区加载失败", true);
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

async function selectProject(id, options = {}) {
  state.projectId = id;
  if (!id) {
    state.project = null;
    state.subId = "";
    state.sub = null;
    state.subOpen = false;
    renderAll();
    showHome();
    syncRoute();
    return;
  }
  setStatus("载入中");
  try {
    const project = await api.project(id);
    state.project = project;
    state.projectId = project.project.id;
    localStorage.setItem("ncat.project", state.projectId);
    renderProjectList();
    if (options.view && ["overview", "board", "list"].includes(options.view)) {
      state.projectView = options.view;
    }
    if (options.subId) {
      const sub = (project.subprojects || []).find(
        (item) => item.id === options.subId);
      if (sub) {
        state.subId = sub.id;
        state.subOpen = true;
        state.activeTab = PROJECT_TABS.includes(options.tab)
          ? options.tab : "overview";
        await loadSub();
        renderProjectHeader();
        showSubprojectTab(state.activeTab);
        syncRoute();
        setStatus("就绪");
        return;
      }
    }
    state.subId = "";
    state.sub = null;
    state.subOpen = false;
    renderProjectHeader();
    setProjectView(state.projectView || "overview");
    syncRoute();
    setStatus("就绪");
  } catch (error) {
    state.projectId = "";
    state.project = null;
    state.subId = "";
    state.sub = null;
    state.subOpen = false;
    showHome();
    syncRoute();
    setStatus("加载失败", true);
    toast(error.message, true);
  }
}

function renderSubprojectSelect() {
  const select = $("subproject-select");
  if (!select) return;
  select.replaceChildren();
  const none = document.createElement("option");
  none.value = "";
  none.textContent = "—";
  select.append(none);
  for (const sub of (state.project && state.project.subprojects) || []) {
    const option = document.createElement("option");
    option.value = sub.id;
    option.textContent = `${sub.name} · ${kindLabel(sub.kind)}`;
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
  state.expanded = new Set();
  if (state.selectedId) state.expanded.add(cueStatus(state.cues[0]));
  state.dirty = false;
  renderAll();
  loadHistory().catch(() => {});
  loadStyles().catch(() => {});
  loadSuggestions().catch(() => {});
}

async function openSubproject(id) {
  const sub = (state.project?.subprojects || []).find((item) => item.id === id);
  if (!sub) return;
  state.subId = id;
  state.subOpen = true;
  state.activeTab = "overview";
  localStorage.setItem(`ncat.sub.${state.projectId}`, id);
  await loadSub();
  renderProjectHeader();
  showSubprojectTab("overview");
  syncRoute();
}

function setProjectView(view) {
  state.projectView = ["overview", "board", "list"].includes(view)
    ? view : "overview";
  state.subOpen = false;
  state.screen = "project";
  document.querySelectorAll("#project-view-switch .view-btn").forEach((button) => {
    button.classList.toggle("is-active",
      button.dataset.projectView === state.projectView);
  });
  if (state.project) {
    $("home-view").hidden = true;
    $("project-shell").hidden = false;
  }
  $("project-tabs").hidden = true;
  document.querySelectorAll("[data-project-panel]").forEach((panel) => {
    panel.hidden = true;
    panel.classList.remove("is-active");
  });
  $("project-overview").hidden = state.projectView !== "overview";
  $("project-board").hidden = state.projectView !== "board";
  $("project-list-view").hidden = state.projectView !== "list";
  if (state.projectView === "overview") {
    loadOverview().catch((error) => toast(error.message, true));
  } else if (state.projectView === "board") {
    loadBoard().catch(() => {});
  } else {
    renderSubprojectList();
    syncRoute();
  }
  syncRoute();
}

function tabEnabled(name) {
  const features = (state.project && state.project.features) || {};
  const required = {
    transcribe: "asr", mt: "mt", subtitle: "subtitle_edit",
    export: "export", styles: "subtitle_edit", qa: "export",
  }[name];
  return !required || features[required] !== false;
}

function syncProjectTabs() {
  document.querySelectorAll("#project-tabs [data-project-tab]")
    .forEach((button) => {
      const enabled = tabEnabled(button.dataset.projectTab);
      button.hidden = !enabled;
      button.disabled = !enabled;
    });
  const features = (state.project && state.project.features) || {};
  const actionFeatures = {
    ingest: "ffmpeg", align: "timing", segment: "segment",
    translate: "mt", style: "subtitle_edit", render: "export",
    "import-ass": "subtitle_edit",
  };
  document.querySelectorAll("[data-action]").forEach((button) => {
    const required = actionFeatures[button.dataset.action];
    button.hidden = Boolean(required && features[required] === false);
  });
}

function showSubprojectTab(name) {
  if (!tabEnabled(name)) name = "overview";
  state.subOpen = true;
  state.screen = "project";
  state.activeTab = name;
  $("home-view").hidden = true;
  $("project-shell").hidden = false;
  $("project-overview").hidden = true;
  $("project-board").hidden = true;
  $("project-list-view").hidden = true;
  $("project-tabs").hidden = false;
  document.querySelectorAll("[data-project-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.projectPanel !== name;
    panel.classList.toggle("is-active", panel.dataset.projectPanel === name);
  });
  document.querySelectorAll("#project-tabs [data-project-tab]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.projectTab === name);
  });
  syncProjectTabs();
  if (name === "overview") renderSubprojectDetail();
  if (name === "text") renderTextEdit();
  if (name === "transcript") renderTranscriptTab();
  if (name === "styles") {
    loadStyles().catch((error) => toast(error.message, true));
    loadAudit().catch(() => {});
  }
  if (name === "qa") loadQa().catch((error) => toast(error.message, true));
  requestAnimationFrame(() => {
    renderCues();
    renderEditor();
  });
  syncRoute();
}

function projectStatusText() {
  const subs = (state.project && state.project.subprojects) || [];
  const done = subs.filter((sub) => sub.done).length;
  return `任务总数 ${subs.length} · 完成 ${done} · 未完成 ${subs.length - done}`;
}

function renderProjectHeader() {
  const subtitle = $("header-subtitle");
  if (!state.project) {
    $("project-name").textContent = "项目";
    $("project-meta").textContent = "";
    if (subtitle) {
      subtitle.textContent = "Nick's Computer-Aided Translating Solution";
    }
    return;
  }
  $("project-name").textContent = state.project.project.name;
  $("project-meta").textContent = projectStatusText();
  if (subtitle) subtitle.textContent = state.project.project.name;
  syncProjectTabs();
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
  setStatus(state.project ? "ready" : "空闲");
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
      project.client || "", `${project.counts.subprojects} 个子项目`,
      `${project.counts.done || 0} 完成`,
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
    body.textContent = "请选择项目。";
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
    project.client || "未填客户",
    `${project.source_lang} → ${(project.target_langs || []).join(", ")}`,
    state.project.counts.subprojects + " 个子项目",
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
      sub.kind, sub.counts.cues + " 行",
      sub.state.source && sub.state.source.proxy_path ? "代理就绪" : "无代理",
      sub.state.asr && sub.state.asr.words ? `${sub.state.asr.words} words` : "",
    ].filter(Boolean).join(" · ");
    main.append(name, detail);
    const actions = document.createElement("div");
    actions.className = "subproject-actions";
    const open = document.createElement("button");
    open.className = "btn btn-quiet";
    open.type = "button";
    open.textContent = "打开";
    open.addEventListener("click", () => {
      openSubproject(sub.id).catch((error) => toast(error.message, true));
    });
    actions.append(open);
    row.append(main, actions);
    table.append(row);
  }
  body.append(table);
}

function renderContext() {
  $("context-note").textContent = state.project
    ? [state.project.project.client, state.project.project.name,
       currentSub() ? currentSub().name : ""].filter(Boolean).join(" / ")
    : "未选择项目";
}

function renderEditorContext() {
  const node = $("editor-context");
  node.replaceChildren();
  if (!state.project) { node.textContent = "未选择项目"; return; }
  const project = state.project.project;
  const sub = currentSub();
  const termbases = (state.project.termbases || [])
    .filter((tb) => tb.level === "project"
      || (sub && tb.subproject_id === sub.id));
  const tms = (state.project.tm || [])
    .filter((tm) => tm.level === "project"
      || (sub && tm.subproject_id === sub.id));
  const parts = [
    ["项目", project.name],
    ["客户", project.client || "—"],
    ["子项目", sub ? sub.name : "—"],
    ["术语库", termbases.map((tb) => tb.name).join(", ") || "—"],
    ["记忆库", tms.map((tm) => tm.name).join(", ") || "—"],
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
    summary.textContent = "请选择子项目。";
    $("asset-media-meta").textContent = "暂无媒体。";
    $("asset-video").removeAttribute("src");
    return;
  }
  const source = sub.state.source;
  summary.textContent = [
    `${sub.name} (${sub.kind})`,
    source ? source.path : "无源文件",
    source && source.proxy_path ? "代理就绪" : "无代理",
    source && source.audio_path ? "音频就绪" : "无音频",
    source && source.duration_ms
      ? `duration ${(source.duration_ms / 1000).toFixed(1)}s` : "",
  ].filter(Boolean).join(" · ");
  const video = $("asset-video");
  const proxy = sub.files && sub.files.proxy;
  if (proxy) video.src = proxy; else video.removeAttribute("src");
  $("asset-media-meta").textContent = proxy
    ? `Proxy: ${proxy}` : "请先转码入库生成媒体素材。";
}

const CUE_GROUPS = [
  { key: "empty", label: "空" },
  { key: "mt_suggested", label: "机器翻译建议" },
  { key: "tm_suggested", label: "记忆库建议" },
  { key: "translated", label: "已翻译" },
  { key: "reviewed", label: "已审阅" },
  { key: "approved", label: "已确认" },
  { key: "rejected", label: "已退回" },
  { key: "drawing", label: "绘图 / 非翻译" },
];

function cueStatus(cue) {
  if (cue.kind === "drawing") return "drawing";
  const segment = segmentForCue(cue);
  return segment ? String(segment.status) : "empty";
}

function renderCues() {
  renderStatusTree();
}

function renderStatusTree() {
  const root = $("status-tree");
  if (!root) return;
  const total = state.cues.length;
  $("cue-count").textContent = `${total} 行`;
  root.replaceChildren();
  if (!total) {
    const empty = document.createElement("div");
    empty.className = "cue-empty";
    empty.textContent = "暂无字幕行。请导入 ASS 或先分段。";
    root.append(empty);
    return;
  }
  const groups = new Map(CUE_GROUPS.map((group) => [group.key, []]));
  const indexById = new Map();
  state.cues.forEach((cue, index) => {
    indexById.set(cue.id, index + 1);
    const key = cueStatus(cue);
    (groups.get(key) || groups.get("empty")).push(cue);
  });
  for (const group of CUE_GROUPS) {
    const cues = groups.get(group.key) || [];
    if (!cues.length) continue;
    const expanded = state.expanded.has(group.key);
    const wrap = document.createElement("section");
    wrap.className = "status-group";
    const head = document.createElement("button");
    head.type = "button";
    head.className = "status-group-head";
    head.setAttribute("aria-expanded", String(expanded));
    const chevron = document.createElement("span");
    chevron.className = "chevron";
    chevron.textContent = expanded ? "▾" : "▸";
    const label = document.createElement("span");
    label.className = "status-label";
    label.textContent = group.label;
    const count = document.createElement("span");
    count.className = "status-count";
    count.textContent = String(cues.length);
    head.append(chevron, label, count);
    head.addEventListener("click", () => {
      if (state.expanded.has(group.key)) state.expanded.delete(group.key);
      else state.expanded.add(group.key);
      renderStatusTree();
    });
    wrap.append(head);
    if (expanded) {
      const children = document.createElement("div");
      children.className = "status-children";
      children.setAttribute("role", "group");
      for (const cue of cues) {
        const row = document.createElement("button");
        row.type = "button";
        row.className = "status-child";
        row.classList.toggle("is-active", cue.id === state.selectedId);
        row.classList.toggle("is-locked", Boolean(cue.locked));
        const meta = document.createElement("span");
        meta.className = "status-child-meta";
        meta.textContent = `${String(indexById.get(cue.id)).padStart(3, "0")}`
          + ` ${formatMs(cue.start_ms)}`;
        const text = document.createElement("span");
        text.className = "status-child-text";
        text.textContent = (visibleText(cue.target_text)
          || visibleText(cue.source_text) || "—").replace(/\n/g, " ⏎ ");
        row.append(meta, text);
        row.addEventListener("click", () => selectCue(cue.id));
        children.append(row);
      }
      wrap.append(children);
    }
    root.append(wrap);
  }
}

function selectCue(id) {
  if (state.dirty && state.selectedId && state.selectedId !== id) {
    saveCue().catch((error) => toast(error.message, true));
  }
  state.selectedId = id;
  const picked = currentCue();
  if (picked) state.expanded.add(cueStatus(picked));
  renderCues();
  renderEditor();
  loadSuggestions().catch(() => {});
  const cue = currentCue();
  const wrapper = state.activeTab === "editor" ? $("video") : $("asset-video");
  if (cue && cue.start_ms != null && wrapper && wrapper.readyState >= 1) {
    wrapper.currentTime = cue.start_ms / 1000;
  }
}

function renderEditor() {
  const cue = currentCue();
  const disabled = !cue;
  const locked = Boolean(cue && cue.locked);
  const drawing = Boolean(cue && cue.kind === "drawing");
  const targetNode = $("target-text");
  const sourceNode = $("source-text");
  $("edit-state").textContent = cue
    ? (drawing ? "绘图" : locked ? "locked" : "编辑中") : "未选择";
  targetNode.contentEditable = String(Boolean(cue) && !locked && !drawing);
  sourceNode.contentEditable = String(Boolean(cue) && !locked);
  for (const id of ["start-ms", "end-ms", "cue-style", "save-cue",
                    "split-cue", "merge-cue", "delete-cue"]) {
    $(id).disabled = disabled || locked;
  }
  $("cue-locked").disabled = disabled;
  $("artwork-note").hidden = !drawing;
  if (!cue) {
    renderTagEditor(targetNode, "");
    renderTagEditor(sourceNode, "");
    $("target-tags").textContent = "";
    $("source-tags").textContent = "";
    $("start-ms").value = "";
    $("end-ms").value = "";
    $("cue-style").value = "";
    $("cue-locked").checked = false;
    $("segment-status").value = "empty";
    $("segment-status").disabled = true;
    $("metrics").textContent = "";
    renderTagInspector(null);
    state.editorBaseline = null;
    state.editorLastGood = null;
    return;
  }
  if (document.activeElement !== targetNode) {
    renderTagEditor(targetNode, drawing ? "" : (cue.target_text || ""));
  }
  if (document.activeElement !== sourceNode) {
    renderTagEditor(sourceNode, cue.source_text || "");
  }
  targetNode.dataset.placeholder = drawing
    ? "矢量绘图行：不参与翻译与 QA" : "译文";
  $("target-tags").textContent = cue.tag_count ? `${cue.tag_count} 个标签` : "";
  $("source-tags").textContent = tagCount(cue.source_text);
  $("start-ms").value = cue.start_ms ?? "";
  $("end-ms").value = cue.end_ms ?? "";
  $("cue-style").value = cue.style || "Default";
  $("cue-locked").checked = Boolean(cue.locked);
  const segment = segmentForCue(cue);
  const statusSelect = $("segment-status");
  statusSelect.value = segment ? segment.status : "empty";
  statusSelect.disabled = disabled || locked || !segment || drawing;
  renderTagInspector(cue);
  if (drawing) {
    $("artwork-note").textContent =
      "绘图行（{\\p1} 矢量图形）：不参与翻译、MT 与 QA。"
      + "要改图形请用 Aegisub 之类的工具。";
  }
  state.editorBaseline = {
    target: tagSignature(cue.target_text),
    source: tagSignature(cue.source_text),
  };
  state.editorLastGood = {
    target: cue.target_text || "",
    source: cue.source_text || "",
  };
  renderStylePreview();
  const metrics = cue.metrics || {};
  if (drawing) {
    $("metrics").textContent = "drawing · 无可读文本，CPS 与行长不适用";
    return;
  }
  $("metrics").textContent = [
    `时长 ${metrics.duration_ms != null ? metrics.duration_ms + " ms" : "—"}`,
    `原文语速 ${metrics.cps_source ?? "—"} 字/秒`,
    `译文语速 ${metrics.cps_target ?? "—"} 字/秒`,
    `行数 ${metrics.lines ?? "—"}`,
    `最长行 ${metrics.line_length ?? "—"} 字`,
    (metrics.warnings || []).length
      ? `警告：${(metrics.warnings || []).map(qaKind).join("、")}` : "无警告",
  ].join("  ·  ");
}

function tagCount(text) {
  const count = (String(text || "").match(ASS_TAG) || []).length;
  return count ? `${count} tags` : "";
}

function renderTranscript() {
  const transcript = state.transcript;
  if (!transcript) {
    $("transcript-preview").textContent = "暂无转写稿。";
    return;
  }
  const text = (transcript.text || "").replace(/\s+/g, " ").trim();
  const tokens = (transcript.metadata
    && transcript.metadata.asr_token_confidence) || [];
  const scores = tokens.map((item) => item.confidence)
    .filter((value) => typeof value === "编号");
  const average = scores.length
    ? (scores.reduce((sum, value) => sum + value, 0) / scores.length) : null;
  $("transcript-preview").textContent =
    `${transcript.backend || "asr"} · ${transcript.language || ""} · `
    + `${(transcript.words || []).length} words · `
    + (average == null ? "无词级置信度"
      : `token confidence avg ${average.toFixed(3)} (${scores.length})`)
    + "\n" + (text.length > 500 ? text.slice(0, 500) + "…" : text);
}

function readEditor() {
  const parse = (value) => value === "" || value == null ? null : Number(value);
  const cue = currentCue();
  const drawing = Boolean(cue && cue.kind === "drawing");
  return {
    target_text: drawing ? (cue.target_text || "") : readTagEditor($("target-text")),
    source_text: drawing ? (cue.source_text || "") : readTagEditor($("source-text")),
    start_ms: parse($("start-ms").value),
    end_ms: parse($("end-ms").value),
    style: $("cue-style").value || "Default",
    locked: $("cue-locked").checked,
  };
}

function markDirty() {
  state.dirty = true;
  $("edit-state").textContent = "未保存";
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
    renderCues();
    renderEditor();
    $("edit-state").textContent = "已保存";
    setStatus("已保存");
  } finally {
    state.saving = false;
  }
}

async function refreshSub() {
  if (!state.project) return;
  const project = await api.project(state.projectId);
  state.project = project;
  await loadSub();
  if (state.subOpen) {
    renderProjectHeader();
    renderSubprojectDetail();
    if (state.activeTab === "text") renderTextEdit();
    if (state.activeTab === "transcript") renderTranscriptTab();
  }
}

async function runAction(action, body = {}) {
  if (!state.project || !state.subId) {
    toast("请先选择项目与子项目", true);
    return;
  }
  if (state.busy) return;
  state.busy = true;
  setStatus(`处理中：${actionLabel(action)}`);
  try {
    const payload = action === "import-ass"
      ? await api.importAss(state.projectId, state.subId, body)
      : await api.action(state.projectId, state.subId, action, body);
    await refreshSub();
    const detail = payload.ass || payload.render || payload.output;
    toast(detail ? `${actionLabel(action)}：${detail}`
      : `${actionLabel(action)}完成`);
    setStatus("就绪");
  } catch (error) {
    setStatus(`${actionLabel(action)}失败`, true);
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
    all.textContent = "全部项目";
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
                         levelLabel(item.termbase.level), entries.length]) {
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
                         levelLabel(item.tm.level), units.length]) {
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


function parseProjectQuery(text) {
  const filters = [];
  const free = [];
  const pattern = /(\w+)(>=|<=|=|:)(?:"([^"]*)"|(\S+))/g;
  let match;
  let rest = String(text || "").trim();
  while ((match = pattern.exec(rest))) {
    filters.push({
      key: match[1].toLowerCase(),
      op: match[2],
      value: match[3] !== undefined ? match[3] : match[4],
    });
  }
  const stripped = rest.replace(pattern, " ");
  for (const word of stripped.split(/\s+/)) {
    if (word) free.push(word.toLowerCase());
  }
  return { filters, free };
}

function subprojectMatches(sub, query, categoryName, laneName) {
  const haystack = [
    sub.name, sub.description || "",
    ...(sub.subtasks || []).map((item) => item.title),
    categoryName || "", laneName || "",
  ].join(" ").toLowerCase();
  for (const word of query.free) {
    if (!haystack.includes(word)) return false;
  }
  const today = new Date().toISOString().slice(0, 10);
  for (const filter of query.filters) {
    const value = filter.value;
    if (filter.key === "status") {
      const wanted = value.toLowerCase();
      if (wanted === "done" && !sub.done) return false;
      if (wanted === "open" && sub.done) return false;
    } else if (filter.key === "category") {
      if ((categoryName || "").toLowerCase() !== value.toLowerCase()) return false;
    } else if (filter.key === "due_within") {
      if (sub.done || !sub.due) return false;
      const days = parseInt(value, 10);
      if (Number.isNaN(days)) return false;
      const due = new Date(`${sub.due}T00:00:00`);
      const now = new Date(`${today}T00:00:00`);
      const limit = new Date(now.getTime() + days * 86400000);
      if (due < now || due > limit) return false;
    } else {
      const field = filter.key === "created" ? sub.created_at
        : filter.key === "completed" ? sub.completed_at
          : filter.key === "due" ? sub.due : null;
      if (field == null) return false;
      const fieldDate = String(field).slice(0, 10);
      if (filter.op.includes(">") && fieldDate < value) return false;
      if (filter.op.includes("<") && fieldDate > value) return false;
      if (filter.op === "=" && fieldDate !== value) return false;
    }
  }
  return true;
}

function currentProjectQuery() {
  return parseProjectQuery(($("project-search") || {}).value || "");
}

function categoryNameById(id) {
  const category = ((state.project && state.project.categories) || [])
    .find((item) => item.id === id);
  return category ? category.name : "";
}

function laneNameById(categoryId, laneId) {
  const category = ((state.project && state.project.categories) || [])
    .find((item) => item.id === categoryId);
  const lane = category && (category.lanes || []).find((item) => item.id === laneId);
  return lane ? lane.name : "";
}

function filteredSubprojects() {
  const query = currentProjectQuery();
  return ((state.project && state.project.subprojects) || []).filter(
    (sub) => subprojectMatches(
      sub, query, categoryNameById(sub.category_id),
      laneNameById(sub.category_id, sub.lane_id)));
}

async function loadOverview() {
  if (!state.projectId) return;
  state.overview = await api.overview(state.projectId);
  renderOverview();
}

function renderOverview() {
  const data = state.overview;
  if (!data) return;
  const summary = $("overview-summary");
  summary.replaceChildren();
  const title = document.createElement("h2");
  title.textContent = data.project.name;
  const totals = document.createElement("div");
  totals.className = "project-meta";
  totals.textContent = `任务 ${data.totals.subprojects} · 完成 ${data.totals.done}`
    + ` · 未完成 ${data.totals.open} · 逾期 ${data.totals.overdue}`;
  summary.append(title, totals);
  const categories = $("overview-categories");
  categories.replaceChildren();
  for (const category of data.categories) {
    const card = document.createElement("section");
    card.className = "panel";
    const head = document.createElement("div");
    head.className = "panel-head";
    const name = document.createElement("span");
    name.textContent = category.name;
    const count = document.createElement("span");
    count.className = "panel-count";
    count.textContent = `${category.done}/${category.total}`;
    head.append(name, count);
    card.append(head);
    for (const lane of category.lanes) {
      const row = document.createElement("div");
      row.className = "stat-row";
      row.textContent = `${lane.name} · ${lane.done}/${lane.total}`
        + (lane.overdue ? ` · 逾期 ${lane.overdue}` : "");
      card.append(row);
    }
    categories.append(card);
  }
  const activity = $("activity-list");
  activity.replaceChildren();
  if (!(data.activity || []).length) {
    activity.textContent = "暂无动态。";
  }
  for (const event of data.activity || []) {
    const row = document.createElement("div");
    row.className = "activity-row";
    const time = document.createElement("span");
    time.className = "activity-time";
    time.textContent = event.at ? new Date(event.at).toLocaleString() : "—";
    const text = document.createElement("span");
    text.textContent = activityLabel(event);
    row.append(time, text);
    activity.append(row);
  }
}

function activityLabel(event) {
  const labels = {
    subproject_added: "添加子项目", subproject_deleted: "删除子项目",
    subproject_moved: "移动泳道", subtask_added: "添加子任务",
    subtask_deleted: "删除子任务", subtask_completed: "完成子任务",
    subtask_uncompleted: "取消完成子任务", category_added: "添加分类",
    category_deleted: "删除分类", lane_added: "添加泳道",
    lane_deleted: "删除泳道",
  };
  const base = labels[event.type] || event.type;
  const name = event.name || event.title || "";
  return name ? `${base}：${name}` : base;
}

async function loadBoard() {
  if (!state.projectId) {
    state.board = null;
    renderBoard();
    return;
  }
  try {
    state.board = await api.projectBoard(state.projectId);
  } catch (error) {
    state.board = null;
    toast(error.message, true);
  }
  renderBoard();
}

function boardCardView(card) {
  const element = document.createElement("article");
  element.className = "board-card";
  element.draggable = true;
  const head = document.createElement("div");
  head.className = "board-card-head";
  const name = document.createElement("strong");
  name.textContent = card.name;
  head.append(name);
  if (card.done) {
    const badge = document.createElement("span");
    badge.className = "board-badge";
    badge.textContent = "完成";
    head.append(badge);
  }
  element.append(head);
  const meta = document.createElement("div");
  meta.className = "board-card-meta";
  meta.textContent = `${card.subtasks ? card.subtasks.done : 0}/`
    + `${card.subtasks ? card.subtasks.total : 0} 子任务`;
  element.append(meta);
  if (card.due) {
    const due = document.createElement("div");
    due.className = "board-card-foot";
    due.textContent = `截止 ${card.due}`;
    element.append(due);
  }
  element.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("text/plain", JSON.stringify({
      id: card.subproject_id, category_id: card.category_id,
      lane_id: card.lane_id,
    }));
    event.dataTransfer.effectAllowed = "move";
  });
  element.addEventListener("click", () => {
    openSubproject(card.subproject_id).catch(
      (error) => toast(error.message, true));
  });
  return element;
}

function renderBoard() {
  const root = $("board");
  if (!root) return;
  root.replaceChildren();
  const data = state.board;
  if (!data) {
    root.textContent = "正在加载看板…";
    return;
  }
  const query = currentProjectQuery();
  const allowed = new Set(((state.project && state.project.subprojects) || [])
    .filter((sub) => subprojectMatches(
      sub, query, categoryNameById(sub.category_id),
      laneNameById(sub.category_id, sub.lane_id)))
    .map((sub) => sub.id));
  for (const category of data.categories || []) {
    const section = document.createElement("section");
    section.className = "board-category";
    const head = document.createElement("button");
    head.type = "button";
    head.className = "board-category-head";
    const collapsed = state.collapsedCategories.has(category.id);
    head.textContent = `${collapsed ? "▸" : "▾"} ${category.name}`;
    head.addEventListener("click", () => {
      if (collapsed) state.collapsedCategories.delete(category.id);
      else state.collapsedCategories.add(category.id);
      renderBoard();
    });
    section.append(head);
    if (!collapsed) {
      const grid = document.createElement("div");
      grid.className = "board-grid";
      grid.style.gridTemplateColumns = `repeat(${category.lanes.length}, 17rem)`;
      for (const lane of category.lanes) {
        const column = document.createElement("div");
        column.className = "board-lane";
        const laneHead = document.createElement("div");
        laneHead.className = "board-head-cell";
        const count = lane.cards.filter((card) => allowed.has(card.subproject_id)).length;
        laneHead.textContent = `${lane.name} · ${count}`;
        column.append(laneHead);
        const cell = document.createElement("div");
        cell.className = "board-cell";
        for (const card of lane.cards.filter(
          (item) => allowed.has(item.subproject_id))) {
          cell.append(boardCardView(card));
        }
        if (!cell.children.length) {
          const empty = document.createElement("div");
          empty.className = "board-column-empty";
          empty.textContent = "—";
          cell.append(empty);
        }
        cell.addEventListener("dragover", (event) => {
          event.preventDefault();
          cell.classList.add("is-drop");
        });
        cell.addEventListener("dragleave", () => cell.classList.remove("is-drop"));
        cell.addEventListener("drop", (event) => {
          event.preventDefault();
          cell.classList.remove("is-drop");
          let payload = null;
          try {
            payload = JSON.parse(event.dataTransfer.getData("text/plain"));
          } catch {}
          if (!payload || payload.category_id !== category.id) {
            toast("不能跨分类移动，请在子项目详情修改分类", true);
            return;
          }
          moveCard(payload.id, category.id, lane.id).catch(
            (error) => toast(error.message, true));
        });
        column.append(cell);
        grid.append(column);
      }
      section.append(grid);
    }
    root.append(section);
  }
  if (!(data.categories || []).length) {
    const empty = document.createElement("div");
    empty.className = "board-empty";
    empty.textContent = "还没有分类/泳道，请先在项目设置 → 编辑看板中创建。";
    root.append(empty);
  }
}

async function moveCard(subprojectId, categoryId, laneId) {
  if (!state.projectId) return;
  try {
    await api.patchWorkflow(state.projectId, subprojectId, {
      category_id: categoryId, lane_id: laneId,
    });
    await loadBoard();
    if (state.projectView === "overview") await loadOverview();
  } catch (error) {
    toast(error.message, true);
    await loadBoard();
  }
}

function renderSubprojectList() {
  const root = $("subproject-list-view");
  if (!root) return;
  const toggle = $("list-mode-toggle");
  if (toggle) {
    toggle.textContent = state.listMode === "flat" ? "层级" : "平铺";
  }
  root.replaceChildren();
  const subs = filteredSubprojects();
  if (!subs.length) {
    root.textContent = "没有匹配的子项目。";
    return;
  }
  if (state.listMode === "tree") {
    for (const category of state.project.categories || []) {
      const categorySubs = subs.filter((sub) => sub.category_id === category.id);
      if (!categorySubs.length) continue;
      const section = document.createElement("section");
      section.className = "list-tree-category";
      const head = document.createElement("div");
      head.className = "list-tree-head";
      head.textContent = category.name;
      section.append(head);
      for (const lane of category.lanes || []) {
        const laneSubs = categorySubs.filter((sub) => sub.lane_id === lane.id);
        if (!laneSubs.length) continue;
        const laneHead = document.createElement("div");
        laneHead.className = "list-tree-lane";
        laneHead.textContent = lane.name;
        section.append(laneHead);
        for (const sub of laneSubs) section.append(subprojectRow(sub));
      }
      root.append(section);
    }
    return;
  }
  for (const sub of subs) root.append(subprojectRow(sub));
}

function subprojectRow(sub) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "subproject-row";
  const main = document.createElement("div");
  const name = document.createElement("strong");
  name.textContent = sub.name;
  const meta = document.createElement("div");
  meta.className = "project-meta";
  meta.textContent = [
    categoryNameById(sub.category_id),
    laneNameById(sub.category_id, sub.lane_id),
    sub.done ? "完成" : "未完成",
    sub.due ? `截止 ${sub.due}` : "",
  ].filter(Boolean).join(" · ");
  main.append(name, meta);
  row.append(main);
  row.addEventListener("click", () => {
    openSubproject(sub.id).catch((error) => toast(error.message, true));
  });
  return row;
}

function renderSubprojectDetail() {
  const sub = state.sub;
  if (!sub) return;
  $("detail-name").value = sub.name || "";
  $("detail-description").value = sub.description || "";
  $("detail-due").value = sub.due || "";
  $("detail-done").checked = Boolean(sub.done);
  const categorySelect = $("detail-category");
  categorySelect.replaceChildren();
  for (const category of state.project.categories || []) {
    const option = document.createElement("option");
    option.value = category.id;
    option.textContent = category.name;
    categorySelect.append(option);
  }
  categorySelect.value = sub.category_id || "";
  renderDetailLanes(sub.category_id, sub.lane_id);
  const list = $("detail-subtasks");
  list.replaceChildren();
  $("detail-subtask-count").textContent = String((sub.subtasks || []).length);
  for (const task of sub.subtasks || []) {
    const row = document.createElement("div");
    row.className = "subtask-row";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = Boolean(task.done);
    check.addEventListener("change", () => {
      api.patchSubtask(state.projectId, state.subId, task.id,
        { done: check.checked })
        .then(() => refreshSub())
        .catch((error) => toast(error.message, true));
    });
    const title = document.createElement("input");
    title.value = task.title;
    title.className = "subtask-title";
    title.addEventListener("change", () => {
      api.patchSubtask(state.projectId, state.subId, task.id,
        { title: title.value })
        .then(() => refreshSub())
        .catch((error) => toast(error.message, true));
    });
    const remove = document.createElement("button");
    remove.className = "btn btn-quiet";
    remove.type = "button";
    remove.textContent = "删除";
    remove.addEventListener("click", () => {
      api.deleteSubtask(state.projectId, state.subId, task.id)
        .then(() => refreshSub())
        .catch((error) => toast(error.message, true));
    });
    row.append(check, title, remove);
    list.append(row);
  }
  renderDetailLinks(sub);
  renderDetailComments(sub);
  renderDetailAttachments(sub);
}

function renderDetailLanes(categoryId, selected) {
  const select = $("detail-lane");
  select.replaceChildren();
  const category = (state.project.categories || [])
    .find((item) => item.id === categoryId);
  for (const lane of (category && category.lanes) || []) {
    const option = document.createElement("option");
    option.value = lane.id;
    option.textContent = lane.name;
    select.append(option);
  }
  select.value = selected || "";
}

function renderDetailLinks(sub) {
  const internal = $("detail-internal-links");
  internal.replaceChildren();
  for (const link of sub.internal_links || []) {
    const target = (state.project.subprojects || [])
      .find((item) => item.id === link.target_subproject_id);
    const row = document.createElement("div");
    row.className = "link-row";
    const label = document.createElement("span");
    label.textContent = `${link.label || target?.name || link.target_subproject_id}`;
    const remove = document.createElement("button");
    remove.className = "btn btn-quiet";
    remove.textContent = "删除";
    remove.addEventListener("click", () => {
      api.deleteInternalLink(state.projectId, state.subId, link.id)
        .then(() => refreshSub()).catch((error) => toast(error.message, true));
    });
    row.append(label, remove);
    internal.append(row);
  }
  const targetSelect = $("new-internal-target");
  targetSelect.replaceChildren();
  for (const item of state.project.subprojects || []) {
    if (item.id === state.subId) continue;
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = item.name;
    targetSelect.append(option);
  }
  const external = $("detail-external-links");
  external.replaceChildren();
  for (const link of sub.external_links || []) {
    const row = document.createElement("div");
    row.className = "link-row";
    const anchor = document.createElement("a");
    anchor.href = link.url;
    anchor.target = "_blank";
    anchor.rel = "noreferrer";
    anchor.textContent = link.label || link.url;
    const remove = document.createElement("button");
    remove.className = "btn btn-quiet";
    remove.textContent = "删除";
    remove.addEventListener("click", () => {
      api.deleteExternalLink(state.projectId, state.subId, link.id)
        .then(() => refreshSub()).catch((error) => toast(error.message, true));
    });
    row.append(anchor, remove);
    external.append(row);
  }
}

function renderDetailAttachments(sub) {
  const root = $("detail-attachments");
  root.replaceChildren();
  for (const item of sub.attachments || []) {
    const row = document.createElement("div");
    row.className = "link-row";
    const link = document.createElement("a");
    link.href = api.attachmentUrl(state.projectId, state.subId, item.id);
    link.textContent = `${item.name} (${Math.round(item.size / 1024)} KB)`;
    const remove = document.createElement("button");
    remove.className = "btn btn-quiet";
    remove.textContent = "删除";
    remove.addEventListener("click", () => {
      api.deleteAttachment(state.projectId, state.subId, item.id)
        .then(() => refreshSub()).catch((error) => toast(error.message, true));
    });
    row.append(link, remove);
    root.append(row);
  }
}

function renderDetailComments(sub) {
  const root = $("detail-comments");
  root.replaceChildren();
  for (const comment of sub.comments || []) {
    const row = document.createElement("div");
    row.className = "comment-row";
    const body = document.createElement("div");
    body.textContent = comment.body;
    const time = document.createElement("span");
    time.className = "project-meta";
    time.textContent = comment.created_at
      ? new Date(comment.created_at).toLocaleString() : "";
    const remove = document.createElement("button");
    remove.className = "btn btn-quiet";
    remove.textContent = "删除";
    remove.addEventListener("click", () => {
      api.deleteComment(state.projectId, state.subId, comment.id)
        .then(() => refreshSub()).catch((error) => toast(error.message, true));
    });
    row.append(body, time, remove);
    root.append(row);
  }
}

function renderTextEdit() {
  const body = $("text-segment-table").querySelector("tbody");
  if (!body) return;
  body.replaceChildren();
  for (const segment of state.segments || []) {
    const row = document.createElement("tr");
    const index = document.createElement("td");
    index.textContent = String((state.segments.indexOf(segment) + 1));
    const source = document.createElement("td");
    source.textContent = segment.source;
    const targetCell = document.createElement("td");
    const target = document.createElement("input");
    target.value = segment.target || "";
    target.addEventListener("change", () => {
      api.patchSegment(state.projectId, state.subId, segment.id,
        { target: target.value })
        .then(() => refreshSub())
        .catch((error) => toast(error.message, true));
    });
    targetCell.append(target);
    const status = document.createElement("td");
    status.textContent = String(segment.status || "");
    row.append(index, source, targetCell, status);
    body.append(row);
  }
}


async function loadHealth() {
  try {
    state.health = await api.healthStatus();
    renderHealth();
  } catch (error) {
    $("health-dot").className = "health-dot is-down";
    $("health-updated").textContent = "连接失败";
    $("health-services").textContent = "无法连接管理服务（8765）";
  }
}

function hwText(node, value, className = "hw-line") {
  const line = document.createElement("div");
  line.className = className;
  line.textContent = value;
  node.append(line);
}

function hwBar(pct, tone = "") {
  const wrap = document.createElement("div");
  wrap.className = "hw-bar";
  const fill = document.createElement("i");
  if (tone) fill.className = tone;
  fill.style.width = `${Math.max(0, Math.min(100, Number(pct) || 0))}%`;
  wrap.append(fill);
  return wrap;
}

function gib(mb) {
  return (Number(mb || 0) / 1024).toFixed(1);
}

function gpuBarTone(pct) {
  if (pct >= 95) return "is-danger";
  if (pct >= 85) return "is-warn";
  return "";
}

function renderServiceCard(service) {
  const card = document.createElement("div");
  card.className = "hw-card health-service-card";
  const head = document.createElement("div");
  head.className = "health-service-head";
  const dot = document.createElement("span");
  dot.className = "service-dot " + (
    service.state === "ok" ? "is-ok"
      : service.state === "unreachable" ? "is-down" : "");
  const name = document.createElement("strong");
  name.textContent = service.name;
  head.append(dot, name);
  if (service.url) {
    const url = document.createElement("span");
    url.className = "service-url";
    url.textContent = service.url.replace(/^https?:\/\//, "");
    head.append(url);
  }
  card.append(head);
  if (service.state === "ok" && service.detail) {
    hwText(card, service.detail, "hw-line health-note");
  }
  const hardware = service.hardware || {};
  const cpu = hardware.cpu || {};
  if (service.state !== "ok" || !Object.keys(cpu).length) {
    const detail = service.state === "unreachable"
      ? `不可达${service.detail ? "：" + service.detail : ""}`
      : service.state === "not_configured" ? "未配置"
        : service.state === "ignored" ? "已忽略运行状态检查" : "无硬件数据";
    if (!(service.state === "ok" && service.detail)) hwText(card, detail);
    appendHealthIgnore(card, service);
    return card;
  }
  if (cpu.usage_pct != null) card.append(hwBar(cpu.usage_pct));
  hwText(card, `CPU ${cpu.usage_pct != null ? cpu.usage_pct + "%" : "—"}`
    + ` · ${cpu.physical || "?"}c/${cpu.logical || "?"}t`
    + ` · ${cpu.freq_mhz ? (cpu.freq_mhz / 1000).toFixed(2) + " GHz" : "—"}`);
  const gpu = hardware.gpu || {};
  if (gpu.name) {
    const memoryPct = gpu.memory_total_mb
      ? 100 * gpu.memory_used_mb / gpu.memory_total_mb : 0;
    card.append(hwBar(memoryPct, gpuBarTone(memoryPct)));
    const parts = [];
    if (gpu.memory_total_mb) {
      parts.push(`显存 ${gib(gpu.memory_used_mb)}/${gib(gpu.memory_total_mb)} GiB`);
    }
    if (gpu.utilization != null) parts.push(`GPU ${gpu.utilization}%`);
    if (gpu.temperature_c != null) parts.push(`${gpu.temperature_c}°C`);
    if (gpu.power_w != null) parts.push(`${Math.round(gpu.power_w)} W`);
    hwText(card, parts.join(" · "));
    for (const proc of gpu.processes || []) {
      hwText(card, `pid ${proc.pid} ${proc.name} · ${proc.memory_mb} MiB`,
        "hw-line hw-sub");
    }
  } else {
    hwText(card, "GPU：不可用", "hw-line hw-sub");
  }
  const memory = hardware.memory || {};
  if (memory.total_mb) {
    const usedPct = 100 * memory.used_mb / memory.total_mb;
    card.append(hwBar(usedPct, usedPct >= 95 ? "is-danger"
      : usedPct >= 85 ? "is-warn" : ""));
    hwText(card, `内存 ${gib(memory.used_mb)}/${gib(memory.total_mb)} GiB`
      + ` · 可用 ${gib(memory.available_mb)} GiB`);
  }
  const models = service.models || {};
  if ((models.loaded || []).length) {
    hwText(card, `已加载：${models.loaded.join(", ")}`,
      "hw-line hw-sub service-model is-loaded");
  } else if (models.count) {
    hwText(card, `可用模型 ${models.count} 个（未加载）`, "hw-line hw-sub");
  }
  appendHealthIgnore(card, service);
  return card;
}

function appendHealthIgnore(card, service) {
  if (!service.provider_id) return;
  if (!(service.health_supported === false
        || service.state === "ignored"
        || service.state === "unreachable")) return;
  const button = document.createElement("button");
  button.className = "btn btn-quiet health-ignore";
  button.type = "button";
  button.textContent = service.state === "ignored" ? "恢复检查" : "忽略检查";
  button.addEventListener("click", () => {
    toggleHealthIgnore(service, service.state !== "ignored");
  });
  card.append(button);
}

async function toggleHealthIgnore(service, ignore) {
  try {
    const settings = await api.settings();
    const providers = (settings.providers || []).map((provider) =>
      provider.id === service.provider_id
        ? { ...provider, ignore_health: ignore } : provider);
    await api.saveSettings({
      providers,
      asr: { active_provider_id: settings.asr?.active_provider_id || "" },
      mt: { active_provider_id: settings.mt?.active_provider_id || "" },
    });
    await loadSettings();
    await loadHealth();
  } catch (error) {
    toast(error.message, true);
  }
}

function renderHealth() {
  const health = state.health || {};
  const root = $("health-services");
  root.replaceChildren();
  const services = health.services || [];
  for (const service of services) root.append(renderServiceCard(service));
  $("health-updated").textContent = health.collected_at
    ? `更新于 ${new Date(health.collected_at).toLocaleTimeString()}` : "—";
  const configured = services.filter(
    (service) => service.state !== "not_configured"
      && service.state !== "ignored");
  const down = configured.some(
    (service) => service.state === "unreachable");
  const hot = configured.some((service) => {
    const gpu = (service.hardware || {}).gpu || {};
    return gpu.temperature_c >= 85
      || (gpu.memory_total_mb && gpu.memory_used_mb / gpu.memory_total_mb >= 0.95);
  });
  const tone = !configured.length ? "" : down ? "is-down" : hot ? "is-warn" : "is-ok";
  $("health-dot").className = "health-dot" + (tone ? ` ${tone}` : "");
}

function segmentForCue(cue) {
  if (!cue || !cue.segment_id) return null;
  return state.segments.find((item) => item.id === cue.segment_id) || null;
}

async function applySegmentStatus(value) {
  const segment = segmentForCue(currentCue());
  if (!segment) {
    toast("本行没有对应分段", true);
    return;
  }
  try {
    await api.patchSegment(state.projectId, state.subId, segment.id,
      { status: value });
    await refreshSub();
    toast(`状态：${SEGMENT_STATUS[value] || value}`);
  } catch (error) {
    toast(error.message, true);
  }
}


async function loadAudit() {
  if (!state.project || !state.subId) return;
  const root = $("audit-list");
  if (!root) return;
  try {
    const rows = await api.audit(state.projectId, state.subId, 20);
    renderAudit(rows);
  } catch {
    root.textContent = "暂无操作记录。";
  }
}

function renderAudit(rows) {
  const root = $("audit-list");
  root.replaceChildren();
  if (!rows || !rows.length) {
    root.textContent = "暂无操作记录。";
    return;
  }
  for (const row of rows.slice().reverse()) {
    const line = document.createElement("div");
    line.className = "tag-row";
    const time = document.createElement("code");
    const stamp = row.at ? new Date(row.at) : null;
    time.textContent = stamp ? stamp.toLocaleTimeString() : "—";
    const label = document.createElement("span");
    label.className = "tag-kind";
    const detail = row.style || row.status || row.source || row.cue_id || "";
    label.textContent = `${auditLabel(row.kind)}${detail ? " · " + detail : ""}`;
    line.append(time, label);
    root.append(line);
  }
}

function renderTagInspector(cue) {
  const root = $("tag-list");
  if (!root) return;
  root.replaceChildren();
  if (!cue) {
    root.textContent = "未选择字幕行。";
    return;
  }
  const tags = tagSpans(cue.target_text).filter((item) => item.kind !== "text");
  if (!tags.length) {
    root.textContent = "本行没有覆盖标签。";
    return;
  }
  for (const item of tags) {
    const row = document.createElement("div");
    row.className = "tag-row";
    const code = document.createElement("code");
    code.textContent = item.value;
    const kind = document.createElement("span");
    kind.className = "tag-kind";
    kind.textContent = item.kind === "break" ? "换行" : "覆盖标签";
    row.append(code, kind);
    root.append(row);
  }
}

/* ------------------------------------------------------- 样式表 */

function renderStyles() {
  const payload = state.stylePayload;
  const body = $("style-table").querySelector("tbody");
  if (!body) return;
  body.replaceChildren();
  const styles = (payload && payload.styles) || [];
  $("style-count").textContent = String(styles.length);
  for (const style of styles) {
    const row = document.createElement("tr");
    row.dataset.name = style.name;
    const values = [style.name, style.Fontname || "", style.Fontsize || "",
                    style.primary_hex || "", style.Outline || "",
                    style.Alignment || ""];
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = String(value);
      row.append(cell);
    }
    row.classList.toggle("is-active", style.name === state.styleName);
    row.addEventListener("click", () => selectStyle(style.name));
    body.append(row);
  }
  $("script-info").textContent =
    ((payload && payload.script_info) || []).join("\n") || "—";
  const path = payload && payload.path;
  $("style-message").textContent = path
    ? `模板：${path}` : "尚未生成模板，保存样式时会创建 assets/template.ass。";
  const selected = state.styleName || (styles[0] && styles[0].name) || "";
  if (selected) selectStyle(selected, state.styleFilledFor !== selected);
}

function selectStyle(name, fill = true) {
  state.styleName = name;
  $("style-edit-name").textContent = name;
  if (fill) state.styleFilledFor = name;
  const style = ((state.stylePayload || {}).styles || [])
    .find((item) => item.name === name);
  if (style && fill) {
    $("style-font").value = style.Fontname || "";
    $("style-size").value = style.Fontsize || "";
    $("style-primary").value = style.primary_hex || "#ffffff";
    $("style-outline-color").value = style.outline_hex || "#000000";
    $("style-outline").value = style.Outline || 0;
    $("style-shadow").value = style.Shadow || 0;
    $("style-alignment").value = style.Alignment || "2";
    $("style-margin-l").value = style.MarginL || 0;
    $("style-margin-r").value = style.MarginR || 0;
    $("style-margin-v").value = style.MarginV || 0;
  }
  document.querySelectorAll("#style-table tbody tr").forEach((row) => {
    row.classList.toggle("is-active", row.dataset.name === name);
  });
}

async function saveStyle() {
  if (!state.project || !state.subId || !state.styleName) return;
  const values = {
    font: $("style-font").value.trim(),
    size: $("style-size").value,
    primary: $("style-primary").value,
    outline_color: $("style-outline-color").value,
    outline: $("style-outline").value,
    shadow: $("style-shadow").value,
    alignment: $("style-alignment").value,
    margin_l: $("style-margin-l").value,
    margin_r: $("style-margin-r").value,
    margin_v: $("style-margin-v").value,
  };
  try {
    state.stylePayload = await api.patchStyle(
      state.projectId, state.subId, { name: state.styleName, values });
    state.styles = state.stylePayload.styles || [];
    renderStyles();
    renderStylePreview();
    $("style-message").textContent = "样式已写入 template.ass";
    toast("样式已保存");
  } catch (error) {
    $("style-message").textContent = error.message;
    toast(error.message, true);
  }
}

/* ------------------------------------------------------- 记忆库检索 */

function searchTm() {
  const query = ($("tm-query").value || "").trim().toLowerCase();
  const body = $("tm-result-table").querySelector("tbody");
  body.replaceChildren();
  let count = 0;
  for (const group of state.assetTm || []) {
    for (const unit of group.units || []) {
      const haystack = `${unit.source} ${unit.target}`.toLowerCase();
      if (query && !haystack.includes(query)) continue;
      count += 1;
      if (count > 200) continue;
      const row = document.createElement("tr");
      for (const value of [`${Math.round((unit.usage_count || 0))}`,
                           unit.source, unit.target, group.project_name]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      body.append(row);
    }
  }
  $("tm-result-wrap").hidden = false;
  $("tm-message").textContent = `匹配 ${count} 条`;
}



/* ------------------------------------------------------- 建议面板 */

function insertTextAtCaret(node, text) {
  node.focus();
  const selection = window.getSelection();
  if (!selection.rangeCount || !node.contains(selection.anchorNode)) {
    focusEnd(node);
  }
  document.execCommand("insertText", false, text);
}

function applySuggestion(text, replace) {
  const node = $("target-text");
  if (!text || node.contentEditable !== "true") return;
  if (replace || !readTagEditor(node).trim()) {
    renderTagEditor(node, text);
    state.editorLastGood.target = text;
  } else {
    insertTextAtCaret(node, text);
  }
  markDirty();
}

async function loadSuggestions() {
  const cue = currentCue();
  if (!cue || !state.project || !state.subId) {
    state.suggest = null;
    renderSuggestions();
    return;
  }
  const query = (visibleText(cue.source_text)
    || visibleText(cue.target_text)).trim();
  if (!query) {
    state.suggest = { query: "", tm: [], terms: [], mt: null };
    renderSuggestions();
    return;
  }
  try {
    const payload = await api.suggest(state.projectId, state.subId, query);
    const previous = state.suggest && state.suggest.query === query
      ? state.suggest.mt : null;
    state.suggest = { ...payload, mt: previous };
  } catch (error) {
    state.suggest = null;
  }
  renderSuggestions();
}

function renderSuggestions() {
  const root = $("suggest-list");
  if (!root) return;
  root.replaceChildren();
  const data = state.suggest;
  if (!data) {
    $("suggest-state").textContent = "—";
    return;
  }
  const tm = data.tm || [];
  const terms = data.terms || [];
  $("suggest-state").textContent =
    `记忆库 ${tm.length} · 术语 ${terms.length}` + (data.mt ? " · MT" : "");
  for (const hit of tm.slice(0, 5)) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "suggest-row";
    const score = document.createElement("span");
    score.className = "suggest-score";
    score.textContent = `${Math.round(Number(hit.score) * 100)}%`;
    const body = document.createElement("span");
    body.className = "suggest-body";
    const source = document.createElement("span");
    source.className = "suggest-source";
    source.textContent = hit.source;
    const target = document.createElement("span");
    target.className = "suggest-target";
    target.textContent = hit.target;
    body.append(source, target);
    row.append(score, body);
    row.title = "点击插入，Shift+点击替换";
    row.addEventListener("click", (event) => {
      event.preventDefault();
      applySuggestion(hit.target, event.shiftKey);
    });
    root.append(row);
  }
  for (const term of terms) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "term-chip"
      + (term.forbidden ? " is-forbidden" : "")
      + (term.status === "deprecated" ? " is-deprecated" : "");
    chip.textContent = `${term.source} → ${term.target}`;
    chip.title = term.forbidden ? "禁译词"
      : `状态：${term.status || "preferred"}`;
    chip.addEventListener("click", () => applySuggestion(term.target, false));
    root.append(chip);
  }
  if (data.mt && data.mt.target) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "suggest-row is-mt";
    const score = document.createElement("span");
    score.className = "suggest-score";
    score.textContent = "MT";
    const body = document.createElement("span");
    body.className = "suggest-body";
    const target = document.createElement("span");
    target.className = "suggest-target";
    target.textContent = data.mt.target;
    body.append(target);
    row.append(score, body);
    row.title = `模型 ${data.mt.model || "本地"}；点击插入`;
    row.addEventListener("click", (event) => {
      event.preventDefault();
      applySuggestion(data.mt.target, event.shiftKey);
    });
    root.append(row);
  }
  if (!tm.length && !terms.length && !(data.mt && data.mt.target)) {
    const empty = document.createElement("div");
    empty.className = "suggest-empty";
    empty.textContent = "没有匹配的记忆库或术语。";
    root.append(empty);
  }
}

async function requestMtSuggestion() {
  const cue = currentCue();
  if (!cue || !state.project || !state.subId) {
    toast("请先选择字幕行", true);
    return;
  }
  $("suggest-state").textContent = "请求 MT…";
  try {
    const payload = await api.suggestMt(state.projectId, state.subId, cue.id);
    state.suggest = state.suggest || { tm: [], terms: [] };
    state.suggest.mt = payload;
    renderSuggestions();
    if (!payload.target) toast("MT 返回为空", true);
  } catch (error) {
    toast(error.message, true);
    renderSuggestions();
  }
}

/* ------------------------------------------------------- 分段状态 */


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

function parseHeaderLines(text) {
  const headers = {};
  for (const raw of String(text || "").split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const separator = line.indexOf(":");
    if (separator <= 0) continue;
    const key = line.slice(0, separator).trim();
    const value = line.slice(separator + 1).trim();
    if (key) headers[key] = value;
  }
  return headers;
}

function formatHeaderLines(headers) {
  return Object.entries(headers || {})
    .map(([key, value]) => `${key}: ${value}`).join("\n");
}

function sortedModels(models) {
  return [...new Set((models || []).map((item) => String(item || "").trim())
    .filter(Boolean))].sort(
      (left, right) => left.localeCompare(
        right, undefined, { sensitivity: "base" }));
}

function newProviderId() {
  return `prov_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function defaultProvider(service) {
  return {
    id: newProviderId(),
    service,
    provider_id: "",
    provider_name: "",
    api_type: service === "asr" ? "openai_audio" : "openai_chat",
    base_url: "",
    api_key: "",
    api_key_set: false,
    api_key_source: "none",
    headers: {},
    default_model: "",
    models: [],
    model_names: {},
    model_tags: {},
  };
}

function providerById(id) {
  if (state.currentProvider && state.currentProvider.id === id) {
    return state.currentProvider;
  }
  return state.providers.find((provider) => provider.id === id);
}

function renderActiveSelects() {
  for (const service of ["asr", "mt"]) {
    const select = $(`settings-${service}-active`);
    const current = state.activeProviders[service] || "";
    select.replaceChildren();
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "未选择";
    select.append(none);
    for (const provider of state.providers.filter(
      (item) => item.service === service)) {
      const option = document.createElement("option");
      option.value = provider.id;
      option.textContent = provider.provider_name
        || provider.provider_id || provider.id;
      select.append(option);
    }
    select.value = state.providers.some(
      (item) => item.id === current && item.service === service)
      ? current : "";
  }
}

function renderDialogModelSelect(provider, selected = "") {
  const select = $("provider-default-model");
  const names = state.providerModelNames[provider.id]
    || { ...(provider.model_names || {}) };
  state.providerModelNames[provider.id] = names;
  const current = String(selected || "");
  const available = sortedModels(
    state.providerModels[provider.id] || provider.models || []);
  if (current && !available.includes(current)) {
    available.push(current);
    available.sort((left, right) => left.localeCompare(
      right, undefined, { sensitivity: "base" }));
  }
  for (const model of available) {
    if (!names[model]) names[model] = model;
  }
  select.replaceChildren();
  for (const model of available) {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = names[model] || model;
    select.append(option);
  }
  const custom = document.createElement("option");
  custom.value = "__custom__";
  custom.textContent = "＋ 添加自定义模型";
  select.append(custom);
  select.value = current && available.includes(current) ? current : "__custom__";
  state.lastModelValue[provider.id] = select.value;
}

function setProviderMessage(text, error = false) {
  const node = $("provider-message");
  node.textContent = text;
  node.classList.toggle("is-error", error);
}

function setProviderFetchMessage(text, error = false) {
  const node = $("provider-fetch-message");
  node.textContent = text;
  node.classList.toggle("is-error", error);
}

function providerApiTypes(service) {
  return service === "asr"
    ? [["openai_audio", "OpenAI Audio（/v1/audio/transcriptions）"]]
    : [["openai_chat", "OpenAI Chat Completions"],
       ["openai_responses", "OpenAI Responses"],
       ["anthropic", "Anthropic Messages"]];
}

function populateProviderDialog(provider) {
  $("provider-dialog-title").textContent =
    `${state.providerDialogEditing ? "编辑" : "添加"} `
    + `${provider.service === "asr" ? "ASR" : "MT"} 提供商`;
  $("provider-submit").textContent =
    state.providerDialogEditing ? "保存" : "添加";
  $("provider-id").value = provider.id;
  $("provider-service").value = provider.service;
  $("provider-provider-id").value = provider.provider_id || "";
  $("provider-provider-name").value = provider.provider_name || "";
  $("provider-base-url").value = provider.base_url || "";
  $("provider-api-key").value = "";
  $("provider-headers").value = formatHeaderLines(provider.headers);
  $("provider-clear-message").textContent = "";
  $("provider-key-state").textContent = provider.api_key_set
    ? `Key configured (${provider.api_key_source || "settings"})`
    : "未配置 Key";
  $("provider-ignore-health").checked = Boolean(provider.ignore_health);
  const apiType = $("provider-api-type");
  apiType.replaceChildren();
  const types = providerApiTypes(provider.service);
  for (const [value, label] of types) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = label;
    apiType.append(option);
  }
  apiType.value = types.some(([value]) => value === provider.api_type)
    ? provider.api_type : types[0][0];
  renderDialogModelSelect(provider, provider.default_model);
  setProviderMessage("");
  setProviderFetchMessage("");
}

function openProviderDialog(service, provider = null) {
  const draft = provider
    ? {
        ...provider,
        headers: { ...(provider.headers || {}) },
        models: [...(provider.models || [])],
        model_names: { ...(provider.model_names || {}) },
        model_tags: { ...(provider.model_tags || {}) },
      }
    : defaultProvider(service);
  state.currentProvider = draft;
  state.providerDialogEditing = Boolean(provider);
  state.providerModels[draft.id] = [...(draft.models || [])];
  state.providerModelNames[draft.id] = { ...(draft.model_names || {}) };
  state.providerModelTags[draft.id] = { ...(draft.model_tags || {}) };
  state.clearKeys[draft.id] = false;
  state.lastModelValue[draft.id] = draft.default_model || "";
  populateProviderDialog(draft);
  $("provider-submit").disabled = false;
  $("provider-dialog").showModal();
  $("provider-provider-id").focus();
}

function closeProviderDialog(commit = false) {
  const provider = state.currentProvider;
  const id = provider ? provider.id : "";
  $("provider-dialog").close();
  if (!commit && id && !state.providers.some((item) => item.id === id)) {
    delete state.providerModels[id];
    delete state.providerModelNames[id];
    delete state.providerModelTags[id];
    delete state.clearKeys[id];
    delete state.lastModelValue[id];
  }
  state.currentProvider = null;
  state.providerDialogEditing = false;
}

function readProviderDialog() {
  const provider = state.currentProvider;
  const selected = $("provider-default-model").value;
  return {
    ...provider,
    provider_id: $("provider-provider-id").value.trim(),
    provider_name: $("provider-provider-name").value.trim(),
    api_type: $("provider-api-type").value,
    base_url: $("provider-base-url").value.trim(),
    api_key: $("provider-api-key").value.trim(),
    headers: parseHeaderLines($("provider-headers").value),
    default_model: selected === "__custom__" ? "" : selected,
    models: state.providerModels[provider.id] || provider.models || [],
    model_names: state.providerModelNames[provider.id]
      || provider.model_names || {},
    model_tags: state.providerModelTags[provider.id]
      || provider.model_tags || {},
    clear_api_key: Boolean(state.clearKeys[provider.id]),
    ignore_health: $("provider-ignore-health").checked,
  };
}

async function fetchProviderModels(providerId) {
  const provider = providerById(providerId);
  if (!provider) throw new Error("提供商不存在");
  const baseUrl = $("provider-base-url").value.trim();
  if (!baseUrl) throw new Error("请先填写 Endpoint / Base URL");
  const result = await api.providerModels({
    service: provider.service,
    id: provider.id,
    base_url: baseUrl,
    api_type: $("provider-api-type").value,
    api_key: $("provider-api-key").value.trim(),
    headers: parseHeaderLines($("provider-headers").value),
  });
  const selected = $("provider-default-model").value;
  state.providerModels[provider.id] = sortedModels([
    ...(state.providerModels[provider.id] || provider.models || []),
    ...(result.models || []),
  ]);
  const names = state.providerModelNames[provider.id]
    || { ...(provider.model_names || {}) };
  for (const model of result.models || []) {
    if (!names[model]) names[model] = model;
  }
  state.providerModelNames[provider.id] = names;
  renderDialogModelSelect(provider,
                          selected === "__custom__" ? "" : selected);
  return state.providerModels[provider.id];
}

async function saveProviderDialog(event) {
  event.preventDefault();
  const submit = $("provider-submit");
  if (submit.disabled) return;
  const provider = readProviderDialog();
  if (!provider.base_url) {
    setProviderMessage("请填写 Endpoint / Base URL。", true);
    return;
  }
  submit.disabled = true;
  setProviderMessage("检查 API…");
  let models;
  try {
    models = await fetchProviderModels(provider.id);
  } catch (error) {
    setProviderMessage(`API 不可用：${error.message}`, true);
    submit.disabled = false;
    return;
  }
  if (!provider.default_model) {
    if (!models.length) {
      setProviderMessage("未获取到模型，请手动添加或填写默认模型。", true);
      submit.disabled = false;
      return;
    }
    provider.default_model = models[0];
    renderDialogModelSelect(provider, provider.default_model);
  }
  if (provider.service === "mt" && !provider.default_model) {
    setProviderMessage("MT 提供商需要选择默认模型。", true);
    submit.disabled = false;
    return;
  }
  const providers = [...state.providers];
  const index = providers.findIndex((item) => item.id === provider.id);
  if (index >= 0) providers[index] = provider;
  else providers.push(provider);
  const active = { ...state.activeProviders };
  if (!active[provider.service]) active[provider.service] = provider.id;
  try {
    await api.saveSettings({
      providers,
      asr: { active_provider_id: active.asr },
      mt: { active_provider_id: active.mt },
    });
  } catch (error) {
    setProviderMessage(error.message, true);
    submit.disabled = false;
    return;
  }
  setProviderMessage(state.providerDialogEditing ? "保存成功" : "添加成功");
  setTimeout(async () => {
    closeProviderDialog(true);
    await loadSettings();
  }, 700);
}

function renderProviderList() {
  const list = $("settings-provider-list");
  list.replaceChildren();
  for (const provider of state.providers) {
    const row = document.createElement("div");
    row.className = "provider-row";
    const main = document.createElement("div");
    main.className = "provider-row-main";
    const name = document.createElement("div");
    name.className = "provider-row-name";
    name.textContent = provider.provider_name || "—";
    const id = document.createElement("div");
    id.className = "provider-row-id";
    id.textContent = provider.provider_id || "—";
    main.append(name, id);
    const meta = document.createElement("div");
    meta.className = "provider-row-meta";
    for (const text of [
      `模型数量 ${(provider.models || []).length}`,
      "丨",
      `默认模型 ${provider.default_model || "—"}`,
    ]) {
      const item = document.createElement("span");
      item.textContent = text;
      meta.append(item);
    }
    const actions = document.createElement("div");
    actions.className = "provider-row-actions";
    const edit = document.createElement("button");
    edit.className = "btn btn-quiet";
    edit.type = "button";
    edit.textContent = "修改";
    edit.addEventListener("click", () => {
      openProviderDialog(provider.service, provider);
    });
    const remove = document.createElement("button");
    remove.className = "btn provider-delete";
    remove.type = "button";
    remove.textContent = "删除";
    remove.addEventListener("click", () => deleteProvider(provider));
    actions.append(edit, remove);
    row.append(main, meta, actions);
    list.append(row);
  }
  $("settings-provider-empty").hidden = state.providers.length > 0;
  renderActiveSelects();
}

async function deleteProvider(provider) {
  if (!window.confirm(`删除提供商「${provider.provider_name || provider.provider_id || provider.id}」？`)) {
    return;
  }
  const providers = state.providers.filter((item) => item.id !== provider.id);
  const active = { ...state.activeProviders };
  if (active[provider.service] === provider.id) {
    const fallback = providers.find(
      (item) => item.service === provider.service);
    active[provider.service] = fallback ? fallback.id : "";
  }
  try {
    await api.saveSettings({
      providers,
      asr: { active_provider_id: active.asr },
      mt: { active_provider_id: active.mt },
    });
    await loadSettings();
    toast("提供商已删除");
  } catch (error) {
    setSettingsMessage(error.message, true);
  }
}

async function persistActive(service) {
  try {
    await api.saveSettings({
      providers: state.providers,
      asr: { active_provider_id: state.activeProviders.asr },
      mt: { active_provider_id: state.activeProviders.mt },
    });
    await loadSettings();
  } catch (error) {
    await loadSettings();
    setSettingsMessage(error.message, true);
  }
}

function openCustomModel(providerId) {
  const provider = providerById(providerId);
  if (!provider) return;
  state.customProviderId = providerId;
  state.customService = provider.service;
  state.customModelPrevious = state.lastModelValue[providerId] || "__custom__";
  $("model-id").value = "";
  $("model-name").value = "";
  $("model-reasoning").checked = false;
  $("model-image").checked = false;
  $("model-extra").hidden = provider.service !== "mt";
  $("model-message").textContent = "";
  $("model-message").classList.remove("is-error");
  $("model-dialog").showModal();
  $("model-id").focus();
}

function closeCustomModel(commit = false) {
  const providerId = state.customProviderId;
  const previous = state.customModelPrevious;
  $("model-dialog").close();
  state.customProviderId = "";
  state.customService = "";
  if (!commit && providerId) {
    const provider = providerById(providerId);
    if (provider) {
      renderDialogModelSelect(
        provider, previous === "__custom__" ? "" : previous);
    }
  }
}

function setSettingsMessage(text, error = false) {
  const node = $("settings-message");
  node.textContent = text;
  node.classList.toggle("is-error", error);
}

function bindSettings() {
  document.querySelectorAll("#settings-nav [data-settings-section]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        document.querySelectorAll("#settings-nav .settings-nav-item")
          .forEach((item) => item.classList.toggle(
            "is-active", item === button));
        document.querySelectorAll("[data-settings-panel]")
          .forEach((panel) => {
            panel.hidden = panel.dataset.settingsPanel
              !== button.dataset.settingsSection;
          });
      });
    });
  $("settings-add-asr").addEventListener(
    "click", () => openProviderDialog("asr"));
  $("settings-add-mt").addEventListener(
    "click", () => openProviderDialog("mt"));
  for (const service of ["asr", "mt"]) {
    $(`settings-${service}-active`).addEventListener("change", (event) => {
      state.activeProviders[service] = event.target.value;
      persistActive(service);
    });
  }
  $("provider-form").addEventListener("submit", saveProviderDialog);
  $("provider-cancel").addEventListener(
    "click", () => closeProviderDialog(false));
  $("provider-fetch-models").addEventListener("click", async () => {
    try {
      const models = await fetchProviderModels(state.currentProvider.id);
      setProviderFetchMessage(
        `获取到 ${models.length} 个模型/后端，请选择`);
    } catch (error) {
      setProviderFetchMessage(
        `自动获取失败：${error.message}；请手动填写模型`, true);
    }
  });
  $("provider-api-key").addEventListener("input", () => {
    if ($("provider-api-key").value) {
      state.clearKeys[state.currentProvider.id] = false;
      $("provider-clear-message").textContent = "";
    }
  });
  $("provider-clear-key").addEventListener("click", () => {
    state.clearKeys[state.currentProvider.id] = true;
    $("provider-api-key").value = "";
    $("provider-clear-message").textContent = "API Key已清除";
  });
  $("provider-default-model").addEventListener("change", () => {
    const value = $("provider-default-model").value;
    if (value === "__custom__") {
      openCustomModel(state.currentProvider.id);
    } else {
      state.lastModelValue[state.currentProvider.id] = value;
    }
  });
  $("model-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const provider = state.currentProvider;
    const modelId = $("model-id").value.trim();
    const name = $("model-name").value.trim();
    if (!modelId || !name) {
      $("model-message").textContent = "Model ID 和 Model Name 都需要填写。";
      $("model-message").classList.add("is-error");
      return;
    }
    if (!state.providerModelNames[provider.id]) {
      state.providerModelNames[provider.id] = {};
    }
    state.providerModelNames[provider.id][modelId] = name;
    if (provider.service === "mt") {
      if (!state.providerModelTags[provider.id]) {
        state.providerModelTags[provider.id] = {};
      }
      state.providerModelTags[provider.id][modelId] = {
        reasoning: $("model-reasoning").checked,
        image: $("model-image").checked,
      };
    }
    state.providerModels[provider.id] = sortedModels([
      ...(state.providerModels[provider.id] || []),
      modelId,
    ]);
    renderDialogModelSelect(provider, modelId);
    closeCustomModel(true);
  });
  $("model-cancel").addEventListener("click", () => closeCustomModel(false));
}

async function loadSettings() {
  try {
    state.settings = await api.settings();
    state.providers = (state.settings.providers || []).map((provider) => ({
      ...provider,
      headers: { ...(provider.headers || {}) },
      models: [...(provider.models || [])],
      model_names: { ...(provider.model_names || {}) },
      model_tags: { ...(provider.model_tags || {}) },
    }));
    state.activeProviders = {
      asr: state.settings.asr?.active_provider_id || "",
      mt: state.settings.mt?.active_provider_id || "",
    };
    state.providerModels = {};
    state.providerModelNames = {};
    state.providerModelTags = {};
    state.lastModelValue = {};
    state.clearKeys = {};
    for (const provider of state.providers) {
      state.providerModels[provider.id] = [...(provider.models || [])];
      state.providerModelNames[provider.id] = {
        ...(provider.model_names || {}),
      };
      state.providerModelTags[provider.id] = {
        ...(provider.model_tags || {}),
      };
      state.clearKeys[provider.id] = false;
    }
    state.currentProvider = null;
    state.providerDialogEditing = false;
    renderProviderList();
    setSettingsMessage("");
  } catch (error) {
    setSettingsMessage(error.message, true);
  }
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
      job.id.slice(-8), jobStatus(job.status), jobStage(job.stage),
      job.started_at ? elapsed(job.started_at) : "—",
      job.result ? `${job.result.words || 0} 词` : (job.error || ""),
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
    $("transcribe-message").textContent = `已加入队列：${job.id}`;
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
  if (!state.project || !state.subId) {
    state.styles = [];
    state.stylePayload = null;
    renderStyles();
    return;
  }
  try {
    const payload = await api.styles(state.projectId, state.subId);
    state.styles = payload.styles || [];
    state.stylePayload = payload;
    if (!state.styles.some((item) => item.name === state.styleName)) {
      state.styleName = state.styles.length ? state.styles[0].name : "";
    }
  } catch {
    state.styles = [];
    state.stylePayload = null;
  }
  renderStyles();
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
  if (cue.kind === "drawing") {
    node.textContent = "▣ 矢量绘图行";
    node.removeAttribute("style");
    return;
  }
  const style = state.styles.find((item) => item.name === cue.style)
    || state.styles.find((item) => item.name === "Default");
  node.textContent = visibleText(cue.target_text || cue.source_text)
    .replace(/\n/g, " ") || "样式预览";
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
    $("qa-message").textContent = "暂无质检项，请先运行质检。";
    return;
  }
  for (const item of state.qa) {
    const row = document.createElement("tr");
    const severity = document.createElement("td");
    severity.textContent = qaSeverity(item.severity);
    severity.className = item.severity === "error" ? "qa-error" : "qa-warning";
    const kind = document.createElement("td");
    kind.textContent = qaKind(item.kind);
    const message = document.createElement("td");
    message.textContent = qaMessage(item);
    const evidence = document.createElement("td");
    evidence.textContent = item.evidence || "";
    const action = document.createElement("td");
    const wrap = document.createElement("div");
    wrap.className = "qa-action";
    for (const [label, status] of [["接受", "accepted"],
                                   ["忽略", "ignored"]]) {
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
  if (!state.project || !state.subId) { toast("请先选择子项目", true); return; }
  $("qa-message").textContent = "正在运行质检…";
  try {
    await api.runQa(state.projectId, state.subId);
    await loadQa();
    $("qa-message").textContent = `质检项：${state.qa.length}`;
    toast("质检完成");
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
  $("transcript-full").textContent = text || "暂无转写稿。";
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
      if (result.changed) { await refreshSub(); toast("已撤销"); }
      else toast("没有可撤销的操作", true);
    } catch (error) { toast(error.message, true); }
  });
  $("redo").addEventListener("click", async () => {
    if (!state.project || !state.subId) return;
    try {
      const result = await api.redo(state.projectId, state.subId);
      if (result.changed) { await refreshSub(); toast("已重做"); }
      else toast("没有可重做的操作", true);
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
    context.fillText("波形不可用", 10, 38);
  }
}

function syncHeaderHeight() {
  const header = document.querySelector(".app-header");
  if (!header) return;
  document.documentElement.style.setProperty(
    "--header-h", `${header.offsetHeight}px`);
}

function setSidebar(open) {
  state.sidebarOpen = Boolean(open);
  $("app-sidebar").classList.toggle("is-open", state.sidebarOpen);
  $("app-sidebar").setAttribute("aria-hidden", String(!state.sidebarOpen));
  $("sidebar-toggle").setAttribute("aria-expanded", String(state.sidebarOpen));
  $("sidebar-backdrop").hidden = !state.sidebarOpen || state.sidebarPinned;
  document.body.classList.toggle(
    "sidebar-reserved", state.sidebarOpen && state.sidebarPinned);
  if (!state.sidebarOpen) $("health-popover").hidden = true;
  try {
    localStorage.setItem("ncat.sidebar", state.sidebarOpen ? "open" : "closed");
  } catch (error) {
    /* storage can be unavailable in private modes */
  }
}

function setSidebarPinned(pinned) {
  state.sidebarPinned = Boolean(pinned);
  $("sidebar-pinned").setAttribute(
    "aria-pressed", String(state.sidebarPinned));
  try {
    localStorage.setItem("ncat.sidebar.pinned",
                        state.sidebarPinned ? "1" : "0");
  } catch (error) {
    /* storage can be unavailable in private modes */
  }
  setSidebar(state.sidebarPinned ? true : state.sidebarOpen);
}

function initSidebar() {
  syncHeaderHeight();
  let stored = null;
  let pinned = false;
  try {
    stored = localStorage.getItem("ncat.sidebar");
    pinned = localStorage.getItem("ncat.sidebar.pinned") === "1";
  } catch (error) {
    /* storage can be unavailable in private modes */
  }
  state.sidebarPinned = pinned;
  $("sidebar-pinned").setAttribute("aria-pressed", String(pinned));
  $("sidebar-pinned").addEventListener("click", () => {
    setSidebarPinned(!state.sidebarPinned);
  });
  setSidebar(stored ? stored === "open" : false);
  if (pinned) setSidebar(true);
  $("sidebar-toggle").addEventListener("click", () => {
    setSidebar(!state.sidebarOpen);
  });
  $("sidebar-backdrop").addEventListener("click", () => setSidebar(false));
  window.addEventListener("resize", syncHeaderHeight);
  if (window.ResizeObserver) {
    new ResizeObserver(syncHeaderHeight).observe(
      document.querySelector(".app-header"));
  }
}

function bindTabs() {
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      switchTab(button.dataset.tab);
      if (!state.sidebarPinned) setSidebar(false);
    });
  });
  document.querySelectorAll("#project-tabs [data-project-tab]").forEach((button) => {
    button.addEventListener(
      "click", () => switchTab(button.dataset.projectTab, true));
  });
}

function showHome() {
  state.activeTab = "home";
  state.screen = "home";
  const subtitle = $("header-subtitle");
  if (subtitle) {
    subtitle.textContent = "Nick's Computer-Aided Translating Solution";
  }
  syncRoute();
  $("home-view").hidden = false;
  $("project-shell").hidden = true;
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.remove("is-active");
  });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tab === "home");
  });
}

function showGlobal(name) {
  state.activeTab = name;
  state.screen = "global";
  syncRoute();
  $("home-view").hidden = true;
  $("project-shell").hidden = true;
  document.querySelectorAll(".tab-panel").forEach((panel) => {
    panel.classList.toggle("is-active", panel.dataset.panel === name);
  });
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.classList.toggle("is-active", button.dataset.tab === name);
  });
}

function showProject(name) {
  if (name === "overview") {
    setProjectView("overview");
    return;
  }
  if (name === "board") {
    setProjectView("board");
    return;
  }
  if (name === "list") {
    setProjectView("list");
    return;
  }
  showSubprojectTab(name);
}

function switchTab(name, projectMode = false) {
  const aliases = { assets: "import", editor: "subtitle" };
  const tab = aliases[name] || name;
  if (projectMode && PROJECT_TABS.includes(tab)) {
    if (!state.project) {
      toast("请先选择项目", true);
      showHome();
      return;
    }
    if (!state.subId) {
      const first = (state.project.subprojects || [])[0];
      if (!first) {
        toast("请先新建子项目", true);
        return;
      }
      state.subId = first.id;
      state.subOpen = true;
      loadSub().catch((error) => toast(error.message, true));
    }
    showSubprojectTab(tab);
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
  document.querySelectorAll("#project-view-switch [data-project-view]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        setProjectView(button.dataset.projectView);
      });
    });
  $("project-search").addEventListener("input", () => {
    if (state.projectView === "board") renderBoard();
    else if (state.projectView === "list") renderSubprojectList();
  });
  $("filter-button").addEventListener("click", (event) => {
    event.stopPropagation();
    const popover = $("filter-popover");
    popover.hidden = !popover.hidden;
  });
  document.querySelectorAll("#filter-popover [data-filter-template]")
    .forEach((button) => {
      button.addEventListener("click", () => {
        const input = $("project-search");
        const template = button.dataset.filterTemplate
          .replace(/&gt;/g, ">").replace(/&lt;/g, "<");
        input.value = `${input.value} ${template}`.trim();
        input.focus();
        $("filter-popover").hidden = true;
        if (state.projectView === "board") renderBoard();
        else if (state.projectView === "list") renderSubprojectList();
      });
    });
  $("list-mode-toggle").addEventListener("click", () => {
    state.listMode = state.listMode === "flat" ? "tree" : "flat";
    $("list-mode-toggle").textContent =
      state.listMode === "flat" ? "层级" : "平铺";
    renderSubprojectList();
  });
  $("new-subproject").addEventListener("click", async () => {
    if (!state.project) return;
    if (!(state.project.categories || []).length) {
      toast("请先在项目设置 → 编辑看板中创建分类和泳道", true);
      return;
    }
    try {
      const sub = await api.createSubproject(state.projectId, {});
      await selectProject(state.projectId);
      await openSubproject(sub.id);
      setTimeout(() => {
        const name = $("detail-name");
        if (name) { name.focus(); name.select(); }
      }, 50);
      toast("已新建子项目");
    } catch (error) {
      toast(error.message, true);
    }
  });
  $("project-settings").addEventListener("click", (event) => {
    event.stopPropagation();
    const menu = $("project-settings-menu");
    menu.hidden = !menu.hidden;
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest("#project-settings")
        && !event.target.closest("#project-settings-menu")) {
      $("project-settings-menu").hidden = true;
    }
    if (!event.target.closest("#filter-button")
        && !event.target.closest("#filter-popover")) {
      $("filter-popover").hidden = true;
    }
  });
  $("edit-project-info").addEventListener("click", openProjectInfo);
  $("edit-board").addEventListener("click", openBoardSettings);
  $("edit-project-settings").addEventListener("click", openProjectSettings);
  bindProjectSettings();
  bindSubprojectDetail();
}

function openProjectInfo() {
  $("project-settings-menu").hidden = true;
  $("edit-project-name").value = state.project.project.name;
  $("project-info-message").textContent = "";
  $("project-info-dialog").showModal();
}

function bindProjectSettings() {
  $("project-info-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await api.patchProject(state.projectId, {
        name: $("edit-project-name").value.trim(),
      });
      $("project-info-dialog").close();
      await selectProject(state.projectId);
      toast("项目信息已保存");
    } catch (error) {
      $("project-info-message").textContent = error.message;
    }
  });
  $("cancel-project-info").addEventListener(
    "click", () => $("project-info-dialog").close());
  $("add-category").addEventListener("click", async () => {
    const name = $("new-category-name").value.trim();
    if (!name) return;
    try {
      await api.addCategory(state.projectId, { name });
      $("new-category-name").value = "";
      await refreshProjectDialogData();
      renderBoardSettings();
    } catch (error) { $("board-settings-message").textContent = error.message; }
  });
  $("close-board-settings").addEventListener(
    "click", () => $("board-settings-dialog").close());
  $("close-project-settings").addEventListener(
    "click", () => $("project-settings-dialog").close());
  $("add-rule").addEventListener("click", async () => {
    try {
      await api.addAutomation(state.projectId, {
        subtask_title: $("rule-subtask-title").value.trim(),
        target_category_id: $("rule-target-category").value,
        target_lane_id: $("rule-target-lane").value,
      });
      $("rule-subtask-title").value = "";
      await refreshProjectDialogData();
      renderProjectSettings();
      toast("自动化规则已添加");
    } catch (error) { $("project-settings-message").textContent = error.message; }
  });
}

async function refreshProjectDialogData() {
  const project = await api.project(state.projectId);
  state.project = project;
}

function openBoardSettings() {
  $("project-settings-menu").hidden = true;
  $("board-settings-message").textContent = "";
  renderBoardSettings();
  $("board-settings-dialog").showModal();
}

function renderBoardSettings() {
  const root = $("board-settings-list");
  root.replaceChildren();
  for (const category of state.project.categories || []) {
    const section = document.createElement("section");
    section.className = "board-settings-category";
    const head = document.createElement("div");
    head.className = "board-settings-head";
    const name = document.createElement("strong");
    name.textContent = category.name;
    const rename = document.createElement("button");
    rename.className = "btn btn-quiet";
    rename.textContent = "改名";
    rename.addEventListener("click", async () => {
      const value = window.prompt("分类名称", category.name);
      if (value === null || !value.trim()) return;
      await api.patchCategory(state.projectId, category.id, { name: value });
      await refreshProjectDialogData();
      renderBoardSettings();
    });
    const remove = document.createElement("button");
    remove.className = "btn provider-delete";
    remove.textContent = "删除";
    remove.addEventListener("click", async () => {
      if (!window.confirm(`删除分类「${category.name}」？`)) return;
      try {
        await api.deleteCategory(state.projectId, category.id);
        await refreshProjectDialogData();
        renderBoardSettings();
      } catch (error) { $("board-settings-message").textContent = error.message; }
    });
    const up = document.createElement("button");
    up.className = "btn btn-quiet";
    up.textContent = "↑";
    up.disabled = state.project.categories.indexOf(category) === 0;
    up.addEventListener("click", () => reorderCategory(category.id, -1));
    const down = document.createElement("button");
    down.className = "btn btn-quiet";
    down.textContent = "↓";
    down.disabled = state.project.categories.indexOf(category)
      === state.project.categories.length - 1;
    down.addEventListener("click", () => reorderCategory(category.id, 1));
    head.append(name, up, down, rename, remove);
    section.append(head);
    for (const lane of category.lanes || []) {
      const row = document.createElement("div");
      row.className = "board-settings-lane";
      const label = document.createElement("span");
      label.textContent = lane.name;
      const renameLane = document.createElement("button");
      renameLane.className = "btn btn-quiet";
      renameLane.textContent = "改名";
      renameLane.addEventListener("click", async () => {
        const value = window.prompt("泳道名称", lane.name);
        if (value === null || !value.trim()) return;
        await api.patchLane(state.projectId, category.id, lane.id,
                            { name: value });
        await refreshProjectDialogData();
        renderBoardSettings();
      });
      const removeLane = document.createElement("button");
      removeLane.className = "btn provider-delete";
      removeLane.textContent = "删除";
      removeLane.addEventListener("click", async () => {
        if (!window.confirm(`删除泳道「${lane.name}」？`)) return;
        try {
          await api.deleteLane(state.projectId, category.id, lane.id);
          await refreshProjectDialogData();
          renderBoardSettings();
        } catch (error) {
          $("board-settings-message").textContent = error.message;
        }
      });
      const laneUp = document.createElement("button");
      laneUp.className = "btn btn-quiet";
      laneUp.textContent = "↑";
      laneUp.disabled = category.lanes.indexOf(lane) === 0;
      laneUp.addEventListener("click", () => reorderLane(category.id, lane.id, -1));
      const laneDown = document.createElement("button");
      laneDown.className = "btn btn-quiet";
      laneDown.textContent = "↓";
      laneDown.disabled = category.lanes.indexOf(lane)
        === category.lanes.length - 1;
      laneDown.addEventListener("click", () => reorderLane(category.id, lane.id, 1));
      row.append(label, laneUp, laneDown, renameLane, removeLane);
      section.append(row);
    }
    const add = document.createElement("button");
    add.className = "btn btn-quiet";
    add.textContent = "添加泳道";
    add.addEventListener("click", async () => {
      const value = window.prompt("新泳道名称");
      if (value === null || !value.trim()) return;
      await api.addLane(state.projectId, category.id, { name: value });
      await refreshProjectDialogData();
      renderBoardSettings();
    });
    section.append(add);
    root.append(section);
  }
  if (!(state.project.categories || []).length) {
    root.textContent = "还没有分类。先添加一个分类，再添加泳道。";
  }
}

function openProjectSettings() {
  $("project-settings-menu").hidden = true;
  $("project-settings-message").textContent = "";
  renderProjectSettings();
  $("project-settings-dialog").showModal();
}

function renderProjectSettings() {
  const features = state.project.features || {};
  const grid = $("feature-grid");
  grid.replaceChildren();
  const labels = {
    ffmpeg: "媒体处理 (ffmpeg)", asr: "ASR", segment: "分句",
    timing: "时轴", mt: "机器翻译", subtitle_edit: "字幕编辑",
    export: "导出渲染",
  };
  for (const [key, label] of Object.entries(labels)) {
    const wrap = document.createElement("label");
    wrap.className = "field checkbox";
    const span = document.createElement("span");
    span.textContent = label;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(features[key]);
    input.addEventListener("change", async () => {
      try {
        const result = await api.features(state.projectId,
                                          { [key]: input.checked });
        state.project.features = result.features;
        syncProjectTabs();
      } catch (error) { $("project-settings-message").textContent = error.message; }
    });
    wrap.append(span, input);
    grid.append(wrap);
  }
  const categorySelect = $("rule-target-category");
  categorySelect.replaceChildren();
  for (const category of state.project.categories || []) {
    const option = document.createElement("option");
    option.value = category.id;
    option.textContent = category.name;
    categorySelect.append(option);
  }
  const syncLanes = () => {
    const category = (state.project.categories || [])
      .find((item) => item.id === categorySelect.value);
    const laneSelect = $("rule-target-lane");
    laneSelect.replaceChildren();
    for (const lane of (category && category.lanes) || []) {
      const option = document.createElement("option");
      option.value = lane.id;
      option.textContent = lane.name;
      laneSelect.append(option);
    }
  };
  categorySelect.onchange = syncLanes;
  syncLanes();
  const titles = new Set();
  for (const sub of state.project.subprojects || []) {
    for (const task of sub.subtasks || []) titles.add(task.title);
  }
  const options = $("subtask-title-options");
  options.replaceChildren();
  for (const title of titles) {
    const option = document.createElement("option");
    option.value = title;
    options.append(option);
  }
  const list = $("automation-list");
  list.replaceChildren();
  for (const rule of state.project.automation || []) {
    const row = document.createElement("div");
    row.className = "link-row";
    const text = document.createElement("span");
    text.textContent = `完成「${rule.subtask_title}」→ 移动到目标泳道`;
    const toggle = document.createElement("button");
    toggle.className = "btn btn-quiet";
    toggle.textContent = rule.enabled ? "停用" : "启用";
    toggle.addEventListener("click", async () => {
      await api.patchAutomation(state.projectId, rule.id,
                                { enabled: !rule.enabled });
      await refreshProjectDialogData();
      renderProjectSettings();
    });
    const remove = document.createElement("button");
    remove.className = "btn provider-delete";
    remove.textContent = "删除";
    remove.addEventListener("click", async () => {
      await api.deleteAutomation(state.projectId, rule.id);
      await refreshProjectDialogData();
      renderProjectSettings();
    });
    row.append(text, toggle, remove);
    list.append(row);
  }
  if (!(state.project.automation || []).length) {
    list.textContent = "暂无自动化规则。";
  }
}

function bindSubprojectDetail() {
  $("detail-category").addEventListener("change", () => {
    renderDetailLanes($("detail-category").value, "");
  });
  $("save-subproject-info").addEventListener("click", async () => {
    try {
      await api.patchSubproject(state.projectId, state.subId, {
        name: $("detail-name").value.trim(),
        description: $("detail-description").value,
        category_id: $("detail-category").value,
        lane_id: $("detail-lane").value,
        due: $("detail-due").value || null,
        done: $("detail-done").checked,
      });
      await selectProject(state.projectId, {
        subId: state.subId, tab: state.activeTab,
      });
      toast("子项目信息已保存");
    } catch (error) {
      $("detail-message").textContent = error.message;
    }
  });
  $("delete-subproject").addEventListener("click", async () => {
    if (!state.subId || !window.confirm("删除当前子项目及其全部文件？")) return;
    const projectId = state.projectId;
    try {
      await api.deleteSubproject(projectId, state.subId);
      await selectProject(projectId);
      toast("子项目已删除");
    } catch (error) { toast(error.message, true); }
  });
  $("add-subtask").addEventListener("click", async () => {
    const title = $("new-subtask-title").value.trim();
    if (!title) return;
    try {
      await api.addSubtask(state.projectId, state.subId, title);
      $("new-subtask-title").value = "";
      await refreshSub();
      toast("子任务已添加");
    } catch (error) { toast(error.message, true); }
  });
  $("add-internal-link").addEventListener("click", async () => {
    try {
      await api.addInternalLink(state.projectId, state.subId, {
        target_subproject_id: $("new-internal-target").value,
        label: $("new-internal-label").value.trim(),
      });
      $("new-internal-label").value = "";
      await refreshSub();
    } catch (error) { toast(error.message, true); }
  });
  $("add-external-link").addEventListener("click", async () => {
    try {
      await api.addExternalLink(state.projectId, state.subId, {
        label: $("new-external-label").value.trim(),
        url: $("new-external-url").value.trim(),
      });
      $("new-external-label").value = "";
      $("new-external-url").value = "";
      await refreshSub();
    } catch (error) { toast(error.message, true); }
  });
  $("add-comment").addEventListener("click", async () => {
    const body = $("new-comment").value.trim();
    if (!body) return;
    try {
      await api.addComment(state.projectId, state.subId, body);
      $("new-comment").value = "";
      await refreshSub();
    } catch (error) { toast(error.message, true); }
  });
  $("add-attachment").addEventListener("click", async () => {
    const file = $("attachment-file").files[0];
    if (!file) return;
    try {
      await api.uploadAttachment(state.projectId, state.subId, file);
      $("attachment-file").value = "";
      await refreshSub();
      toast("附件已上传");
    } catch (error) { toast(error.message, true); }
  });
  $("open-qa").addEventListener("click", () => showSubprojectTab("qa"));
  $("open-styles").addEventListener("click", () => showSubprojectTab("styles"));
  $("back-to-subtitle").addEventListener(
    "click", () => showSubprojectTab("subtitle"));
  $("open-transcript").addEventListener(
    "click", () => showSubprojectTab("transcript"));
  $("back-to-export").addEventListener(
    "click", () => showSubprojectTab("export"));
}

async function reorderCategory(categoryId, delta) {
  const list = [...state.project.categories];
  const index = list.findIndex((item) => item.id === categoryId);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= list.length) return;
  [list[index], list[target]] = [list[target], list[index]];
  for (let i = 0; i < list.length; i += 1) {
    await api.patchCategory(state.projectId, list[i].id, { order: i });
  }
  await refreshProjectDialogData();
  renderBoardSettings();
}

async function reorderLane(categoryId, laneId, delta) {
  const category = state.project.categories.find((item) => item.id === categoryId);
  if (!category) return;
  const list = [...category.lanes];
  const index = list.findIndex((item) => item.id === laneId);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= list.length) return;
  [list[index], list[target]] = [list[target], list[index]];
  for (let i = 0; i < list.length; i += 1) {
    await api.patchLane(state.projectId, categoryId, list[i].id, { order: i });
  }
  await refreshProjectDialogData();
  renderBoardSettings();
}

function bindProjectForm() {
  $("project-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = $("new-name").value.trim();
    if (!name) {
      $("project-message").textContent = "项目名称不能为空。";
      $("project-message").classList.add("is-error");
      return;
    }
    try {
      const created = await api.createProject({
        name, layout: $("new-layout").value,
      });
      $("project-message").textContent = "项目已创建。";
      $("project-message").classList.remove("is-error");
      $("new-name").value = "";
      $("project-dialog").close();
      await loadWorkspace();
      await selectProject(created.project.id);
    } catch (error) {
      $("project-message").textContent = error.message;
      $("project-message").classList.add("is-error");
    }
  });
  $("cancel-project").addEventListener("click", () => {
    $("new-name").value = "";
    $("project-dialog").close();
    $("project-message").textContent = "";
    $("project-message").classList.remove("is-error");
  });
}

function bindHome() {
  $("new-project").addEventListener("click", () => {
    $("new-name").value = "";
    $("project-message").textContent = "";
    $("project-message").classList.remove("is-error");
    $("project-dialog").showModal();
    $("new-name").focus();
  });
  $("home-brand").addEventListener("click", () => {
    showHome();
    if (!state.sidebarPinned) setSidebar(false);
  });
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
        if (!window.confirm("将用转写稿替换现有分段与字幕行，继续？")) return;
        await runAction("segment", { propose: true, force: true });
      } else if (action === "align") {
        await runAction("align", {});
      } else if (action === "translate") {
        await runAction("translate", {});
      } else if (action === "style") {
        const template = window.prompt("ASS 模板路径（可选）", "");
        await runAction("style", template ? { template } : {});
      } else if (action === "render") {
        await runAction("render", { preset: "original" });
      } else if (action === "import-ass") {
        if (!window.confirm("导入 ASS 将替换现有字幕行与分段，继续？")) return;
        await runAction("import-ass", {
          path: $("ass-path").value.trim(),
          bilingual_only: $("ass-bilingual-only").checked,
        });
      } else if (action === "import-text") {
        if (!window.confirm("导入文本将替换现有字幕行与分段，继续？")) return;
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
  bindTagEditor("target-text", "target");
  bindTagEditor("source-text", "source");
  for (const id of ["start-ms", "end-ms", "cue-style", "cue-locked"]) {
    $(id).addEventListener("input", markDirty);
    $(id).addEventListener("change", markDirty);
  }
  $("save-cue").addEventListener("click", () => saveCue().catch(
    (error) => toast(error.message, true)));
  $("segment-status").addEventListener("change", (event) => {
    applySegmentStatus(event.target.value);
  });
  $("suggest-refresh").addEventListener("click", () => {
    loadSuggestions().catch((error) => toast(error.message, true));
  });
  $("suggest-mt").addEventListener("click", () => {
    requestMtSuggestion().catch((error) => toast(error.message, true));
  });
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
      toast("已拆分");
    } catch (error) { toast(error.message, true); }
  });
  $("merge-cue").addEventListener("click", async () => {
    const cue = currentCue();
    const index = state.cues.findIndex((item) => item.id === (cue && cue.id));
    const next = index >= 0 ? state.cues[index + 1] : null;
    if (!cue || !next) { toast("没有下一行", true); return; }
    await saveCue();
    try {
      const merged = await api.mergeCue(
        state.projectId, state.subId, cue.id, next.id);
      await refreshSub();
      state.selectedId = merged.id;
      renderAll();
      toast("已合并");
    } catch (error) { toast(error.message, true); }
  });
  $("delete-cue").addEventListener("click", async () => {
    const cue = currentCue();
    if (!cue) return;
    if (!window.confirm("删除这一行？")) return;
    try {
      await api.deleteCue(state.projectId, state.subId, cue.id);
      await refreshSub();
      toast("已删除");
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
      document.activeElement && document.activeElement.tagName)
      || Boolean(document.activeElement && document.activeElement.isContentEditable);
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

function bindTagEditor(id, key) {
  const node = $(id);
  node.addEventListener("input", () => {
    const baseline = state.editorBaseline ? state.editorBaseline[key] : null;
    const value = readTagEditor(node);
    if (baseline != null && tagSignature(value) !== baseline) {
      const restore = state.editorLastGood ? state.editorLastGood[key] : "";
      renderTagEditor(node, restore);
      focusEnd(node);
      toast("覆盖标签受保护，只能编辑正文", true);
      return;
    }
    if (state.editorLastGood) state.editorLastGood[key] = value;
    if (key === "target") $("target-tags").textContent = tagCount(value);
    else $("source-tags").textContent = tagCount(value);
    markDirty();
  });
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      insertBreak(node);
    }
  });
  node.addEventListener("paste", (event) => {
    event.preventDefault();
    const clipboard = event.clipboardData || window.clipboardData;
    const text = clipboard.getData("text").replace(/[\r\n]+/g, " ");
    document.execCommand("insertText", false, text);
  });
}

function focusEnd(node) {
  node.focus();
  const range = document.createRange();
  range.selectNodeContents(node);
  range.collapse(false);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
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
    if (!projectId) { toast("请先选择项目筛选或打开项目", true); return; }
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
      toast("术语库已导入");
    } catch (error) { toast(error.message, true); }
  });
}

function bindTm() {
  $("tm-project-filter").addEventListener("change", () => {
    loadTm().catch((error) => toast(error.message, true));
  });
  $("import-tm").addEventListener("click", async () => {
    const projectId = assetTargetProject("tm-project-filter");
    if (!projectId) { toast("请先选择项目筛选或打开项目", true); return; }
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
      toast("记忆库已导入");
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
    ? "代理已就绪" : "尚无代理视频，请先转码入库。";
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
