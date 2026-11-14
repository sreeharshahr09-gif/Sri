"""Read sidewall scans from lab report workbooks and other files.

Mirror of the reader in web/engine.js (same steps, same choices), so the CLI
and the browser tool read files identically.

Strategy: collect every long run of numbers the file holds -- sheet rows and
columns, embedded chart datasheets (MS Graph OLE objects), native chart caches,
nested workbooks, legacy .xls sheets, text columns -- then pick the
(laser distance, Z position) pair by content: the Z channel runs one way and
comes back. Names only break ties. Test details are found by label wording
(several spellings, units converted to mm / daN / psi / kg / degC).

Standard library only.
"""
from __future__ import annotations

import math
import re
import struct
import zipfile
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path

MIN_SERIES = 50


# ----------------------------------------------------------------- numbers
_NUM = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


def parse_num(v) -> float:
    if isinstance(v, bool):
        return math.nan
    if isinstance(v, (int, float)):
        return float(v) if math.isfinite(v) else math.nan
    if not isinstance(v, str):
        return math.nan
    s = v.strip()
    if re.match(r"^[-+]?\d+,\d+$", s):
        s = s.replace(",", ".", 1)
    return float(s) if _NUM.match(s) else math.nan


def _fin(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ------------------------------------------------------- OLE compound files
def cfb_streams(b: bytes) -> list[tuple[str, str, bytes]]:
    """Every stream of an OLE2 compound file as (path, name, data)."""
    if b[:8] != bytes.fromhex("D0CF11E0A1B11AE1"):
        raise ValueError("not an OLE compound file")
    ssz = 1 << struct.unpack_from("<H", b, 0x1E)[0]
    msz = 1 << struct.unpack_from("<H", b, 0x20)[0]
    n_fat, dir_start = struct.unpack_from("<II", b, 0x2C)
    cutoff, mfat_start, n_mfat, dif_start, n_dif = struct.unpack_from("<IIIII", b, 0x38)

    def sector(i):
        return b[(i + 1) * ssz:(i + 2) * ssz]

    def words(s):
        return list(struct.unpack(f"<{len(s) // 4}I", s[:len(s) // 4 * 4]))

    difat = list(struct.unpack_from("<109I", b, 0x4C))
    nxt = dif_start
    for _ in range(n_dif):
        v = words(sector(nxt))
        difat += v[:-1]
        nxt = v[-1]
    fat: list[int] = []
    for i in difat[:n_fat]:
        fat += words(sector(i))

    def chain(start, table):
        out, i, seen = [], start, set()
        while i < 0xFFFFFFFA and i not in seen and i < len(table) + 1:
            seen.add(i)
            out.append(i)
            i = table[i] if i < len(table) else 0xFFFFFFFE
        return out

    def read(start, size):
        return b"".join(sector(i) for i in chain(start, fat))[:size]

    dirs = read(dir_start, len(chain(dir_start, fat)) * ssz)
    entries = []
    for off in range(0, len(dirs) - 127, 128):
        e = dirs[off:off + 128]
        nlen = struct.unpack_from("<H", e, 0x40)[0]
        name = e[:max(nlen - 2, 0)].decode("utf-16-le", "replace")
        left, right, child = struct.unpack_from("<III", e, 0x44)
        start, size = struct.unpack_from("<II", e, 0x74)
        entries.append(dict(name=name, type=e[0x42], left=left, right=right, child=child, start=start, size=size))
    root = entries[0]
    ministream = read(root["start"], root["size"])
    mfat: list[int] = []
    if n_mfat:
        for i in chain(mfat_start, fat)[:n_mfat]:
            mfat += words(sector(i))

    def data(e):
        if e["size"] < cutoff:
            return b"".join(ministream[i * msz:(i + 1) * msz] for i in chain(e["start"], mfat))[:e["size"]]
        return read(e["start"], e["size"])

    out, seen = [], set()

    def visit(i, prefix):
        if i == 0xFFFFFFFF or i >= len(entries) or i in seen:
            return
        seen.add(i)
        e = entries[i]
        visit(e["left"], prefix)
        path = f"{prefix}/{e['name']}" if prefix else e["name"]
        if e["type"] == 2:
            out.append((path, e["name"], data(e)))
        elif e["type"] == 1:
            visit(e["child"], path)
        visit(e["right"], prefix)

    visit(root["child"], "")
    return out


# ------------------------------------------------------------------ BIFF
def _rk(rk: int) -> float:
    if rk & 2:
        v = float(struct.unpack("<i", struct.pack("<I", rk))[0] >> 2)
    else:
        v = struct.unpack("<d", struct.pack("<II", 0, rk & 0xFFFFFFFC))[0]
    return v / 100 if rk & 1 else v


def _read_sst(segs: list[bytes]) -> list[str]:
    st = {"si": 0, "p": 8}
    total = struct.unpack_from("<I", segs[0], 4)[0]
    out: list[str] = []

    def byte():
        while st["si"] < len(segs) and st["p"] >= len(segs[st["si"]]):
            st["si"] += 1
            st["p"] = 0
        if st["si"] >= len(segs):
            raise EOFError
        v = segs[st["si"]][st["p"]]
        st["p"] += 1
        return v

    def u16c():
        return byte() | (byte() << 8)

    try:
        for _ in range(total):
            cch, flags = u16c(), byte()
            wide = flags & 1
            runs = u16c() if flags & 8 else 0
            ext = (u16c() + u16c() * 65536) if flags & 4 else 0
            chars = []
            for _ in range(cch):
                if st["si"] < len(segs) and st["p"] >= len(segs[st["si"]]):
                    st["si"] += 1
                    st["p"] = 0
                    if st["si"] < len(segs):
                        wide = segs[st["si"]][0] & 1
                        st["p"] = 1
                chars.append(chr(u16c() if wide else byte()))
            for _ in range(4 * runs + ext):
                byte()
            out.append("".join(chars))
    except EOFError:
        pass
    return out


def biff_sheets(w: bytes) -> list[dict]:
    """BIFF stream -> one {(row, col): value} per substream (worksheet / datasheet)."""
    recs, p = [], 0
    while p + 4 <= len(w):
        t, n = struct.unpack_from("<HH", w, p)
        recs.append((t, p + 4, n))
        p += 4 + n
    sheets, sst, cur = [], [], None
    k = 0
    while k < len(recs):
        t, d, n = recs[k]
        k += 1
        if t in (0x0809, 0x0409, 0x0209, 0x0009):
            cur = {}
            sheets.append(cur)
            continue
        if t == 0x00FC:
            segs = [w[d:d + n]]
            while k < len(recs) and recs[k][0] == 0x003C:
                segs.append(w[recs[k][1]:recs[k][1] + recs[k][2]])
                k += 1
            sst += _read_sst(segs)
            continue
        if cur is None:
            continue
        rc = lambda: struct.unpack_from("<HH", w, d)  # noqa: E731
        if t in (0x0203, 0x0003) and n >= 12:
            cur[rc()] = struct.unpack_from("<d", w, d + n - 8)[0]
        elif t == 0x0002 and n >= 9:
            cur[rc()] = float(struct.unpack_from("<H", w, d + n - 2)[0])
        elif t == 0x027E and n >= 10:
            cur[rc()] = _rk(struct.unpack_from("<I", w, d + 6)[0])
        elif t == 0x00BD and n >= 6:
            r, c0 = rc()
            for i in range((n - 6) // 6):
                cur[(r, c0 + i)] = _rk(struct.unpack_from("<I", w, d + 4 + 6 * i + 2)[0])
        elif t == 0x0006 and n >= 14:
            if not (w[d + 12] == 0xFF and w[d + 13] == 0xFF):
                cur[rc()] = struct.unpack_from("<d", w, d + 6)[0]
        elif t == 0x00FD and n >= 10:
            i = struct.unpack_from("<I", w, d + 6)[0]
            if i < len(sst):
                cur[rc()] = sst[i]
        elif t == 0x0204 and n >= 9:
            cch, wide = struct.unpack_from("<H", w, d + 6)[0], w[d + 8] & 1
            raw = w[d + 9:d + 9 + cch * (2 if wide else 1)]
            cur[rc()] = raw.decode("utf-16-le" if wide else "latin-1", "replace")
        elif t == 0x0004 and n >= 9:
            cch = struct.unpack_from("<H", w, d + 7)[0]
            cur[rc()] = w[d + 9:min(d + 9 + cch, d + n)].decode("latin-1", "replace")
    return [s for s in sheets if s]


# ---------------------------------------------------------------- xlsx
_ENT = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}


def _xml_text(s: str) -> str:
    def ent(m):
        e = m.group(1)
        if e[0] == "#":
            return chr(int(e[2:], 16) if e[1] == "x" else int(e[1:]))
        return _ENT[e]
    return re.sub(r"&(#x?[0-9a-fA-F]+|amp|lt|gt|quot|apos);", ent, s)


def _t_texts(xml: str) -> str:
    xml = re.sub(r"<(?:\w+:)?rPh\b[\s\S]*?</(?:\w+:)?rPh>", "", xml)
    return "".join(_xml_text(m) for m in re.findall(r"<(?:\w+:)?t(?:\s[^>]*)?>([\s\S]*?)</(?:\w+:)?t>", xml))


def sheet_cells(sheet_xml: str, shared: list[str]) -> dict:
    cells = {}
    for m in re.finditer(r"<(?:\w+:)?c\b([^>]*?)(?:/>|>([\s\S]*?)</(?:\w+:)?c>)", sheet_xml):
        attrs, body = m.group(1), m.group(2) or ""
        ref = re.search(r'\br="([A-Z]+)(\d+)"', attrs)
        if not ref:
            continue
        t = (re.search(r'\bt="([^"]+)"', attrs) or [None, None])[1]
        v = re.search(r"<(?:\w+:)?v>([\s\S]*?)</(?:\w+:)?v>", body)
        if not v:
            is_ = re.search(r"<(?:\w+:)?is>([\s\S]*?)</(?:\w+:)?is>", body)
            if not is_:
                continue
            val = _t_texts(is_.group(1))
        elif t == "s":
            i = int(v.group(1))
            if i >= len(shared):
                continue
            val = shared[i]
        elif t in ("str", "inlineStr", "e"):
            val = _xml_text(v.group(1))
        elif t == "b":
            val = v.group(1) == "1"
        else:
            try:
                val = float(v.group(1))
            except ValueError:
                continue
            if val.is_integer():
                val = int(val)
        col = 0
        for ch in ref.group(1):
            col = col * 26 + ord(ch) - 64
        cells[(int(ref.group(2)), col)] = val
    return cells


def _col_name(c: int) -> str:
    s, c = "", c + 1
    while c > 0:
        s = chr(65 + (c - 1) % 26) + s
        c = (c - 1) // 26
    return s


def grid_series(cells: dict, group: str, where: str) -> list[dict]:
    """Long numeric runs along rows and columns of a cell map."""
    lines = {"row": {}, "col": {}}
    for (r, c), v in cells.items():
        num = parse_num(v)
        lines["row"].setdefault(r, []).append((c, num, v))
        lines["col"].setdefault(c, []).append((r, num, v))
    out = []
    for o in ("row", "col"):
        for key, items in lines[o].items():
            items = sorted(items, key=lambda it: it[0])
            seg: list = []

            def flush():
                if len(seg) >= MIN_SERIES:
                    p0, p1 = seg[0][0], seg[-1][0]
                    values = [math.nan] * (p1 - p0 + 1)
                    for pos, num, _ in seg:
                        values[pos - p0] = num
                    labs = [it for it in items if p0 - 3 <= it[0] < p0 and isinstance(it[2], str)
                            and it[2].strip() and not math.isfinite(it[1])]
                    labs.sort(key=lambda it: -it[0])
                    at = f"row {key}" if o == "row" else f"column {_col_name(key - 1)}"
                    out.append(dict(name=labs[0][2].strip() if labs else "", values=values, start=p0,
                                    group=f"{group}|{o}", where=f"{where}, {at}"))
                seg.clear()

            for it in items:
                if not math.isfinite(it[1]):
                    continue
                if seg and it[0] - seg[-1][0] > 6:
                    flush()
                seg.append(it)
            flush()
    return out


def chart_series(xml: str, group: str, where: str) -> list[dict]:
    """Cached values of native Excel charts (xl/charts/chartN.xml)."""
    def tag(name, body):
        return re.search(rf"<(?:\w+:)?{name}>([\s\S]*?)</(?:\w+:)?{name}>", body)

    out = []
    for ser in re.finditer(r"<(?:\w+:)?ser>([\s\S]*?)</(?:\w+:)?ser>", xml):
        body = ser.group(1)
        tx = tag("tx", body)
        name = ""
        if tx:
            name = (" ".join(_xml_text(m) for m in re.findall(r"<(?:\w+:)?v>([\s\S]*?)</(?:\w+:)?v>", tx.group(1)))
                    or _t_texts(tx.group(1))).strip()
        for t, role in (("val", "y"), ("yVal", "y"), ("cat", "x"), ("xVal", "x")):
            m = tag(t, body)
            if not m:
                continue
            cache = tag("numCache", m.group(1)) or tag("numLit", m.group(1))
            if not cache:
                continue
            cnt = re.search(r'<(?:\w+:)?ptCount val="(\d+)"', cache.group(1))
            pts = re.findall(r'<(?:\w+:)?pt idx="(\d+)"[^>]*>\s*<(?:\w+:)?v>([^<]*)</(?:\w+:)?v>', cache.group(1))
            if len(pts) < MIN_SERIES:
                continue
            values = [math.nan] * max(int(cnt.group(1)) if cnt else 0, *(int(i) + 1 for i, _ in pts))
            for i, v in pts:
                values[int(i)] = parse_num(v)
            out.append(dict(name=name if role == "y" else f"{name} (axis)".strip(), values=values, start=0,
                            group=group, where=where))
    return out


# ---------------------------------------------------------- test details
def split_label(s: str) -> dict:
    t = str(s).replace("（", "(").replace("）", ")").replace("：", ":")
    units = [u.strip().lower() for u in re.findall(r"\(([^)]*)\)", t)]
    base = re.sub(r"\([^)]*\)", " ", t).lower().replace("tyre", "tire")
    base = re.sub(r"\s+", " ", re.sub(r"[.:#*_]", " ", base)).strip()
    return dict(base=base, unit=units[-1] if units else "", colon=bool(re.search(r":\s*$", t)))


RULES = [  # most specific first
    ("unload_distance_mm", r"^unload(ed)? ?(distance|dist)$", "len"),
    ("unload_position_mm", r"^unload(ed)? ?(position|pos)$", "len"),
    ("load_distance_mm", r"^load(ed)? ?(distance|dist)$", "len"),
    ("load_position_mm", r"^load(ed)? ?(position|pos)$", "len"),
    ("radial_deflection_mm", r"\b(radial|vertical) ?deflection$", "len"),
    ("horizontal_deflection_mm", r"\b(horizontal|lateral|sidewall) ?(deflection|bulge)$", "len"),
    ("request", r"^req(uest)?( no| number| id)?$", "text"),
    ("test_date", r"^(test )?date( of test)?$", "date"),
    ("serial", r"^serial( no| number)?$|^dot( no| number| code)?$", "text"),
    ("feature", r"^(test )?(feature|purpose|objective)$", "text"),
    ("engineer", r"engineer|^tested by$|^operator$", "text"),
    ("product_id", r"^(product|spec) ?(id|code|no)$", "any"),
    ("size", r"^(tire )?size$", "text"),
    ("brand", r"^(brand|make|manufacturer)( name)?$", "text"),
    ("section_width_mm", r"^(sw|section width)$", "len"),
    ("od_mm", r"^(od|outer diameter|overall diameter)$", "len"),
    ("tire_weight_kg", r"^(tire )?weight$", "mass"),
    ("pressure_psi", r"^(test |inflation )?pressure$", "pressure"),
    ("load_daN", r"^(test |vertical |applied |radial )?load$", "force"),
    ("rim", r"^rim( type| size)?$", "text"),
    ("room_temp_C", r"^(room |ambient )?temp(erature)?$", "temp"),
]
FIELD_KEYS = [r[0] for r in RULES]
CONV = {
    "len": {"": 1, "mm": 1, "cm": 10, "m": 1000, "in": 25.4, "inch": 25.4},
    "force": {"": 1, "dan": 1, "kg": 0.980665, "kgf": 0.980665, "n": 0.1, "kn": 100, "lb": 0.4448222,
              "lbs": 0.4448222, "lbf": 0.4448222},
    "pressure": {"": 1, "psi": 1, "bar": 14.50377, "kpa": 0.1450377, "mpa": 145.0377, "kg/cm2": 14.22334,
                 "kgf/cm2": 14.22334, "ksc": 14.22334},
    "mass": {"": 1, "kg": 1, "g": 0.001, "lb": 0.4535924, "lbs": 0.4535924},
}


def _match_rule(base: str):
    return next((r for r in RULES if re.search(r[1], base)), None)


def _excel_date(v: float) -> str:
    d = date(1899, 12, 30) + timedelta(days=round(v))
    return f"{d.day:02d}-{d.month:02d}-{d.year}"


def _convert(kind, raw, label_unit, key, notes):
    if kind in ("text", "any"):
        if isinstance(raw, str):
            return raw.strip() or None
        return raw if _fin(raw) else None
    if kind == "date":
        if _fin(raw) and 20000 < raw < 80000:
            return _excel_date(raw)
        return (raw.strip() or None) if isinstance(raw, str) else raw
    num, unit = (float(raw) if _fin(raw) else math.nan), label_unit
    if isinstance(raw, str):
        m = re.match(r"^\s*([-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)\s*([^\d\s].*)?$", raw)
        if not m:
            return None
        num = float(m.group(1).replace(",", "."))
        if m.group(2) and not unit:
            unit = m.group(2).strip().lower()
    if not math.isfinite(num):
        return None
    unit = re.sub(r"\s+", "", unit).replace("²", "2")
    if kind == "temp":
        return (num - 32) / 1.8 if unit.endswith("f") else num
    table = CONV[kind]
    if unit not in table:
        notes.append(f'unit "{unit}" for {key} not recognised; used as is')
        return num
    return num * table[unit]


def meta_from_cells(cells: dict, into: dict, notes: list) -> None:
    def labelish(v):
        return isinstance(v, str) and (_match_rule(split_label(v)["base"]) is not None
                                       or bool(re.search(r"[:：]\s*$", v)))

    for (r, c) in sorted(cells):
        v = cells[(r, c)]
        if not isinstance(v, str):
            continue
        lab = split_label(v)
        rule = _match_rule(lab["base"])
        if not rule or into.get(rule[0]) is not None:
            continue
        key, _, kind = rule

        def right():
            for cc in range(c + 1, c + 5):
                x = cells.get((r, cc))
                if x is None or x == "":
                    continue
                return None if labelish(x) else x
            return None

        def below():
            for rr in range(r + 1, r + 4):
                x = cells.get((rr, c))
                if x is None or x == "":
                    continue
                return None if labelish(x) else x
            return None

        for look in ((right, below) if lab["colon"] else (below, right)):
            raw = look()
            if raw is None:
                continue
            val = _convert(kind, raw, lab["unit"], key, notes)
            if val is not None:
                into[key] = val
                break


# --------------------------------------------------------- picking the scan
def stage_like(values) -> bool:
    """Stage channel: runs one way, turns once, runs back (each half >= 90% one direction)."""
    f = [v for v in values if _fin(v)]
    n = len(f)
    if n < 20 or max(f) - min(f) <= 20:
        return False
    i = f.index(min(f)) if f[0] > f[n >> 1] else f.index(max(f))
    if i < 0.2 * n or i > 0.8 * n:
        return False

    def share(a, b, sign):
        good = tot = 0
        for k in range(a + 1, b + 1):
            d = f[k] - f[k - 1]
            if d == 0:
                continue
            tot += 1
            good += (1 if d > 0 else -1) == sign
        return good / tot if tot else 0

    s1 = (f[i] > f[0]) - (f[i] < f[0])
    return share(0, i, s1) >= 0.9 and share(i, n - 1, -s1) >= 0.9


STAGE_NAME = re.compile(r"(^|[^a-z])z([^a-z]|$)|position|stage|height|travel", re.I)
DIST_NAME = re.compile(r"laser|distance|reading|displacement|deflection|(^|[^a-z])x([^a-z]|$)", re.I)


def choose_pair(cands: list[dict]):
    best = None
    for zc in cands:
        if not stage_like(zc["values"]):
            continue
        for xc in cands:
            if xc is zc or xc["group"] != zc["group"]:
                continue
            a = max(zc["start"], xc["start"])
            b = min(zc["start"] + len(zc["values"]), xc["start"] + len(xc["values"]))
            if b - a < MIN_SERIES:
                continue
            zs = zc["values"][a - zc["start"]:b - zc["start"]]
            xs = xc["values"][a - xc["start"]:b - xc["start"]]
            keep = [i for i, v in enumerate(zs) if math.isfinite(v)]
            if len(keep) < MIN_SERIES:
                continue
            fx = sum(1 for i in keep if math.isfinite(xs[i])) / len(keep)
            if fx < 0.6:
                continue
            score = (len(keep) + fx * 1000 + (1e5 if STAGE_NAME.search(zc["name"]) else 0)
                     + (1e5 if DIST_NAME.search(xc["name"]) else 0) - (1e6 if stage_like(xs) else 0))
            if best is None or score > best["score"]:
                best = dict(score=score, x=[xs[i] for i in keep], z=[zs[i] for i in keep], xc=xc, zc=zc)
    return best


# ------------------------------------------------------------- collectors
def _natkey(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def collect_zip(data: bytes, label: str, depth: int = 0) -> dict:
    out = dict(cands=[], meta={}, notes=[], looked=[])
    with zipfile.ZipFile(BytesIO(data)) as z:
        names = z.namelist()

        def text(n):
            return z.read(n).decode("utf-8", "replace") if n in names else ""

        shared = [_t_texts(m) for m in re.findall(r"<(?:\w+:)?si>([\s\S]*?)</(?:\w+:)?si>", text("xl/sharedStrings.xml"))]
        sheet_names = {}
        try:
            target = {}
            for m in re.finditer(r"<Relationship\b[^>]*>", text("xl/_rels/workbook.xml.rels")):
                rid = re.search(r'\bId="([^"]+)"', m.group(0))
                tg = re.search(r'\bTarget="([^"]+)"', m.group(0))
                if rid and tg:
                    target[rid.group(1)] = "xl/" + re.sub(r"^/", "", re.sub(r"^/?xl/", "", tg.group(1)))
            for m in re.finditer(r"<(?:\w+:)?sheet\b[^>]*>", text("xl/workbook.xml")):
                nm = re.search(r'\bname="([^"]*)"', m.group(0))
                rid = re.search(r'\br:id="([^"]+)"', m.group(0)) or re.search(r'\bid="([^"]+)"', m.group(0))
                if nm and rid and rid.group(1) in target:
                    sheet_names[target[rid.group(1)]] = _xml_text(nm.group(1))
        except Exception:  # names are only for messages
            pass
        for n in sorted((x for x in names if re.match(r"^xl/worksheets/[^/]+\.xml$", x)), key=_natkey):
            cells = sheet_cells(text(n), shared)
            sname = sheet_names.get(n, n.split("/")[-1].replace(".xml", ""))
            out["looked"].append(f'sheet "{sname}"')
            meta_from_cells(cells, out["meta"], out["notes"])
            out["cands"] += grid_series(cells, f"{label}:{n}", f'sheet "{sname}"')
        for n in sorted((x for x in names if re.match(r"^xl/charts/[^/]+\.xml$", x)), key=_natkey):
            out["looked"].append(f"chart {n.split('/')[-1]}")
            out["cands"] += chart_series(text(n), f"{label}:{n}", f"Excel chart {n.split('/')[-1]}")
        for n in sorted((x for x in names if x.startswith("xl/embeddings/")), key=_natkey):
            d = z.read(n)
            if len(d) < 8:
                continue
            out["looked"].append(f"embedded {n.split('/')[-1]}")
            try:
                if d[:2] == b"PK" and depth < 2:
                    out["cands"] += collect_zip(d, f"{label}>{n}", depth + 1)["cands"]
                elif d[:2] == b"\xd0\xcf":
                    out["cands"] += collect_cfb(d, f"{label}>{n}", f"embedded object {n.split('/')[-1]}", depth + 1)["cands"]
            except Exception as e:  # keep going with the rest of the file
                out["notes"].append(f"could not open {n}: {e}")
    return out


def collect_cfb(data: bytes, label: str, where: str, depth: int = 0) -> dict:
    out = dict(cands=[], meta={}, notes=[], looked=[])
    for path, name, d in cfb_streams(data):
        if name in ("Workbook", "Book"):
            top = "/" not in path
            kind = "sheet" if top and depth == 0 else "chart datasheet"
            for i, cells in enumerate(biff_sheets(d)):
                at = f"{where} sheet {i + 1}" if top and depth == 0 else f"{where}{'' if top else f' ({path})'}, {kind}"
                out["looked"].append(at)
                if top and depth == 0:
                    meta_from_cells(cells, out["meta"], out["notes"])
                out["cands"] += grid_series(cells, f"{label}:{path}:{i}", at)
        elif name == "Package" and d[:2] == b"PK" and depth < 3:
            out["cands"] += collect_zip(d, f"{label}>pkg", depth + 1)["cands"]
    return out


def collect_text(text: str) -> dict:
    lines = re.split(r"\r?\n", text)
    while lines and not lines[-1].strip():
        lines.pop()
    sample = "\n".join(lines[:200])
    if "\t" in sample:
        split = lambda l: l.split("\t")  # noqa: E731
    elif ";" in sample:
        split = lambda l: l.split(";")  # noqa: E731
    elif "," in sample and not ("." not in sample and all(not l.strip() or re.match(r"^\s*[-+]?\d+,\d+\s*$", l)
                                                          for l in lines)):
        split = lambda l: l.split(",")  # noqa: E731
    else:
        split = lambda l: l.strip().split()  # noqa: E731
    cells = {}
    for r, line in enumerate(lines):
        for c, v in enumerate(split(line)):
            if v.strip() != "":
                cells[(r + 1, c + 1)] = v.strip()
    out = dict(cands=grid_series(cells, "text", "text"), meta={}, notes=[], looked=["text table"])
    meta_from_cells(cells, out["meta"], out["notes"])
    return out


def finish(name: str, col: dict):
    best = choose_pair(col["cands"])
    if not best:
        seen = []
        for c in col["cands"]:
            named = f' ("{c["name"]}")' if c["name"] else ""
            seen.append(f"{c['where']}{named}: {sum(1 for v in c['values'] if math.isfinite(v))} numbers")
        raise ValueError(
            "could not find the scan (a laser-distance series with a Z series that goes out and back). "
            f"Looked in: {', '.join(col['looked']) or 'nothing readable'}. "
            + (f"Number series found: {'; '.join(seen[:6])}." if seen else "No long number series found.")
            + " Use “Paste columns” in the browser tool instead.")
    meta = {"file": name}
    for k in FIELD_KEYS:
        meta[k] = col["meta"].get(k)

    def nm(c):
        return f"“{c['name']}”" if c["name"] else c["where"].split(", ")[-1]

    meta["source"] = f"{best['xc']['where'].split(', ')[0]}: {nm(best['xc'])} and {nm(best['zc'])}, {len(best['z'])} points"
    meta["fields_found"] = sum(1 for k in FIELD_KEYS if meta[k] is not None)
    meta["notes"] = col["notes"]
    return best["x"], best["z"], meta


def read_any(path: str | Path):
    """(x, z, meta) from a report workbook (.xlsx/.xlsm/.xls) or a text/CSV file."""
    p = Path(path)
    b = p.read_bytes()
    if b[:2] == b"PK":
        col = collect_zip(b, "file")
    elif b[:2] == b"\xd0\xcf":
        col = collect_cfb(b, "file", "workbook")
    else:
        col = collect_text(decode_text(b))
    return finish(p.name, col)


def decode_text(b: bytes) -> str:
    if b[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return b.decode("utf-16", "replace")
    return b.decode("utf-8-sig", "replace")


# ------------------------------------------------------------- dropouts
def _percentile(sorted_vals, p):
    i = p * (len(sorted_vals) - 1)
    lo, hi = math.floor(i), math.ceil(i)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def dropout_mask(x) -> list[bool]:
    """Invalid readings: not finite, or cut off from the rest by a big gap at either end
    (sensor out-of-range codes such as 300.15, 999, 0 or -1, whatever the value)."""
    fin = sorted(v for v in x if math.isfinite(v))
    n = len(fin)
    lo, hi = -math.inf, math.inf
    if n >= 20:
        g = max(10.0, 0.25 * (_percentile(fin, 0.95) - _percentile(fin, 0.05)))
        for i in range(max(1, math.ceil(0.95 * (n - 1))), n):
            if fin[i] - fin[i - 1] > g:
                hi = fin[i - 1]
                break
        for i in range(math.floor(0.05 * (n - 1)), 0, -1):
            if fin[i] - fin[i - 1] > g:
                lo = fin[i]
                break
    return [math.isfinite(v) and lo <= v <= hi for v in x]
