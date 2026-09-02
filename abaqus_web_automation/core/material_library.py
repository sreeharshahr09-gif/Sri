from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List


_MATERIAL_RE = re.compile(
    r"^[ \t]*\*MATERIAL\b[^\r\n]*?\bNAME\s*=\s*([^,\r\n]+)",
    flags=re.IGNORECASE | re.MULTILINE,
)


def scan_material_include(path: Path) -> Dict[str, object]:
    """Return material names/codes declared by *Material, name=... in an Abaqus include file.

    The original spelling is preserved. Duplicate names are collapsed case-insensitively.
    The parser is deliberately narrow: it does not try to interpret constitutive data, only
    the material-name declarations needed by the web UI and validation layer.
    """
    text = path.read_text(encoding="utf-8", errors="ignore")
    codes: List[str] = []
    seen = set()
    for m in _MATERIAL_RE.finditer(text):
        raw = m.group(1).strip().strip('"').strip("'")
        if not raw:
            continue
        key = raw.upper()
        if key not in seen:
            seen.add(key)
            codes.append(raw)
    return {
        "material_codes": codes,
        "count": len(codes),
    }


def resolve_material_code(path: Path, requested: str) -> str | None:
    requested = str(requested or "").strip()
    if not requested:
        return None
    data = scan_material_include(path)
    for code in data["material_codes"]:  # type: ignore[index]
        if str(code).upper() == requested.upper():
            return str(code)
    return None
