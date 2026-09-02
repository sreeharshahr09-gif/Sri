from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .names import abaqus_name, name_collisions
from .project import ELEMENT_TYPES, POSITIONS


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Coerce without raising.

    v0.6 called float() straight on request data, so a non-numeric field turned a
    422 validation response into a 500 traceback.
    """
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _path_is_numeric(obj: Any, path: str) -> bool:
    cur = obj
    try:
        for p in path.split('.'):
            cur = cur[int(p)] if p.isdigit() else cur[p]
        return isinstance(cur, (int, float)) or cur is None
    except Exception:
        return False


def _dupes(names: List[Any]) -> List[str]:
    seen, dupes = set(), []
    for n in names:
        if n in seen and n not in dupes:
            dupes.append(str(n))
        seen.add(n)
    return dupes


def validate_project(p: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []

    sets = {s.get('name'): s for s in p.get('sets', []) if s.get('name')}
    rps = {r.get('name'): r for r in p.get('reference_points', []) if r.get('name')}
    road = p.get('road') or {}
    if road.get('enabled'):
        rps['RP_ROAD'] = {'name': 'RP_ROAD', 'auto': True}

    if not p.get('step_token'):
        errors.append('No STEP file is associated with the project. Upload geometry first.')
    if not p.get('cad_metadata'):
        errors.append('CAD metadata is missing. Re-upload the STEP file.')

    size = _num(p.get('mesh', {}).get('global_size'))
    if size is None or size <= 0:
        errors.append('Global mesh size must be a number greater than zero.')
    cpus = _num(p.get('analysis', {}).get('cpus'), 1)
    if cpus is None or cpus < 1:
        errors.append('CPU count must be at least 1.')

    solver = p.get('analysis', {}).get('solver', 'standard')
    if solver not in ELEMENT_TYPES:
        errors.append(f"Unsupported solver '{solver}'.")
    else:
        elem = str(p.get('mesh', {}).get('element_type', '')).upper()
        if elem and elem not in ELEMENT_TYPES[solver]:
            errors.append(
                f"Element type {elem} is not available for {solver}. "
                f"Choose one of: {', '.join(ELEMENT_TYPES[solver])}."
            )

    # Sets
    for dupe in _dupes([s.get('name') for s in p.get('sets', [])]):
        errors.append(f'Set name {dupe} is used more than once.')
    for collision in name_collisions([s.get('name') for s in p.get('sets', []) if s.get('name')]):
        errors.append('Set names collide after conversion to Abaqus names: ' + collision)
    for s in p.get('sets', []):
        if s.get('kind') not in {'face', 'edge', 'body'}:
            errors.append(f"Set {s.get('name')} has an unsupported kind.")
        if not s.get('entity_ids'):
            errors.append(f"Set {s.get('name')} is empty.")

    # Steps
    steps = p.get('steps') or []
    if not steps:
        steps = [{'name': p.get('analysis', {}).get('step_name') or 'Load', 'type': 'static'}]
    step_names = [s.get('name') for s in steps if s.get('name')]
    if not step_names:
        errors.append('At least one analysis step is required.')
    for dupe in _dupes(step_names):
        errors.append(f'Analysis step name {dupe} is used more than once.')
    step_index = {name: i for i, name in enumerate(step_names)}
    for st in steps:
        if not st.get('name'):
            errors.append('Every analysis step needs a name.')
        if st.get('type', 'static') not in {'static', 'explicit'}:
            errors.append(f"Step {st.get('name')} has unsupported type {st.get('type')}.")
        tp = _num(st.get('time_period'), 1.0)
        if tp is None or tp <= 0:
            errors.append(f"Step {st.get('name')} time period must be > 0.")
        if st.get('type', 'static') == 'static':
            for key, default, label in (('initial_increment', 0.01, 'initial increment'),
                                        ('min_increment', 1e-8, 'minimum increment'),
                                        ('max_increment', 0.1, 'maximum increment')):
                val = _num(st.get(key), default)
                if val is None or val <= 0:
                    errors.append(f"Step {st.get('name')} {label} must be > 0.")
            ini = _num(st.get('initial_increment'), 0.01)
            mx = _num(st.get('max_increment'), 0.1)
            if ini and mx and ini > mx:
                errors.append(f"Step {st.get('name')} initial increment cannot exceed its maximum increment.")

    # External material library (mandatory: constitutive data live in the .inc)
    matlib = p.get('material_library') or {}
    if not matlib.get('include_token'):
        errors.append('Upload the Abaqus material .inc library.')
    if not str(matlib.get('material_code') or '').strip():
        errors.append('Enter or select a material number/code from the include library.')
    body_set = matlib.get('body_set')
    if not body_set:
        errors.append('Choose the body set the material library should be assigned to.')
    elif body_set not in sets or sets[body_set].get('kind') != 'body':
        errors.append(f"Material assignment target '{body_set}' must be an existing body set.")

    # Reference points
    for dupe in _dupes([r.get('name') for r in p.get('reference_points', [])]):
        errors.append(f'Reference point name {dupe} is used more than once.')
    for r in p.get('reference_points', []):
        if r.get('name') == 'RP_ROAD' and road.get('enabled'):
            errors.append('RP_ROAD is created automatically by the road; rename this reference point.')
        surf = r.get('coupled_surface')
        if surf and (surf not in sets or sets[surf].get('kind') != 'face'):
            errors.append(f"Reference point {r.get('name')} coupling target must be a valid face set.")

    # Analytical rigid road
    if road.get('enabled'):
        for k in ('length', 'width'):
            val = _num(road.get(k))
            if val is None or val <= 0:
                errors.append(f'Road {k} must be greater than zero.')
        if str(road.get('normal_axis', 'Y')).upper() not in {'X', 'Y', 'Z'}:
            errors.append('Road normal axis must be X, Y or Z.')
        contact_set = road.get('contact_set')
        if not contact_set:
            errors.append('Select the tread face set that contacts the road.')
        elif contact_set not in sets or sets[contact_set].get('kind') != 'face':
            errors.append('Road contact set must be a valid face set on the tread.')
        mu = _num(road.get('friction'), 0.0)
        if mu is None or mu < 0:
            errors.append('Road friction must be a number and cannot be negative.')

    # Contacts
    for dupe in _dupes([i.get('name') for i in p.get('interactions', [])]):
        errors.append(f'Interaction name {dupe} is used more than once.')
    for inter in p.get('interactions', []):
        for side in ('master_set', 'slave_set'):
            n = inter.get(side)
            if n not in sets or sets[n].get('kind') != 'face':
                errors.append(f"Interaction {inter.get('name')} {side.replace('_', ' ')} must be a valid face set.")
        if inter.get('master_set') and inter.get('master_set') == inter.get('slave_set'):
            errors.append(f"Interaction {inter.get('name')} uses the same set on both sides; use self-contact instead.")
        mu = _num(inter.get('friction'), 0.0)
        if mu is None or mu < 0:
            errors.append(f"Interaction {inter.get('name')} friction must be a number and cannot be negative.")

    for dupe in _dupes([c.get('name') for c in p.get('self_contacts', [])]):
        errors.append(f'Self-contact name {dupe} is used more than once.')
    for sc in p.get('self_contacts', []):
        surf = sc.get('surface_set')
        if surf not in sets or sets[surf].get('kind') != 'face':
            errors.append(f"Self-contact {sc.get('name')} must target a valid face set.")
        mu = _num(sc.get('friction'), 0.0)
        if mu is None or mu < 0:
            errors.append(f"Self-contact {sc.get('name')} friction must be a number and cannot be negative.")

    # Boundary conditions
    valid_steps = set(step_names)
    first_step = step_names[0] if step_names else None
    created_bcs = {bc.get('name'): (bc.get('step') or first_step)
                   for bc in p.get('boundary_conditions', [])
                   if bc.get('operation', 'create') == 'create' and bc.get('name')}
    for dupe in _dupes([b.get('name') for b in p.get('boundary_conditions', [])]):
        errors.append(f'Boundary condition name {dupe} is used more than once.')
    for bc in p.get('boundary_conditions', []):
        op = bc.get('operation', 'create')
        step = bc.get('step') or first_step
        if step and step not in valid_steps:
            errors.append(f"BC {bc.get('name')} references unknown step {step}.")
        if op == 'modify':
            target = bc.get('target_bc') or bc.get('name')
            if target not in created_bcs:
                errors.append(f"BC modification {bc.get('name')} targets unknown created BC {target}.")
            elif step and created_bcs[target] in step_index and step in step_index:
                if step_index[step] <= step_index[created_bcs[target]]:
                    errors.append(
                        f"BC modification {bc.get('name')} must act in a step after {target} is created."
                    )
            if not any(bc.get(k) not in (None, '') for k in ('u1', 'u2', 'u3', 'ur1', 'ur2', 'ur3')):
                errors.append(f"BC modification {bc.get('name')} does not change any DOF.")
            continue
        region = bc.get('region')
        if bc.get('region_type') == 'rp':
            if region not in rps:
                errors.append(f"BC {bc.get('name')} references an unknown reference point.")
        else:
            if region not in sets:
                errors.append(f"BC {bc.get('name')} references an unknown geometry set.")
            if any(bc.get(k) not in (None, '') for k in ('ur1', 'ur2', 'ur3')):
                warnings.append(
                    f"BC {bc.get('name')} applies rotational DOFs to a geometry set; solid elements have no "
                    f"rotational DOFs. Prefer a coupled reference point for torsion/rotation."
                )

    # Loads
    for dupe in _dupes([l.get('name') for l in p.get('loads', [])]):
        errors.append(f'Load name {dupe} is used more than once.')
    for load in p.get('loads', []):
        typ = load.get('type')
        region = load.get('region')
        if typ == 'pressure':
            if region not in sets or sets[region].get('kind') != 'face':
                errors.append(f"Pressure load {load.get('name')} must target a face set.")
        elif typ in {'force', 'moment'}:
            if region not in rps:
                errors.append(f"{str(typ).title()} load {load.get('name')} must target a reference point.")
        else:
            errors.append(f"Load {load.get('name')} has unsupported type {typ}.")
        step = load.get('step') or first_step
        if step and step not in valid_steps:
            errors.append(f"Load {load.get('name')} references unknown step {step}.")

    # ODB outputs
    for o in p.get('outputs', []):
        if not o.get('variable'):
            errors.append('Every ODB output row needs a variable name.')
        if o.get('position') not in POSITIONS:
            errors.append(f"ODB output {o.get('variable')} has invalid position {o.get('position')}.")
        if o.get('region') and o.get('region') not in sets:
            errors.append(f"ODB output {o.get('variable')} references an unknown set {o.get('region')}.")

    # Parametric sweeps
    for sw in p.get('parametric', []):
        if not sw.get('enabled', True):
            continue
        path = sw.get('path', '')
        if not _path_is_numeric(p, path):
            errors.append(f'Parametric path does not resolve to a numeric field: {path}')
        start, end, inc = _num(sw.get('start')), _num(sw.get('end')), _num(sw.get('increment'))
        if start is None or end is None or inc is None:
            errors.append(f'Parametric start, end and increment must all be numeric: {path}')
            continue
        if inc == 0:
            errors.append(f'Parametric increment cannot be zero: {path}')
        elif (end > start and inc < 0) or (end < start and inc > 0):
            errors.append(f'Parametric increment sign does not move from start to end: {path}')

    if not p.get('boundary_conditions') and not road.get('enabled'):
        warnings.append('No boundary conditions are defined; check for rigid-body motion.')
    if p.get('sipe_detection', {}).get('applied') and not p.get('self_contacts'):
        warnings.append('Sipe walls were detected, but no self-contact definitions are active.')
    if str(p.get('mesh', {}).get('element_type', '')).upper() in {'C3D10', 'C3D4'} and solver == 'standard':
        warnings.append(
            'Nearly incompressible rubber usually needs hybrid elements (C3D10H/C3D4H). '
            'Non-hybrid tets can lock volumetrically and fail to converge.'
        )
    return errors, warnings
