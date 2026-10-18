"""API HTTP sobre el inventario ISoT multi-sitio.

Los listados y el grafo salen de los índices. El YAML se parsea solo
cuando se pide el detalle de un dispositivo, clúster o template.
Los values de un chart se leen bajo demanda (values_ref / values_inline).
Los POST /api/wizard/* solo existen si ISOT_EDIT=1 (o create_app(edit=True)).
La sección Renders (/api/renders/...) está en isot.web_renders.
"""

from __future__ import annotations

import contextvars
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from isot.compare import changed_files, file_diff
from isot.indexer import ARTIFACTS_INDEX_NAME, build_artifacts_index, cached_status, live_indexes, pending_index_writes
from isot.gitdiff import RefError, clear_caches, list_commits, list_refs, semantic_diff, snapshot_for
from isot.refs import describe_ref, resolve_ref
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
    save_preview,
    device_preview,
    edit_enabled,
    list_site_templates as wizard_site_templates,
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


def _load_list_index(path: Path, label: str, root: Path | None = None) -> list[dict[str, Any]]:
    """Lee un índice escrito; si el índice del repo está desactualizado, usa el calculado
    en memoria (`indexer.live_indexes`): la web nunca sirve datos viejos."""
    if root is not None:
        overlay = live_indexes(root)
        if overlay is not None and str(path) in overlay:
            data = overlay[str(path)]
            return data if isinstance(data, list) else []
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
    return _load_list_index(index_path(root), "sites/_sites_index.json", root)


def load_device_index(root: Path, site_id: str) -> list[dict[str, Any]]:
    sites, _ = load_catalog(root)
    site = site_by_id(sites, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail=f"Sitio no encontrado: {site_id}")
    return _load_list_index(
        site.devices_dir / DEVICE_INDEX_NAME,
        f"{site.rel_path}/devices/{DEVICE_INDEX_NAME}",
        root,
    )


def load_cluster_index(root: Path, site_id: str) -> list[dict[str, Any]]:
    sites, _ = load_catalog(root)
    site = site_by_id(sites, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail=f"Sitio no encontrado: {site_id}")
    path = site.clusters_dir / CLUSTER_INDEX_NAME
    overlay = live_indexes(root)
    if not path.is_file() and (overlay is None or str(path) not in overlay):
        return []
    return _load_list_index(path, f"{site.rel_path}/clusters/{CLUSTER_INDEX_NAME}", root)


def load_site_template_index(root: Path, site_id: str) -> list[dict[str, Any]]:
    sites, _ = load_catalog(root)
    site = site_by_id(sites, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail=f"Sitio no encontrado: {site_id}")
    path = site.templates_dir / TEMPLATE_INDEX_NAME
    overlay = live_indexes(root)
    if not path.is_file() and (overlay is None or str(path) not in overlay):
        return []
    return _load_list_index(path, f"{site.rel_path}/cluster-templates/{TEMPLATE_INDEX_NAME}", root)


def load_global_template_index(root: Path) -> list[dict[str, Any]]:
    """Índice global del repo de datos + templates de la biblioteca que algún
    clúster referencia (se calculan al vuelo; no se escriben en el repo)."""
    path = root / "cluster-templates" / TEMPLATE_INDEX_NAME
    overlay = live_indexes(root)
    present = path.is_file() or (overlay is not None and str(path) in overlay)
    entries = _load_list_index(path, f"cluster-templates/{TEMPLATE_INDEX_NAME}", root) if present else []
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


_REQUEST_REPO: contextvars.ContextVar[str | None] = contextvars.ContextVar("isot_request_repo", default=None)
REPO_PREFIX = "/api/repos/"
STATUS_TTL = 3.0


class RepoPrefixMiddleware:
    """`/api/repos/{id}/<ruta>` → `/api/<ruta>` sobre el repositorio `id`.

    Es la forma canónica de toda API de datos; las rutas `/api/<ruta>` sin prefijo
    siguen funcionando contra el repositorio por defecto (compatibilidad).
    `/api/repos` y `/api/repos/{id}` (estado) no se reescriben.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and scope.get("path", "").startswith(REPO_PREFIX):
            repo_id, _, rest = scope["path"][len(REPO_PREFIX):].partition("/")
            if repo_id and rest:
                path = "/api/" + rest
                scope = dict(scope, path=path, raw_path=path.encode("utf-8"))
                token = _REQUEST_REPO.set(repo_id)
                try:
                    await self.app(scope, receive, send)
                finally:
                    _REQUEST_REPO.reset(token)
                return
        await self.app(scope, receive, send)


class RepoRegistry:
    """Repositorios de datos configurados, indexados por id.

    Cada repositorio se valida al pedirlo (un repo inválido queda «no disponible»
    con su error y no afecta a los demás). Mientras no hay configuración se
    vuelve a resolver en cada pedido (el onboarding detecta un isot.yaml nuevo),
    y si el archivo de configuración cambia se recarga.
    """

    def __init__(self, fixed: list[Path] | None, config_path: str | Path | None) -> None:
        self.fixed = [path.resolve() for path in fixed] if fixed else None
        self.config_path = config_path
        self.config = isot_config.Config(source=None)
        self.repos: list[isot_config.DataRepoConfig] = []
        self.default_id: str | None = None
        self.origin: str | None = None
        self.error: str | None = None
        self.missing = False
        self._stamp: float | None = None
        self._status: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self.refresh()

    def _config_stamp(self) -> float | None:
        source = self.config.source
        try:
            return source.stat().st_mtime if source else None
        except OSError:
            return None

    def refresh(self) -> None:
        with self._lock:
            self._status.clear()
            try:
                if self.fixed is not None:
                    try:
                        config = isot_config.load_config(self.config_path) or isot_config.Config(source=None)
                    except isot_config.ConfigError:
                        config = isot_config.Config(source=None)
                    repos = isot_config.repos_from_paths(list(self.fixed))
                    default_id, origin = repos[0].id, "create_app"
                else:
                    config, repos, default_id, origin = isot_config.resolve_repos(config_path=self.config_path)
                self.config, self.repos, self.default_id, self.origin = config, repos, default_id, origin
                self.error, self.missing = None, False
                isot_config.apply(config)
            except isot_config.ConfigMissing as exc:
                self.repos, self.default_id, self.error, self.missing = [], None, str(exc), True
            except isot_config.ConfigError as exc:
                self.repos, self.default_id, self.error, self.missing = [], None, str(exc), False
            self._stamp = self._config_stamp()

    def check(self) -> None:
        if not self.repos or (self.config.source is not None and self._config_stamp() != self._stamp):
            self.refresh()

    @property
    def configured(self) -> bool:
        self.check()
        return bool(self.repos)

    def entry(self, repo_id: str | None = None) -> isot_config.DataRepoConfig:
        self.check()
        if not self.repos:
            raise HTTPException(status_code=503, detail=self.error or isot_config.NO_CONFIG_MESSAGE)
        wanted = repo_id or _REQUEST_REPO.get() or self.default_id
        found = next((item for item in self.repos if item.id == wanted), None)
        if found is None:
            known = ", ".join(item.id for item in self.repos)
            raise HTTPException(status_code=404, detail=f"repositorio desconocido: {wanted} (configurados: {known})")
        return found

    def root(self, repo_id: str | None = None) -> Path:
        entry = self.entry(repo_id)
        error = entry.error()
        if error:
            raise HTTPException(status_code=503, detail=f"repositorio {entry.id} no disponible: {error}")
        return entry.path

    def status(self, entry: isot_config.DataRepoConfig, *, fresh: bool = False) -> dict[str, Any]:
        """Estado del repositorio (git, rama, HEAD, dirty) con un TTL corto."""
        now = time.monotonic()
        cached = self._status.get(entry.id)
        if cached and not fresh and now - cached[0] < STATUS_TTL:
            return cached[1]
        error = entry.error()
        if error:
            info: dict[str, Any] = {
                "id": entry.id,
                "name": entry.label,
                "path": entry.path.name,
                "git": False,
                "branch": None,
                "head": None,
                "short": None,
                "dirty": False,
                "remote": entry.remote,
                "remote_url": None,
                "expected_branch": entry.branch,
            }
        else:
            info = isot_config.repo_status(entry.path, self.config, entry)
            info["path"] = entry.path.name
        info.update(
            {
                "available": error is None,
                "error": error,
                "default": entry.id == self.default_id,
            }
        )
        self._status[entry.id] = (now, info)
        return info


def create_app(
    repo_root: Path | list[Path] | None = None,
    edit: bool | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    fixed = [repo_root] if isinstance(repo_root, Path) else (list(repo_root) if repo_root else None)
    registry = RepoRegistry(fixed, config_path or os.environ.get("ISOT_CONFIG") or None)

    def edit_globally() -> bool:
        return edit_enabled(edit) or (edit is None and registry.config.edit.enabled)

    def edit_for(entry: isot_config.DataRepoConfig) -> bool:
        """Edición por repositorio: global (flag / edit.enabled) salvo `repos[].edit: false`."""
        return edit_globally() and entry.edit is not False

    app = FastAPI(
        title="ISoT",
        description="Infrastructure Source of Truth — API de inventario (multi-sitio, varios repositorios)",
        version="0.10.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.add_middleware(RepoPrefixMiddleware)
    app.state.repos = registry

    def resolved_root() -> Path:
        return registry.root()

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

    def unconfigured_payload() -> dict[str, Any]:
        return {
            "configured": False,
            "error": None if registry.missing else registry.error,
            "message": isot_config.NO_CONFIG_MESSAGE,
            "hints": isot_config.HINTS,
            "search": ["--config", "$ISOT_CONFIG", "./isot.yaml", "~/.config/isot/config.yaml"],
            "edit": edit_globally(),
        }

    def repos_payload(*, fresh: bool = False) -> list[dict[str, Any]]:
        return [
            {**registry.status(item, fresh=fresh), "edit": edit_for(item)}
            for item in registry.repos
        ]

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        configured = registry.configured
        repos = repos_payload() if configured else []
        return {
            "status": "ok",
            "configured": configured,
            "data_repo": next((item["name"] for item in repos if item["default"]), None),
            "default_repo": registry.default_id,
            "repos": len(repos),
            "available": sum(1 for item in repos if item["available"]),
        }

    @app.get("/api/repos")
    def list_repos() -> dict[str, Any]:
        """Repositorios configurados con su estado (git, disponibilidad y error)."""
        if not registry.configured:
            return {**unconfigured_payload(), "repos": [], "default_repo": None}
        return {
            "configured": True,
            "origin": registry.origin,
            "config_file": str(registry.config.source) if registry.config.source else None,
            "default_repo": registry.default_id,
            "repos": repos_payload(),
        }

    @app.get("/api/repos/{repo_id}")
    def repo_detail(repo_id: str) -> dict[str, Any]:
        entry = registry.entry(repo_id)
        return {**registry.status(entry, fresh=True), "edit": edit_for(entry)}

    @app.get("/api/config")
    def config_info() -> dict[str, Any]:
        """Configuración vista desde un repositorio (prefijo /api/repos/{id}/ o el por defecto).
        Sin secretos: ISoT no maneja credenciales."""
        if not registry.configured:
            return unconfigured_payload()
        entry = registry.entry()
        cfg = registry.config
        status = registry.status(entry, fresh=True)
        can = edit_for(entry)
        return {
            "configured": True,
            "origin": registry.origin,
            "config_file": str(cfg.source) if cfg.source else None,
            "data_repo": status,
            "repos": [
                {**item, "active": item["id"] == entry.id}
                for item in repos_payload()
            ],
            "active_repo": entry.id,
            "default_repo": registry.default_id,
            "templates": [str(path) for path in cfg.templates],
            "edit": can,
            "push": False,  # ISoT nunca hace push (sin escrituras remotas)
            "push_remote": entry.remote if cfg.edit.push_remote == "origin" else cfg.edit.push_remote,
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
                    "refs": {"base": resolve_ref(resolved_root(), base).meta(), "head": resolve_ref(resolved_root(), head).meta()},
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
        entry = registry.entry() if registry.configured else None
        root = entry.path if entry and not entry.error() else None
        can = bool(entry and edit_for(entry))
        return {
            "repo": entry.id if entry else None,
            "edit": can,
            "push": False,  # ISoT no publica: el commit queda local
            "site_templates": wizard_site_templates(root),
            "template_library": library_catalog(root),
        }

    def require_edit() -> None:
        entry = registry.entry()
        if not edit_globally():
            raise HTTPException(status_code=403, detail="edición deshabilitada (ISOT_EDIT=1 o --edit)")
        if not edit_for(entry):
            raise HTTPException(status_code=403, detail=f"repositorio {entry.id} en solo lectura (repos[].edit: false)")

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

    def wizard_payload(preview: dict[str, Any]) -> dict[str, Any]:
        """Preview del wizard + token de concurrencia + archivos que ya tienen cambios sin commit."""
        from isot.worktree import pending_info, plan_token

        root = resolved_root()
        writes = {str(f["path"]): str(f.get("content") or "") for f in preview.get("files") or []}
        pending = pending_info(root, writes)
        return {
            **preview,
            "token": plan_token(root, writes, []),
            "pending": [{"path": rel, **info} for rel, info in pending.items()],
            "worktree": True,
        }

    def wizard_save(preview: dict[str, Any], body: dict[str, Any], site: Any) -> dict[str, Any]:
        from isot.worktree import ConflictError

        _guard_wizard_findings(preview, body)
        try:
            result = save_preview(resolved_root(), preview, site=site, token=str(body.get("token") or "") or None)
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        clear_caches(resolved_root())
        return {**result, "preview": preview}

    @app.post("/api/wizard/site/preview")
    def wizard_site_preview(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        try:
            return wizard_payload(site_preview(resolved_root(), body))
        except WizardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/wizard/site/save")
    def wizard_site_save(body: dict[str, Any]) -> dict[str, Any]:
        """Escribe el sitio nuevo en el working tree (sin commit; el commit va por «Cambios»)."""
        require_edit()
        try:
            preview = site_preview(resolved_root(), body)
            return wizard_save(preview, body, preview.get("site") or body.get("id"))
        except WizardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/wizard/device/preview")
    def wizard_device_preview(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        try:
            return wizard_payload(device_preview(resolved_root(), body))
        except (WizardError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/wizard/device/save")
    def wizard_device_save(body: dict[str, Any]) -> dict[str, Any]:
        """Escribe los devices en el working tree (sin commit; el commit va por «Cambios»)."""
        require_edit()
        try:
            preview = device_preview(resolved_root(), body)
            return wizard_save(preview, body, preview.get("site") or body.get("site"))
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

    @app.get("/api/index/status")
    def index_status_route() -> dict[str, Any]:
        """¿El índice del working tree está al día? (huellas por grupo de fuentes)."""
        root = resolved_root()
        status = dict(cached_status(root, ttl=0))
        status.pop("groups", None)
        status["can_reindex"] = bool(registry.configured and edit_for(registry.entry()))
        status["live"] = not status["fresh"]
        from isot.worktree import WorktreeError, status_entries

        try:
            entries = status_entries(root)
        except WorktreeError:
            entries = []
        # El índice se calcula sobre el working tree: los cambios sin commit cuentan.
        status["uncommitted"] = sum(1 for e in entries if e["area"] != "index")
        return status

    @app.get("/api/index/artifacts")
    def index_artifacts() -> dict[str, Any]:
        """Índice de artefactos (manifest-templates/, renders.yaml, rendered/, _ingest/)."""
        root = resolved_root()
        overlay = live_indexes(root)
        path = root / ARTIFACTS_INDEX_NAME
        if overlay is not None and str(path) in overlay:
            return overlay[str(path)]
        data = _json_file(path) if path.is_file() else build_artifacts_index(root)
        return data if isinstance(data, dict) else {}

    @app.post("/api/index/reindex")
    def index_reindex() -> dict[str, Any]:
        """Regenera el índice en el working tree (sin commit; también se regenera al hacer commit)."""
        if not edit_globally() or not (registry.configured and edit_for(registry.entry())):
            raise HTTPException(status_code=403, detail="edición deshabilitada")
        root = resolved_root()
        from isot.worktree import save_files

        writes = pending_index_writes(root)
        if not writes:
            return {"ok": True, "saved": False, "message": "El índice ya está al día"}
        try:
            result = save_files(root, writes, [])
        except WizardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        clear_caches(root)
        return {**result, "message": f"Índice regenerado en el working tree ({len(result['files'])} archivo(s)); entra en el próximo commit."}

    @app.get("/api/refs/resolve")
    def git_resolve(ref: str = Query(...)) -> dict[str, Any]:
        """Valida y resuelve una ref (`local`, rama, tag, SHA completo o corto)."""
        try:
            return describe_ref(resolved_root(), ref)
        except RefError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/compare/files")
    def compare_files(
        base: str = Query(...),
        head: str = Query(...),
        prefix: list[str] | None = Query(default=None),
    ) -> dict[str, Any]:
        """Archivos que cambian de base a head (cualquiera puede ser `local`). Solo lectura."""
        try:
            return changed_files(resolved_root(), base, head, prefix)
        except RefError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/compare/file")
    def compare_file(base: str = Query(...), head: str = Query(...), path: str = Query(...)) -> dict[str, Any]:
        """Contenido de un archivo en base y en head + diff unificado. Solo lectura."""
        try:
            return file_diff(resolved_root(), base, head, path)
        except RefError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
                base_spec = resolve_ref(root, base)
                head_spec = resolve_ref(root, head)
                before = link_details(view_from_snapshot(snapshot_for(root, base_spec)), source, target)
                after = link_details(view_from_snapshot(snapshot_for(root, head_spec)), source, target)
            except RefError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if before is None and after is None:
                raise HTTPException(status_code=404, detail=f"Conexión no encontrada en base ni head: {source} – {target}")
            return {
                "detail": after or before,
                "base": before,
                "head": after,
                "diff": diff_details(before, after),
                "refs": {"base": base_spec.meta(), "head": head_spec.meta()},
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

    from isot import web_renders, web_worktree

    web_worktree.register(app, registry=registry, resolved_root=resolved_root, require_edit=require_edit, edit_for=edit_for, edit_globally=edit_globally)
    web_renders.register(
        app,
        registry=registry,
        resolved_root=resolved_root,
        edit_for=edit_for,
        edit_globally=edit_globally,
    )

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


DEFAULT_HOST = "127.0.0.1"


def build_parser():
    import argparse

    parser = argparse.ArgumentParser(description="Sirve la API y la UI de ISoT sobre uno o varios repositorios de datos.")
    parser.add_argument("--config", type=Path, default=None, help="Archivo de configuración isot.yaml.")
    parser.add_argument(
        "--repo",
        type=Path,
        action="append",
        default=None,
        help="Repositorio de datos (repetible; ignora los repos de isot.yaml). El id es el nombre de la carpeta.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("ISOT_HOST") or DEFAULT_HOST,
        help="Interfaz de escucha (por defecto 127.0.0.1; 0.0.0.0 expone la herramienta a la red).",
    )
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
        os.environ["ISOT_ROOT"] = os.pathsep.join(str(item.expanduser().resolve()) for item in args.repo)
    if args.edit:
        os.environ["ISOT_EDIT"] = "1"
    try:
        _config, repos, default_id, origin = isot_config.resolve_repos()
        print(f"ISoT: {len(repos)} repositorio(s) de datos ({origin}); por defecto: {default_id}", file=sys.stderr)
        for item in repos:
            error = item.error()
            state = f"NO DISPONIBLE: {error}" if error else "ok"
            print(f"  {item.id:<16} {isot_config.display_path(item.path)}  [{state}]", file=sys.stderr)
    except isot_config.ConfigMissing:
        print(isot_config.missing_message(), file=sys.stderr)
        print("\nLa UI mostrará estas instrucciones hasta que exista la configuración.", file=sys.stderr)
    except isot_config.ConfigError as exc:
        print(f"error de configuración: {exc}", file=sys.stderr)
        print("La UI mostrará el error hasta que se corrija.", file=sys.stderr)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print(
            f"aviso: escuchando en {args.host}: cualquiera que alcance este puerto puede ver el inventario"
            + (" y crear ramas wizard/…" if os.environ.get("ISOT_EDIT") == "1" else ""),
            file=sys.stderr,
        )
    # La app del módulo se creó al importar; se recrea con las opciones ya aplicadas.
    global app
    app = create_app()
    uvicorn.run(app, host=args.host, port=args.port, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
