"""Detalle de una conexión (arista del grafo) a partir del inventario.

Una arista del grafo junta todos los enlaces lógicos entre dos nodos: bonds
con `leaf_pc`, interfaces con `connected_to`, OOB, hypervisor, enlaces DCI
entre sitios y referencias Kubernetes (`template_ref` / `cluster_ref`). Este
módulo los reconstruye desde el YAML (sin inventar campos: lo que no está en
el modelo queda en None) e indica el archivo y la clave de origen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from isot.helm import flatten_values
from isot.inventory import load_catalog, load_clusters, load_devices, load_templates, load_topology, parse_ref

OOB_DEFAULT_VLAN = 2002
# Claves que se muestran como columnas propias; el resto va a «otros atributos».
BOND_KEYS = {"name", "mode", "members", "leaf_pc", "allowed_vlans", "extra_host_vlans", "mtu"}
IFACE_KEYS = {"interface", "name", "parent", "vlan", "ip", "mac", "mtu", "connected_to", "network"}
OOB_KEYS = {"type", "ip", "switch", "port", "vlan", "undocumented_peer"}
HYPERVISOR_KEYS = {"device"}


@dataclass
class InventoryView:
    """Lo mínimo del inventario para describir conexiones (árbol de trabajo o snapshot)."""

    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    vlans: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    subnets: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    clusters: dict[str, dict[str, Any]] = field(default_factory=dict)
    templates: dict[str, dict[str, Any]] = field(default_factory=dict)
    sites: dict[str, dict[str, Any]] = field(default_factory=dict)


def _add_topology(view: InventoryView, site_id: str, topology: dict[str, Any]) -> None:
    vlans = view.vlans.setdefault(site_id, {})
    for vlan in topology.get("vlans") or []:
        if isinstance(vlan, dict) and vlan.get("id") is not None:
            vlans[str(vlan["id"])] = vlan
    subnets = view.subnets.setdefault(site_id, {})
    for subnet in topology.get("subnets") or []:
        if isinstance(subnet, dict) and subnet.get("name"):
            subnets[str(subnet["name"])] = subnet


def view_from_tree(root: Path) -> InventoryView:
    sites, _ = load_catalog(root)
    view = InventoryView()
    for site in sites:
        view.sites[site.id] = {"id": site.id, "name": site.name, "path": site.rel_path}
        _add_topology(view, site.id, load_topology(site))
    for device in load_devices(root, sites):
        view.devices[f"{device.site_id}:{device.hostname}"] = {
            "hostname": device.hostname,
            "site": device.site_id,
            "role": device.role,
            "kind": device.kind,
            "cluster_ref": device.cluster_ref,
            "path": device.rel_path,
            "data": device.data,
        }
    for doc in load_clusters(root, sites):
        name = str(doc.get("cluster_name") or doc.get("_stem"))
        site_id = str(doc.get("_site"))
        view.clusters[f"cluster:{site_id}:{name}"] = {
            "name": name,
            "site": site_id,
            "path": doc.get("_path"),
            "data": {k: v for k, v in doc.items() if not str(k).startswith("_")},
        }
    for doc in load_templates(root, sites):
        name = str(doc.get("template_name") or doc.get("_stem"))
        site_id = str(doc.get("_site") or "global")
        view.templates[f"template:{site_id}:{name}"] = {"name": name, "site": site_id, "path": doc.get("_path")}
    return view


def view_from_snapshot(snap: dict[str, Any]) -> InventoryView:
    """Mismo InventoryView desde un snapshot de `isot.gitdiff.snapshot_ref`."""
    view = InventoryView()
    entities = snap.get("entities") or {}
    index = {f"{item.get('site')}:{item.get('hostname')}": item for item in snap.get("devices") or []}
    for site in snap.get("sites") or []:
        view.sites[str(site.get("id"))] = {"id": site.get("id"), "name": site.get("name"), "path": site.get("path")}
    for entity in entities.values():
        kind = entity.get("kind")
        payload = entity.get("payload") or {}
        site_id = str(entity.get("site") or "")
        if kind == "device":
            entry = index.get(entity["id"], {})
            view.devices[entity["id"]] = {
                "hostname": entity.get("label"),
                "site": site_id,
                "role": payload.get("role"),
                "kind": entry.get("kind"),
                "cluster_ref": entry.get("cluster_ref", payload.get("cluster_ref")),
                "path": entry.get("path"),
                "data": payload,
            }
        elif kind == "network" and ":vlan:" in entity["id"]:
            view.vlans.setdefault(site_id, {})[str(payload.get("id"))] = payload
        elif kind == "network" and ":subnet:" in entity["id"]:
            view.subnets.setdefault(site_id, {})[str(payload.get("name"))] = payload
    for item in snap.get("clusters") or []:
        name, site_id = item.get("name"), item.get("site")
        entity = entities.get(f"{site_id}:{name}") or {}
        view.clusters[f"cluster:{site_id}:{name}"] = {
            "name": name,
            "site": site_id,
            "path": item.get("path"),
            "data": entity.get("payload") or {},
        }
    for item in snap.get("templates") or []:
        name, site_id = item.get("name"), item.get("site") or "global"
        view.templates[f"template:{site_id}:{name}"] = {"name": name, "site": site_id, "path": item.get("path")}
    return view


# ---------------------------------------------------------------------------
# Helpers


def _dump(fragment: Any) -> str:
    return yaml.safe_dump(fragment, allow_unicode=True, sort_keys=False, default_flow_style=None).strip()


def _extra(raw: dict[str, Any], known: set[str]) -> dict[str, Any]:
    return {str(k): v for k, v in raw.items() if k not in known and not str(k).startswith("_")}


def _ref_id(value: Any, default_site: str) -> str | None:
    parsed = parse_ref(None if value is None else str(value), default_site)
    return f"{parsed[0]}:{parsed[1]}" if parsed else None


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _endpoint(view: InventoryView, node_id: str) -> dict[str, Any]:
    if node_id.startswith("site:"):
        site = view.sites.get(node_id[5:], {})
        return {"id": node_id, "entity": "site", "label": site.get("name") or node_id[5:], "site": node_id[5:], "path": site.get("path")}
    if node_id.startswith("cluster:"):
        item = view.clusters.get(node_id, {})
        _, site, name = (node_id.split(":", 2) + ["", ""])[:3]
        return {
            "id": node_id,
            "entity": "cluster",
            "label": item.get("name") or name,
            "site": site,
            "role": (item.get("data") or {}).get("distro"),
            "path": item.get("path"),
        }
    if node_id.startswith("template:"):
        item = view.templates.get(node_id, {})
        _, site, name = (node_id.split(":", 2) + ["", ""])[:3]
        return {"id": node_id, "entity": "template", "label": item.get("name") or name, "site": site, "path": item.get("path")}
    device = view.devices.get(node_id)
    site, _, host = node_id.partition(":")
    if device is None:
        return {"id": node_id, "entity": "device", "label": host, "site": site, "hostname": host, "remote": True, "path": None}
    return {
        "id": node_id,
        "entity": "device",
        "label": device["hostname"],
        "hostname": device["hostname"],
        "site": device["site"],
        "role": device.get("role"),
        "kind": device.get("kind"),
        "cluster_ref": device.get("cluster_ref"),
        "path": device.get("path"),
        "remote": False,
        "proposed": bool(device.get("proposed")),
    }


def _vlan_info(view: InventoryView, site: str, vlan: Any, **extra: Any) -> dict[str, Any]:
    entry = view.vlans.get(site, {}).get(str(vlan), {})
    subnet_name = entry.get("subnet") or entry.get("name")
    subnet = view.subnets.get(site, {}).get(str(subnet_name), {}) if subnet_name else {}
    if not subnet:
        subnet = next((item for item in view.subnets.get(site, {}).values() if str(item.get("vlan")) == str(vlan)), {})
    info = {
        "id": vlan,
        "name": entry.get("name") or subnet.get("name"),
        "subnet": subnet.get("name"),
        "cidr": subnet.get("cidr") or None,
        "gateway": subnet.get("gateway") or None,
        "vrf": subnet.get("vrf") or None,
        "description": subnet.get("description") or None,
    }
    info.update(extra)
    return info


def _empty_side() -> dict[str, Any]:
    return {
        "interface": None,
        "port": None,
        "bond": None,
        "port_channel": None,
        "lacp": None,
        "members": None,
        "ip": None,
        "mtu": None,
        "device_mtu": None,
        "extra": {},
    }


def _peer_interface(device: dict[str, Any] | None, port: Any) -> tuple[int, dict[str, Any]] | None:
    if not device or port is None:
        return None
    for idx, raw in enumerate(device["data"].get("networking") or []):
        if isinstance(raw, dict) and str(raw.get("interface") or raw.get("name") or "") == str(port):
            return idx, raw
    return None


def _peer_side(view: InventoryView, peer_id: str, port: Any, sources: list[dict[str, Any]]) -> dict[str, Any]:
    side = _empty_side()
    peer = view.devices.get(peer_id)
    side["port"] = None if port is None else str(port)
    if port is not None and str(port).lower().startswith(("portchannel", "port-channel", "po")):
        side["port_channel"] = str(port)
    if peer:
        side["device_mtu"] = peer["data"].get("mtu")
        match = _peer_interface(peer, port)
        if match:
            idx, raw = match
            side["interface"] = raw.get("interface") or raw.get("name")
            side["ip"] = raw.get("ip")
            side["mtu"] = raw.get("mtu")
            side["extra"] = _extra(raw, IFACE_KEYS)
            sources.append({"path": peer.get("path"), "key": f"networking[{idx}]", "yaml": _dump(raw)})
    return side


def _device_links(view: InventoryView, owner_id: str, peer_id: str, owner_role: str) -> list[dict[str, Any]]:
    """Enlaces que `owner` declara hacia `peer`. owner_role: «source» o «target»."""
    owner = view.devices.get(owner_id)
    if not owner:
        return []
    data = owner["data"]
    site = owner["site"]
    peer_role = "target" if owner_role == "source" else "source"
    path = owner.get("path")
    links: list[dict[str, Any]] = []

    def over_bond(bond_name: str) -> list[tuple[int, dict[str, Any]]]:
        return [
            (idx, raw)
            for idx, raw in enumerate(data.get("networking") or [])
            if isinstance(raw, dict) and raw.get("parent") == bond_name
        ]

    bond_names: set[str] = set()
    for idx, bond in enumerate(data.get("bonds") or []):
        if not isinstance(bond, dict):
            continue
        leaf_pc = bond.get("leaf_pc") or {}
        if not isinstance(leaf_pc, dict):
            continue
        for switch, port in leaf_pc.items():
            if _ref_id(switch, site) != peer_id:
                continue
            name = str(bond.get("name") or f"bonds[{idx}]")
            bond_names.add(name)
            sources = [{"path": path, "key": f"bonds[{idx}]", "yaml": _dump(bond)}]
            own = _empty_side()
            own.update(
                bond=name,
                interface=name,
                lacp=bond.get("mode"),
                members=bond.get("members"),
                mtu=bond.get("mtu"),
                device_mtu=data.get("mtu"),
                extra=_extra(bond, BOND_KEYS),
            )
            ips = []
            vlans: list[dict[str, Any]] = []
            seen: set[str] = set()
            for iidx, iface in over_bond(name):
                sources.append({"path": path, "key": f"networking[{iidx}]", "yaml": _dump(iface)})
                if iface.get("ip"):
                    ips.append(f"{iface.get('interface')}: {iface.get('ip')}")
                if iface.get("vlan") is not None:
                    seen.add(str(iface["vlan"]))
                    vlans.append(
                        _vlan_info(
                            view,
                            site,
                            iface["vlan"],
                            interface=iface.get("interface"),
                            ip=iface.get("ip"),
                            allowed=iface["vlan"] in _list(bond.get("allowed_vlans")),
                        )
                    )
            for vlan in _list(bond.get("allowed_vlans")):
                if str(vlan) not in seen:
                    seen.add(str(vlan))
                    vlans.append(_vlan_info(view, site, vlan, interface=None, ip=None, allowed=True))
            for vlan in _list(bond.get("extra_host_vlans")):
                if str(vlan) not in seen:
                    seen.add(str(vlan))
                    vlans.append(_vlan_info(view, site, vlan, interface=None, ip=None, allowed=False, extra_host=True))
            own["ip"] = ", ".join(ips) or None
            peer = _peer_side(view, peer_id, port, sources)
            links.append(
                {
                    "type": "bond",
                    "label": f"{name} → {port}",
                    "declared_by": owner_role,
                    owner_role: own,
                    peer_role: peer,
                    "vlans": vlans,
                    "attributes": {"mtu": bond.get("mtu"), "lacp": bond.get("mode")},
                    "sources": sources,
                }
            )

    for idx, raw in enumerate(data.get("networking") or []):
        if not isinstance(raw, dict):
            continue
        connected = raw.get("connected_to") or {}
        if not isinstance(connected, dict) or not connected.get("device"):
            continue
        target = _ref_id(connected.get("device"), str(connected.get("site") or site))
        if target != peer_id:
            continue
        if raw.get("parent") in bond_names:
            continue  # ya está dentro del bond
        sources = [{"path": path, "key": f"networking[{idx}]", "yaml": _dump(raw)}]
        own = _empty_side()
        own.update(
            interface=raw.get("interface") or raw.get("name"),
            bond=raw.get("parent"),
            ip=raw.get("ip"),
            mtu=raw.get("mtu"),
            device_mtu=data.get("mtu"),
            extra=_extra(raw, IFACE_KEYS),
        )
        if str(own["interface"] or "").lower().startswith("portchannel"):
            own["port_channel"] = own["interface"]
        peer = _peer_side(view, peer_id, connected.get("port"), sources)
        vlans = (
            [_vlan_info(view, site, raw["vlan"], interface=own["interface"], ip=raw.get("ip"), network=raw.get("network"))]
            if raw.get("vlan") is not None
            else []
        )
        links.append(
            {
                "type": "interface",
                "label": f"{own['interface'] or '—'} → {connected.get('port') or '—'}",
                "declared_by": owner_role,
                owner_role: own,
                peer_role: peer,
                "vlans": vlans,
                "attributes": {"mtu": raw.get("mtu"), "lacp": None},
                "sources": sources,
            }
        )

    oob = data.get("oob") or {}
    if isinstance(oob, dict) and oob.get("switch") and not oob.get("undocumented_peer"):
        if _ref_id(oob["switch"], site) == peer_id:
            sources = [{"path": path, "key": "oob", "yaml": _dump(oob)}]
            own = _empty_side()
            own.update(interface=oob.get("type") or "oob", ip=oob.get("ip"), extra=_extra(oob, OOB_KEYS))
            peer = _peer_side(view, peer_id, oob.get("port"), sources)
            vlan = oob.get("vlan")
            vlans = [
                _vlan_info(view, site, vlan if vlan is not None else OOB_DEFAULT_VLAN, interface=own["interface"], ip=oob.get("ip"), implicit=vlan is None)
            ]
            links.append(
                {
                    "type": "oob",
                    "label": f"OOB {own['interface']} → {oob.get('port') or '—'}",
                    "declared_by": owner_role,
                    owner_role: own,
                    peer_role: peer,
                    "vlans": vlans,
                    "attributes": {"mtu": None, "lacp": None},
                    "sources": sources,
                }
            )

    hypervisor = data.get("hypervisor") or {}
    if isinstance(hypervisor, dict) and hypervisor.get("device") and _ref_id(hypervisor["device"], site) == peer_id:
        own = _empty_side()
        own["extra"] = _extra(hypervisor, HYPERVISOR_KEYS)
        links.append(
            {
                "type": "hypervisor",
                "label": "VM sobre hypervisor",
                "declared_by": owner_role,
                owner_role: own,
                peer_role: _empty_side(),
                "vlans": [],
                "attributes": {"mtu": None, "lacp": None},
                "sources": [{"path": path, "key": "hypervisor", "yaml": _dump(hypervisor)}],
            }
        )
    return links


def _reference_link(view: InventoryView, source: str, target: str) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    if source.startswith("template:") and target.startswith("cluster:"):
        cluster = view.clusters.get(target, {})
        ref = (cluster.get("data") or {}).get("template_ref")
        if ref is not None:
            links.append(
                {
                    "type": "reference",
                    "label": f"template_ref: {ref}",
                    "declared_by": "target",
                    "source": _empty_side(),
                    "target": _empty_side(),
                    "vlans": [],
                    "attributes": {"field": "template_ref", "value": ref},
                    "sources": [{"path": cluster.get("path"), "key": "template_ref", "yaml": _dump({"template_ref": ref})}],
                }
            )
    elif source.startswith("cluster:"):
        device = view.devices.get(target)
        if device:
            data = device["data"]
            fragment = {key: data[key] for key in ("cluster_ref", "k8s_roles") if key in data}
            links.append(
                {
                    "type": "reference",
                    "label": f"cluster_ref: {data.get('cluster_ref')}",
                    "declared_by": "target",
                    "source": _empty_side(),
                    "target": _empty_side(),
                    "vlans": [],
                    "attributes": {"field": "cluster_ref", "value": data.get("cluster_ref"), "k8s_roles": data.get("k8s_roles")},
                    "sources": [{"path": device.get("path"), "key": "cluster_ref", "yaml": _dump(fragment)}],
                }
            )
    return links


def _site_links(view: InventoryView, source: str, target: str) -> list[dict[str, Any]]:
    a, b = source[5:], target[5:]
    out: list[dict[str, Any]] = []
    pairs: set[tuple[str, str]] = set()
    for dev_id, device in view.devices.items():
        if device["site"] not in (a, b):
            continue
        other = b if device["site"] == a else a
        for peer_id in _declared_peers(device):
            if peer_id.split(":", 1)[0] != other:
                continue
            key = tuple(sorted((dev_id, peer_id)))
            if key in pairs:
                continue
            pairs.add(key)  # type: ignore[arg-type]
            left, right = (dev_id, peer_id) if device["site"] == a else (peer_id, dev_id)
            for link in _device_links(view, left, right, "source") + _device_links(view, right, left, "target"):
                link["pair"] = {"source": _endpoint(view, left), "target": _endpoint(view, right)}
                out.append(link)
    return out


def _declared_peers(device: dict[str, Any]) -> list[str]:
    data, site = device["data"], device["site"]
    peers: list[str] = []
    for bond in data.get("bonds") or []:
        if isinstance(bond, dict) and isinstance(bond.get("leaf_pc"), dict):
            peers.extend(filter(None, (_ref_id(sw, site) for sw in bond["leaf_pc"])))
    for raw in data.get("networking") or []:
        connected = raw.get("connected_to") if isinstance(raw, dict) else None
        if isinstance(connected, dict) and connected.get("device"):
            peers.append(_ref_id(connected["device"], str(connected.get("site") or site)) or "")
    oob = data.get("oob") or {}
    if isinstance(oob, dict) and oob.get("switch"):
        peers.append(_ref_id(oob["switch"], site) or "")
    hypervisor = data.get("hypervisor") or {}
    if isinstance(hypervisor, dict) and hypervisor.get("device"):
        peers.append(_ref_id(hypervisor["device"], site) or "")
    return [peer for peer in dict.fromkeys(peers) if peer]


def link_details(view: InventoryView, source: str, target: str) -> dict[str, Any] | None:
    """Detalle de la arista source–target, o None si el inventario no la declara."""
    if source.startswith("site:") and target.startswith("site:"):
        kind, links = "dci", _site_links(view, source, target)
    elif source.startswith(("template:", "cluster:")) or target.startswith(("template:", "cluster:")):
        a, b = (source, target) if source.startswith(("template:", "cluster:")) else (target, source)
        if a.startswith("cluster:") and b.startswith("template:"):
            a, b = b, a
        source, target = a, b
        kind, links = "kubernetes", _reference_link(view, source, target)
    else:
        kind = "network"
        links = _device_links(view, source, target, "source") + _device_links(view, target, source, "target")
    if not links:
        return None
    vlans: dict[str, dict[str, Any]] = {}
    for link in links:
        for vlan in link["vlans"]:
            vlans.setdefault(str(vlan["id"]), {k: vlan[k] for k in ("id", "name", "subnet", "cidr", "gateway", "vrf", "description")})
    return {
        "kind": kind,
        "source": _endpoint(view, source),
        "target": _endpoint(view, target),
        "links": links,
        "vlans": sorted(vlans.values(), key=lambda item: str(item["id"])),
        "ports": sorted(
            {
                str(port)
                for link in links
                for side in ("source", "target")
                for port in (link.get(side, {}).get("port"), link.get(side, {}).get("interface"), link.get(side, {}).get("bond"))
                if port
            }
        ),
        "paths": sorted({src["path"] for link in links for src in link["sources"] if src.get("path")}),
    }


def comparable(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Forma estable para el diff: enlaces por etiqueta, sin fragmentos YAML."""
    if not detail:
        return {}
    out: dict[str, Any] = {}
    for link in detail["links"]:
        pair = link.get("pair")
        prefix = f"{pair['source']['label']}–{pair['target']['label']} " if pair else ""
        key = f"{prefix}{link['type']} {link['label']} ({link['declared_by']})"
        out[key] = {
            "source": {k: v for k, v in link["source"].items() if v not in (None, {}, [])},
            "target": {k: v for k, v in link["target"].items() if v not in (None, {}, [])},
            "vlans": {str(v["id"]): {k: v[k] for k in ("name", "cidr", "interface", "ip") if v.get(k) is not None} for v in link["vlans"]},
            "attributes": {k: v for k, v in link["attributes"].items() if v is not None},
        }
    return out


def diff_details(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    left, right = comparable(before), comparable(after)
    left_map, right_map = dict(flatten_values(left)), dict(flatten_values(right))
    changes: list[dict[str, Any]] = []
    for path in sorted(set(left_map) | set(right_map)):
        a, b = left_map.get(path), right_map.get(path)
        if path in left_map and path in right_map and a == b:
            continue
        status = "removed" if path not in right_map else "added" if path not in left_map else "modified"
        changes.append({"path": path, "before": a, "after": b, "status": status})
    if before is None and after is None:
        status = "missing"
    elif before is None:
        status = "added"
    elif after is None:
        status = "removed"
    else:
        status = "modified" if changes else "unchanged"
    return {"status": status, "changes": changes}


DEVICE_PATH = re.compile(r"^sites/([A-Za-z0-9._-]+)/devices/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)\.ya?ml$")
TOPOLOGY_PATH = re.compile(r"^sites/([A-Za-z0-9._-]+)/topology/index\.ya?ml$")
MAX_OVERLAY_FILES = 400
MAX_OVERLAY_BYTES = 256_000


def overlay_files(view: InventoryView, files: list[dict[str, Any]]) -> InventoryView:
    """Aplica archivos propuestos (vista previa del wizard) sobre la vista.

    Solo entiende YAML de devices y topology; el resto se ignora. Ningún
    archivo se escribe: es una vista en memoria.
    """
    for item in files[:MAX_OVERLAY_FILES]:
        path = str(item.get("path") or "")
        content = item.get("content")
        if not isinstance(content, str) or len(content) > MAX_OVERLAY_BYTES:
            continue
        try:
            data = yaml.safe_load(content) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(data, dict):
            continue
        match = DEVICE_PATH.fullmatch(path)
        if match:
            site_id, kind, stem = match.groups()
            hostname = str(data.get("hostname") or stem)
            view.devices[f"{site_id}:{hostname}"] = {
                "hostname": hostname,
                "site": site_id,
                "role": data.get("role"),
                "kind": kind,
                "cluster_ref": data.get("cluster_ref"),
                "path": path,
                "data": data,
                "proposed": True,
            }
            view.sites.setdefault(site_id, {"id": site_id, "name": site_id.upper(), "path": f"sites/{site_id}"})
            continue
        match = TOPOLOGY_PATH.fullmatch(path)
        if match:
            site_id = match.group(1)
            view.vlans.pop(site_id, None)
            view.subnets.pop(site_id, None)
            _add_topology(view, site_id, data)
            view.sites.setdefault(site_id, {"id": site_id, "name": site_id.upper(), "path": f"sites/{site_id}"})
    return view
