"""Snapshot de inventario para evaluar reglas."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from isot.inventory import (
    rel_path,
    AddressSpace,
    Device,
    Site,
    load_catalog,
    load_clusters,
    load_devices,
    load_templates,
    load_topology,
    resolve_template_path,
    site_by_id,
)


@dataclass
class RuleContext:
    root: Path
    sites: list[Site]
    spaces: list[AddressSpace]
    devices: list[Device]
    clusters: list[dict[str, Any]]
    templates: list[dict[str, Any]]
    topologies: dict[str, dict[str, Any]]
    as_of: date
    devices_by_host: dict[tuple[str, str], Device] = field(default_factory=dict)
    templates_by_name: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    def site(self, site_id: str) -> Site | None:
        return site_by_id(self.sites, site_id)

    def device(self, site_id: str, hostname: str) -> Device | None:
        return self.devices_by_host.get((site_id, hostname.lower()))

    def template_for(self, cluster: dict[str, Any]) -> dict[str, Any] | None:
        ref = cluster.get("template_ref")
        if not ref:
            return None
        site_id = str(cluster.get("_site") or "")
        path = resolve_template_path(self.root, self.sites, site_id, str(ref))
        if path is None:
            return None
        rel = rel_path(path, self.root)
        for doc in self.templates:
            if str(doc.get("_path")) == rel:
                return doc
        return None


def load_context(root: Path, *, as_of: date | None = None) -> RuleContext:
    sites, spaces = load_catalog(root)
    devices = load_devices(root, sites)
    clusters = load_clusters(root, sites)
    templates = load_templates(root, sites)
    topologies = {site.id: load_topology(site) for site in sites}
    ctx = RuleContext(
        root=root,
        sites=sites,
        spaces=spaces,
        devices=devices,
        clusters=clusters,
        templates=templates,
        topologies=topologies,
        as_of=as_of or date.today(),
    )
    for device in devices:
        ctx.devices_by_host[(device.site_id, device.hostname.lower())] = device
    for doc in templates:
        name = str(doc.get("template_name") or doc.get("_stem") or "")
        site = str(doc.get("_site") or "global")
        ctx.templates_by_name[(site, name)] = doc
    return ctx
