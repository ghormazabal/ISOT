"""API de la sección Renders (`/api/renders/...`, también bajo `/api/repos/{id}/`).

Lectura (dashboard, templates, contexto, preview, diff contra el destino) siempre
disponible; escritura (guardar template, renders.yaml, aplicar un render) solo
con edición habilitada y siempre en una rama `wizard/…` del repo afectado.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any, Callable, Iterator

import yaml
from fastapi import FastAPI, HTTPException, Query

from isot.gitdiff import RefError, _materialize, clear_caches, resolve_sha, validate_ref
from isot.paths import tool_root
from isot.renders import (
    INSTANCE_KEYS,
    MAX_SOURCE_BYTES,
    RENDERS_FILE,
    SAFE_FILE,
    SAFE_ID,
    TEMPLATE_DIRNAME,
    TEMPLATE_FILE,
    RenderError,
    RenderService,
    file_diffs,
    find_template,
    head_sha,
    list_templates,
    parse_template,
    render_template,
    safe_rel_dir,
    _parse_instances,
)
from isot.wizard import WizardError

GUIDE_PATH = Path("docs") / "renders" / "GUIDE.md"
LLM_GUIDE_PATH = Path("docs") / "llm" / "RENDERS.md"
ID_LINE = re.compile(r"^id:\s*.*$", re.MULTILINE)


def register(
    app: FastAPI,
    *,
    registry: Any,
    resolved_root: Callable[[], Path],
    edit_for: Callable[[Any], bool],
    edit_globally: Callable[[], bool],
    maybe_push: Callable[[dict[str, Any]], dict[str, Any]],
) -> None:
    @contextlib.contextmanager
    def render_root(ref: str | None) -> Iterator[Path]:
        root = resolved_root()
        if not ref:
            yield root
            return
        try:
            sha = resolve_sha(root, validate_ref(ref))
        except RefError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        for tree in _materialize(root, sha):
            yield tree
            return

    def service(root: Path) -> RenderService:
        return RenderService(root)

    def can_edit() -> bool:
        return bool(registry.configured and edit_for(registry.entry()))

    def require_edit() -> None:
        if not edit_globally():
            raise HTTPException(status_code=403, detail="edición deshabilitada (ISOT_EDIT=1 o --edit)")
        if not can_edit():
            raise HTTPException(status_code=403, detail="repositorio en solo lectura (repos[].edit: false)")

    # ----------------------------------------------------------------- lectura

    @app.get("/api/renders")
    def renders_dashboard(ref: str | None = Query(default=None)) -> dict[str, Any]:
        with render_root(ref) as root:
            svc = service(root)
            payload = svc.dashboard()
        payload["edit"] = can_edit()
        payload["ref"] = ref
        return payload

    @app.get("/api/renders/templates")
    def renders_templates(ref: str | None = Query(default=None)) -> list[dict[str, Any]]:
        with render_root(ref) as root:
            svc = service(root)
            usage: dict[str, int] = {}
            for inst in svc.config.instances:
                usage[inst.template] = usage.get(inst.template, 0) + 1
            return [{**t.summary(), "instances": usage.get(t.id, 0)} for t in list_templates(root)]

    @app.get("/api/renders/templates/{template_id}")
    def renders_template(template_id: str, source: str | None = Query(default=None), ref: str | None = Query(default=None)) -> dict[str, Any]:
        if not SAFE_ID.fullmatch(template_id):
            raise HTTPException(status_code=400, detail="id de template inválido")
        if source not in (None, "repo", "bundled"):
            raise HTTPException(status_code=400, detail="source debe ser repo o bundled")
        with render_root(ref) as root:
            template = find_template(root, template_id, source)
            if template is None:
                raise HTTPException(status_code=404, detail=f"no existe el template de render {template_id}")
            svc = service(root)
            used_by = [i.to_dict() for i in svc.config.instances if i.template == template_id]
            return {**template.detail(), "used_by": used_by}

    @app.get("/api/renders/templates/{template_id}/clone")
    def renders_template_clone(template_id: str, new_id: str = Query(...)) -> dict[str, Any]:
        """Borrador (sin escribir) de una copia de un template con otro id."""
        if not SAFE_ID.fullmatch(template_id) or not SAFE_ID.fullmatch(new_id):
            raise HTTPException(status_code=400, detail="id inválido (minúsculas, números, . _ -)")
        template = find_template(resolved_root(), template_id)
        if template is None:
            raise HTTPException(status_code=404, detail=f"no existe el template de render {template_id}")
        spec_text = template.spec_text
        spec_text = ID_LINE.sub(f"id: {new_id}", spec_text, count=1) if ID_LINE.search(spec_text) else f"id: {new_id}\n" + spec_text
        exists = (resolved_root() / TEMPLATE_DIRNAME / new_id).exists()
        return {"id": new_id, "from": template_id, "spec_text": spec_text, "file_contents": template.files, "exists": exists}

    @app.get("/api/renders/context")
    def renders_context(site: str = Query(...), cluster: str | None = Query(default=None), ref: str | None = Query(default=None)) -> dict[str, Any]:
        with render_root(ref) as root:
            try:
                return service(root).builder.explorer(site, cluster or None)
            except RenderError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/renders/preview")
    def renders_preview(body: dict[str, Any]) -> dict[str, Any]:
        """Render en memoria del template del editor (o de uno guardado) sobre un sitio/clúster."""
        ref = body.get("ref") or None
        with render_root(ref) as root:
            svc = service(root)
            site = str(body.get("site") or "")
            cluster = body.get("cluster") or None
            params = body.get("params") if isinstance(body.get("params"), dict) else {}
            instance_id = "preview"
            if body.get("instance"):
                inst = svc.instance(str(body["instance"]))
                if inst is None:
                    raise HTTPException(status_code=404, detail=f"instancia desconocida: {body['instance']}")
                site, cluster, instance_id = inst.site, inst.cluster, inst.id
                params = {**inst.params, **params}
            if not site:
                raise HTTPException(status_code=400, detail="falta site (o instance)")
            template_id = str(body.get("id") or body.get("template") or "")
            if not template_id and "spec_text" in body:
                match = ID_LINE.search(str(body.get("spec_text") or ""))
                template_id = match.group(0).split(":", 1)[1].strip().strip("\"'") if match else ""
            template_id = template_id or "draft"
            if "spec_text" in body:
                files = body.get("files") or {}
                if not isinstance(files, dict) or not all(isinstance(v, str) for v in files.values()):
                    raise HTTPException(status_code=400, detail="files debe ser {nombre: contenido}")
                if len(str(body.get("spec_text") or "")) > MAX_SOURCE_BYTES or any(len(v) > MAX_SOURCE_BYTES for v in files.values()):
                    raise HTTPException(status_code=400, detail="template demasiado grande")
                template = parse_template(template_id, str(body.get("spec_text") or ""), files, source="draft", path="(editor)")
            else:
                found = find_template(root, template_id)
                if found is None:
                    raise HTTPException(status_code=404, detail=f"no existe el template de render {template_id}")
                template = found
            result = render_template(template, svc.builder, site=site, cluster=cluster, params=params, instance_id=instance_id)
            return {**result.to_dict(), "site": site, "cluster": cluster, "scope": template.scope, "template_errors": template.errors}

    @app.get("/api/renders/diff")
    def renders_diff(
        instance: str | None = Query(default=None),
        target_ref: str | None = Query(default=None),
        ref: str | None = Query(default=None),
    ) -> dict[str, Any]:
        with render_root(ref) as root:
            svc = service(root)
            try:
                payload = svc.target_diff(instance_id=instance, ref=target_ref or None)
            except (RenderError, RefError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            payload["edit"] = can_edit() and not ref
            return payload

    @app.get("/api/renders/guide")
    def renders_guide(doc: str = Query(default="human")) -> dict[str, Any]:
        """Guía de renders (`doc=human`, en castellano) o la guía para LLMs (`doc=llm`)."""
        if doc not in ("human", "llm"):
            raise HTTPException(status_code=400, detail="doc debe ser human o llm")
        rel = LLM_GUIDE_PATH if doc == "llm" else GUIDE_PATH
        path = tool_root() / rel
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"no existe {rel.as_posix()}")
        return {"doc": doc, "path": rel.as_posix(), "markdown": path.read_text(encoding="utf-8")}

    @app.get("/api/renders/config")
    def renders_config(ref: str | None = Query(default=None)) -> dict[str, Any]:
        """Texto actual de renders.yaml (para el editor de texto de la UI)."""
        with render_root(ref) as root:
            path = root / RENDERS_FILE
            text = path.read_text(encoding="utf-8") if path.is_file() else ""
            return {"path": RENDERS_FILE, "exists": path.is_file(), "text": text, "errors": validate_renders_text(text) if text else [], "ref": ref}

    # --------------------------------------------------------------- escritura

    def _branch(body: dict[str, Any]) -> str | None:
        value = body.get("branch")
        return str(value) if value else None

    def template_plan(body: dict[str, Any], root: Path) -> dict[str, Any]:
        template_id = str(body.get("id") or "")
        if not SAFE_ID.fullmatch(template_id):
            raise HTTPException(status_code=400, detail="id inválido (minúsculas, números, . _ -)")
        base = f"{TEMPLATE_DIRNAME}/{template_id}"
        folder = root / base
        existing = sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []
        if body.get("delete_template"):
            return {"id": template_id, "writes": {}, "deletes": [f"{base}/{name}" for name in existing], "errors": [], "base": base}
        files = body.get("files") or {}
        spec_text = str(body.get("spec_text") or "")
        if not isinstance(files, dict) or not all(isinstance(v, str) for v in files.values()):
            raise HTTPException(status_code=400, detail="files debe ser {nombre: contenido}")
        for name in files:
            if not SAFE_FILE.fullmatch(name) or name == TEMPLATE_FILE:
                raise HTTPException(status_code=400, detail=f"nombre de archivo inválido: {name}")
        if len(spec_text) > MAX_SOURCE_BYTES or any(len(v) > MAX_SOURCE_BYTES for v in files.values()):
            raise HTTPException(status_code=400, detail="template demasiado grande")
        template = parse_template(template_id, spec_text, files, source="draft", path=base)
        writes = {f"{base}/{TEMPLATE_FILE}": spec_text if spec_text.endswith("\n") else spec_text + "\n"}
        for name, content in files.items():
            writes[f"{base}/{name}"] = content if content.endswith("\n") or not content else content + "\n"
        deletes = [f"{base}/{name}" for name in existing if name != TEMPLATE_FILE and name not in files]
        return {"id": template_id, "writes": writes, "deletes": deletes, "errors": template.errors, "base": base}

    @app.post("/api/renders/templates/save/preview")
    def renders_template_save_preview(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        with render_root(_branch(body)) as base:
            plan = template_plan(body, base)
            diffs = [d for d in file_diffs(base, plan["writes"], plan["deletes"]) if d["status"] != "unchanged"]
        return {"id": plan["id"], "files": diffs, "errors": plan["errors"], "branch": _branch(body)}

    @app.post("/api/renders/templates/save/commit")
    def renders_template_save_commit(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        with render_root(_branch(body)) as base:
            plan = template_plan(body, base)
            unchanged = {k for k, v in plan["writes"].items() if (base / k).is_file() and (base / k).read_text(encoding="utf-8") == v}
        if plan["errors"]:
            raise HTTPException(status_code=400, detail="template.yaml inválido: " + "; ".join(e["message"] for e in plan["errors"][:5]))
        root = resolved_root()
        writes = {k: v for k, v in plan["writes"].items() if k not in unchanged}
        if not writes and not plan["deletes"]:
            raise HTTPException(status_code=400, detail="no hay cambios que guardar")
        verb = "borrar" if body.get("delete_template") else "template de render"
        try:
            from isot.renders import commit_files

            result = commit_files(root, writes, plan["deletes"], f"{verb} {plan['id']}", allowed_prefix=plan["base"], branch=_branch(body))
        except WizardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        clear_caches(root)
        return maybe_push(result) if result.get("created") else result

    def config_plan(body: dict[str, Any], root: Path) -> dict[str, Any]:
        path = root / RENDERS_FILE
        current = path.read_text(encoding="utf-8") if path.is_file() else ""
        if "text" in body:
            text = str(body.get("text") or "")
        else:
            data = yaml.safe_load(current) if current.strip() else {}
            if not isinstance(data, dict):
                raise HTTPException(status_code=400, detail=f"{RENDERS_FILE} no es un mapa YAML; edítalo como texto")
            data.setdefault("version", 1)
            settings = body.get("settings")
            if isinstance(settings, dict):
                for key in ("target_dir",):
                    if key in settings:
                        if settings[key] in (None, ""):
                            data.pop(key, None)
                        else:
                            data[key] = str(settings[key])
            upsert = body.get("upsert_instance")
            if isinstance(upsert, dict):
                clean = {k: v for k, v in upsert.items() if k in INSTANCE_KEYS and v not in (None, "", {})}
                if clean.get("enabled") is True:
                    clean.pop("enabled")
                items = [i for i in data.get("instances") or [] if isinstance(i, dict)]
                replaced = False
                for index, item in enumerate(items):
                    if item.get("id") == clean.get("id"):
                        items[index] = clean
                        replaced = True
                if not replaced:
                    items.append(clean)
                data["instances"] = items
            remove = body.get("remove_instance")
            if remove:
                data["instances"] = [i for i in data.get("instances") or [] if not (isinstance(i, dict) and i.get("id") == remove)]
            text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
            # Se conserva el bloque de comentarios inicial del archivo.
            header_lines = []
            for line in current.splitlines(keepends=True):
                if not line.startswith("#"):
                    break
                header_lines.append(line)
            text = "".join(header_lines) + text
        errors = validate_renders_text(text)
        return {"text": text, "current": current, "errors": errors}

    @app.post("/api/renders/config/preview")
    def renders_config_preview(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        with render_root(_branch(body)) as base:
            plan = config_plan(body, base)
            diffs = [d for d in file_diffs(base, {RENDERS_FILE: plan["text"]}) if d["status"] != "unchanged"]
        return {"text": plan["text"], "files": diffs, "errors": plan["errors"]}

    @app.post("/api/renders/config/commit")
    def renders_config_commit(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        with render_root(_branch(body)) as base:
            plan = config_plan(body, base)
        if plan["text"] == plan["current"]:
            raise HTTPException(status_code=400, detail=f"{RENDERS_FILE} no cambia")
        if plan["errors"]:
            raise HTTPException(status_code=400, detail=f"{RENDERS_FILE} inválido: " + "; ".join(plan["errors"][:5]))
        root = resolved_root()
        try:
            from isot.renders import commit_files

            result = commit_files(root, {RENDERS_FILE: plan["text"]}, [], str(body.get("description") or "renders.yaml"), branch=_branch(body))
        except WizardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        clear_caches(root)
        return maybe_push(result) if result.get("created") else result

    def apply_plan(body: dict[str, Any]) -> tuple[RenderService, dict[str, Any]]:
        root = resolved_root()
        svc = service(root)
        instances = body.get("instances") or None
        if instances is not None and not isinstance(instances, list):
            raise HTTPException(status_code=400, detail="instances debe ser una lista de ids")
        try:
            plan = svc.apply_plan(instances, prune=bool(body.get("prune")), source_commit=head_sha(root))
        except RenderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return svc, plan

    @app.post("/api/renders/apply/preview")
    def renders_apply_preview(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        _svc, plan = apply_plan(body)
        diffs = [d for d in file_diffs(resolved_root(), plan["writes"], plan["deletes"]) if d["status"] != "unchanged"]
        return {"files": diffs, "skipped": plan["skipped"], "folders": plan["folders"], "prune": bool(body.get("prune"))}

    @app.post("/api/renders/apply/commit")
    def renders_apply_commit(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        _svc, plan = apply_plan(body)
        root = resolved_root()
        if not plan["writes"] and not plan["deletes"]:
            raise HTTPException(status_code=400, detail="la carpeta destino ya está al día")
        try:
            from isot.renders import commit_files

            result = commit_files(
                root,
                plan["writes"],
                plan["deletes"],
                "renders",
                allowed_prefix=plan["folders"],
                branch=_branch(body),
            )
        except WizardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        clear_caches(root)
        return maybe_push(result) if result.get("created") else result


def validate_renders_text(text: str) -> list[str]:
    """Errores de un renders.yaml propuesto (sin escribirlo)."""
    try:
        data = yaml.safe_load(text) if text.strip() else {}
    except yaml.YAMLError as exc:
        return [f"YAML inválido: {exc}"]
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return [f"{RENDERS_FILE} debe ser un mapa YAML"]
    errors: list[str] = []
    from isot.renders import REMOVED_KEYS, RENDERS_KEYS

    removed = sorted(set(data) & REMOVED_KEYS)
    if removed:
        errors.append(
            f"{', '.join(removed)} ya no existe(n): los renders solo se escriben en carpetas de este "
            "mismo repositorio (target_dir global o por instancia)"
        )
    unknown = sorted(set(data) - RENDERS_KEYS - REMOVED_KEYS)
    if unknown:
        errors.append(f"claves desconocidas: {', '.join(unknown)}")
    try:
        safe_rel_dir(data.get("target_dir") or "rendered", "target_dir")
    except RenderError as exc:
        errors.append(str(exc))
    instances = _parse_instances(data.get("instances"), RENDERS_FILE, None, errors)
    ids = [i.id for i in instances]
    errors.extend(f"instancia duplicada: {dup}" for dup in sorted({i for i in ids if ids.count(i) > 1}))
    return errors
