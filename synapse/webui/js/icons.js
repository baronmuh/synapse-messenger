/* ==========================================================================
   Synapse — Iconography (24 px grid, 1.75 stroke, rounded caps)
   Usage: icon('dashboard') -> SVG string to insert.
   ========================================================================== */

const PATHS = {
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  agents: '<circle cx="9" cy="8" r="3.5"/><path d="M3.5 19.5c.8-3.2 3-5 5.5-5s4.7 1.8 5.5 5"/><circle cx="17" cy="9" r="2.6"/><path d="M15.5 14.6c2.8.1 4.8 1.8 5.5 4.9"/>',
  message: '<path d="M21 12a8 8 0 0 1-8 8H5l-2 2V12a8 8 0 0 1 8-8h2a8 8 0 0 1 8 8Z"/><path d="M8.5 10.5h7M8.5 13.5h4.5"/>',
  tasks: '<rect x="4" y="3.5" width="16" height="17" rx="2.5"/><path d="M8.5 8.5 10 10l3.5-3.5M8.5 13.5h7M8.5 17h7"/>',
  activity: '<path d="M3 12h4l2.5-7 5 14 2.5-7h4"/>',
  organisation: '<path d="M3.5 21V7l6-3.5L15.5 7v14"/><path d="M3.5 12h12M3.5 17h12"/><path d="M15.5 10h5v11h-5"/><path d="M17 13.5h2M17 17h2"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M6.5 7.5h.01M6.5 16.5h.01M10 7.5h7.5M10 16.5h7.5"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20.5 20.5-4.6-4.6"/>',
  bell: '<path d="M18 9.5a6 6 0 0 0-12 0c0 6-2.5 7-2.5 7h17S18 15.5 18 9.5"/><path d="M10.3 20a2 2 0 0 0 3.4 0"/>',
  shield: '<path d="M12 3 5 5.5v5.2c0 4.6 3 7.9 7 9.8 4-1.9 7-5.2 7-9.8V5.5Z"/><path d="m9.3 11.8 2 2 3.6-3.8"/>',
  lock: '<rect x="5" y="11" width="14" height="9.5" rx="2"/><path d="M8.5 11V8a3.5 3.5 0 0 1 7 0v3"/>',
  chevron: '<path d="m9 6 6 6-6 6"/>',
  chevronDown: '<path d="m6 9 6 6 6-6"/>',
  arrowRight: '<path d="M4 12h15M13.5 6l6 6-6 6"/>',
  arrowLeft: '<path d="M20 12H5M10.5 6l-6 6 6 6"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  refresh: '<path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 3.5V8h-4.5"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  alert: '<path d="M12 3.5 2.5 20h19Z"/><path d="M12 10v4.5M12 17.5h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5.5M12 7.5h.01"/>',
  error: '<circle cx="12" cy="12" r="9"/><path d="m8.5 8.5 7 7M15.5 8.5l-7 7"/>',
  warning: '<path d="M12 3.5 2.5 20h19Z"/><path d="M12 10v4.5M12 17.5h.01"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5c1.2-3.8 4-5.5 7.5-5.5s6.3 1.7 7.5 5.5"/>',
  bot: '<rect x="5" y="8" width="14" height="11" rx="3"/><path d="M12 8V5M9 3.5h6M12 13.5h.01M8.5 13.5h.01M15.5 13.5h.01"/><circle cx="12" cy="16" r=".01"/><path d="M8.5 16h.01M15.5 16h.01"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
  zap: '<path d="M13 2.5 4.5 13.5H11l-1 8L18.5 10.5H12Z"/>',
  external: '<path d="M14 5h5v5M19 5l-8.5 8.5"/><path d="M19 13.5V19a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 5 19V8a1.5 1.5 0 0 1 1.5-1.5H12"/>',
  send: '<path d="M21 3.5 10.5 14M21 3.5 14 21l-3.5-7L3.5 10.5Z"/>',
  chat: '<path d="M21 12a8 8 0 0 1-8 8H4.5l1.8-1.8A8 8 0 1 1 21 12Z"/><path d="M8.5 10.5h7M8.5 13.5h4.5"/>',
  logout: '<path d="M15 4.5V3.5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v17a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-1"/><path d="M10.5 12H21M17.5 8l4 4-4 4"/>',
  lock: '<rect x="5" y="11" width="14" height="9.5" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
  filter: '<path d="M4 5h16l-6.5 7.5V19l-3 1.5v-8Z"/>',
  doc: '<path d="M7 3.5h7L19 8v12.5H7Z"/><path d="M14 3.5V8h5M9.5 12.5h6M9.5 16h6"/>',
  key: '<circle cx="8.5" cy="15.5" r="4.5"/><path d="m12 12 8-8M16 8l3 3M14 10l2 2"/>',
  link: '<path d="M10 14a4.5 4.5 0 0 0 6.4.4l2.5-2.5a4.5 4.5 0 0 0-6.4-6.4L11.5 6.5"/><path d="M14 10a4.5 4.5 0 0 0-6.4-.4l-2.5 2.5a4.5 4.5 0 0 0 6.4 6.4l1.5-1.5"/>',
  eye: '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z"/><circle cx="12" cy="12" r="3"/>',
  grid: '<rect x="4" y="4" width="7" height="7" rx="1.5"/><rect x="13" y="4" width="7" height="7" rx="1.5"/><rect x="4" y="13" width="7" height="7" rx="1.5"/><rect x="13" y="13" width="7" height="7" rx="1.5"/>',
  cpu: '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="10" y="10" width="4" height="4"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
  home: '<path d="m3.5 11 8.5-7 8.5 7"/><path d="M5.5 9.5V20h13V9.5"/>',
  pulse: '<path d="M3 12h4l2.5-7 5 14 2.5-7h4"/>',
  spark: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5Z"/><path d="m3 13 9 5 9-5"/>',
  inbox: '<path d="M4 4h16v13H4Z"/><path d="M4 11h5l1.5 2.5h3L15 11h5"/>',
  users: '<circle cx="9" cy="8.5" r="3.5"/><path d="M3 19.5c.9-3.3 3.1-5 6-5s5.1 1.7 6 5"/><path d="M16 5.6a3.5 3.5 0 0 1 0 5.8M18.5 14.7c1.6.8 2.6 2.3 3 4.8"/>',
  database: '<ellipse cx="12" cy="5.5" rx="8" ry="3"/><path d="M4 5.5v13c0 1.7 3.6 3 8 3s8-1.3 8-3v-13"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
  globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.5 3.8 5.6 3.8 9s-1.3 6.5-3.8 9c-2.5-2.5-3.8-5.6-3.8-9S9.5 5.5 12 3Z"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.8 2.1c-.8.5-1.3 1-1.3 2.4M12 17h.01"/>',
  power: '<path d="M12 3v8"/><path d="M6.3 6.5a8 8 0 1 0 11.4 0"/>',
  terminal: '<rect x="3" y="4.5" width="18" height="15" rx="2"/><path d="m7 9.5 3 3-3 3M13 15.5h4"/>',
  bookmark: '<path d="M6 3.5h12V21l-6-4-6 4Z"/>',
  flag: '<path d="M5.5 21V4"/><path d="M5.5 4C9 2.5 11.5 2.5 15 4c2.5 1 4.5.8 6 0v9c-1.5.8-3.5 1-6 0-3.5-1.5-6-1.5-9.5 0"/>',
  scale: '<path d="M12 3v18M8 21h8M4 7h16"/><path d="M6 7 3.5 13a3 3 0 0 0 5 0ZM18 7l-2.5 6a3 3 0 0 0 5 0Z"/>',
  download: '<path d="M12 4v10M8 10.5 12 14.5l4-4"/><path d="M4 19h16"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/>',
  sortAsc: '<path d="M12 4v16M8 8l4-4 4 4M8 16l4 4 4-4"/>',
  sortDesc: '<path d="M12 20V4M8 16l4 4 4-4M8 8l4-4 4 4"/>',
  collapse: '<path d="m6 9 6 6 6-6"/>',
  expand: '<path d="m6 15 6-6 6 6"/>',
};

const ICON_CACHE = new Map();

export function icon(name, size = 16, stroke = 'currentColor') {
  const key = `${name}:${size}`;
  let svg = ICON_CACHE.get(key);
  if (!svg) {
    const body = PATHS[name] || PATHS.help;
    svg = `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="${stroke}" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
    ICON_CACHE.set(key, svg);
  }
  return svg;
}

export function hasIcon(name) { return name in PATHS; }

/* Synapse brand mark: two linked nodes (nerve influx). */
export function brandMark(size = 26) {
  return `<svg class="brand-mark" width="${size}" height="${size}" viewBox="0 0 32 32" aria-hidden="true">
    <rect width="32" height="32" rx="9" fill="var(--color-paper-2)" stroke="var(--color-rule)"/>
    <circle cx="11" cy="16" r="4.2" fill="none" stroke="var(--color-accent)" stroke-width="2.4"/>
    <circle cx="21" cy="16" r="4.2" fill="none" stroke="var(--color-link)" stroke-width="2.4"/>
    <path d="M15.2 16h1.6" stroke="var(--color-accent)" stroke-width="2.4" stroke-linecap="round"/>
  </svg>`;
}
