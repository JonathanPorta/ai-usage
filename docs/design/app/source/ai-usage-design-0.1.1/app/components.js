/* ai-usage — reusable view components (checkpoint 0.1.1).
   Pure render functions: Snapshot in, HTML string out. No sample data lives here.
   Charts register their data points so keyboard and pointer readouts can show
   exact dates and values (bindCharts). mountPopover adds navigation. */
(function () {
  const I = (...a) => window.AIU_ICON(...a);
  const F = window.AIU_FIXTURES;
  const MIN = 60000, HOUR = 3600000, DAY = 86400000;
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const MO = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  /* ------------------------------------------------------------ formatting */
  const fmt = {
    clock(t) { const d = new Date(t); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; },
    day(t) { const d = new Date(t); return `${d.getDate()} ${MO[d.getMonth()]}`; },
    wday(t) { const d = new Date(t); return `${WD[d.getDay()]} ${d.getDate()} ${MO[d.getMonth()]}`; },
    stamp(t, now) {
      const d = new Date(t), n = new Date(now);
      if (d.toDateString() === n.toDateString()) return fmt.clock(t);
      if (d.toDateString() === new Date(now - DAY).toDateString()) return `yesterday ${fmt.clock(t)}`;
      return `${fmt.wday(t)}, ${fmt.clock(t)}`;
    },
    ago(t, now) {
      if (t == null) return null;
      const m = Math.round((now - t) / MIN);
      if (m < 1) return 'just now';
      if (m < 60) return `${m} min ago`;
      const h = Math.floor(m / 60), r = m % 60;
      if (h < 6) return r ? `${h} h ${r} min ago` : `${h} h ago`;
      if (h < 48) return `${h} h ago`;
      return `${Math.floor(h / 24)} days ago`;
    },
    until(t, now) {
      if (t == null) return null;
      const m = Math.round((t - now) / MIN);
      if (m <= 0) return 'now';
      if (m < 60) return `in ${m} min`;
      const h = Math.floor(m / 60), r = m % 60;
      if (h < 24) return r ? `in ${h} h ${r} min` : `in ${h} h`;
      return `${WD[new Date(t).getDay()]} ${fmt.clock(t)}`;
    },
    tokens(n) {
      if (n == null) return null;
      if (n === 0) return '0';
      if (n < 1000) return String(n);
      if (n < 1e6) return `${n < 10000 ? (n / 1000).toFixed(1) : Math.round(n / 1000)}K`;
      return `${(n / 1e6).toFixed(n < 1e7 ? 2 : 1)}M`;
    },
    usd(n) { return n == null ? null : `$${n.toFixed(2).replace(/\.00$/, '')}`; },
    na(label) { return `<span class="na">${esc(label || 'Not reported')}</span>`; }
  };
  const dayAt = (now, i) => { const d = new Date(now); return new Date(d.getFullYear(), d.getMonth(), d.getDate() - i).getTime(); };

  /* ------------------------------------------------------------ derivations */
  const byId = (snap, id) => snap.providers.find((p) => p.id === id);
  const active = (snap) => snap.providers.filter((p) => p.enabled);
  const ioOf = (d) => (d && d.input != null ? d.input + d.output : null);
  const failureFor = (snap, pid) => snap.collection.failures.filter((f) => f.providerId === pid);
  const hasReadings = (p) => p.quota.windows.some((w) => w.readings.length);

  function windowState(w) {
    const remaining = w.basis === 'remaining' ? w.percent : 100 - w.percent;
    return { remaining, limit: remaining <= 0, low: remaining > 0 && remaining < 20 };
  }

  /* Every window is evaluated on its own freshness. A current, exhausted
     weekly window is flagged even when the 5-hour window is stale or has room.
     Usage limits, stale readings, and collector failures stay separate flags. */
  const readWindows = (p) => p.quota.windows.filter((w) => w.status && w.status !== 'none');
  function providerFlags(snap, p) {
    const f = [];
    const pr = snap.collection.progress;
    if (snap.collection.activity === 'collecting' && pr && pr.current === p.id) f.push({ k: 'checking', tone: 'info', icon: 'loader', text: 'Checking', spin: true });
    const fails = failureFor(snap, p.id);
    if (p.setup === 'auth_required') f.push({ k: 'auth', tone: 'bad', icon: 'lock', text: 'Sign-in needed' });
    else if (fails.length) f.push({ k: 'failed', tone: 'bad', icon: 'circle-x', text: fails.every((x) => x.kind === 'quota') ? 'Quota check failed' : 'Check failed' });
    if (p.setup === 'not_configured') f.push({ k: 'setup', tone: 'muted', icon: 'plug', text: 'Not set up' });
    if (p.setup === 'waiting') f.push({ k: 'waiting', tone: 'muted', icon: 'hourglass', text: 'Waiting' });
    const ws = readWindows(p);
    ws.forEach((w) => {
      if (w.status !== 'current') return;
      const st = windowState(w);
      if (st.limit) f.push({ k: 'limit', tone: 'warn', icon: 'gauge', text: `${w.short} limit reached` });
      else if (st.low) f.push({ k: 'low', tone: 'warn', icon: 'gauge', text: `${w.short} low` });
    });
    const staleW = ws.filter((w) => w.status === 'stale' && !['auth', 'failed'].includes(w.staleCause));
    if (staleW.length && staleW.length === ws.length && ws.length > 1) f.push({ k: 'stale', tone: 'warn', icon: 'clock-alert', text: 'Quota stale' });
    else staleW.forEach((w) => f.push({ k: 'stale', tone: 'warn', icon: 'clock-alert', text: ws.length > 1 ? `${w.short} stale` : 'Quota stale' }));
    return f;
  }

  /* Why one window's reading isn't current. */
  function windowStaleText(p, w, now) {
    const when = w.measuredAt ? fmt.ago(w.measuredAt, now) : 'a while ago';
    switch (w.staleCause) {
      case 'auth': return `Sign-in required. Showing the last reading, from ${when}.`;
      case 'failed': return `The latest check failed. Showing the reading from ${when}.`;
      case 'not_collected': return `No check has run since this reading (${when}).`;
      case 'source_old': return `${w.omittedNote ? `${w.omittedNote} ` : ''}The latest reading is from ${when}, so it isn’t shown as current capacity.`;
      case 'reset_passed': return `This window reset at ${fmt.clock(w.resetsAt)}, after the reading was taken.`;
      default: return `Last reading ${when}.`;
    }
  }
  /* Provider-level summary; never replaces per-window states. */
  function staleText(p, now) {
    const ws = readWindows(p).filter((w) => w.status === 'stale');
    if (!ws.length) return '';
    if (ws.length === 1 && readWindows(p).length > 1) return `${ws[0].label}: ${windowStaleText(p, ws[0], now)}`;
    return windowStaleText(p, ws[0], now);
  }

  function overallStatus(snap) {
    const c = snap.collection, now = snap.now;
    const last = c.lastAttemptAt ? `Checked ${fmt.ago(c.lastAttemptAt, now)}` : 'No checks yet';
    const problems = c.failures.length;
    if (c.activity === 'collecting') {
      const pr = c.progress || { done: 0, total: 4 };
      const cur = pr.current ? byId(snap, pr.current) : null;
      return { tone: 'info', icon: 'loader', spin: true, menu: 'collecting', title: 'Checking providers…', sub: `${cur ? `Reading ${cur.name}` : 'Starting'} · ${Math.min(pr.done + 1, pr.total)} of ${pr.total}` };
    }
    if (snap.service.state === 'stopped') return { tone: 'bad', icon: 'circle-stop', menu: 'stopped', title: 'Collector stopped', sub: `${last} · no checks scheduled` };
    if (c.lastAttemptResult === 'failed') return { tone: 'bad', icon: 'circle-x', menu: 'attention', title: 'Last check failed', sub: last };
    if (c.lastAttemptResult === 'partial' || problems) return { tone: 'warn', icon: 'circle-alert', menu: 'attention', title: 'Monitoring running', sub: `${last} · ${problems} ${problems === 1 ? 'problem' : 'problems'}` };
    if (snap.schedule.state === 'paused') return { tone: 'muted', icon: 'circle-pause', menu: 'paused', title: 'Scheduled checks paused', sub: `${last} · Collect now still works` };
    if (!c.lastAttemptAt) return { tone: 'info', icon: 'hourglass', menu: 'normal', title: 'Getting ready', sub: `First check ${fmt.until(c.nextScheduledAt, now)}` };
    return { tone: 'ok', quiet: true, icon: 'circle-check', menu: 'normal', title: 'Monitoring', sub: `${last} · next ${fmt.until(c.nextScheduledAt, now)}` };
  }

  /* Prominent notices only for conditions someone can act on or must know. */
  function notices(snap) {
    const out = [];
    const now = snap.now;
    if (snap.service.state === 'stopped') out.push({ tone: 'bad', icon: 'circle-stop', title: 'The background collector isn’t running.', body: `Values below are from ${fmt.ago(snap.collection.lastSuccessAt, now)}.`, action: { id: 'open-monitor', label: 'Start…' } });
    snap.collection.failures.forEach((f) => {
      const p = f.providerId ? byId(snap, f.providerId) : null;
      if (f.action === 'signin') out.push({ tone: 'bad', icon: 'lock', title: `${p.name} needs you to sign in.`, body: 'Usage is still collected. Quota can’t be refreshed.', action: { id: `open:${p.id}`, label: 'Details' } });
      else out.push({ tone: 'bad', icon: 'circle-x', title: p ? (f.kind === 'quota' ? `${p.name} quota couldn’t be read.` : `${p.name} couldn’t be checked.`) : 'The last check failed.', body: p ? (f.kind === 'quota' ? 'Its usage was updated; quota keeps the previous reading.' : 'It keeps its previous values. Other providers were updated.') : f.message, action: { id: 'open-monitor', label: 'Details' } });
    });
    active(snap).forEach((p) => {
      readWindows(p).filter((w) => w.status === 'stale' && w.staleCause === 'source_old').forEach((w) => {
        const others = readWindows(p).filter((x) => x !== w && x.status === 'current');
        const title = readWindows(p).length > 1 ? `${p.name} ${w.label.toLowerCase()} reading is out of date.` : `${p.name} quota is out of date.`;
        const body = others.length ? `The ${others.map((x) => x.label.toLowerCase()).join(' and ')} reading is current.` : p.usage.status === 'current' ? 'Usage collection is still working.' : windowStaleText(p, w, now);
        out.push({ tone: 'warn', icon: 'clock-alert', title, body, action: { id: `open:${p.id}`, label: 'Details' } });
      });
      if (p.sources.some((s) => s.status === 'stale' && s.role === 'history')) out.push({ tone: 'warn', icon: 'clock-alert', title: `${p.name} daily history stopped updating.`, body: 'Today’s usage is still current.', action: { id: `open:${p.id}`, label: 'Details' } });
    });
    return out;
  }

  /* ------------------------------------------------------------ small parts */
  const pill = (f) => `<span class="pill ${f.tone}">${I(f.icon, f.spin ? 'spin' : '')}${esc(f.text)}</span>`;
  const statusGlyph = (tone, icon, spin) => `<span class="status-glyph tone-${tone}">${I(icon, spin ? 'spin' : '')}</span>`;
  function meter(w, stale) {
    const ws = windowState(w);
    const cls = stale ? 'stale' : (ws.limit || ws.low ? 'warn' : '');
    return `<span class="meter ${cls} ${w.percent === 0 ? 'empty' : ''}" aria-hidden="true"><span style="width:${Math.max(0, Math.min(100, w.percent))}%"></span></span>`;
  }
  function resetCaption(w, now, stale) {
    if (w.resetPassed) return `Reset due ${fmt.clock(w.resetsAt)} has passed`;
    if (w.resetsAt == null) return 'Window starts on next use';
    const ws = windowState(w);
    if (ws.limit && !stale) return `Available ${fmt.until(w.resetsAt, now)}`;
    const u = fmt.until(w.resetsAt, now);
    return /^in /.test(u) ? `Resets ${u}` : `Resets ${u}`;
  }

  /* ------------------------------------------------------------ chart registry */
  const CHARTS = {};
  let seq = 0;
  function chartBox(svg, points, o) {
    const id = `ch${++seq}`;
    CHARTS[id] = { points, def: o.readout, y1: o.y1, y2: o.y2 };
    return `<div class="chartx ${o.cls || ''}" tabindex="0" role="group" aria-roledescription="chart" data-chart="${id}" aria-label="${esc(o.label)}. Use the left and right arrow keys to read each value." aria-describedby="${id}-ro">${svg}</div><p class="chart-ro num" id="${id}-ro" aria-live="polite">${esc(o.readout)}</p>`;
  }
  function bindCharts(root) {
    if (root.__aiuCharts) return;
    root.__aiuCharts = true;
    const show = (el, i) => {
      const c = CHARTS[el.dataset.chart]; if (!c || !c.points.length) return;
      i = Math.max(0, Math.min(c.points.length - 1, i));
      el.dataset.idx = i;
      const pt = c.points[i];
      const cur = el.querySelector('.cursor');
      if (cur) { cur.setAttribute('x1', pt.x); cur.setAttribute('x2', pt.x); cur.setAttribute('visibility', 'visible'); }
      const ro = root.querySelector(`#${el.dataset.chart}-ro`) || document.getElementById(`${el.dataset.chart}-ro`);
      if (ro) ro.textContent = pt.text;
    };
    const hide = (el) => {
      const c = CHARTS[el.dataset.chart]; if (!c) return;
      const cur = el.querySelector('.cursor'); if (cur) cur.setAttribute('visibility', 'hidden');
      const ro = document.getElementById(`${el.dataset.chart}-ro`); if (ro) ro.textContent = c.def;
    };
    root.addEventListener('keydown', (e) => {
      const el = e.target.closest && e.target.closest('.chartx'); if (!el) return;
      const c = CHARTS[el.dataset.chart]; if (!c) return;
      const i = el.dataset.idx != null ? Number(el.dataset.idx) : c.points.length - 1;
      const map = { ArrowLeft: i - 1, ArrowRight: i + 1, Home: 0, End: c.points.length - 1 };
      if (e.key in map) { e.preventDefault(); e.stopPropagation(); show(el, map[e.key]); }
    });
    root.addEventListener('focusin', (e) => { const el = e.target.closest && e.target.closest('.chartx'); if (el) show(el, el.dataset.idx != null ? Number(el.dataset.idx) : CHARTS[el.dataset.chart].points.length - 1); });
    root.addEventListener('focusout', (e) => { const el = e.target.closest && e.target.closest('.chartx'); if (el) hide(el); });
    root.addEventListener('pointermove', (e) => {
      const el = e.target.closest && e.target.closest('.chartx'); if (!el) return;
      const c = CHARTS[el.dataset.chart]; const svg = el.querySelector('svg'); if (!c || !svg || !c.points.length) return;
      const r = svg.getBoundingClientRect(); const vb = svg.viewBox.baseVal;
      const x = ((e.clientX - r.left) / r.width) * vb.width;
      let best = 0; c.points.forEach((p, i) => { if (Math.abs(p.x - x) < Math.abs(c.points[best].x - x)) best = i; });
      show(el, best);
    });
    root.addEventListener('pointerout', (e) => {
      const el = e.target.closest && e.target.closest('.chartx');
      if (el && !el.contains(e.relatedTarget) && document.activeElement !== el) hide(el);
    });
  }

  /* ------------------------------------------------------------ bar charts */
  function daySlots(p, now, n) {
    const slots = [];
    for (let i = n - 1; i >= 0; i--) {
      const date = dayAt(now, i);
      const rec = p.days.find((d) => d.date === date);
      let kind = 'none';
      if (rec) kind = rec.state === 'missing' ? 'missing' : (rec.partial ? 'today' : (ioOf(rec) === 0 ? 'zero' : 'measured'));
      else if (p.coverageStart != null && date >= p.coverageStart) kind = 'missing';
      slots.push({ date, rec, kind });
    }
    return slots;
  }
  function slotText(s, p, now) {
    const d = fmt.wday(s.date);
    if (s.kind === 'none') return `${d}: not collected (before ${p.name} data began)`;
    if (s.kind === 'missing') return `${d}: no data — missing reading, not zero`;
    const r = s.rec;
    const parts = `${fmt.tokens(r.input)} input, ${fmt.tokens(r.output)} output`;
    if (s.kind === 'zero') return `${d}: 0 tokens — measured, no usage`;
    if (s.kind === 'today') return `Today so far (as of ${fmt.clock(p.usage.measuredAt || now)}): ${fmt.tokens(ioOf(r))} · ${parts}`;
    return `${d}: ${fmt.tokens(ioOf(r))} · ${parts}`;
  }
  function niceTop(max) {
    if (max <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(max)));
    const n = max / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }

  function miniChart(p, now) {
    const slots = daySlots(p, now, 7);
    const measuredDays = p.days.filter((d) => d.state === 'measured').length;
    if (measuredDays < 2) return `<div class="mini-empty">${I('chart', 'icon-sm')}<span>Not enough history for a 7-day chart yet</span></div>`;
    const W = 196, H = 46, top = 4, base = 44, bw = W / 7;
    const max = Math.max(1, ...slots.map((s) => (s.rec && s.rec.state === 'measured' ? ioOf(s.rec) : 0)));
    const peak = slots.reduce((m, s) => (s.rec && s.rec.state === 'measured' && ioOf(s.rec) === max ? s : m), null);
    let g = `<line class="base" x1="0" x2="${W}" y1="${base + .5}" y2="${base + .5}"/>`;
    const pts = [];
    slots.forEach((s, i) => {
      const x = i * bw + bw * 0.2, w = bw * 0.6, cx = i * bw + bw / 2;
      pts.push({ x: cx, text: slotText(s, p, now) });
      if (s.kind === 'missing') g += `<rect class="bar-gap" x="${x}" y="${base - 12}" width="${w}" height="12" rx="2"/>`;
      else if (s.kind === 'none') g += `<line class="bar-none" x1="${x}" x2="${x + w}" y1="${base - 1}" y2="${base - 1}"/>`;
      else if (s.kind === 'zero') g += `<rect class="bar-zero" x="${x}" y="${base - 2}" width="${w}" height="2"/>`;
      else {
        const h = Math.max(2, (ioOf(s.rec) / max) * (base - top));
        g += `<rect class="${s.kind === 'today' ? 'bar-today' : 'bar-in'}" x="${x}" y="${base - h}" width="${w}" height="${h}" rx="2"/>`;
      }
    });
    g += `<line class="cursor" x1="0" x2="0" y1="${top - 2}" y2="${base}" visibility="hidden"/>`;
    // Axis text is HTML at the caption size (never scaled below 11px): first day and "Today" only.
    const svg = `<svg class="chart mini" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${g}</svg><div class="mini-axis" aria-hidden="true"><span>${fmt.day(slots[0].date)}</span><span class="today-lbl">Today</span></div>`;
    const def = `7 days · input + output · peak ${fmt.tokens(max)}${peak ? ` ${WD[new Date(peak.date).getDay()]}` : ''}`;
    return chartBox(svg, pts, { cls: 'mini-wrap', label: `${p.name}, input plus output tokens per day, last 7 days; today is incomplete`, readout: def });
  }

  function barChartRange(p, now, days, o) {
    const opt = Object.assign({ w: 372, h: 150, l: 38, r: 6, t: 10, b: 22 }, o || {});
    const slots = daySlots(p, now, days);
    const innerW = opt.w - opt.l - opt.r, base = opt.h - opt.b, bw = innerW / days;
    const max = Math.max(1, ...slots.map((s) => (s.rec && s.rec.state === 'measured' ? ioOf(s.rec) : 0)));
    const topV = niceTop(max);
    const y = (v) => base - (v / topV) * (base - opt.t);
    let g = '';
    [0, topV / 2, topV].forEach((v) => { g += `<line class="grid" x1="${opt.l}" x2="${opt.w - opt.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${opt.l - 5}" y="${y(v) + 3}" text-anchor="end">${fmt.tokens(v)}</text>`; });
    const firstCovered = slots.findIndex((s) => s.kind !== 'none');
    if (firstCovered > 0) {
      const wNone = firstCovered * bw;
      g += `<rect class="no-cover" x="${opt.l}" y="${opt.t}" width="${wNone}" height="${base - opt.t}"/>`;
      if (wNone > 70) g += `<text x="${opt.l + wNone / 2}" y="${opt.t + 14}" text-anchor="middle" class="cover-lbl">Not collected</text>`;
    }
    const pts = [];
    slots.forEach((s, i) => {
      const x = opt.l + i * bw + bw * 0.15, w = Math.max(1, bw * 0.7), cx = opt.l + i * bw + bw / 2;
      pts.push({ x: cx, text: slotText(s, p, now) });
      if (s.kind === 'missing') g += `<rect class="bar-gap" x="${x}" y="${base - 14}" width="${w}" height="14" rx="1.5"/>`;
      else if (s.kind === 'zero') g += `<rect class="bar-zero" x="${x}" y="${base - 2}" width="${w}" height="2"/>`;
      else if (s.kind === 'measured' || s.kind === 'today') {
        const r = s.rec;
        const hin = base - y(r.input), hout = base - y(r.output);
        if (s.kind === 'today') g += `<rect class="bar-today" x="${x}" y="${y(r.input + r.output)}" width="${w}" height="${Math.max(2, hin + hout)}" rx="1.5"/>`;
        else g += `<rect class="bar-in" x="${x}" y="${y(r.input)}" width="${w}" height="${Math.max(0, hin)}" rx="1"/><rect class="bar-out" x="${x}" y="${y(r.input + r.output)}" width="${w}" height="${Math.max(0, hout)}" rx="1"/>`;
      }
    });
    const tickIdx = days <= 7 ? [0, 3, days - 1] : days <= 31 ? [0, Math.floor(days / 2), days - 1] : [0, Math.floor(days / 3), Math.floor((2 * days) / 3), days - 1];
    tickIdx.forEach((i) => {
      const cx = opt.l + i * bw + bw / 2;
      const anchor = i === 0 ? 'start' : i === days - 1 ? 'end' : 'middle';
      const x = i === 0 ? opt.l : i === days - 1 ? opt.w - opt.r : cx;
      g += `<text x="${x}" y="${opt.h - 6}" text-anchor="${anchor}">${fmt.day(slots[i].date)}</text>`;
    });
    g += `<line class="cursor" x1="0" x2="0" y1="${opt.t}" y2="${base}" visibility="hidden"/>`;
    const measured = slots.filter((s) => s.kind === 'measured' || s.kind === 'today' || s.kind === 'zero');
    const total = measured.reduce((a, s) => a + ioOf(s.rec), 0);
    const miss = slots.filter((s) => s.kind === 'missing').length, none = slots.filter((s) => s.kind === 'none').length;
    const def = `${days} days to ${fmt.day(now)} · ${fmt.tokens(total)} input + output on ${measured.length} measured days${miss ? ` · ${miss} missing` : ''}${none ? ` · ${none} before collection began` : ''}`;
    const svg = `<svg class="chart" viewBox="0 0 ${opt.w} ${opt.h}" aria-hidden="true">${g}</svg>`;
    return { html: chartBox(svg, pts, { label: `${p.name} daily input plus output tokens, ${days} days ending ${fmt.day(now)}`, readout: def }), total, measured: measured.length, missing: miss, none, slots };
  }

  /* ------------------------------------------------------------ quota line chart */
  function quotaChart(p, w, now, rangeMs, o) {
    const opt = Object.assign({ w: 372, h: 150, l: 34, r: 8, t: 12, b: 22 }, o || {});
    const start = now - rangeMs;
    const innerW = opt.w - opt.l - opt.r, base = opt.h - opt.b;
    const x = (t) => opt.l + ((t - start) / rangeMs) * innerW;
    const y = (v) => base - (v / 100) * (base - opt.t);
    const stale = w.status === 'stale';
    const inRange = w.readings.filter((r) => r.t >= start && r.t <= now);
    const prev = w.readings.filter((r) => r.t < start).slice(-1)[0];
    let g = '';
    [0, 50, 100].forEach((v) => { g += `<line class="grid" x1="${opt.l}" x2="${opt.w - opt.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${opt.l - 5}" y="${y(v) + 3}" text-anchor="end">${v}%</text>`; });
    const firstT = w.readings.length ? w.readings[0].t : null;
    if (firstT == null || firstT > start + rangeMs * 0.02) {
      const until = firstT == null ? now : Math.min(firstT, now);
      const wNone = x(until) - opt.l;
      if (wNone > 1) {
        g += `<rect class="no-cover" x="${opt.l}" y="${opt.t}" width="${wNone}" height="${base - opt.t}"/>`;
        if (wNone > 80) g += `<text x="${opt.l + wNone / 2}" y="${opt.t + 14}" text-anchor="middle" class="cover-lbl">${firstT == null ? 'No readings' : `No readings kept before ${fmt.day(firstT)}`}</text>`;
      }
    }
    const resets = w.resets.filter((r) => r >= start && r <= now);
    resets.forEach((r) => { g += `<line class="reset" x1="${x(r)}" x2="${x(r)}" y1="${opt.t}" y2="${base}"/>`; });
    // Only the latest reset is labelled, and only when there is room; the legend and readout cover the rest.
    const lastReset = resets[resets.length - 1];
    if (lastReset != null && x(lastReset) > opt.l + 36 && x(lastReset) < opt.w - opt.r - 46) g += `<text class="reset-lbl" x="${x(lastReset) + 3}" y="${opt.t + 10}">reset</text>`;
    // Line segments: break on resets (value jumps) and on gaps longer than 3× the usual interval.
    const pts = [];
    const series = (prev ? [prev] : []).concat(inRange);
    const maxGap = p.quota.mode === 'event' ? Infinity : (w.id === 'weekly' || w.id === 'credits' ? 7 * HOUR : 2.5 * HOUR);
    let d = '';
    series.forEach((r, i) => {
      const xr = Math.max(opt.l, x(r.t));
      const prevR = series[i - 1];
      const broken = !prevR || r.t - prevR.t > maxGap || resets.some((z) => z > prevR.t && z <= r.t);
      if (p.quota.mode === 'event' && prevR && !broken) d += `L${xr.toFixed(1)} ${y(prevR.percent).toFixed(1)} `;
      d += `${broken ? 'M' : 'L'}${xr.toFixed(1)} ${y(r.percent).toFixed(1)} `;
      const note = prevR && r.t - prevR.t > maxGap ? ' · after a gap in readings' : prevR && resets.some((z) => z > prevR.t && z <= r.t) ? ' · after a reset' : '';
      if (r.t >= start) pts.push({ x: xr, text: `${fmt.wday(r.t)} ${fmt.clock(r.t)}: ${r.percent}% ${w.basis}${r.resetsAt ? ` · resets ${fmt.stamp(r.resetsAt, now)}` : ' · no active window'}${note}` });
    });
    g += `<path class="line" d="${d}"/>`;
    if (p.quota.mode === 'event') inRange.forEach((r) => { g += `<circle class="dot" cx="${x(r.t)}" cy="${y(r.percent)}" r="2"/>`; });
    const lastR = w.readings[w.readings.length - 1];
    if (lastR && lastR.t >= start) {
      g += `<circle class="dot-last" cx="${x(lastR.t)}" cy="${y(lastR.percent)}" r="3"/>`;
      if (stale) {
        g += `<path class="line-stale" d="M${x(lastR.t)} ${y(lastR.percent)} L${opt.w - opt.r} ${y(lastR.percent)}"/>`;
        g += `<text class="stale-lbl" x="${opt.w - opt.r}" y="${y(lastR.percent) + (lastR.percent > 70 ? 16 : -7)}" text-anchor="end">No reading since ${fmt.stamp(lastR.t, now)}</text>`;
      }
    } else if (lastR && stale) {
      g += `<path class="line-stale" d="M${opt.l} ${y(lastR.percent)} L${opt.w - opt.r} ${y(lastR.percent)}"/><text class="stale-lbl" x="${opt.w - opt.r}" y="${y(lastR.percent) - 6}" text-anchor="end">Last reading ${fmt.stamp(lastR.t, now)} is before this range</text>`;
    }
    const labelT = rangeMs <= DAY ? (t) => fmt.clock(t) : (t) => fmt.day(t);
    g += `<text x="${opt.l}" y="${opt.h - 6}">${labelT(start)}</text><text x="${(opt.l + opt.w - opt.r) / 2}" y="${opt.h - 6}" text-anchor="middle">${labelT(start + rangeMs / 2)}</text><text x="${opt.w - opt.r}" y="${opt.h - 6}" text-anchor="end">${rangeMs <= DAY ? `${fmt.clock(now)} now` : `${fmt.day(now)} now`}</text>`;
    g += `<line class="cursor" x1="0" x2="0" y1="${opt.t}" y2="${base}" visibility="hidden"/>`;
    const def = lastR ? `${w.label}, % ${w.basis} · last reading ${fmt.stamp(lastR.t, now)}: ${lastR.percent}% ${w.basis}${resets.length ? ` · ${resets.length} reset${resets.length > 1 ? 's' : ''} in range` : ''}` : `${w.label}: no readings yet`;
    const svg = `<svg class="chart" viewBox="0 0 ${opt.w} ${opt.h}" aria-hidden="true">${g}</svg>`;
    return { html: chartBox(svg, pts, { label: `${p.name} ${w.label}, percent ${w.basis} at each reading`, readout: def }), inRange, resets, lastR };
  }

  /* ------------------------------------------------------------ overview card */
  function quotaCells(snap, p) {
    const now = snap.now;
    const ws = p.quota.windows.filter((w) => w.percent != null);
    return `<div class="qgrid" style="--cols:${Math.min(2, ws.length)}">${ws.map((w) => {
      const stale = w.status === 'stale';
      const st = windowState(w);
      const warn = !stale && (st.limit || st.low);
      return `<div class="qcell ${stale ? 'is-stale' : ''}">
        <span class="q-label">${esc(w.label)}</span>
        <span class="q-val"><span class="q-num ${warn ? 'tone-warn' : ''}">${w.percent}%</span><span class="q-basis">${w.basis}</span>${warn ? I('gauge', 'icon-sm tone-warn') : ''}</span>
        ${meter(w, stale)}
        <span class="q-reset">${esc(resetCaption(w, now, stale))}</span>
        ${stale ? `<span class="q-fresh tone-warn">${I('clock-alert', 'icon-sm')}${esc(w.staleCause === 'failed' ? `Check failed · reading ${fmt.ago(w.measuredAt, now)}` : w.staleCause === 'auth' ? `Sign-in needed · ${fmt.ago(w.measuredAt, now)}` : `Reading ${fmt.ago(w.measuredAt, now)}`)}</span>` : ''}
      </div>`;
    }).join('')}</div>`;
  }

  function providerCard(snap, p) {
    const now = snap.now;
    const flags = providerFlags(snap, p);
    const checking = flags.some((f) => f.k === 'checking');
    const label = [p.name, ...flags.map((f) => f.text)];
    const topFor = (extra) => `<div class="card-top"><button type="button" class="card-link" data-open="${p.id}" aria-label="${esc(extra)}">${esc(p.name)}</button><span class="card-pills">${flags.map(pill).join('')}</span>${I('chevron-right', 'chev icon-sm')}</div>`;

    if (p.setup === 'not_configured') return `<article class="card compact">${topFor(`${p.name}: not set up. Open details.`)}<p class="c-note">Turn on usage telemetry to track ${esc(p.name)}.</p></article>`;
    if (p.setup === 'waiting') return `<article class="card compact ${checking ? 'checking' : ''}">${topFor(`${p.name}: waiting for first check. Open details.`)}<p class="c-note">No measurements yet — appears after the first check.</p></article>`;

    let quota = '';
    const qs = p.quota.status;
    if (['current', 'stale', 'mixed'].includes(qs) && p.quota.windows.some((w) => w.percent != null)) {
      quota = quotaCells(snap, p);
      p.quota.windows.forEach((w) => { if (w.percent != null) label.push(`${w.label} ${w.percent}% ${w.basis}${w.status === 'stale' ? `, reading ${fmt.ago(w.measuredAt, now)}` : ''}`); });
    }
    const io = p.today ? ioOf(p.today) : null;
    label.push(io == null ? 'no usage yet' : `${fmt.tokens(io)} tokens today so far`);
    const usage = `<div class="urow">
      <div class="u-total"><span class="u-num ${p.usage.status === 'stale' ? 'dim' : ''}">${io == null ? '—' : fmt.tokens(io)}</span><span class="u-cap">${io == null ? 'no usage yet' : 'today so far'}</span><span class="u-cap">input + output</span></div>
      <div class="u-chart">${miniChart(p, now)}</div>
    </div>`;

    const fresh = [];
    if (qs === 'unsupported') fresh.push(`<span>${I('minus')}Quota not available</span>`);
    else if (qs === 'unavailable') fresh.push(`<span>${I('minus')}No quota reading</span>`);
    else if (qs === 'mixed') fresh.push(`<span class="warn">${I('clock-alert')}Quota freshness mixed</span>`);
    // Each quota cell already carries its own staleness; this line covers usage.
    if (p.usage.status === 'stale') fresh.push(`<span class="warn">${I('clock-alert')}${p.usage.staleCause === 'failed' ? 'Usage check failed · from ' : 'Usage from '}${fmt.ago(p.usage.measuredAt, now)}</span>`);
    else if (p.usage.status === 'current') {
      const qm = qs === 'current' ? p.quota.measuredAt : null;
      if (qm && Math.abs(qm - p.usage.measuredAt) < 10 * MIN) fresh.push(`<span>Measured ${fmt.ago(Math.max(qm, p.usage.measuredAt), now)}</span>`);
      else {
        if (qm) fresh.push(`<span>Quota measured ${fmt.ago(qm, now)}</span>`);
        fresh.push(`<span>${qs === 'stale' || qs === 'mixed' ? `${I('circle-check', 'tone-ok')}Usage current · ` : 'Usage measured '}${fmt.ago(p.usage.measuredAt, now)}</span>`);
      }
    }
    if (p.sources.some((s) => s.status === 'stale' && s.role === 'history')) fresh.push(`<span class="warn">${I('clock-alert')}Daily history behind</span>`);

    return `<article class="card ${checking ? 'checking' : ''}">${topFor(`${label.join('. ')}. Open details.`)}${quota}${usage}${fresh.length ? `<p class="fresh">${fresh.join('')}</p>` : ''}</article>`;
  }

  function overview(snap, ui) {
    const u = ui || {};
    const st = overallStatus(snap);
    const ns = notices(snap);
    const collecting = snap.collection.activity === 'collecting';
    const off = snap.providers.filter((p) => !p.enabled);
    const result = u.result ? `<div class="notice ${u.result.tone}" role="status">${I(u.result.icon)}<span class="n-body"><span class="n-title">${esc(u.result.title)}</span><span>${esc(u.result.body)}</span></span></div>` : '';
    const n = !u.result && ns[0] ? ns[0] : null;
    const notice = n ? `<div class="notice ${n.tone}">${I(n.icon)}<span class="n-body"><span class="n-title">${esc(n.title)}</span><span>${esc(n.body)}${ns.length > 1 ? ` <strong>+${ns.length - 1} more in Monitoring.</strong>` : ''}</span></span>${n.action ? `<button type="button" class="n-action" data-act="${n.action.id}">${esc(n.action.label)}</button>` : ''}</div>` : '';
    const statusRow = st.quiet
      ? `<button type="button" class="status-row quiet" data-open="monitor" aria-label="${esc(`${st.title}: ${st.sub}. Open monitoring details.`)}">${I('circle-check', 'tone-ok icon-sm')}<span class="sr-text"><span class="sr-sub num"><strong>${esc(st.title)}</strong> · ${esc(st.sub)}</span></span>${I('chevron-right', 'chev icon-sm')}</button>`
      : `<button type="button" class="status-row" data-open="monitor" aria-label="${esc(`${st.title}. ${st.sub}. Open monitoring details.`)}">${statusGlyph(st.tone, st.icon, st.spin)}<span class="sr-text"><span class="sr-title">${esc(st.title)}</span><span class="sr-sub num">${esc(st.sub)}</span></span>${I('chevron-right', 'chev icon-sm')}</button>`;
    return {
      top: `<div class="pop-head"><div><h1>AI Usage</h1><p>Your AI tools, at a glance</p></div><span class="app-mark">${I('activity', 'icon-lg')}</span></div>${statusRow}<div class="live" aria-live="polite">${result}${notice}</div>`,
      scroll: `<div class="cards">${active(snap).map((p) => providerCard(snap, p)).join('')}</div>${off.length ? `<p class="caption" style="margin:8px 2px 0">${off.map((p) => esc(p.name)).join(', ')} turned off in Settings.</p>` : ''}`,
      foot: `<button type="button" class="tbtn primary" data-act="collect" ${collecting ? 'disabled' : ''}>${I('refresh', collecting ? 'spin' : '')}${collecting ? 'Checking…' : 'Collect now'}</button><span class="grow"></span><button type="button" class="tbtn" data-act="history">${I('chart')}Open history</button><button type="button" class="tbtn" data-open="settings" aria-label="Settings">${I('settings')}</button>`
    };
  }

  /* ------------------------------------------------------------ provider detail */
  const SRC_STATUS = {
    ok: { tone: 'ok', icon: 'circle-check', text: 'Working' },
    stale: { tone: 'warn', icon: 'clock-alert', text: 'Not updating' },
    failed: { tone: 'bad', icon: 'circle-x', text: 'Failed' },
    auth: { tone: 'bad', icon: 'lock', text: 'Sign-in needed' },
    off: { tone: 'muted', icon: 'plug', text: 'Off' },
    waiting: { tone: 'muted', icon: 'hourglass', text: 'Waiting for first check' }
  };
  function sourceRow(snap, s) {
    const st = SRC_STATUS[s.status] || SRC_STATUS.ok;
    const now = snap.now;
    const bits = [];
    if (s.lastReadAt) bits.push(`${s.status === 'failed' ? 'Last good read' : 'Read'} ${fmt.stamp(s.lastReadAt, now)}`);
    if (s.measuredAt) bits.push(`data from ${fmt.stamp(s.measuredAt, now)}`);
    return `<li>${I(st.icon, `tone-${st.tone}`)}<span class="s-body"><span class="s-name">${esc(s.label)} <span class="pill ${st.tone}" style="margin-left:4px">${esc(st.text)}</span></span><span class="s-meta">${esc(s.kind)}</span>${bits.length ? `<span class="s-meta num">${esc(bits.join(' · '))}</span>` : ''}${s.error ? `<span class="tone-bad">${esc(s.error)}</span>` : ''}${s.detail ? `<span class="s-meta">${esc(s.detail)}</span>` : ''}</span></li>`;
  }

  const RECORD_KIND = {
    period_total: 'Daily totals the provider can revise; the latest row per day is used.',
    delta: 'Token deltas summed within each day.',
    event_total: 'Per-event totals summed within each day.'
  };

  function defaultDetailState(p) {
    const w = p.quota.windows.find((x) => x.readings.length) || p.quota.windows[0];
    return { metric: 'tokens', trange: 7, win: w ? w.id : null, qrange: w ? w.ranges[0].id : null };
  }

  function chartPanel(snap, p, ds) {
    const now = snap.now;
    const quotaOK = hasReadings(p);
    const metric = quotaOK ? ds.metric : 'tokens';
    const seg = (items, cur, key) => `<span class="seg-s" role="group">${items.map(([v, l, dis]) => `<button type="button" data-dact="${key}:${v}" aria-pressed="${String(cur) === String(v)}" ${dis ? 'disabled' : ''}>${esc(l)}</button>`).join('')}</span>`;
    let controls = seg([['tokens', 'Tokens'], ['quota', 'Quota', !quotaOK]], metric, 'metric');
    let body = '', legend = '';
    if (metric === 'tokens') {
      controls += seg([[7, '7 days'], [30, '30 days']], ds.trange, 'trange');
      const c = barChartRange(p, now, ds.trange);
      body = c.html;
      legend = `<span><i class="lg-in"></i>Input</span><span><i class="lg-out"></i>Output</span><span><i class="lg-today"></i>Today so far</span>${c.missing ? '<span><i class="lg-gap"></i>Missing</span>' : ''}${c.slots.some((s) => s.kind === 'zero') ? '<span><i class="lg-zero"></i>Measured zero</span>' : ''}<span>Cache excluded</span>`;
    } else {
      const w = p.quota.windows.find((x) => x.id === ds.win) || p.quota.windows[0];
      const rng = w.ranges.find((r) => r.id === ds.qrange) || w.ranges[0];
      if (p.quota.windows.length > 1) controls += `<label class="win-sel"><span class="sr-only">Quota window</span><select data-dsel="win">${p.quota.windows.map((x) => `<option value="${x.id}" ${x.id === w.id ? 'selected' : ''}>${esc(x.label)}</option>`).join('')}</select></label>`;
      controls += seg(w.ranges.map((r) => [r.id, r.label]), rng.id, 'qrange');
      const c = quotaChart(p, w, now, rng.ms);
      body = c.html;
      legend = `<span><i class="lg-line"></i>% ${w.basis}</span><span><i class="lg-reset"></i>Reset</span>${w.status === 'stale' ? `<span><i class="lg-stale"></i>No new ${esc(w.short.toLowerCase())} reading</span>` : ''}${p.quota.mode === 'event' ? '<span>Readings arrive only while it runs</span>' : ''}`;
    }
    return `<section class="panel chart-panel" aria-label="Usage chart"><div class="cp-controls">${controls}</div>${body}<div class="legend">${legend}</div>${p.missingNote && metric === 'tokens' ? `<p class="caption tone-warn" style="margin:6px 0 0">${esc(p.missingNote)}</p>` : ''}${p.coverageNote && metric === 'tokens' && ds.trange > 30 ? `<p class="caption">${esc(p.coverageNote)}</p>` : ''}</section>`;
  }

  function providerDetail(snap, p, dsIn) {
    const now = snap.now;
    const ds = Object.assign(defaultDetailState(p), dsIn || {});
    const flags = providerFlags(snap, p).filter((f) => f.k !== 'checking');
    const fails = failureFor(snap, p.id);
    let banner = '';
    if (p.setup === 'auth_required') { const f = fails[0]; banner = `<div class="notice bad">${I('lock')}<span class="n-body"><span class="n-title">${esc(f ? f.message : 'Sign-in needed')}</span><span>${esc(f ? f.hint : '')}</span></span><button type="button" class="n-action" data-act="copy-cmd">Copy command</button></div>`; }
    else if (fails.length) banner = `<div class="notice bad">${I('circle-x')}<span class="n-body"><span class="n-title">${esc(fails[0].message)}</span><span>${esc(fails[0].hint)}</span></span><button type="button" class="n-action" data-act="collect">Try again</button></div>`;
    else {
      const staleW = readWindows(p).filter((w) => w.status === 'stale');
      if (staleW.length) banner = `<div class="notice warn">${I('clock-alert')}<span class="n-body"><span class="n-title">${staleW.length === readWindows(p).length ? 'Quota reading is out of date' : `${esc(staleW.map((w) => w.label).join(' and '))} reading is out of date`}</span>${staleW.map((w) => `<span>${esc(readWindows(p).length > 1 ? `${w.label}: ` : '')}${esc(windowStaleText(p, w, now))}</span>`).join('')}</span></div>`;
    }

    const head = `<div class="od-row-top"><div class="od-fill"><h2 tabindex="-1">${esc(p.name)}</h2><p class="sub">${esc(p.product)}</p></div><div class="od-cluster od-fixed" style="--od-gap:4px;justify-content:flex-end;max-width:55%">${flags.map(pill).join('')}</div></div>${banner ? `<div style="margin-top:12px">${banner}</div>` : ''}`;

    if (p.setup === 'not_configured') {
      return `${head}
        <div class="notice info" style="margin-top:16px">${I('plug')}<span class="n-body"><span class="n-title">Usage telemetry is off</span><span>${esc(p.setupNote)}</span></span></div>
        <div class="section-label">What you’d get</div>
        <div class="panel"><dl class="facts"><div><dt>Token usage</dt><dd>Input and output tokens per model</dd></div><div><dt>Quota</dt><dd>${fmt.na('Not available')}</dd></div><div><dt>Cost</dt><dd>${fmt.na('Not reported')}</dd></div></dl></div>
        <p class="caption" style="margin:12px 0 8px">Turning this on writes a telemetry block to Gemini CLI’s settings. Prompt logging stays off.</p>
        <button type="button" class="btn" data-open="settings">${I('settings')}Open Settings</button>
        <details class="disc"><summary>Source</summary><ul class="src-list">${p.sources.map((s) => sourceRow(snap, s)).join('')}</ul></details>`;
    }
    if (p.setup === 'waiting') {
      return `${head}<div class="notice info" style="margin-top:16px">${I('hourglass')}<span class="n-body"><span class="n-title">No measurements yet</span><span>This fills in after the first check. Empty here means “not measured”, not zero.</span></span></div>
        <details class="disc" open><summary>Sources</summary><ul class="src-list">${p.sources.map((s) => sourceRow(snap, s)).join('')}</ul></details>`;
    }

    // Quota windows
    let quota;
    if (['current', 'stale', 'mixed'].includes(p.quota.status) && p.quota.windows.some((w) => w.percent != null)) {
      quota = `<div class="panel">${p.quota.windows.filter((w) => w.percent != null).map((w) => {
        const stale = w.status === 'stale';
        const ws = windowState(w);
        return `<div class="quota-row"><div class="figure ${stale ? 'dim' : ''}"><span class="big">${w.percent}%</span><span class="unit">${w.basis}</span>${!stale && ws.limit ? pill({ tone: 'warn', icon: 'gauge', text: 'Limit reached' }) : !stale && ws.low ? pill({ tone: 'warn', icon: 'gauge', text: 'Low' }) : ''}<span class="window">${esc(w.label)}</span></div>${meter(w, stale)}<div class="q-meta"><span class="${w.resetPassed ? 'tone-warn' : ''}">${esc(resetCaption(w, now, stale))}${w.resetsAt && !w.resetPassed && w.resetsAt - now < DAY ? ` · ${fmt.clock(w.resetsAt)}` : ''}</span><span class="num ${stale ? 'tone-warn' : ''}">${stale ? `${I('clock-alert', 'icon-sm')} Stale · ` : 'Current · '}measured ${esc(fmt.stamp(w.measuredAt, now))}</span></div>${stale ? `<p class="caption tone-warn" style="margin:4px 0 0">${esc(windowStaleText(p, w, now))}</p>` : ''}</div>`;
      }).join('')}</div>`;
    } else {
      const title = p.quota.status === 'unsupported' ? 'Not available' : 'No reading';
      const why = p.quota.status === 'unsupported' ? `${p.name} doesn’t offer a supported capacity reading, so none is estimated.` : `${p.name} hasn’t reported a quota reading. It appears after a session on a plan that exposes one.`;
      quota = `<div class="panel"><div class="od-row-top">${I('minus', 'tone-muted')}<div class="od-stack" style="--od-gap:2px"><strong>${title}</strong><span class="caption">${esc(why)} This is not the same as 0%.</span></div></div></div>`;
    }

    const t = p.today || {};
    const stat = (v, label) => `<div class="stat"><span class="s-val">${v == null ? '<span class="na">—</span>' : fmt.tokens(v)}</span><span class="s-label">${label}${v == null ? ' · not reported' : ''}</span></div>`;
    const usageAge = p.usage.measuredAt ? `So far today · measured ${fmt.stamp(p.usage.measuredAt, now)} · collected ${fmt.stamp(p.usage.collectedAt, now)}` : 'Not measured yet';
    const today = `<div class="stat-grid">${stat(t.input, 'Input')}${stat(t.output, 'Output')}${stat(t.cacheRead, 'Cache reads')}${stat(t.cacheWrite, 'Cache writes')}</div><p class="caption num" style="margin:8px 2px 0">${p.usage.status === 'stale' ? `<span class="tone-warn">${I('clock-alert', 'icon-sm')} </span>` : ''}${esc(usageAge)}</p>`;

    const maxM = Math.max(1, ...p.models.map((m) => m.tokens));
    const models = p.models.length ? `<div class="panel"><ul class="models">${p.models.map((m) => `<li><span class="od-truncate">${esc(m.name)}</span><span class="num">${fmt.tokens(m.tokens)}</span><span class="bar"><span style="width:${(m.tokens / maxM) * 100}%"></span></span></li>`).join('')}</ul><p class="caption" style="margin:8px 0 0">Input + output so far today · totals ${fmt.tokens(ioOf(p.today))}.</p></div>` : '';

    const c = p.costs || {};
    const period = c.reported ? `${fmt.day(c.reported.periodStart)}–${fmt.day(c.reported.measuredAt)} (billing period to date, ends ${fmt.day(c.reported.periodEnd - 1)})` : '';
    const cost = `<details class="disc"><summary>Cost and accounting</summary>
      <dl class="facts">
        <div><dt>Reported usage cost</dt><dd>${c.reported ? `<span class="num">${fmt.usd(c.reported.amountUsd)}</span><small>${esc(period)}</small><small>${esc(c.reported.basis)} · as of ${esc(fmt.stamp(c.reported.measuredAt, now))}</small>` : fmt.na(c.reportedNote || 'Not reported')}</dd></div>
        <div><dt>Subscription price</dt><dd>${c.subscriptionMonthlyUsd != null ? `<span class="num">${fmt.usd(c.subscriptionMonthlyUsd)} per month</span><small>Entered by you · recorded for Sep 2026</small>` : fmt.na('Not set')}</dd></div>
        <div><dt>Daily usage</dt><dd><small>${esc(RECORD_KIND[p.recordKind] || '')}</small></dd></div>
      </dl>
      <p class="caption">Prices, reported costs, token counts, and quota percentages are kept separate. AI Usage doesn’t turn quota into dollars or unused tokens, and token totals aren’t comparable across providers.${p.quota.note ? ` ${esc(p.quota.note)}` : ''}</p>
    </details>`;
    const sources = `<details class="disc"${p.sources.some((s) => s.status !== 'ok') ? ' open' : ''}><summary>Sources and diagnostics</summary><ul class="src-list">${p.sources.map((s) => sourceRow(snap, s)).join('')}</ul></details>`;

    return `${head}
      <div style="margin-top:12px">${chartPanel(snap, p, ds)}</div>
      <div class="section-label">Quota</div>${quota}
      <div class="section-label">Today</div>${today}
      ${models ? `<div class="section-label">Models today</div>${models}` : ''}
      <div class="section-label">More</div>${cost}${sources}
      <div style="margin-top:12px"><button type="button" class="btn" data-act="history" data-provider="${p.id}">${I('chart')}Open in history window</button></div>`;
  }

  /* ------------------------------------------------------------ monitoring */
  function monitoring(snap, ui) {
    const u = ui || {};
    const now = snap.now, c = snap.collection;
    const running = snap.service.state === 'running';
    const paused = snap.schedule.state === 'paused';
    const collecting = c.activity === 'collecting';
    const resultTxt = { success: 'Succeeded', partial: 'Partly succeeded', failed: 'Failed' };
    const resultTone = { success: 'tone-ok', partial: 'tone-warn', failed: 'tone-bad' };
    const svc = `<div class="panel ctl-block"><div class="ctl-head">${statusGlyph(running ? 'ok' : 'bad', running ? 'power' : 'circle-stop')}
        <div class="c-text"><span class="c-title">Background collector</span><span class="c-state num">${running ? `Running since ${fmt.stamp(snap.service.since, now)}` : `Stopped ${fmt.ago(snap.service.since, now)}`}</span></div>
        ${running ? `<button type="button" class="btn" data-act="svc-stop-ask" ${u.confirmStop ? 'aria-expanded="true"' : ''}>${I('square', 'icon-sm')}Stop…</button>` : `<button type="button" class="btn primary" data-act="svc-start">${I('play', 'icon-sm')}Start</button>`}</div>
      ${u.confirmStop ? `<div class="confirm" role="alertdialog" aria-label="Stop the background collector?"><strong>Stop the background collector?</strong><span>Scheduled checks stop until you start it again, including after you log in. Your data is kept.</span><div class="row"><button type="button" class="btn" data-act="svc-stop-cancel">Cancel</button><button type="button" class="btn danger" data-act="svc-stop">Stop collector</button></div></div>` : ''}
      <p class="ctl-scope">Runs on its own as a login service on this Mac. Closing this popover or quitting the menu app doesn’t stop it.</p></div>`;
    const sched = `<div class="panel ctl-block"><div class="ctl-head">${statusGlyph(!running || paused ? 'muted' : 'ok', paused ? 'circle-pause' : 'clock')}
        <div class="c-text"><span class="c-title">Scheduled checks</span><span class="c-state num">${!running ? 'Not running — collector stopped' : paused ? `Paused ${fmt.ago(snap.schedule.pausedAt, now)}` : `Every ${snap.schedule.intervalMinutes >= 60 ? `${snap.schedule.intervalMinutes / 60} h` : `${snap.schedule.intervalMinutes} min`} · next ${fmt.until(c.nextScheduledAt, now)}`}</span></div>
        ${paused ? `<button type="button" class="btn" data-act="sched-resume" ${running ? '' : 'disabled'}>${I('play', 'icon-sm')}Resume</button>` : `<button type="button" class="btn" data-act="sched-pause" ${running ? '' : 'disabled'}>${I('pause', 'icon-sm')}Pause</button>`}</div>
      <p class="ctl-scope">${running ? 'Pausing skips scheduled checks only. The collector stays on and Collect now still works.' : 'Start the collector to schedule checks again.'}</p></div>`;
    const fails = c.failures.map((f) => {
      const p = f.providerId ? byId(snap, f.providerId) : null;
      const act = f.action === 'signin' ? '<button type="button" class="btn" data-act="copy-cmd">Copy command</button>' : f.action === 'details' ? '<button type="button" class="btn" data-act="reveal-log">Show log</button>' : '<button type="button" class="btn" data-act="collect">Try again</button>';
      return `<div class="notice bad" style="margin:0 0 8px">${I(f.action === 'signin' ? 'lock' : 'circle-x')}<span class="n-body"><span class="n-title">${p ? `${esc(p.name)}: ` : ''}${esc(f.message)}</span><span>${esc(f.hint)}</span><span class="num">${fmt.stamp(f.at, now)}</span></span></div><div style="margin:-2px 0 10px;text-align:right">${act}</div>`;
    }).join('');
    const coll = `<div class="panel"><dl class="facts">
        <div><dt>Last attempt</dt><dd>${c.lastAttemptAt ? `<span class="num">${fmt.stamp(c.lastAttemptAt, now)}</span><small class="${resultTone[c.lastAttemptResult] || ''}">${resultTxt[c.lastAttemptResult] || ''}</small>` : fmt.na('None yet')}</dd></div>
        <div><dt>Last successful collection</dt><dd>${c.lastSuccessAt ? `<span class="num">${fmt.stamp(c.lastSuccessAt, now)}</span><small>${fmt.ago(c.lastSuccessAt, now)}</small>` : fmt.na('None yet')}</dd></div>
        <div><dt>Next scheduled collection</dt><dd>${c.nextScheduledAt ? `<span class="num">${fmt.clock(c.nextScheduledAt)}</span><small>${fmt.until(c.nextScheduledAt, now)}</small>` : fmt.na(!running ? 'None — collector stopped' : 'None — checks paused')}</dd></div>
      </dl>
      <div class="od-row" style="margin-top:10px"><button type="button" class="btn primary" data-act="collect" ${collecting ? 'disabled' : ''}>${I('refresh', `icon-sm ${collecting ? 'spin' : ''}`)}${collecting ? 'Checking…' : 'Collect now'}</button><span class="caption od-fill">${running ? 'Runs one check right away. Doesn’t change the schedule.' : 'Runs one check without starting the collector.'}</span></div>
      ${collecting && c.progress ? `<p class="caption" role="status" style="margin:8px 0 0">Reading ${esc((byId(snap, c.progress.current) || {}).name || '')} · ${Math.min(c.progress.done + 1, c.progress.total)} of ${c.progress.total}</p>` : ''}</div>`;
    const sources = active(snap).map((p) => `<details class="disc"${p.sources.some((s) => !['ok', 'off'].includes(s.status)) ? ' open' : ''}><summary>${esc(p.name)} <span class="caption">· ${p.sources.map((s) => (SRC_STATUS[s.status] || SRC_STATUS.ok).text).join(', ')}</span></summary><ul class="src-list">${p.sources.map((s) => sourceRow(snap, s)).join('')}</ul><button type="button" class="tbtn" data-open="${p.id}">Open ${esc(p.name)}${I('chevron-right', 'icon-sm')}</button></details>`).join('');
    return `<h2 tabindex="-1">Monitoring</h2><p class="sub">Collector on this Mac · checks are read-only and never use model turns</p>
      <div class="section-label">Service</div>${svc}
      <div class="section-label">Schedule</div>${sched}
      <div class="section-label">Collection</div>${fails}${coll}
      <div class="section-label">Provider sources</div>${sources}
      <div class="od-row" style="margin-top:12px"><button type="button" class="tbtn" data-act="reveal-log">${I('file-text')}Show collector log</button></div>`;
  }

  /* ------------------------------------------------------------ settings */
  function settings(snap, ui) {
    const u = ui || {};
    const s = u.settings;
    const sw = (id, on, label) => `<label class="switch"><input type="checkbox" role="switch" data-set="${id}" ${on ? 'checked' : ''} aria-label="${esc(label)}"><span class="track"></span></label>`;
    const intervals = [[15, 'Every 15 minutes'], [30, 'Every 30 minutes'], [60, 'Every hour'], [120, 'Every 2 hours'], [360, 'Every 6 hours']];
    const prov = snap.providers.map((p) => {
      const ps = s.providers[p.id];
      const err = u.priceErrors && u.priceErrors[p.id];
      const help = p.setup === 'not_configured' ? 'Needs usage telemetry turned on. It includes account identifiers.' : p.product;
      return `<div class="setting" style="flex-wrap:wrap">${sw(`prov:${p.id}`, ps.enabled, `Collect ${p.name}`)}
        <span class="st-text"><span class="st-label">${esc(p.name)}</span><span class="st-help">${esc(help)}</span></span>
        <span class="price"><label class="sr-only" for="price-${p.id}">${esc(p.name)} subscription price, US dollars per month</label>$<input id="price-${p.id}" type="text" inputmode="decimal" placeholder="—" value="${ps.price == null ? '' : esc(ps.price)}" data-price="${p.id}" ${err ? 'aria-invalid="true"' : ''} aria-describedby="perr-${p.id}"><span class="caption">/ mo</span></span>
        ${p.setup === 'not_configured' && ps.enabled ? `<span style="flex-basis:100%;padding-left:50px" class="od-row"><button type="button" class="btn" data-act="gemini-setup">${I('plug', 'icon-sm')}Turn on telemetry…</button></span>` : ''}
        <span id="perr-${p.id}" style="flex-basis:100%;padding-left:50px" ${err ? '' : 'hidden'} class="field-err">${err ? `${I('circle-alert', 'icon-sm')}${esc(err)}` : ''}</span></div>`;
    }).join('');
    return `<h2 tabindex="-1">Settings</h2><p class="sub">Collector settings apply to the background service. App settings apply to this menu only.</p>
      <div class="live" aria-live="polite">${u.saved ? `<p class="saved" style="margin:8px 0 0">${I('check', 'icon-sm')}${esc(u.saved)}</p>` : ''}</div>
      <div class="section-label">Collector · Checks</div>
      <div class="panel"><div class="setting"><span class="st-text"><label class="st-label" for="set-interval">Check for new usage</label><span class="st-help">Takes effect after the current check. Waiting between checks is normal.</span></span>
        <select id="set-interval" data-set="interval">${intervals.map(([v, l]) => `<option value="${v}" ${s.interval === v ? 'selected' : ''}>${l}</option>`).join('')}</select></div></div>
      <div class="section-label">Collector · Providers and subscription prices</div>
      <div class="panel">${prov}<p class="caption" style="margin:8px 0 0">Prices are what you pay per month, for your reference. They’re recorded monthly and never compared with quota. Leave blank if unknown.</p></div>
      <div class="section-label">Notifications</div>
      <div class="panel">
        <div class="setting">${sw('n:service', s.notify.service, 'Collector stops or checks fail')}<span class="st-text"><span class="st-label">Collector stops or checks fail</span></span></div>
        <div class="setting">${sw('n:auth', s.notify.auth, 'A provider needs sign-in')}<span class="st-text"><span class="st-label">A provider needs sign-in</span></span></div>
        <div class="setting">${sw('n:low', s.notify.low, 'Any quota window drops below 20% remaining')}<span class="st-text"><span class="st-label">Any quota window drops below 20% remaining</span><span class="st-help">Checked per window, for providers that report quota.</span></span></div>
        <div class="setting">${sw('n:reset', s.notify.reset, 'A used-up limit resets')}<span class="st-text"><span class="st-label">A used-up limit resets</span></span></div>
      </div>
      <div class="section-label">This app</div>
      <div class="panel">
        <div class="setting">${sw('login', s.login, 'Open AI Usage menu at login')}<span class="st-text"><span class="st-label">Open AI Usage menu at login</span><span class="st-help">Only the menu bar app. The background collector starts at login on its own and keeps collecting either way.</span></span></div>
        <div class="setting"><span class="st-text"><span class="st-label">Background collector</span><span class="st-help">Start, stop, or pause it in Monitoring.</span></span><button type="button" class="btn" data-open="monitor">Monitoring${I('chevron-right', 'icon-sm')}</button></div>
      </div>
      <div class="od-row" style="margin-top:12px"><button type="button" class="tbtn" data-act="quit">${I('power')}Quit menu app</button><span class="caption od-fill">The collector keeps running.</span></div>`;
  }

  function defaultSettings(snap) {
    const providers = {};
    snap.providers.forEach((p) => { providers[p.id] = { enabled: p.enabled, price: p.costs && p.costs.subscriptionMonthlyUsd != null ? String(p.costs.subscriptionMonthlyUsd) : null }; });
    return { interval: snap.schedule.intervalMinutes, providers, notify: { service: true, auth: true, low: true, reset: false }, login: true };
  }

  /* ------------------------------------------------------------ popover shell */
  function menubar(state, expanded) {
    const labels = { normal: 'AI Usage: monitoring', collecting: 'AI Usage: checking', paused: 'AI Usage: checks paused', stopped: 'AI Usage: collector stopped', attention: 'AI Usage: needs attention' };
    return `<div class="menubar"><span class="mb-extra">${I('sparkle', 'icon-sm')}</span><button type="button" class="mb-button" data-act="toggle" aria-haspopup="dialog" aria-expanded="${expanded ? 'true' : 'false'}" aria-label="${labels[state]}">${window.AIU_MENU_GLYPH(state)}</button><span class="mb-clock num">Tue 29 Sep 10:40</span></div>`;
  }

  function viewHTML(snap, view, providerId, ui, ds) {
    if (view === 'overview') {
      const o = overview(snap, ui);
      return `<div class="pop-top">${o.top}</div><div class="pop-scroll" data-scroll>${o.scroll}</div><div class="pop-foot">${o.foot}</div>`;
    }
    const title = view === 'provider' ? byId(snap, providerId).name : view === 'monitor' ? 'Monitoring' : 'Settings';
    const body = view === 'provider' ? providerDetail(snap, byId(snap, providerId), ds) : view === 'monitor' ? monitoring(snap, ui) : settings(snap, ui);
    return `<div class="pop-top pop-nav"><button type="button" class="back" data-act="back" aria-label="Back to overview from ${esc(title)}">${I('chevron-left', 'icon-sm')}Overview</button></div><div class="pop-scroll detail" data-scroll>${body}</div>`;
  }

  function staticPopover(snap, view, providerId, ui, ds) {
    const u = Object.assign({ settings: defaultSettings(snap) }, ui || {});
    return `<div class="popover no-arrow unbounded"><div class="pop-view">${viewHTML(snap, view, providerId, u, ds)}</div></div>`;
  }

  /* ------------------------------------------------------------ shared active state
     Prototype-scope sync between the popover (the only writer) and History
     (a reader). Every popover change publishes the whole active snapshot:
       1. localStorage key 'aiu:active-state:v1' (read on open; 'storage' events
          reach other open windows of the same origin),
       2. BroadcastChannel 'aiu-active-state' (live updates where supported),
       3. window.__AIU_ACTIVE__ (fallback: History reads it via window.opener).
     Readers ignore payloads whose rev is not newer than the last one applied. */
  const SHARED_KEY = 'aiu:active-state:v1';
  const shared = (function () {
    let bc = null;
    try { bc = new BroadcastChannel('aiu-active-state'); } catch (_) { bc = null; }
    let lastRev = 0;
    return {
      publish(snap, meta) {
        lastRev = Math.max(Date.now(), lastRev + 1);
        const payload = { rev: lastRev, savedAt: Date.now(), meta: meta || {}, snap };
        window.__AIU_ACTIVE__ = payload;
        try { localStorage.setItem(SHARED_KEY, JSON.stringify(payload)); } catch (_) { /* storage blocked: opener fallback only */ }
        try { if (bc) bc.postMessage(payload); } catch (_) { /* ignore */ }
      },
      read() {
        try { const v = localStorage.getItem(SHARED_KEY); if (v) return JSON.parse(v); } catch (_) { /* fall through */ }
        try { if (window.opener && window.opener.__AIU_ACTIVE__) return JSON.parse(JSON.stringify(window.opener.__AIU_ACTIVE__)); } catch (_) { /* cross-origin opener */ }
        return null;
      },
      subscribe(cb) {
        let seen = 0;
        const take = (payload) => { if (payload && payload.rev > seen) { seen = payload.rev; cb(payload); } };
        window.addEventListener('storage', (e) => { if (e.key === SHARED_KEY && e.newValue) { try { take(JSON.parse(e.newValue)); } catch (_) { /* ignore */ } } });
        if (bc) bc.addEventListener('message', (e) => take(e.data));
        return { mark(rev) { seen = Math.max(seen, rev || 0); } };
      },
      live: !!bc
    };
  })();

  /* ------------------------------------------------------------ controller */
  function mountPopover(root, opts) {
    const o = opts || {};
    let snap = F.getSnapshot(o.scenario || 'healthy');
    const freshUI = () => ({ settings: defaultSettings(snap), priceErrors: {}, result: null, confirmStop: false, saved: null });
    const state = { open: true, view: 'overview', providerId: null, returnFocus: null, overviewScroll: 0, ui: freshUI(), detail: {}, forcedOutcome: o.outcome || null, timer: null };
    const menuState = () => overallStatus(snap).menu;
    const keyOf = (el) => {
      for (const a of ['data-set', 'data-price', 'data-act', 'data-open', 'data-dact', 'data-dsel']) { const v = el && el.getAttribute && el.getAttribute(a); if (v) return `[${a}="${v}"]`; }
      return el && el.id ? `#${el.id}` : null;
    };
    bindCharts(root);

    function publish() { if (o.share !== false) shared.publish(snap, { scenario: snap.id, label: snap.label }); }
    function render(dir, keepScroll) {
      publish();
      root.querySelector('[data-slot="menubar"]').innerHTML = menubar(menuState(), state.open);
      const pop = root.querySelector('[data-slot="popover"]');
      pop.hidden = !state.open;
      if (o.onChange) o.onChange({ snap, view: state.view, open: state.open, menu: menuState() });
      if (!state.open) return;
      const v = pop.querySelector('.pop-view');
      const oldScroll = v.querySelector('[data-scroll]');
      const top = keepScroll && oldScroll ? oldScroll.scrollTop : null;
      v.className = `pop-view${dir ? ` enter-${dir}` : ''}`;
      v.innerHTML = viewHTML(snap, state.view, state.providerId, state.ui, state.detail[state.providerId]);
      if (top != null) v.querySelector('[data-scroll]').scrollTop = top;
    }
    function go(view, pid, trigger) {
      if (view === 'overview') {
        state.view = 'overview'; state.ui.confirmStop = false; state.ui.saved = null;
        render('back');
        root.querySelector('[data-scroll]').scrollTop = state.overviewScroll;
        const target = state.returnFocus && root.querySelector(`.card-link[data-open="${state.returnFocus}"], .status-row[data-open="${state.returnFocus}"], [data-open="${state.returnFocus}"]`);
        (target || root.querySelector('.status-row')).focus({ preventScroll: !!target });
        state.returnFocus = null;
        return;
      }
      if (state.view === 'overview') { state.overviewScroll = (root.querySelector('[data-scroll]') || {}).scrollTop || 0; if (trigger) state.returnFocus = trigger; }
      state.view = view; state.providerId = pid || null; state.ui.result = null;
      render('forward');
      const h = root.querySelector('.detail h2'); if (h) h.focus();
    }
    function collect() {
      if (snap.collection.activity === 'collecting') return;
      const order = F.collectOrder.filter((id) => { const p = byId(snap, id); return p && p.enabled && p.setup !== 'not_configured'; });
      state.ui.result = null;
      snap.collection.activity = 'collecting';
      snap.collection.progress = { done: 0, total: order.length, current: order[0] };
      render(null, true);
      let i = 0;
      const stepMs = window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 450 : 650;
      clearInterval(state.timer);
      state.timer = setInterval(() => {
        i += 1;
        if (i < order.length) { snap.collection.progress = { done: i, total: order.length, current: order[i] }; render(null, true); return; }
        clearInterval(state.timer);
        const outcome = state.forcedOutcome || snap.collectOutcome;
        snap = F.applyCollectResult(snap, outcome);
        const fails = snap.collection.failures.length;
        const sm = snap.collection.summary || {};
        const skip = sm.skipped && sm.skipped.length ? ` ${sm.skipped.join(', ')} ${sm.skipped.length === 1 ? 'is' : 'are'} off and kept ${sm.skipped.length === 1 ? 'its' : 'their'} cached values.` : '';
        const partlyTxt = sm.providersPartly ? ` ${sm.providersPartly} partly updated.` : '';
        state.ui.result = snap.collection.lastAttemptResult === 'success'
          ? { tone: 'info', icon: 'circle-check', title: 'Check complete', body: `${sm.providersUpdated} of ${sm.providersAttempted} providers read at ${fmt.clock(snap.now)}.${skip}` }
          : snap.collection.lastAttemptResult === 'partial'
            ? { tone: 'warn', icon: 'circle-alert', title: 'Check partly complete', body: `${sm.providersUpdated} of ${sm.providersAttempted} providers fully updated.${partlyTxt}${sm.providersFailed ? ` ${sm.providersFailed} kept previous values.` : ''} See Monitoring.${skip}` }
            : { tone: 'bad', icon: 'circle-x', title: 'Check failed', body: `Nothing new was saved. Previous values and their times are unchanged.${skip}` };
        render(null, true);
      }, stepMs);
    }

    root.addEventListener('click', (e) => {
      const actEl = e.target.closest('[data-act]');
      const openEl = e.target.closest('[data-open]');
      const dEl = e.target.closest('[data-dact]');
      if (dEl && !dEl.disabled) {
        const [k, v] = dEl.getAttribute('data-dact').split(':');
        const p = byId(snap, state.providerId);
        const ds = state.detail[p.id] = Object.assign(defaultDetailState(p), state.detail[p.id] || {});
        if (k === 'trange') ds[k] = Number(v); else ds[k] = v;
        render(null, true);
        const again = root.querySelector(`[data-dact="${k}:${v}"]`); if (again) again.focus();
        return;
      }
      if (actEl) {
        const a = actEl.getAttribute('data-act');
        if (a === 'toggle') { state.open = !state.open; render(); if (!state.open) root.querySelector('.mb-button').focus(); return; }
        if (a === 'back') return go('overview');
        if (a === 'collect') return collect();
        if (a === 'history') { const pid = actEl.getAttribute('data-provider') || ''; if (o.onHistory) o.onHistory(pid); return; }
        if (a === 'open-monitor') return go('monitor', null, 'monitor');
        if (a.startsWith('open:')) return go('provider', a.slice(5), a.slice(5));
        if (a === 'svc-stop-ask') { state.ui.confirmStop = true; render(null, true); root.querySelector('[data-act="svc-stop"]').focus(); return; }
        if (a === 'svc-stop-cancel') { state.ui.confirmStop = false; render(null, true); root.querySelector('[data-act="svc-stop-ask"]').focus(); return; }
        if (a === 'svc-stop') { snap.service.state = 'stopped'; snap.service.since = snap.now; snap.collection.nextScheduledAt = null; state.ui.confirmStop = false; render(null, true); root.querySelector('[data-act="svc-start"]').focus(); return; }
        if (a === 'svc-start') { snap.service.state = 'running'; snap.service.since = snap.now; if (snap.schedule.state === 'active') snap.collection.nextScheduledAt = snap.now + snap.schedule.intervalMinutes * MIN; render(null, true); const b = root.querySelector('[data-act="svc-stop-ask"]'); if (b) b.focus(); return; }
        if (a === 'sched-pause') { snap.schedule.state = 'paused'; snap.schedule.pausedAt = snap.now; snap.collection.nextScheduledAt = null; render(null, true); root.querySelector('[data-act="sched-resume"]').focus(); return; }
        if (a === 'sched-resume') { snap.schedule.state = 'active'; snap.schedule.pausedAt = null; snap.collection.nextScheduledAt = snap.now + snap.schedule.intervalMinutes * MIN; render(null, true); root.querySelector('[data-act="sched-pause"]').focus(); return; }
        if (a === 'copy-cmd') { actEl.textContent = 'Copied “grok login”'; return; }
        if (a === 'reveal-log') { actEl.innerHTML = `${I('file-text')}Log shown in Finder (demo)`; return; }
        if (a === 'gemini-setup') { actEl.innerHTML = `${I('info', 'icon-sm')}Would ask for confirmation, then edit Gemini settings (demo)`; return; }
        if (a === 'quit') { state.open = false; render(); root.querySelector('.mb-button').focus(); return; }
      }
      if (openEl) {
        const id = openEl.getAttribute('data-open');
        if (id === 'monitor' || id === 'settings') return go(id, null, state.view === 'overview' ? id : null);
        return go('provider', id, state.view === 'overview' ? id : null);
      }
    });

    root.addEventListener('change', (e) => {
      const el = e.target;
      if (el.getAttribute('data-dsel') === 'win') {
        const p = byId(snap, state.providerId);
        const ds = state.detail[p.id] = Object.assign(defaultDetailState(p), state.detail[p.id] || {});
        ds.win = el.value;
        ds.qrange = p.quota.windows.find((w) => w.id === el.value).ranges[0].id;
        render(null, true);
        root.querySelector('[data-dsel="win"]').focus();
        return;
      }
      const key = el.getAttribute('data-set');
      if (!key) return;
      const s = state.ui.settings;
      if (key === 'interval') {
        s.interval = Number(el.value); snap.schedule.intervalMinutes = s.interval;
        if (snap.schedule.state === 'active' && snap.service.state === 'running') snap.collection.nextScheduledAt = (snap.collection.lastAttemptAt || snap.now) + s.interval * MIN;
        state.ui.saved = `Checks ${el.options[el.selectedIndex].text.toLowerCase()} from the next check.`;
      } else if (key.startsWith('prov:')) {
        const id = key.slice(5); s.providers[id].enabled = el.checked; byId(snap, id).enabled = el.checked;
        state.ui.saved = `${byId(snap, id).name} ${el.checked ? 'will be collected' : 'turned off. Existing data is kept'}.`;
      } else if (key.startsWith('n:')) { s.notify[key.slice(2)] = el.checked; state.ui.saved = 'Notification preference saved.'; }
      else if (key === 'login') { s.login = el.checked; state.ui.saved = el.checked ? 'The menu app will open at login.' : 'The menu app won’t open at login. The collector still starts at login.'; }
      render(null, true);
      const again = root.querySelector(`[data-set="${key}"]`); if (again) again.focus();
    });

    // Validate prices on blur without re-rendering, so a click on Back or a switch still lands.
    root.addEventListener('focusout', (e) => {
      const el = e.target;
      const id = el.getAttribute && el.getAttribute('data-price');
      if (!id) return;
      const v = el.value.trim();
      const s = state.ui.settings;
      const err = v !== '' && !/^\d{1,5}(\.\d{1,2})?$/.test(v) ? 'Enter a monthly price like 20 or 19.99, or leave it blank.' : null;
      state.ui.priceErrors[id] = err;
      const errEl = root.querySelector(`#perr-${id}`);
      if (err) { el.setAttribute('aria-invalid', 'true'); if (errEl) { errEl.hidden = false; errEl.innerHTML = `${I('circle-alert', 'icon-sm')}${esc(err)}`; } return; }
      el.removeAttribute('aria-invalid');
      if (errEl) { errEl.hidden = true; errEl.textContent = ''; }
      const prev = s.providers[id].price == null ? '' : String(s.providers[id].price);
      if (prev === v) return;
      s.providers[id].price = v === '' ? null : v;
      const p = byId(snap, id);
      p.costs.subscriptionMonthlyUsd = v === '' ? null : Number(v);
      state.ui.saved = v === '' ? `${p.name} price cleared.` : `${p.name} price saved: $${v} per month.`;
      publish();
      const live = root.querySelector('.detail .live');
      if (live) live.innerHTML = `<p class="saved" style="margin:8px 0 0">${I('check', 'icon-sm')}${esc(state.ui.saved)}</p>`;
    });

    root.addEventListener('keydown', (e) => {
      if (e.defaultPrevented) return;
      if (e.key === 'Escape') {
        if (state.ui.confirmStop) { state.ui.confirmStop = false; render(null, true); return; }
        if (state.view !== 'overview') { e.preventDefault(); go('overview'); }
        else if (state.open) { state.open = false; render(); root.querySelector('.mb-button').focus(); }
      }
      if ((e.metaKey || e.ctrlKey) && e.key === 'r') { e.preventDefault(); collect(); }
      if ((e.metaKey || e.ctrlKey) && e.key === ',') { e.preventDefault(); go('settings', null, 'settings'); }
      if (state.view === 'overview' && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
        const items = [...root.querySelectorAll('.status-row, .card-link')];
        const i = items.indexOf(document.activeElement);
        if (i >= 0) { e.preventDefault(); items[Math.max(0, Math.min(items.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))].focus(); }
      }
    });
    void keyOf;

    function resumeIfCollecting() { if (snap.collection.activity === 'collecting') { snap.collection.activity = 'idle'; collect(); } }
    render();
    resumeIfCollecting();
    return {
      setScenario(id) { clearInterval(state.timer); snap = F.getSnapshot(id); state.ui = freshUI(); state.detail = {}; state.view = 'overview'; state.open = true; render(); resumeIfCollecting(); },
      setOutcome(v) { state.forcedOutcome = v || null; },
      show(view, pid) { go(view, pid); },
      collect
    };
  }

  window.AIU = { fmt, esc, overallStatus, notices, providerFlags, windowState, staleText, windowStaleText, readWindows, shared, overview, providerDetail, monitoring, settings, defaultSettings, defaultDetailState,
    barChartRange, quotaChart, miniChart, bindCharts, menubar, staticPopover, mountPopover, dayAt, ioOf, RECORD_KIND };
})();
