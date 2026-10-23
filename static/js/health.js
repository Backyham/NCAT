/* Backend status, shown at the right of the header.
 *
 * It polls /health, which is a liveness check: it answers only when the ASGI
 * app is serving, so "ok" means the backend is up and its job directory is
 * readable. It says nothing about whether the models or the GPU are usable —
 * /api/models does that. The colour tells the two apart at a glance so a
 * working server is not mistaken for a working pipeline.
 */

const POLL_MS = 15000;

function paint(text, state) {
  const node = document.getElementById('health');
  node.textContent = text;
  node.classList.toggle('is-ok', state === 'ok');
  node.classList.toggle('is-down', state === 'down');
}

async function poll() {
  try {
    const response = await fetch('/health', { cache: 'no-store' });
    if (!response.ok) throw new Error(String(response.status));
    const data = await response.json();
    paint(`ok · ${data.jobs_dir ?? ''}`, 'ok');
  } catch {
    paint('unreachable', 'down');
  }
}

export function initHealth() {
  poll();
  setInterval(poll, POLL_MS);
}