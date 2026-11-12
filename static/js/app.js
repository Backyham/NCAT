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
  suggest: null,
  stylePayload: null,
  styleName: "",
  styleFilledFor: "",
  projectView: "board",
  board: null,
  expanded: new Set(),
  collapsedLanes: new Set(),
};


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

async function boot() {
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
  $("board-filter").addEventListener("input", () => renderBoard());
  $("board-show-archived").addEventListener("change", () => renderBoard());
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

async function selectProject(id) {
  state.projectId = id;
  if (!id) { state.project = null; state.subId = ""; state.sub = null; renderAll(); showHome(); return; }
  setStatus("载入中");
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
    setProjectView(state.projectView);
    setStatus("就绪");
  } catch (error) {
    setStatus("加载失败", true);
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
  renderSubprojectSelect();
  renderAll();
  loadHistory().catch(() => {});
  loadStyles().catch(() => {});
  loadSuggestions().catch(() => {});
}

function renderProjectHeader() {
  if (!state.project) {
    $("project-name").textContent = "项目";
    $("project-meta").textContent = "";
    return;
  }
  const project = state.project.project;
  const sub = currentSub();
  $("project-name").textContent = project.name;
  $("project-meta").textContent = [
    project.client || "未填客户",
    `${project.source_lang} → ${(project.target_langs || []).join(", ")}`,
    sub ? `${sub.name} · ${kindLabel(sub.kind)}` : "无子项目",
    `${state.project.counts.subprojects} 个子项目`,
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
      `${project.counts.cues} 行`,
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
    for (const [label, tab] of [["素材", "assets"], ["编辑", "editor"]]) {
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
    : "未选择项目";
  $("subproject-select").disabled = !state.project;
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
    const payload = await api.action(state.projectId, state.subId, action, body);
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
  const hardware = service.hardware || {};
  const cpu = hardware.cpu || {};
  if (service.state !== "ok" || !Object.keys(cpu).length) {
    const detail = service.state === "unreachable"
      ? `不可达${service.detail ? "：" + service.detail : ""}`
      : service.state === "not_configured" ? "未配置" : "无硬件数据";
    hwText(card, detail);
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
  return card;
}

function renderHealth() {
  const health = state.health || {};
  const root = $("health-services");
  root.replaceChildren();
  const services = health.services || [];
  for (const service of services) root.append(renderServiceCard(service));
  $("health-updated").textContent = health.collected_at
    ? `更新于 ${new Date(health.collected_at).toLocaleTimeString()}` : "—";
  const down = services.some((service) => service.state === "unreachable");
  const hot = services.some((service) => {
    const gpu = (service.hardware || {}).gpu || {};
    return gpu.temperature_c >= 85
      || (gpu.memory_total_mb && gpu.memory_used_mb / gpu.memory_total_mb >= 0.95);
  });
  $("health-dot").className = "health-dot "
    + (down ? "is-down" : hot ? "is-warn" : "is-ok");
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


/* ------------------------------------------------------------- 看板视图 */

function stageLabel(key) {
  const columns = (state.board && state.board.columns) || [];
  const column = columns.find((item) => item.key === key);
  return column ? column.label : key;
}

function boardCard(card) {
  const element = document.createElement("article");
  element.className = "board-card";
  element.draggable = true;
  element.dataset.subprojectId = card.subproject_id;
  element.dataset.stage = card.stage;
  element.dataset.kind = card.kind || "video";
  const updated = card.updated_at ? new Date(card.updated_at).getTime() : 0;
  if (updated && Date.now() - updated < 24 * 3600 * 1000) {
    element.classList.add("is-recent");
  }
  const head = document.createElement("div");
  head.className = "board-card-head";
  const name = document.createElement("strong");
  name.textContent = card.name;
  head.append(name);
  if (card.stage_source === "manual") {
    const badge = document.createElement("span");
    badge.className = "board-badge";
    badge.textContent = "手动";
    badge.title = `自动推导为「${stageLabel(card.derived_stage)}」`;
    head.append(badge);
  }
  element.append(head);
  const meta = document.createElement("div");
  meta.className = "board-card-meta";
  meta.textContent = `${kindLabel(card.kind)} · ${(card.langs || []).join(" → ")}`;
  element.append(meta);
  const total = card.evidence ? card.evidence.segments : 0;
  const approved = card.evidence ? card.evidence.approved : 0;
  const bar = document.createElement("div");
  bar.className = "board-progress";
  const fill = document.createElement("i");
  fill.style.width = `${total ? Math.round(100 * approved / total) : 0}%`;
  bar.append(fill);
  element.append(bar);
  const evidence = document.createElement("div");
  evidence.className = "board-card-evidence";
  const parts = [`已确认 ${approved}/${total}`];
  if (card.evidence && card.evidence.qa_open) {
    parts.push(`QA 待办 ${card.evidence.qa_open}`);
  }
  if (card.evidence && card.evidence.cues) {
    parts.push(`${card.evidence.cues} 行`);
  }
  evidence.textContent = parts.join(" · ");
  element.append(evidence);
  const footer = [];
  if (card.workflow && card.workflow.due) footer.push(`截止 ${card.workflow.due}`);
  if (card.workflow && card.workflow.priority
      && card.workflow.priority !== "normal") {
    footer.push(card.workflow.priority === "high" ? "高优先级" : "低优先级");
  }
  if (card.workflow && card.workflow.blocked
      && card.workflow.blocked.reason) {
    footer.push(`受阻：${card.workflow.blocked.reason}`);
  }
  if (footer.length) {
    const foot = document.createElement("div");
    foot.className = "board-card-foot";
    const overdue = card.workflow && card.workflow.due
      && new Date(card.workflow.due).getTime() < Date.now()
      && !["delivered", "archived"].includes(card.stage);
    foot.classList.toggle("is-overdue", Boolean(overdue));
    foot.textContent = footer.join(" · ");
    element.append(foot);
  }
  element.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("text/plain",
      JSON.stringify({ id: card.subproject_id, kind: card.kind || "video" }));
    event.dataTransfer.effectAllowed = "move";
    element.classList.add("is-dragging");
  });
  element.addEventListener("dragend", () => {
    element.classList.remove("is-dragging");
  });
  element.addEventListener("click", () => {
    openSubproject(card.subproject_id).catch((error) => toast(error.message, true));
  });
  return element;
}

async function loadBoard() {
  if (!state.projectId) {
    state.board = null;
    renderBoard();
    return;
  }
  try {
    state.board = await api.board(state.projectId);
  } catch (error) {
    state.board = null;
    toast(error.message, true);
  }
  renderBoard();
}

function boardCardMatches(card, query) {
  if (!query) return true;
  const haystack = [card.name, card.kind, ...(card.langs || []),
                    ...((card.workflow && card.workflow.labels) || [])]
    .join(" ").toLowerCase();
  return haystack.includes(query);
}

const LANE_LABELS = { video: "视频", text: "文本", other: "其他" };

function laneName(kind) {
  return LANE_LABELS[kind] || (kind ? kind : "其他");
}

function renderBoard() {
  const root = $("board");
  if (!root) return;
  root.replaceChildren();
  const data = state.board;
  if (!data) {
    const loading = document.createElement("div");
    loading.className = "board-empty";
    loading.textContent = "正在加载看板…";
    root.append(loading);
    return;
  }
  const query = (($("board-filter") || {}).value || "").trim().toLowerCase();
  const showArchived = !$("board-show-archived")
    || $("board-show-archived").checked;
  const columns = data.columns || [];
  const cards = (data.cards || []).filter((card) => {
    if (!showArchived && card.stage === "archived") return false;
    return boardCardMatches(card, query);
  });
  const kinds = [];
  for (const card of cards) {
    const kind = card.kind || "other";
    if (!kinds.includes(kind)) kinds.push(kind);
  }
  if (!kinds.length) kinds.push("video");
  const grid = document.createElement("div");
  grid.className = "board-grid";
  grid.style.gridTemplateColumns = `5.5rem repeat(${columns.length}, 17rem)`;

  const spacer = document.createElement("div");
  spacer.className = "board-corner";
  spacer.textContent = "泳道";
  grid.append(spacer);
  for (const column of columns) {
    const head = document.createElement("div");
    head.className = "board-head-cell";
    const label = document.createElement("span");
    label.textContent = column.label;
    const count = document.createElement("span");
    count.textContent = String(cards.filter(
      (card) => card.stage === column.key).length);
    head.append(label, count);
    grid.append(head);
  }

  for (const kind of kinds) {
    const laneCards = cards.filter((card) => (card.kind || "other") === kind);
    const laneKey = kind || "other";
    const collapsed = state.collapsedLanes.has(laneKey);
    const laneHead = document.createElement("button");
    laneHead.type = "button";
    laneHead.className = "board-lane-head";
    laneHead.textContent = `${collapsed ? "▸" : "▾"} ${laneName(kind)}`;
    laneHead.title = collapsed ? "展开泳道" : "收起泳道";
    laneHead.addEventListener("click", () => {
      if (collapsed) state.collapsedLanes.delete(laneKey);
      else state.collapsedLanes.add(laneKey);
      renderBoard();
    });
    if (collapsed) {
      laneHead.style.gridColumn = "1 / -1";
      laneHead.classList.add("is-collapsed");
      laneHead.textContent = `▸ ${laneName(kind)} · ${laneCards.length} 张卡片`;
      grid.append(laneHead);
      continue;
    }
    grid.append(laneHead);
    for (const column of columns) {
      const cell = document.createElement("div");
      cell.className = "board-cell";
      cell.dataset.stage = column.key;
      cell.dataset.kind = kind;
      const cellCards = laneCards.filter((card) => card.stage === column.key);
      for (const card of cellCards) cell.append(boardCard(card));
      if (!cellCards.length) {
        const empty = document.createElement("div");
        empty.className = "board-column-empty";
        empty.textContent = "—";
        cell.append(empty);
      }
      cell.addEventListener("dragover", (event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
        cell.classList.add("is-drop");
      });
      cell.addEventListener("dragleave", (event) => {
        if (!cell.contains(event.relatedTarget)) cell.classList.remove("is-drop");
      });
      cell.addEventListener("drop", (event) => {
        event.preventDefault();
        cell.classList.remove("is-drop");
        let payload = null;
        try {
          payload = JSON.parse(event.dataTransfer.getData("text/plain"));
        } catch {
          payload = null;
        }
        if (!payload || !payload.id) return;
        if ((payload.kind || "video") !== kind) {
          toast("不能跨泳道（类型）移动卡片", true);
          return;
        }
        moveCard(payload.id, column.key, kind)
          .catch((error) => toast(error.message, true));
      });
      grid.append(cell);
    }
  }
  root.append(grid);
}

async function moveCard(subprojectId, stage, kind) {
  const card = ((state.board && state.board.cards) || [])
    .find((item) => item.subproject_id === subprojectId);
  if (!card) return;
  if (kind && (card.kind || "video") !== kind) {
    toast("不能跨泳道（类型）移动卡片", true);
    return;
  }
  if (card.stage === stage && card.stage_source === "auto") return;
  const order = ((state.board && state.board.columns) || [])
    .map((column) => column.key);
  const from = order.indexOf(card.derived_stage);
  const to = order.indexOf(stage);
  let reason = "";
  if (from >= 0 && to >= 0 && to < from) {
    reason = window.prompt(
      `把「${card.name}」移回「${stageLabel(stage)}」需要填写原因：`, "");
    if (reason === null) return;
    if (!reason.trim()) {
      toast("需要填写原因", true);
      return;
    }
  }
  try {
    await api.patchWorkflow(card.project_id, subprojectId, {
      stage_override: stage === "archived" ? null : stage,
      archived: stage === "archived",
      reason,
      revision: card.revision,
    });
    await loadBoard();
    toast(`已移到「${stageLabel(stage)}」`);
  } catch (error) {
    toast(error.message, true);
    await loadBoard();
  }
}

async function openSubproject(id) {
  state.subId = id;
  localStorage.setItem(`ncat.sub.${state.projectId}`, id);
  await loadSub();
  setProjectView("list");
  switchTab("editor");
}

function setProjectView(view) {
  state.projectView = view === "list" ? "list" : "board";
  document.querySelectorAll("#project-view-switch .view-btn").forEach((button) => {
    button.classList.toggle("is-active",
      button.dataset.projectView === state.projectView);
  });
  const board = $("project-board");
  const tabs = $("project-tabs");
  const panels = [...document.querySelectorAll("[data-project-panel]")];
  if (!board || !tabs) return;
  if (state.projectView === "board") {
    if (state.project) {
      $("home-view").hidden = true;
      $("project-shell").hidden = false;
    }
    board.hidden = false;
    tabs.hidden = true;
    panels.forEach((panel) => {
      panel.hidden = true;
      panel.classList.remove("is-active");
    });
    loadBoard().catch(() => {});
    return;
  }
  board.hidden = true;
  tabs.hidden = false;
  const name = state.activeTab && state.activeTab !== "home"
    ? state.activeTab : "assets";
  showProject(name);
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
      : "未配置 ASR Key（本地模式无需）";
    $("settings-mt-key-state").textContent = mt.api_key_set
      ? `MT key configured (${mt.api_key_source})`
      : "未配置 MT Key（本地模式无需）";
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
      $("settings-message").textContent = "设置已保存。";
      $("settings-message").classList.remove("is-error");
      await loadSettings();
      await loadHealth();
      toast("设置已保存");
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
  if (state.projectView === "board") {
    $("project-board").hidden = false;
    $("project-tabs").hidden = true;
    document.querySelectorAll("[data-project-panel]").forEach((panel) => {
      panel.hidden = true;
      panel.classList.remove("is-active");
    });
    renderBoard();
    return;
  }
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
  if (name === "styles") {
    loadStyles().catch((error) => toast(error.message, true));
    loadAudit().catch(() => {});
  }
  if (name === "qa") loadQa().catch((error) => toast(error.message, true));
  requestAnimationFrame(() => {
    renderCues();
    renderEditor();
  });
}

function switchTab(name) {
  const projectTabs = ["assets", "editor", "transcript", "styles", "qa"];
  if (projectTabs.includes(name)) {
    if (!state.project) {
      toast("请先选择项目", true);
      showHome();
      return;
    }
    if (state.projectView === "board") {
      state.projectView = "list";
      document.querySelectorAll("#project-view-switch .view-btn").forEach((button) => {
        button.classList.toggle("is-active",
          button.dataset.projectView === "list");
      });
      $("project-board").hidden = true;
      $("project-tabs").hidden = false;
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
      $("project-message").textContent = "项目名称不能为空。";
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
      $("project-message").textContent = "项目已创建。";
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
    if (!state.project) { toast("请先选择项目", true); return; }
    $("subproject-dialog").showModal();
  });
  $("cancel-subproject").addEventListener("click", () => {
    $("subproject-dialog").close();
  });
  $("add-subproject").addEventListener("click", async () => {
    if (!state.project) { toast("请先选择项目", true); return; }
    try {
      const sub = await api.createSubproject(state.projectId, {
        name: $("sub-name").value.trim() || "子项目",
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
      toast("子项目已添加");
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
