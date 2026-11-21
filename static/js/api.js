const BASE = "/api/v1";

async function request(path, options = {}) {
  const response = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await response.text();
  let payload = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = { output: text }; }
  }
  if (!response.ok) {
    const error = payload && payload.error ? payload.error : {};
    throw new Error(error.message || text || `HTTP ${response.status}`);
  }
  return payload;
}

const enc = encodeURIComponent;

export const api = {
  workspace: () => request("/workspace"),
  createProject: (body) => request("/projects", {
    method: "POST", body: JSON.stringify(body),
  }),
  project: (id) => request(`/projects/${enc(id)}`),
  patchProject: (id, body) => request(`/projects/${enc(id)}`, {
    method: "PATCH", body: JSON.stringify(body),
  }),
  projectBoard: (id) => request(`/projects/${enc(id)}/board`),
  overview: (id) => request(`/projects/${enc(id)}/overview`),
  activity: (id, limit = 50) => request(
    `/projects/${enc(id)}/activity?limit=${limit}`),
  features: (id, body) => request(`/projects/${enc(id)}/features`, {
    method: "PATCH", body: JSON.stringify(body),
  }),
  addCategory: (id, body) => request(`/projects/${enc(id)}/categories`, {
    method: "POST", body: JSON.stringify(body),
  }),
  patchCategory: (id, cid, body) => request(
    `/projects/${enc(id)}/categories/${enc(cid)}`,
    { method: "PATCH", body: JSON.stringify(body) }),
  deleteCategory: (id, cid) => request(
    `/projects/${enc(id)}/categories/${enc(cid)}`, { method: "DELETE" }),
  addLane: (id, cid, body) => request(
    `/projects/${enc(id)}/categories/${enc(cid)}/lanes`,
    { method: "POST", body: JSON.stringify(body) }),
  patchLane: (id, cid, lid, body) => request(
    `/projects/${enc(id)}/categories/${enc(cid)}/lanes/${enc(lid)}`,
    { method: "PATCH", body: JSON.stringify(body) }),
  deleteLane: (id, cid, lid) => request(
    `/projects/${enc(id)}/categories/${enc(cid)}/lanes/${enc(lid)}`,
    { method: "DELETE" }),
  automation: (id) => request(`/projects/${enc(id)}/automation`),
  addAutomation: (id, body) => request(`/projects/${enc(id)}/automation`, {
    method: "POST", body: JSON.stringify(body),
  }),
  patchAutomation: (id, rid, body) => request(
    `/projects/${enc(id)}/automation/${enc(rid)}`,
    { method: "PATCH", body: JSON.stringify(body) }),
  deleteAutomation: (id, rid) => request(
    `/projects/${enc(id)}/automation/${enc(rid)}`, { method: "DELETE" }),
  subprojects: (id) => request(`/projects/${enc(id)}/subprojects`),
  createSubproject: (id, body) => request(`/projects/${enc(id)}/subprojects`, {
    method: "POST", body: JSON.stringify(body),
  }),
  patchSubproject: (id, sid, body) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}`,
    { method: "PATCH", body: JSON.stringify(body) },
  ),
  deleteSubproject: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}`,
    { method: "DELETE" },
  ),
  addSubtask: (id, sid, title) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/subtasks`,
    { method: "POST", body: JSON.stringify({ title }) }),
  patchSubtask: (id, sid, tid, body) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/subtasks/${enc(tid)}`,
    { method: "PATCH", body: JSON.stringify(body) }),
  deleteSubtask: (id, sid, tid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/subtasks/${enc(tid)}`,
    { method: "DELETE" }),
  addInternalLink: (id, sid, body) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/internal-links`,
    { method: "POST", body: JSON.stringify(body) }),
  deleteInternalLink: (id, sid, lid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/internal-links/${enc(lid)}`,
    { method: "DELETE" }),
  addExternalLink: (id, sid, body) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/external-links`,
    { method: "POST", body: JSON.stringify(body) }),
  deleteExternalLink: (id, sid, lid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/external-links/${enc(lid)}`,
    { method: "DELETE" }),
  addComment: (id, sid, body) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/comments`,
    { method: "POST", body: JSON.stringify({ body }) }),
  deleteComment: (id, sid, cid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/comments/${enc(cid)}`,
    { method: "DELETE" }),
  attachments: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/attachments`),
  uploadAttachment: async (id, sid, file) => {
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(
      `${BASE}/projects/${enc(id)}/subprojects/${enc(sid)}/attachments`,
      { method: "POST", body: form });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : null;
    if (!response.ok) {
      throw new Error((payload && payload.error && payload.error.message)
        || text || `HTTP ${response.status}`);
    }
    return payload;
  },
  deleteAttachment: (id, sid, aid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/attachments/${enc(aid)}`,
    { method: "DELETE" }),
  attachmentUrl: (id, sid, aid) =>
    `${BASE}/projects/${enc(id)}/subprojects/${enc(sid)}/attachments/${enc(aid)}`,
  segments: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/segments`),
  cues: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/cues`),
  transcript: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/transcript`),
  mediaInfo: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/media-info`),
  action: (id, sid, action, body = {}) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/actions/${action}`,
    { method: "POST", body: JSON.stringify(body) },
  ),
  importAss: (id, sid, body = {}) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/import/ass`,
    { method: "POST", body: JSON.stringify(body) },
  ),
  patchCue: (id, sid, cueId, body) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/cues/${enc(cueId)}`,
    { method: "PATCH", body: JSON.stringify(body) },
  ),
  splitCue: (id, sid, cueId, body = {}) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/cues/${enc(cueId)}/split`,
    { method: "POST", body: JSON.stringify(body) },
  ),
  mergeCue: (id, sid, cueId, otherId) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/cues/${enc(cueId)}/merge`,
    { method: "POST", body: JSON.stringify({ other_id: otherId }) },
  ),
  deleteCue: (id, sid, cueId) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/cues/${enc(cueId)}`,
    { method: "DELETE" },
  ),
  importAss: (id, sid, body) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/import/ass`,
    { method: "POST", body: JSON.stringify(body) },
  ),
  allTermbases: (projectId) => request(
    `/termbases${projectId ? `?project_id=${enc(projectId)}` : ""}`),
  allTm: (projectId) => request(
    `/tm${projectId ? `?project_id=${enc(projectId)}` : ""}`),
  termbases: (id) => request(`/projects/${enc(id)}/termbases`),
  termbase: (id, tb) => request(`/projects/${enc(id)}/termbases/${enc(tb)}`),
  importTermbase: (id, body) => request(`/projects/${enc(id)}/termbases`, {
    method: "POST", body: JSON.stringify(body),
  }),
  tm: (id) => request(`/projects/${enc(id)}/tm`),
  importTm: (id, body) => request(`/projects/${enc(id)}/tm`, {
    method: "POST", body: JSON.stringify(body),
  }),
  effectiveTerms: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/terms`),
  effectiveTm: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/tm`),
  search: (id, sid, q) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/search?q=${enc(q)}`),
  qa: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/qa`),
  runQa: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/qa`, { method: "POST" }),
  patchQa: (id, sid, qaId, status) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/qa/${enc(qaId)}`,
    { method: "PATCH", body: JSON.stringify({ status }) }),
  suggest: (id, sid, q) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/suggest?q=${enc(q)}`),
  suggestMt: (id, sid, cueId) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/cues/${enc(cueId)}/suggest-mt`,
    { method: "POST" }),
  patchSegment: (id, sid, segmentId, body) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/segments/${enc(segmentId)}`,
    { method: "PATCH", body: JSON.stringify(body) }),
  styles: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/styles`),
  patchStyle: (id, sid, body) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/styles`,
    { method: "PATCH", body: JSON.stringify(body) }),
  audit: (id, sid, limit = 50) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/audit?limit=${limit}`),
  history: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/history`),
  undo: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/history`,
    { method: "POST", body: JSON.stringify({ action: "undo" }) }),
  redo: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/history`,
    { method: "POST", body: JSON.stringify({ action: "redo" }) }),
  transcribeBackends: () => request("/transcribe/backends"),
  transcribeJobs: () => request("/transcribe/jobs"),
  createJob: (body) => request("/transcribe/jobs", {
    method: "POST", body: JSON.stringify(body),
  }),
  job: (id) => request(`/transcribe/jobs/${enc(id)}`),
  cancelJob: (id) => request(`/transcribe/jobs/${enc(id)}`, {
    method: "DELETE",
  }),
  system: () => request("/system"),
  healthStatus: () => request("/health/status"),
  board: (projectId) => request(
    `/board${projectId ? `?project_id=${enc(projectId)}` : ""}`),
  boardMove: (moves) => request("/board/move", {
    method: "POST", body: JSON.stringify({ moves }),
  }),
  workflow: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/workflow`),
  patchWorkflow: (id, sid, body) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/workflow`,
    { method: "PATCH", body: JSON.stringify(body) }),
  settings: () => request("/settings"),
  saveSettings: (body) => request("/settings", {
    method: "PATCH", body: JSON.stringify(body),
  }),
  providerModels: (body) => request("/settings/models", {
    method: "POST", body: JSON.stringify(body),
  }),
};
