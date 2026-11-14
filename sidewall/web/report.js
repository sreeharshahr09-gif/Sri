/* Report generation: project-details form, HTML report and vector PDF report.
 * Uses the app's chart builders (tireModel / compareModel), drawn in the light theme.
 * The PDF writer is built in (no libraries, works offline): standard Helvetica fonts,
 * charts converted from SVG to PDF vector drawing commands, so they stay sharp. */
'use strict';

// ============================================================== settings & form
const REPORT_DEFAULT = {
  title: 'Sidewall Deflection Analysis', project: '', number: '', customer: '', preparedBy: '', reviewedBy: '',
  date: new Date().toISOString().slice(0, 10), objective: '', conclusions: '', logo: null,
  sections: { summary: true, tires: true, compare: true, quality: true, method: true, glossary: false },
};
const REPORT = (() => {
  try { return { ...REPORT_DEFAULT, ...JSON.parse(localStorage.getItem('swda-report') || '{}') }; } catch (e) { return { ...REPORT_DEFAULT }; }
})();
REPORT.sections = { ...REPORT_DEFAULT.sections, ...(REPORT.sections || {}) };
const saveReport = () => {
  try { localStorage.setItem('swda-report', JSON.stringify(REPORT)); } catch (e) {
    try { localStorage.setItem('swda-report', JSON.stringify({ ...REPORT, logo: null })); } catch (e2) { /* storage blocked */ }
  }
};
let reportTires = null; // Set of tire ids (this session)

const RFIELDS = [
  ['title', 'Report title'], ['project', 'Project name'], ['number', 'Project / request no.'], ['customer', 'Customer / department'],
  ['preparedBy', 'Prepared by'], ['reviewedBy', 'Reviewed / approved by'], ['date', 'Report date', 'date'],
];
const RSECTIONS = [
  ['summary', 'Summary table and highlights'], ['tires', 'One section per tire (all charts with explanations)'],
  ['compare', 'Comparison charts (2 or more tires)'], ['quality', 'Data checks'], ['method', 'How the numbers are worked out'],
  ['glossary', 'Glossary of terms'],
];

function renderReport(main) {
  const ts = okTires();
  if (!ts.length) { main.innerHTML = emptyHTML(); wireEmpty(); return; }
  if (!reportTires || ![...reportTires].some((id) => ts.find((t) => t.id === id))) {
    const pickd = ts.filter((t) => t.inCompare);
    reportTires = new Set((pickd.length ? pickd : ts).map((t) => t.id));
  }
  main.innerHTML = `<section class="panel first">
    <h2>Report</h2>
    <p class="muted">Fill in the project details, choose the tires and sections, then download. Both formats contain the same pages; charts are vector graphics, so they stay sharp when zoomed or printed. Details are remembered on this computer.</p>
    <div class="formgrid">
      ${RFIELDS.map(([k, name, type]) => `<label><span>${name}</span><input data-r="${k}" ${type ? `type="${type}"` : ''} value="${esc(REPORT[k] || '')}"></label>`).join('')}
      <label class="wide"><span>Objective of the test</span><textarea data-r="objective" placeholder="e.g. Compare sidewall flexing of candidate designs at 4500 kg / 170 psi">${esc(REPORT.objective)}</textarea></label>
      <label class="wide"><span>Conclusions / remarks</span><textarea data-r="conclusions" placeholder="Your conclusions. The report also adds automatic highlights from the numbers.">${esc(REPORT.conclusions)}</textarea></label>
      <label><span>Logo (PNG or JPG, optional)</span><input type="file" id="rLogo" accept="image/png,image/jpeg"></label>
      <div style="display:flex;align-items:end;gap:10px">${REPORT.logo ? `<img src="${REPORT.logo}" alt="logo" style="max-height:44px;max-width:160px;border:1px solid var(--border);border-radius:6px;padding:3px;background:#fff"><button class="btn ghost" id="rLogoX">Remove logo</button>` : '<span class="small muted">No logo</span>'}</div>
    </div>
    <h3 style="margin-top:16px">Tires in the report</h3>
    <div class="formgrid">${ts.map((t) => `<label style="flex-direction:row;align-items:center;gap:8px"><input type="checkbox" data-t="${t.id}" ${reportTires.has(t.id) ? 'checked' : ''}><span>${esc(t.label)}${t.meta.load_daN != null ? ` · ${f(t.meta.load_daN, 0)} daN` : ''}${t.meta.pressure_psi != null ? ` · ${f(t.meta.pressure_psi, 0)} psi` : ''}</span></label>`).join('')}</div>
    <h3 style="margin-top:16px">Sections</h3>
    <div class="formgrid">${RSECTIONS.map(([k, name]) => `<label style="flex-direction:row;align-items:center;gap:8px"><input type="checkbox" data-s="${k}" ${REPORT.sections[k] ? 'checked' : ''}><span>${name}</span></label>`).join('')}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:16px;align-items:center">
      <button class="btn primary" id="rPdf">Download PDF report</button>
      <button class="btn" id="rHtml">Download HTML report</button>
      <button class="btn ghost" id="rPreview">Preview HTML in a new tab</button>
      <span class="small" id="rMsg"></span>
    </div>
    <p class="small muted" style="margin-top:10px">Charts use the current settings: curve smoothing, rim drawn at ${S.settings.rimDraw}, colour by “${(COLOR_BY[S.settings.colorBy] || COLOR_BY.du).name}”, rubber over ply ${S.settings.hMm} mm, and each tire's analysis range.</p>
  </section>`;
  main.querySelectorAll('[data-r]').forEach((el) => { el.oninput = () => { REPORT[el.dataset.r] = el.value; saveReport(); }; });
  main.querySelectorAll('[data-s]').forEach((el) => { el.onchange = () => { REPORT.sections[el.dataset.s] = el.checked; saveReport(); }; });
  main.querySelectorAll('[data-t]').forEach((el) => { el.onchange = () => { const id = +el.dataset.t; if (el.checked) reportTires.add(id); else reportTires.delete(id); }; });
  $('rLogo').onchange = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const rd = new FileReader();
    rd.onload = () => { REPORT.logo = rd.result; saveReport(); renderReport(main); };
    rd.readAsDataURL(file);
  };
  if ($('rLogoX')) $('rLogoX').onclick = () => { REPORT.logo = null; saveReport(); renderReport(main); };
  const chosen = () => ts.filter((t) => reportTires.has(t.id));
  const name = () => slug(REPORT.project || REPORT.number || REPORT.title || 'sidewall') + '_report';
  const go = async (kind) => {
    const list = chosen();
    if (!list.length) { $('rMsg').innerHTML = '<span class="warn">Tick at least one tire.</span>'; return; }
    $('rMsg').textContent = 'Building the report…';
    await new Promise((r) => setTimeout(r, 30));
    try {
      const blocks = buildReportBlocks(list);
      if (kind === 'pdf') {
        const bytes = await reportPDF(blocks, list);
        download(new Blob([bytes], { type: 'application/pdf' }), `${name()}.pdf`);
      } else {
        const html = reportHTML(blocks, list);
        const blob = new Blob([html], { type: 'text/html' });
        if (kind === 'preview') window.open(URL.createObjectURL(blob), '_blank');
        else download(blob, `${name()}.html`);
      }
      $('rMsg').textContent = kind === 'preview' ? 'Preview opened in a new tab.' : 'Report downloaded.';
    } catch (err) {
      console.error(err);
      $('rMsg').innerHTML = `<span class="warn">Could not build the report: ${esc(err.message)}</span>`;
    }
  };
  $('rPdf').onclick = () => go('pdf');
  $('rHtml').onclick = () => go('html');
  $('rPreview').onclick = () => go('preview');
}

// ============================================================== report outline
/** The report as a list of blocks; both the HTML and the PDF writer draw from it. */
function buildReportBlocks(list) {
  const root = document.documentElement, prevTheme = root.dataset.theme;
  root.dataset.theme = 'light'; // reports are always light, for print
  try {
    const B = [], sec = REPORT.sections, many = list.length > 1;
    const colorOf = (t) => css(`--s${(list.indexOf(t) % 8) + 1}`);
    B.push({ type: 'cover' });

    if (sec.summary) {
      B.push({ type: 'h1', text: 'Summary' });
      B.push({ type: 'p', html: highlights(list) });
      B.push({ type: 'table', head: ['Tire', 'Load (daN)', 'Pressure (psi)', 'Radial defl. (mm)', 'Bulge (mm)', 'Stiffness (N/mm)', 'Sidewall share (%)', 'Peak bending (%)', 'Bending at (% height)'],
        widths: [2.6, 1, 1, 1, 1, 1.1, 1.1, 1, 1.1], align: 'lrrrrrrrr',
        rows: list.map((t) => { const r = t.row; return [t.label, f(r.load_daN, 0), f(r.pressure_psi, 0), f(r.radial_deflection_lab_mm, 1), f(r.horizontal_deflection_smoothed_mm, 1),
          f(r.vertical_stiffness_N_per_mm, 0), f(r.sidewall_share_pct, 0), f(r.peak_bending_strain_pct, 1), r.peak_bending_height_mm != null ? f(pctAt(t.res, r.peak_bending_height_mm), 0) : '–']; }) });
      B.push({ type: 'p', small: true, html: 'Radial deflection is the machine value from each file. Bulge is the outward push of the widest point (smoothed). Stiffness = load ÷ radial deflection. Sidewall share = part of the deflection taken by the sidewall; the rest is taken by the tread/crown. Bending is the estimated surface strain where the sidewall flexes most.' });
    }

    if (sec.compare && many) {
      const shown = list.slice(0, 8);
      const C = compareModel(shown, { forReport: true, colorOf, metricKeys: ['vertical_stiffness_N_per_mm', 'horizontal_deflection_smoothed_mm', 'peak_bending_strain_pct', 'sidewall_share_pct'] });
      B.push({ type: 'h1', text: 'Comparison', pageBreak: true });
      if (list.length > 8) B.push({ type: 'p', small: true, html: `Charts show the first 8 of ${list.length} tires; the summary table lists all of them.` });
      const fig = (s) => ({ title: s.title, svgs: [svgOf(s.svg)], caption: captionHTML(s, 'In this comparison') });
      B.push({ type: 'figrow', items: [fig(C.bulge), fig(C.bend)] });
      B.push({ type: 'figrow', items: [fig(C.tilt), fig(C.metrics[0])] });
      B.push({ type: 'figrow', items: [fig(C.metrics[1]), fig(C.metrics[2])] });
      B.push({ type: 'figrow', items: [fig(C.metrics[3]), { html: '' }] });
    }

    const checks = [];
    if (sec.tires) {
      for (const t of list) {
        const M = tireModel(t, { forReport: true, showRaw: true });
        const m = M.m, meta = t.meta, rg = m.range;
        B.push({ type: 'h1', text: t.label, pageBreak: true });
        B.push({ type: 'kv2', rows: [
          ['Tire size', meta.size], ['Brand', meta.brand], ['Serial', meta.serial], ['Test date', meta.test_date], ['Test type', meta.feature],
          ['Load', meta.load_daN != null ? `${f(meta.load_daN, 0)} daN (${f(kg(meta.load_daN), 0)} kg)` : null],
          ['Pressure', meta.pressure_psi != null ? `${f(meta.pressure_psi, 1).replace(/\.0$/, '')} psi` : null], ['Rim', meta.rim],
          ['Room temperature', meta.room_temp_C != null ? `${f(meta.room_temp_C, 1)} °C` : null],
        ].filter(([, v]) => v != null && v !== ''), long: [['File', t.name], ['Scan read from', meta.source],
          ['Analysis range', `${rg.manual ? 'set by hand' : 'automatic'}: sidewall from ${f(rg.z_start, 1)} mm, shoulder edge ${f(rg.edge_inflated, 1)} mm (inflated) / ${f(rg.edge_loaded, 1)} mm (loaded)`]] });
        B.push({ type: 'kpis', items: M.kpis });
        B.push({ type: 'figrow', widths: [0.5, 0.5], items: [
          { title: M.shape.title, svgs: [svgOf(M.shape.svg), svgOf(M.shape.colorbar)] },
          { html: captionHTML(M.shape) },
        ] });
        const fig = (s) => ({ title: s.title, svgs: [svgOf(s.svg)], caption: captionHTML(s) });
        B.push({ type: 'figrow', items: [fig(M.bulge), fig(M.tilt)] });
        B.push({ type: 'figrow', items: [fig(M.bend), fig(M.zones)] });
        checks.push([t.label, M.quality]);
      }
    }
    if (sec.quality && sec.tires && checks.length) {
      B.push({ type: 'h1', text: 'Data checks', pageBreak: true });
      for (const [label, items] of checks) { B.push({ type: 'h2', text: label }); B.push({ type: 'list', items }); }
    }

    if (sec.method) {
      B.push({ type: 'h1', text: 'How the numbers are worked out', pageBreak: true });
      for (const [h, p] of METHOD_TEXT) { B.push({ type: 'h2', text: h }); B.push({ type: 'p', html: p }); }
    }
    if (sec.glossary) {
      B.push({ type: 'h1', text: 'Glossary', pageBreak: !sec.method });
      B.push({ type: 'kv', wide: true, rows: GLOSSARY });
    }
    return B;
  } finally {
    if (prevTheme) root.dataset.theme = prevTheme; else delete root.dataset.theme;
  }
}

const captionHTML = (s, label = 'For this tire') =>
  `<b>What it shows.</b> ${s.what} <b>How to read it.</b> ${s.how}${s.mine ? ` <b>${label}.</b> ${s.mine}` : ''}`;

function highlights(list) {
  const pickT = (key, hi) => { // only meaningful when 2+ tires have the value and they differ
    const v = list.filter((t) => t.row[key] != null);
    if (v.length < 2) return null;
    const vals = v.map((t) => t.row[key]);
    if (Math.max(...vals) - Math.min(...vals) < 1e-9) return null;
    return v.reduce((a, b) => ((hi ? b.row[key] > a.row[key] : b.row[key] < a.row[key]) ? b : a));
  };
  if (list.length === 1) {
    const t = list[0], r = t.row;
    return `<b>${esc(t.label)}</b>: radial deflection ${f(r.radial_deflection_lab_mm ?? r.shoulder_travel_mm, 1)} mm${r.load_daN != null ? ` at ${f(r.load_daN, 0)} daN` : ''}`
      + `${r.vertical_stiffness_N_per_mm ? `, vertical stiffness ${f(r.vertical_stiffness_N_per_mm, 0)} N/mm` : ''}; the sidewall bulges ${f(r.horizontal_deflection_smoothed_mm, 1)} mm`
      + `${r.sidewall_share_pct != null ? ` and takes ${f(r.sidewall_share_pct, 0)}% of the deflection` : ''}. `
      + `${r.peak_bending_strain_pct != null ? `It bends hardest at about ${f(pctAt(t.res, r.peak_bending_height_mm), 0)}% of the height above the rim (≈${f(r.peak_bending_strain_pct, 1)}% surface strain).` : ''}`;
  }
  const parts = [];
  const add = (key, hi, txt, unit, d) => { const t = pickT(key, hi); if (t) parts.push(`${txt} <b>${esc(t.label)}</b> (${f(t.row[key], d)} ${unit})`); };
  add('vertical_stiffness_N_per_mm', true, 'Stiffest:', 'N/mm', 0);
  add('vertical_stiffness_N_per_mm', false, 'least stiff:', 'N/mm', 0);
  add('horizontal_deflection_smoothed_mm', true, 'largest sidewall bulge:', 'mm', 1);
  add('horizontal_deflection_smoothed_mm', false, 'smallest bulge:', 'mm', 1);
  add('peak_bending_strain_pct', true, 'hardest bending:', '%', 1);
  add('peak_bending_strain_pct', false, 'gentlest bending:', '%', 1);
  const loads = new Set(list.map((t) => t.row.load_daN).filter((v) => v != null).map((v) => Math.round(v)));
  const press = new Set(list.map((t) => t.row.pressure_psi).filter((v) => v != null).map((v) => Math.round(v)));
  const warn = loads.size > 1 || press.size > 1 ? ' <b>Note:</b> the tires were not all tested at the same load and pressure, so compare with care.' : '';
  const missing = list.filter((t) => t.row.radial_deflection_lab_mm == null).map((t) => `<b>${esc(t.label)}</b>`);
  const miss = missing.length ? ` No machine radial deflection for ${missing.join(', ')}, so stiffness and sidewall share are not given for ${missing.length > 1 ? 'them' : 'it'}.` : '';
  return `${list.length} tires analysed.${parts.length ? ` ${parts.join('; ').replace(/^./, (c) => c.toUpperCase())}.` : ''}${miss}${warn}`;
}

const METHOD_TEXT = [
  ['The test', 'A laser looks at the side of the tire and travels from the rim end to the shoulder, measuring how far away the rubber is at each height. It does this twice: first with the tire only inflated, then with the load applied. Comparing the two scans shows how the sidewall moves under load.'],
  ['Cleaning the readings', 'Readings where the laser lost the surface (out-of-range codes) are removed, as are repeated readings logged while the laser was still updating. Profiles are smoothed lightly; slope and curvature are smoothed over 45 mm so that raised lettering on the sidewall does not look like bending.'],
  ['Shoulder travel and sidewall share', 'The textured band at the shoulder end of the scan moves straight toward the rim under load. Its shift between the two scans is how much the sidewall itself shortened (shoulder travel). The machine’s radial deflection minus the shoulder travel is the part taken by the tread and crown.'],
  ['Following the same rubber', 'Under load the sidewall also slides toward the rim, so comparing the two scans at the same height compares different pieces of rubber. The cords make the sidewall almost unstretchable, so the tool follows each point by its distance along the sidewall from the rim instead.'],
  ['Bulge, tilt and bending', 'Outward deflection is how far each point moved outward. Tilt is how much the surface rotated. Bending strain is the change in curvature multiplied by the rubber thickness over the cords (an assumed value), which estimates how much the outer surface stretches.'],
  ['Limits', 'Under load a little of the sidewall just below the shoulder folds out of the laser’s view, so values near the shoulder end are less certain. Strain values depend on the assumed rubber thickness over the cords. The analysis range (where the sidewall starts and ends) is found automatically and can be set by hand for each tire.'],
];
const GLOSSARY = [
  ['Rim end / shoulder end', 'The sidewall runs from the wheel rim to the shoulder, where it meets the tread.'],
  ['Radial deflection', 'How far the tire squashes down under the load, measured by the machine.'],
  ['Sidewall bulge (horizontal deflection)', 'How far the widest point of the sidewall pushes out under load.'],
  ['Vertical stiffness', 'Load divided by radial deflection. Higher means the tire squashes less.'],
  ['Shoulder travel', 'How far the shoulder moves toward the rim under load: the shortening of the sidewall.'],
  ['Sidewall share', 'Shoulder travel as a share of the radial deflection.'],
  ['Outward deflection', 'How far each point of the sidewall moves outward under load.'],
  ['Tilt', 'How much a part of the sidewall rotates under load, in degrees.'],
  ['Bending strain', 'Estimated stretch of the outer rubber surface caused by bending. Peaks are where fatigue and heat build up first.'],
  ['Rim-side / middle / shoulder-side', 'The sidewall split into three equal parts.'],
];

// ============================================================== HTML report
function reportHTML(B, list) {
  const R = REPORT;
  const cell = (v) => esc(v ?? '–');
  const out = [];
  for (const b of B) {
    if (b.type === 'cover') out.push(coverHTML(list));
    else if (b.type === 'h1') out.push(`<h1 class="${b.pageBreak ? 'pb' : ''}">${esc(b.text)}</h1>`);
    else if (b.type === 'h2') out.push(`<h2>${esc(b.text)}</h2>`);
    else if (b.type === 'p') out.push(`<p class="${b.small ? 'small' : ''}">${b.html}</p>`);
    else if (b.type === 'kv2') {
      const half = Math.ceil(b.rows.length / 2);
      out.push(`<table class="kv kv2">${Array.from({ length: half }, (_, i) => {
        const a = b.rows[i], c = b.rows[i + half];
        return `<tr><th>${esc(a[0])}</th><td>${cell(a[1])}</td>${c ? `<th>${esc(c[0])}</th><td>${cell(c[1])}</td>` : '<th></th><td></td>'}</tr>`;
      }).join('')}</table><table class="kv kvl">${b.long.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${cell(v)}</td></tr>`).join('')}</table>`);
    } else if (b.type === 'kv') out.push(`<table class="kv ${b.wide ? 'wide' : ''}">${b.rows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${cell(v)}</td></tr>`).join('')}</table>`);
    else if (b.type === 'table') out.push(`<table class="data"><thead><tr>${b.head.map((h, i) => `<th class="${b.align[i] === 'r' ? 'r' : ''}">${esc(h)}</th>`).join('')}</tr></thead><tbody>${b.rows.map((r) => `<tr>${r.map((v, i) => `<td class="${b.align[i] === 'r' ? 'r' : ''}">${cell(v)}</td>`).join('')}</tr>`).join('')}</tbody></table>`);
    else if (b.type === 'kpis') out.push(`<table class="data kpi"><thead><tr><th>Result</th><th class="r">Value</th><th>What it means</th></tr></thead><tbody>${b.items.map((k) => `<tr><td><b>${esc(k.k)}</b></td><td class="r nowrap">${esc(k.v)} ${esc(k.u)}</td><td>${esc(k.d)}</td></tr>`).join('')}</tbody></table>`);
    else if (b.type === 'figrow') {
      const w = b.widths || b.items.map(() => 1 / b.items.length);
      out.push(`<div class="figrow" style="grid-template-columns:${w.map((x) => `${x}fr`).join(' ')}">${b.items.map((it) => (it.svgs
        ? `<figure class="${it.svgs.length > 1 ? 'shape' : ''}">${it.title ? `<div class="ft">${esc(it.title)}</div>` : ''}${it.svgs.join('')}${it.caption ? `<figcaption>${it.caption}</figcaption>` : ''}</figure>`
        : `<div class="side">${it.html}</div>`)).join('')}</div>`);
    } else if (b.type === 'list') out.push(`<ul>${b.items.map((i) => `<li>${i}</li>`).join('')}</ul>`);
  }
  const foot = [R.title, R.project, R.number].filter(Boolean).join(' · ');
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(R.title)}${R.project ? ` – ${esc(R.project)}` : ''}</title>
<style>
@page { size: A4; margin: 15mm 14mm 17mm; }
* { box-sizing: border-box; }
html { background: #ecebe7; }
body { margin: 0; color: #111; font: 10.5pt/1.45 system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
.sheet { background: #fff; max-width: 210mm; margin: 10mm auto; padding: 16mm 15mm; box-shadow: 0 2px 14px rgba(0,0,0,.12); }
@media print { html { background: #fff; } .sheet { margin: 0; padding: 0; box-shadow: none; max-width: none; } }
h1 { font-size: 16pt; margin: 18pt 0 8pt; padding-bottom: 4pt; border-bottom: 2px solid #2a78d6; break-after: avoid; }
h1.pb { break-before: page; }
h2 { font-size: 12pt; margin: 14pt 0 6pt; break-after: avoid; }
p { margin: 0 0 8pt; } p.small, figcaption, .side { font-size: 8.8pt; color: #333; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 10pt; break-inside: avoid; }
table.data th, table.data td { border-bottom: 1px solid #dcdbd6; padding: 4pt 5pt; vertical-align: top; font-size: 9pt; }
table.data th { background: #f0efec; text-align: left; font-weight: 600; }
table.data .r { text-align: right; font-variant-numeric: tabular-nums; } .nowrap { white-space: nowrap; }
table.kv th { text-align: left; font-weight: 600; color: #52514e; width: 34%; padding: 2.5pt 8pt 2.5pt 0; vertical-align: top; font-size: 9.5pt; }
table.kv td { padding: 2.5pt 0; font-size: 9.5pt; overflow-wrap: anywhere; } table.kv.wide th { width: 28%; }
table.kv2 { margin-bottom: 0; } table.kv2 th, table.kvl th { width: 17%; } table.kv2 td { width: 33%; padding-right: 8pt; }
table.kv2 th, table.kv2 td, table.kvl th, table.kvl td { padding-top: 1.5pt; padding-bottom: 1.5pt; font-size: 9pt; }
figure.shape { max-width: 76mm; }
.figrow { display: grid; gap: 7mm; margin: 6pt 0 10pt; break-inside: avoid; align-items: start; }
figure { margin: 0; break-inside: avoid; } figure svg { width: 100%; height: auto; display: block; }
.ft { font-weight: 600; font-size: 10pt; margin-bottom: 3pt; } figcaption { margin-top: 4pt; } .side { padding-top: 18pt; }
ul { margin: 0 0 10pt; padding-left: 16pt; font-size: 9pt; } li { margin-bottom: 3pt; }
.cover-top { display: flex; justify-content: space-between; align-items: flex-start; gap: 10mm; border-bottom: 3px solid #2a78d6; padding-bottom: 6mm; }
.cover-top h1 { border: 0; font-size: 22pt; margin: 0 0 3pt; } .cover-top .sub { color: #52514e; font-size: 11pt; }
.cover-top img { max-height: 22mm; max-width: 55mm; object-fit: contain; }
.foot { margin-top: 12mm; font-size: 8pt; color: #77766f; border-top: 1px solid #dcdbd6; padding-top: 3mm; }
</style></head><body><div class="sheet">
${out.join('\n')}
<div class="foot">${esc(foot)} · Generated ${esc(new Date().toLocaleString())} with the Sidewall Deflection Analyzer.</div>
</div></body></html>`;
}

function coverRows(list) {
  const R = REPORT;
  return [['Project', R.project], ['Project / request no.', R.number], ['Customer / department', R.customer], ['Prepared by', R.preparedBy],
    ['Reviewed / approved by', R.reviewedBy], ['Report date', R.date], ['Tires in this report', list.map((t) => t.label).join('; ')]]
    .filter(([, v]) => v);
}
function coverHTML(list) {
  const R = REPORT;
  return `<div class="cover-top"><div><h1>${esc(R.title || 'Sidewall Deflection Analysis')}</h1><div class="sub">Sidewall deflection under load: bulge, tilt and bending</div></div>${R.logo ? `<img src="${R.logo}" alt="logo">` : ''}</div>
    <table class="kv" style="margin-top:6mm">${coverRows(list).map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join('')}</table>
    ${R.objective ? `<h2>Objective</h2><p>${esc(R.objective).replace(/\n/g, '<br>')}</p>` : ''}
    ${R.conclusions ? `<h2>Conclusions</h2><p>${esc(R.conclusions).replace(/\n/g, '<br>')}</p>` : ''}`;
}

// ============================================================== PDF: fonts and text
const PDF_W = 595.28, PDF_H = 841.89;
const HELV = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584];
const HELVB = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584];
const WIN_EXTRA = { '€': 0x80, '‚': 0x82, '„': 0x84, '…': 0x85, '‘': 0x91, '’': 0x92, '“': 0x93, '”': 0x94, '•': 0x95, '–': 0x96, '—': 0x97, '™': 0x99 };
const WIN_SUB = { '≈': '~', '→': '->', '←': '<-', '⇕': '', '▲': '^', '▼': 'v', 'Δ': 'd', 'κ': 'k', 'θ': 'th', '≥': '>=', '≤': '<=', '−': '-', '✎': '', ' ': ' ', ' ': ' ', ' ': ' ', '​': '' };
const EXTRA_W = { 0x80: 556, 0x82: 222, 0x84: 333, 0x85: 1000, 0x91: 222, 0x92: 222, 0x93: 333, 0x94: 333, 0x95: 350, 0x96: 556, 0x97: 1000, 0x99: 1000 };
/** JS text -> WinAnsi bytes (as a binary string) for the standard PDF fonts */
function toWin(str) {
  let out = '';
  for (const ch of String(str)) {
    const c = ch.codePointAt(0);
    if (c >= 32 && c <= 126) out += ch;
    else if (WIN_EXTRA[ch]) out += String.fromCharCode(WIN_EXTRA[ch]);
    else if (ch in WIN_SUB) out += WIN_SUB[ch];
    else if (c >= 160 && c <= 255) out += ch;
    else if (c === 9 || c === 10 || c === 13) out += ' ';
    else out += '?';
  }
  return out;
}
function charW(code, bold) {
  if (code >= 32 && code <= 126) return (bold ? HELVB : HELV)[code - 32];
  if (EXTRA_W[code]) return EXTRA_W[code] + (bold && (code === 0x93 || code === 0x94) ? 167 : 0);
  if (code === 0xb0) return 400;
  if (code === 0xb2 || code === 0xb3 || code === 0xb9) return 333;
  if (code === 0xb7) return 278;
  if (code === 0xd7 || code === 0xf7 || code === 0xb1) return 584;
  return 556;
}
function winWidth(w, size, bold) { let s = 0; for (let i = 0; i < w.length; i++) s += charW(w.charCodeAt(i), bold); return (s * size) / 1000; }
const pdfStr = (w) => `(${w.replace(/[\\()]/g, '\\$&').replace(/[\x80-\xff]/g, (c) => `\\${c.charCodeAt(0).toString(8).padStart(3, '0')}`)})`;
const P = (v) => String(Math.round(v * 100) / 100);
const decodeEnt = (s) => s.replace(/&(#x?[0-9a-fA-F]+|amp|lt|gt|quot|#39|apos|nbsp);/g, (_, e) => (e[0] === '#'
  ? String.fromCodePoint(e[1] === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10))
  : { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' }[e]));
/** "<b>bold</b> plain" -> [{t, b}] (other tags dropped) */
function htmlRuns(html) {
  const out = [], re = /<b>([\s\S]*?)<\/b>/g;
  let last = 0, mm;
  while ((mm = re.exec(html))) {
    if (mm.index > last) out.push({ t: html.slice(last, mm.index), b: false });
    out.push({ t: mm[1], b: true });
    last = re.lastIndex;
  }
  if (last < html.length) out.push({ t: html.slice(last), b: false });
  return out.map((r) => ({ t: decodeEnt(r.t.replace(/<br\s*\/?>/g, ' ').replace(/<[^>]+>/g, '')).replace(/\s+/g, ' '), b: r.b })).filter((r) => r.t);
}
/** Greedy line wrap of styled runs; long words are broken. */
function wrapRuns(runs, size, width) {
  const toks = [];
  for (const r of runs) for (const part of r.t.split(/(\s+)/)) if (part) toks.push({ w: toWin(/^\s+$/.test(part) ? ' ' : part), b: r.b, sp: /^\s+$/.test(part) });
  const lines = [];
  let cur = [], cw = 0;
  const flush = () => {
    while (cur.length && cur[cur.length - 1].sp) cw -= winWidth(cur.pop().w, size, false);
    if (cur.length) lines.push({ segs: cur, width: cw });
    cur = []; cw = 0;
  };
  for (const tk of toks) {
    if (tk.sp && !cur.length) continue;
    let w = winWidth(tk.w, size, tk.b);
    if (!tk.sp && cw + w > width && cur.length) flush();
    if (!tk.sp && w > width) { // break a very long word
      let piece = '';
      for (const ch of tk.w) {
        if (winWidth(piece + ch, size, tk.b) > width && piece) { cur.push({ ...tk, w: piece }); cw += winWidth(piece, size, tk.b); flush(); piece = ''; }
        piece += ch;
      }
      tk.w = piece; w = winWidth(piece, size, tk.b);
    }
    cur.push(tk); cw += w;
  }
  flush();
  return lines;
}

// ============================================================== PDF: document writer
async function deflateBytes(u8) {
  const s = new Blob([u8]).stream().pipeThrough(new CompressionStream('deflate'));
  return new Uint8Array(await new Response(s).arrayBuffer());
}
const latin1 = (s) => { const u = new Uint8Array(s.length); for (let i = 0; i < s.length; i++) u[i] = s.charCodeAt(i) & 255; return u; };

class PdfWriter {
  constructor() { this.pages = []; this.gs = new Map(); this.images = []; }
  addPage() { const p = { ops: [] }; this.pages.push(p); return p; }
  gsName(fa, sa) {
    const k = `${fa.toFixed(3)}|${sa.toFixed(3)}`;
    if (!this.gs.has(k)) this.gs.set(k, { name: `GS${this.gs.size}`, fa, sa });
    return this.gs.get(k).name;
  }
  addJpeg(bytes, w, h) { const name = `Im${this.images.length}`; this.images.push({ name, bytes, w, h }); return name; }
  async save(info) {
    const chunks = [], offs = [];
    let len = 0, n = 0;
    const push = (u) => { chunks.push(u); len += u.length; };
    const id = () => ++n;
    const cat = id(), pagesId = id(), f1 = id(), f2 = id(), resId = id(), infoId = id();
    const gsIds = [...this.gs.values()].map((g) => ({ ...g, id: id() }));
    const imgs = this.images.map((im) => ({ ...im, id: id() }));
    const pids = this.pages.map(() => ({ page: id(), content: id() }));
    const obj = (i, body, stream) => {
      offs[i] = len;
      push(latin1(`${i} 0 obj\n${body}\n`));
      if (stream) { push(latin1('stream\n')); push(stream); push(latin1('\nendstream\n')); }
      push(latin1('endobj\n'));
    };
    push(latin1('%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'));
    obj(cat, `<< /Type /Catalog /Pages ${pagesId} 0 R >>`);
    obj(pagesId, `<< /Type /Pages /Kids [${pids.map((p) => `${p.page} 0 R`).join(' ')}] /Count ${pids.length} >>`);
    obj(f1, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>');
    obj(f2, '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>');
    obj(resId, `<< /Font << /F1 ${f1} 0 R /F2 ${f2} 0 R >> /ExtGState << ${gsIds.map((g) => `/${g.name} ${g.id} 0 R`).join(' ')} >> /XObject << ${imgs.map((im) => `/${im.name} ${im.id} 0 R`).join(' ')} >> >>`);
    const d = new Date(), pad = (v) => String(v).padStart(2, '0');
    const stamp = `D:${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
    obj(infoId, `<< /Title ${pdfStr(toWin(info.title || ''))} /Author ${pdfStr(toWin(info.author || ''))} /Subject ${pdfStr(toWin(info.subject || ''))} /Creator (Sidewall Deflection Analyzer) /Producer (Sidewall Deflection Analyzer) /CreationDate (${stamp}) >>`);
    for (const g of gsIds) obj(g.id, `<< /Type /ExtGState /ca ${g.fa.toFixed(3)} /CA ${g.sa.toFixed(3)} >>`);
    for (const im of imgs) obj(im.id, `<< /Type /XObject /Subtype /Image /Width ${im.w} /Height ${im.h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${im.bytes.length} >>`, im.bytes);
    for (let k = 0; k < this.pages.length; k++) {
      const content = await deflateBytes(latin1(this.pages[k].ops.join('\n')));
      obj(pids[k].page, `<< /Type /Page /Parent ${pagesId} 0 R /MediaBox [0 0 ${PDF_W} ${PDF_H}] /Resources ${resId} 0 R /Contents ${pids[k].content} 0 R >>`);
      obj(pids[k].content, `<< /Length ${content.length} /Filter /FlateDecode >>`, content);
    }
    const xref = len;
    let x = `xref\n0 ${n + 1}\n0000000000 65535 f \n`;
    for (let i = 1; i <= n; i++) x += `${String(offs[i]).padStart(10, '0')} 00000 n \n`;
    x += `trailer\n<< /Size ${n + 1} /Root ${cat} 0 R /Info ${infoId} 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
    push(latin1(x));
    const out = new Uint8Array(len);
    let p = 0;
    for (const c of chunks) { out.set(c, p); p += c.length; }
    return out;
  }
}

function parseColor(c) {
  if (!c || c === 'none' || c === 'transparent') return null;
  c = String(c).trim();
  let mm;
  if ((mm = /^#([0-9a-f]{3})$/i.exec(c))) return [...mm[1]].map((h) => parseInt(h + h, 16) / 255).concat(1);
  if ((mm = /^#([0-9a-f]{6})([0-9a-f]{2})?$/i.exec(c))) return [0, 2, 4].map((i) => parseInt(mm[1].slice(i, i + 2), 16) / 255).concat(mm[2] ? parseInt(mm[2], 16) / 255 : 1);
  if ((mm = /^rgba?\(([^)]+)\)$/i.exec(c))) { const v = mm[1].split(',').map((s) => parseFloat(s)); return [v[0] / 255, v[1] / 255, v[2] / 255, v[3] ?? 1]; }
  return c === 'white' ? [1, 1, 1, 1] : [0, 0, 0, 1];
}
const rgbOps = (c) => `${c[0].toFixed(3)} ${c[1].toFixed(3)} ${c[2].toFixed(3)}`;

/** Draw one of the tool's SVG charts as PDF vector graphics, top-left at (x0, yTop), given width. */
function svgToPdf(pdf, ops, markup, x0, yTop, width) {
  const svg = new DOMParser().parseFromString(markup, 'image/svg+xml').documentElement;
  if (!svg || svg.localName !== 'svg') throw new Error('a chart could not be converted for the PDF');
  const vb = (svg.getAttribute('viewBox') || '0 0 100 100').split(/[\s,]+/).map(Number);
  const s = width / vb[2];
  const M0 = [s, 0, 0, -s, x0 - s * vb[0], yTop + s * vb[1]];
  const clips = {};
  for (const cp of svg.getElementsByTagName('clipPath')) { const r = cp.getElementsByTagName('rect')[0]; if (r) clips[cp.getAttribute('id')] = r; }
  const num = (el, a, d = 0) => { const v = parseFloat(el.getAttribute(a)); return isFinite(v) ? v : d; };
  const mul = (A, Bm) => [A[0] * Bm[0] + A[2] * Bm[1], A[1] * Bm[0] + A[3] * Bm[1], A[0] * Bm[2] + A[2] * Bm[3], A[1] * Bm[2] + A[3] * Bm[3],
    A[0] * Bm[4] + A[2] * Bm[5] + A[4], A[1] * Bm[4] + A[3] * Bm[5] + A[5]];
  const ap = (M, x, y) => [M[0] * x + M[2] * y + M[4], M[1] * x + M[3] * y + M[5]];
  const scaleOf = (M) => Math.sqrt(Math.abs(M[0] * M[3] - M[1] * M[2]));
  const parseT = (tr) => {
    let M = [1, 0, 0, 1, 0, 0];
    for (const mm of tr.matchAll(/(translate|rotate|scale|matrix)\(([^)]*)\)/g)) {
      const a = mm[2].split(/[\s,]+/).filter(Boolean).map(Number);
      let T;
      if (mm[1] === 'translate') T = [1, 0, 0, 1, a[0] || 0, a[1] || 0];
      else if (mm[1] === 'scale') T = [a[0], 0, 0, a[1] ?? a[0], 0, 0];
      else if (mm[1] === 'rotate') {
        const r = ((a[0] || 0) * Math.PI) / 180, c = Math.cos(r), sn = Math.sin(r);
        T = [c, sn, -sn, c, 0, 0];
        if (a.length === 3) T = mul(mul([1, 0, 0, 1, a[1], a[2]], T), [1, 0, 0, 1, -a[1], -a[2]]);
      } else T = a;
      M = mul(M, T);
    }
    return M;
  };
  const ATTRS = ['fill', 'stroke', 'stroke-width', 'stroke-dasharray', 'stroke-linecap', 'stroke-linejoin', 'font-size', 'font-weight', 'text-anchor', 'dominant-baseline'];
  const styleOf = (el, inh) => { const st = { ...inh, opacity: el.getAttribute('opacity') ?? 1 }; for (const a of ATTRS) { const v = el.getAttribute(a); if (v != null) st[a] = v; } return st; };
  const paint = (st, M, path) => {
    const fill = parseColor(st.fill ?? 'black'), stroke = parseColor(st.stroke ?? 'none');
    if ((!fill && !stroke) || !path.length) return;
    const op = parseFloat(st.opacity ?? 1);
    const fa = fill ? fill[3] * op : 1, sa = stroke ? stroke[3] * op : 1;
    ops.push('q');
    if (fa < 0.999 || sa < 0.999) ops.push(`/${pdf.gsName(fa, sa)} gs`);
    if (fill) ops.push(`${rgbOps(fill)} rg`);
    if (stroke) {
      const sc = scaleOf(M);
      ops.push(`${rgbOps(stroke)} RG ${P(parseFloat(st['stroke-width'] ?? 1) * sc)} w ${{ round: 1, square: 2 }[st['stroke-linecap']] ?? 0} J ${{ round: 1, bevel: 2 }[st['stroke-linejoin']] ?? 0} j`);
      const da = st['stroke-dasharray'];
      if (da && da !== 'none') ops.push(`[${da.split(/[\s,]+/).filter(Boolean).map((v) => P(parseFloat(v) * sc)).join(' ')}] 0 d`);
    }
    ops.push(...path, fill && stroke ? 'B' : fill ? 'f' : 'S', 'Q');
  };
  const poly = (M, pts, close) => pts.map(([x, y], i) => { const p = ap(M, x, y); return `${P(p[0])} ${P(p[1])} ${i ? 'l' : 'm'}`; }).concat(close ? ['h'] : []);
  const pathD = (M, d) => {
    const out = [], tk = d.match(/[MLHVZmlhvz]|-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?/gi) || [];
    let i = 0, cmd = 'M', x = 0, y = 0, sx = 0, sy = 0;
    const nx = () => parseFloat(tk[i++]);
    while (i < tk.length) {
      if (/^[A-Za-z]$/.test(tk[i])) cmd = tk[i++];
      const C = cmd.toUpperCase(), rel = cmd !== C;
      if (C === 'Z') { out.push('h'); x = sx; y = sy; continue; }
      if (C === 'M' || C === 'L') {
        let a = nx(), b = nx();
        if (rel) { a += x; b += y; }
        x = a; y = b;
        const p = ap(M, x, y);
        out.push(`${P(p[0])} ${P(p[1])} ${C === 'M' ? 'm' : 'l'}`);
        if (C === 'M') { sx = x; sy = y; cmd = rel ? 'l' : 'L'; }
      } else if (C === 'H' || C === 'V') {
        const v = nx();
        if (C === 'H') x = rel ? x + v : v; else y = rel ? y + v : v;
        const p = ap(M, x, y);
        out.push(`${P(p[0])} ${P(p[1])} l`);
      } else i++;
    }
    return out;
  };
  const circle = (M, cx, cy, r) => {
    const k = 0.5523 * r, pt = (x, y) => { const p = ap(M, x, y); return `${P(p[0])} ${P(p[1])}`; };
    return [`${pt(cx + r, cy)} m`, `${pt(cx + r, cy + k)} ${pt(cx + k, cy + r)} ${pt(cx, cy + r)} c`, `${pt(cx - k, cy + r)} ${pt(cx - r, cy + k)} ${pt(cx - r, cy)} c`,
      `${pt(cx - r, cy - k)} ${pt(cx - k, cy - r)} ${pt(cx, cy - r)} c`, `${pt(cx + k, cy - r)} ${pt(cx + r, cy - k)} ${pt(cx + r, cy)} c`, 'h'];
  };
  const text = (el, st, M) => {
    const str = el.textContent;
    if (!str.trim()) return;
    const fill = parseColor(st.fill ?? 'black');
    if (!fill) return;
    const fs = parseFloat(st['font-size'] ?? 12), bold = /^(bold|[6-9]\d\d)$/.test(String(st['font-weight'] ?? ''));
    const w = toWin(str), tw = winWidth(w, fs, bold);
    let x = num(el, 'x'), y = num(el, 'y');
    if (st['text-anchor'] === 'middle') x -= tw / 2; else if (st['text-anchor'] === 'end') x -= tw;
    if (st['dominant-baseline'] === 'middle' || st['dominant-baseline'] === 'central') y += 0.35 * fs;
    const [px, py] = ap(M, x, y), L = Math.hypot(M[0], M[1]), cx = M[0] / L, cy = M[1] / L, F = fs * L;
    const op = parseFloat(st.opacity ?? 1) * fill[3];
    ops.push('q');
    if (op < 0.999) ops.push(`/${pdf.gsName(op, op)} gs`);
    ops.push(`BT /${bold ? 'F2' : 'F1'} 1 Tf ${rgbOps(fill)} rg ${P(cx * F)} ${P(cy * F)} ${P(-cy * F)} ${P(cx * F)} ${P(px)} ${P(py)} Tm ${pdfStr(w)} Tj ET`, 'Q');
  };
  const walk = (el, M, inh) => {
    for (const c of el.children) {
      const tag = c.localName, cls = c.getAttribute('class') || '';
      if (/^(clipPath|title|defs|desc|style)$/.test(tag) || /\b(hx|hit|handle)\b/.test(cls) || c.getAttribute('visibility') === 'hidden') continue;
      const tr = c.getAttribute('transform'), Mc = tr ? mul(M, parseT(tr)) : M;
      const st = styleOf(c, inh);
      if (tag === 'g' || tag === 'svg') {
        const cp = /url\(#([^)]+)\)/.exec(c.getAttribute('clip-path') || '');
        const r = cp && clips[cp[1]];
        if (r) {
          const x = num(r, 'x'), y = num(r, 'y'), w = num(r, 'width'), h = num(r, 'height');
          ops.push('q', ...poly(Mc, [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], true), 'W n');
          walk(c, Mc, st);
          ops.push('Q');
        } else walk(c, Mc, st);
      } else if (tag === 'rect') {
        const x = num(c, 'x'), y = num(c, 'y'), w = num(c, 'width'), h = num(c, 'height');
        if (w > 0 && h > 0) paint(st, Mc, poly(Mc, [[x, y], [x + w, y], [x + w, y + h], [x, y + h]], true));
      } else if (tag === 'line') {
        paint({ ...st, fill: 'none' }, Mc, poly(Mc, [[num(c, 'x1'), num(c, 'y1')], [num(c, 'x2'), num(c, 'y2')]], false));
      } else if (tag === 'path') paint(st, Mc, pathD(Mc, c.getAttribute('d') || ''));
      else if (tag === 'circle') paint(st, Mc, circle(Mc, num(c, 'cx'), num(c, 'cy'), num(c, 'r')));
      else if (tag === 'text') text(c, st, Mc);
    }
  };
  walk(svg, M0, { fill: 'black' });
  return (vb[3] / vb[2]) * width;
}

// ============================================================== PDF: page layout
const PC = { ink: [0.043, 0.043, 0.043], ink2: [0.322, 0.318, 0.306], muted: [0.467, 0.463, 0.435], rule: [0.863, 0.859, 0.839], band: [0.941, 0.937, 0.925], accent: [0.165, 0.471, 0.839] };

class PdfLayout {
  constructor(pdf) { this.pdf = pdf; this.ml = 46; this.mr = 46; this.mt = 50; this.mb = 56; this.cw = PDF_W - this.ml - this.mr; this.pages = []; this.newPage(); }
  newPage() { this.page = this.pdf.addPage(); this.pages.push(this.page); this.y = PDF_H - this.mt; }
  need(h) { if (this.y - h < this.mb && this.y < PDF_H - this.mt - 1) { this.newPage(); return true; } return false; }
  lineOps(segs, x, y, size, color) {
    let o = `BT ${rgbOps(color)} rg 1 0 0 1 ${P(x)} ${P(y)} Tm`, cur = null, buf = '';
    const flush = () => { if (buf) o += ` /${cur ? 'F2' : 'F1'} ${size} Tf ${pdfStr(buf)} Tj`; buf = ''; };
    for (const sg of segs) { if (sg.b !== cur) { flush(); cur = sg.b; } buf += sg.w; }
    flush();
    return `${o} ET`;
  }
  /** wrapped text at (x, top y), returns height used; draws on the current page */
  textBlock(html, x, yTop, width, size = 9.5, color = PC.ink, lh = 1.38, bold = false) {
    const runs = typeof html === 'string' ? htmlRuns(html) : html;
    if (bold) runs.forEach((r) => { r.b = true; });
    const lines = wrapRuns(runs, size, width);
    lines.forEach((ln, i) => this.page.ops.push(this.lineOps(ln.segs, x, yTop - size * 0.82 - i * size * lh, size, color)));
    return lines.length * size * lh;
  }
  measure(html, width, size = 9.5, lh = 1.38) { return wrapRuns(htmlRuns(html), size, width).length * size * lh; }
  para(html, o = {}) {
    const size = o.size || 9.5, lh = 1.38;
    const lines = wrapRuns(htmlRuns(html), size, this.cw);
    for (const ln of lines) {
      this.need(size * lh);
      this.page.ops.push(this.lineOps(ln.segs, this.ml, this.y - size * 0.82, size, o.color || PC.ink));
      this.y -= size * lh;
    }
    this.y -= o.after ?? 6;
  }
  heading(text, level, pageBreak) {
    const size = level === 1 ? 15 : 11.5;
    if (pageBreak && this.y < PDF_H - this.mt - 1) this.newPage();
    else this.need(size * 1.4 + 90);
    this.y -= level === 1 ? 4 : 6;
    const h = this.textBlock([{ t: text, b: true }], this.ml, this.y, this.cw, size, PC.ink);
    this.y -= h + 2;
    if (level === 1) { this.page.ops.push(`${rgbOps(PC.accent)} RG 1.6 w ${P(this.ml)} ${P(this.y)} m ${P(this.ml + this.cw)} ${P(this.y)} l S`); this.y -= 8; }
    else this.y -= 2;
  }
  kv(rows, keyFrac = 0.32) {
    const kw = this.cw * keyFrac, vw = this.cw - kw - 8, size = 9;
    for (const [k, v] of rows) {
      const hk = this.measure(k, kw, size), hv = this.measure(String(v ?? '–'), vw, size), h = Math.max(hk, hv) + 3;
      this.need(h);
      this.textBlock([{ t: k, b: true }], this.ml, this.y, kw, size, PC.ink2);
      this.textBlock(esc(String(v ?? '–')), this.ml + kw + 8, this.y, vw, size, PC.ink);
      this.y -= h;
    }
    this.y -= 6;
  }
  kv2(rows, long) {
    const gap = 16, colW = (this.cw - gap) / 2, kw = colW * 0.4, vw = colW - kw - 6, size = 9, half = Math.ceil(rows.length / 2);
    for (let i = 0; i < half; i++) {
      const pair = [rows[i], rows[i + half]].filter(Boolean);
      const h = Math.max(...pair.map(([k, v]) => Math.max(this.measure(k, kw, size), this.measure(String(v ?? '–'), vw, size)))) + 3;
      this.need(h);
      pair.forEach(([k, v], j) => {
        const x = this.ml + j * (colW + gap);
        this.textBlock([{ t: k, b: true }], x, this.y, kw, size, PC.ink2);
        this.textBlock(esc(String(v ?? '–')), x + kw + 6, this.y, vw, size, PC.ink);
      });
      this.y -= h;
    }
    this.kv(long, 0.2);
  }
  table(head, rows, widths, align, size = 8.4) {
    const tot = widths.reduce((a, b) => a + b, 0), ws = widths.map((w) => (w / tot) * this.cw), pad = 4;
    const xs = ws.map((_, i) => this.ml + ws.slice(0, i).reduce((a, b) => a + b, 0));
    const rowH = (cells, bold) => Math.max(...cells.map((c, i) => wrapRuns([{ t: String(c ?? '–'), b: bold }], size, ws[i] - 2 * pad).length)) * size * 1.3 + 2 * pad;
    const drawRow = (cells, bold, fill) => {
      const h = rowH(cells, bold);
      if (fill) this.page.ops.push(`${rgbOps(fill)} rg ${P(this.ml)} ${P(this.y - h)} ${P(this.cw)} ${P(h)} re f`);
      cells.forEach((c, i) => {
        const lines = wrapRuns([{ t: String(c ?? '–'), b: bold }], size, ws[i] - 2 * pad);
        lines.forEach((ln, k) => {
          const x = align[i] === 'r' ? xs[i] + ws[i] - pad - ln.width : xs[i] + pad;
          this.page.ops.push(this.lineOps(ln.segs, x, this.y - pad - size * 0.82 - k * size * 1.3, size, bold ? PC.ink : PC.ink));
        });
      });
      this.page.ops.push(`${rgbOps(PC.rule)} RG 0.6 w ${P(this.ml)} ${P(this.y - h)} m ${P(this.ml + this.cw)} ${P(this.y - h)} l S`);
      this.y -= h;
    };
    this.need(rowH(head, true) + rowH(rows[0] || head, false));
    drawRow(head, true, PC.band);
    for (const r of rows) {
      if (this.need(rowH(r, false))) drawRow(head, true, PC.band);
      drawRow(r, false, null);
    }
    this.y -= 8;
  }
  figrow(items, widths) {
    const gap = 18, n = items.length, fr = widths || items.map(() => 1 / n);
    const ws = fr.map((w) => w * (this.cw - gap * (n - 1)));
    const heights = items.map((it, i) => {
      if (!it.svgs) return it.html ? this.measure(it.html, ws[i], 8.4, 1.36) + 18 : 0;
      const svgH = it.svgs.reduce((h, sv) => { const vb = (/viewBox="([^"]+)"/.exec(sv) || [, '0 0 1 1'])[1].split(/\s+/).map(Number); return h + (vb[3] / vb[2]) * ws[i]; }, 0);
      return (it.title ? 15 : 0) + svgH + (it.caption ? this.measure(it.caption, ws[i], 8.4, 1.36) + 6 : 0);
    });
    const H = Math.max(...heights);
    this.need(H + 6);
    let x = this.ml;
    items.forEach((it, i) => {
      let y = this.y;
      if (it.svgs) {
        if (it.title) { this.textBlock([{ t: it.title, b: true }], x, y, ws[i], 9.6, PC.ink); y -= 15; }
        for (const sv of it.svgs) y -= svgToPdf(this.pdf, this.page.ops, sv, x, y, ws[i]);
        if (it.caption) { y -= 6; this.textBlock(it.caption, x, y, ws[i], 8.4, PC.ink2, 1.36); }
      } else if (it.html) this.textBlock(it.html, x, y - 18, ws[i], 8.4, PC.ink2, 1.36);
      x += ws[i] + gap;
    });
    this.y -= H + 10;
  }
  bullets(items) {
    for (const it of items) {
      const h = this.measure(it, this.cw - 12, 8.6, 1.36);
      this.need(h + 3);
      this.page.ops.push(`${rgbOps(PC.ink2)} rg ${P(this.ml + 3)} ${P(this.y - 6.2)} 2.4 2.4 re f`);
      this.textBlock(it, this.ml + 12, this.y, this.cw - 12, 8.6, PC.ink, 1.36);
      this.y -= h + 3;
    }
    this.y -= 6;
  }
}

async function logoJpeg(dataUrl) {
  const img = new Image();
  await new Promise((ok, bad) => { img.onload = ok; img.onerror = () => bad(new Error('logo could not be read')); img.src = dataUrl; });
  const scale = Math.min(1, 900 / img.naturalWidth);
  const w = Math.max(1, Math.round(img.naturalWidth * scale)), h = Math.max(1, Math.round(img.naturalHeight * scale));
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const ctx = c.getContext('2d');
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, w, h); ctx.drawImage(img, 0, 0, w, h);
  const b64 = c.toDataURL('image/jpeg', 0.93).split(',')[1], bin = atob(b64);
  return { bytes: latin1(bin), w, h };
}

async function reportPDF(B, list) {
  const R = REPORT, pdf = new PdfWriter(), L = new PdfLayout(pdf);
  const logo = R.logo ? await logoJpeg(R.logo).catch(() => null) : null;
  for (const b of B) {
    if (b.type === 'cover') {
      let titleW = L.cw;
      if (logo) {
        const name = pdf.addJpeg(logo.bytes, logo.w, logo.h);
        const lh = Math.min(56, (150 * logo.h) / logo.w), lw = (lh * logo.w) / logo.h;
        L.page.ops.push(`q ${P(lw)} 0 0 ${P(lh)} ${P(L.ml + L.cw - lw)} ${P(L.y - lh)} cm /${name} Do Q`);
        titleW = L.cw - lw - 16;
      }
      const h1 = L.textBlock([{ t: R.title || 'Sidewall Deflection Analysis', b: true }], L.ml, L.y, titleW, 21, PC.ink, 1.2);
      L.y -= h1 + 2;
      L.y -= L.textBlock('Sidewall deflection under load: bulge, tilt and bending', L.ml, L.y, titleW, 11, PC.ink2) + 10;
      L.page.ops.push(`${rgbOps(PC.accent)} RG 2.2 w ${P(L.ml)} ${P(L.y)} m ${P(L.ml + L.cw)} ${P(L.y)} l S`);
      L.y -= 16;
      L.kv(coverRows(list));
      if (R.objective) { L.heading('Objective', 2); L.para(esc(R.objective)); }
      if (R.conclusions) { L.heading('Conclusions', 2); L.para(esc(R.conclusions)); }
    } else if (b.type === 'h1') L.heading(b.text, 1, b.pageBreak);
    else if (b.type === 'h2') L.heading(b.text, 2);
    else if (b.type === 'p') L.para(b.html, b.small ? { size: 8.4, color: PC.ink2 } : {});
    else if (b.type === 'kv') L.kv(b.rows, b.wide ? 0.3 : 0.28);
    else if (b.type === 'kv2') L.kv2(b.rows, b.long);
    else if (b.type === 'table') L.table(b.head, b.rows, b.widths, b.align);
    else if (b.type === 'kpis') L.table(['Result', 'Value', 'What it means'], b.items.map((k) => [k.k, `${k.v} ${k.u}`.trim(), k.d]), [1.5, 0.9, 4], 'lrl', 8.4);
    else if (b.type === 'figrow') L.figrow(b.items, b.widths);
    else if (b.type === 'list') L.bullets(b.items);
  }
  // footers
  const foot = toWin([R.title, R.project, R.number].filter(Boolean).join(' · '));
  L.pages.forEach((pg, i) => {
    const right = toWin(`Page ${i + 1} of ${L.pages.length}`), size = 7.8, y = L.mb - 26;
    pg.ops.push(`${rgbOps(PC.rule)} RG 0.6 w ${P(L.ml)} ${P(y + 10)} m ${P(L.ml + L.cw)} ${P(y + 10)} l S`);
    pg.ops.push(`BT ${rgbOps(PC.muted)} rg /F1 ${size} Tf 1 0 0 1 ${P(L.ml)} ${P(y)} Tm ${pdfStr(foot)} Tj ET`);
    pg.ops.push(`BT ${rgbOps(PC.muted)} rg /F1 ${size} Tf 1 0 0 1 ${P(L.ml + L.cw - winWidth(right, size, false))} ${P(y)} Tm ${pdfStr(right)} Tj ET`);
  });
  return pdf.save({ title: R.title, author: R.preparedBy, subject: [R.project, R.number].filter(Boolean).join(' · ') });
}
