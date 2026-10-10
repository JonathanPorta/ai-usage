/* ai-usage icon set — one outline family (24px grid, 1.75 stroke, round caps),
   Lucide-derived shapes inlined so the prototype works offline.
   SwiftUI mapping candidates are listed in design-notes.md (SF Symbols). */
(function () {
  const P = {
    activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    'chevron-right': '<path d="m9 18 6-6-6-6"/>',
    'chevron-left': '<path d="m15 18-6-6 6-6"/>',
    'chevron-down': '<path d="m6 9 6 6 6-6"/>',
    'circle-check': '<circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    'circle-alert': '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5"/><path d="M12 16.2h.01"/>',
    'circle-x': '<circle cx="12" cy="12" r="9"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>',
    'circle-pause': '<circle cx="12" cy="12" r="9"/><path d="M10 9v6"/><path d="M14 9v6"/>',
    'circle-stop': '<circle cx="12" cy="12" r="9"/><rect x="9" y="9" width="6" height="6" rx=".8"/>',
    'circle-dashed': '<path d="M10.1 3.2a9 9 0 0 1 3.8 0"/><path d="M17.6 5.3a9 9 0 0 1 1.9 1.9"/><path d="M20.8 10.1a9 9 0 0 1 0 3.8"/><path d="M18.7 17.6a9 9 0 0 1-1.9 1.9"/><path d="M13.9 20.8a9 9 0 0 1-3.8 0"/><path d="M6.4 18.7a9 9 0 0 1-1.9-1.9"/><path d="M3.2 13.9a9 9 0 0 1 0-3.8"/><path d="M5.3 6.4a9 9 0 0 1 1.9-1.9"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    'clock-alert': '<path d="M12 7v5l2.5 1.5"/><path d="M20.9 13.2A9 9 0 1 0 12 21"/><path d="M19 16v3"/><path d="M19 22h.01"/>',
    'refresh': '<path d="M20 12a8 8 0 1 1-2.3-5.6"/><path d="M20 4v5h-5"/>',
    loader: '<path d="M21 12a9 9 0 1 1-6.2-8.6"/>',
    pause: '<rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/>',
    play: '<path d="M7 4.8v14.4a.8.8 0 0 0 1.2.7l11.3-7.2a.8.8 0 0 0 0-1.4L8.2 4.1a.8.8 0 0 0-1.2.7Z"/>',
    square: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    power: '<path d="M12 3v9"/><path d="M18.4 6.6a9 9 0 1 1-12.8 0"/>',
    settings: '<path d="M20 7h-9"/><path d="M14 17H5"/><circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>',
    chart: '<path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="M8 17v-4"/><path d="M13 17V9"/><path d="M18 17V5"/>',
    gauge: '<path d="m12 14 4-4"/><path d="M3.3 19a10 10 0 1 1 17.4 0"/>',
    lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    plug: '<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/><path d="M18 8v5a6 6 0 0 1-12 0V8Z"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    external: '<path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    'file-text': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h5"/>',
    terminal: '<path d="m4 17 6-6-6-6"/><path d="M12 19h8"/>',
    bell: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
    'minus': '<path d="M5 12h14"/>',
    x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    hourglass: '<path d="M5 22h14"/><path d="M5 2h14"/><path d="M17 22v-4.2a2 2 0 0 0-.6-1.4L12 12l-4.4 4.4a2 2 0 0 0-.6 1.4V22"/><path d="M7 2v4.2a2 2 0 0 0 .6 1.4L12 12l4.4-4.4a2 2 0 0 0 .6-1.4V2"/>',
    sparkle: '<path d="M12 3v4"/><path d="M12 17v4"/><path d="M3 12h4"/><path d="M17 12h4"/><path d="m6.3 6.3 2.1 2.1"/><path d="m15.6 15.6 2.1 2.1"/><path d="m6.3 17.7 2.1-2.1"/><path d="m15.6 8.4 2.1-2.1"/>'
  };

  function icon(name, cls, label) {
    const body = P[name] || P.info;
    const a11y = label ? `role="img" aria-label="${label}"` : 'aria-hidden="true"';
    return `<svg class="icon ${cls || ''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" ${a11y}>${body}</svg>`;
  }

  /* Menu-bar glyph: monochrome template image (macOS tints it). State is carried
     by shape — a badge or an arc — never by color alone. 18pt canvas. */
  function menuGlyph(state, opts) {
    const o = opts || {};
    const base = '<path d="M1.5 9h3l2-5.5 4 11 2-5.5h4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>';
    const dim = '<path d="M1.5 9h3l2-5.5 4 11 2-5.5h4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" opacity=".4"/>';
    const cut = (x, y) => `<circle cx="${x}" cy="${y}" r="4.6" style="fill:var(--mb-bg, #f6f6f8)"/>`;
    let badge = '';
    let glyph = base;
    const bx = 15.5, by = 13.5;
    switch (state) {
      case 'collecting':
        badge = cut(bx, by) + `<g class="${o.animate === false ? '' : 'spin-origin'}"><path d="M${bx + 3} ${by}a3 3 0 1 1-1-2.2" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></g>`;
        break;
      case 'paused':
        badge = cut(bx, by) + `<rect x="${bx - 2.2}" y="${by - 2.6}" width="1.6" height="5.2" rx=".5" fill="currentColor"/><rect x="${bx + .6}" y="${by - 2.6}" width="1.6" height="5.2" rx=".5" fill="currentColor"/>`;
        break;
      case 'stopped':
        glyph = dim;
        badge = cut(bx, by) + `<rect x="${bx - 2.5}" y="${by - 2.5}" width="5" height="5" rx="1" fill="currentColor"/>`;
        break;
      case 'attention':
        badge = cut(bx, by) + `<circle cx="${bx}" cy="${by}" r="3.4" fill="currentColor"/><path d="M${bx} ${by - 1.8}v2" style="stroke:var(--mb-bg, #f6f6f8)" stroke-width="1.3" stroke-linecap="round"/><circle cx="${bx}" cy="${by + 1.6}" r=".7" style="fill:var(--mb-bg, #f6f6f8)"/>`;
        break;
      default:
        break;
    }
    const size = o.size || 18;
    return `<svg class="mb-glyph" width="${size}" height="${size}" viewBox="0 0 20 18" aria-hidden="true">${glyph}${badge}</svg>`;
  }

  window.AIU_ICON = icon;
  window.AIU_MENU_GLYPH = menuGlyph;
})();
