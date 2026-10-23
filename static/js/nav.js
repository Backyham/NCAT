/* Sidebar, routing and page switching.
 *
 * Hash routing rather than the History API: a reload or a pasted link has to
 * land on the same section, and a static file server has no rewrite rules
 * to turn a real path into index.html.
 *
 * No page is ever refused. An earlier version bounced "Transcribe" back to
 * Home when no job was selected, which read as a broken link; an empty page
 * with a placeholder is a better answer than a redirect the user cannot
 * explain.
 */
import { PAGES, icon } from './sections.js';

const COLLAPSE_KEY = 'ncat.nav.collapsed';
const DEFAULT_PAGE = 'home';

const el = (id) => document.getElementById(id);

function currentPage() {
  const id = (location.hash || '').replace(/^#\/?/, '');
  return PAGES.some((p) => p.id === id) ? id : DEFAULT_PAGE;
}

function buildNav() {
  el('nav').innerHTML = PAGES.map((p) => `
    <a class="nav-item" data-nav="${p.id}" href="#/${p.id}"
       title="${p.hint}">
      ${icon(p.id)}<span class="nav-label">${p.label}</span>
    </a>`).join('');
}

function show(id) {
  const target = PAGES.some((p) => p.id === id) ? id : DEFAULT_PAGE;
  document.querySelectorAll('[data-page]').forEach((node) => {
    node.hidden = node.dataset.page !== target;
  });
  document.querySelectorAll('[data-nav]').forEach((node) => {
    node.classList.toggle('is-active', node.dataset.nav === target);
    node.setAttribute('aria-current', node.dataset.nav === target ? 'page' : 'false');
  });
  document.title = `NCAT · ${PAGES.find((p) => p.id === target).label}`;
}

function setCollapsed(collapsed) {
  const bar = el('sidebar');
  bar.classList.toggle('is-collapsed', collapsed);
  el('nav-chevron').textContent = collapsed ? '›' : '‹';
  const label = bar.querySelector('.nav-toggle .nav-label');
  if (label) label.textContent = collapsed ? '' : 'Collapse';
  el('nav-toggle').setAttribute('aria-expanded', String(!collapsed));
  try {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? '1' : '0');
  } catch {
    /* private browsing: the preference simply does not persist */
  }
}

export function initNav() {
  buildNav();

  let collapsed = false;
  try {
    collapsed = localStorage.getItem(COLLAPSE_KEY) === '1';
  } catch {
    /* ignore */
  }
  setCollapsed(collapsed);

  el('nav-toggle').addEventListener('click', () => {
    setCollapsed(!el('sidebar').classList.contains('is-collapsed'));
  });

  window.addEventListener('hashchange', () => show(currentPage()));
  show(currentPage());

  /* A narrow window cannot afford a 14rem rail beside the content. */
  const mq = window.matchMedia('(max-width: 767px)');
  const applyViewport = () => {
    if (mq.matches) setCollapsed(true);
  };
  mq.addEventListener('change', applyViewport);
  applyViewport();
}

export function placeholder(page) {
  return `<div class="empty">
    <h2>${page.label}</h2>
    <p>${page.hint} — not built yet.</p>
  </div>`;
}