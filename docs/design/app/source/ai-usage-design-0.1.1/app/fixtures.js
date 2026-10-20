/* ai-usage — ILLUSTRATIVE sample fixtures (checkpoint 0.1.1).
   Every number, time, and model name here is sample data for design review.

   One source of truth per provider:
     days[]            daily usage records {date, state, input, output, cacheRead, cacheWrite, partial}
                       state: 'measured' | 'missing'; a measured 0 is a real zero, 'missing' is a gap.
                       The last record is today, marked partial (the day is not over).
     quota.windows[]   one entry per limiting window, each with its own readings[]
                       {t, percent, resetsAt, costUsd?} and resets[] (times a window reset).
   Everything the UI shows — card totals, chart endpoints, model splits, tables,
   current percentages, freshness — is derived from these in finalize().
   Scenarios are transformations of the same measurements (cut, fail, stale...),
   so values can never disagree between views.

   Derived Provider view used by components.js (abridged):
     today {input, output, cacheRead, cacheWrite} | null, models[{name,tokens}],
     usage {status, measuredAt, collectedAt, recordKind},
     quota {status, staleCause, measuredAt, collectedAt, windows[{id,label,short,basis,
            percent, measuredAt, resetsAt, readings, resets, ranges}]},
     costs {reported {amountUsd, periodStart, periodEnd, measuredAt} | null, subscriptionMonthlyUsd}
*/
(function () {
  const NOW = new Date(2026, 8, 29, 10, 40, 0).getTime(); // Tue 29 Sep 2026, 10:40 (illustrative)
  const MIN = 60 * 1000, HOUR = 60 * MIN, DAY = 24 * HOUR;
  const ago = (m) => NOW - m * MIN;
  const ahead = (m) => NOW + m * MIN;
  const dayAt = (i) => new Date(2026, 8, 29 - i).getTime();      // midnight, i days ago (DST-safe)
  const TODAY = dayAt(0);
  const at = (i, h, m) => new Date(2026, 8, 29 - i, h, m).getTime();
  const clone = (o) => JSON.parse(JSON.stringify(o));

  function rng(seed) {
    let s = seed >>> 0;
    return () => {
      s = (s + 0x6D2B79F5) >>> 0;
      let t = s;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  const isWeekend = (t) => { const d = new Date(t).getDay(); return d === 0 || d === 6; };

  /* ---------------------------------------------------------- daily usage */
  function dailySeries(o) {
    const r = rng(o.seed);
    const out = [];
    for (let i = o.days - 1; i >= 1; i--) {
      const date = dayAt(i);
      if ((o.missing || []).includes(i)) { out.push({ date, state: 'missing', input: null, output: null, cacheRead: null, cacheWrite: null }); continue; }
      const f = isWeekend(date) ? 0.3 : 1;
      let total = Math.round((o.base * f * (0.55 + r() * 0.9)) / 1000) * 1000;
      if ((o.zeros || []).includes(i)) total = 0;
      const input = Math.round((total * (1 - o.outShare)) / 1000) * 1000;
      const cr = r(), cw = r();
      out.push({
        date, state: 'measured',
        input, output: total - input,
        cacheRead: o.cacheRead == null ? null : Math.round((total * o.cacheRead * (0.8 + cr * 0.4)) / 1000) * 1000,
        cacheWrite: o.cacheWrite == null ? null : Math.round((total * o.cacheWrite * (0.8 + cw * 0.4)) / 1000) * 1000
      });
    }
    out.push(Object.assign({ date: TODAY, state: 'measured', partial: true }, o.today));
    return out;
  }

  /* ------------------------------------------- rolling 5-hour style windows
     Usage opens a window; it resets 5 h after it opened. Past days are generated;
     today's readings are explicit so the current value is exact. */
  function rollingWindow(o) {
    const r = rng(o.seed);
    const events = [];
    for (let i = o.days; i >= 1; i--) {
      const n = isWeekend(dayAt(i)) ? 2 + Math.floor(r() * 3) : 6 + Math.floor(r() * 7);
      for (let k = 0; k < n; k++) events.push({ t: at(i, 9, 0) + Math.floor(r() * 10 * 60) * MIN, c: 3 + r() * 9 });
    }
    events.sort((a, b) => a.t - b.t);
    const readings = [], resets = [];
    let remaining = 100, windowEnd = null, ei = 0;
    const apply = (t) => {
      while (ei < events.length && events[ei].t <= t) {
        const e = events[ei++];
        if (windowEnd != null && e.t >= windowEnd) { resets.push(windowEnd); remaining = 100; windowEnd = null; }
        if (windowEnd == null) windowEnd = e.t + 5 * HOUR;
        remaining = Math.max(0, remaining - e.c);
        if (o.sample === 'event') readings.push({ t: e.t, percent: Math.round(remaining), resetsAt: windowEnd });
      }
      if (windowEnd != null && t >= windowEnd) { resets.push(windowEnd); remaining = 100; windowEnd = null; }
    };
    const todayStart = at(0, o.today.startH, o.today.startM);
    if (o.sample === 'hourly') {
      for (let t = at(o.days, 0, 38); t < todayStart; t += HOUR) {
        apply(t);
        readings.push({ t, percent: Math.round(remaining), resetsAt: windowEnd });
      }
    } else apply(todayStart - 1);
    if (windowEnd != null && windowEnd <= todayStart) resets.push(windowEnd);
    const end = todayStart + 5 * HOUR;
    o.today.points.forEach(([h, m, pct]) => readings.push({ t: at(0, h, m), percent: pct, resetsAt: end }));
    return { readings, resets };
  }

  /* -------------------------------------------- calendar windows (weekly, monthly)
     Used share grows with activity inside each period and resets at the period
     boundary. Each period ends exactly at its configured value. */
  function periodWindow(o) {
    const r = rng(o.seed);
    const ticks = [];
    for (let t = o.start; t <= o.end; t += o.step) ticks.push(t);
    const periodOf = (t) => { let p = null; o.boundaries.forEach((b, i) => { if (b <= t) p = i; }); return p; };
    const inc = ticks.map((t) => {
      const h = new Date(t).getHours();
      const active = !isWeekend(t) && h >= 9 && h <= 20 ? 1 : 0.15;
      return active * (0.3 + r());
    });
    const totals = {};
    ticks.forEach((t, i) => { const p = periodOf(t); totals[p] = (totals[p] || 0) + inc[i]; });
    const cum = {};
    const readings = ticks.map((t, i) => {
      const p = periodOf(t);
      cum[p] = (cum[p] || 0) + inc[i];
      const target = o.periodUsed[p];
      const used = Math.min(100, (target * cum[p]) / totals[p]);
      const nextB = o.boundaries[p + 1];
      const rd = { t, percent: Math.round(o.basis === 'used' ? used : 100 - used), resetsAt: nextB };
      if (o.cost) rd.costUsd = Math.round(o.cost * used) / 100;
      return rd;
    });
    const resets = o.boundaries.filter((b) => b > o.start && b <= o.end);
    return { readings, resets };
  }

  /* ---------------------------------------------------------- baseline */
  function codexQuota(opts) {
    const o = Object.assign({ fivePoints: [[7, 38, 92], [8, 38, 71], [9, 38, 55], [10, 38, 38]], weeklyUsedNow: 29 }, opts);
    const five = rollingWindow({
      seed: 5, days: 7, sample: 'hourly',
      today: { startH: 7, startM: 22, points: o.fivePoints }
    });
    const fridays = [new Date(2026, 7, 21, 9).getTime(), new Date(2026, 7, 28, 9).getTime(), new Date(2026, 8, 4, 9).getTime(), new Date(2026, 8, 11, 9).getTime(), new Date(2026, 8, 18, 9).getTime(), new Date(2026, 8, 25, 9).getTime(), new Date(2026, 9, 2, 9).getTime()];
    const weekly = periodWindow({
      seed: 7, basis: 'remaining', start: at(35, 1, 38), end: at(0, 10, 38), step: 3 * HOUR,
      boundaries: fridays, periodUsed: [64, 81, 52, 73, 58, o.weeklyUsedNow, 0]
    });
    return {
      mode: 'polled',
      note: 'Weighted percentages reported by Codex. They are not unused-token counts.',
      windows: [
        { id: 'five-hour', label: '5-hour limit', short: '5-hour', basis: 'remaining', readings: five.readings, resets: five.resets,
          ranges: [{ id: '24h', label: '24 hours', ms: DAY }, { id: '7d', label: '7 days', ms: 7 * DAY }], keptDays: 7 },
        { id: 'weekly', label: 'Weekly limit', short: 'Weekly', basis: 'remaining', readings: weekly.readings, resets: weekly.resets,
          ranges: [{ id: '7d', label: '7 days', ms: 7 * DAY }, { id: '5w', label: '5 weeks', ms: 35 * DAY }], keptDays: 35 }
      ]
    };
  }

  function baseline() {
    const monthStarts = [new Date(2026, 5, 1).getTime(), new Date(2026, 6, 1).getTime(), new Date(2026, 7, 1).getTime(), new Date(2026, 8, 1).getTime(), new Date(2026, 9, 1).getTime()];
    const grokCredits = periodWindow({
      seed: 13, basis: 'used', start: at(89, 1, 9), end: at(0, 10, 9), step: 3 * HOUR,
      boundaries: monthStarts, periodUsed: [60, 71, 88, 46, 0], cost: 6.913
    });
    const agy = rollingWindow({
      seed: 17, days: 7, sample: 'event',
      today: { startH: 8, startM: 45, points: [[8, 52, 93], [9, 20, 80], [9, 53, 64]] }
    });
    return {
      id: 'healthy',
      now: NOW,
      service: { state: 'running', since: NOW - 6 * DAY },
      schedule: { state: 'active', intervalMinutes: 60, pausedAt: null },
      collection: { activity: 'idle', progress: null, lastAttemptAt: ago(2), lastAttemptResult: 'success', lastSuccessAt: ago(2), nextScheduledAt: ahead(58), failures: [] },
      collectOutcome: 'success',
      providers: [
        {
          id: 'codex', name: 'Codex', product: 'OpenAI Codex', enabled: true, setup: 'ready',
          recordKind: 'period_total', usageMode: 'polled', usageMeasuredAt: ago(2),
          days: dailySeries({ seed: 11, days: 90, base: 820000, outShare: 0.12, cacheRead: 4.0, cacheWrite: null,
            today: { input: 684000, output: 92000, cacheRead: 3120000, cacheWrite: null } }),
          modelShares: [['gpt-5-codex', 0.787], ['gpt-5-codex-mini', 0.213]],
          quota: codexQuota(),
          costs: { reported: null, reportedNote: 'Codex does not report a usage cost.', subscriptionMonthlyUsd: 20 },
          sources: [
            { id: 'rate-limits', role: 'quota', label: 'Rate limits', kind: 'Codex account API, read-only (account/rateLimits/read)', status: 'ok' },
            { id: 'account-usage', role: 'usage', label: 'Account usage', kind: 'Codex account API, read-only (account/usage/read)', status: 'ok' }
          ]
        },
        {
          id: 'claude', name: 'Claude Code', product: 'Claude Code', enabled: true, setup: 'ready',
          recordKind: 'period_total', usageMode: 'polled', usageMeasuredAt: ago(9),
          days: dailySeries({ seed: 29, days: 90, base: 560000, outShare: 0.16, cacheRead: 15, cacheWrite: 1.1,
            today: { input: 431000, output: 81000, cacheRead: 8420000, cacheWrite: 612000 } }),
          modelShares: [['claude-sonnet-4-5', 0.785], ['claude-opus-4-1', 0.168], ['claude-haiku-4-5', 0.047]],
          quota: { mode: 'unsupported', windows: [] },
          costs: { reported: null, reportedNote: 'Claude Code’s local stats don’t include cost.', subscriptionMonthlyUsd: 100 },
          sources: [
            { id: 'sessions', role: 'usage', label: 'Recent sessions', kind: 'Local session activity (proposed source — not in the repository yet)', status: 'ok' },
            { id: 'stats-cache', role: 'history', label: 'Daily stats', kind: 'Claude Code local stats cache (undocumented format)', status: 'ok', measuredAt: ago(24) }
          ]
        },
        {
          id: 'antigravity', name: 'Antigravity', product: 'Antigravity CLI', enabled: true, setup: 'ready',
          recordKind: 'delta', usageMode: 'event', usageMeasuredAt: ago(47),
          days: dailySeries({ seed: 43, days: 34, base: 150000, outShare: 0.12, cacheRead: null, cacheWrite: null, zeros: [2, 9, 16],
            today: { input: 128000, output: 18000, cacheRead: null, cacheWrite: null } }),
          coverageNote: 'Collection for Antigravity began on 26 Aug, when its status-line callback was attached.',
          modelShares: [['gemini-3-pro', 1]],
          quota: {
            mode: 'event',
            note: 'Reported by Antigravity while it runs. Between sessions the last reading is kept; that is normal, not stale.',
            windows: [{ id: 'model', label: 'Model quota', short: 'Model', basis: 'remaining', readings: agy.readings, resets: agy.resets,
              ranges: [{ id: '24h', label: '24 hours', ms: DAY }, { id: '7d', label: '7 days', ms: 7 * DAY }], keptDays: 7 }]
          },
          costs: { reported: null, reportedNote: 'Antigravity does not report a usage cost.', subscriptionMonthlyUsd: null },
          sources: [
            { id: 'status-line', role: 'both', label: 'Status-line updates', kind: 'Sent by Antigravity while it runs', status: 'ok' }
          ]
        },
        {
          id: 'grok', name: 'Grok Build', product: 'Grok Build CLI', enabled: true, setup: 'ready',
          recordKind: 'event_total', usageMode: 'polled', usageMeasuredAt: ago(6),
          days: dailySeries({ seed: 71, days: 90, base: 110000, outShare: 0.13, cacheRead: 2.5, cacheWrite: null, missing: [4],
            today: { input: 71000, output: 11000, cacheRead: 204000, cacheWrite: null } }),
          missingNote: '25 Sep is missing: that day’s session log rotated before it was read.',
          modelShares: [['grok-code-fast-1', 0.78], ['grok-4', 0.22]],
          quota: {
            mode: 'polled', staleAfterMinutes: 180,
            note: 'Credit percentage for the current billing period, from xAI billing. It is not your subscription price.',
            windows: [{ id: 'credits', label: 'Monthly credits', short: 'Credits', basis: 'used', readings: grokCredits.readings, resets: grokCredits.resets,
              ranges: [{ id: '30d', label: '30 days', ms: 30 * DAY }, { id: '90d', label: '90 days', ms: 90 * DAY }], keptDays: 90 }]
          },
          costs: { reportedFrom: 'credits', reportedBasis: 'Usage cost reported by xAI billing', subscriptionMonthlyUsd: null },
          sources: [
            { id: 'session-log', role: 'usage', label: 'Session log', kind: 'Local Grok session files', status: 'ok' },
            { id: 'billing', role: 'quota', label: 'Billing snapshot', kind: 'Local billing log; xAI billing lookup as fallback (no model calls)', status: 'ok' }
          ]
        },
        {
          id: 'gemini', name: 'Gemini CLI', product: 'Gemini CLI', enabled: true, setup: 'not_configured',
          setupNote: 'Usage telemetry is off. Gemini’s telemetry includes account identifiers, so turning it on is your choice.',
          recordKind: 'event_total', usageMode: 'polled', usageMeasuredAt: null,
          days: [], modelShares: [],
          quota: { mode: 'unsupported', windows: [] },
          costs: { reported: null, reportedNote: 'Gemini CLI does not report a usage cost.', subscriptionMonthlyUsd: null },
          sources: [{ id: 'telemetry', role: 'usage', label: 'Local telemetry file', kind: 'Gemini CLI telemetry (opt-in)', status: 'off' }]
        }
      ]
    };
  }

  /* ---------------------------------------------------------- transforms */
  const byId = (s, id) => s.providers.find((p) => p.id === id);
  const TODAY_START_H = 7, NOW_H = 10 + 38 / 60;
  function cutUsage(p, t) {
    if (!p.days.length) return;
    const cutDay = new Date(t); cutDay.setHours(0, 0, 0, 0);
    p.days = p.days.filter((d) => d.date <= cutDay.getTime());
    const last = p.days[p.days.length - 1];
    if (last && last.date === TODAY && last.state === 'measured') {
      const h = new Date(t).getHours() + new Date(t).getMinutes() / 60;
      const f = Math.max(0, Math.min(1, (h - TODAY_START_H) / (NOW_H - TODAY_START_H)));
      ['input', 'output', 'cacheRead', 'cacheWrite'].forEach((k) => { if (last[k] != null) last[k] = Math.round((last[k] * f) / 1000) * 1000; });
    }
    if (p.usageMeasuredAt != null) p.usageMeasuredAt = Math.min(p.usageMeasuredAt, t);
  }
  function cutWindow(w, t) { w.readings = w.readings.filter((r) => r.t <= t); w.resets = w.resets.filter((r) => r <= t); }
  function cutQuota(p, t) { (p.quota.windows || []).forEach((w) => cutWindow(w, t)); }
  const codexFailure = (kind) => kind === 'quota'
    ? { providerId: 'codex', kind: 'quota', sourceIds: ['rate-limits'], message: 'Codex returned usage but no rate limits.', action: 'retry', hint: 'Usage was updated. Quota keeps its previous reading until a check succeeds.' }
    : { providerId: 'codex', kind: 'both', sourceIds: ['rate-limits', 'account-usage'], message: 'Codex didn’t respond within 30 seconds.', action: 'retry', hint: 'Usually temporary. Try again, or check that Codex opens normally in Terminal.' };

  const T = {
    cutAll: (t) => ({ id: 'cutAll', fn: (s) => s.providers.forEach((p) => { cutUsage(p, t); cutQuota(p, t); }) }),
    codexFailed: (since) => ({ id: 'codexFailed', fn: (s) => {
      const p = byId(s, 'codex');
      cutUsage(p, since); cutQuota(p, since);
      p.usageFailed = true; p.quotaFailed = true;
      p.usageCollectedAt = since; p.quotaCollectedAt = since;
      p.sources.forEach((src) => { src.status = 'failed'; src.error = `No response within 30 s at ${clock(NOW - 2 * MIN)}`; });
      s.collection.failures.push(Object.assign({ at: ago(2) }, codexFailure('both')));
    } }),
    grokBillingStale: { id: 'grokBillingStale', persistent: true, fn: (s) => {
      const p = byId(s, 'grok');
      cutQuota(p, ago(26 * 60 + 12));
      const src = p.sources.find((x) => x.id === 'billing');
      src.status = 'stale';
      src.detail = 'The billing log hasn’t changed since yesterday. The fallback lookup was skipped because no Grok sign-in was found.';
      p.quota.sourceStale = true;
    } },
    claudeCacheStale: { id: 'claudeCacheStale', persistent: true, fn: (s) => {
      const p = byId(s, 'claude');
      p.days.forEach((d) => { if (d.date === dayAt(1) || d.date === dayAt(2)) Object.assign(d, { state: 'missing', input: null, output: null, cacheRead: null, cacheWrite: null }); });
      const src = p.sources.find((x) => x.id === 'stats-cache');
      src.status = 'stale';
      src.measuredAt = at(3, 23, 58);
      src.detail = 'Claude Code hasn’t rewritten its stats cache since 26 Sep. Today’s totals come from recent sessions; 27–28 Sep are missing.';
      p.missingNote = '27–28 Sep are missing because the daily stats cache stopped updating. Today comes from recent sessions.';
    } },
    antigravityNoQuota: { id: 'antigravityNoQuota', persistent: true, fn: (s) => { byId(s, 'antigravity').quota = { mode: 'unavailable', windows: [] }; } },
    codexLimit: { id: 'codexLimit', persistent: true, fn: (s) => {
      byId(s, 'codex').quota = codexQuota({ fivePoints: [[7, 38, 90], [8, 38, 72], [9, 38, 55], [10, 38, 41]], weeklyUsedNow: 118 });
    } },
    codexWeeklyOmitted: { id: 'codexWeeklyOmitted', persistent: true, fn: (s) => {
      const w = byId(s, 'codex').quota.windows.find((x) => x.id === 'weekly');
      cutWindow(w, at(1, 19, 38));
      w.omittedNote = 'Recent rate-limit responses didn’t include the weekly window.';
    } },
    codexFiveOmittedWeeklyLimit: { id: 'codexFiveOmittedWeeklyLimit', persistent: true, fn: (s) => {
      const q = codexQuota({ fivePoints: [[7, 38, 92], [8, 38, 71]], weeklyUsedNow: 118 });
      q.windows[0].omittedNote = 'Since 08:38, rate-limit responses have included only the weekly window.';
      byId(s, 'codex').quota = q;
    } },
    grokAuth: { id: 'grokAuth', persistent: true, fn: (s) => {
      const p = byId(s, 'grok');
      cutQuota(p, at(3, 9, 9));
      p.setup = 'auth_required';
      p.quotaFailed = true;
      p.quotaCollectedAt = at(3, 9, 9);
      const src = p.sources.find((x) => x.id === 'billing');
      src.status = 'auth';
      src.detail = 'Grok sign-in not found or expired, so billing can’t be read.';
      s.collection.failures.push({ providerId: 'grok', kind: 'quota', sourceIds: ['billing'], at: ago(2), message: 'Grok needs you to sign in before billing can be read.', action: 'signin', hint: 'Run “grok login” in Terminal, then collect again. AI Usage never stores your credentials.' });
    } },
    firstRun: { id: 'firstRun', fn: (s) => {
      s.providers.forEach((p) => {
        if (p.setup === 'ready') p.setup = 'waiting';
        p.days = []; p.usageMeasuredAt = null; p.usageCollectedAt = null; p.quotaCollectedAt = null;
        (p.quota.windows || []).forEach((w) => { w.readings = []; w.resets = []; });
        p.sources.forEach((src) => { if (src.status !== 'off') src.status = 'waiting'; delete src.measuredAt; });
      });
    } }
  };

  function clock(t) { const d = new Date(t); return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; }

  /* ---------------------------------------------------------- derivation
     Freshness is evaluated separately for usage and for EACH quota window.
     Provider-level quota status is only a summary: current, stale, or mixed. */
  function finalize(s) {
    const now = s.now;
    const staleMs = 2 * s.schedule.intervalMinutes * MIN;
    s.providers.forEach((p) => {
      const last = p.days[p.days.length - 1];
      const today = last && last.date === TODAY && last.state === 'measured' ? last : null;
      p.today = today ? { input: today.input, output: today.output, cacheRead: today.cacheRead, cacheWrite: today.cacheWrite } : null;
      p.coverageStart = p.days.length ? p.days[0].date : null;

      const io = p.today ? p.today.input + p.today.output : 0;
      let acc = 0;
      p.models = p.today && io > 0 ? p.modelShares.map(([name, share], i, a) => {
        const tokens = i === a.length - 1 ? io - acc : Math.round((io * share) / 1000) * 1000;
        acc += tokens;
        return { name, tokens };
      }) : [];

      const uc = p.usageCollectedAt;
      p.usage = { recordKind: p.recordKind, measuredAt: p.usageMeasuredAt, collectedAt: uc, staleCause: null };
      if (p.setup === 'not_configured' || !p.days.length) p.usage.status = 'none';
      else if (p.usageFailed) { p.usage.status = 'stale'; p.usage.staleCause = 'failed'; }
      else if (uc == null || now - uc > staleMs) { p.usage.status = 'stale'; p.usage.staleCause = 'not_collected'; }
      else if (p.usageMode !== 'event' && now - p.usageMeasuredAt > staleMs) { p.usage.status = 'stale'; p.usage.staleCause = 'source_old'; }
      else p.usage.status = 'current';

      const q = p.quota;
      const qc = p.quotaCollectedAt;
      q.collectedAt = qc;
      const limitMs = q.staleAfterMinutes ? q.staleAfterMinutes * MIN : staleMs;
      q.windows.forEach((w) => {
        const lr = w.readings[w.readings.length - 1];
        w.percent = lr ? lr.percent : null;
        w.measuredAt = lr ? lr.t : null;
        w.resetsAt = lr ? lr.resetsAt : null;
        w.resetPassed = !!(lr && lr.resetsAt && lr.resetsAt < now);
        w.collectedAt = qc;
        if (!lr) { w.status = 'none'; w.staleCause = null; return; }
        let cause = null;
        if (p.setup === 'auth_required') cause = 'auth';
        else if (p.quotaFailed) cause = 'failed';
        else if (qc == null || now - qc > staleMs) cause = 'not_collected';
        else if (q.sourceStale || (q.mode !== 'event' && now - lr.t > limitMs)) cause = 'source_old';
        else if (w.resetPassed) cause = 'reset_passed';
        w.status = cause ? 'stale' : 'current';
        w.staleCause = cause;
      });
      const withR = q.windows.filter((w) => w.status !== 'none');
      q.measuredAt = withR.length ? Math.max(...withR.map((w) => w.measuredAt)) : null;
      q.staleCause = null;
      if (q.mode === 'unsupported') q.status = 'unsupported';
      else if (q.mode === 'unavailable') q.status = 'unavailable';
      else if (!withR.length) q.status = p.setup === 'waiting' ? 'none' : 'unavailable';
      else {
        const kinds = [...new Set(withR.map((w) => w.status))];
        q.status = kinds.length > 1 ? 'mixed' : kinds[0];
        const sw = withR.find((w) => w.status === 'stale');
        q.staleCause = sw ? sw.staleCause : null;
      }

      if (p.costs.reportedFrom) {
        const w = q.windows.find((x) => x.id === p.costs.reportedFrom);
        const lr = w && w.readings[w.readings.length - 1];
        p.costs.reported = lr && lr.costUsd != null ? {
          amountUsd: lr.costUsd, measuredAt: lr.t, basis: p.costs.reportedBasis,
          periodStart: w.resets.filter((r) => r <= lr.t).slice(-1)[0] || new Date(2026, 8, 1).getTime(), periodEnd: lr.resetsAt
        } : null;
      }

      p.sources.forEach((src) => {
        if (src.status === 'off' || src.status === 'waiting') { src.lastReadAt = null; return; }
        const read = { usage: uc, history: uc, quota: qc, both: Math.max(uc || 0, qc || 0) || null }[src.role];
        src.lastReadAt = read;
        if (src.role === 'quota' && q.measuredAt) src.measuredAt = q.measuredAt;
        if (src.role === 'usage' && p.usageMeasuredAt) src.measuredAt = p.usageMeasuredAt;
        if (src.role === 'both') src.measuredAt = Math.max(p.usageMeasuredAt || 0, q.measuredAt || 0) || null;
      });
    });
    return s;
  }

  /* ---------------------------------------------------------- scenarios */
  const SCENARIOS = [
    { id: 'healthy', label: 'Healthy', summary: 'Collector running, last check succeeded, every measurement current. Gemini not set up.', transforms: [] },
    { id: 'collecting', label: 'Collecting', summary: 'The hourly check is running. Values from the 09:38 check stay visible while providers are read.',
      collection: { activity: 'collecting', lastAttemptAt: ago(62), lastSuccessAt: ago(62), nextScheduledAt: ahead(58) },
      transforms: [T.cutAll(ago(62))] },
    { id: 'paused', label: 'Scheduled checks paused', summary: 'Collector running, schedule paused since 07:10. Values age normally; Collect now still works.',
      schedule: { state: 'paused', pausedAt: ago(210) }, collection: { lastAttemptAt: ago(214), lastSuccessAt: ago(214), nextScheduledAt: null },
      transforms: [T.cutAll(ago(214))] },
    { id: 'stopped', label: 'Collector stopped', summary: 'The background collector is not running. Cached data shown with its age; Start is offered.',
      service: { state: 'stopped', since: ago(410) }, collection: { lastAttemptAt: ago(412), lastSuccessAt: ago(412), nextScheduledAt: null },
      transforms: [T.cutAll(ago(412))] },
    { id: 'partial', label: 'Partial provider failure', summary: 'The 10:38 check partly succeeded: Codex did not respond, so Codex keeps its 09:38 values (563K, 55% remaining). Others are current.',
      collection: { lastAttemptResult: 'partial' }, collectOutcome: 'partial', transforms: [T.codexFailed(ago(62))] },
    { id: 'stale-quota', label: 'Stale quota, current usage', summary: 'Grok usage is current but its billing snapshot is 26 h old. Claude’s daily stats cache stopped on 26 Sep while session data stays current.',
      transforms: [T.grokBillingStale, T.claudeCacheStale] },
    { id: 'weekly-stale', label: 'Weekly reading stale, 5-hour current', summary: 'Codex’s 5-hour reading is current, but recent rate-limit responses omitted the weekly window; its last reading is from yesterday 19:38.',
      transforms: [T.codexWeeklyOmitted] },
    { id: 'five-stale-weekly-limit', label: 'Weekly limit reached, 5-hour stale', summary: 'Codex’s weekly allowance is used up and current. The 5-hour window hasn’t been reported since 08:38, so it is stale. The weekly warning stays visible.',
      transforms: [T.codexFiveOmittedWeeklyLimit] },
    { id: 'quota-unavailable', label: 'Quota unavailable', summary: 'Antigravity reports usage but no quota reading. Shown as unavailable, never as 0%.', transforms: [T.antigravityNoQuota] },
    { id: 'limit-reached', label: 'Weekly limit reached', summary: 'Codex weekly allowance is used up while the 5-hour window still shows 41% remaining. Both readings current. A usage state, not a service problem.',
      transforms: [T.codexLimit] },
    { id: 'auth-required', label: 'Sign-in required', summary: 'Grok’s billing lookup needs a sign-in. Usage from local sessions still works; quota is 3 days old.',
      collection: { lastAttemptResult: 'partial' }, collectOutcome: 'partial', transforms: [T.grokAuth] },
    { id: 'first-run', label: 'First run, no data', summary: 'Collector just installed. The first check hasn’t finished; nothing has been measured yet.',
      service: { state: 'running', since: ago(0.5) },
      collection: { lastAttemptAt: null, lastAttemptResult: null, lastSuccessAt: null, nextScheduledAt: ahead(1) },
      transforms: [T.firstRun] }
  ];

  function build(sc, overrides) {
    const s = baseline();
    Object.assign(s.service, sc.service || {});
    Object.assign(s.schedule, sc.schedule || {});
    Object.assign(s.collection, sc.collection || {});
    if (sc.collectOutcome) s.collectOutcome = sc.collectOutcome;
    (overrides || sc.transforms).forEach((t) => t.fn(s));
    s.providers.forEach((p) => {
      if (p.usageCollectedAt === undefined) p.usageCollectedAt = p.days.length ? s.collection.lastSuccessAt : null;
      if (p.quotaCollectedAt === undefined) p.quotaCollectedAt = s.collection.lastSuccessAt;
      p.usageFailed = !!p.usageFailed; p.quotaFailed = !!p.quotaFailed;
    });
    s.id = sc.id; s.label = sc.label; s.summary = sc.summary;
    return s;
  }

  function getSnapshot(id) {
    const sc = SCENARIOS.find((x) => x.id === id) || SCENARIOS[0];
    return finalize(build(sc));
  }

  /* ---------------------------------------------------------- Collect now
     The result is merged into the ACTIVE snapshot, source by source:
     - providers turned off (or not set up) are not attempted and keep cached data;
     - a successful source replaces its measurements and collection time;
     - a failed source keeps its previous measurements and timestamps and records the failed attempt;
     - usage and quota are independent: a quota-only failure still updates usage.
     "Truth" for successful sources comes from the scenario's persistent source
     conditions (stale billing, stale cache, sign-in, missing windows, limits). */
  const OUTCOMES = ['success', 'partial', 'partial-quota', 'failure'];
  function applyCollectResult(snap, outcome) {
    const sc = SCENARIOS.find((x) => x.id === snap.id) || SCENARIOS[0];
    const s = clone(snap);
    const c = s.collection;
    const attempted = s.providers.filter((p) => p.enabled && p.setup !== 'not_configured');
    const skipped = s.providers.filter((p) => !p.enabled).map((p) => p.name);
    Object.assign(c, { activity: 'idle', progress: null, lastAttemptAt: NOW });
    const running = s.service.state === 'running';
    c.nextScheduledAt = running && s.schedule.state === 'active' ? NOW + s.schedule.intervalMinutes * MIN : null;

    if (outcome === 'failure') {
      c.lastAttemptResult = 'failed';
      c.failures = [{ providerId: null, kind: 'all', at: NOW, message: 'The check couldn’t save its results.', action: 'details', hint: 'Check free disk space, then try again. The collector log has the exact error. Nothing was changed.' }];
      c.summary = { outcome: 'failed', providersAttempted: attempted.length, providersUpdated: 0, providersPartly: 0, providersFailed: attempted.length, skipped };
      return finalize(s);
    }

    const fresh = build({ id: sc.id, label: sc.label, summary: sc.summary }, sc.transforms.filter((t) => t.persistent));
    const failures = fresh.collection.failures.filter((f) => attempted.some((p) => p.id === f.providerId)).map((f) => Object.assign({}, f, { at: NOW }));
    if (attempted.some((p) => p.id === 'codex')) {
      if (outcome === 'partial') failures.push(Object.assign({ at: NOW }, codexFailure('both')));
      if (outcome === 'partial-quota') failures.push(Object.assign({ at: NOW }, codexFailure('quota')));
    }
    let full = 0, partly = 0, failed = 0;
    attempted.forEach((p) => {
      const fp = fresh.providers.find((x) => x.id === p.id);
      const fs = failures.filter((f) => f.providerId === p.id);
      const quotaFail = fs.some((f) => f.kind === 'quota' || f.kind === 'both');
      const usageFail = fs.some((f) => f.kind === 'usage' || f.kind === 'both');
      const failedIds = fs.reduce((a, f) => a.concat(f.sourceIds || []), []);
      p.setup = fp.setup;
      if (!usageFail) {
        p.days = fp.days;
        p.usageMeasuredAt = p.usageMode === 'polled' ? Math.max(fp.usageMeasuredAt || 0, NOW - MIN) : fp.usageMeasuredAt;
        p.usageCollectedAt = NOW;
        p.usageFailed = false;
        p.missingNote = fp.missingNote;
      } else p.usageFailed = true;
      if (!quotaFail) {
        p.quota = fp.quota;
        p.quotaCollectedAt = NOW;
        p.quotaFailed = false;
      } else p.quotaFailed = true;
      const oldSources = p.sources;
      p.sources = fp.sources.map((src) => {
        if (!failedIds.includes(src.id) || src.status === 'auth') return src;
        const old = oldSources.find((o) => o.id === src.id) || src;
        return Object.assign({}, old, { status: 'failed', error: `Failed at ${clock(NOW)} — ${fs[0].message}` });
      });
      if (!fs.length) full++; else if (quotaFail && usageFail) failed++; else partly++;
    });
    c.failures = failures;
    const anyOk = full + partly > 0;
    if (anyOk) c.lastSuccessAt = NOW;
    c.lastAttemptResult = !anyOk && attempted.length ? 'failed' : failures.length ? 'partial' : 'success';
    c.summary = { outcome: c.lastAttemptResult, providersAttempted: attempted.length, providersUpdated: full, providersPartly: partly, providersFailed: failed, skipped };
    return finalize(s);
  }

  window.AIU_FIXTURES = {
    NOW, MIN, HOUR, DAY, TODAY,
    scenarios: SCENARIOS.map(({ id, label, summary }) => ({ id, label, summary })),
    outcomes: OUTCOMES,
    getSnapshot,
    applyCollectResult,
    refresh: finalize,
    collectOrder: ['codex', 'claude', 'antigravity', 'grok']
  };
})();
