/* Sidewall deflection engine: file readers + analysis.
 * A line-for-line port of sidewall/analysis.py and sidewall/report_xlsx.py so the
 * browser tool and the Python CLI give the same numbers. No dependencies; runs in
 * any modern browser and in Node 18+ (for tests).
 */
const SW = (() => {
  'use strict';
  const DROPOUT = 250;

  // ------------------------------------------------------------- numeric helpers
  const arange = (a, b, step) => {
    const n = Math.max(0, Math.ceil((b - a) / step));
    const o = new Float64Array(n);
    for (let i = 0; i < n; i++) o[i] = a + i * step;
    return o;
  };
  const minOf = (a) => { let m = Infinity; for (const v of a) if (v < m) m = v; return m; };
  const maxOf = (a) => { let m = -Infinity; for (const v of a) if (v > m) m = v; return m; };
  const argmin = (a) => { let k = 0; for (let i = 1; i < a.length; i++) if (a[i] < a[k]) k = i; return k; };
  const argmax = (a) => { let k = 0; for (let i = 1; i < a.length; i++) if (a[i] > a[k]) k = i; return k; };
  const mean = (a) => { let s = 0; for (const v of a) s += v; return s / a.length; };
  const std = (a) => { const m = mean(a); let s = 0; for (const v of a) s += (v - m) ** 2; return Math.sqrt(s / a.length); };
  const median = (a) => {
    const s = Array.from(a).sort((p, q) => p - q), n = s.length;
    return n % 2 ? s[(n - 1) / 2] : 0.5 * (s[n / 2 - 1] + s[n / 2]);
  };
  const corr = (a, b) => {
    const ma = mean(a), mb = mean(b);
    let sab = 0, saa = 0, sbb = 0;
    for (let i = 0; i < a.length; i++) {
      const p = a[i] - ma, q = b[i] - mb;
      sab += p * q; saa += p * p; sbb += q * q;
    }
    return sab / Math.sqrt(saa * sbb);
  };
  const pick = (a, idx) => Float64Array.from(idx, (i) => a[i]);
  const mask = (a, m) => Float64Array.from(a.filter((_, i) => m[i]));

  function interp(x, xp, fp) {
    const n = xp.length, out = new Float64Array(x.length);
    for (let k = 0; k < x.length; k++) {
      const v = x[k];
      if (v <= xp[0]) { out[k] = fp[0]; continue; }
      if (v >= xp[n - 1]) { out[k] = fp[n - 1]; continue; }
      let lo = 0, hi = n - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (xp[mid] <= v) lo = mid; else hi = mid; }
      out[k] = fp[lo] + (v - xp[lo]) / (xp[hi] - xp[lo]) * (fp[hi] - fp[lo]);
    }
    return out;
  }
  const interp1 = (v, xp, fp) => interp([v], xp, fp)[0];

  function solve3(M, r) { // Gaussian elimination, 3x3
    const a = M.map((row, i) => [...row, r[i]]);
    for (let c = 0; c < 3; c++) {
      let p = c;
      for (let i = c + 1; i < 3; i++) if (Math.abs(a[i][c]) > Math.abs(a[p][c])) p = i;
      [a[c], a[p]] = [a[p], a[c]];
      for (let i = c + 1; i < 3; i++) {
        const f = a[i][c] / a[c][c];
        for (let j = c; j < 4; j++) a[i][j] -= f * a[c][j];
      }
    }
    const x = [0, 0, 0];
    for (let i = 2; i >= 0; i--) {
      let s = a[i][3];
      for (let j = i + 1; j < 3; j++) s -= a[i][j] * x[j];
      x[i] = s / a[i][i];
    }
    return x;
  }
  function polyfit2(xs, ys) {
    const S = [0, 0, 0, 0, 0], T = [0, 0, 0];
    for (let i = 0; i < xs.length; i++) {
      let p = 1;
      for (let k = 0; k < 5; k++) { S[k] += p; if (k < 3) T[k] += p * ys[i]; p *= xs[i]; }
    }
    return solve3([[S[0], S[1], S[2]], [S[1], S[2], S[3]], [S[2], S[3], S[4]]], T);
  }
  const polyDeriv = (c, x, d) => (d === 0 ? c[0] + c[1] * x + c[2] * x * x : d === 1 ? c[1] + 2 * c[2] * x : 2 * c[2]);

  /** Savitzky-Golay, polyorder 2, same as scipy.signal.savgol_filter(mode="interp"). */
  function savgol(y, w, d = 0, delta = 1) {
    const n = y.length;
    if (w > n) w = n % 2 ? n : n - 1;
    const h = (w - 1) / 2, out = new Float64Array(n), scale = delta ** d;
    // interior: least-squares weights for the d-th derivative at the window centre
    const S = [0, 0, 0, 0, 0];
    for (let j = -h; j <= h; j++) { let p = 1; for (let k = 0; k < 5; k++) { S[k] += p; p *= j; } }
    const G = [[S[0], S[1], S[2]], [S[1], S[2], S[3]], [S[2], S[3], S[4]]];
    const e = [0, 0, 0]; e[d] = 1;
    const row = solve3(G, e); // row d of G^-1 (G symmetric)
    const fact = d === 2 ? 2 : 1;
    const coef = new Float64Array(w);
    for (let j = -h; j <= h; j++) coef[j + h] = fact * (row[0] + row[1] * j + row[2] * j * j) / scale;
    for (let i = h; i < n - h; i++) {
      let s = 0;
      for (let j = 0; j < w; j++) s += coef[j] * y[i - h + j];
      out[i] = s;
    }
    // edges: polynomial fitted to the first / last window
    const xs = Array.from({ length: w }, (_, i) => i);
    const cl = polyfit2(xs, y.slice(0, w)), cr = polyfit2(xs, y.slice(n - w));
    for (let i = 0; i < h; i++) out[i] = polyDeriv(cl, i, d) / scale;
    for (let i = n - h; i < n; i++) out[i] = polyDeriv(cr, i - (n - w), d) / scale;
    return out;
  }

  // --------------------------------------------------------------- file readers
  const u16 = (b, o) => b[o] | (b[o + 1] << 8);
  const u32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)) + b[o + 3] * 16777216;

  async function inflateRaw(bytes) {
    const ds = new DecompressionStream('deflate-raw');
    const stream = new Blob([bytes]).stream().pipeThrough(ds);
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  /** {name: Uint8Array} for every entry of a .zip / .xlsx */
  async function unzip(buf) {
    const b = new Uint8Array(buf);
    let eocd = -1;
    for (let i = b.length - 22; i >= Math.max(0, b.length - 65557); i--) {
      if (u32(b, i) === 0x06054b50) { eocd = i; break; }
    }
    if (eocd < 0) throw new Error('not a zip/xlsx file');
    const count = u16(b, eocd + 10);
    let p = u32(b, eocd + 16);
    const out = {}, dec = new TextDecoder();
    for (let k = 0; k < count; k++) {
      if (u32(b, p) !== 0x02014b50) break;
      const method = u16(b, p + 10), csize = u32(b, p + 20);
      const nlen = u16(b, p + 28), xlen = u16(b, p + 30), clen = u16(b, p + 32), off = u32(b, p + 42);
      const name = dec.decode(b.subarray(p + 46, p + 46 + nlen));
      const start = off + 30 + u16(b, off + 26) + u16(b, off + 28);
      out[name] = { method, data: b.subarray(start, start + csize) };
      p += 46 + nlen + xlen + clen;
    }
    return {
      names: Object.keys(out),
      async get(name) {
        const e = out[name];
        if (!e) return null;
        return e.method === 8 ? inflateRaw(e.data) : e.data;
      },
    };
  }

  /** OLE compound file -> {streamName: Uint8Array} */
  function cfbStreams(b) {
    const sig = [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1];
    if (!sig.every((v, i) => b[i] === v)) throw new Error('not an OLE compound file');
    const ssz = 1 << u16(b, 0x1e), msz = 1 << u16(b, 0x20);
    const nFat = u32(b, 0x2c), dirStart = u32(b, 0x30), cutoff = u32(b, 0x38);
    const mfatStart = u32(b, 0x3c), nMfat = u32(b, 0x40), difStart = u32(b, 0x44), nDif = u32(b, 0x48);
    const sector = (i) => b.subarray((i + 1) * ssz, (i + 2) * ssz);
    const words = (s) => Array.from({ length: s.length / 4 }, (_, i) => u32(s, 4 * i));
    let difat = Array.from({ length: 109 }, (_, i) => u32(b, 0x4c + 4 * i));
    let nxt = difStart;
    for (let k = 0; k < nDif; k++) { const v = words(sector(nxt)); difat = difat.concat(v.slice(0, -1)); nxt = v[v.length - 1]; }
    let fat = [];
    for (const i of difat.slice(0, nFat)) fat = fat.concat(words(sector(i)));
    const chain = (start, table) => {
      const out = [], seen = new Set();
      let i = start;
      while (i < 0xfffffffa && !seen.has(i) && i < table.length + 1) { seen.add(i); out.push(i); i = table[i]; }
      return out;
    };
    const concat = (parts, size) => {
      const o = new Uint8Array(size);
      let p = 0;
      for (const s of parts) { const n = Math.min(s.length, size - p); o.set(s.subarray(0, n), p); p += n; if (p >= size) break; }
      return o;
    };
    const read = (start, size) => concat(chain(start, fat).map(sector), size);
    const dirChain = chain(dirStart, fat);
    const dirs = read(dirStart, dirChain.length * ssz);
    const entries = [];
    for (let off = 0; off + 128 <= dirs.length; off += 128) {
      const nlen = u16(dirs, off + 0x40);
      let name = '';
      for (let i = 0; i < Math.max(nlen - 2, 0); i += 2) name += String.fromCharCode(u16(dirs, off + i));
      entries.push({ name, type: dirs[off + 0x42], start: u32(dirs, off + 0x74), size: u32(dirs, off + 0x78) });
    }
    const root = entries[0];
    const ministream = read(root.start, root.size);
    let mfat = [];
    if (nMfat) for (const i of chain(mfatStart, fat).slice(0, nMfat)) mfat = mfat.concat(words(sector(i)));
    const out = {};
    for (const e of entries) {
      if (e.type !== 2) continue;
      out[e.name] = e.size < cutoff
        ? concat(chain(e.start, mfat).map((i) => ministream.subarray(i * msz, (i + 1) * msz)), e.size)
        : read(e.start, e.size);
    }
    return out;
  }

  /** MS Graph datasheet: numeric cells and series titles */
  function graphDatasheet(w) {
    const cells = new Map(), titles = [];
    const dv = new DataView(w.buffer, w.byteOffset, w.byteLength);
    let p = 0;
    while (p + 4 <= w.length) {
      const type = u16(w, p), n = u16(w, p + 2), d = p + 4;
      p += 4 + n;
      if ((type === 0x0003 || type === 0x0203) && n >= 12) {
        cells.set(`${u16(w, d)},${u16(w, d + 2)}`, dv.getFloat64(d + n - 8, true));
      } else if (type === 0x0002 && n >= 9) {
        cells.set(`${u16(w, d)},${u16(w, d + 2)}`, u16(w, d + n - 2));
      } else if (type === 0x100d && n >= 4) {
        const k = w[d + 2];
        let t = '';
        if (w[d + 3]) for (let i = 0; i < k; i++) t += String.fromCharCode(u16(w, d + 4 + 2 * i));
        else for (let i = 0; i < k; i++) t += String.fromCharCode(w[d + 4 + i]);
        titles.push(t.trim());
      }
    }
    return { cells, titles };
  }

  const isStage = (v) => {
    if (v.length < 10) return false;
    let flips = 0;
    for (let i = 2; i < v.length; i++) if ((v[i - 1] - v[i - 2]) * (v[i] - v[i - 1]) < 0) flips++;
    return flips <= 3 && maxOf(v) - minOf(v) > 20;
  };

  const xmlText = (s) => s.replace(/&(#x?[0-9a-fA-F]+|amp|lt|gt|quot|apos);/g, (_, e) =>
    e[0] === '#' ? String.fromCodePoint(e[1] === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10))
      : { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }[e]);

  function sheetCells(sheetXml, sharedXml) {
    const shared = [];
    if (sharedXml) {
      for (const m of sharedXml.matchAll(/<si>([\s\S]*?)<\/si>/g)) {
        shared.push([...m[1].matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((t) => xmlText(t[1])).join(''));
      }
    }
    const cells = new Map();
    for (const m of sheetXml.matchAll(/<c ([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g)) {
      const attrs = m[1], body = m[2] || '';
      const ref = /r="([A-Z]+)(\d+)"/.exec(attrs);
      if (!ref) continue;
      const t = (/ t="([^"]+)"/.exec(attrs) || [])[1];
      const v = /<v>([\s\S]*?)<\/v>/.exec(body);
      let val;
      if (!v) {
        const is = /<is>([\s\S]*?)<\/is>/.exec(body);
        if (!is) continue;
        val = [...is[1].matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((x) => xmlText(x[1])).join('');
      } else if (t === 's') val = shared[parseInt(v[1], 10)];
      else if (t === 'str' || t === 'inlineStr' || t === 'e') val = xmlText(v[1]);
      else if (t === 'b') val = v[1] === '1';
      else val = parseFloat(v[1]);
      let col = 0;
      for (const ch of ref[1]) col = col * 26 + ch.charCodeAt(0) - 64;
      cells.set(`${ref[2]},${col}`, val);
    }
    return cells;
  }

  const normLabel = (s) => s.replace(/（/g, '(').replace(/）/g, ')').replace(/[\s:：]+$/, '').trim();
  const looksLikeLabel = (s) => /\((mm|kg|daN|PSI|°C)\)|^(Tyre Size|Brand|Product ID|Rim Type)$/.test(normLabel(s));

  function headerPairs(cells) {
    const out = {};
    const keys = [...cells.keys()].map((k) => k.split(',').map(Number)).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const get = (r, c) => cells.get(`${r},${c}`);
    for (const [r, c] of keys) {
      const v = get(r, c);
      if (typeof v !== 'string' || !v.trim()) continue;
      if (/[:：]\s*$/.test(v)) {
        for (let cc = c + 1; cc < c + 6; cc++) {
          const rv = get(r, cc);
          if (rv !== undefined && rv !== '') { out[normLabel(v)] = rv; break; }
        }
        continue;
      }
      for (let rr = r + 1; rr < r + 4; rr++) {
        const cv = get(rr, c);
        if (cv === undefined || cv === '') continue;
        if (typeof cv === 'string' && looksLikeLabel(cv)) break;
        if (!(normLabel(v) in out)) out[normLabel(v)] = cv;
        break;
      }
    }
    return out;
  }

  const FIELDS = {
    'Request No.': 'request', 'Test Date': 'test_date', 'Serial number': 'serial',
    'Test Feature': 'feature', 'Test engineer name': 'engineer', 'Product ID': 'product_id',
    'Tyre Size': 'size', 'Brand': 'brand', 'SW (mm)': 'section_width_mm', 'OD (mm)': 'od_mm',
    'Tire Weight(kg)': 'tire_weight_kg', 'Test Pressure(PSI)': 'pressure_psi',
    'Load (daN)': 'load_daN', 'Rim Type': 'rim', 'Room Temp. (°C)': 'room_temp_C',
    'Unload Distance (mm)': 'unload_distance_mm', 'Unload Position (mm)': 'unload_position_mm',
    'Load Distance (mm)': 'load_distance_mm', 'Load Position (mm)': 'load_position_mm',
    'Radial Deflection (mm)': 'radial_deflection_mm',
    'Horizontal Deflection (mm)': 'horizontal_deflection_mm',
  };

  async function readReport(name, buf) {
    const z = await unzip(buf);
    const dec = new TextDecoder();
    const sheetName = z.names.filter((n) => /^xl\/worksheets\/sheet\d+\.xml$/.test(n)).sort()[0];
    const shared = z.names.includes('xl/sharedStrings.xml') ? dec.decode(await z.get('xl/sharedStrings.xml')) : '';
    const hdr = sheetName ? headerPairs(sheetCells(dec.decode(await z.get(sheetName)), shared)) : {};
    const meta = { file: name };
    for (const [label, key] of Object.entries(FIELDS)) meta[key] = hdr[label] ?? null;
    for (const n of z.names.filter((n) => n.startsWith('xl/embeddings/')).sort()) {
      let streams;
      try { streams = cfbStreams(await z.get(n)); } catch { continue; }
      if (!streams.Workbook) continue;
      const { cells, titles } = graphDatasheet(streams.Workbook);
      const rows = new Map();
      for (const [k, v] of cells) {
        const [r, c] = k.split(',').map(Number);
        if (!rows.has(r)) rows.set(r, new Map());
        rows.get(r).set(c, v);
      }
      const series = [...rows.keys()].sort((a, b) => a - b)
        .map((r) => { const row = rows.get(r); return [...row.keys()].sort((a, b) => a - b).map((c) => row.get(c)); });
      if (series.length < 2) continue;
      let zi = series.findIndex(isStage);
      if (zi < 0) zi = 1;
      const xi = zi === 0 ? 1 : 0;
      meta.series_titles = titles.filter(Boolean);
      return { x: Float64Array.from(series[xi]), z: Float64Array.from(series[zi]), meta };
    }
    throw new Error('no embedded scan found in this workbook (expected an MS Graph chart with the profile)');
  }

  function readText(name, text) {
    const rows = text.split(/\r?\n/).filter((r) => r.trim());
    const nums = (cells) => cells.map((c) => c.trim()).filter((c) => c !== '' && !isNaN(Number(c))).map(Number);
    let x, z;
    if (rows.length === 2) {
      [x, z] = rows.map((r) => nums(r.split('\t')));
    } else {
      const sep = rows[0].includes('\t') ? '\t' : rows[0].includes(',') ? ',' : /\s+/;
      const pairs = rows.map((r) => nums(r.split(sep))).filter((p) => p.length >= 2);
      x = pairs.map((p) => p[0]); z = pairs.map((p) => p[1]);
    }
    if (!x.length || x.length !== z.length) throw new Error(`could not read two equal-length channels (got ${x.length} and ${z.length})`);
    return { x: Float64Array.from(x), z: Float64Array.from(z), meta: { file: name } };
  }

  async function readFile(name, buf) {
    if (/\.(xlsx|xlsm)$/i.test(name)) return readReport(name, buf);
    return readText(name, new TextDecoder().decode(buf));
  }

  // ------------------------------------------------------------------- analysis
  function splitPasses(z) {
    const n = z.length;
    const i = z[0] > z[n >> 1] ? argmin(z) : argmax(z);
    return [[0, i], [i, n]];
  }

  function cleanPass(name, x, z) {
    const n = x.length, keep = [];
    let nDrop = 0, nHold = 0;
    for (let i = 0; i < n; i++) {
      const ok = x[i] < DROPOUT;
      if (!ok) { nDrop++; continue; }
      if (i > 0 && x[i] === x[i - 1]) { nHold++; continue; }
      keep.push(i);
    }
    keep.sort((a, b) => z[a] - z[b]);
    return { name, xRaw: x, zRaw: z, z: pick(z, keep), x: pick(x, keep), nDropout: nDrop, nHold };
  }

  function crossingUp(p, level, zStart) {
    const xs = savgol(p.x, 9, 0);
    for (let i = 0; i < p.z.length; i++) if (p.z[i] > zStart && xs[i] >= level) return p.z[i];
    throw new Error('could not find where the sidewall meets the shoulder band');
  }

  function findBandShift(inf, load, topMm = 30, search = 8, tol = 1) {
    const zTop = maxOf(inf.z);
    const xBand = median(inf.x.filter((_, i) => inf.z[i] > zTop - topMm));
    const zMwI = inf.z[argmin(savgol(inf.x, 15, 0))];
    const zMwL = load.z[argmin(savgol(load.x, 15, 0))];
    const eI = crossingUp(inf, xBand - tol, zMwI), eL = crossingUp(load, xBand - tol, zMwL);
    const dEdge = eI - eL;
    const zg = arange(eI + 4, zTop - 1, 0.25);
    const a = interp(zg, inf.z, inf.x), zLoadMin = minOf(load.z);
    let best = [-2, dEdge, 0];
    for (const d of arange(dEdge - search, dEdge + search + 1e-9, 0.25)) {
      if (zg[0] - d < zLoadMin) continue;
      const b = interp(zg.map((v) => v - d), load.z, load.x);
      const r = corr(a, b);
      if (r > best[0]) best = [r, d, mean(b) - mean(a)];
    }
    const [r, d, off] = best;
    return { x_band: xBand, edge_inflated: eI, edge_loaded: eI - d, delta: d, delta_edge: dEdge,
      texture_corr: r, x_offset: off };
  }

  function textureCorr(inf, load, eI, d) {
    const zg = arange(eI + 4, maxOf(inf.z) - 1, 0.25);
    if (zg.length < 8 || zg[0] - d < minOf(load.z)) return null;
    const a = interp(zg, inf.z, inf.x), b = interp(zg.map((v) => v - d), load.z, load.x);
    return corr(a, b);
  }

  function fitProfile(p, xBand, zLo, zHi, windowMm = 45, uWindowMm = 5, step = 0.25) {
    const m = Array.from(p.z, (v) => v >= zLo && v <= zHi);
    const zz = mask(p.z, m), uu = mask(p.x, m).map((v) => xBand - v);
    const z = arange(minOf(zz), maxOf(zz) + 1e-9, step);
    const ul = interp(z, zz, uu);
    const sg = (wMm, d) => savgol(ul, Math.max(5, Math.round(wMm / step) | 1), d, step);
    const u = sg(uWindowMm, 0), du = sg(windowMm, 1), d2u = sg(windowMm, 2);
    const g = du.map((v) => Math.sqrt(1 + v * v));
    const seg = new Float64Array(z.length);
    for (let i = 1; i < z.length; i++) seg[i] = seg[i - 1] + 0.5 * (g[i] + g[i - 1]) * (z[i] - z[i - 1]);
    const L = seg[seg.length - 1];
    const resid = uu.map((v, i) => v - interp1(zz[i], z, u));
    return {
      name: p.name, z, u, du, theta: du.map((v) => Math.atan(v) * 180 / Math.PI),
      kappa: d2u.map((v, i) => -v / g[i] ** 3), s: seg.map((v) => L - v), sBottom: seg, length: L,
      sigma: std(resid), resid, residZ: zz,
    };
  }

  function matchByArclength(pi, pl, anchor = 'bottom', ds = 0.25) {
    const s = arange(0, Math.min(pi.length, pl.length), ds);
    const at = (p, arr) => (anchor === 'bottom' ? interp(s, p.sBottom, arr)
      : interp(s, Float64Array.from(p.s).reverse(), Float64Array.from(arr).reverse()));
    const zi = at(pi, pi.z), zl = at(pl, pl.z), ui = at(pi, pi.u), ul = at(pl, pl.u);
    const ki = at(pi, pi.kappa), kl = at(pl, pl.kappa), ti = at(pi, pi.theta), tl = at(pl, pl.theta);
    return { s, z_inf: zi, z_load: zl, u_inf: ui, u_load: ul,
      du: ul.map((v, i) => v - ui[i]), dz: zl.map((v, i) => v - zi[i]), dtheta: tl.map((v, i) => v - ti[i]),
      kappa_inf: ki, kappa_load: kl, dkappa: kl.map((v, i) => v - ki[i]) };
  }

  function gradient(a) {
    const n = a.length, g = new Float64Array(n);
    if (n < 2) return g;
    g[0] = a[1] - a[0]; g[n - 1] = a[n - 1] - a[n - 2];
    for (let i = 1; i < n - 1; i++) g[i] = (a[i + 1] - a[i - 1]) / 2;
    return g;
  }

  function localExtrema(y, x, n = 3, minSep = 15) {
    const order = Array.from(y.keys()).sort((a, b) => Math.abs(y[b]) - Math.abs(y[a]));
    const picked = [];
    for (const i of order) {
      if (i > 0 && i < y.length - 1 && picked.every((j) => Math.abs(x[i] - x[j]) >= minSep)
          && Math.abs(y[i]) >= Math.abs(y[i - 1]) && Math.abs(y[i]) >= Math.abs(y[i + 1])) picked.push(i);
      if (picked.length === n) break;
    }
    return picked.map((i) => [x[i], y[i]]);
  }

  function zoneTable(mt, towardRim, hMm) {
    const names = ['rim-side', 'middle', 'shoulder-side'];
    const sEnd = mt.s[mt.s.length - 1];
    const edges = names.map((_, k) => (k * sEnd) / 3).concat([sEnd]);
    const ds = gradient(mt.s);
    let bulgeTot = 0, energyTot = 0;
    for (let i = 0; i < mt.s.length; i++) { bulgeTot += Math.max(mt.du[i], 0) * ds[i]; energyTot += mt.dkappa[i] ** 2 * ds[i]; }
    return names.map((zone, k) => {
      const idx = [];
      for (let i = 0; i < mt.s.length; i++) if (mt.s[i] >= edges[k] && mt.s[i] < edges[k + 1] + (k === 2 ? 1 : 0)) idx.push(i);
      const sel = (a) => idx.map((i) => a[i]);
      const dk = sel(mt.dkappa).map(Math.abs);
      return {
        zone, s_from: edges[k], s_to: edges[k + 1],
        z_inflated_from: mt.z_inf[idx[0]], z_inflated_to: mt.z_inf[idx[idx.length - 1]],
        mean_outward_mm: mean(sel(mt.du)), max_outward_mm: maxOf(sel(mt.du)),
        mean_toward_rim_mm: mean(sel(towardRim)),
        mean_abs_rotation_deg: mean(sel(mt.dtheta).map(Math.abs)),
        peak_abs_dkappa_per_m: 1000 * maxOf(dk), peak_surface_strain_pct: 100 * hMm * maxOf(dk),
        share_of_bulge_pct: 100 * idx.reduce((s, i) => s + Math.max(mt.du[i], 0) * ds[i], 0) / bulgeTot,
        share_of_bending_energy_pct: 100 * idx.reduce((s, i) => s + mt.dkappa[i] ** 2 * ds[i], 0) / energyTot,
      };
    });
  }

  function trapezoid(y, x) {
    let s = 0;
    for (let i = 1; i < y.length; i++) s += 0.5 * (y[i] + y[i - 1]) * (x[i] - x[i - 1]);
    return s;
  }

  const num = (v) => (typeof v === 'number' && isFinite(v) ? v : null);

  function analyze(x, z, meta = {}, opts = {}) {
    const hMm = opts.hMm ?? 5, rimEnd = opts.rimEnd ?? 'bottom';
    const windowMm = opts.windowMm ?? 45, smoothMm = opts.smoothMm ?? 5;
    let loadKN = opts.loadKN ?? null;
    if (loadKN == null && num(meta.load_daN) != null) loadKN = meta.load_daN / 100;
    const [s1, s2] = splitPasses(z);
    const inf = cleanPass('inflated', x.slice(s1[0], s1[1]), z.slice(s1[0], s1[1]));
    const load = cleanPass('loaded', x.slice(s2[0], s2[1]), z.slice(s2[0], s2[1]));
    const rawMin = {};
    for (const p of [inf, load]) { const k = argmin(p.x); rawMin[p.name] = [p.x[k], p.z[k]]; }

    // automatic range, then the user's overrides (null/undefined = keep automatic)
    const bandAuto = findBandShift(inf, load);
    const rimBottom = rimEnd === 'bottom';
    const loAuto = Math.max(minOf(inf.z), minOf(load.z));
    const auto = { z_start: loAuto, edge_inflated: bandAuto.edge_inflated, edge_loaded: bandAuto.edge_loaded };
    const given = (v) => v != null && v !== '' && isFinite(v);
    const manual = given(opts.zStart) || given(opts.edgeInflated) || given(opts.edgeLoaded);
    const eI = given(opts.edgeInflated) ? +opts.edgeInflated : bandAuto.edge_inflated;
    const eL = given(opts.edgeLoaded) ? +opts.edgeLoaded : eI - bandAuto.delta;
    const band = manual
      ? { ...bandAuto, edge_inflated: eI, edge_loaded: eL, delta: eI - eL, texture_corr: textureCorr(inf, load, eI, eI - eL), manual: true }
      : { ...bandAuto, manual: false };
    let loI, loL;
    if (given(opts.zStart)) loI = loL = +opts.zStart;
    else [loI, loL] = rimBottom ? [loAuto, loAuto] : [minOf(inf.z), minOf(load.z)];
    if (!(eI - loI >= 20 && eL - loL >= 20)) {
      throw new Error('The sidewall start must be at least 20 mm below both shoulder edges.');
    }
    if (eI > maxOf(inf.z) || eL > maxOf(load.z) || loI < minOf(inf.z) - 1 || loL < minOf(load.z) - 1) {
      throw new Error('A chosen position is outside the scanned range.');
    }
    const pi = fitProfile(inf, band.x_band, loI, band.edge_inflated, windowMm, smoothMm);
    const pl = fitProfile(load, band.x_band, loL, band.edge_loaded, windowMm, smoothMm);
    const mt = matchByArclength(pi, pl, rimBottom ? 'bottom' : 'top');
    const towardRim = mt.dz.map((v) => (rimBottom ? -v : v));

    const zc = arange(Math.max(pi.z[0], pl.z[0]), Math.min(pi.z[pi.z.length - 1], pl.z[pl.z.length - 1]), 0.25);
    const ndU = interp(zc, pl.z, pl.u), ndI = interp(zc, pi.z, pi.u);
    const nd = ndU.map((v, i) => v - ndI[i]);

    const iMw = argmax(pi.u), lMw = argmax(pl.u), kMt = argmax(mt.du), kNv = argmax(nd);
    const fracFromRim = (zv, p) => { const f = (zv - p.z[0]) / (p.z[p.z.length - 1] - p.z[0]); return rimBottom ? f : 1 - f; };
    const labRd = num(meta.radial_deflection_mm) || null;
    const radial = labRd || band.delta;
    const growth = pl.u[lMw] - pi.u[iMw];
    const ds = gradient(mt.s);
    let bulgeArea = 0;
    for (let i = 0; i < mt.s.length; i++) bulgeArea += Math.max(mt.du[i], 0) * ds[i];
    const zones = zoneTable(mt, towardRim, hMm);
    const hdRaw = rawMin.inflated[0] - rawMin.loaded[0];
    const margin = windowMm / 2, sEnd = mt.s[mt.s.length - 1];
    const tIdx = Array.from(mt.s.keys()).filter((i) => mt.s[i] >= margin && mt.s[i] <= sEnd - margin);
    const ext = localExtrema(pick(mt.dkappa, tIdx), pick(mt.s, tIdx));
    let kRot = 0;
    for (let i = 1; i < mt.dtheta.length; i++) if (Math.abs(mt.dtheta[i]) > Math.abs(mt.dtheta[kRot])) kRot = i;

    const metrics = {
      samples_total: x.length,
      inflated: { samples: inf.xRaw.length, dropouts: inf.nDropout, held_repeats: inf.nHold, z_range: [minOf(inf.z), maxOf(inf.z)] },
      loaded: { samples: load.xRaw.length, dropouts: load.nDropout, held_repeats: load.nHold, z_range: [minOf(load.z), maxOf(load.z)] },
      noise_floor_mm: { inflated: pi.sigma, loaded: pl.sigma },
      band,
      deflection: {
        radial_lab_mm: labRd, shoulder_travel_mm: band.delta,
        crown_share_mm: labRd ? labRd - band.delta : null,
        sidewall_share_pct: labRd ? (100 * band.delta) / labRd : null,
        basis: labRd ? 'lab radial deflection' : 'shoulder travel (no lab value)',
      },
      load_kN: loadKN,
      vertical_stiffness_N_per_mm: loadKN ? (1000 * loadKN) / radial : null,
      max_width: {
        inflated_z: pi.z[iMw], inflated_u: pi.u[iMw], inflated_frac_from_rim: fracFromRim(pi.z[iMw], pi),
        loaded_z: pl.z[lMw], loaded_u: pl.u[lMw], loaded_frac_from_rim: fracFromRim(pl.z[lMw], pl),
        growth_mm: growth, point_shift_mm: pl.z[lMw] - pi.z[iMw],
      },
      lab_style: {
        unload_distance_mm: rawMin.inflated[0], unload_position_mm: rawMin.inflated[1],
        load_distance_mm: rawMin.loaded[0], load_position_mm: rawMin.loaded[1], horizontal_deflection_mm: hdRaw,
      },
      bulge_per_mm_deflection: growth / radial,
      bulge_per_kN_mm: loadKN ? growth / loadKN : null,
      naive_same_z: { peak_mm: nd[kNv], at_z: zc[kNv], area_mm2: trapezoid(nd.map((v) => Math.max(v, 0)), zc) },
      matched: {
        peak_outward_mm: mt.du[kMt], at_s: mt.s[kMt], inflated_z_of_that_point: mt.z_inf[kMt],
        bulge_area_mm2: bulgeArea, toward_rim_at_rim_end_mm: towardRim[0],
        toward_rim_at_far_end_mm: towardRim[towardRim.length - 1], max_rotation_deg: mt.dtheta[kRot],
      },
      arc_length: { inflated_mm: pi.length, loaded_visible_mm: pl.length, deficit_mm: pi.length - pl.length,
        deficit_pct: (100 * (pi.length - pl.length)) / pi.length },
      hinges: ext.map(([s_, k_]) => ({ s: s_, z_inflated: interp1(s_, mt.s, mt.z_inf), dkappa_per_m: 1000 * k_,
        surface_strain_pct: 100 * hMm * Math.abs(k_) })),
      assumptions: { rim_end: rimEnd, surface_offset_from_ply_mm: hMm, derivative_window_mm: windowMm, smoothing_mm: smoothMm },
      range: { z_start: loI, edge_inflated: eI, edge_loaded: eL, manual, auto },
      test: Object.fromEntries(Object.entries(meta).filter(([k]) => k !== 'series_titles')),
    };
    if (labRd) metrics.vs_lab = { horizontal_deflection_lab_mm: meta.horizontal_deflection_mm,
      horizontal_deflection_raw_mm: hdRaw, horizontal_deflection_smoothed_mm: growth };
    return { passes: { inflated: inf, loaded: load }, band, inf: pi, load: pl, matched: mt, towardRim,
      naive: { z: zc, du: nd }, metrics, zones, meta, auto, opts: { hMm, rimEnd, windowMm, smoothMm } };
  }

  /** Same scan, profiles re-smoothed for display only (numbers stay from analyze()). */
  function displayCurves(res, smoothMm) {
    const { passes, band, opts } = res;
    const rimBottom = opts.rimEnd === 'bottom';
    const pi = fitProfile(passes.inflated, band.x_band, res.inf.z[0], band.edge_inflated, opts.windowMm, smoothMm);
    const pl = fitProfile(passes.loaded, band.x_band, res.load.z[0], band.edge_loaded, opts.windowMm, smoothMm);
    const mt = matchByArclength(pi, pl, rimBottom ? 'bottom' : 'top');
    const zc = res.naive.z;
    const a = interp(zc, pl.z, pl.u), b = interp(zc, pi.z, pi.u);
    return { inf: pi, load: pl, matched: mt, towardRim: mt.dz.map((v) => (rimBottom ? -v : v)),
      naive: { z: zc, du: a.map((v, i) => v - b[i]) } };
  }

  function label(meta) {
    const parts = ['brand', 'size'].filter((k) => meta[k]).map((k) => String(meta[k]));
    if (meta.serial) parts.push(String(meta.serial));
    return parts.length ? parts.join(' | ') : String(meta.file || 'scan').replace(/\.[^.]+$/, '');
  }

  /** One flat row per tire, same columns as the Python comparison.csv */
  function summaryRow(res) {
    const m = res.metrics, t = m.test, d = m.deflection, mw = m.max_width, mt = m.matched;
    const z = Object.fromEntries(res.zones.map((r) => [r.zone, r]));
    const peak = m.hinges.length ? m.hinges.reduce((a, b) => (b.surface_strain_pct > a.surface_strain_pct ? b : a)) : {};
    return {
      file: t.file, tire: label(res.meta), size: t.size ?? null, brand: t.brand ?? null, serial: t.serial ?? null,
      test_date: t.test_date ?? null, feature: t.feature ?? null, load_daN: t.load_daN ?? null,
      pressure_psi: t.pressure_psi ?? null, rim: t.rim ?? null,
      radial_deflection_lab_mm: d.radial_lab_mm, shoulder_travel_mm: d.shoulder_travel_mm,
      crown_share_mm: d.crown_share_mm, sidewall_share_pct: d.sidewall_share_pct,
      vertical_stiffness_N_per_mm: m.vertical_stiffness_N_per_mm,
      horizontal_deflection_lab_mm: t.horizontal_deflection_mm ?? null,
      horizontal_deflection_raw_mm: m.lab_style.horizontal_deflection_mm,
      horizontal_deflection_smoothed_mm: mw.growth_mm,
      bulge_per_mm_deflection: m.bulge_per_mm_deflection, bulge_per_kN_mm: m.bulge_per_kN_mm,
      widest_point_inflated_pct_from_rim: 100 * mw.inflated_frac_from_rim,
      widest_point_loaded_pct_from_rim: 100 * mw.loaded_frac_from_rim,
      peak_outward_deflection_mm: mt.peak_outward_mm, bulge_area_mm2: mt.bulge_area_mm2,
      max_rotation_deg: mt.max_rotation_deg,
      peak_bending_strain_pct: peak.surface_strain_pct ?? null, peak_bending_height_mm: peak.z_inflated ?? null,
      rim_side_bending_share_pct: z['rim-side'].share_of_bending_energy_pct,
      middle_bending_share_pct: z.middle.share_of_bending_energy_pct,
      shoulder_side_bending_share_pct: z['shoulder-side'].share_of_bending_energy_pct,
      hidden_sidewall_when_loaded_mm: m.arc_length.deficit_mm,
      noise_mm: Math.max(m.noise_floor_mm.inflated, m.noise_floor_mm.loaded),
      range_manual: m.range.manual, range_z_start_mm: m.range.z_start,
      range_edge_inflated_mm: m.range.edge_inflated, range_edge_loaded_mm: m.range.edge_loaded,
    };
  }

  /** Google "turbo" colormap (polynomial fit). t in [0,1] -> [r,g,b] 0-255 */
  function turbo(t) {
    t = Math.min(1, Math.max(0, t));
    const r = 0.13572138 + t * (4.6153926 + t * (-42.66032258 + t * (132.13108234 + t * (-152.94239396 + t * 59.28637943))));
    const g = 0.09140261 + t * (2.19418839 + t * (4.84296658 + t * (-14.18503333 + t * (4.27729857 + t * 2.82956604))));
    const b = 0.1066733 + t * (12.64194608 + t * (-60.58204836 + t * (110.36276771 + t * (-89.90310912 + t * 27.34824973))));
    const c = (v) => Math.round(255 * Math.min(1, Math.max(0, v)));
    return [c(r), c(g), c(b)];
  }

  return { readFile, analyze, displayCurves, summaryRow, label, turbo, interp, savgol };
})();
if (typeof module !== 'undefined') module.exports = SW;
