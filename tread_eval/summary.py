"""Terminal summary of a report payload.

Useful on its own for CI-style comparison of design variants without opening
the HTML: the same numbers the report shows, printed.
"""

from __future__ import annotations

from typing import Any

SEV_MARK = {"flag": "!!", "watch": " ~", "ok": " ."}


def _f(v: Any, nd: int = 2, dash: str = "-") -> str:
    if v is None or isinstance(v, str):
        return dash if v is None else v
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return dash


def print_summary(payload: dict) -> None:
    meta = payload["meta"]
    print()
    print(f"{'lean':>5} {'area':>9} {'CoV':>7} {'max/min':>8} {'blocks':>7} {'Kx':>9} {'Ky':>9} {'Kx/Ky':>6} {'top order':>12}")
    print("-" * 78)
    for r in payload["summary"]:
        top = r["dominant_orders"][0] if r["dominant_orders"] else None
        order_txt = f"{top['order']:.0f} @ {100 * top['amplitude']:.1f}%" if top else "-"
        print(
            f"{_f(r['gamma_deg'], 0):>4}° {_f(r['area_mean'], 0):>9} "
            f"{_f(100 * (r['area_cov'] or 0), 1) + '%':>7} {_f(r['area_max_min'], 2):>8} "
            f"{_f(r['block_count_mean'], 2):>7} {_f(r['kx_mean'], 0):>9} {_f(r['ky_mean'], 0):>9} "
            f"{_f(r['anisotropy_mean'], 3):>6} {order_txt:>12}"
        )

    z = payload["zones"]
    print()
    print(f"land ratio  overall {100 * z['overall_land_ratio']:.1f}%   " + "   ".join(
        f"{k} {100 * v['land_ratio']:.1f}%" for k, v in z["zones"].items()
    ))
    p = payload["pitch"]
    print(
        f"pitches     {p['n_pitches']} of {len(p['unique'])} lengths, mean {_f(p['mean'], 1)} mm, "
        f"max step {_f(p.get('max_adjacent_ratio'), 2)}x, shoulder phase {_f(p.get('shoulder_phase_pitches'), 2)} pitch"
    )
    print(f"crown       supports lean to {_f(meta['max_supported_lean'], 1)}°, curvature correction "
          f"{'ON' if meta['curvature_correction'] else 'off'}")

    print()
    for f in payload["flags"]:
        print(f"{SEV_MARK.get(f['severity'], '  ')} {f['title']}: {f['value']}")
