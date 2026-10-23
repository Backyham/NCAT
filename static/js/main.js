/* Entry point.
 *
 * Modules load as ES modules, so each file is fetched separately and a
 * syntax error in one does not take the whole application down the way it
 * did when everything shared a single inline script.
 */
import { PAGES } from './sections.js';
import { initNav, placeholder } from './nav.js';
import { initHealth } from './health.js';

function fillPlaceholders() {
  for (const page of PAGES) {
    const node = document.querySelector(`[data-page="${page.id}"]`);
    if (node && !node.innerHTML.trim()) node.innerHTML = placeholder(page);
  }
}

function boot() {
  fillPlaceholders();
  initNav();
  initHealth();
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}