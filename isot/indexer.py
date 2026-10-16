"""Generadores deterministas de índices ligeros multi-sitio.

Escribe:
  * sites/_sites_index.json
  * sites/<id>/devices/_device_index.json
  * sites/<id>/clusters/_cluster_index.json
  * cluster-templates/_template_index.json
  * sites/<id>/cluster-templates/_template_index.json (si el sitio tiene templates)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

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
from isot.paths import find_repo_root


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


def _used_by_map(repo_root: Path, sites: list[Site]) -> dict[tuple[str, str], list[str]]:
    used_by: dict[tuple[str, str], list[str]] = defaultdict(list)
    for site in sites:
        for cluster in build_cluster_index(repo_root, site):
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


def build_sites_index(repo_root: Path) -> list[dict[str, Any]]:
    sites, spaces = load_catalog(repo_root)
    entries: list[dict[str, Any]] = []
    for site in sites:
        devices = build_device_index(repo_root, site)
        clusters = build_cluster_index(repo_root, site)
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


def index_targets(repo_root: Path) -> list[tuple[Path, Any]]:
    sites, _ = load_catalog(repo_root)
    used_by = _used_by_map(repo_root, sites)
    targets: list[tuple[Path, Any]] = [
        (repo_root / "sites" / SITES_INDEX_NAME, build_sites_index(repo_root)),
        (
            repo_root / "cluster-templates" / TEMPLATE_INDEX_NAME,
            build_template_index(
                repo_root,
                [doc for doc in load_templates(repo_root, sites) if doc.get("_site") == "global"],
                used_by,
            ),
        ),
    ]
    for site in sites:
        targets.append((site.devices_dir / DEVICE_INDEX_NAME, build_device_index(repo_root, site)))
        targets.append((site.clusters_dir / CLUSTER_INDEX_NAME, build_cluster_index(repo_root, site)))
        site_templates = [doc for doc in load_templates(repo_root, [site]) if doc.get("_site") == site.id]
        if site_templates or site.templates_dir.is_dir():
            targets.append(
                (
                    site.templates_dir / TEMPLATE_INDEX_NAME,
                    build_template_index(repo_root, site_templates, used_by),
                )
            )
    return targets


def write_indexes(repo_root: Path) -> list[Path]:
    written: list[Path] = []
    for target, entries in index_targets(repo_root):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_index(entries), encoding="utf-8")
        written.append(target)
    return written


def check_index(repo_root: Path) -> tuple[bool, str]:
    mismatches: list[str] = []
    for target, entries in index_targets(repo_root):
        expected = render_index(entries)
        rel = target.relative_to(repo_root).as_posix()
        if not target.is_file():
            mismatches.append(f"No existe {rel}")
            continue
        if target.read_text(encoding="utf-8") != expected:
            mismatches.append(f"{rel} no está al día")
    if mismatches:
        return False, "; ".join(mismatches) + ". Ejecuta: python -m isot.indexer"
    return True, "Índices al día"


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
        description="Genera índices ligeros (sitios, devices, clusters, cluster-templates)."
    )
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument(
        "--check",
        action="store_true",
        help="No escribe: falla si algún índice commiteado no coincide.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = args.root.resolve() if args.root else find_repo_root()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.check:
        ok, message = check_index(root)
        print(message)
        return 0 if ok else 1

    written = write_indexes(root)
    for path in written:
        print(f"Escrito {path.relative_to(root).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
