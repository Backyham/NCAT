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
  subprojects: (id) => request(`/projects/${enc(id)}/subprojects`),
  createSubproject: (id, body) => request(`/projects/${enc(id)}/subprojects`, {
    method: "POST", body: JSON.stringify(body),
  }),
  patchSubproject: (id, sid, body) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}`,
    { method: "PATCH", body: JSON.stringify(body) },
  ),
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
  styles: (id, sid) => request(
    `/projects/${enc(id)}/subprojects/${enc(sid)}/styles`),
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
  settings: () => request("/settings"),
  saveSettings: (body) => request("/settings", {
    method: "PATCH", body: JSON.stringify(body),
  }),
};
