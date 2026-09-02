from __future__ import annotations

import copy
import itertools
from typing import Any, Dict, List

# A mistyped increment (0.001 across a range of 1) would otherwise try to write
# thousands of case directories and copies of the STEP file.
MAX_VALUES_PER_SWEEP = 500
MAX_CASES = 2000


class ParametricError(ValueError):
    pass


def _frange(start: float, stop: float, step: float) -> List[float]:
    if step == 0:
        raise ParametricError('Parametric increment cannot be zero.')
    span = stop - start
    if (span > 0 and step < 0) or (span < 0 and step > 0):
        raise ParametricError('Parametric increment sign does not move from start to end.')
    # Computed from the index rather than by repeated addition, which accumulated
    # floating-point drift and could drop or add a final value.
    count = int(round(abs(span) / abs(step))) + 1
    if count > MAX_VALUES_PER_SWEEP:
        raise ParametricError(
            'A parametric sweep would produce %d values (limit %d). Increase the increment.'
            % (count, MAX_VALUES_PER_SWEEP)
        )
    values = [round(start + i * step, 12) for i in range(count)]
    # Guard against an increment that does not divide the span exactly.
    if values and abs(values[-1] - stop) > abs(step) * 1e-9:
        if (step > 0 and values[-1] < stop) or (step < 0 and values[-1] > stop):
            values.append(round(stop, 12))
    return values


def _set_path(obj: Dict[str, Any], path: str, value: Any) -> None:
    parts = path.split('.')
    cur: Any = obj
    for p in parts[:-1]:
        cur = cur[int(p)] if p.isdigit() else cur[p]
    last = parts[-1]
    if last.isdigit():
        cur[int(last)] = value
    else:
        cur[last] = value


def expand_cases(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    sweeps = [s for s in project.get('parametric', []) if s.get('enabled', True)]
    if not sweeps:
        return [{'name': 'case_001', 'project': copy.deepcopy(project), 'parameters': {}}]

    vectors = []
    total = 1
    for s in sweeps:
        try:
            values = _frange(float(s['start']), float(s['end']), float(s['increment']))
        except (TypeError, ValueError) as e:
            if isinstance(e, ParametricError):
                raise
            raise ParametricError('Parametric start, end and increment must be numeric: %s' % s.get('path'))
        vectors.append((s['path'], values))
        total *= len(values)
        if total > MAX_CASES:
            raise ParametricError(
                'The enabled sweeps would produce %d or more cases (limit %d). '
                'Reduce the ranges or disable a sweep.' % (total, MAX_CASES)
            )

    out = []
    for i, combo in enumerate(itertools.product(*[v[1] for v in vectors]), start=1):
        p = copy.deepcopy(project)
        params = {}
        for (path, _), value in zip(vectors, combo):
            _set_path(p, path, value)
            params[path] = value
        out.append({'name': 'case_%03d' % i, 'project': p, 'parameters': params})
    return out
