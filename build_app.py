#!/usr/bin/env python3
"""Build the single-file standalone browser app ``tread_tool.html``.

Inlines the JS engine, the Web Worker glue, the UI code, the stylesheet, an
embedded Plotly, the reader's guide (rendered from ``GUIDE.md`` with the same
converter the report uses) and a bundled sample DXF into one self-contained
HTML file that opens directly in a browser -- no server, no build tools, no
network.  The engine is the same code the Node parity tests in ``app/selftest``
check against the verified Python pipeline, so the numbers the page shows are
the numbers the tests verify.
"""

from __future__ import annotations

import os

from tread_eval import markdown as md

ROOT = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(ROOT, "app")


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _find_plotly() -> str:
    import plotly

    candidate = os.path.join(os.path.dirname(plotly.__file__), "package_data", "plotly.min.js")
    if not os.path.exists(candidate):
        raise FileNotFoundError(
            "plotly.min.js not found in the installed plotly package; run `pip install plotly`"
        )
    return _read(candidate)


def _sample_dxf() -> str:
    path = os.path.join(ROOT, "data", "130_80R17_Tramplr_XR_tread_plan.dxf")
    if not os.path.exists(path):
        return ""
    text = _read(path)
    # A DXF is group-code text and never contains "</script>", but guard anyway
    # so a stray token can never break out of the <script type=text/plain> host.
    return text.replace("</", "<\\/")


def build(out_path: str | None = None) -> str:
    template = _read(os.path.join(APP, "template.html"))
    guide_html = md.render(_read(os.path.join(ROOT, "GUIDE.md")))

    # Order matters only for readability; each token is a literal placeholder.
    replacements = {
        "/*__STYLE__*/": _read(os.path.join(APP, "style.css")),
        "/*__PLOTLY__*/": _find_plotly(),
        "/*__ENGINE__*/": _read(os.path.join(APP, "engine.js")),
        "/*__WORKER__*/": _read(os.path.join(APP, "worker.js")),
        "/*__UI__*/": _read(os.path.join(APP, "ui.js")),
        "<!--__GUIDE__-->": guide_html,
        "<!--__SAMPLE_DXF__-->": _sample_dxf(),
    }
    html = template
    for token, value in replacements.items():
        if token not in html:
            raise ValueError(f"placeholder {token!r} missing from template.html")
        html = html.replace(token, value)

    out_path = out_path or os.path.join(ROOT, "tread_tool.html")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    size_mb = os.path.getsize(out_path) / 1e6
    print(f"wrote {out_path} ({size_mb:.2f} MB)")
    return out_path


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output", default=None, help="output HTML path (default: tread_tool.html)")
    args = ap.parse_args()
    build(args.output)
