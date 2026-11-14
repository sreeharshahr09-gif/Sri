"""Read the lab's Load Deflection Report workbook (.xlsx).

Layout of the file:
  * Sheet 1 holds the test header: labels with the value below them (table
    style, e.g. "Load (daN)") or to their right (labels ending in a colon,
    e.g. "Request No.:"). Cached values are read; no Excel needed.
  * The scan itself is NOT on the sheet. It is the datasheet of an embedded
    MS Graph chart (xl/embeddings/oleObject*.bin): an OLE compound file whose
    "Workbook" stream holds BIFF records. Type 0x0003 records are numeric
    cells: row 0 = "Laser Distance (mm)", row 1 = "Z Position (mm)".

Standard library only, so it runs anywhere Python does.
"""
from __future__ import annotations

import re
import struct
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


# ------------------------------------------------------------ OLE compound file
def _cfb_streams(data: bytes) -> dict[str, bytes]:
    """Minimal reader for OLE2 compound files: returns {stream name: bytes}."""
    if data[:8] != bytes.fromhex("D0CF11E0A1B11AE1"):
        raise ValueError("not an OLE compound file")
    ssz = 1 << struct.unpack_from("<H", data, 0x1E)[0]
    msz = 1 << struct.unpack_from("<H", data, 0x20)[0]
    n_fat, dir_start = struct.unpack_from("<II", data, 0x2C)
    cutoff, mfat_start, n_mfat, difat_start, n_difat = struct.unpack_from("<IIIII", data, 0x38)

    def sector(i):
        return data[(i + 1) * ssz:(i + 2) * ssz]

    difat = list(struct.unpack_from("<109I", data, 0x4C))
    nxt = difat_start
    for _ in range(n_difat):
        s = sector(nxt)
        vals = struct.unpack(f"<{ssz // 4}I", s)
        difat += vals[:-1]
        nxt = vals[-1]
    fat: list[int] = []
    for i in difat[:n_fat]:
        fat += struct.unpack(f"<{ssz // 4}I", sector(i))

    def chain(start, table):
        out, i, seen = [], start, set()
        while i < 0xFFFFFFFA and i not in seen:
            seen.add(i)
            out.append(i)
            i = table[i]
        return out

    def read(start, size):
        return b"".join(sector(i) for i in chain(start, fat))[:size]

    dirs = read(dir_start, len(chain(dir_start, fat)) * ssz)
    entries = []
    for off in range(0, len(dirs), 128):
        e = dirs[off:off + 128]
        nlen = struct.unpack_from("<H", e, 0x40)[0]
        name = e[:max(nlen - 2, 0)].decode("utf-16-le", "replace")
        etype = e[0x42]
        start, size = struct.unpack_from("<II", e, 0x74)
        entries.append((name, etype, start, size))

    root = entries[0]
    ministream = read(root[2], root[3])
    mfat: list[int] = []
    for i in chain(mfat_start, fat)[:n_mfat] if n_mfat else []:
        mfat += struct.unpack(f"<{ssz // 4}I", sector(i))

    out = {}
    for name, etype, start, size in entries:
        if etype != 2:  # 2 = stream
            continue
        if size < cutoff:
            out[name] = b"".join(ministream[i * msz:(i + 1) * msz] for i in chain(start, mfat))[:size]
        else:
            out[name] = read(start, size)
    return out


# ----------------------------------------------------------- MS Graph datasheet
def _graph_datasheet(workbook_stream: bytes) -> tuple[dict, list[str]]:
    """Numeric cells {(row, col): value} and series titles from a Graph BIFF stream."""
    cells, titles, p = {}, [], 0
    w = workbook_stream
    while p + 4 <= len(w):
        rtype, n = struct.unpack_from("<HH", w, p)
        d = w[p + 4:p + 4 + n]
        p += 4 + n
        if rtype in (0x0003, 0x0203) and n >= 12:      # NUMBER (BIFF2 / BIFF3+)
            r, c = struct.unpack_from("<HH", d)
            cells[(r, c)] = struct.unpack_from("<d", d, n - 8)[0]
        elif rtype == 0x0002 and n >= 9:               # INTEGER (BIFF2)
            r, c = struct.unpack_from("<HH", d)
            cells[(r, c)] = float(struct.unpack_from("<H", d, n - 2)[0])
        elif rtype == 0x100D and n >= 4:               # SERIESTEXT
            k = d[2]
            titles.append(d[4:4 + 2 * k].decode("utf-16-le", "replace").strip()
                          if d[3] else d[4:4 + k].decode("latin-1").strip())
    return cells, titles


def _is_stage(v: list[float]) -> bool:
    """The stage channel runs one way, turns once, and runs back."""
    if len(v) < 10:
        return False
    d = [b - a for a, b in zip(v, v[1:])]
    flips = sum(1 for a, b in zip(d, d[1:]) if a * b < 0)
    return flips <= 3 and max(v) - min(v) > 20


# ----------------------------------------------------------------- sheet header
def _sheet_cells(z: zipfile.ZipFile) -> dict[tuple[int, int], object]:
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['m']}}}t")))
    sheet = sorted(n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n))[0]
    cells = {}
    for c in ET.fromstring(z.read(sheet)).iter(f"{{{NS['m']}}}c"):
        ref, t = c.get("r"), c.get("t")
        v = c.find("m:v", NS)
        if v is None or v.text is None:
            is_ = c.find("m:is", NS)
            if is_ is None:
                continue
            val = "".join(x.text or "" for x in is_.iter(f"{{{NS['m']}}}t"))
        elif t == "s":
            val = shared[int(v.text)]
        elif t in ("str", "inlineStr", "e"):
            val = v.text
        elif t == "b":
            val = v.text == "1"
        else:
            val = float(v.text)
            val = int(val) if val.is_integer() else val
        m = re.match(r"([A-Z]+)(\d+)", ref)
        col = 0
        for ch in m.group(1):
            col = col * 26 + ord(ch) - 64
        cells[(int(m.group(2)), col)] = val
    return cells


def _norm(label: str) -> str:
    return re.sub(r"[\s:：]+$", "", label.replace("（", "(").replace("）", ")")).strip()


def _header(cells: dict) -> dict:
    """Label -> value. Colon labels read rightwards, table labels read downwards."""
    out = {}
    for (r, c), v in sorted(cells.items()):
        if not isinstance(v, str) or not v.strip():
            continue
        if re.search(r"[:：]\s*$", v):
            right = [cells[(r, cc)] for cc in range(c + 1, c + 6) if cells.get((r, cc)) not in (None, "")]
            if right:
                out[_norm(v)] = right[0]
            continue
        below = None
        for rr in range(r + 1, r + 4):
            cv = cells.get((rr, c))
            if cv in (None, ""):
                continue
            if isinstance(cv, str) and (rr, c) in cells and _looks_like_label(cv, cells, rr, c):
                break
            below = cv
            break
        if below is not None:
            out.setdefault(_norm(v), below)
    return out


def _looks_like_label(v: str, cells, r, c) -> bool:
    return bool(re.search(r"\((mm|kg|daN|PSI|°C)\)|^(Tyre Size|Brand|Product ID|Rim Type)$", _norm(v)))


# ---------------------------------------------------------------------- public
FIELDS = {  # report label -> short key
    "Request No.": "request", "Test Date": "test_date", "Serial number": "serial",
    "Test Feature": "feature", "Test engineer name": "engineer", "Product ID": "product_id",
    "Tyre Size": "size", "Brand": "brand", "SW (mm)": "section_width_mm", "OD (mm)": "od_mm",
    "Tire Weight(kg)": "tire_weight_kg", "Test Pressure(PSI)": "pressure_psi",
    "Load (daN)": "load_daN", "Rim Type": "rim", "Room Temp. (°C)": "room_temp_C",
    "Unload Distance (mm)": "unload_distance_mm", "Unload Position (mm)": "unload_position_mm",
    "Load Distance (mm)": "load_distance_mm", "Load Position (mm)": "load_position_mm",
    "Radial Deflection (mm)": "radial_deflection_mm",
    "Horizontal Deflection (mm)": "horizontal_deflection_mm",
}


def read_report(path: str | Path) -> tuple[list[float], list[float], dict]:
    """Return (x, z, meta) from a Load Deflection Report workbook."""
    with zipfile.ZipFile(path) as z:
        hdr = _header(_sheet_cells(z))
        meta = {key: hdr.get(label) for label, key in FIELDS.items()}
        meta["file"] = Path(path).name
        scan = None
        for name in sorted(n for n in z.namelist() if n.startswith("xl/embeddings/")):
            try:
                streams = _cfb_streams(z.read(name))
            except ValueError:
                continue
            if "Workbook" not in streams:
                continue
            cells, titles = _graph_datasheet(streams["Workbook"])
            rows = {}
            for (r, c), v in cells.items():
                rows.setdefault(r, {})[c] = v
            series = [[row[c] for c in sorted(row)] for _, row in sorted(rows.items())]
            if len(series) >= 2:
                scan = (series, titles)
                break
    if scan is None:
        raise ValueError(f"{Path(path).name}: no embedded scan datasheet found")
    series, titles = scan
    zi = next((i for i, s in enumerate(series) if _is_stage(s)), 1)
    xi = next(i for i in range(len(series)) if i != zi)
    meta["series_titles"] = [t for t in titles if t]
    return series[xi], series[zi], meta
