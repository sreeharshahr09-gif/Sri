"""STEP -> Abaqus automation platform, v0.7.

The browser keeps exactly one job: showing the geometry and letting you click
faces, edges and bodies.  Everything else -- project state, defaults, validation,
sipe detection, package generation and the solver run -- lives here in Python and
is delivered as server-rendered HTML forms.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import project as proj
from core.abaqus_generator import generate_build_script, generate_odb_script
from core.cad import parse_step, sha256_file
from core.material_library import scan_material_include, resolve_material_code
from core.parametric import ParametricError, expand_cases
from core.runner import is_active, load_status, start_generation
from core.sipe_detection import detect_sipes
from core.validation import validate_project

VERSION = proj.VERSION
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
TEMPLATES = ROOT / "templates"
WORKSPACE = ROOT / "workspace"
UPLOADS = WORKSPACE / "uploads"
GENERATED = WORKSPACE / "generated"
for d in (UPLOADS, GENERATED):
    d.mkdir(parents=True, exist_ok=True)

# Uploads are read fully into memory before parsing, so cap them.
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_GEN_ID_RE = re.compile(r"^[0-9]{8}_[0-9]{6}_[0-9a-f]{6}$")

app = FastAPI(title="STEP to Abaqus Automation Platform", version=VERSION)
templates = Jinja2Templates(directory=str(TEMPLATES))
store = proj.ProjectStore(WORKSPACE / "project.json")

# Single-user local tool: notices for the next rendered page.
_notices: Dict[str, List[str]] = {"info": [], "error": [], "warning": []}


def notify(kind: str, message: str) -> None:
    if message:
        _notices.setdefault(kind, []).append(message)


def take_notices() -> Dict[str, List[str]]:
    out = {k: list(v) for k, v in _notices.items()}
    for v in _notices.values():
        del v[:]
    return out


def back() -> RedirectResponse:
    """POST/redirect/GET so a browser refresh never re-submits a form."""
    return RedirectResponse("/", status_code=303)


def _uploaded_step(token: Optional[str]) -> Optional[Path]:
    """Resolve a STEP token to a file without letting it escape the upload dir."""
    if not token or not _TOKEN_RE.match(str(token)):
        return None
    for suffix in (".step", ".stp"):
        candidate = UPLOADS / f"{token}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _generation_dir(generation_id: str) -> Optional[Path]:
    if not _GEN_ID_RE.match(generation_id or ""):
        return None
    gen_dir = GENERATED / generation_id
    return gen_dir if gen_dir.is_dir() else None


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    project = store.load()
    errors, warnings = ([], [])
    if project.get("step_token"):
        errors, warnings = validate_project(project)
    return templates.TemplateResponse(request, "index.html", {
        "version": VERSION,
        "p": project,
        "sections": proj.SECTIONS,
        "groups": proj.SCALAR_GROUPS,
        "options": lambda source, row=None: proj.option_source(project, source, row),
        "errors": errors,
        "warnings": warnings,
        "notices": take_notices(),
        "sets_json": json.dumps([{"name": s["name"], "kind": s["kind"], "entity_ids": s["entity_ids"]}
                                 for s in project["sets"]]),
        "sipe_preview_json": json.dumps(
            (project.get("sipe_detection", {}).get("result") or {}).get("all_wall_ids") or []),
    })


@app.get("/api/health")
def health():
    return {"ok": True, "version": VERSION}


@app.get("/api/mesh")
def api_mesh():
    """Tessellated geometry for the viewer. The only fetch the page makes."""
    project = store.load()
    token = project.get("step_token")
    path = _uploaded_step(token)
    if not path:
        return JSONResponse({"ok": False, "reason": "no STEP loaded"}, status_code=404)
    cached = UPLOADS / f"{token}.mesh.json"
    if cached.exists():
        return FileResponse(cached, media_type="application/json")
    return JSONResponse({"ok": False, "reason": "mesh cache missing"}, status_code=404)


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------

@app.post("/step")
async def upload_step(file: UploadFile = File(...)):
    project = store.load()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".step", ".stp"}:
        notify("error", "Please upload a .step or .stp file.")
        return back()
    data = await file.read()
    if not data:
        notify("error", "The uploaded STEP file was empty.")
        return back()
    if len(data) > MAX_UPLOAD_BYTES:
        notify("error", f"STEP file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
        return back()

    token = uuid.uuid4().hex
    path = UPLOADS / f"{token}{suffix}"
    path.write_bytes(data)
    try:
        parsed = parse_step(path)
    except Exception as e:
        path.unlink(missing_ok=True)
        notify("error", f"STEP import failed: {e}")
        return back()

    # One payload with everything the viewer needs: triangles, edge polylines and
    # the face -> body mapping used for body picking.
    meta = parsed["metadata"]
    viewer_payload = {
        "mesh": parsed["mesh"],
        "bbox": meta["bbox"],
        "faces": [{"id": f["id"], "body_id": f["body_id"]} for f in meta["faces"]],
        "edges": [{"id": e["id"], "polyline": e["polyline"]} for e in meta["edges"]],
    }
    (UPLOADS / f"{token}.mesh.json").write_text(json.dumps(viewer_payload), encoding="utf-8")
    project["step_token"] = token
    project["uploaded_name"] = file.filename
    project["cad_metadata"] = parsed["metadata"]
    # New geometry invalidates every entity id the old sets referred to.
    project["sets"] = []
    project["self_contacts"] = []
    project["sipe_detection"] = {"applied": False, "result": None}
    project["road"]["contact_set"] = ""
    project["material_library"]["body_set"] = ""
    project["generation_id"] = None
    store.save(project)
    notify("info", "Loaded %s: %d bodies, %d faces, %d edges." % (
        file.filename, len(meta["bodies"]), len(meta["faces"]), len(meta["edges"])))
    return back()


@app.post("/material")
async def upload_material(file: UploadFile = File(...)):
    project = store.load()
    if Path(file.filename or "").suffix.lower() != ".inc":
        notify("error", "Please upload an Abaqus .inc material include file.")
        return back()
    data = await file.read()
    if not data:
        notify("error", "The uploaded material include file was empty.")
        return back()
    if len(data) > MAX_UPLOAD_BYTES:
        notify("error", "Material include file is too large.")
        return back()

    token = uuid.uuid4().hex
    path = UPLOADS / f"{token}.inc"
    path.write_bytes(data)
    try:
        scanned = scan_material_include(path)
    except Exception as e:
        path.unlink(missing_ok=True)
        notify("error", f"Could not read the material include file: {e}")
        return back()

    codes = scanned.get("material_codes", [])
    matlib = project["material_library"]
    matlib.update({
        "include_token": token,
        "uploaded_name": file.filename,
        "sha256": sha256_file(path),
        "available_codes": codes,
    })
    if codes and matlib.get("material_code") not in codes:
        matlib["material_code"] = codes[0]
    store.save(project)
    if codes:
        notify("info", "Loaded %s: %d material code(s) found." % (file.filename, len(codes)))
    else:
        notify("warning",
               "Loaded %s, but no plaintext '*Material, name=' headers were readable. "
               "This is expected for an encrypted library: type the material code and Abaqus "
               "will validate it during input processing." % file.filename)
    return back()


# ---------------------------------------------------------------------------
# Project editing
# ---------------------------------------------------------------------------

@app.post("/project")
async def save_project(request: Request):
    form = await request.form()
    project = store.load()
    proj.apply_form(project, form)
    message = proj.apply_action(project, str(form.get("action") or ""))
    store.save(project)
    notify("info", message or "Settings saved.")
    return back()


@app.post("/project/reset")
def reset_project():
    store.reset()
    notify("info", "Project reset to defaults.")
    return back()


@app.get("/project.json")
def download_project():
    path = WORKSPACE / "project_export.json"
    path.write_text(json.dumps(proj.export_project(store.load()), indent=2), encoding="utf-8")
    return FileResponse(path, media_type="application/json", filename="abaqus_project.json")


@app.post("/project/load")
async def load_project_file(file: UploadFile = File(...)):
    data = await file.read()
    try:
        incoming = json.loads(data.decode("utf-8"))
    except Exception as e:
        notify("error", f"Invalid project JSON: {e}")
        return back()
    if not isinstance(incoming, dict):
        notify("error", "Project JSON must be a JSON object.")
        return back()

    project = store.load()
    # Geometry stays with whatever STEP is loaded now; a saved project describes
    # settings, and its entity ids only mean anything against the same STEP.
    keep = {k: project[k] for k in ("step_token", "uploaded_name", "cad_metadata")}
    merged = proj.default_project()
    merged.update({k: v for k, v in incoming.items() if k in merged})
    merged.update(keep)
    merged["material_library"].update({
        k: project["material_library"].get(k)
        for k in ("include_token", "uploaded_name", "sha256", "available_codes")
        if project["material_library"].get(k)
    })
    store.save(merged)

    known = {s["name"] for s in merged["sets"]}
    if merged["sets"] and project.get("cad_metadata"):
        valid_ids = {i["id"] for kind in ("bodies", "faces", "edges")
                     for i in project["cad_metadata"].get(kind, [])}
        stale = [s["name"] for s in merged["sets"]
                 if not set(s.get("entity_ids", [])) <= valid_ids]
        if stale:
            notify("warning", "These sets reference geometry ids the loaded STEP does not have: "
                              + ", ".join(stale) + ". Re-create them against this geometry.")
    notify("info", "Loaded project settings (%d set(s))." % len(known))
    return back()


# ---------------------------------------------------------------------------
# Named sets
# ---------------------------------------------------------------------------

@app.post("/sets")
def create_set(name: str = Form(""), kind: str = Form("face"), entity_ids: str = Form("")):
    project = store.load()
    ids = [x for x in (entity_ids or "").split(",") if x]
    error = proj.add_set(project, name, kind, ids)
    if error:
        notify("error", error)
    else:
        store.save(project)
        notify("info", "Set %s now contains %d entity/entities." % (name.strip(), len(ids)))
    return back()


@app.post("/sets/delete")
def remove_set(name: str = Form(...)):
    project = store.load()
    proj.delete_set(project, name)
    store.save(project)
    notify("info", f"Deleted set {name}.")
    return back()


# ---------------------------------------------------------------------------
# Sipe detection
# ---------------------------------------------------------------------------

@app.post("/sipes/detect")
async def sipes_detect(request: Request):
    form = await request.form()
    project = store.load()
    proj.apply_form(project, form)
    path = _uploaded_step(project.get("step_token"))
    if not path:
        notify("error", "Load a STEP file before detecting sipes.")
        return back()

    cfg = dict(project["sipe"])
    friction = cfg.pop("friction", 0.8)
    try:
        result = detect_sipes(path, cfg)
    except Exception as e:
        notify("error", f"Sipe detection failed: {e}")
        return back()

    project["sipe_detection"] = {"applied": False, "result": result}
    project["sipe"]["friction"] = friction
    store.save(project)
    sipes = result.get("sipes") or []
    if sipes:
        notify("info", "Detected %d sipe(s) covering %d wall faces. Review the purple faces, then apply."
               % (len(sipes), len(result.get("all_wall_ids") or [])))
    else:
        notify("warning", "No sipe wall pairs matched these settings. Increase the max gap, "
                          "reduce the min wall depth, or check the radial axis.")
    return back()


@app.post("/sipes/apply")
def sipes_apply():
    project = store.load()
    result = (project.get("sipe_detection") or {}).get("result")
    if not result:
        notify("error", "Run sipe detection first.")
        return back()
    message = proj.apply_sipe_detection(project, result, float(project["sipe"].get("friction", 0.8) or 0.0))
    store.save(project)
    notify("info", message)
    return back()


# ---------------------------------------------------------------------------
# Generate and run
# ---------------------------------------------------------------------------

@app.post("/generate")
async def generate(request: Request):
    form = await request.form()
    project = store.load()
    proj.apply_form(project, form)
    store.save(project)

    errors, warnings = validate_project(project)
    if errors:
        # v0.6 returned this as a JSON object that the page rendered as
        # "[object Object]". Every reason is now shown as readable text.
        for message in errors:
            notify("error", message)
        return back()

    step_path = _uploaded_step(project.get("step_token"))
    if not step_path:
        notify("error", "The uploaded STEP file is no longer available. Upload it again.")
        return back()
    source_hash = (project.get("cad_metadata") or {}).get("source", {}).get("sha256")
    if source_hash and source_hash != sha256_file(step_path):
        notify("error", "The STEP file on disk no longer matches the project metadata. Re-upload it.")
        return back()

    matlib = project["material_library"]
    inc_path = UPLOADS / f"{matlib.get('include_token')}.inc"
    if not _TOKEN_RE.match(str(matlib.get("include_token") or "")) or not inc_path.exists():
        notify("error", "The uploaded material include file is no longer available. Upload it again.")
        return back()
    if matlib.get("sha256") and matlib["sha256"] != sha256_file(inc_path):
        notify("error", "The material include file changed on disk. Upload it again.")
        return back()

    available = scan_material_include(inc_path).get("material_codes", [])
    requested = str(matlib.get("material_code") or "").strip()
    if available:
        canonical = resolve_material_code(inc_path, requested)
        if not canonical:
            notify("error", "Material code '%s' was not found in %s. Available codes: %s"
                   % (requested, matlib.get("uploaded_name") or inc_path.name, ", ".join(available)))
            return back()
    else:
        # Encrypted libraries expose no plaintext headers; Abaqus validates the
        # code during input processing instead.
        canonical = requested

    try:
        cases = expand_cases(project)
    except ParametricError as e:
        notify("error", str(e))
        return back()

    gen_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    gen_dir = GENERATED / gen_id
    gen_dir.mkdir(parents=True)
    include_name = Path(str(matlib.get("uploaded_name") or "materials.inc")).name

    manifest: Dict[str, Any] = {"generation_id": gen_id, "source": step_path.name,
                                "material_include": include_name, "cases": []}
    for case in cases:
        case_dir = gen_dir / case["name"]
        case_dir.mkdir()
        case_project = case["project"]
        if len(cases) > 1:
            base_job = case_project.setdefault("analysis", {}).get("job_name", "AutoJob")
            case_project["analysis"]["job_name"] = f"{base_job}_{case['name']}"
        shutil.copy2(step_path, case_dir / "input.step")
        shutil.copy2(inc_path, case_dir / include_name)
        case_matlib = case_project.setdefault("material_library", {})
        case_matlib["material_code"] = canonical
        case_matlib["local_include_name"] = include_name
        (case_dir / "project.json").write_text(
            json.dumps(proj.export_project(case_project), indent=2), encoding="utf-8")
        (case_dir / "parameters.json").write_text(json.dumps(case["parameters"], indent=2), encoding="utf-8")
        (case_dir / "build_and_run.py").write_text(
            generate_build_script(case_project, case_dir / "input.step", case_dir), encoding="utf-8")
        (case_dir / "extract_odb.py").write_text(
            generate_odb_script(case_project, case_dir), encoding="utf-8")
        manifest["cases"].append({"name": case["name"], "parameters": case["parameters"]})

    (gen_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    shutil.make_archive(str(gen_dir), "zip", root_dir=gen_dir)

    project["generation_id"] = gen_id
    store.save(project)
    for message in warnings:
        notify("warning", message)
    notify("info", "Generated %d case(s) as %s." % (len(cases), gen_id))
    return back()


@app.post("/run")
def run(abaqus_command: str = Form("abaqus")):
    project = store.load()
    generation_id = project.get("generation_id") or ""
    gen_dir = _generation_dir(generation_id)
    if not gen_dir:
        notify("error", "Generate an analysis package before running Abaqus.")
        return back()
    project["abaqus_command"] = abaqus_command
    store.save(project)
    start_generation(gen_dir, abaqus_command or "abaqus")
    return RedirectResponse(f"/run/{generation_id}", status_code=303)


@app.get("/run/{generation_id}", response_class=HTMLResponse)
def run_status(request: Request, generation_id: str):
    gen_dir = _generation_dir(generation_id)
    if not gen_dir:
        notify("error", "Generation not found.")
        return back()
    status = load_status(gen_dir)
    # The page refreshes itself while work is outstanding. v0.6 polled from
    # JavaScript and stopped on the first 'not_started' reply, which arrived
    # before the worker thread had written its first status.
    return templates.TemplateResponse(request, "run.html", {
        "version": VERSION,
        "generation_id": generation_id,
        "status": status,
        "active": is_active(status),
        "status_json": json.dumps(status, indent=2),
    })


@app.get("/download/{generation_id}")
def download(generation_id: str):
    if not _GEN_ID_RE.match(generation_id or ""):
        notify("error", "Invalid generation id.")
        return back()
    zip_path = GENERATED / f"{generation_id}.zip"
    if not zip_path.exists():
        notify("error", "Generated package not found.")
        return back()
    return FileResponse(zip_path, media_type="application/zip",
                        filename=f"abaqus_generation_{generation_id}.zip")


app.mount("/static", StaticFiles(directory=STATIC), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8765, reload=False)
