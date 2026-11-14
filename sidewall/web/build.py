"""Build the single-file tool: inline engine.js into app.html.

    python sidewall/web/build.py   ->   Sidewall_Deflection_Tool.html (repo root)
"""
from pathlib import Path

here = Path(__file__).resolve().parent
html = (here / "app.html").read_text(encoding="utf-8")
engine = (here / "engine.js").read_text(encoding="utf-8")
assert "/*__ENGINE__*/" in html
out = here.parent.parent / "Sidewall_Deflection_Tool.html"
out.write_text(html.replace("/*__ENGINE__*/", engine), encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
