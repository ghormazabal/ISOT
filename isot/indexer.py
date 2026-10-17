"""Generadores deterministas de índices ligeros multi-sitio.

Escribe:
  * sites/_sites_index.json
  * sites/<id>/devices/_device_index.json
  * sites/<id>/clusters/_cluster_index.json
  * cluster-templates/_template_index.json
  * sites/<id>/cluster-templates/_template_index.json (si el sitio tiene templates)
  * _artifacts_index.json: manifest-templates/, renders.yaml, salidas en rendered/
    (o el target_dir de cada instancia) y _ingest/
  * _index_manifest.json: huella (sha1) de cada grupo de fuentes, para detectar
    al instante un índice desactualizado y reindexar solo lo que cambió.

El índice es lo que permite a la web trabajar rápido sobre miles de archivos:
cualquier escritura de ISoT (wizards, renders, «Corregir») lo regenera en el
mismo commit; la web detecta un índice viejo (`index_status`) y nunca sirve
datos desactualizados (ver `live_indexes`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from isot.helm import charts_from_doc
from isot.inventory import (
    CLUSTER_INDEX_NAME,
    DEVICE_INDEX_NAME,
    SITES_INDEX_NAME,
    TEMPLATE_INDEX_NAME,
    Site,
    load_catalog,
    load_clusters,
    load_devices,
    load_templates,
)
from isot.config import add_repo_arguments, cli_targets


def render_index(entries: Any) -> str:
    return json.dumps(entries, indent=2, ensure_ascii=False) + "\n"


def build_device_index(repo_root: Path, site: Site) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for device in load_devices(repo_root, [site]):
        entries.append(
            {
                "hostname": device.hostname,
                "site": device.site_id,
                "role": device.role,
                "ip": device.primary_ip,
                "path": device.rel_path,
                "kind": device.kind,
                "cluster_ref": device.cluster_ref,
                "cluster_ref_site": device.cluster_ref_site,
                "links": device.index_links(),
            }
        )
        leaf_pair = device.data.get("leaf_pair")
        if leaf_pair:
            entries[-1]["leaf_pair"] = leaf_pair
    entries.sort(key=lambda item: (item["path"] or "", item["hostname"] or ""))
    return entries


def build_cluster_index(repo_root: Path, site: Site) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for doc in load_clusters(repo_root, [site]):
        name = doc.get("cluster_name") or doc.get("_stem")
        nodes = doc.get("nodes") if isinstance(doc.get("nodes"), list) else []
        hosts = doc.get("hosts") if isinstance(doc.get("hosts"), list) else []
        overrides = doc.get("values_overrides") if isinstance(doc.get("values_overrides"), list) else []
        entries.append(
            {
                "name": name,
                "site": site.id,
                "kind": doc.get("kind"),
                "template_ref": doc.get("template_ref"),
                "distro": doc.get("distro") or doc.get("type"),
                "version": doc.get("version"),
                "path": doc.get("_path"),
                "node_count": len(nodes) or len(hosts),
                "override_count": len(overrides),
            }
        )
    entries.sort(key=lambda item: (item["path"] or "", item["name"] or ""))
    return entries


def _used_by_map(repo_root: Path, sites: list[Site], builder: "IndexBuilder | None" = None) -> dict[tuple[str, str], list[str]]:
    used_by: dict[tuple[str, str], list[str]] = defaultdict(list)
    for site in sites:
        clusters = builder.clusters(site) if builder else build_cluster_index(repo_root, site)
        for cluster in clusters:
            ref = cluster.get("template_ref")
            if not ref:
                continue
            used_by[("any", str(ref))].append(f"{site.id}/{cluster['name']}")
    return used_by


def build_template_index(
    repo_root: Path,
    docs: list[dict[str, Any]],
    used_by: dict[tuple[str, str], list[str]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for doc in docs:
        name = doc.get("template_name") or doc.get("_stem")
        site = doc.get("_site") or "global"
        used = sorted(used_by.get(("any", str(name)), []))
        charts = charts_from_doc(doc)
        entries.append(
            {
                "name": name,
                "site": site,
                "scope": "global" if site == "global" else "site",
                "version": doc.get("version") or None,
                "distro": doc.get("distro"),
                "cni": (doc.get("cni") or {}).get("primary") if isinstance(doc.get("cni"), dict) else None,
                "path": doc.get("_path"),
                "chart_count": len(charts),
                "charts": [item["name"] for item in charts],
                "used_by": used if site == "global" else [item for item in used if item.startswith(f"{site}/")],
            }
        )
    entries.sort(key=lambda item: (item["path"] or "", item["name"] or ""))
    return entries


def build_sites_index(repo_root: Path, builder: "IndexBuilder | None" = None) -> list[dict[str, Any]]:
    builder = builder or IndexBuilder(repo_root)
    sites, spaces = builder.sites, builder.spaces
    entries: list[dict[str, Any]] = []
    for site in sites:
        devices = builder.devices(site)
        clusters = builder.clusters(site)
        links: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for device in devices:
            for link in device.get("links") or []:
                peer_site = link.get("site") or site.id
                if peer_site == site.id:
                    continue
                key = (site.id, str(device["hostname"]), str(peer_site), str(link.get("device")))
                if key in seen:
                    continue
                seen.add(key)
                links.append(
                    {
                        "device": device["hostname"],
                        "peer_site": peer_site,
                        "peer_device": link.get("device"),
                        "vlan": link.get("vlan"),
                        "port": link.get("port"),
                    }
                )
        links.sort(key=lambda item: (item["peer_site"] or "", item["device"] or "", item["peer_device"] or ""))
        entries.append(
            {
                "id": site.id,
                "name": site.name,
                "path": site.rel_path,
                "description": site.description,
                "device_count": len(devices),
                "cluster_count": len(clusters),
                "address_spaces": [space.id for space in spaces if site.id in space.sites],
                "inter_site_links": links,
            }
        )
    entries.sort(key=lambda item: item["id"] or "")
    return entries


class IndexBuilder:
    """Construye los índices de un árbol, memorizando lo ya calculado.

    `reuse_devices` / `reuse_clusters`: sitios cuyo índice escrito sigue al día
    (su grupo de fuentes no cambió) y se leen del disco en vez de reparsear
    sus YAML: es el reindexado incremental.
    """

    def __init__(self, repo_root: Path, *, reuse_devices: Iterable[str] = (), reuse_clusters: Iterable[str] = ()) -> None:
        self.root = repo_root
        self.sites, self.spaces = load_catalog(repo_root)
        self.reuse_devices = set(reuse_devices)
        self.reuse_clusters = set(reuse_clusters)
        self._devices: dict[str, list[dict[str, Any]]] = {}
        self._clusters: dict[str, list[dict[str, Any]]] = {}
        self.parsed: set[str] = set()  # grupos reparseados (para tests y diagnóstico)

    @staticmethod
    def _read(path: Path) -> list[dict[str, Any]] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, list) else None

    def devices(self, site: Site) -> list[dict[str, Any]]:
        if site.id not in self._devices:
            cached = self._read(site.devices_dir / DEVICE_INDEX_NAME) if site.id in self.reuse_devices else None
            if cached is None:
                self.parsed.add(f"{site.rel_path}/devices")
                cached = build_device_index(self.root, site)
            self._devices[site.id] = cached
        return self._devices[site.id]

    def clusters(self, site: Site) -> list[dict[str, Any]]:
        if site.id not in self._clusters:
            cached = self._read(site.clusters_dir / CLUSTER_INDEX_NAME) if site.id in self.reuse_clusters else None
            if cached is None:
                self.parsed.add(f"{site.rel_path}/clusters")
                cached = build_cluster_index(self.root, site)
            self._clusters[site.id] = cached
        return self._clusters[site.id]


def index_targets(repo_root: Path, builder: IndexBuilder | None = None) -> list[tuple[Path, Any]]:
    builder = builder or IndexBuilder(repo_root)
    sites = builder.sites
    used_by = _used_by_map(repo_root, sites, builder)
    targets: list[tuple[Path, Any]] = [
        (repo_root / "sites" / SITES_INDEX_NAME, build_sites_index(repo_root, builder)),
        (
            repo_root / "cluster-templates" / TEMPLATE_INDEX_NAME,
            build_template_index(
                repo_root,
                [
                    doc
                    for doc in load_templates(repo_root, sites, include_library=False)
                    if doc.get("_site") == "global"
                ],
                used_by,
            ),
        ),
    ]
    for site in sites:
        targets.append((site.devices_dir / DEVICE_INDEX_NAME, builder.devices(site)))
        targets.append((site.clusters_dir / CLUSTER_INDEX_NAME, builder.clusters(site)))
        site_templates = [
            doc
            for doc in load_templates(repo_root, [site], include_library=False)
            if doc.get("_site") == site.id
        ]
        if site_templates or site.templates_dir.is_dir():
            targets.append(
                (
                    site.templates_dir / TEMPLATE_INDEX_NAME,
                    build_template_index(repo_root, site_templates, used_by),
                )
            )
    targets.append((repo_root / ARTIFACTS_INDEX_NAME, build_artifacts_index(repo_root)))
    return targets


def write_indexes(repo_root: Path, *, incremental: bool = True) -> list[Path]:
    """Regenera los índices (solo reescribe los que cambian) y el manifiesto.

    Incremental: si el manifiesto existe, los sitios cuyos devices/clusters no
    cambiaron se toman del índice escrito en vez de reparsear sus YAML.
    """
    builder = plan_builder(repo_root) if incremental else IndexBuilder(repo_root)
    written: list[Path] = []
    for target, entries in index_targets(repo_root, builder):
        text = render_index(entries)
        if target.is_file() and target.read_text(encoding="utf-8") == text:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        written.append(target)
    manifest = repo_root / MANIFEST_NAME
    text = render_index(build_manifest(repo_root, builder.sites))
    if not manifest.is_file() or manifest.read_text(encoding="utf-8") != text:
        manifest.write_text(text, encoding="utf-8")
        written.append(manifest)
    _STATUS_CACHE.pop(str(repo_root.resolve()), None)
    write_indexes.last_builder = builder  # type: ignore[attr-defined]
    return written


def check_index(repo_root: Path) -> tuple[bool, str]:
    """Comprobación completa (CI): cada índice y el manifiesto deben coincidir byte a byte."""
    mismatches: list[str] = []
    sites, _ = load_catalog(repo_root)
    expected_manifest = render_index(build_manifest(repo_root, sites))
    manifest = repo_root / MANIFEST_NAME
    if not manifest.is_file():
        mismatches.append(f"No existe {MANIFEST_NAME}")
    elif manifest.read_text(encoding="utf-8") != expected_manifest:
        mismatches.append(f"{MANIFEST_NAME} no está al día")
    for target, entries in index_targets(repo_root):
        expected = render_index(entries)
        rel = target.relative_to(repo_root).as_posix()
        if not target.is_file():
            mismatches.append(f"No existe {rel}")
            continue
        if target.read_text(encoding="utf-8") != expected:
            mismatches.append(f"{rel} no está al día")
    if mismatches:
        return False, "; ".join(mismatches) + ". Ejecuta: isot index (o python -m isot.indexer)"
    return True, "Índices al día"


# --------------------------------------------------------------------------------------
# Artefactos: manifest-templates/, renders.yaml, salidas renderizadas, _ingest/

ARTIFACTS_INDEX_NAME = "_artifacts_index.json"
MANIFEST_NAME = "_index_manifest.json"
INDEX_FILE_NAMES = frozenset(
    {SITES_INDEX_NAME, DEVICE_INDEX_NAME, CLUSTER_INDEX_NAME, TEMPLATE_INDEX_NAME, ARTIFACTS_INDEX_NAME, MANIFEST_NAME}
)
INGEST_DIR = "_ingest"
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__"})


def _render_dirs(repo_root: Path) -> list[str]:
    """Carpetas de salida de renders (target_dir global + las de cada instancia), en este repo."""
    from isot.renders import load_render_config, safe_rel_dir

    try:
        cfg = load_render_config(repo_root)
    except Exception:  # noqa: BLE001 - un renders.yaml roto no debe impedir indexar
        return ["rendered"]
    dirs = {cfg.target_dir or "rendered"}
    for inst in cfg.instances:
        if inst.target_dir:
            dirs.add(inst.target_dir)
    out = []
    for item in sorted(dirs):
        try:
            out.append(safe_rel_dir(item, "target_dir"))
        except Exception:  # noqa: BLE001
            continue
    # sin anidados: rendered/ ya cubre rendered/x
    return [d for d in out if not any(d != o and d.startswith(o + "/") for o in out)]


def _walk(base: Path, rel: str) -> list[str]:
    root = base / rel
    if root.is_file():
        return [rel]
    if not root.is_dir() or root.is_symlink():
        return []
    out: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (Path(current) / d).is_symlink())
        for name in sorted(files):
            if name in INDEX_FILE_NAMES:
                continue
            path = Path(current) / name
            if path.is_symlink():
                continue
            out.append(path.relative_to(base).as_posix())
    return out


def build_artifacts_index(repo_root: Path) -> dict[str, Any]:
    from isot.renders import SIDECAR, list_templates, load_render_config

    templates = []
    for tmpl in list_templates(repo_root):
        if tmpl.source != "repo":
            continue
        templates.append(
            {
                "id": tmpl.id,
                "version": tmpl.version,
                "path": tmpl.path,
                "description": tmpl.spec.get("description"),
                "files": sorted(tmpl.files),
                "errors": len(tmpl.errors),
            }
        )
    renders: dict[str, Any] = {"path": "renders.yaml", "exists": (repo_root / "renders.yaml").is_file(), "instances": []}
    try:
        cfg = load_render_config(repo_root)
        renders["target_dir"] = cfg.target_dir
        renders["errors"] = len(cfg.errors)
        renders["instances"] = [
            {
                "id": inst.id,
                "template": inst.template,
                "site": inst.site,
                "cluster": inst.cluster,
                "target_dir": inst.target_dir,
                "dir": inst.out_dir,
                "enabled": inst.enabled,
            }
            for inst in cfg.instances
        ]
    except Exception as exc:  # noqa: BLE001
        renders["errors"] = 1
        renders["error"] = str(exc)[:200]
    rendered = []
    for folder in _render_dirs(repo_root):
        files = _walk(repo_root, folder)
        by_dir: dict[str, list[str]] = defaultdict(list)
        for rel in files:
            parent, _, name = rel.rpartition("/")
            by_dir[parent].append(name)
        for directory in sorted(by_dir):
            names = by_dir[directory]
            meta: dict[str, Any] = {}
            if SIDECAR in names:
                try:
                    meta = json.loads((repo_root / directory / SIDECAR).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    meta = {}
            rendered.append(
                {
                    "dir": directory,
                    "template": str(meta.get("template") or "").split("@")[0] or None,
                    "version": (str(meta.get("template") or "").split("@") + [None])[1],
                    "instance": meta.get("instance"),
                    "files": sorted(name for name in names if name != SIDECAR),
                }
            )
    ingest = [
        {"path": rel, "size": (repo_root / rel).stat().st_size}
        for rel in _walk(repo_root, INGEST_DIR)
    ]
    return {"manifest_templates": templates, "renders": renders, "rendered": rendered, "ingest": ingest}


# --------------------------------------------------------------------------------------
# Manifiesto de fuentes: detección rápida de índice viejo e incremental

_HASH_MEMO: dict[str, tuple[int, int, str]] = {}
_MEMO_LOCK = threading.Lock()


def _file_hash(path: Path) -> str:
    """sha1 del contenido, memorizado por (tamaño, mtime) para no releer archivos sin cambios."""
    stat = path.stat()
    key = str(path)
    with _MEMO_LOCK:
        hit = _HASH_MEMO.get(key)
    if hit and hit[0] == stat.st_size and hit[1] == stat.st_mtime_ns:
        return hit[2]
    digest = hashlib.sha1(path.read_bytes()).hexdigest()
    with _MEMO_LOCK:
        _HASH_MEMO[key] = (stat.st_size, stat.st_mtime_ns, digest)
    return digest


def source_groups(repo_root: Path, sites: list[Site] | None = None) -> dict[str, list[str]]:
    """Grupos de fuentes que alimentan cada índice → archivos (rutas relativas)."""
    if sites is None:
        sites, _ = load_catalog(repo_root)
    groups: dict[str, list[str]] = {
        "sites.yaml": _walk(repo_root, "sites.yaml"),
        "cluster-templates": _walk(repo_root, "cluster-templates"),
        "manifest-templates": _walk(repo_root, "manifest-templates"),
        "renders.yaml": _walk(repo_root, "renders.yaml"),
        INGEST_DIR: _walk(repo_root, INGEST_DIR),
    }
    for folder in _render_dirs(repo_root):
        groups[f"rendered:{folder}"] = _walk(repo_root, folder)
    for site in sites:
        rel = site.rel_path.strip("/")
        groups[f"{rel}/devices"] = _walk(repo_root, f"{rel}/devices")
        groups[f"{rel}/clusters"] = _walk(repo_root, f"{rel}/clusters")
        groups[f"{rel}/cluster-templates"] = _walk(repo_root, f"{rel}/cluster-templates")
        groups[f"{rel}/renders.yaml"] = _walk(repo_root, f"{rel}/renders.yaml")
    return groups


def build_manifest(repo_root: Path, sites: list[Site] | None = None) -> dict[str, Any]:
    digests: dict[str, str] = {}
    files = 0
    for group, paths in sorted(source_groups(repo_root, sites).items()):
        h = hashlib.sha1()
        for rel in paths:
            h.update(f"{rel}\0{_file_hash(repo_root / rel)}\n".encode("utf-8", "replace"))
        digests[group] = h.hexdigest()
        files += len(paths)
    return {"version": 1, "generator": "isot index", "files": files, "groups": digests}


def _index_files(repo_root: Path, sites: list[Site]) -> list[Path]:
    out = [repo_root / "sites" / SITES_INDEX_NAME, repo_root / "cluster-templates" / TEMPLATE_INDEX_NAME, repo_root / ARTIFACTS_INDEX_NAME]
    for site in sites:
        out.append(site.devices_dir / DEVICE_INDEX_NAME)
        out.append(site.clusters_dir / CLUSTER_INDEX_NAME)
    return out


def index_status(repo_root: Path) -> dict[str, Any]:
    """¿El índice escrito refleja las fuentes? Rápido: compara huellas por grupo."""
    sites, _ = load_catalog(repo_root)
    current = build_manifest(repo_root, sites)
    manifest_path = repo_root / MANIFEST_NAME
    stored: dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            stored = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            stored = {}
    missing = [p.relative_to(repo_root).as_posix() for p in _index_files(repo_root, sites) if not p.is_file()]
    old = stored.get("groups") if isinstance(stored.get("groups"), dict) else None
    if old is None:
        stale = sorted(current["groups"])
    else:
        stale = sorted(g for g in set(current["groups"]) | set(old) if current["groups"].get(g) != old.get(g))
    fresh = bool(old is not None and not stale and not missing)
    return {
        "fresh": fresh,
        "manifest": old is not None,
        "missing": missing,
        "stale": stale,
        "files": current["files"],
        "key": hashlib.sha1(json.dumps(current["groups"], sort_keys=True).encode()).hexdigest()[:16],
        "groups": current["groups"],
    }


def plan_builder(repo_root: Path, status: dict[str, Any] | None = None) -> IndexBuilder:
    """IndexBuilder que reutiliza el índice escrito de los sitios cuyo grupo no cambió."""
    status = status or index_status(repo_root)
    if not status["manifest"] or "sites.yaml" in status["stale"]:
        return IndexBuilder(repo_root)
    sites, _ = load_catalog(repo_root)
    stale = set(status["stale"])
    missing = set(status["missing"])
    reuse_devices, reuse_clusters = set(), set()
    for site in sites:
        rel = site.rel_path.strip("/")
        if f"{rel}/devices" not in stale and f"{rel}/devices/{DEVICE_INDEX_NAME}" not in missing:
            reuse_devices.add(site.id)
        if f"{rel}/clusters" not in stale and f"{rel}/clusters/{CLUSTER_INDEX_NAME}" not in missing:
            reuse_clusters.add(site.id)
    return IndexBuilder(repo_root, reuse_devices=reuse_devices, reuse_clusters=reuse_clusters)


STATUS_TTL = 2.0
_STATUS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LIVE_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}


def cached_status(repo_root: Path, *, ttl: float = STATUS_TTL) -> dict[str, Any]:
    key = str(repo_root.resolve())
    hit = _STATUS_CACHE.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < ttl:
        return hit[1]
    status = index_status(repo_root)
    _STATUS_CACHE[key] = (now, status)
    return status


def live_indexes(repo_root: Path) -> dict[str, Any] | None:
    """None si el índice escrito está al día; si no, {ruta absoluta: contenido} calculado
    en memoria (incremental), para que la web nunca sirva datos viejos. No escribe nada."""
    status = cached_status(repo_root)
    if status["fresh"]:
        return None
    key = str(repo_root.resolve())
    hit = _LIVE_CACHE.get(key)
    if hit and hit[0] == status["key"]:
        return hit[1]
    builder = plan_builder(repo_root, status)
    overlay = {str(target): entries for target, entries in index_targets(repo_root, builder)}
    _LIVE_CACHE[key] = (status["key"], overlay)
    return overlay


def write_index(repo_root: Path) -> Path:
    write_indexes(repo_root)
    return repo_root / "sites" / SITES_INDEX_NAME


def build_index(repo_root: Path) -> list[dict[str, Any]]:
    """Compat: todos los dispositivos, aplanados."""
    sites, _ = load_catalog(repo_root)
    entries: list[dict[str, Any]] = []
    for site in sites:
        entries.extend(build_device_index(repo_root, site))
    entries.sort(key=lambda item: (item["path"] or "", item["hostname"] or ""))
    return entries


def build_device_index_all(repo_root: Path) -> list[dict[str, Any]]:
    return build_index(repo_root)


def build_cluster_index_all(repo_root: Path) -> list[dict[str, Any]]:
    sites, _ = load_catalog(repo_root)
    entries: list[dict[str, Any]] = []
    for site in sites:
        entries.extend(build_cluster_index(repo_root, site))
    entries.sort(key=lambda item: (item["path"] or "", item["name"] or ""))
    return entries


def build_template_index_all(repo_root: Path) -> list[dict[str, Any]]:
    sites, _ = load_catalog(repo_root)
    used_by = _used_by_map(repo_root, sites)
    return build_template_index(repo_root, load_templates(repo_root, sites), used_by)


def index_path(repo_root: Path) -> Path:
    return repo_root / "sites" / SITES_INDEX_NAME


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Genera índices ligeros (sitios, devices, clusters, cluster-templates, artefactos: "
            "manifest-templates/, renders.yaml, rendered/, _ingest/) y su manifiesto. Incremental por defecto."
        )
    )
    add_repo_arguments(parser)
    parser.add_argument(
        "--check",
        action="store_true",
        help="No escribe: falla si algún índice commiteado no coincide (comprobación completa, para CI).",
    )
    parser.add_argument("--full", action="store_true", help="Reindexado completo (sin reutilizar sitios sin cambios).")
    parser.add_argument("--status", action="store_true", help="No escribe: muestra qué grupos de fuentes cambiaron.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    targets = cli_targets(args)
    if targets is None:
        return 2
    worst = 0
    for target in targets:
        prefix = f"[{target.id}] " if len(targets) > 1 else ""
        if target.error:
            print(f"{prefix}no disponible: {target.error}")
            worst = 1
            continue
        root = target.path
        if args.check:
            ok, message = check_index(root)
            print(f"{prefix}{message}")
            worst = worst if ok else 1
            continue
        if args.status:
            status = index_status(root)
            if status["fresh"]:
                print(f"{prefix}Índice al día ({status['files']} archivos fuente)")
            else:
                worst = 1
                print(f"{prefix}Índice desactualizado: " + ", ".join(status["stale"] or status["missing"] or ["sin manifiesto"]))
            continue
        written = write_indexes(root, incremental=not args.full)
        for path in written:
            print(f"{prefix}Escrito {path.relative_to(root).as_posix()}")
        if not written:
            print(f"{prefix}Índice ya al día")
    return worst


if __name__ == "__main__":
    raise SystemExit(main())


def pending_index_writes(repo_root: Path, *, incremental: bool = True) -> dict[str, str]:
    """{ruta relativa: contenido} de los índices que cambiarían al reindexar (no escribe)."""
    builder = plan_builder(repo_root) if incremental else IndexBuilder(repo_root)
    writes: dict[str, str] = {}
    for target, entries in index_targets(repo_root, builder):
        text = render_index(entries)
        if not target.is_file() or target.read_text(encoding="utf-8") != text:
            writes[target.relative_to(repo_root).as_posix()] = text
    manifest = render_index(build_manifest(repo_root, builder.sites))
    path = repo_root / MANIFEST_NAME
    if not path.is_file() or path.read_text(encoding="utf-8") != manifest:
        writes[MANIFEST_NAME] = manifest
    return writes
