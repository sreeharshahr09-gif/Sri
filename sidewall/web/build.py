"""Build the single-file tool: inline engine.js, charts.js, report.js and the report runtime into app.html.

    python sidewall/web/build.py   ->   Sidewall_Deflection_Tool.html (repo root)
"""
from pathlib import Path

here = Path(__file__).resolve().parent
html = (here / "app.html").read_text(encoding="utf-8")
engine = (here / "engine.js").read_text(encoding="utf-8")
report = (here / "report.js").read_text(encoding="utf-8")
charts = (here / "charts.js").read_text(encoding="utf-8")
runtime = (here / "report_runtime.js").read_text(encoding="utf-8")
for marker in ("/*__ENGINE__*/", "/*__REPORT__*/", "/*__CHARTS__*/", "/*__RUNTIME__*/"):
    assert marker in html, marker
for name, src in (("engine.js", engine), ("charts.js", charts), ("report.js", report), ("report_runtime.js", runtime)):
    assert "</script" not in src.lower(), f"{name} must not contain a closing script tag"
out = here.parent.parent / "Sidewall_Deflection_Tool.html"
html = (html.replace("/*__ENGINE__*/", engine).replace("/*__CHARTS__*/", charts)
        .replace("/*__REPORT__*/", report).replace("/*__RUNTIME__*/", runtime))
out.write_text(html, encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB)")
