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

from isot.compare import changed_files
from isot.gitdiff import RefError, clear_caches
from isot.refs import is_local, resolve_ref, tree_for
from isot.paths import tool_root
from isot.renders import (
    INSTANCE_KEYS,
    MAX_SOURCE_BYTES,
    RENDERS_FILE,
    SAFE_FILE,
    SAFE_ID,
    TEMPLATE_DIRNAME,
    TEMPLATE_FILE,
    Instance,
    RenderError,
    RenderService,
    compare_outputs,
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
        """Árbol de lectura: vacío o `local` = working tree; si no, el commit (caché por SHA)."""
        root = resolved_root()
        if not ref or is_local(ref):
            yield root
            return
        try:
            spec = resolve_ref(root, ref)
        except RefError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        with tree_for(root, spec) as tree:
            yield tree

    def service(root: Path) -> RenderService:
        return RenderService(root, git_root=resolved_root())

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
                payload = svc.target_diff(instance_id=instance, ref=None if is_local(target_ref) else (target_ref or None))
            except (RenderError, RefError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            payload["edit"] = can_edit() and (not ref or is_local(ref))
            return payload

    @app.get("/api/renders/compare")
    def renders_compare(
        base: str = Query(...),
        head: str = Query(...),
        instance: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Salida renderizada y templates en base vs head (cualquiera puede ser `local`). Solo lectura."""
        root = resolved_root()
        try:
            base_spec, head_spec = resolve_ref(root, base), resolve_ref(root, head)
            with tree_for(root, base_spec) as base_tree, tree_for(root, head_spec) as head_tree:
                payload = compare_outputs(base_tree, head_tree, instance_id=instance or None)
            payload["templates"] = changed_files(root, base_spec, head_spec, [TEMPLATE_DIRNAME, RENDERS_FILE])
        except (RenderError, RefError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payload["base"], payload["head"] = base_spec.meta(), head_spec.meta()
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

    # ------------------------------------------- guardar la salida de la preview

    def _unique_instance_id(svc: RenderService, wanted: str) -> str:
        base = re.sub(r"[^a-z0-9._-]+", "-", wanted.lower()).strip("-._")[:56] or "render"
        taken = {i.id for i in svc.instances(enabled_only=False)}
        candidate, n = base, 2
        while candidate in taken:
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    def save_target(body: dict[str, Any], base: Path) -> tuple[RenderService, Instance, dict[str, Any]]:
        """Instancia a la que corresponde la preview (o la que habría que crear con esos parámetros)."""
        # La carpeta destino se lee en el mismo árbol (working tree o rama wizard/… materializada).
        svc = RenderService(base, git_root=base)
        template_id = str(body.get("template") or "")
        if not SAFE_ID.fullmatch(template_id):
            raise HTTPException(status_code=400, detail="template inválido")
        if find_template(base, template_id) is None:
            raise HTTPException(status_code=400, detail=f"el template {template_id} no está guardado: guárdalo (o clónalo) antes de guardar su salida")
        site = str(body.get("site") or "")
        if not site:
            raise HTTPException(status_code=400, detail="falta site")
        cluster = str(body.get("cluster") or "") or None
        params = body.get("params") or {}
        if not isinstance(params, dict):
            raise HTTPException(status_code=400, detail="params debe ser un mapa")
        instance_id = str(body.get("instance") or "") or None
        inst = svc.instance(instance_id) if instance_id else None
        reason: str | None = None
        if instance_id and inst is None:
            reason = f"la instancia {instance_id} no existe en {RENDERS_FILE}"
        if inst is None and not instance_id:
            same = [
                i
                for i in svc.instances(enabled_only=False)
                if i.template == template_id and i.site == site and (i.cluster or None) == cluster and {**i.params, **params} == i.params
            ]
            inst = same[0] if same else None
        if inst is not None:
            if inst.template != template_id or inst.site != site or (inst.cluster or None) != cluster:
                reason = f"la instancia {inst.id} es de otro template, sitio o clúster"
            elif {**inst.params, **params} != inst.params:
                reason = f"los params de la preview difieren de los guardados en la instancia {inst.id}"
            elif not inst.enabled:
                reason = f"la instancia {inst.id} está deshabilitada (enabled: false)"
        elif reason is None:
            reason = f"no hay ninguna instancia de {template_id} para {site}{'/' + cluster if cluster else ''} en {RENDERS_FILE}"
        info: dict[str, Any] = {"template": template_id, "site": site, "cluster": cluster, "needs_instance": reason is not None, "reason": reason}
        if reason is None:
            info.update(instance=inst.id, new_instance=None)  # type: ignore[union-attr]
            return svc, inst, info  # type: ignore[return-value]
        base_params = dict(inst.params) if inst is not None and inst.template == template_id else {}
        merged = {**base_params, **params}
        wanted = str(body.get("new_instance_id") or "").strip()
        if wanted:
            if not SAFE_ID.fullmatch(wanted):
                raise HTTPException(status_code=400, detail=f"id de instancia inválido: {wanted}")
            if svc.instance(wanted) is not None:
                raise HTTPException(status_code=400, detail=f"ya existe la instancia {wanted}")
            new_id = wanted
        else:
            new_id = _unique_instance_id(svc, f"{site}-{cluster or 'site'}-{template_id}")
        target = Instance(id=new_id, template=template_id, site=site, cluster=cluster, params=merged)
        svc.config.instances.append(target)
        proposal: dict[str, Any] = {"id": new_id, "template": template_id, "site": site}
        if cluster:
            proposal["cluster"] = cluster
        if merged:
            proposal["params"] = merged
        info.update(instance=None, new_instance=proposal)
        return svc, target, info

    def _save_entry(svc: RenderService, target: Instance, base: Path) -> dict[str, Any]:
        diff = svc.target_diff(instance_id=target.id)
        entry = diff["instances"][0] if diff["instances"] else {"errors": [], "files": [], "dir": None}
        folder = entry.get("dir")
        if entry.get("target_error") or not folder:
            state = "error"
        elif entry.get("errors"):
            state = "error"
        elif not (base / folder).is_dir():
            state = "no_existe"
        else:
            state = "al_dia" if entry.get("in_sync") else "desfasado"
        return {"entry": entry, "dir": folder, "state": state}

    @app.post("/api/renders/save/status")
    def renders_save_status(body: dict[str, Any]) -> dict[str, Any]:
        """¿Dónde se guardaría la salida de la preview y cómo está hoy en el repo? (solo lectura)."""
        with render_root(_branch(body) or body.get("ref") or None) as base:
            svc, target, info = save_target(body, base)
            found = _save_entry(svc, target, base)
        entry = found["entry"]
        return {
            **info,
            "dir": found["dir"],
            "state": found["state"],
            "counts": entry.get("counts"),
            "errors": entry.get("errors") or [],
            "target_error": entry.get("target_error"),
            "edit": can_edit(),
        }

    def save_plan(body: dict[str, Any], base: Path, root: Path) -> dict[str, Any]:
        svc, target, info = save_target(body, base)
        if info["needs_instance"] and not body.get("create_instance"):
            raise HTTPException(status_code=400, detail=f"{info['reason']}: crea una instancia con estos parámetros")
        branch = _branch(body)
        source = resolve_ref(root, branch).sha if branch else head_sha(root)
        try:
            plan = svc.apply_plan([target.id], prune=bool(body.get("prune")), source_commit=source)
        except RenderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        found = _save_entry(svc, target, base)
        entry = found["entry"]
        if plan["skipped"] or not found["dir"]:
            problems = [e.get("message", str(e)) if isinstance(e, dict) else str(e) for e in entry.get("errors") or []]
            raise HTTPException(status_code=400, detail="no se puede guardar: " + "; ".join([entry.get("target_error") or "", *problems]).strip("; "))
        folder = found["dir"]
        writes = dict(plan["writes"])
        extra: list[dict[str, Any]] = []
        if info["new_instance"]:
            cfg = config_plan({"upsert_instance": info["new_instance"]}, base)
            if cfg["errors"]:
                raise HTTPException(status_code=400, detail=f"{RENDERS_FILE} inválido: " + "; ".join(cfg["errors"][:5]))
            writes[RENDERS_FILE] = cfg["text"]
            extra = file_diffs(base, {RENDERS_FILE: cfg["text"]})
        sidecar = [d for d in file_diffs(base, {k: v for k, v in writes.items() if k.endswith("/.isot-render.json")})]
        for rel in [*writes, *plan["deletes"]]:
            if rel != RENDERS_FILE and not rel.startswith(folder + "/"):
                raise HTTPException(status_code=400, detail=f"ruta fuera del destino: {rel}")
        files = [
            {
                "path": item["target_path"],
                "status": item["status"],
                "diff": item.get("diff") or "",
                "action": "write" if item["status"] in {"new", "modified"} else ("delete" if item["status"] == "orphaned" and body.get("prune") else "keep"),
            }
            for item in entry.get("files") or []
        ]
        return {
            **info,
            "instance": target.id,
            "dir": folder,
            "state": found["state"],
            "files": files,
            "extra": [{"path": d["path"], "status": d["status"], "diff": d["diff"]} for d in [*extra, *sidecar] if d["status"] != "unchanged"],
            "writes": writes,
            "deletes": plan["deletes"],
            "changes": len(writes) + len(plan["deletes"]),
            "branch": branch,
        }

    @app.post("/api/renders/save/preview")
    def renders_save_preview(body: dict[str, Any]) -> dict[str, Any]:
        """Lo que escribiría «Guardar en el repo» (destino, estado por archivo y diffs), sin escribir."""
        require_edit()
        root = resolved_root()
        with render_root(_branch(body)) as base:
            plan = save_plan(body, base, root)
        plan.pop("writes")
        plan.pop("deletes")
        return plan

    @app.post("/api/renders/save/commit")
    def renders_save_commit(body: dict[str, Any]) -> dict[str, Any]:
        """Guarda la salida en la carpeta destino (mismo repo) en una rama wizard/…; índice en el mismo commit."""
        require_edit()
        root = resolved_root()
        with render_root(_branch(body)) as base:
            plan = save_plan(body, base, root)
        if not plan["writes"] and not plan["deletes"]:
            raise HTTPException(status_code=400, detail="la carpeta destino ya está al día")
        message = " ".join(str(body.get("message") or "").split())[:200] or f"renders: guardar {plan['instance']}"
        try:
            from isot.renders import commit_files

            result = commit_files(
                root,
                plan["writes"],
                plan["deletes"],
                message,
                branch=_branch(body),
                branch_label=f"render {plan['instance']}",
            )
        except WizardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        clear_caches(root)
        out = maybe_push(result) if result.get("created") else result
        return {**out, "instance": plan["instance"], "dir": plan["dir"], "created_instance": bool(plan["new_instance"])}


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
