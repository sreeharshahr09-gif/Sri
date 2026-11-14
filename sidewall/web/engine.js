/* Sidewall deflection engine: file readers + analysis.
 * A line-for-line port of sidewall/analysis.py and sidewall/report_xlsx.py so the
 * browser tool and the Python CLI give the same numbers. No dependencies; runs in
 * any modern browser and in Node 18+ (for tests).
 */
const SW = (() => {
  'use strict';

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
  // Strategy: collect every long run of numbers the file holds (sheet rows and
  // columns, embedded chart datasheets, chart caches, nested workbooks, legacy
  // .xls sheets, text columns). Then pick the (laser distance, Z position) pair
  // by content: the Z channel runs one way and comes back. Names only break ties.
  // Test details are found by label wording (several spellings and units).
  const u16 = (b, o) => b[o] | (b[o + 1] << 8);
  const u32 = (b, o) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)) + b[o + 3] * 16777216;
  const MIN_SERIES = 50;

  async function inflateRaw(bytes) {
    const ds = new DecompressionStream('deflate-raw');
    const stream = new Blob([bytes]).stream().pipeThrough(ds);
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  /** Entries of a .zip / .xlsx */
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
      async text(name) { const d = await this.get(name); return d ? new TextDecoder().decode(d) : ''; },
    };
  }

  /** A number from a cell or text: accepts "178.79", "178,79", "1.2e3"; anything else -> NaN */
  function parseNum(v) {
    if (typeof v === 'number') return isFinite(v) ? v : NaN;
    if (typeof v !== 'string') return NaN;
    let s = v.trim();
    if (/^[-+]?\d+,\d+$/.test(s)) s = s.replace(',', '.');
    return /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(s) ? parseFloat(s) : NaN;
  }

  // ---- OLE compound files (.xls, embedded objects): every stream with its path
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
    const dirs = read(dirStart, chain(dirStart, fat).length * ssz);
    const entries = [];
    for (let off = 0; off + 128 <= dirs.length; off += 128) {
      const nlen = u16(dirs, off + 0x40);
      let name = '';
      for (let i = 0; i < Math.max(nlen - 2, 0); i += 2) name += String.fromCharCode(u16(dirs, off + i));
      entries.push({ name, type: dirs[off + 0x42], left: u32(dirs, off + 0x44), right: u32(dirs, off + 0x48),
        child: u32(dirs, off + 0x4c), start: u32(dirs, off + 0x74), size: u32(dirs, off + 0x78) });
    }
    const root = entries[0];
    const ministream = read(root.start, root.size);
    let mfat = [];
    if (nMfat) for (const i of chain(mfatStart, fat).slice(0, nMfat)) mfat = mfat.concat(words(sector(i)));
    const data = (e) => (e.size < cutoff
      ? concat(chain(e.start, mfat).map((i) => ministream.subarray(i * msz, (i + 1) * msz)), e.size)
      : read(e.start, e.size));
    const out = [], seen = new Set();
    const visit = (id, prefix) => {
      if (id === 0xffffffff || id >= entries.length || seen.has(id)) return;
      seen.add(id);
      const e = entries[id];
      visit(e.left, prefix);
      const path = prefix ? `${prefix}/${e.name}` : e.name;
      if (e.type === 2) out.push({ path, name: e.name, data: data(e) });
      else if (e.type === 1) visit(e.child, path);
      visit(e.right, prefix);
    };
    visit(root.child, '');
    return out;
  }

  // ---- BIFF records (legacy .xls workbooks and MS Graph datasheets)
  function rkValue(rk) {
    let v;
    if (rk & 2) v = (rk | 0) >> 2;
    else {
      const dv = new DataView(new ArrayBuffer(8));
      dv.setUint32(4, (rk & 0xfffffffc) >>> 0, true);
      v = dv.getFloat64(0, true);
    }
    return rk & 1 ? v / 100 : v;
  }

  function readSST(segs) {
    let si = 0, p = 8;
    const total = u32(segs[0], 4), out = [];
    const byte = () => {
      while (si < segs.length && p >= segs[si].length) { si++; p = 0; }
      if (si >= segs.length) throw new Error('end');
      return segs[si][p++];
    };
    const u16c = () => byte() | (byte() << 8);
    try {
      for (let s = 0; s < total; s++) {
        const cch = u16c(), flags = byte();
        let wide = flags & 1;
        const runs = flags & 8 ? u16c() : 0, ext = flags & 4 ? u16c() + u16c() * 65536 : 0;
        let str = '';
        for (let i = 0; i < cch; i++) {
          if (si < segs.length && p >= segs[si].length) { si++; p = 0; if (si < segs.length) wide = segs[si][p++] & 1; }
          str += String.fromCharCode(wide ? u16c() : byte());
        }
        for (let i = 0; i < 4 * runs + ext; i++) byte();
        out.push(str);
      }
    } catch (e) { /* truncated table: keep what was read */ }
    return out;
  }

  /** BIFF stream -> one cell map per substream (worksheet / datasheet) */
  function biffSheets(w) {
    const dv = new DataView(w.buffer, w.byteOffset, w.byteLength);
    const recs = [];
    for (let p = 0; p + 4 <= w.length;) { const n = u16(w, p + 2); recs.push([u16(w, p), p + 4, n]); p += 4 + n; }
    const sheets = [], sst = [];
    let cur = null;
    for (let k = 0; k < recs.length; k++) {
      const [type, d, n] = recs[k];
      if (type === 0x0809 || type === 0x0409 || type === 0x0209 || type === 0x0009) { cur = new Map(); sheets.push(cur); continue; }
      if (type === 0x00fc) {
        const segs = [w.subarray(d, d + n)];
        while (k + 1 < recs.length && recs[k + 1][0] === 0x003c) { k++; segs.push(w.subarray(recs[k][1], recs[k][1] + recs[k][2])); }
        sst.push(...readSST(segs));
        continue;
      }
      if (!cur) continue;
      const set = (r, c, v) => cur.set(`${r},${c}`, v);
      if ((type === 0x0203 || type === 0x0003) && n >= 12) set(u16(w, d), u16(w, d + 2), dv.getFloat64(d + n - 8, true));
      else if (type === 0x0002 && n >= 9) set(u16(w, d), u16(w, d + 2), u16(w, d + n - 2));
      else if (type === 0x027e && n >= 10) set(u16(w, d), u16(w, d + 2), rkValue(u32(w, d + 6)));
      else if (type === 0x00bd && n >= 6) {
        const r = u16(w, d), c0 = u16(w, d + 2), cnt = Math.floor((n - 6) / 6);
        for (let i = 0; i < cnt; i++) set(r, c0 + i, rkValue(u32(w, d + 4 + 6 * i + 2)));
      } else if (type === 0x0006 && n >= 14) {
        if (!(w[d + 12] === 0xff && w[d + 13] === 0xff)) set(u16(w, d), u16(w, d + 2), dv.getFloat64(d + 6, true));
      } else if (type === 0x00fd && n >= 10) {
        const s = sst[u32(w, d + 6)];
        if (s != null) set(u16(w, d), u16(w, d + 2), s);
      } else if (type === 0x0204 && n >= 9) {
        const cch = u16(w, d + 6), wide = w[d + 8] & 1;
        let s = '';
        for (let i = 0; i < cch; i++) s += String.fromCharCode(wide ? u16(w, d + 9 + 2 * i) : w[d + 9 + i]);
        set(u16(w, d), u16(w, d + 2), s);
      } else if (type === 0x0004 && n >= 9) {
        const cch = u16(w, d + 7);
        let s = '';
        for (let i = 0; i < cch && d + 9 + i < d + n; i++) s += String.fromCharCode(w[d + 9 + i]);
        set(u16(w, d), u16(w, d + 2), s);
      }
    }
    return sheets.filter((s) => s.size);
  }

  // ---- worksheets in .xlsx
  const xmlText = (s) => s.replace(/&(#x?[0-9a-fA-F]+|amp|lt|gt|quot|apos);/g, (_, e) =>
    e[0] === '#' ? String.fromCodePoint(e[1] === 'x' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10))
      : { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }[e]);
  const tTexts = (xml) => [...xml.replace(/<(?:\w+:)?rPh\b[\s\S]*?<\/(?:\w+:)?rPh>/g, '')
    .matchAll(/<(?:\w+:)?t(?:\s[^>]*)?>([\s\S]*?)<\/(?:\w+:)?t>/g)].map((t) => xmlText(t[1])).join('');

  function sheetCells(sheetXml, shared) {
    const cells = new Map();
    for (const m of sheetXml.matchAll(/<(?:\w+:)?c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/(?:\w+:)?c>)/g)) {
      const attrs = m[1], body = m[2] || '';
      const ref = /\br="([A-Z]+)(\d+)"/.exec(attrs);
      if (!ref) continue;
      const t = (/\bt="([^"]+)"/.exec(attrs) || [])[1];
      const v = /<(?:\w+:)?v>([\s\S]*?)<\/(?:\w+:)?v>/.exec(body);
      let val;
      if (!v) {
        const is = /<(?:\w+:)?is>([\s\S]*?)<\/(?:\w+:)?is>/.exec(body);
        if (!is) continue;
        val = tTexts(is[1]);
      } else if (t === 's') val = shared[parseInt(v[1], 10)];
      else if (t === 'str' || t === 'inlineStr' || t === 'e') val = xmlText(v[1]);
      else if (t === 'b') val = v[1] === '1';
      else val = parseFloat(v[1]);
      if (val === undefined) continue;
      let col = 0;
      for (const ch of ref[1]) col = col * 26 + ch.charCodeAt(0) - 64;
      cells.set(`${+ref[2]},${col}`, val);
    }
    return cells;
  }

  const colName = (c) => { let s = ''; for (c += 1; c > 0; c = Math.floor((c - 1) / 26)) s = String.fromCharCode(65 + ((c - 1) % 26)) + s; return s; };

  /** Long numeric runs along rows and columns of a cell map. */
  function gridSeries(cells, group, where) {
    const lines = { row: new Map(), col: new Map() };
    for (const [k, v] of cells) {
      const [r, c] = k.split(',').map(Number);
      const num = parseNum(v);
      for (const [o, key, pos] of [['row', r, c], ['col', c, r]]) {
        if (!lines[o].has(key)) lines[o].set(key, []);
        lines[o].get(key).push([pos, num, v]);
      }
    }
    const out = [];
    for (const o of ['row', 'col']) {
      for (const [key, items] of lines[o]) {
        items.sort((a, b) => a[0] - b[0]);
        let seg = [];
        const flush = () => {
          if (seg.length >= MIN_SERIES) {
            const p0 = seg[0][0], p1 = seg[seg.length - 1][0];
            const values = new Float64Array(p1 - p0 + 1).fill(NaN);
            for (const [pos, num] of seg) values[pos - p0] = num;
            const lab = items.filter((it) => it[0] < p0 && it[0] >= p0 - 3 && typeof it[2] === 'string' && it[2].trim() && !isFinite(it[1]))
              .sort((a, b) => b[0] - a[0])[0];
            const at = o === 'row' ? `row ${key}` : `column ${colName(key - 1)}`;
            out.push({ name: lab ? String(lab[2]).trim() : '', values, start: p0, group: `${group}|${o}`, where: `${where}, ${at}` });
          }
          seg = [];
        };
        for (const it of items) {
          if (!isFinite(it[1])) continue;
          if (seg.length && it[0] - seg[seg.length - 1][0] > 6) flush();
          seg.push(it);
        }
        flush();
      }
    }
    return out;
  }

  /** Cached values of native Excel charts (xl/charts/chartN.xml) */
  function chartSeries(xml, group, where) {
    const out = [];
    const tag = (name, body) => new RegExp(`<(?:\\w+:)?${name}>([\\s\\S]*?)</(?:\\w+:)?${name}>`).exec(body);
    for (const ser of xml.matchAll(/<(?:\w+:)?ser>([\s\S]*?)<\/(?:\w+:)?ser>/g)) {
      const body = ser[1], tx = tag('tx', body);
      const name = tx ? ([...tx[1].matchAll(/<(?:\w+:)?v>([\s\S]*?)<\/(?:\w+:)?v>/g)].map((m) => xmlText(m[1])).join(' ') || tTexts(tx[1])).trim() : '';
      for (const [t, role] of [['val', 'y'], ['yVal', 'y'], ['cat', 'x'], ['xVal', 'x']]) {
        const m = tag(t, body);
        if (!m) continue;
        const cache = tag('numCache', m[1]) || tag('numLit', m[1]);
        if (!cache) continue;
        const cnt = +((/<(?:\w+:)?ptCount val="(\d+)"/.exec(cache[1]) || [])[1] || 0);
        const pts = [...cache[1].matchAll(/<(?:\w+:)?pt idx="(\d+)"[^>]*>\s*<(?:\w+:)?v>([^<]*)<\/(?:\w+:)?v>/g)];
        if (pts.length < MIN_SERIES) continue;
        const values = new Float64Array(Math.max(cnt, ...pts.map((p) => +p[1] + 1))).fill(NaN);
        for (const p of pts) values[+p[1]] = parseNum(p[2]);
        out.push({ name: role === 'y' ? name : `${name} (axis)`.trim(), values, start: 0, group, where });
      }
    }
    return out;
  }

  // ---- test details found by label wording
  function splitLabel(s) {
    const t = String(s).replace(/（/g, '(').replace(/）/g, ')').replace(/：/g, ':');
    const units = [...t.matchAll(/\(([^)]*)\)/g)].map((m) => m[1].trim().toLowerCase());
    return {
      base: t.replace(/\([^)]*\)/g, ' ').toLowerCase().replace(/tyre/g, 'tire').replace(/[.:#*_]/g, ' ').replace(/\s+/g, ' ').trim(),
      unit: units.length ? units[units.length - 1] : '',
      colon: /:\s*$/.test(t),
    };
  }
  const RULES = [ // most specific first
    ['unload_distance_mm', /^unload(ed)? ?(distance|dist)$/, 'len'],
    ['unload_position_mm', /^unload(ed)? ?(position|pos)$/, 'len'],
    ['load_distance_mm', /^load(ed)? ?(distance|dist)$/, 'len'],
    ['load_position_mm', /^load(ed)? ?(position|pos)$/, 'len'],
    ['radial_deflection_mm', /\b(radial|vertical) ?deflection$/, 'len'],
    ['horizontal_deflection_mm', /\b(horizontal|lateral|sidewall) ?(deflection|bulge)$/, 'len'],
    ['request', /^req(uest)?( no| number| id)?$/, 'text'],
    ['test_date', /^(test )?date( of test)?$/, 'date'],
    ['serial', /^serial( no| number)?$|^dot( no| number| code)?$/, 'text'],
    ['feature', /^(test )?(feature|purpose|objective)$/, 'text'],
    ['engineer', /engineer|^tested by$|^operator$/, 'text'],
    ['product_id', /^(product|spec) ?(id|code|no)$/, 'any'],
    ['size', /^(tire )?size$/, 'text'],
    ['brand', /^(brand|make|manufacturer)( name)?$/, 'text'],
    ['section_width_mm', /^(sw|section width)$/, 'len'],
    ['od_mm', /^(od|outer diameter|overall diameter)$/, 'len'],
    ['tire_weight_kg', /^(tire )?weight$/, 'mass'],
    ['pressure_psi', /^(test |inflation )?pressure$/, 'pressure'],
    ['load_daN', /^(test |vertical |applied |radial )?load$/, 'force'],
    ['rim', /^rim( type| size)?$/, 'text'],
    ['room_temp_C', /^(room |ambient )?temp(erature)?$/, 'temp'],
  ];
  const FIELD_KEYS = RULES.map((r) => r[0]);
  const CONV = {
    len: { '': 1, mm: 1, cm: 10, m: 1000, in: 25.4, inch: 25.4 },
    force: { '': 1, dan: 1, kg: 0.980665, kgf: 0.980665, n: 0.1, kn: 100, lb: 0.4448222, lbs: 0.4448222, lbf: 0.4448222 },
    pressure: { '': 1, psi: 1, bar: 14.50377, kpa: 0.1450377, mpa: 145.0377, 'kg/cm2': 14.22334, 'kgf/cm2': 14.22334, ksc: 14.22334 },
    mass: { '': 1, kg: 1, g: 0.001, lb: 0.4535924, lbs: 0.4535924 },
  };
  const matchRule = (base) => RULES.find((r) => r[1].test(base));
  const excelDate = (v) => {
    const d = new Date(Date.UTC(1899, 11, 30) + Math.round(v) * 86400000);
    return `${String(d.getUTCDate()).padStart(2, '0')}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${d.getUTCFullYear()}`;
  };

  function convertValue(kind, raw, labelUnit, key, notes) {
    if (kind === 'text' || kind === 'any') {
      if (typeof raw === 'string') return raw.trim() || null;
      return typeof raw === 'number' ? raw : null;
    }
    if (kind === 'date') {
      if (typeof raw === 'number' && raw > 20000 && raw < 80000) return excelDate(raw);
      return typeof raw === 'string' ? raw.trim() || null : raw ?? null;
    }
    let num = typeof raw === 'number' ? raw : NaN, unit = labelUnit;
    if (typeof raw === 'string') {
      const m = /^\s*([-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)\s*([^\d\s].*)?$/.exec(raw);
      if (!m) return null;
      num = parseFloat(m[1].replace(',', '.'));
      if (m[2] && !unit) unit = m[2].trim().toLowerCase();
    }
    if (!isFinite(num)) return null;
    unit = unit.replace(/\s+/g, '').replace('²', '2').replace(/^°/, '°');
    if (kind === 'temp') return /f$/.test(unit) ? (num - 32) / 1.8 : num;
    const table = CONV[kind];
    if (!(unit in table)) { notes.push(`unit "${unit}" for ${key} not recognised; used as is`); return num; }
    return num * table[unit];
  }

  function metaFromCells(cells, into, notes) {
    const get = (r, c) => cells.get(`${r},${c}`);
    const labelish = (v) => typeof v === 'string' && (!!matchRule(splitLabel(v).base) || /[:：]\s*$/.test(v));
    const keys = [...cells.keys()].map((k) => k.split(',').map(Number)).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    for (const [r, c] of keys) {
      const v = get(r, c);
      if (typeof v !== 'string') continue;
      const lab = splitLabel(v), rule = matchRule(lab.base);
      if (!rule || into[rule[0]] != null) continue;
      const [key, , kind] = rule;
      const right = () => { for (let cc = c + 1; cc <= c + 4; cc++) { const x = get(r, cc); if (x === undefined || x === '') continue; return labelish(x) ? undefined : x; } return undefined; };
      const below = () => { for (let rr = r + 1; rr <= r + 3; rr++) { const x = get(rr, c); if (x === undefined || x === '') continue; return labelish(x) ? undefined : x; } return undefined; };
      for (const look of lab.colon ? [right, below] : [below, right]) {
        const raw = look();
        if (raw === undefined) continue;
        const val = convertValue(kind, raw, lab.unit, key, notes);
        if (val != null) { into[key] = val; break; }
      }
    }
  }

  // ---- choosing the scan
  /** Stage channel: runs one way, turns once, runs back (each half >= 90% one direction). */
  function stageLike(values) {
    const f = Array.from(values).filter(isFinite);
    const n = f.length;
    if (n < 20 || maxOf(f) - minOf(f) <= 20) return false;
    const i = f[0] > f[n >> 1] ? argmin(f) : argmax(f);
    if (i < 0.2 * n || i > 0.8 * n) return false;
    const share = (a, b, sign) => {
      let good = 0, tot = 0;
      for (let k = a + 1; k <= b; k++) { const d = f[k] - f[k - 1]; if (d === 0) continue; tot++; if (Math.sign(d) === sign) good++; }
      return tot ? good / tot : 0;
    };
    const s1 = Math.sign(f[i] - f[0]);
    return share(0, i, s1) >= 0.9 && share(i, n - 1, -s1) >= 0.9;
  }
  const STAGE_NAME = /(^|[^a-z])z([^a-z]|$)|position|stage|height|travel/i;
  const DIST_NAME = /laser|distance|reading|displacement|deflection|(^|[^a-z])x([^a-z]|$)/i;

  function choosePair(cands) {
    let best = null;
    for (const zc of cands) {
      if (!stageLike(zc.values)) continue;
      for (const xc of cands) {
        if (xc === zc || xc.group !== zc.group) continue;
        const a = Math.max(zc.start, xc.start), b = Math.min(zc.start + zc.values.length, xc.start + xc.values.length);
        if (b - a < MIN_SERIES) continue;
        const zs = zc.values.slice(a - zc.start, b - zc.start), xs = xc.values.slice(a - xc.start, b - xc.start);
        const keep = [];
        for (let i = 0; i < zs.length; i++) if (isFinite(zs[i])) keep.push(i);
        const fx = keep.filter((i) => isFinite(xs[i])).length / keep.length;
        if (keep.length < MIN_SERIES || fx < 0.6) continue;
        const score = keep.length + fx * 1000 + (STAGE_NAME.test(zc.name) ? 1e5 : 0) + (DIST_NAME.test(xc.name) ? 1e5 : 0)
          - (stageLike(xs) ? 1e6 : 0);
        if (!best || score > best.score) best = { score, x: pick(xs, keep), z: pick(zs, keep), xc, zc };
      }
    }
    return best;
  }

  // ---- collectors: {cands, meta, notes, looked}
  async function collectZip(bytes, label, depth = 0) {
    const z = await unzip(bytes);
    const out = { cands: [], meta: {}, notes: [], looked: [] };
    const dec = new TextDecoder();
    const sharedXml = z.names.includes('xl/sharedStrings.xml') ? await z.text('xl/sharedStrings.xml') : '';
    const shared = [...sharedXml.matchAll(/<(?:\w+:)?si>([\s\S]*?)<\/(?:\w+:)?si>/g)].map((m) => tTexts(m[1]));
    // sheet names from workbook.xml + rels
    const names = {};
    try {
      const wb = await z.text('xl/workbook.xml'), rels = await z.text('xl/_rels/workbook.xml.rels');
      const target = {};
      for (const m of rels.matchAll(/<Relationship\b[^>]*>/g)) {
        const id = (/\bId="([^"]+)"/.exec(m[0]) || [])[1], tg = (/\bTarget="([^"]+)"/.exec(m[0]) || [])[1];
        if (id && tg) target[id] = `xl/${tg.replace(/^\/?xl\//, '').replace(/^\//, '')}`;
      }
      for (const m of wb.matchAll(/<(?:\w+:)?sheet\b[^>]*>/g)) {
        const nm = (/\bname="([^"]*)"/.exec(m[0]) || [])[1], id = (/\br:id="([^"]+)"/.exec(m[0]) || /\bid="([^"]+)"/.exec(m[0]) || [])[1];
        if (nm && target[id]) names[target[id]] = xmlText(nm);
      }
    } catch (e) { /* names are only for messages */ }
    const nat = (a, b) => a.localeCompare(b, undefined, { numeric: true });
    for (const n of z.names.filter((x) => /^xl\/worksheets\/[^/]+\.xml$/.test(x)).sort(nat)) {
      const cells = sheetCells(await z.text(n), shared);
      const sname = names[n] || n.split('/').pop().replace('.xml', '');
      out.looked.push(`sheet "${sname}"`);
      metaFromCells(cells, out.meta, out.notes);
      out.cands.push(...gridSeries(cells, `${label}:${n}`, `sheet "${sname}"`));
    }
    for (const n of z.names.filter((x) => /^xl\/charts\/[^/]+\.xml$/.test(x)).sort(nat)) {
      out.looked.push(`chart ${n.split('/').pop()}`);
      out.cands.push(...chartSeries(await z.text(n), `${label}:${n}`, `Excel chart ${n.split('/').pop()}`));
    }
    for (const n of z.names.filter((x) => x.startsWith('xl/embeddings/')).sort(nat)) {
      const data = await z.get(n);
      if (!data || data.length < 8) continue;
      out.looked.push(`embedded ${n.split('/').pop()}`);
      try {
        if (data[0] === 0x50 && data[1] === 0x4b && depth < 2) {
          const sub = await collectZip(data, `${label}>${n}`, depth + 1);
          out.cands.push(...sub.cands);
        } else if (data[0] === 0xd0 && data[1] === 0xcf) {
          out.cands.push(...(await collectCfb(data, `${label}>${n}`, `embedded object ${n.split('/').pop()}`, depth + 1)).cands);
        }
      } catch (e) { out.notes.push(`could not open ${n}: ${e.message}`); }
    }
    return out;
  }

  async function collectCfb(bytes, label, where, depth = 0) {
    const out = { cands: [], meta: {}, notes: [], looked: [] };
    const streams = cfbStreams(bytes);
    for (const s of streams) {
      if (s.name === 'Workbook' || s.name === 'Book') {
        const top = !s.path.includes('/');
        const kind = top && depth === 0 ? 'sheet' : 'chart datasheet';
        biffSheets(s.data).forEach((cells, i) => {
          const at = top && depth === 0 ? `${where} sheet ${i + 1}` : `${where}${top ? '' : ` (${s.path})`}, ${kind}`;
          out.looked.push(at);
          if (top && depth === 0) metaFromCells(cells, out.meta, out.notes);
          out.cands.push(...gridSeries(cells, `${label}:${s.path}:${i}`, at));
        });
      } else if (s.name === 'Package' && s.data[0] === 0x50 && depth < 3) {
        out.cands.push(...(await collectZip(s.data, `${label}>pkg`, depth + 1)).cands);
      }
    }
    return out;
  }

  function collectText(text) {
    const lines = text.split(/\r?\n/);
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    const sample = lines.slice(0, 200).join('\n');
    let split;
    if (sample.includes('\t')) split = (l) => l.split('\t');
    else if (sample.includes(';')) split = (l) => l.split(';');
    else if (sample.includes(',') && !(!sample.includes('.') && lines.every((l) => !l.trim() || /^\s*[-+]?\d+,\d+\s*$/.test(l)))) split = (l) => l.split(',');
    else split = (l) => l.trim().split(/\s+/);
    const cells = new Map();
    lines.forEach((l, r) => split(l).forEach((v, c) => { if (v.trim() !== '') cells.set(`${r + 1},${c + 1}`, v.trim()); }));
    const out = { cands: gridSeries(cells, 'text', 'text'), meta: {}, notes: [], looked: ['text table'] };
    metaFromCells(cells, out.meta, out.notes);
    return out;
  }

  function finish(name, col) {
    const best = choosePair(col.cands);
    if (!best) {
      const seen = col.cands.map((c) => `${c.where}${c.name ? ` ("${c.name}")` : ''}: ${Array.from(c.values).filter(isFinite).length} numbers`);
      throw new Error(`could not find the scan (a laser-distance series with a Z series that goes out and back). `
        + `Looked in: ${col.looked.join(', ') || 'nothing readable'}. `
        + `${seen.length ? `Number series found: ${seen.slice(0, 6).join('; ')}.` : 'No long number series found.'} Use “Paste columns” instead.`);
    }
    const meta = { file: name };
    for (const k of FIELD_KEYS) meta[k] = col.meta[k] ?? null;
    const nm = (c) => (c.name ? `“${c.name}”` : c.where.split(', ').pop());
    meta.source = `${best.xc.where.split(', ')[0]}: ${nm(best.xc)} and ${nm(best.zc)}, ${best.z.length} points`;
    meta.fields_found = FIELD_KEYS.filter((k) => meta[k] != null).length;
    meta.notes = col.notes;
    return { x: best.x, z: best.z, meta };
  }

  async function readFile(name, buf) {
    const b = new Uint8Array(buf);
    let col;
    if (b[0] === 0x50 && b[1] === 0x4b) col = await collectZip(b, 'file');
    else if (b[0] === 0xd0 && b[1] === 0xcf) col = await collectCfb(b, 'file', 'workbook');
    else {
      const enc = b[0] === 0xff && b[1] === 0xfe ? 'utf-16le' : b[0] === 0xfe && b[1] === 0xff ? 'utf-16be' : 'utf-8';
      col = collectText(new TextDecoder(enc).decode(b));
    }
    return finish(name, col);
  }

  /** Columns pasted by hand: a list of numbers per box (blank / text lines inside = missing). */
  function parseColumnText(text) {
    const lines = String(text).split(/\r?\n/);
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    let first = 0;
    while (first < lines.length && !isFinite(parseNum(lines[first].trim().split(/\t/)[0]))) first++;
    const body = lines.slice(first);
    const tabbed = body.some((l) => l.includes('\t'));
    if (tabbed) { // two columns (or two rows) pasted into one box
      const grid = body.map((l) => l.split('\t').map((c) => parseNum(c)));
      if (grid.length <= 3 && grid[0].length > MIN_SERIES) {
        return { columns: grid.map((row) => Float64Array.from(row)), header: lines.slice(0, first).join(' ').trim() };
      }
      const ncol = Math.max(...grid.map((r) => r.length));
      return { columns: Array.from({ length: ncol }, (_, c) => Float64Array.from(grid, (r) => (c < r.length ? r[c] : NaN))),
        header: lines.slice(0, first).join(' ').trim() };
    }
    return { columns: [Float64Array.from(body, (l) => parseNum(l))], header: lines.slice(0, first).join(' ').trim() };
  }

  /** Build a scan from two pasted columns. Swaps them if the "Z" box is clearly the laser one. */
  function fromColumns(xVals, zVals, meta = {}) {
    if (xVals.length !== zVals.length) {
      throw new Error(`the two columns have different lengths (${xVals.length} and ${zVals.length}); they must line up row by row`);
    }
    let x = xVals, z = zVals, swapped = false;
    if (!stageLike(z) && stageLike(x)) { [x, z] = [z, x]; swapped = true; }
    if (!stageLike(z)) throw new Error('the Z column does not look like a scan that goes out and comes back');
    const keep = [];
    for (let i = 0; i < z.length; i++) if (isFinite(z[i])) keep.push(i);
    const m = { file: meta.file || 'pasted', source: `pasted columns, ${keep.length} points${swapped ? ' (columns were swapped)' : ''}`, notes: [] };
    for (const k of FIELD_KEYS) m[k] = meta[k] ?? null;
    m.fields_found = FIELD_KEYS.filter((k) => m[k] != null).length;
    return { x: pick(x, keep), z: pick(z, keep), meta: m, swapped };
  }

  // ---- dropouts: values cut off from the rest by a big gap at either end
  function percentile(sorted, p) {
    const i = p * (sorted.length - 1), lo = Math.floor(i), hi = Math.ceil(i);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (i - lo);
  }
  function dropoutMask(x) {
    const fin = Array.from(x).filter(isFinite).sort((a, b) => a - b);
    const n = fin.length;
    let lo = -Infinity, hi = Infinity;
    if (n >= 20) {
      const g = Math.max(10, 0.25 * (percentile(fin, 0.95) - percentile(fin, 0.05)));
      for (let i = Math.max(1, Math.ceil(0.95 * (n - 1))); i < n; i++) if (fin[i] - fin[i - 1] > g) { hi = fin[i - 1]; break; }
      for (let i = Math.floor(0.05 * (n - 1)); i > 0; i--) if (fin[i] - fin[i - 1] > g) { lo = fin[i]; break; }
    }
    return Array.from(x, (v) => isFinite(v) && v >= lo && v <= hi);
  }

  // ------------------------------------------------------------------- analysis
  function splitPasses(z) {
    const n = z.length;
    const i = z[0] > z[n >> 1] ? argmin(z) : argmax(z);
    return [[0, i], [i, n]];
  }

  function cleanPass(name, x, z, valid) {
    const n = x.length, keep = [];
    let nDrop = 0, nHold = 0;
    for (let i = 0; i < n; i++) {
      const ok = valid[i];
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
    const shifts = arange(dEdge - search, dEdge + search + 1e-9, 0.25);
    const rs = Array.from(shifts, (d) => (zg[0] - d >= zLoadMin ? corr(a, interp(zg.map((v) => v - d), load.z, load.x)) : -1));
    // centre of the best-matching 2 mm range, not a single noisy peak (see analysis.py)
    const smooth = rs.map((_, k) => { let t = 0; for (let j = Math.max(0, k - 4); j <= Math.min(rs.length - 1, k + 4); j++) t += rs[j]; return t / 9; });
    const k = argmax(smooth), d = shifts[k];
    const b = interp(zg.map((v) => v - d), load.z, load.x);
    const r = rs[k], off = mean(b) - mean(a);
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
    const valid = dropoutMask(x);
    const inf = cleanPass('inflated', x.slice(s1[0], s1[1]), z.slice(s1[0], s1[1]), valid.slice(s1[0], s1[1]));
    const load = cleanPass('loaded', x.slice(s2[0], s2[1]), z.slice(s2[0], s2[1]), valid.slice(s2[0], s2[1]));
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
      test: Object.fromEntries(Object.entries(meta).filter(([k]) => !['series_titles', 'notes'].includes(k))),
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

  return { readFile, analyze, displayCurves, summaryRow, label, turbo, interp, savgol,
    parseColumnText, fromColumns, stageLike, parseNum, dropoutMask, FIELD_KEYS };
})();
if (typeof module !== 'undefined') module.exports = SW;
