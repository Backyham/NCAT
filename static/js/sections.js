/* The six sections the tool exposes, in pipeline order.
 *
 * `hint` documents what each page is for so the shell can explain itself
 * while the pages are still being built.
 */
export const PAGES = [
  { id: 'home',      label: 'Home',      hint: 'Jobs, pipeline, glossary' },
  { id: 'download',  label: 'Download',  hint: 'Fetch source media' },
  { id: 'transcribe', label: 'Transcribe', hint: 'Speech to text, and review' },
  { id: 'translate', label: 'Translate', hint: 'Machine translation' },
  { id: 'timeline',  label: 'Timeline',  hint: 'Cues and timing' },
  { id: 'export',    label: 'Export',    hint: 'Render and download' },
];

/* Icons are inline paths rather than an icon package: six glyphs do not
 * justify a dependency, and the previous build already avoided one. */
const PATHS = {
  home: 'M3 9.5 10 4l7 5.5V16a1 1 0 0 1-1 1h-4v-4H8v4H4a1 1 0 0 1-1-1z',
  download: 'M10 3v9m0 0 3.5-3.5M10 12 6.5 8.5M4 15v1a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-1',
  transcribe: 'M10 3a3 3 0 0 1 3 3v4a3 3 0 0 1-6 0V6a3 3 0 0 1 3-3zM5 10a5 5 0 0 0 10 0M10 15v2',
  translate: 'M3 5h7M6.5 5v-1M5 5c0 3 2 5 4.5 6M8 5c0 3-2 5-4.5 6M11 16l3.5-8 3.5 8M12.4 13.4h4.2',
  timeline: 'M3 6h14M3 10h14M3 14h14M6 4v4M12 8v4M9 12v4',
  export: 'M10 13V4m0 0L6.5 7.5M10 4l3.5 3.5M4 12v3a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-3',
};

export function icon(id) {
  const d = PATHS[id] || '';
  return `<svg viewBox="0 0 20 20" fill="none" stroke="currentColor"
               stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
               aria-hidden="true"><path d="${d}"/></svg>`;
}