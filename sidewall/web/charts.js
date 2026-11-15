/* Interactive chart toolkit, shared by the tool and the HTML report.
 *
 * A chart is a plain spec (cfg) drawn as SVG. Two renderings:
 *  - live:   read-out on hover (crosshair, dots, values; linked across charts with the
 *            same cfg.sync), drag-box zoom, Shift+drag pan, Ctrl+wheel zoom, double-click
 *            reset, click to pin readings, legend toggles, colour-by switch, full screen,
 *            data table / CSV / copy, PNG / SVG export.
 *  - static: legend and colour bar drawn inside the SVG; used for the PDF, exports and
 *            as the picture shown when scripts are blocked.
 * Specs are JSON-serialisable (no functions except optional app callbacks), so the HTML
 * report can embed them and bring the charts to life without the tool. */
'use strict';

// ---------------------------------------------------------------- small helpers
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const f = (v, d = 1) => (v == null || !isFinite(v) ? '–' : Number(v).toFixed(d));
const fs = (v, d = 1) => (v == null || !isFinite(v) ? '–' : (v > 0 ? '+' : '') + Number(v).toFixed(d));
function download(blob, name) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1500);
}
const csvCell = (v) => (v == null ? '' : typeof v === 'number' ? (isFinite(v) ? +v.toFixed(6) : '') : /[",\n]/.test(String(v)) ? `"${String(v).replace(/"/g, '""')}"` : String(v));
const toCSV = (rows) => rows.map((r) => r.map(csvCell).join(',')).join('\n');
const slug = (s) => String(s).replace(/\.[^.]+$/, '').replace(/[^\w.-]+/g, '_').slice(0, 80);
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); return true; } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (e2) { ok = false; }
    ta.remove();
    return ok;
  }
}

function niceTicks(min, max, count = 5) {
  const span = max - min;
  if (!(span > 0)) return [min];
  const raw = span / count, mag = 10 ** Math.floor(Math.log10(raw)), e = raw / mag;
  const step = (e >= 7.5 ? 10 : e >= 3.5 ? 5 : e >= 1.5 ? 2 : 1) * mag;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-6; v += step) out.push(+v.toFixed(10));
  return out;
}
function niceDomain(min, max, count = 5, pad = 0.04) {
  const span = max - min || 1;
  min -= span * pad; max += span * pad;
  const t = niceTicks(min, max, count), step = t.length > 1 ? t[1] - t[0] : span;
  return [Math.floor(min / step) * step, Math.ceil(max / step) * step];
}
const extent = (arrs) => { let a = Infinity, b = -Infinity; for (const r of arrs) for (const v of r) if (isFinite(v)) { if (v < a) a = v; if (v > b) b = v; } return [a, b]; };
const stepDecimals = (step) => { const s = String(+step.toPrecision(6)); return s.includes('e') ? 6 : (s.split('.')[1] || '').length; };
const tickText = (v, step, pct) => `${(+v).toFixed(stepDecimals(step))}${pct ? '%' : ''}`;

/** Google "turbo" colormap (polynomial fit) */
function turboRGB(t) {
  t = Math.min(1, Math.max(0, t));
  const r = 0.13572138 + t * (4.6153926 + t * (-42.66032258 + t * (132.13108234 + t * (-152.94239396 + t * 59.28637943))));
  const g = 0.09140261 + t * (2.19418839 + t * (4.84296658 + t * (-14.18503333 + t * (4.27729857 + t * 2.82956604))));
  const b = 0.1066733 + t * (12.64194608 + t * (-60.58204836 + t * (110.36276771 + t * (-89.90310912 + t * 27.34824973))));
  const c = (v) => Math.round(255 * Math.min(1, Math.max(0, v)));
  return [c(r), c(g), c(b)];
}
const turboCss = (t) => `rgb(${turboRGB(t).join(',')})`;

/** linear interpolation at v in ascending xp */
function interpAt(v, xp, fp) {
  const n = xp.length;
  if (!n || v < xp[0] || v > xp[n - 1]) return NaN;
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xp[mid] <= v) lo = mid; else hi = mid; }
  return xp[hi] === xp[lo] ? fp[lo] : fp[lo] + ((v - xp[lo]) / (xp[hi] - xp[lo])) * (fp[hi] - fp[lo]);
}

// ---------------------------------------------------------------- registry
const CH = new Map();
let chSeq = 0;
function newState(cfg, kind, extra = {}) {
  const id = `ch${++chSeq}`;
  const st = { id, kind, cfg, view: { xd: null, yd: null }, hidden: new Set(cfg.hidden || []), pins: [], colorBy: cfg.colorBy, showData: false, ...extra };
  CH.set(id, st);
  return st;
}
/** forget charts no longer on the page (call before re-rendering a view) */
function clearCharts() { for (const [id, st] of CH) if (!st.el || !st.el.isConnected) CH.delete(id); }
const groupOf = (s) => s.group || s.name;
const visibleSeries = (st) => st.cfg.series.filter((s) => !st.hidden.has(groupOf(s)));
const idOfChart = (html) => (/data-chart="(ch\d+)"/.exec(html) || [])[1];

function lineChart(cfg) { return chartHTML(newState(cfg, 'line')); }
function barChart(cfg) { return chartHTML(newState(cfg, 'bar')); }
const chartHTML = (st) => `<div class="ichart" data-chart="${st.id}">${innerHTML(st)}</div>`;
/** static SVG (legend + colour bar inside) of a chart made by lineChart/barChart */
function chartStatic(html) { const st = CH.get(idOfChart(html)); return st ? (st.kind === 'line' ? lineSVG(st, true) : barSVG(st, true)) : ''; }
const svgOf = chartStatic;

function colorInfo(st) {
  const o = st.cfg.colorOptions;
  if (!o) return null;
  const key = o[st.colorBy] ? st.colorBy : Object.keys(o)[0], c = o[key];
  let lo = Infinity, hi = -Infinity;
  for (const v of c.values) if (isFinite(v)) { if (v < lo) lo = v; if (v > hi) hi = v; }
  return { key, ...c, lo, hi };
}

// ---------------------------------------------------------------- legend inside SVG (static)
function svgLegend(items, x0, y0, xMax, hidden) {
  const ink2 = css('--ink2');
  let x = x0, y = y0, g = '';
  for (const it of items) {
    if (hidden && hidden.has(it.name)) continue;
    const label = it.name.length > 48 ? `${it.name.slice(0, 47)}…` : it.name;
    const w = 26 + label.length * 6.1 + 14;
    if (x + w > xMax && x > x0) { x = x0; y += 17; }
    if (it.dot) g += `<circle cx="${x + 9}" cy="${y}" r="3.5" fill="${it.color}"/>`;
    else if (it.gradient) [0, 0.25, 0.5, 0.75].forEach((t, k) => { g += `<line x1="${x + k * 5}" x2="${x + k * 5 + 5}" y1="${y}" y2="${y}" stroke="${turboCss(t + 0.125)}" stroke-width="4"/>`; });
    else if (it.box) g += `<rect x="${x + 2}" y="${y - 5}" width="16" height="10" rx="2" fill="${it.color}"/>`;
    else g += `<line x1="${x}" x2="${x + 20}" y1="${y}" y2="${y}" stroke="${it.color}" stroke-width="${it.opacity ? 1.6 : 2.6}" ${it.dash ? 'stroke-dasharray="5 3"' : ''} opacity="${it.opacity ?? 1}" stroke-linecap="round"/>`;
    g += `<text x="${x + 26}" y="${y}" dominant-baseline="middle" font-size="11.5" fill="${ink2}">${esc(label)}</text>`;
    x += w;
  }
  return { g, h: y - y0 + 22 };
}

// ---------------------------------------------------------------- line chart SVG
function lineSVG(st, isStatic) {
  const cfg = st.cfg, uid = st.id + (isStatic ? 's' : '');
  const cb = colorInfo(st), cbH = cb && cfg.colorbar !== false ? 62 : 0;
  const W = cfg.w || 560, H = (cfg.h || 420) + cbH;
  const m = { l: 58, r: 18, t: 10, b: 44 + cbH, ...(cfg.m || {}) };
  const leg = isStatic && cfg.legend ? svgLegend(cfg.legend, m.l, 14, W - m.r, st.hidden) : null;
  if (leg) m.t += leg.h;
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const ser = visibleSeries(st), base = ser.length ? ser : cfg.series;
  const autoX = cfg.xDomain || niceDomain(...extent(base.map((s) => s.x).concat((cfg.vlines || []).map((v) => [v]))));
  const autoY = cfg.yDomain || niceDomain(...extent(base.map((s) => s.y)), 6, 0.02);
  const xd = st.view.xd || autoX, yd = st.view.yd || autoY;
  const X = (v) => m.l + ((v - xd[0]) / (xd[1] - xd[0])) * pw;
  const Y = cfg.yFlip ? (v) => m.t + ((v - yd[0]) / (yd[1] - yd[0])) * ph : (v) => m.t + ph - ((v - yd[0]) / (yd[1] - yd[0])) * ph;
  const Xinv = (px) => xd[0] + ((px - m.l) / pw) * (xd[1] - xd[0]);
  const Yinv = cfg.yFlip ? (py) => yd[0] + ((py - m.t) / ph) * (yd[1] - yd[0]) : (py) => yd[0] + ((m.t + ph - py) / ph) * (yd[1] - yd[0]);
  if (!isStatic) st.geom = { W, H, m, pw, ph, xd, yd, X, Y, Xinv, Yinv, autoX, autoY };
  const grid = css('--grid'), ink2 = css('--ink2'), muted = css('--muted'), zone = css('--zone'), ink = css('--ink'), card = css('--card'), accent = css('--accent') || '#2a78d6';
  const pct = cfg.yFmt === 'pct';
  let g = '';
  for (const b of cfg.bands || []) {
    const lo = Math.max(b.y0, yd[0]), hi = Math.min(b.y1, yd[1]);
    if (hi <= lo) continue;
    const ya = Y(lo), yb = Y(hi), y0 = Math.max(ya, yb), y1 = Math.min(ya, yb);
    if (b.fill !== false) g += `<rect x="${m.l}" y="${y1}" width="${pw}" height="${y0 - y1}" fill="${zone}"/>`;
    if (b.label) g += `<text x="${m.l + pw - 6}" y="${(y0 + y1) / 2}" text-anchor="end" dominant-baseline="middle" font-size="11" fill="${muted}">${esc(b.label)}</text>`;
  }
  const xt = niceTicks(xd[0], xd[1], cfg.xTicks || 6), yt = niceTicks(yd[0], yd[1], cfg.yTicks || 6);
  const xs = xt.length > 1 ? xt[1] - xt[0] : 1, ys = yt.length > 1 ? yt[1] - yt[0] : 1;
  for (const v of xt) {
    g += `<line x1="${X(v)}" x2="${X(v)}" y1="${m.t}" y2="${m.t + ph}" stroke="${grid}"/>`;
    g += `<text x="${X(v)}" y="${m.t + ph + 16}" text-anchor="middle" font-size="11.5" fill="${ink2}">${tickText(v, xs)}</text>`;
  }
  for (const v of yt) {
    g += `<line x1="${m.l}" x2="${m.l + pw}" y1="${Y(v)}" y2="${Y(v)}" stroke="${grid}"/>`;
    g += `<text x="${m.l - 8}" y="${Y(v)}" text-anchor="end" dominant-baseline="middle" font-size="11.5" fill="${ink2}">${tickText(v, ys, pct)}</text>`;
  }
  for (const v of cfg.vlines || []) if (v >= xd[0] && v <= xd[1]) g += `<line x1="${X(v)}" x2="${X(v)}" y1="${m.t}" y2="${m.t + ph}" stroke="${muted}" stroke-width="1"/>`;
  g += `<text x="${m.l + pw / 2}" y="${m.t + ph + 34}" text-anchor="middle" font-size="12" fill="${ink2}">${esc(cfg.xLabel || '')}</text>`;
  g += `<text transform="translate(14 ${m.t + ph / 2}) rotate(-90)" text-anchor="middle" font-size="12" fill="${ink2}">${esc(cfg.yLabel || '')}</text>`;
  if (leg) g += leg.g;
  g += `<clipPath id="${uid}c"><rect x="${m.l}" y="${m.t}" width="${pw}" height="${ph}"/></clipPath><g clip-path="url(#${uid}c)"><g class="plotg">`;
  const qcol = cb ? (v) => turboCss(Math.round(((v - cb.lo) / (cb.hi - cb.lo || 1)) * 48) / 48) : null;
  for (const s of ser) {
    const n = s.x.length, grp = esc(groupOf(s));
    if (s.dots) {
      let d = '';
      for (let i = 0; i < n; i++) { const cx = X(s.x[i]), cy = Y(s.y[i]); if (isFinite(cx) && isFinite(cy)) d += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${s.r || 1.6}"/>`; }
      g += `<g data-g="${grp}" fill="${s.color}" opacity="${s.opacity ?? 0.4}">${d}</g>`;
      continue;
    }
    if (s.colorKey && cb) { // coloured by the selected quantity: runs of equal colour
      let run = null, d = '', out = '';
      const flush = () => { if (run) out += `<path d="${d}" fill="none" stroke="${run}" stroke-width="${s.width || 5}" stroke-linecap="round" stroke-linejoin="round"/>`; };
      for (let i = 1; i < n; i++) {
        const c = qcol(cb.values[i]);
        if (c !== run) { flush(); run = c; d = `M${X(s.x[i - 1]).toFixed(1)} ${Y(s.y[i - 1]).toFixed(1)}`; }
        d += `L${X(s.x[i]).toFixed(1)} ${Y(s.y[i]).toFixed(1)}`;
      }
      flush();
      g += `<g data-g="${grp}">${out}</g>`;
      continue;
    }
    let d = '', pen = false;
    for (let i = 0; i < n; i++) {
      const px = X(s.x[i]), py = Y(s.y[i]);
      if (!isFinite(px) || !isFinite(py)) { pen = false; continue; }
      d += `${pen ? 'L' : 'M'}${px.toFixed(1)} ${py.toFixed(1)}`; pen = true;
    }
    g += `<path data-g="${grp}" d="${d}" fill="none" stroke="${s.color}" stroke-width="${s.width || 2}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ''} opacity="${s.opacity ?? 1}" stroke-linejoin="round" stroke-linecap="round"/>`;
  }
  g += '</g></g>';
  const markers = (cfg.markers || []).filter((mk) => !mk.group || !st.hidden.has(mk.group));
  if (cb && cfg.markColorMax) {
    const s = cfg.series.find((x) => x.colorKey);
    if (s && !st.hidden.has(groupOf(s))) {
      let k = 0;
      cb.values.forEach((v, i) => { if (v > cb.values[k]) k = i; });
      markers.push({ x: s.x[k], y: s.y[k], color: turboCss(1), label: `max: ${f(cb.values[k], cb.dec ?? 1)} ${cb.unit}` });
    }
  }
  for (const mk of markers) {
    if (mk.x < xd[0] || mk.x > xd[1] || mk.y < Math.min(...yd) || mk.y > Math.max(...yd)) continue;
    g += `<circle cx="${X(mk.x)}" cy="${Y(mk.y)}" r="5" fill="${mk.color}" stroke="${card}" stroke-width="2"/>`;
    if (mk.label) {
      const right = mk.side !== 'left';
      g += `<text x="${X(mk.x) + (right ? 9 : -9)}" y="${Y(mk.y)}" text-anchor="${right ? 'start' : 'end'}" dominant-baseline="middle" font-size="11.5" fill="${ink}" paint-order="stroke" stroke="${card}" stroke-width="3">${esc(mk.label)}</text>`;
    }
  }
  for (const rl of cfg.refLines || []) { // fixed dashed reference lines (static stand-in for drag handles)
    if (!(rl.y >= Math.min(...yd) && rl.y <= Math.max(...yd))) continue;
    const y = Y(rl.y).toFixed(1), left = rl.side === 'left';
    g += `<line x1="${m.l}" x2="${m.l + pw}" y1="${y}" y2="${y}" stroke="${rl.color}" stroke-width="1.4" stroke-dasharray="6 4"/>`;
    if (rl.label) g += `<text x="${left ? m.l + 6 : m.l + pw - 6}" y="${(+y - 5).toFixed(1)}" text-anchor="${left ? 'start' : 'end'}" font-size="11" fill="${rl.color}" paint-order="stroke" stroke="${card}" stroke-width="3">${esc(rl.label)}</text>`;
  }
  for (const t of cfg.texts || []) {
    const px = t.px != null ? t.px : X(t.x);
    let py = (t.py != null ? t.py : Y(t.y)) + (t.dy || 0);
    if (t.keepIn) py = Math.max(m.t + 7, Math.min(m.t + ph - 7, py)); // stay inside the plot
    g += `<text x="${px}" y="${py}" text-anchor="${t.anchor || 'start'}" dominant-baseline="middle" font-size="${t.size || 11.5}" font-weight="${t.bold ? 650 : 400}" fill="${t.color || ink2}" paint-order="stroke" stroke="${card}" stroke-width="3">${esc(t.text)}</text>`;
  }
  if (cb && cbH) { // colour bar under the x axis
    const y0 = m.t + ph + 48, N = 64;
    for (let i = 0; i < N; i++) g += `<rect x="${(m.l + (i * pw) / N).toFixed(2)}" y="${y0}" width="${(pw / N + 0.6).toFixed(2)}" height="11" fill="${turboCss((i + 0.5) / N)}"/>`;
    const ct = niceTicks(cb.lo, cb.hi, 4).filter((v) => v >= cb.lo && v <= cb.hi), cs = ct.length > 1 ? ct[1] - ct[0] : 1;
    for (const v of ct) {
      const x = m.l + ((v - cb.lo) / (cb.hi - cb.lo || 1)) * pw;
      g += `<line x1="${x}" x2="${x}" y1="${y0 + 11}" y2="${y0 + 15}" stroke="${ink2}"/><text x="${x}" y="${y0 + 26}" text-anchor="middle" font-size="11" fill="${ink2}">${tickText(v, cs)}</text>`;
    }
    g += `<text x="${m.l + pw / 2}" y="${y0 + 44}" text-anchor="middle" font-size="12" fill="${ink2}">${esc(cb.name)} (${esc(cb.unit)}): blue = less, red = more (${f(cb.lo, cb.dec ?? 1)} to ${f(cb.hi, cb.dec ?? 1)})</text>`;
  }
  if (!isStatic) {
    for (const [i, py] of st.pins.entries()) {
      if (py < Math.min(...yd) || py > Math.max(...yd)) continue;
      const y = Y(py);
      g += `<line x1="${m.l}" x2="${m.l + pw}" y1="${y}" y2="${y}" stroke="${accent}" stroke-width="1.2" stroke-dasharray="2 3"/>`;
      for (const v of valuesAt(st, py)) if (v.x != null && isFinite(v.x) && v.x >= xd[0] && v.x <= xd[1]) g += `<circle cx="${X(v.x)}" cy="${y}" r="3.5" fill="${v.color}" stroke="${card}" stroke-width="1.5"/>`;
      g += `<text x="${m.l + 4}" y="${y - 5}" font-size="10.5" font-weight="650" fill="${accent}" paint-order="stroke" stroke="${card}" stroke-width="3">P${i + 1}</text>`;
    }
    g += `<g class="hdots"></g><line class="hx" x1="${m.l}" x2="${m.l + pw}" y1="0" y2="0" stroke="${muted}" stroke-dasharray="3 3" visibility="hidden"/>`;
    g += `<rect class="zbox" x="0" y="0" width="0" height="0" fill="${accent}" fill-opacity="0.12" stroke="${accent}" stroke-width="1" visibility="hidden"/>`;
    g += `<rect class="hit" x="${m.l}" y="${m.t}" width="${pw}" height="${ph}" fill="transparent"/>`;
    for (const hd of cfg.handles || []) {
      if (!(hd.y >= Math.min(...yd) && hd.y <= Math.max(...yd))) continue;
      g += `<g class="handle" data-key="${hd.key}" transform="translate(0 ${Y(hd.y).toFixed(1)})">
        <line x1="${m.l}" x2="${m.l + pw}" y1="0" y2="0" stroke="${hd.color}" stroke-width="1.6" stroke-dasharray="6 4"/>
        <line x1="${m.l}" x2="${m.l + pw}" y1="0" y2="0" stroke="transparent" stroke-width="14"/>
        <rect x="${m.l + pw - 30}" y="-7" width="26" height="14" rx="7" fill="${hd.color}"/>
        <text x="${m.l + pw - 17}" y="0.5" text-anchor="middle" dominant-baseline="middle" font-size="10" fill="#fff">⇕</text>
        <text class="hl" x="${m.l + pw - 36}" y="-7" text-anchor="end" font-size="11" fill="${ink}" paint-order="stroke" stroke="${card}" stroke-width="3">${esc(hd.label)} · ${hd.y.toFixed(1)} mm</text></g>`;
    }
  }
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(cfg.title || '')}" font-family="system-ui, sans-serif">${g}</svg>`;
}

// ---------------------------------------------------------------- bar chart SVG
function barSVG(st, isStatic) {
  const cfg = st.cfg, W = cfg.w || 560, rows = cfg.rows;
  const groups = cfg.groups.filter((gr) => !st.hidden.has(gr.name));
  const bh = cfg.barH || 16, gap = 3, rowGap = 16, m = { l: cfg.ml || 120, r: 60, t: 6, b: 34 };
  const leg = isStatic && cfg.legend ? svgLegend(cfg.legend, 8, 14, W - m.r, st.hidden) : null;
  if (leg) m.t += leg.h;
  const ng = Math.max(1, groups.length), rowH = ng * (bh + gap) - gap;
  const H = m.t + rows.length * (rowH + rowGap) - rowGap + m.b, pw = W - m.l - m.r;
  const vals = groups.flatMap((gr) => gr.values).filter((v) => v != null && isFinite(v));
  const lo = Math.min(0, ...vals), hi = Math.max(0, ...vals) || 1;
  const nd = niceDomain(lo, hi, 5, 0.02), xd = [lo >= 0 ? 0 : nd[0], hi <= 0 ? 0 : nd[1]];
  const X = (v) => m.l + ((v - xd[0]) / (xd[1] - xd[0])) * pw;
  const grid = css('--grid'), ink2 = css('--ink2'), ink = css('--ink'), muted = css('--muted');
  const xt = niceTicks(xd[0], xd[1], 5), xs = xt.length > 1 ? xt[1] - xt[0] : 1;
  let g = '';
  for (const v of xt) {
    g += `<line x1="${X(v)}" x2="${X(v)}" y1="${m.t}" y2="${H - m.b}" stroke="${grid}"/>`;
    g += `<text x="${X(v)}" y="${H - m.b + 16}" text-anchor="middle" font-size="11.5" fill="${ink2}">${tickText(v, xs)}</text>`;
  }
  g += `<line x1="${X(0)}" x2="${X(0)}" y1="${m.t}" y2="${H - m.b}" stroke="${muted}"/>`;
  rows.forEach((row, i) => {
    const y0 = m.t + i * (rowH + rowGap);
    g += `<text x="${m.l - 8}" y="${y0 + rowH / 2}" text-anchor="end" dominant-baseline="middle" font-size="12" fill="${ink}">${esc(row.label)}</text>`;
    groups.forEach((gr, j) => {
      const v = gr.values[i];
      if (v == null || !isFinite(v)) return;
      const y = y0 + j * (bh + gap), x0 = X(Math.min(0, v)), x1 = X(Math.max(0, v));
      const tip = `${row.full || row.label}${groups.length > 1 || gr.name !== cfg.title ? ` · ${gr.name}` : ''}: ${f(v, cfg.dec ?? 1)}${cfg.unit || ''}`;
      g += `<rect data-g="${esc(row.group || gr.name)}" data-tip="${esc(tip)}" x="${x0}" y="${y}" width="${Math.max(1, x1 - x0)}" height="${bh}" rx="3" fill="${gr.colors ? gr.colors[i] : gr.color}"><title>${esc(tip)}</title></rect>`;
      g += `<text x="${v >= 0 ? x1 + 5 : x0 - 5}" y="${y + bh / 2}" text-anchor="${v >= 0 ? 'start' : 'end'}" dominant-baseline="middle" font-size="11.5" fill="${ink}">${f(v, cfg.dec ?? 1)}${esc(cfg.unit || '')}</text>`;
    });
  });
  g += `<text x="${m.l + pw / 2}" y="${H - 4}" text-anchor="middle" font-size="12" fill="${ink2}">${esc(cfg.xLabel || '')}</text>`;
  if (leg) g += leg.g;
  if (!isStatic) st.geom = { W, H, m, pw };
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(cfg.title || '')}" font-family="system-ui, sans-serif">${g}</svg>`;
}

// ---------------------------------------------------------------- values for read-outs
function sortedSeries(st) {
  if (!st._sorted) {
    st._sorted = st.cfg.series.map((s) => {
      if (!s.hover) return null;
      const idx = Array.from({ length: s.y.length }, (_, i) => i).filter((i) => isFinite(s.y[i]) && isFinite(s.x[i])).sort((a, b) => s.y[a] - s.y[b]);
      return { s, idx, y: idx.map((i) => s.y[i]), x: idx.map((i) => s.x[i]) };
    });
  }
  return st._sorted;
}
/** values of every visible hover series at height yv */
function valuesAt(st, yv) {
  const cb = colorInfo(st), out = [];
  for (const o of sortedSeries(st)) {
    if (!o || st.hidden.has(groupOf(o.s))) continue;
    const x = interpAt(yv, o.y, o.x);
    out.push({ name: o.s.name, color: o.s.tipColor || o.s.color, x, unit: o.s.unit || '', dec: o.s.dec ?? 1 });
    if (o.s.colorKey && cb) {
      const v = interpAt(yv, o.y, o.idx.map((i) => cb.values[i]));
      out.push({ name: cb.name, color: isFinite(v) ? turboCss((v - cb.lo) / (cb.hi - cb.lo || 1)) : css('--muted'), x: null, value: v, unit: cb.unit, dec: cb.dec ?? 1 });
    }
  }
  return out;
}
const yText = (st, yv) => (st.cfg.yFmt === 'pct' ? `${f(yv, 0)}%` : `${f(yv, 1)}${st.cfg.yUnit || ''}`);

// ---------------------------------------------------------------- toolbar, legend, pins, data
const SYNC_NOTE = 'Hover to read values · drag a box to zoom · Shift+drag to pan · Ctrl+wheel to zoom · double-click to reset · click to pin a reading';
function toolbarHTML(st) {
  const cfg = st.cfg, line = st.kind === 'line', cb = line ? colorInfo(st) : null;
  const chip = (it) => {
    const off = st.hidden.has(it.name);
    const sw = it.gradient ? `background:linear-gradient(90deg,${turboCss(0.05)},${turboCss(0.5)},${turboCss(0.95)})`
      : it.dot ? `background:${it.color};width:8px;height:8px;border-radius:50%`
        : it.dash ? `background:repeating-linear-gradient(90deg,${it.color} 0 5px,transparent 5px 8px)`
          : it.box ? `background:${it.color};height:9px;border-radius:2px` : `background:${it.color};opacity:${it.opacity ?? 1}`;
    return `<button type="button" class="ich-chip${off ? ' off' : ''}" data-leg="${esc(it.name)}" title="Click to ${off ? 'show' : 'hide'}"><i style="${sw}"></i>${esc(it.name)}</button>`;
  };
  const zoomed = line && (st.view.xd || st.view.yd);
  return `<div class="ich-bar">
    <div class="ich-legend">${(cfg.legend || []).map(chip).join('')}</div>
    <div class="ich-tools">
      ${cb ? `<label class="ich-sel" title="What the colours show">Colour <select data-colorby>${Object.entries(cfg.colorOptions).map(([k, o]) => `<option value="${k}" ${k === cb.key ? 'selected' : ''}>${esc(o.name)}</option>`).join('')}</select></label>` : ''}
      ${line ? `<button type="button" data-act="zin" title="Zoom in">＋</button><button type="button" data-act="zout" title="Zoom out">－</button><button type="button" data-act="reset" title="Reset zoom (or double-click the chart)" ${zoomed ? 'class="on"' : ''}>⟲</button>` : ''}
      <button type="button" data-act="full" title="Open this chart large (Esc to close)">Full screen</button>
      <button type="button" data-act="data" title="Show the numbers" ${st.showData ? 'class="on"' : ''}>Data</button>
      <button type="button" data-act="png" title="Save as PNG picture">PNG</button>
      <button type="button" data-act="svg" title="Save as SVG (vector, for slides)">SVG</button>
    </div></div>${zoomed ? '<div class="ich-note">Zoomed in · double-click or ⟲ to reset</div>' : ''}`;
}

function pinsHTML(st) {
  if (st.kind !== 'line' || !st.pins.length) return st.kind === 'line' ? `<div class="ich-hint">${SYNC_NOTE}</div>` : '';
  const cols = valuesAt(st, st.pins[0]).map((v) => `${v.name}${v.unit ? ` (${v.unit})` : ''}`);
  const rows = st.pins.map((py, i) => {
    const vals = valuesAt(st, py);
    return `<tr><td><b>P${i + 1}</b></td><td>${yText(st, py)}</td>${vals.map((v) => `<td>${fs(v.x ?? v.value, v.dec)}</td>`).join('')}<td><button type="button" class="ich-x" data-act="unpin" data-i="${i}" title="Remove">×</button></td></tr>`;
  }).join('');
  return `<div class="ich-pins"><div class="ich-pins-h"><b>Pinned readings</b><span><button type="button" data-act="pcopy">Copy for Excel</button><button type="button" data-act="pclear">Clear</button></span></div>
    <div class="ich-scroll"><table><thead><tr><th></th><th>${esc(st.cfg.yName || 'height')}</th>${cols.map((c) => `<th>${esc(c)}</th>`).join('')}<th></th></tr></thead><tbody>${rows}</tbody></table></div></div>`;
}

function dataTable(st) {
  const cfg = st.cfg;
  if (st.kind === 'bar') {
    const groups = cfg.groups.filter((gr) => !st.hidden.has(gr.name));
    return [[cfg.rowName || '', ...groups.map((gr) => `${gr.name}${cfg.unit ? ` (${cfg.unit.trim()})` : ''}`)],
      ...cfg.rows.map((r, i) => [r.full || r.label, ...groups.map((gr) => gr.values[i])])];
  }
  let ys;
  if (cfg.yFmt === 'pct') ys = Array.from({ length: 21 }, (_, i) => i * 5);
  else {
    const [a, b] = extent(visibleSeries(st).filter((s) => s.hover).map((s) => s.y));
    ys = niceTicks(a, b, 30);
  }
  const head = valuesAt(st, ys[Math.floor(ys.length / 2)]).map((v) => `${v.name}${v.unit ? ` (${v.unit})` : ''}`);
  return [[`${cfg.yName || 'height'}${cfg.yFmt === 'pct' ? ' (%)' : cfg.yUnit ? ` (${cfg.yUnit.trim()})` : ''}`, ...head],
    ...ys.map((y) => [y, ...valuesAt(st, y).map((v) => { const x = v.x ?? v.value; return isFinite(x) ? +x.toFixed(4) : null; })])];
}
function dataHTML(st) {
  const t = dataTable(st);
  return `<div class="ich-data"><div class="ich-pins-h"><b>Numbers behind this chart</b><span><button type="button" data-act="dcopy">Copy for Excel</button><button type="button" data-act="dcsv">Download CSV</button></span></div>
    <div class="ich-scroll"><table><thead><tr>${t[0].map((h) => `<th>${esc(h)}</th>`).join('')}</tr></thead><tbody>${t.slice(1).map((r) => `<tr>${r.map((v, i) => `<td>${typeof v !== 'number' ? esc(v ?? '–')
      : i === 0 && st.kind === 'line' ? (st.cfg.yFmt === 'pct' ? `${f(v, 0)}%` : f(v, 1)) : f(v, Math.abs(v) >= 100 ? 1 : 2)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></div>`;
}

function innerHTML(st) {
  const svg = st.kind === 'line' ? lineSVG(st, false) : barSVG(st, false);
  return `${toolbarHTML(st)}<div class="ich-plot">${svg}<div class="tip"></div></div>${pinsHTML(st)}${st.showData ? dataHTML(st) : ''}`;
}

// ---------------------------------------------------------------- behaviour
function redraw(st) {
  if (!st.el || !st.el.isConnected) return;
  st.el.innerHTML = innerHTML(st);
  wireChart(st.el);
}
function redrawAll() { for (const st of CH.values()) if (st.el && st.el.isConnected) redraw(st); }
/** dim every series except `name` (null = show all) in all charts on the page */
function highlightAll(name) {
  for (const st of CH.values()) {
    if (!st.el || !st.el.isConnected) continue;
    const els = [...st.el.querySelectorAll('[data-g]')];
    const has = name && els.some((n) => n.dataset.g === name); // only charts that show this series
    els.forEach((n) => { n.style.opacity = has && n.dataset.g !== name ? '0.15' : ''; });
  }
}
function syncPeers(st) {
  if (!st.cfg.sync) return [];
  return [...CH.values()].filter((o) => o !== st && o.cfg.sync === st.cfg.sync && o.el && o.el.isConnected && o.geom);
}

function showRead(st, yv, ev) {
  const el = st.el, svg = el.querySelector('svg'), tip = el.querySelector('.tip'), g = st.geom;
  if (!svg || !g || !isFinite(yv)) return;
  const inY = yv >= Math.min(...g.yd) && yv <= Math.max(...g.yd);
  const hx = svg.querySelector('.hx'), dots = svg.querySelector('.hdots');
  if (!inY) { hideRead(st); return; }
  const py = g.Y(yv);
  hx.setAttribute('y1', py); hx.setAttribute('y2', py); hx.setAttribute('visibility', 'visible');
  const vals = valuesAt(st, yv), card = css('--card');
  dots.innerHTML = vals.filter((v) => v.x != null && isFinite(v.x) && v.x >= g.xd[0] && v.x <= g.xd[1])
    .map((v) => `<circle cx="${g.X(v.x)}" cy="${py}" r="4.5" fill="${v.color}" stroke="${card}" stroke-width="2"/>`).join('');
  const rows = vals.filter((v) => isFinite(v.x ?? v.value)).map((v) => `<div class="row"><i class="sw" style="background:${v.color}"></i>${esc(v.name)}: <b>${fs(v.x ?? v.value, v.dec)}</b> ${esc(v.unit)}</div>`);
  tip.innerHTML = `<div class="muted">${esc(st.cfg.yName || 'height')}: ${yText(st, yv)}</div>${rows.join('') || '<div class="muted">no data here</div>'}`;
  tip.style.display = 'block';
  const pr = el.querySelector('.ich-plot').getBoundingClientRect(), r = svg.getBoundingClientRect(), k = r.width / g.W;
  let left, top;
  if (ev) {
    left = ev.clientX - pr.left + 14; top = ev.clientY - pr.top + 12;
    if (left + tip.offsetWidth > pr.width) left = ev.clientX - pr.left - tip.offsetWidth - 14;
  } else { // linked chart: tip at the right of the plot, beside the crosshair
    left = (g.m.l + g.pw) * k - tip.offsetWidth - 4; top = py * k - tip.offsetHeight - 6;
    if (top < 0) top = py * k + 8;
  }
  tip.style.left = `${Math.max(0, left)}px`; tip.style.top = `${Math.max(0, top)}px`;
}
function hideRead(st) {
  const svg = st.el && st.el.querySelector('svg');
  if (!svg) return;
  svg.querySelector('.hx')?.setAttribute('visibility', 'hidden');
  const d = svg.querySelector('.hdots'); if (d) d.innerHTML = '';
  const tip = st.el.querySelector('.tip'); if (tip) tip.style.display = 'none';
}

function zoomBy(st, k, cx, cy) {
  const g = st.geom;
  const ax = cx ?? (g.xd[0] + g.xd[1]) / 2, ay = cy ?? (g.yd[0] + g.yd[1]) / 2;
  st.view.xd = [ax - (ax - g.xd[0]) * k, ax + (g.xd[1] - ax) * k];
  st.view.yd = [ay - (ay - g.yd[0]) * k, ay + (g.yd[1] - ay) * k];
  redraw(st);
}

async function exportChart(st, type) {
  const name = slug(st.cfg.fileName || st.cfg.title || 'chart');
  let svg = st.kind === 'line' ? lineSVG(st, true) : barSVG(st, true);
  svg = svg.replace(/(<svg[^>]*>)/, `$1<rect width="100%" height="100%" fill="${css('--card') || '#fff'}"/>`);
  if (type === 'svg') { download(new Blob([svg], { type: 'image/svg+xml' }), `${name}.svg`); return; }
  const vb = /viewBox="0 0 ([\d.]+) ([\d.]+)"/.exec(svg), w = +vb[1], h = +vb[2], scale = 3;
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
  const img = new Image();
  img.onload = () => {
    const c = document.createElement('canvas');
    c.width = w * scale; c.height = h * scale;
    const ctx = c.getContext('2d'); ctx.scale(scale, scale); ctx.drawImage(img, 0, 0, w, h);
    URL.revokeObjectURL(url);
    c.toBlob((b) => download(b, `${name}.png`));
  };
  img.src = url;
}

function openFull(st) {
  const st2 = newState(st.cfg, st.kind, { view: { ...st.view }, hidden: new Set(st.hidden), colorBy: st.colorBy, pins: [...st.pins], full: true });
  const ov = document.createElement('div');
  ov.className = 'ich-modal';
  ov.innerHTML = `<div class="ich-modal-in" role="dialog" aria-label="${esc(st.cfg.title || 'chart')}"><div class="ich-modal-h"><b>${esc(st.cfg.title || '')}</b><button type="button" class="ich-close">✕ Close</button></div>${chartHTML(st2)}</div>`;
  document.body.appendChild(ov);
  document.body.style.overflow = 'hidden';
  wireChart(ov.querySelector('.ichart'));
  const close = () => { ov.remove(); document.body.style.overflow = ''; CH.delete(st2.id); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  ov.querySelector('.ich-close').onclick = close;
  ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
  document.addEventListener('keydown', onKey);
}

function act(st, what, btn) {
  if (what === 'zin') zoomBy(st, 0.7);
  else if (what === 'zout') zoomBy(st, 1 / 0.7);
  else if (what === 'reset') { st.view = { xd: null, yd: null }; redraw(st); }
  else if (what === 'full') openFull(st);
  else if (what === 'data') { st.showData = !st.showData; redraw(st); }
  else if (what === 'png' || what === 'svg') exportChart(st, what);
  else if (what === 'unpin') { st.pins.splice(+btn.dataset.i, 1); redraw(st); }
  else if (what === 'pclear') { st.pins = []; redraw(st); }
  else if (what === 'pcopy' || what === 'dcopy' || what === 'dcsv') {
    let rows;
    if (what === 'pcopy') {
      const head = ['pin', st.cfg.yName || 'height', ...valuesAt(st, st.pins[0]).map((v) => `${v.name}${v.unit ? ` (${v.unit})` : ''}`)];
      rows = [head, ...st.pins.map((py, i) => [`P${i + 1}`, +py.toFixed(3), ...valuesAt(st, py).map((v) => { const x = v.x ?? v.value; return isFinite(x) ? +x.toFixed(4) : ''; })])];
    } else rows = dataTable(st);
    if (what === 'dcsv') { download(new Blob([toCSV(rows)], { type: 'text/csv' }), `${slug(st.cfg.fileName || st.cfg.title || 'chart')}_data.csv`); return; }
    copyText(rows.map((r) => r.map((v) => (v == null ? '' : v)).join('\t')).join('\n')).then((ok) => {
      const old = btn.textContent; btn.textContent = ok ? 'Copied ✓' : 'Copy failed'; setTimeout(() => { btn.textContent = old; }, 1400);
    });
  }
}

function wireChart(el) {
  const st = CH.get(el.dataset.chart);
  if (!st) return;
  st.el = el;
  el.querySelectorAll('[data-act]').forEach((b) => { b.onclick = (e) => { e.stopPropagation(); act(st, b.dataset.act, b); }; });
  const sel = el.querySelector('select[data-colorby]');
  if (sel) sel.onchange = () => { st.colorBy = sel.value; if (st.cfg.onColorBy) st.cfg.onColorBy(sel.value); else redraw(st); };
  el.querySelectorAll('[data-leg]').forEach((chip) => {
    const name = chip.dataset.leg;
    chip.onclick = () => {
      if (st.hidden.has(name)) st.hidden.delete(name); else st.hidden.add(name);
      st.cfg.onLegend?.(name, st.hidden.has(name));
      redraw(st);
    };
    chip.onmouseenter = () => { if (!st.hidden.has(name)) el.querySelectorAll('[data-g]').forEach((n) => { n.style.opacity = n.dataset.g === name ? '' : '0.15'; }); };
    chip.onmouseleave = () => el.querySelectorAll('[data-g]').forEach((n) => { n.style.opacity = ''; });
  });
  const svg = el.querySelector('.ich-plot svg'), tip = el.querySelector('.tip');
  if (!svg) return;
  if (st.kind === 'bar') {
    svg.querySelectorAll('rect[data-tip]').forEach((r) => {
      r.addEventListener('mousemove', (ev) => {
        const pr = el.querySelector('.ich-plot').getBoundingClientRect();
        tip.innerHTML = esc(r.dataset.tip); tip.style.display = 'block';
        tip.style.left = `${Math.min(ev.clientX - pr.left + 12, pr.width - tip.offsetWidth)}px`; tip.style.top = `${ev.clientY - pr.top + 12}px`;
      });
      r.addEventListener('mouseleave', () => { tip.style.display = 'none'; });
    });
    return;
  }
  const hit = svg.querySelector('.hit'), zbox = svg.querySelector('.zbox');
  if (!hit) return;
  const toSvg = (ev) => { const r = svg.getBoundingClientRect(), k = st.geom.W / r.width; return { x: (ev.clientX - r.left) * k, y: (ev.clientY - r.top) * k }; };
  // draggable range lines (tool only)
  svg.querySelectorAll('.handle').forEach((hg) => {
    const key = hg.dataset.key, hd = (st.cfg.handles || []).find((h) => h.key === key), lbl = hg.querySelector('.hl');
    let drag = false, value = hd.y;
    hg.addEventListener('pointerdown', (ev) => { ev.preventDefault(); ev.stopPropagation(); drag = true; hg.setPointerCapture(ev.pointerId); });
    hg.addEventListener('pointermove', (ev) => {
      if (!drag) return;
      const g = st.geom, py = Math.min(g.m.t + g.ph, Math.max(g.m.t, toSvg(ev).y));
      value = Math.round(g.Yinv(py) * 2) / 2;
      hg.setAttribute('transform', `translate(0 ${g.Y(value).toFixed(1)})`);
      lbl.textContent = `${hd.label} · ${value.toFixed(1)} mm`;
    });
    hg.addEventListener('pointerup', () => { if (drag) { drag = false; st.cfg.onHandle?.(key, value); } });
  });
  let drag = null, raf = 0;
  const hover = (ev) => {
    const g = st.geom, p = toSvg(ev), yv = g.Yinv(p.y);
    showRead(st, yv, ev);
    for (const o of syncPeers(st)) showRead(o, yv, null);
  };
  hit.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    const p = toSvg(ev);
    drag = { x0: p.x, y0: p.y, x: p.x, y: p.y, pan: ev.shiftKey, moved: false };
    hit.setPointerCapture(ev.pointerId);
  });
  hit.addEventListener('pointermove', (ev) => {
    if (drag) {
      const p = toSvg(ev);
      drag.x = p.x; drag.y = p.y;
      if (Math.hypot(p.x - drag.x0, p.y - drag.y0) > 4) drag.moved = true;
      if (drag.moved) {
        if (drag.pan) {
          svg.querySelector('.plotg').setAttribute('transform', `translate(${p.x - drag.x0} ${p.y - drag.y0})`);
        } else {
          const x = Math.min(p.x, drag.x0), y = Math.min(p.y, drag.y0);
          zbox.setAttribute('x', x); zbox.setAttribute('y', y);
          zbox.setAttribute('width', Math.abs(p.x - drag.x0)); zbox.setAttribute('height', Math.abs(p.y - drag.y0));
          zbox.setAttribute('visibility', 'visible');
        }
      }
    }
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => hover(ev));
  });
  hit.addEventListener('pointerup', (ev) => {
    if (!drag) return;
    const d = drag, g = st.geom;
    drag = null;
    if (!d.moved) { // click: pin / unpin a reading at this height
      const yv = g.Yinv(d.y0), near = st.pins.findIndex((py) => Math.abs(g.Y(py) - d.y0) < 6);
      if (near >= 0) st.pins.splice(near, 1); else st.pins.push(yv);
      st.pins.sort((a, b) => a - b);
      redraw(st);
      return;
    }
    if (d.pan) {
      const dx = g.Xinv(d.x0) - g.Xinv(d.x), dy = g.Yinv(d.y0) - g.Yinv(d.y);
      st.view.xd = [g.xd[0] + dx, g.xd[1] + dx]; st.view.yd = [g.yd[0] + dy, g.yd[1] + dy];
    } else {
      if (Math.abs(d.x - d.x0) > 6) { const a = g.Xinv(d.x0), b = g.Xinv(d.x); st.view.xd = [Math.min(a, b), Math.max(a, b)]; }
      if (Math.abs(d.y - d.y0) > 6) { const a = g.Yinv(d.y0), b = g.Yinv(d.y); st.view.yd = [Math.min(a, b), Math.max(a, b)]; }
    }
    redraw(st);
  });
  hit.addEventListener('dblclick', () => { st.view = { xd: null, yd: null }; redraw(st); });
  hit.addEventListener('wheel', (ev) => {
    if (!(ev.ctrlKey || ev.metaKey)) return; // plain wheel keeps scrolling the page
    ev.preventDefault();
    const g = st.geom, p = toSvg(ev);
    zoomBy(st, ev.deltaY < 0 ? 0.8 : 1.25, g.Xinv(p.x), g.Yinv(p.y));
  }, { passive: false });
  hit.addEventListener('pointerleave', () => {
    if (drag) return;
    hideRead(st);
    for (const o of syncPeers(st)) hideRead(o);
  });
}

/** wire every chart inside root (after inserting chart HTML into the page) */
function wireCharts(root) { root.querySelectorAll('.ichart[data-chart]').forEach(wireChart); }

// ---------------------------------------------------------------- specs (for the HTML report)
const round4 = (v) => (typeof v === 'number' && isFinite(v) ? Math.round(v * 1e4) / 1e4 : null);
function plain(v) {
  if (v instanceof Float64Array || v instanceof Float32Array) return Array.from(v, round4);
  if (Array.isArray(v)) return v.map((x) => (typeof x === 'number' ? round4(x) : plain(x)));
  if (v && typeof v === 'object') {
    const o = {};
    for (const [k, x] of Object.entries(v)) if (typeof x !== 'function' && k !== 'handles') o[k] = plain(x);
    return o;
  }
  return v;
}
/** JSON-safe description of a chart (data rounded to 4 decimals) */
function chartSpec(html) {
  const st = CH.get(idOfChart(html));
  return st ? { kind: st.kind, cfg: plain(st.cfg), colorBy: st.colorBy, hidden: [...st.hidden] } : null;
}
/** bring report placeholders [data-spec] to life */
function mountSpecs(root, specs) {
  root.querySelectorAll('[data-spec]').forEach((slot) => {
    const sp = specs[slot.dataset.spec];
    if (!sp) return;
    const st = newState(sp.cfg, sp.kind, { colorBy: sp.colorBy, hidden: new Set(sp.hidden || []) });
    slot.classList.add('ichart');
    slot.dataset.chart = st.id;
    slot.innerHTML = innerHTML(st);
    wireChart(slot);
  });
}

// ---------------------------------------------------------------- styles (injected once)
(function addChartStyles() {
  if (document.getElementById('ich-css')) return;
  const s = document.createElement('style');
  s.id = 'ich-css';
  s.textContent = `
.ichart { position: relative; }
.ich-bar { display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 4px 10px; margin: 0 0 4px; }
.ich-legend { display: flex; flex-wrap: wrap; gap: 2px 4px; min-width: 0; }
.ich-chip { display: inline-flex; align-items: center; gap: 6px; font: inherit; font-size: 12px; color: var(--ink2); background: transparent; border: 1px solid transparent; border-radius: 999px; padding: 1px 8px 1px 6px; cursor: pointer; }
.ich-chip:hover { border-color: var(--border); background: var(--hover); }
.ich-chip i { width: 18px; height: 3px; border-radius: 2px; display: inline-block; flex: none; }
.ich-chip.off { opacity: .45; text-decoration: line-through; }
.ich-tools { display: flex; flex-wrap: wrap; gap: 3px; align-items: center; margin-left: auto; }
.ich-tools button, .ich-pins-h button { font: inherit; font-size: 12px; color: var(--ink2); background: var(--raise); border: 1px solid var(--border); border-radius: 6px; padding: 2px 7px; cursor: pointer; line-height: 1.5; }
.ich-tools button:hover, .ich-pins-h button:hover { background: var(--hover); color: var(--ink); }
.ich-tools button.on { border-color: var(--accent); color: var(--accent); }
.ich-sel { font-size: 12px; color: var(--ink2); display: inline-flex; gap: 4px; align-items: center; margin-right: 4px; }
.ich-sel select { font: inherit; font-size: 12px; background: var(--raise); color: var(--ink); border: 1px solid var(--border); border-radius: 6px; padding: 1px 4px; }
.ich-note { font-size: 11.5px; color: var(--accent); margin: -2px 0 2px; }
.ich-hint { font-size: 11px; color: var(--muted); margin-top: 2px; }
.ich-plot { position: relative; }
.ich-plot svg { display: block; width: 100%; height: auto; overflow: visible; user-select: none; -webkit-user-select: none; }
.ich-plot .hit { cursor: crosshair; }
.ich-plot .tip { position: absolute; pointer-events: none; background: var(--raise); border: 1px solid var(--border); border-radius: 8px; padding: 6px 8px; font-size: 12px; box-shadow: 0 4px 14px rgba(0,0,0,.12); white-space: nowrap; z-index: 3; display: none; color: var(--ink); }
.ich-plot .tip .row { display: flex; gap: 6px; align-items: center; }
.ich-plot .tip .sw { width: 10px; height: 10px; border-radius: 3px; display: inline-block; flex: none; }
.ich-plot .tip .muted { color: var(--muted); }
.ich-pins, .ich-data { border: 1px solid var(--border); border-radius: 8px; padding: 6px 8px; margin-top: 6px; background: var(--raise); }
.ich-pins-h { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 12.5px; margin-bottom: 4px; }
.ich-pins-h span { display: flex; gap: 4px; }
.ich-scroll { max-height: 240px; overflow: auto; }
.ich-pins table, .ich-data table { border-collapse: collapse; width: 100%; font-size: 12px; font-variant-numeric: tabular-nums; }
.ich-pins th, .ich-pins td, .ich-data th, .ich-data td { padding: 2px 6px; border-bottom: 1px solid var(--border); text-align: right; white-space: nowrap; }
.ich-pins th, .ich-data th { color: var(--ink2); font-weight: 600; position: sticky; top: 0; background: var(--raise); }
.ich-pins td:first-child, .ich-pins th:first-child, .ich-data td:first-child, .ich-data th:first-child { text-align: left; }
.ich-x { border: none !important; background: none !important; color: var(--muted) !important; font-size: 15px !important; padding: 0 4px !important; }
.ich-modal { position: fixed; inset: 0; background: rgba(0,0,0,.55); z-index: 50; display: flex; align-items: center; justify-content: center; padding: 2vh 2vw; }
.ich-modal-in { background: var(--card, #fff); color: var(--ink); border-radius: 12px; padding: 12px 16px; width: min(96vw, 1400px); max-height: 96vh; overflow: auto; box-shadow: 0 10px 40px rgba(0,0,0,.3); }
.ich-modal-h { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
.ich-modal-h button { font: inherit; background: var(--raise); border: 1px solid var(--border); border-radius: 8px; padding: 4px 10px; cursor: pointer; color: var(--ink); }
.ich-modal .ich-plot svg { max-height: calc(96vh - 150px); width: auto; max-width: 100%; margin: 0 auto; }
@media print { .ich-bar, .ich-hint, .ich-note, .ich-data, .ich-pins .ich-pins-h span, .ich-x { display: none !important; } }
`;
  document.head.appendChild(s);
})();
