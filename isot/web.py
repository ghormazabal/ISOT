"""API HTTP de solo lectura sobre el inventario ISoT multi-sitio.

Los listados y el grafo salen de los índices. El YAML se parsea solo
cuando se pide el detalle de un dispositivo, clúster o template.
Los values de un chart se leen bajo demanda (values_ref / values_inline).
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

from isot.gitdiff import RefError, list_commits, list_refs, semantic_diff
from isot.helm import (
    chart_meta,
    charts_from_doc,
    cluster_values_diff,
    load_chart_values,
    strip_charts_payload,
)
from isot.indexer import index_path
from isot.inventory import (
    CLUSTER_INDEX_NAME,
    DEVICE_INDEX_NAME,
    GLOBAL_SITE,
    TEMPLATE_INDEX_NAME,
    find_template_file,
    load_catalog,
    load_yaml,
    resolve_template_path,
    site_by_id,
)
from isot.paths import find_repo_root

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"


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
    path = root / "cluster-templates" / TEMPLATE_INDEX_NAME
    if not path.is_file():
        return []
    return _load_list_index(path, f"cluster-templates/{TEMPLATE_INDEX_NAME}")


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


def create_app(repo_root: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="ISoT",
        description="Infrastructure Source of Truth — API de solo lectura (multi-sitio)",
        version="0.6.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    def resolved_root() -> Path:
        if repo_root is not None:
            return repo_root
        if os.environ.get("ISOT_ROOT"):
            return Path(os.environ["ISOT_ROOT"]).resolve()
        return find_repo_root()

    def safe_under_root(path: Path, base: Path) -> Path:
        resolved = path.resolve()
        if not resolved.is_relative_to(base.resolve()):
            raise HTTPException(status_code=400, detail="Ruta fuera del repositorio")
        return resolved

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
    def health() -> dict[str, str]:
        return {"status": "ok"}

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
                template_path = found.relative_to(base).as_posix()
                template_site = (
                    GLOBAL_SITE if template_path.startswith("cluster-templates/") else template_path.split("/", 2)[1]
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
        rel = path.relative_to(base).as_posix()
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
        located = find_template_file(base / "cluster-templates", name)
        if located is None:
            raise HTTPException(status_code=404, detail=f"No existe cluster-templates/{name}/")
        return safe_under_root(located, base)

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
        found = safe_under_root(found, base)
        tdoc = load_yaml(found)
        if not isinstance(tdoc, dict):
            tdoc = {}
        return cluster_values_diff(base, cluster, found, tdoc)

    if FRONTEND_DIST.is_dir():
        assets = FRONTEND_DIST / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/")
        def spa_root() -> FileResponse:
            return FileResponse(FRONTEND_DIST / "index.html")

        @app.get("/{full_path:path}")
        def spa_fallback(full_path: str) -> FileResponse:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Recurso no encontrado")
            candidate = FRONTEND_DIST / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            index = FRONTEND_DIST / "index.html"
            if index.is_file():
                return FileResponse(index)
            raise HTTPException(status_code=404, detail="Frontend no construido")

    return app


app = create_app()


def main(argv: list[str] | None = None) -> int:
    del argv
    import uvicorn

    uvicorn.run("isot.web:app", host="0.0.0.0", port=8000, reload=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
