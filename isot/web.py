"""API HTTP sobre el inventario ISoT multi-sitio.

Los listados y el grafo salen de los índices. El YAML se parsea solo
cuando se pide el detalle de un dispositivo, clúster o template.
Los values de un chart se leen bajo demanda (values_ref / values_inline).
Los POST /api/wizard/* solo existen si ISOT_EDIT=1 (o create_app(edit=True)).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from isot.gitdiff import RefError, clear_caches, list_commits, list_refs, resolve_sha, semantic_diff, snapshot_ref, validate_ref
from isot.helm import (
    chart_meta,
    charts_from_doc,
    cluster_values_diff,
    load_chart_values,
    strip_charts_payload,
)
from isot.indexer import index_path
from isot.links import diff_details, link_details, overlay_files, view_from_snapshot, view_from_tree
from isot.inventory import (
    CLUSTER_INDEX_NAME,
    DEVICE_INDEX_NAME,
    GLOBAL_SITE,
    TEMPLATE_INDEX_NAME,
    find_global_template,
    find_template_file,
    is_library_rel,
    library_roots,
    library_template_files,
    load_catalog,
    load_docs,
    referenced_library_templates,
    rel_path,
    load_yaml,
    resolve_template_path,
    site_by_id,
)
from isot import config as isot_config
from isot.indexer import build_template_index
from isot.cabling import default_policy, policy_from_site_body
from isot.wizard import (
    WizardError,
    commit_preview,
    device_preview,
    edit_enabled,
    list_site_templates as wizard_site_templates,
    push_branch,
    site_preview,
)

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_NODE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


def _guard_wizard_findings(preview: dict[str, Any], body: dict[str, Any]) -> None:
    findings = preview.get("findings") or {}
    added = findings.get("added") or []
    errors = [item for item in added if item.get("severity") == "error"]
    if errors:
        raise WizardError(
            "reglas: " + "; ".join(f"{item.get('id')} {item.get('message')}" for item in errors[:6])
        )
    warnings = [item for item in added if item.get("severity") == "warning"]
    if warnings and not body.get("accept_warnings"):
        raise WizardError("hay avisos nuevos: marca el checkbox para confirmar")


def _json_file(path: Path) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _load_list_index(path: Path, label: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"No existe {label}. Ejecuta: python -m isot.indexer",
        )
    data = _json_file(path)
    if not isinstance(data, list):
        raise HTTPException(status_code=500, detail=f"{label} no es una lista")
    return data


def load_sites_index(root: Path) -> list[dict[str, Any]]:
    return _load_list_index(index_path(root), "sites/_sites_index.json")


def load_device_index(root: Path, site_id: str) -> list[dict[str, Any]]:
    sites, _ = load_catalog(root)
    site = site_by_id(sites, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail=f"Sitio no encontrado: {site_id}")
    return _load_list_index(
        site.devices_dir / DEVICE_INDEX_NAME,
        f"{site.rel_path}/devices/{DEVICE_INDEX_NAME}",
    )


def load_cluster_index(root: Path, site_id: str) -> list[dict[str, Any]]:
    sites, _ = load_catalog(root)
    site = site_by_id(sites, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail=f"Sitio no encontrado: {site_id}")
    path = site.clusters_dir / CLUSTER_INDEX_NAME
    if not path.is_file():
        return []
    return _load_list_index(path, f"{site.rel_path}/clusters/{CLUSTER_INDEX_NAME}")


def load_site_template_index(root: Path, site_id: str) -> list[dict[str, Any]]:
    sites, _ = load_catalog(root)
    site = site_by_id(sites, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail=f"Sitio no encontrado: {site_id}")
    path = site.templates_dir / TEMPLATE_INDEX_NAME
    if not path.is_file():
        return []
    return _load_list_index(path, f"{site.rel_path}/cluster-templates/{TEMPLATE_INDEX_NAME}")


def load_global_template_index(root: Path) -> list[dict[str, Any]]:
    """Índice global del repo de datos + templates de la biblioteca que algún
    clúster referencia (se calculan al vuelo; no se escriben en el repo)."""
    path = root / "cluster-templates" / TEMPLATE_INDEX_NAME
    entries = _load_list_index(path, f"cluster-templates/{TEMPLATE_INDEX_NAME}") if path.is_file() else []
    return entries + library_template_index(root)


def library_template_index(root: Path) -> list[dict[str, Any]]:
    sites, _ = load_catalog(root)
    docs = referenced_library_templates(root, sites)
    if not docs:
        return []
    from isot.indexer import _used_by_map

    entries = build_template_index(root, docs, _used_by_map(root, sites))
    layers = {str(doc.get("_path")): doc.get("_library") for doc in docs}
    for entry in entries:
        entry["library"] = layers.get(str(entry.get("path")))
    return entries


def library_catalog(root: Path | None) -> list[dict[str, Any]]:
    """Templates de la biblioteca disponibles para el wizard (aunque nadie los use)."""
    present = set()
    if root is not None:
        for path in (root / "cluster-templates").glob("*"):
            present.add(path.stem if path.is_file() else path.name)
    out: list[dict[str, Any]] = []
    for prefix, path in library_template_files():
        docs = load_docs(path.parent, path.parent, GLOBAL_SITE, files=[path])
        doc = docs[0] if docs else {}
        name = str(doc.get("template_name") or doc.get("_stem"))
        out.append(
            {
                "name": name,
                "site": GLOBAL_SITE,
                "scope": "global",
                "library": prefix,
                "distro": doc.get("distro"),
                "version": doc.get("version") or None,
                "path": f"{prefix}/{name}",
                "in_repo": name in present,
            }
        )
    return out


def load_all_templates(root: Path) -> list[dict[str, Any]]:
    entries = list(load_global_template_index(root))
    for site in load_sites_index(root):
        site_id = site.get("id")
        if not site_id:
            continue
        entries.extend(load_site_template_index(root, str(site_id)))
    return entries


def load_all_devices(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for site in load_sites_index(root):
        site_id = site.get("id")
        if site_id:
            entries.extend(load_device_index(root, str(site_id)))
    return entries


def load_all_clusters(root: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for site in load_sites_index(root):
        site_id = site.get("id")
        if site_id:
            entries.extend(load_cluster_index(root, str(site_id)))
    return entries


def graph_from_index(entries: list[dict[str, Any]], view: str = "network") -> dict[str, Any]:
    nodes = [
        {
            "id": f"{entry.get('site')}:{entry.get('hostname')}",
            "hostname": entry.get("hostname"),
            "site": entry.get("site"),
            "role": entry.get("role"),
            "ip": entry.get("ip"),
            "kind": entry.get("kind"),
            "cluster_ref": entry.get("cluster_ref"),
            "leaf_pair": entry.get("leaf_pair"),
            "path": entry.get("path"),
            "entity": "device",
        }
        for entry in entries
        if entry.get("hostname")
    ]
    host_ids = {node["id"] for node in nodes}
    known = {(entry.get("site"), entry.get("hostname")) for entry in entries}
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        src_host = entry.get("hostname")
        src_site = entry.get("site")
        if not src_host:
            continue
        src_id = f"{src_site}:{src_host}"
        for link in entry.get("links") or []:
            if not isinstance(link, dict):
                continue
            dst = link.get("device")
            dst_site = link.get("site") or src_site
            if not dst:
                continue
            dst_id = f"{dst_site}:{dst}"
            if (dst_site, dst) not in known:
                if dst_id not in host_ids:
                    nodes.append(
                        {
                            "id": dst_id,
                            "hostname": dst,
                            "site": dst_site,
                            "role": "remote",
                            "ip": None,
                            "kind": "remote",
                            "cluster_ref": None,
                            "path": None,
                            "entity": "device",
                            "remote": True,
                        }
                    )
                    host_ids.add(dst_id)
            a, b = sorted((src_id, dst_id))
            bucket = buckets.setdefault(
                (a, b),
                {
                    "id": f"{a}--{b}",
                    "source": src_id,
                    "target": dst_id,
                    "vlans": [],
                    "ports": [],
                },
            )
            vlan = link.get("vlan")
            if vlan is not None and vlan not in bucket["vlans"]:
                bucket["vlans"].append(vlan)
            port = link.get("port")
            if port and port not in bucket["ports"]:
                bucket["ports"].append(port)
    return {"nodes": nodes, "edges": list(buckets.values()), "view": view}


def kubernetes_graph(
    devices: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    templates: list[dict[str, Any]],
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for template in templates:
        name = template.get("name")
        if not name:
            continue
        site = template.get("site") or "global"
        nodes.append(
            {
                "id": f"template:{site}:{name}",
                "hostname": name,
                "site": site,
                "role": "cluster-template",
                "ip": template.get("version"),
                "kind": "template",
                "cluster_ref": None,
                "path": template.get("path"),
                "entity": "template",
            }
        )
    k8s = [c for c in clusters if c.get("kind") == "kubernetes"]
    for cluster in k8s:
        name = cluster.get("name")
        if not name:
            continue
        site = cluster.get("site")
        cluster_id = f"cluster:{site}:{name}"
        nodes.append(
            {
                "id": cluster_id,
                "hostname": name,
                "site": site,
                "role": cluster.get("distro"),
                "ip": cluster.get("version"),
                "kind": "cluster",
                "cluster_ref": name,
                "path": cluster.get("path"),
                "entity": "cluster",
                "template_ref": cluster.get("template_ref"),
            }
        )
        ref = cluster.get("template_ref")
        if ref:
            t_site = "global"
            for template in templates:
                if template.get("name") == ref:
                    t_site = template.get("site") or "global"
                    if t_site == "global":
                        break
            edges.append(
                {
                    "id": f"template:{t_site}:{ref}--{cluster_id}",
                    "source": f"template:{t_site}:{ref}",
                    "target": cluster_id,
                    "vlans": [],
                    "ports": ["template_ref"],
                }
            )
        for device in devices:
            if device.get("cluster_ref") != name or device.get("site") != site:
                continue
            hostname = device.get("hostname")
            device_id = f"{site}:{hostname}"
            nodes.append(
                {
                    "id": device_id,
                    "hostname": hostname,
                    "site": site,
                    "role": device.get("role"),
                    "ip": device.get("ip"),
                    "kind": device.get("kind"),
                    "cluster_ref": name,
                    "leaf_pair": device.get("leaf_pair"),
                    "path": device.get("path"),
                    "entity": "device",
                }
            )
            edges.append(
                {
                    "id": f"{cluster_id}--{device_id}",
                    "source": cluster_id,
                    "target": device_id,
                    "vlans": [],
                    "ports": ["cluster_ref"],
                }
            )
    return {"nodes": nodes, "edges": edges, "view": "kubernetes"}


def global_sites_graph(sites: list[dict[str, Any]]) -> dict[str, Any]:
    nodes = [
        {
            "id": f"site:{item.get('id')}",
            "hostname": item.get("name") or item.get("id"),
            "site": item.get("id"),
            "role": "site",
            "ip": f"{item.get('device_count', 0)} dispositivos",
            "kind": "site",
            "cluster_ref": None,
            "path": item.get("path"),
            "entity": "site",
            "description": item.get("description"),
        }
        for item in sites
        if item.get("id")
    ]
    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for item in sites:
        src = item.get("id")
        if not src:
            continue
        for link in item.get("inter_site_links") or []:
            dst = link.get("peer_site")
            if not dst:
                continue
            a, b = sorted((str(src), str(dst)))
            bucket = buckets.setdefault(
                (a, b),
                {
                    "id": f"site:{a}--site:{b}",
                    "source": f"site:{a}",
                    "target": f"site:{b}",
                    "vlans": [],
                    "ports": [],
                },
            )
            vlan = link.get("vlan")
            if vlan is not None and vlan not in bucket["vlans"]:
                bucket["vlans"].append(vlan)
            label = f"{link.get('device')}→{link.get('peer_device')}"
            if label not in bucket["ports"]:
                bucket["ports"].append(label)
    return {"nodes": nodes, "edges": list(buckets.values()), "view": "global"}


def _chart_values_payload(template_path: Path, release: str) -> dict[str, Any]:
    data = load_yaml(template_path)
    if not isinstance(data, dict):
        data = {}
    charts = charts_from_doc(data)
    chart = next((item for item in charts if item["name"] == release), None)
    if chart is None:
        raise HTTPException(status_code=404, detail=f"Release no definido en el template: {release}")
    values, source, rel, error = load_chart_values(template_path, chart)
    if error and source == "ref":
        raise HTTPException(status_code=404, detail=error)
    return {
        "release": release,
        "chart": chart["chart"],
        "version": chart["version"] or None,
        "source": source,
        "path": rel,
        "values": values,
        "missing": source == "missing",
        "error": error,
    }


class DataRepoState:
    """Repositorio de datos activo. Se vuelve a resolver mientras no haya uno
    (así el onboarding detecta un isot.yaml recién creado sin reiniciar)."""

    def __init__(self, repo_root: Path | None, config_path: str | Path | None) -> None:
        self.fixed = repo_root.resolve() if repo_root is not None else None
        self.config_path = config_path
        self.resolved: isot_config.Resolved | None = None
        self.error: str | None = None
        self.missing = False
        self.refresh()

    def refresh(self) -> None:
        if self.fixed is not None:
            config = isot_config.Config(source=None)
            try:
                config = isot_config.load_config(self.config_path) or config
            except isot_config.ConfigError:
                pass
            self.resolved = isot_config.Resolved(self.fixed, "create_app", config)
            return
        try:
            self.resolved = isot_config.resolve(config_path=self.config_path)
            self.error = None
            self.missing = False
            isot_config.apply(self.resolved.config)
        except isot_config.ConfigMissing as exc:
            self.resolved, self.error, self.missing = None, str(exc), True
        except isot_config.ConfigError as exc:
            self.resolved, self.error, self.missing = None, str(exc), False

    def current(self) -> isot_config.Resolved | None:
        if self.resolved is None:
            self.refresh()
        return self.resolved


def create_app(
    repo_root: Path | None = None,
    edit: bool | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    state = DataRepoState(repo_root, config_path or os.environ.get("ISOT_CONFIG") or None)
    config_edit = bool(state.resolved and state.resolved.config.edit.enabled)
    can_edit = edit_enabled(edit) or (edit is None and config_edit)
    app = FastAPI(
        title="ISoT",
        description="Infrastructure Source of Truth — API de inventario (multi-sitio)",
        version="0.7.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    def resolved_root() -> Path:
        current = state.current()
        if current is None:
            raise HTTPException(status_code=503, detail=state.error or isot_config.NO_CONFIG_MESSAGE)
        return current.repo

    def safe_under_root(path: Path, base: Path, *, library: bool = False) -> Path:
        resolved = path.resolve()
        if resolved.is_relative_to(base.resolve()):
            return resolved
        if library and any(resolved.is_relative_to(root.resolve()) for root in library_roots()):
            return resolved
        raise HTTPException(status_code=400, detail="Ruta fuera del repositorio")

    def require_site(site_id: str):
        if not SAFE_NAME.fullmatch(site_id):
            raise HTTPException(status_code=400, detail="id de sitio inválido")
        base = resolved_root()
        sites, _ = load_catalog(base)
        site = site_by_id(sites, site_id)
        if site is None:
            raise HTTPException(status_code=404, detail=f"Sitio no encontrado: {site_id}")
        return base, site

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        current = state.current()
        return {
            "status": "ok",
            "configured": current is not None,
            "data_repo": current.repo.name if current else None,
        }

    @app.get("/api/config")
    def config_info() -> dict[str, Any]:
        """Repositorio de datos activo (sin secretos: ISoT no maneja credenciales)."""
        current = state.current()
        if current is None:
            return {
                "configured": False,
                "error": None if state.missing else state.error,
                "message": isot_config.NO_CONFIG_MESSAGE,
                "hints": isot_config.HINTS,
                "search": ["--config", "$ISOT_CONFIG", "./isot.yaml", "~/.config/isot/config.yaml"],
                "edit": can_edit,
            }
        cfg = current.config
        return {
            "configured": True,
            "origin": current.origin,
            "config_file": str(cfg.source) if cfg.source else None,
            "data_repo": isot_config.repo_status(current.repo, cfg),
            # Preparado para varios repos (selector futuro): lista y repo activo.
            "repos": [
                {"id": item.id, "name": item.name or item.path.name, "active": item.path == current.repo}
                for item in cfg.repos
            ],
            "active_repo": current.repo_id,
            "templates": [str(path) for path in cfg.templates],
            "edit": can_edit,
            "push": bool(can_edit and cfg.edit.push),
            "push_remote": cfg.edit.push_remote,
            "ui": {"title": cfg.ui.title},
        }

    @app.get("/api/rules")
    def list_rules() -> list[dict[str, str]]:
        from isot.rules.evaluate import catalog

        return catalog()

    @app.get("/api/findings")
    def list_findings(
        site: str | None = Query(default=None),
        ref: str | None = Query(default=None),
        base: str | None = Query(default=None),
        head: str | None = Query(default=None),
    ) -> dict[str, Any]:
        from isot.rules.evaluate import (
            counts_by_site,
            diff_findings,
            evaluate,
            evaluate_ref,
            finding_counts,
            payload as findings_payload,
        )

        site_id = site.strip().lower() if site else None
        if site_id == "all":
            site_id = None
        try:
            if base and head:
                base_findings = evaluate_ref(resolved_root(), base, site=site_id)
                head_findings = evaluate_ref(resolved_root(), head, site=site_id)
                added, resolved = diff_findings(base_findings, head_findings)
                known = sorted(set(base_findings.site_ids) | set(head_findings.site_ids))
                scope_ids = [site_id] if site_id else known
                return {
                    "scope": site_id or "all",
                    "base": findings_payload(base_findings, site=site_id),
                    "head": findings_payload(head_findings, site=site_id),
                    "added": [item.to_dict() for item in added],
                    "resolved": [item.to_dict() for item in resolved],
                    "added_counts": finding_counts(added),
                    "resolved_counts": finding_counts(resolved),
                    "added_by_site": counts_by_site(added, scope_ids),
                    "resolved_by_site": counts_by_site(resolved, scope_ids),
                }
            if ref:
                findings = evaluate_ref(resolved_root(), ref, site=site_id)
            else:
                findings = evaluate(resolved_root(), site=site_id)
            return findings_payload(findings, site=site_id)
        except RefError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/wizard/status")
    def wizard_status() -> dict[str, Any]:
        current = state.current()
        return {
            "edit": can_edit,
            "push": bool(can_edit and current and current.config.edit.push),
            "site_templates": wizard_site_templates(current.repo if current else None),
            "template_library": library_catalog(current.repo if current else None),
        }

    def maybe_push(result: dict[str, Any]) -> dict[str, Any]:
        current = state.current()
        if not current or not current.config.edit.push:
            return result
        result["push"] = push_branch(current.repo, result["branch"], current.config.edit.push_remote)
        return result

    def require_edit() -> None:
        if not can_edit:
            raise HTTPException(status_code=403, detail="edición deshabilitada (ISOT_EDIT=1 o --edit)")

    @app.get("/api/wizard/cabling-defaults")
    def wizard_cabling_defaults(
        architecture: str = Query(default="spine-leaf"),
        spines: int | None = Query(default=None),
        leaf_pairs: int | None = Query(default=None),
        unpaired_leafs: int | None = Query(default=None),
        mgmt_switches: int | None = Query(default=None),
        dcgw: int | None = Query(default=None),
        ports_per_leaf: int = Query(default=48),
        pair_policy: str = Query(default="fill-first"),
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "architecture": architecture,
            "ports_per_leaf": ports_per_leaf,
            "pair_policy": pair_policy,
        }
        if spines is not None:
            body["spines"] = spines
        if leaf_pairs is not None:
            body["leaf_pairs"] = leaf_pairs
        if unpaired_leafs is not None:
            body["unpaired_leafs"] = unpaired_leafs
        if mgmt_switches is not None:
            body["mgmt_switches"] = mgmt_switches
        if dcgw is not None:
            body["dcgw"] = dcgw
        try:
            return policy_from_site_body(body)
        except Exception:
            return default_policy(architecture)

    @app.post("/api/wizard/site/preview")
    def wizard_site_preview(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        try:
            return site_preview(resolved_root(), body)
        except WizardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/wizard/site/commit")
    def wizard_site_commit(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        try:
            preview = site_preview(resolved_root(), body)
            _guard_wizard_findings(preview, body)
            description = str(body.get("description") or body.get("id") or "nuevo-sitio")
            result = commit_preview(
                resolved_root(), preview, f"sitio {description}", site=preview.get("site") or body.get("id")
            )
            clear_caches()
            return {**maybe_push(result), "preview": preview}
        except WizardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/wizard/device/preview")
    def wizard_device_preview(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        try:
            return device_preview(resolved_root(), body)
        except (WizardError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/wizard/device/commit")
    def wizard_device_commit(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        try:
            preview = device_preview(resolved_root(), body)
            _guard_wizard_findings(preview, body)
            hostnames = ",".join(preview.get("hostnames") or []) or "device"
            result = commit_preview(
                resolved_root(), preview, f"device {hostnames}", site=preview.get("site") or body.get("site")
            )
            clear_caches()
            return {**maybe_push(result), "preview": preview}
        except (WizardError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


    @app.get("/api/refs")
    def git_refs(rev: str | None = Query(default=None)) -> dict[str, Any]:
        try:
            payload = list_refs(resolved_root())
            if rev:
                history = list_commits(resolved_root(), rev)
                payload["commits"] = history["commits"]
                payload["rev"] = {"ref": history["ref"], "sha": history["sha"], "short": history["short"]}
            return payload
        except RefError as exc:
            raise HTTPException(status_code=400 if rev else 503, detail=str(exc)) from exc

    @app.get("/api/commits")
    def git_commits(rev: str = Query(...), limit: int = Query(default=80)) -> dict[str, Any]:
        try:
            return list_commits(resolved_root(), rev, limit=limit)
        except RefError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/diff")
    def git_diff(
        base: str = Query(...),
        head: str = Query(...),
        site: str | None = Query(default=None),
    ) -> dict[str, Any]:
        site_id = site.strip() if site else None
        if site_id == "all":
            site_id = None
        try:
            return semantic_diff(resolved_root(), base, head, site_id)
        except RefError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/link")
    def link(
        source: str = Query(...),
        target: str = Query(...),
        base: str | None = Query(default=None),
        head: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Detalle de la arista source–target (ids del grafo). Con base/head, además el diff."""
        if not SAFE_NODE_ID.fullmatch(source) or not SAFE_NODE_ID.fullmatch(target):
            raise HTTPException(status_code=400, detail="id de nodo inválido")
        root = resolved_root()
        if base and head:
            try:
                base_sha = resolve_sha(root, validate_ref(base))
                head_sha = resolve_sha(root, validate_ref(head))
                before = link_details(view_from_snapshot(snapshot_ref(root, base_sha)), source, target)
                after = link_details(view_from_snapshot(snapshot_ref(root, head_sha)), source, target)
            except RefError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if before is None and after is None:
                raise HTTPException(status_code=404, detail=f"Conexión no encontrada en base ni head: {source} – {target}")
            return {
                "detail": after or before,
                "base": before,
                "head": after,
                "diff": diff_details(before, after),
                "refs": {"base": {"ref": base, "sha": base_sha}, "head": {"ref": head, "sha": head_sha}},
            }
        detail = link_details(view_from_tree(root), source, target)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"Conexión no encontrada: {source} – {target}")
        return {"detail": detail, "base": None, "head": None, "diff": None, "refs": None}

    @app.post("/api/link/preview")
    def link_preview(body: dict[str, Any]) -> dict[str, Any]:
        """Como GET /api/link, pero sobre los archivos propuestos por el wizard (en memoria)."""
        source, target = str(body.get("source") or ""), str(body.get("target") or "")
        if not SAFE_NODE_ID.fullmatch(source) or not SAFE_NODE_ID.fullmatch(target):
            raise HTTPException(status_code=400, detail="id de nodo inválido")
        files = body.get("files") or []
        if not isinstance(files, list):
            raise HTTPException(status_code=400, detail="files debe ser una lista")
        view = overlay_files(view_from_tree(resolved_root()), [item for item in files if isinstance(item, dict)])
        detail = link_details(view, source, target)
        if detail is None:
            raise HTTPException(status_code=404, detail=f"Conexión no encontrada: {source} – {target}")
        return {"detail": detail, "base": None, "head": None, "diff": None, "refs": None}

    @app.get("/api/sites")
    def list_sites() -> list[dict[str, Any]]:
        return load_sites_index(resolved_root())

    @app.get("/api/templates")
    def list_templates() -> list[dict[str, Any]]:
        return load_all_templates(resolved_root())

    @app.get("/api/graph")
    def graph(view: str = Query(default="global")) -> dict[str, Any]:
        base = resolved_root()
        if view == "kubernetes":
            return kubernetes_graph(
                load_all_devices(base),
                load_all_clusters(base),
                load_all_templates(base),
            )
        if view == "network":
            return graph_from_index(load_all_devices(base), "network")
        return global_sites_graph(load_sites_index(base))

    @app.get("/api/sites/{site_id}/devices")
    def list_devices(site_id: str) -> list[dict[str, Any]]:
        base, _ = require_site(site_id)
        return load_device_index(base, site_id)

    @app.get("/api/sites/{site_id}/clusters")
    def list_clusters(site_id: str) -> list[dict[str, Any]]:
        base, _ = require_site(site_id)
        return load_cluster_index(base, site_id)

    @app.get("/api/sites/{site_id}/templates")
    def list_site_templates(site_id: str) -> list[dict[str, Any]]:
        base, _ = require_site(site_id)
        global_ones = load_global_template_index(base)
        local_ones = load_site_template_index(base, site_id)
        return global_ones + local_ones

    @app.get("/api/sites/{site_id}/topology")
    def topology(site_id: str) -> Any:
        base, site = require_site(site_id)
        path = safe_under_root(site.topology_path, base)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="No existe topology/index.yaml")
        return load_yaml(path)

    @app.get("/api/sites/{site_id}/graph")
    def site_graph(site_id: str, view: str = Query(default="network")) -> dict[str, Any]:
        base, _ = require_site(site_id)
        devices = load_device_index(base, site_id)
        if view == "kubernetes":
            templates = load_global_template_index(base) + load_site_template_index(base, site_id)
            return kubernetes_graph(devices, load_cluster_index(base, site_id), templates)
        return graph_from_index(devices, "network")

    @app.get("/api/sites/{site_id}/devices/{hostname}")
    def device_detail(site_id: str, hostname: str) -> dict[str, Any]:
        if not SAFE_NAME.fullmatch(hostname):
            raise HTTPException(status_code=400, detail="hostname inválido")
        base, _ = require_site(site_id)
        entry = next(
            (item for item in load_device_index(base, site_id) if item.get("hostname") == hostname),
            None,
        )
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Dispositivo no encontrado: {site_id}/{hostname}")
        rel = entry.get("path")
        if not rel:
            raise HTTPException(status_code=404, detail="Entrada de índice sin path")
        path = safe_under_root(base / str(rel), base)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"No existe {rel}")
        return {"index": entry, "data": load_yaml(path)}

    @app.get("/api/sites/{site_id}/clusters/{name}")
    def cluster_detail(site_id: str, name: str) -> dict[str, Any]:
        if not SAFE_NAME.fullmatch(name):
            raise HTTPException(status_code=400, detail="nombre de clúster inválido")
        base, site = require_site(site_id)
        rel = f"{site.rel_path}/clusters/{name}.yaml"
        path = safe_under_root(base / rel, base)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"No existe {rel}")
        members = [
            item.get("hostname")
            for item in load_device_index(base, site_id)
            if item.get("cluster_ref") == name
        ]
        data = load_yaml(path)
        template_ref = data.get("template_ref") if isinstance(data, dict) else None
        template_site = None
        template_path = None
        if template_ref:
            found = resolve_template_path(base, load_catalog(base)[0], site_id, str(template_ref))
            if found is not None:
                template_path = rel_path(found, base)
                template_site = (
                    GLOBAL_SITE
                    if template_path.startswith("cluster-templates/") or is_library_rel(template_path)
                    else template_path.split("/", 2)[1]
                )
        return {
            "name": name,
            "site": site_id,
            "path": rel,
            "data": data,
            "members": members,
            "template_site": template_site,
            "template_path": template_path,
        }

    def _template_payload(base: Path, path: Path, site_id: str, name: str) -> dict[str, Any]:
        rel = rel_path(path, base)
        data = load_yaml(path)
        if not isinstance(data, dict):
            data = {"_raw": data}
        used_by = [
            f"{item.get('site')}/{item.get('name')}"
            for item in load_all_clusters(base)
            if item.get("template_ref") == name
        ]
        if site_id != GLOBAL_SITE:
            used_by = [item for item in used_by if item.startswith(f"{site_id}/")]
        charts = [chart_meta(item) for item in charts_from_doc(data)]
        return {
            "name": name,
            "site": site_id,
            "path": rel,
            "data": strip_charts_payload(data),
            "charts": charts,
            "used_by": used_by,
        }

    def _locate_site_template(base: Path, site, name: str) -> Path:
        located = find_template_file(site.templates_dir, name)
        if located is None:
            raise HTTPException(
                status_code=404,
                detail=f"No existe template {name} en {site.rel_path}/cluster-templates/",
            )
        return safe_under_root(located, base)

    def _locate_global_template(base: Path, name: str) -> Path:
        located = find_global_template(base, name)
        if located is None:
            raise HTTPException(status_code=404, detail=f"No existe cluster-templates/{name}/")
        return safe_under_root(located, base, library=True)

    @app.get("/api/sites/{site_id}/templates/{name}/values/{release}")
    def site_template_values(site_id: str, name: str, release: str) -> dict[str, Any]:
        if not SAFE_NAME.fullmatch(name) or not SAFE_NAME.fullmatch(release):
            raise HTTPException(status_code=400, detail="nombre inválido")
        base, site = require_site(site_id)
        path = _locate_site_template(base, site, name)
        return _chart_values_payload(path, release)

    @app.get("/api/sites/{site_id}/templates/{name}")
    def site_template_detail(site_id: str, name: str) -> dict[str, Any]:
        if not SAFE_NAME.fullmatch(name):
            raise HTTPException(status_code=400, detail="nombre de template inválido")
        base, site = require_site(site_id)
        path = _locate_site_template(base, site, name)
        return _template_payload(base, path, site_id, name)

    @app.get("/api/templates/{name}/values/{release}")
    def template_values(name: str, release: str) -> dict[str, Any]:
        if not SAFE_NAME.fullmatch(name) or not SAFE_NAME.fullmatch(release):
            raise HTTPException(status_code=400, detail="nombre inválido")
        base = resolved_root()
        path = _locate_global_template(base, name)
        return _chart_values_payload(path, release)

    @app.get("/api/templates/{name}")
    def template_detail(name: str) -> dict[str, Any]:
        if not SAFE_NAME.fullmatch(name):
            raise HTTPException(status_code=400, detail="nombre de template inválido")
        base = resolved_root()
        path = _locate_global_template(base, name)
        return _template_payload(base, path, GLOBAL_SITE, name)

    @app.get("/api/sites/{site_id}/clusters/{name}/values-diff")
    def cluster_values_diff_endpoint(site_id: str, name: str) -> dict[str, Any]:
        if not SAFE_NAME.fullmatch(name):
            raise HTTPException(status_code=400, detail="nombre de clúster inválido")
        base, site = require_site(site_id)
        rel = f"{site.rel_path}/clusters/{name}.yaml"
        path = safe_under_root(base / rel, base)
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"No existe {rel}")
        cluster = load_yaml(path)
        if not isinstance(cluster, dict):
            cluster = {"_raw": cluster}
        cluster = dict(cluster)
        cluster["_site"] = site_id
        cluster["_stem"] = name
        cluster["_path"] = rel
        template_ref = cluster.get("template_ref")
        if not template_ref:
            raise HTTPException(
                status_code=404,
                detail=f"El clúster {site_id}/{name} no declara template_ref",
            )
        sites, _ = load_catalog(base)
        found = resolve_template_path(base, sites, site_id, str(template_ref))
        if found is None:
            raise HTTPException(status_code=404, detail=f"template_ref={template_ref!r} no existe")
        found = safe_under_root(found, base, library=True)
        tdoc = load_yaml(found)
        if not isinstance(tdoc, dict):
            tdoc = {}
        return cluster_values_diff(base, cluster, found, tdoc)

    if FRONTEND_DIST.is_dir():
        assets = FRONTEND_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        def spa_index() -> FileResponse:
            return FileResponse(
                FRONTEND_DIST / "index.html",
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/")
        def spa_root() -> FileResponse:
            return spa_index()

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Recurso no encontrado")
            candidate = FRONTEND_DIST / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            index = FRONTEND_DIST / "index.html"
            if index.is_file():
                return spa_index()
            raise HTTPException(status_code=404, detail="Frontend no construido")

    return app


app = create_app()


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(description="Sirve la API y la UI de ISoT sobre un repositorio de datos.")
    parser.add_argument("--config", type=Path, default=None, help="Archivo de configuración isot.yaml.")
    parser.add_argument("--repo", type=Path, default=None, help="Repositorio de datos (ignora data_repo.path).")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--edit", action="store_true", help="Habilita los wizards (equivale a edit.enabled).")
    return parser


def main(argv: list[str] | None = None) -> int:
    import sys

    import uvicorn

    args = build_parser().parse_args(argv)
    if args.config:
        os.environ["ISOT_CONFIG"] = str(args.config.expanduser().resolve())
    if args.repo:
        os.environ["ISOT_ROOT"] = str(args.repo.expanduser().resolve())
    if args.edit:
        os.environ["ISOT_EDIT"] = "1"
    try:
        resolved = isot_config.resolve()
        print(f"ISoT: repositorio de datos {resolved.repo} ({resolved.origin})", file=sys.stderr)
    except isot_config.ConfigMissing:
        print(isot_config.missing_message(), file=sys.stderr)
        print("\nLa UI mostrará estas instrucciones hasta que exista la configuración.", file=sys.stderr)
    except isot_config.ConfigError as exc:
        print(f"error de configuración: {exc}", file=sys.stderr)
        print("La UI mostrará el error hasta que se corrija.", file=sys.stderr)
    # La app del módulo se creó al importar; se recrea con las opciones ya aplicadas.
    global app
    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
