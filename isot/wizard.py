"""Wizards de edición: preview en memoria y commit en rama wizard/<slug>.

No escribe en main ni en la rama actual. Si el validador falla, se
descartan los cambios y se borra la rama nueva.
"""

from __future__ import annotations

import copy
import difflib
import ipaddress
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

import yaml

from isot.allocator import (
    AllocatorError,
    AutoField,
    DeviceDraft,
    convention_prefix,
    hostname_role_warning,
    next_hostname,
    next_ip,
    next_port,
    next_port_bundle,
    parse_port,
    reserved_from_clusters,
    reserved_from_topology,
    same_role_family,
    subnet_for_vlan,
    token_for_role,
    token_from_hostname,
    used_ips_from_devices,
    used_ports_by_peer,
)
from isot.cabling import (
    COMPUTE_ROLES,
    build_compute,
    build_fabric_devices,
    load_or_infer,
    mgmt_name,
    pick_pair,
    policy_block,
    policy_from_site_body,
    role_matches,
    retarget_leafs,
)
from isot.gitdiff import diff_from_snapshots, git, is_git_toplevel, snapshot_inventory
from isot.indexer import write_indexes
from isot.inventory import (
    find_global_template,
    is_library_rel,
    library_template_files,
    load_catalog,
    load_clusters,
    load_devices,
    load_templates,
    load_topology,
    load_yaml,
    rel_path,
    site_by_id,
    template_dir,
    template_stem,
)
from isot.paths import bundled_templates_dir

PROTECTED_BRANCHES = {"main", "master", "HEAD"}
SITE_TEMPLATE_PREFIX = "plantilla:"
SITE_TEMPLATES_DIRNAME = "site-templates"
KIND_DIRS = {"baremetal": "baremetal", "vm": "virtual-machines", "network": "network"}
SAFE_ID = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


class WizardError(ValueError):
    pass


def edit_enabled(flag: bool | None = None) -> bool:
    if flag is not None:
        return bool(flag)
    return os.environ.get("ISOT_EDIT", "").strip().lower() in {"1", "true", "yes", "on"}


def dump_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


def unified_diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def slugify(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    ascii_text = "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()
    ascii_text = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return (ascii_text or "cambio")[:48]


def current_branch(repo: Path) -> str:
    proc = git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    if proc.returncode != 0:
        raise WizardError("no se pudo leer la rama actual")
    name = proc.stdout.decode("utf-8").strip()
    return name or "HEAD"


def _device_docs(repo: Path, site_id: str) -> list[dict[str, Any]]:
    sites, _ = load_catalog(repo)
    docs: list[dict[str, Any]] = []
    for device in load_devices(repo, [s for s in sites if s.id == site_id] or sites):
        if device.site_id != site_id:
            continue
        data = copy.deepcopy(device.data)
        data["_rel_path"] = device.rel_path
        data["_kind"] = device.kind
        docs.append(data)
    return docs


def _cluster_docs(repo: Path, site_id: str) -> list[dict[str, Any]]:
    sites, _ = load_catalog(repo)
    return [doc for doc in load_clusters(repo, sites) if doc.get("_site") == site_id]


def _kind_of(doc: dict[str, Any]) -> str:
    return str(doc.get("_kind") or doc.get("kind") or "")


def _same_kind(doc: dict[str, Any], kind: str) -> bool:
    kind_dir = KIND_DIRS.get(kind, kind)
    return _kind_of(doc) == kind_dir or str(doc.get("kind") or "") == kind


def _role_siblings(
    devices: list[dict[str, Any]],
    *,
    cluster: str | None,
    role: str | None,
    kind: str,
) -> list[dict[str, Any]]:
    """Hermanos del mismo rol y tipo. Nunca mezcla prefijos de otro rol."""
    same_kind = [doc for doc in devices if _same_kind(doc, kind)]
    pool = same_kind or list(devices)
    if role:
        pool = [doc for doc in pool if same_role_family(role, str(doc.get("role") or ""))]
        if not pool:
            return []
    if cluster:
        in_cluster = [doc for doc in pool if doc.get("cluster_ref") == cluster]
        if in_cluster:
            return sorted(in_cluster, key=lambda doc: str(doc.get("hostname") or ""))
    return sorted(pool, key=lambda doc: str(doc.get("hostname") or ""))


def _foreign_role_sibling(
    repo: Path,
    *,
    site_id: str,
    role: str | None,
    kind: str,
) -> dict[str, Any] | None:
    """Patrón de hostname del mismo rol en otro sitio."""
    if not role:
        return None
    sites, _ = load_catalog(repo)
    others = [item for item in sites if item.id != site_id]
    if not others:
        return None
    found: list[dict[str, Any]] = []
    for device in load_devices(repo, others):
        data = copy.deepcopy(device.data)
        data["_kind"] = device.kind
        data["_site"] = device.site_id
        if _same_kind(data, kind) and same_role_family(role, str(data.get("role") or "")):
            found.append(data)
    if not found:
        return None
    return sorted(found, key=lambda doc: str(doc.get("hostname") or ""))[-1]


def _similar_device(
    devices: list[dict[str, Any]],
    *,
    cluster: str | None,
    kind: str,
) -> dict[str, Any] | None:
    """Último recurso de specs: device del clúster más parecido (mismo tipo)."""
    same_kind = [doc for doc in devices if _same_kind(doc, kind)]
    pool = same_kind or list(devices)
    if cluster:
        clustered = [doc for doc in pool if doc.get("cluster_ref") == cluster]
        if clustered:
            pool = clustered
    if not pool:
        return None
    return sorted(pool, key=lambda doc: str(doc.get("hostname") or ""))[-1]


def _k8s_roles_for(role: str, sibling: dict[str, Any] | None = None) -> list[str]:
    if role in {"worker", "apps-worker", "k8s-worker"}:
        return ["worker"]
    if sibling:
        existing = sibling.get("k8s_roles")
        if isinstance(existing, list) and existing:
            return [str(item) for item in existing]
    if role:
        return [role]
    return []


def _prepare_network_device(
    data: dict[str, Any], devices: list[dict[str, Any]], used_ips: set[str], source: str
) -> list[str]:
    """Limpia un equipo de red copiado de un hermano.

    - Quita interfaces conectadas a equipos que no son de red (servidores): el
      leaf nuevo nace sin servidores; se conectan después.
    - Reasigna IPs sin VLAN (loopback) a la siguiente libre de su /24, y
      ``bgp.router_id`` si era la loopback heredada.
    """
    network_hosts = {
        str(doc.get("hostname") or "")
        for doc in devices
        if str(doc.get("kind") or doc.get("_kind") or "").lower() == "network"
    }
    kept: list[Any] = []
    dropped = 0
    renamed: dict[str, str] = {}
    for iface in data.get("networking") or []:
        if not isinstance(iface, dict):
            kept.append(iface)
            continue
        connected = iface.get("connected_to")
        if isinstance(connected, dict) and connected.get("device") and str(connected["device"]) not in network_hosts:
            dropped += 1
            continue
        if iface.get("ip") and iface.get("vlan") is None:
            raw = str(iface["ip"])
            addr, _, prefix = raw.partition("/")
            try:
                net = ipaddress.ip_network(f"{addr}/24", strict=False)
                plan = next_ip(str(net), used_ips, reserved=(), after=addr)
            except (AllocatorError, ValueError):
                plan = None
            if plan is not None:
                new_addr = plan.value.split("/", 1)[0]
                iface["ip"] = f"{new_addr}/{prefix}" if prefix else new_addr
                used_ips.add(new_addr)
                renamed[addr] = new_addr
        kept.append(iface)
    data["networking"] = kept
    bgp = data.get("bgp")
    if isinstance(bgp, dict) and str(bgp.get("router_id") or "") in renamed:
        bgp["router_id"] = renamed[str(bgp["router_id"])]
    notes: list[str] = []
    if dropped:
        notes.append(
            f"equipo de red nuevo sin servidores: se omitieron {dropped} enlace(s) heredados de {source}; "
            "conecta los servidores después"
        )
    return notes


def allocate_devices(
    repo: Path,
    *,
    site_id: str,
    kind: str,
    cluster: str | None,
    role: str | None,
    count: int = 1,
    overrides: dict[str, Any] | None = None,
) -> tuple[list[DeviceDraft], list[dict[str, Any]], dict[str, Any]]:
    if kind not in KIND_DIRS:
        raise WizardError(f"tipo no soportado: {kind}")
    if count < 1 or count > 32:
        raise WizardError("count debe estar entre 1 y 32")
    sites, _ = load_catalog(repo)
    site = next((item for item in sites if item.id == site_id), None)
    if site is None:
        raise WizardError(f"sitio desconocido: {site_id}")
    topology = load_topology(site)
    devices = _device_docs(repo, site_id)
    clusters = _cluster_docs(repo, site_id)
    policy = load_or_infer(topology, devices)
    local_siblings = _role_siblings(devices, cluster=cluster, role=role, kind=kind)
    pattern_note: str | None = None
    pattern_source = "sibling"
    sibling: dict[str, Any] | None = local_siblings[-1] if local_siblings else None
    sibling_host = str(sibling.get("hostname") or "") if sibling else None
    hostname_prefix: str | None = None
    hostname_width: int | None = None
    if sibling_host:
        pattern_source = "cluster" if sibling and sibling.get("cluster_ref") == cluster else "site"
    else:
        pattern_note = "sin hermanos del mismo rol: patrón nuevo"
        foreign = _foreign_role_sibling(repo, site_id=site_id, role=role, kind=kind)
        token = token_from_hostname(str(foreign.get("hostname") or "")) if foreign else token_for_role(role)
        if foreign and token:
            pattern_source = "other-site"
        else:
            pattern_source = "convention"
            token = token or "NODE"
        hostname_prefix = convention_prefix(site.name, role, token)
        hostname_width = 3
        sibling = None
    spec_source = sibling
    used_hosts = [str(doc.get("hostname") or "") for doc in devices]
    used_ips = used_ips_from_devices(devices) | reserved_from_topology(topology) | reserved_from_clusters(clusters)
    used_ports = used_ports_by_peer(devices)
    drafts: list[DeviceDraft] = []
    extra_cluster_nodes: list[dict[str, Any]] = []
    overrides = overrides or {}
    similar = None if sibling else _similar_device(devices, cluster=cluster, kind=kind)
    spec_label = f"hermano {sibling_host}" if sibling_host else (
        f"device más parecido {similar.get('hostname')} (sin hostname ni rol)" if similar else "connection_rules"
    )

    for index in range(count):
        if sibling_host:
            plan = next_hostname(used_hosts, sibling=sibling_host)
        else:
            plan = next_hostname(used_hosts, prefix=hostname_prefix, width=hostname_width)
        hostname = str(overrides.get("hostname") or plan.value) if index == 0 and overrides.get("hostname") else plan.value
        if hostname in used_hosts:
            raise WizardError(f"hostname {hostname} ya existe")
        host_origin = plan.origin
        if pattern_note:
            host_origin = f"{pattern_note} ({host_origin})"
        warnings = []
        mismatch = hostname_role_warning(hostname, role)
        if mismatch:
            warnings.append(mismatch)

        pair = pick_pair(policy, devices, rr_index=index) if role_matches(role, list(COMPUTE_ROLES), [role] if role else []) else None
        built_from_rules = False
        if spec_source:
            data = copy.deepcopy(spec_source)
            data.pop("_rel_path", None)
            data.pop("_kind", None)
            data.pop("_site", None)
        elif (
            not sibling
            and pair
            and len(pair.get("leafs") or []) >= 2
            and kind == "baremetal"
        ):
            try:
                data = build_compute(
                    hostname,
                    pair=pair,
                    policy=policy,
                    topology=topology,
                    used_ips=used_ips,
                    used_ports=used_ports,
                    cluster=cluster,
                    mgmt_switch=mgmt_name(devices, site.name),
                    role=role or "worker",
                )
                built_from_rules = True
                spec_label = "connection_rules / template del clúster"
            except (AllocatorError, ValueError, WizardError):
                data = {}
        else:
            data = {}
        if not data and similar:
            data = copy.deepcopy(similar)
            data.pop("_rel_path", None)
            data.pop("_kind", None)
            data.pop("_site", None)
            built_from_rules = False
        if not data:
            raise WizardError("no hay un dispositivo hermano del que heredar ni reglas para generar el alta")
        data["hostname"] = hostname
        if role:
            data["role"] = role
        if cluster:
            data["cluster_ref"] = cluster
        data["kind"] = kind if not spec_source else (spec_source.get("kind") or kind)
        data["vendor"] = "generic"
        if not data.get("model"):
            data["model"] = "compute-2u" if kind == "baremetal" else data.get("model")
        if kind == "network":
            # Un equipo de red no es nodo Kubernetes ni hereda los enlaces a servidores del hermano.
            data.pop("k8s_roles", None)
            data.pop("k8s_name", None)
            if not cluster:
                data.pop("cluster_ref", None)
            warnings.extend(_prepare_network_device(data, devices, used_ips, sibling_host or spec_label))
        else:
            data["k8s_roles"] = _k8s_roles_for(role or "", spec_source)
            data["k8s_name"] = hostname.lower()
        role_origin = f"heredado de {sibling_host}" if sibling_host else f"solicitado; {pattern_note}"
        spec_origin = f"heredado de {sibling_host}" if sibling_host else spec_label
        fields = [
            AutoField("hostname", hostname, hostname == plan.value, host_origin),
            AutoField("cluster_ref", data.get("cluster_ref"), True, spec_origin),
            AutoField("role", data.get("role"), True, role_origin),
            AutoField("vendor", data.get("vendor"), True, spec_origin),
            AutoField("model", data.get("model"), True, spec_origin),
        ]
        if not built_from_rules and role_matches(str(data.get("role")), list(COMPUTE_ROLES), data.get("k8s_roles") or []):
            pair = pair or pick_pair(policy, devices, rr_index=index)
            if pair and pair.get("leafs"):
                mapping = retarget_leafs(data, list(pair["leafs"]))
                data["leaf_pair"] = pair.get("id")
                origin_pair = (
                    f"política {policy.get('pair_policy')} → {pair.get('id')} "
                    f"({'/'.join(pair.get('leafs') or [])})"
                )
                if mapping and any(src != dst for src, dst in mapping.items()) and sibling_host:
                    origin_pair += f"; retarget desde {sibling_host}"
                fields.append(AutoField("leaf_pair", pair.get("id"), True, origin_pair))

        oob = data.get("oob")
        if not built_from_rules and isinstance(oob, dict) and oob.get("ip"):
            oob_ip = str(oob["ip"])
            oob_cidr = oob_ip if "/" in oob_ip else f"{oob_ip.split('/')[0]}/24"
            try:
                oob_subnet = subnet_for_vlan(topology, 2002) or {}
                cidr = str(oob_subnet.get("cidr") or oob_cidr)
                ip_plan = next_ip(cidr, used_ips, reserved=(), after=oob_ip)
                oob["ip"] = ip_plan.value
                used_ips.add(ip_plan.value.split("/", 1)[0])
                fields.append(AutoField("oob.ip", oob["ip"], True, f"{ip_plan.origin}; oob de {spec_label}"))
            except AllocatorError as exc:
                raise WizardError(str(exc)) from exc
            if oob.get("switch") and oob.get("port"):
                switch = str(oob["switch"])
                try:
                    port_plan = next_port(used_ports.get(switch, set()), str(oob["port"]))
                    oob["port"] = port_plan.value
                    used_ports.setdefault(switch, set()).add(port_plan.value)
                    fields.append(
                        AutoField(
                            "oob.port",
                            oob["port"],
                            True,
                            f"{port_plan.origin} en {switch}",
                        )
                    )
                except AllocatorError:
                    pass

        bonds = [item for item in (data.get("bonds") or []) if isinstance(item, dict)]
        grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        if built_from_rules:
            bonds = []
        for bond in bonds:
            leaf_pc = bond.get("leaf_pc")
            if not isinstance(leaf_pc, dict) or not leaf_pc:
                continue
            peers = tuple(sorted(str(item) for item in leaf_pc.keys()))
            grouped.setdefault(peers, []).append(bond)
        for peers, group in grouped.items():
            sibling_ports = [str(next(iter(item["leaf_pc"].values()))) for item in group]
            try:
                delta = next_port_bundle(used_ports, list(peers), sibling_ports)
            except AllocatorError as exc:
                raise WizardError(str(exc)) from exc
            for bond in group:
                sample = str(next(iter(bond["leaf_pc"].values())))
                parts = parse_port(sample)
                if parts is None:
                    continue
                prefix, number, width = parts
                new_port = f"{prefix}{number + delta:0{width}d}"
                bond["leaf_pc"] = {peer: new_port for peer in peers}
                for peer in peers:
                    used_ports.setdefault(peer, set()).add(new_port)
                fields.append(
                    AutoField(
                        f"bonds.{bond.get('name')}.leaf_pc",
                        new_port,
                        True,
                        f"siguiente bloque libre (+{delta}) en {'/'.join(peers)}; {spec_label}",
                    )
                )

        ctrl_ip = None
        if built_from_rules:
            for iface in data.get("networking") or []:
                if isinstance(iface, dict) and str(iface.get("interface") or "") in {"APP_CTRL", "MGMT_SW", "EDGE_SVC"}:
                    ip_val = str(iface.get("ip") or "").split("/", 1)[0]
                    if ip_val:
                        ctrl_ip = ip_val
                        if str(iface.get("interface")) == "APP_CTRL":
                            break
        for iface in [] if built_from_rules else (data.get("networking") or []):
            if not isinstance(iface, dict):
                continue
            name = str(iface.get("interface") or iface.get("name") or "")
            vlan = iface.get("vlan")
            subnet = subnet_for_vlan(topology, vlan) if vlan is not None else None
            cidr = (subnet or {}).get("cidr")
            if iface.get("ip") and cidr:
                forced = None
                if index == 0 and isinstance(overrides.get("ips"), dict):
                    forced = overrides["ips"].get(name)
                if forced:
                    iface["ip"] = str(forced)
                    origin_ip = "ajuste manual"
                    auto_ip = False
                else:
                    ip_plan = next_ip(str(cidr), used_ips, reserved=(), after=str(iface["ip"]))
                    iface["ip"] = ip_plan.value
                    origin_ip = f"{ip_plan.origin} (VLAN {vlan}; {spec_label})"
                    auto_ip = True
                used_ips.add(str(iface["ip"]).split("/", 1)[0])
                if name in {"APP_CTRL", "MGMT_SW", "EDGE_SVC"} and ctrl_ip is None:
                    ctrl_ip = str(iface["ip"]).split("/", 1)[0]
                if name == "APP_CTRL":
                    ctrl_ip = str(iface["ip"]).split("/", 1)[0]
                fields.append(
                    AutoField(
                        f"networking.{name}.ip",
                        iface["ip"],
                        auto_ip,
                        origin_ip,
                    )
                )
            connected = iface.get("connected_to")
            if isinstance(connected, dict) and connected.get("device") and connected.get("port"):
                peer = str(connected["device"])
                parent = str(iface.get("parent") or "")
                leaf_match = None
                origin_port = ""
                for bond in data.get("bonds") or []:
                    if not isinstance(bond, dict) or not isinstance(bond.get("leaf_pc"), dict):
                        continue
                    if parent and bond.get("name") != parent:
                        continue
                    if peer in bond["leaf_pc"]:
                        leaf_match = bond["leaf_pc"][peer]
                        origin_port = f"mismo PortChannel que {parent or 'el bond'} hacia {peer}"
                        break
                if leaf_match:
                    connected["port"] = leaf_match
                else:
                    try:
                        port_plan = next_port(used_ports.get(peer, set()), str(connected["port"]))
                        connected["port"] = port_plan.value
                        used_ports.setdefault(peer, set()).add(port_plan.value)
                        origin_port = f"{port_plan.origin} en {peer}"
                    except AllocatorError:
                        origin_port = ""
                if origin_port:
                    fields.append(
                        AutoField(
                            f"networking.{name}.connected_to.port",
                            connected["port"],
                            True,
                            origin_port,
                        )
                    )

        used_hosts.append(hostname)
        rel = f"sites/{site_id}/devices/{KIND_DIRS[kind]}/{hostname}.yaml"
        drafts.append(
            DeviceDraft(
                hostname=hostname,
                kind=kind,
                rel_path=rel,
                data=data,
                fields=fields,
                sibling=sibling_host,
                pattern_source=pattern_source,
                pattern_note=pattern_note,
                warnings=warnings,
            )
        )
        extra_cluster_nodes.append(
            {
                "hostname": hostname,
                "k8s_name": hostname.lower(),
                "roles": data.get("k8s_roles") or ["worker"],
                "internal_ip": ctrl_ip or "",
                "sriov_capable": bool(((spec_source or similar or {}).get("labels") or {}).get("feature.node.kubernetes.io/network-sriov.capable")),
            }
        )
        devices.append(data)
    return drafts, extra_cluster_nodes, policy


def _set_nested_field(data: dict[str, Any], path: str, value: Any) -> None:
    parts = [item for item in str(path).split(".") if item]
    if not parts:
        return
    if len(parts) == 1:
        data[parts[0]] = value
        return
    if parts[0] == "oob" and len(parts) == 2:
        data.setdefault("oob", {})
        if isinstance(data["oob"], dict):
            data["oob"][parts[1]] = value
        return
    if parts[0] == "bonds" and len(parts) >= 3:
        name = parts[1]
        for bond in data.get("bonds") or []:
            if isinstance(bond, dict) and bond.get("name") == name:
                if parts[2] == "leaf_pc":
                    current = bond.get("leaf_pc") if isinstance(bond.get("leaf_pc"), dict) else {}
                    bond["leaf_pc"] = {peer: str(value) for peer in current}
                break
        return
    if parts[0] == "networking" and len(parts) >= 3:
        iface_name = parts[1]
        for iface in data.get("networking") or []:
            if not isinstance(iface, dict):
                continue
            if str(iface.get("interface") or iface.get("name") or "") != iface_name:
                continue
            rest = parts[2:]
            if rest == ["ip"]:
                iface["ip"] = str(value)
            elif rest[:1] == ["connected_to"]:
                connected = iface.setdefault("connected_to", {})
                if isinstance(connected, dict) and len(rest) >= 2:
                    connected[rest[1]] = value
            break


def _parse_edit_key(key: str) -> tuple[str | None, str]:
    text = str(key)
    if "::" in text:
        host, path = text.split("::", 1)
        return host or None, path
    return None, text


def apply_field_edits(preview: dict[str, Any], edits: dict[str, Any] | None) -> None:
    if not edits:
        return
    by_host: dict[str | None, dict[str, Any]] = {}
    for raw_key, value in edits.items():
        host, path = _parse_edit_key(str(raw_key))
        by_host.setdefault(host, {})[path] = value
    for item in preview.get("files") or []:
        rel = str(item.get("path") or "")
        if "/devices/" not in rel or item.get("action") not in {"create", "modify"}:
            continue
        data = yaml.safe_load(item.get("content") or "") or {}
        if not isinstance(data, dict):
            continue
        hostname = str(data.get("hostname") or Path(rel).stem)
        patch = dict(by_host.get(None) or {})
        patch.update(by_host.get(hostname) or {})
        if not patch:
            continue
        for path, value in patch.items():
            _set_nested_field(data, path, value)
        new_host = str(data.get("hostname") or hostname)
        role = str(data.get("role") or preview.get("role") or "")
        mismatch = hostname_role_warning(new_host, role)
        if mismatch:
            warns = list(preview.get("warnings") or [])
            if mismatch not in warns:
                warns.append(mismatch)
            preview["warnings"] = warns
        if new_host != hostname:
            parent = rel.rsplit("/", 1)[0]
            rel = f"{parent}/{new_host}.yaml"
            item["path"] = rel
            hostnames = list(preview.get("hostnames") or [])
            preview["hostnames"] = [new_host if host == hostname else host for host in hostnames]
        before = "" if item.get("action") == "create" else (item.get("diff") or "")
        item["content"] = dump_yaml(data)
        item["diff"] = unified_diff(rel, "" if item.get("action") == "create" else before, item["content"])
    for field in preview.get("fields") or []:
        host = field.get("hostname")
        path = str(field.get("path") or "")
        keys = [path]
        if host:
            keys.append(f"{host}::{path}")
        for key in keys:
            if key in edits:
                field["value"] = edits[key]
                field["auto"] = False
                field["origin"] = "ajuste manual"


def _copy_inventory(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    catalog = src / "sites.yaml"
    if catalog.is_file():
        shutil.copy2(catalog, dest / "sites.yaml")
    for name in ("sites", "cluster-templates"):
        folder = src / name
        if folder.is_dir():
            shutil.copytree(
                folder,
                dest / name,
                ignore=shutil.ignore_patterns("*_index.json", "__pycache__", "*.pyc"),
                dirs_exist_ok=True,
            )


def _paint_touched(graph: dict[str, Any]) -> None:
    touched: set[str] = set()
    for edge in graph.get("edges") or []:
        if edge.get("diff_status") in {"added", "modified"}:
            touched.add(str(edge.get("source") or ""))
            touched.add(str(edge.get("target") or ""))
    for node in graph.get("nodes") or []:
        status = node.get("diff_status")
        if status in (None, "unchanged") and str(node.get("id") or "") in touched:
            node["diff_status"] = "modified"


def attach_preview_graph(repo: Path, preview: dict[str, Any], site: str | None) -> dict[str, Any]:
    """Inventario propuesto en un overlay temporal + diff semántico (sin git write)."""
    files = preview.get("files") or []
    with tempfile.TemporaryDirectory(prefix="isot-wiz-") as tmp:
        dest = Path(tmp)
        _copy_inventory(repo, dest)
        apply_files(dest, files)
        base = snapshot_inventory(repo)
        head = snapshot_inventory(dest)
        scoped = diff_from_snapshots(
            base,
            head,
            site,
            base_meta={"ref": "working", "sha": "", "short": "working"},
            head_meta={"ref": "preview", "sha": "", "short": "preview"},
        )
        graphs = dict(scoped.get("graphs") or {})
        if preview.get("kind") == "site":
            full = diff_from_snapshots(
                base,
                head,
                None,
                base_meta={"ref": "working", "sha": "", "short": "working"},
                head_meta={"ref": "preview", "sha": "", "short": "preview"},
            )
            graphs["global"] = full.get("graphs", {}).get("global") or {"nodes": [], "edges": [], "view": "global"}
            preview["summary"] = full.get("summary")
        else:
            preview["summary"] = scoped.get("summary")
        for graph in graphs.values():
            if isinstance(graph, dict):
                _paint_touched(graph)
        preview["graphs"] = graphs
        sites, _ = load_catalog(dest)
        chosen = site_by_id(sites, site) if site else None
        preview["topology"] = load_topology(chosen) if chosen else {}
        hostnames = [str(item) for item in (preview.get("hostnames") or []) if item]
        if hostnames and site:
            preview["focus_ids"] = [f"{site}:{item}" for item in hostnames]
        elif site:
            preview["focus_ids"] = [f"site:{site}"]
        else:
            preview["focus_ids"] = []
        from isot.rules.evaluate import evaluate_overlay

        preview["findings"] = evaluate_overlay(repo, files, site=site)
    return preview


def device_preview(repo: Path, body: dict[str, Any]) -> dict[str, Any]:
    site_id = str(body.get("site") or "")
    kind = str(body.get("kind") or "baremetal")
    cluster = body.get("cluster") or None
    role = body.get("role") or None
    count = int(body.get("count") or 1)
    try:
        drafts, extra_nodes, policy = allocate_devices(
            repo,
            site_id=site_id,
            kind=kind,
            cluster=str(cluster) if cluster else None,
            role=str(role) if role else None,
            count=count,
            overrides=body.get("overrides") if isinstance(body.get("overrides"), dict) else None,
        )
    except AllocatorError as exc:
        raise WizardError(str(exc)) from exc
    files = []
    for draft in drafts:
        content = dump_yaml(draft.data)
        files.append(
            {
                "path": draft.rel_path,
                "action": "create",
                "content": content,
                "diff": unified_diff(draft.rel_path, "", content),
            }
        )
    cluster_file = None
    if cluster:
        rel = f"sites/{site_id}/clusters/{cluster}.yaml"
        path = repo / rel
        if path.is_file():
            original = load_yaml(path)
            updated = copy.deepcopy(original) if isinstance(original, dict) else {}
            nodes = list(updated.get("nodes") or [])
            nodes.extend(extra_nodes)
            updated["nodes"] = nodes
            before = path.read_text(encoding="utf-8")
            after = dump_yaml(updated)
            cluster_file = {
                "path": rel,
                "action": "modify",
                "content": after,
                "diff": unified_diff(rel, before, after),
            }
            files.append(cluster_file)
    if body.get("save_inferred_rules") and policy.get("inferred"):
        topo_rel = f"sites/{site_id}/topology/index.yaml"
        topo_path = repo / topo_rel
        if topo_path.is_file():
            original = load_yaml(topo_path)
            updated = copy.deepcopy(original) if isinstance(original, dict) else {}
            updated["architecture"] = policy.get("architecture")
            updated["connection_rules"] = policy_block(policy)
            before = topo_path.read_text(encoding="utf-8")
            after = dump_yaml(updated)
            files.append(
                {
                    "path": topo_rel,
                    "action": "modify",
                    "content": after,
                    "diff": unified_diff(topo_rel, before, after),
                }
            )
    preview = {
        "kind": "device",
        "site": site_id,
        "role": role,
        "count": count,
        "sibling": drafts[0].sibling if drafts else None,
        "pattern_source": drafts[0].pattern_source if drafts else None,
        "pattern_note": drafts[0].pattern_note if drafts else None,
        "warnings": list(dict.fromkeys(item for draft in drafts for item in draft.warnings)),
        "hostnames": [item.hostname for item in drafts],
        "connection_rules": policy_block(policy),
        "rules_source": "inferred" if policy.get("inferred") else "declared",
        "inferred_rules": policy_block(policy) if policy.get("inferred") else None,
        "fields": [
            {
                "path": field.path,
                "value": field.value,
                "auto": field.auto,
                "origin": field.origin,
                "hostname": draft.hostname,
            }
            for draft in drafts
            for field in draft.fields
        ],
        "files": files,
    }
    edits = body.get("fields") if isinstance(body.get("fields"), dict) else None
    apply_field_edits(preview, edits)
    preview = attach_preview_graph(repo, preview, site_id)
    fix = body.get("fix")
    findings = preview.get("findings")
    if isinstance(fix, dict) and isinstance(findings, dict):
        # Hallazgo que el wizard intenta corregir (abierto desde «Corregir» en el panel).
        from isot.rules.remediation import fix_status

        findings["fix"] = fix_status(fix, findings.get("current") or [], findings.get("resolved") or [])
    return preview


def _shift_ipv4(address: str, src_net: ipaddress.IPv4Network, dst_net: ipaddress.IPv4Network) -> str:
    has_prefix = "/" in address
    raw, _, prefix = address.partition("/")
    addr = ipaddress.ip_address(raw)
    offset = int(addr) - int(src_net.network_address)
    if offset < 0 or offset >= dst_net.num_addresses:
        mapped = ipaddress.ip_address(int(dst_net.network_address) + (offset % max(dst_net.num_addresses, 1)))
    else:
        mapped = ipaddress.ip_address(int(dst_net.network_address) + offset)
    if has_prefix:
        return f"{mapped}/{prefix}"
    return str(mapped)


def _remap_text(value: Any, src_name: str, dst_name: str, src_id: str, dst_id: str) -> Any:
    if isinstance(value, str):
        return value.replace(src_name, dst_name).replace(src_id, dst_id)
    return value


def _site_template_dirs(repo: Path | None) -> list[tuple[str, Path]]:
    dirs: list[tuple[str, Path]] = []
    if repo is not None:
        dirs.append(("repo", repo / SITE_TEMPLATES_DIRNAME))
    dirs.append(("isot", bundled_templates_dir() / "sites"))
    return dirs


def list_site_templates(repo: Path | None) -> list[dict[str, Any]]:
    """Plantillas de sitio: `site-templates/` del repo de datos y las de la herramienta."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for source, folder in _site_template_dirs(repo):
        if not folder.is_dir():
            continue
        for child in sorted(folder.iterdir()):
            topo = child / "topology" / "index.yaml"
            if not child.is_dir() or child.name in seen or not topo.is_file():
                continue
            seen.add(child.name)
            try:
                data = load_yaml(topo)
            except yaml.YAMLError:
                data = {}
            meta = data.get("site") if isinstance(data, dict) and isinstance(data.get("site"), dict) else {}
            out.append(
                {
                    "id": f"{SITE_TEMPLATE_PREFIX}{child.name}",
                    "name": child.name,
                    "description": meta.get("description"),
                    "source": source,
                }
            )
    return out


def site_template_topology(repo: Path, template_id: str) -> dict[str, Any] | None:
    name = template_id[len(SITE_TEMPLATE_PREFIX):]
    if not SAFE_ID.fullmatch(name):
        return None
    for _source, folder in _site_template_dirs(repo):
        topo = folder / name / "topology" / "index.yaml"
        if topo.is_file():
            data = load_yaml(topo)
            return data if isinstance(data, dict) else {}
    return None


def _library_template_files(repo: Path, name: str, src: Path) -> list[dict[str, Any]]:
    """Copia un template de la biblioteca a cluster-templates/ del repo de datos."""
    files: list[dict[str, Any]] = []
    folder = template_dir(src)
    sources = sorted(path for path in folder.rglob("*") if path.is_file()) if src.name == "template.yaml" else [src]
    for path in sources:
        if path.name.startswith("_") or "__pycache__" in path.parts:
            continue
        if src.name == "template.yaml":
            rel = f"cluster-templates/{name}/{path.relative_to(folder).as_posix()}"
        else:
            rel = f"cluster-templates/{path.name}"
        content = path.read_text(encoding="utf-8")
        files.append({"path": rel, "action": "create", "content": content, "diff": unified_diff(rel, "", content)})
    return files


def site_preview(repo: Path, body: dict[str, Any]) -> dict[str, Any]:
    new_id = str(body.get("id") or "").strip()
    new_name = str(body.get("name") or new_id.upper()).strip()
    description = str(body.get("description") or f"Sitio de ejemplo {new_name}")
    template_site_id = str(body.get("template_site") or "")
    vlan_offset = int(body.get("vlan_offset") or 0)
    infra_range = str(body.get("infra_range") or "").strip()
    dci = bool(body.get("dci"))
    dci_peer = str(body.get("dci_peer") or "")
    clusters_spec = body.get("clusters") or []
    if not SAFE_ID.fullmatch(new_id):
        raise WizardError("id de sitio inválido (minúsculas, números y guiones)")
    if not SAFE_NAME.fullmatch(new_name):
        raise WizardError("nombre de sitio inválido")
    sites, spaces = load_catalog(repo)
    if any(item.id == new_id for item in sites):
        raise WizardError(f"el sitio {new_id} ya existe")
    if dci and not dci_peer and sites:
        dci_peer = sites[0].id
    if dci and not any(item.id == dci_peer for item in sites):
        raise WizardError(f"DCI pedido hacia {dci_peer}, pero ese sitio no existe en el repositorio de datos")
    if not template_site_id:
        # Sin plantilla explícita: el primer sitio del catálogo o la plantilla «edge».
        template_site_id = sites[0].id if sites else f"{SITE_TEMPLATE_PREFIX}edge"
    if template_site_id.startswith(SITE_TEMPLATE_PREFIX):
        loaded = site_template_topology(repo, template_site_id)
        if loaded is None:
            raise WizardError(f"plantilla de sitio desconocida: {template_site_id}")
        topology = copy.deepcopy(loaded)
    else:
        template = next((item for item in sites if item.id == template_site_id), None)
        if template is None:
            raise WizardError(f"sitio plantilla desconocido: {template_site_id}")
        topology = copy.deepcopy(load_topology(template))
    src_range = str((topology.get("site") or {}).get("infra_range") or "")
    src_net = ipaddress.ip_network(src_range, strict=False) if src_range else None
    dst_net = ipaddress.ip_network(infra_range, strict=False) if infra_range else src_net

    def remap_ip(value: Any) -> Any:
        if not isinstance(value, str) or not src_net or not dst_net or src_net == dst_net:
            return value
        try:
            ipaddress.ip_address(value.split("/", 1)[0])
        except ValueError:
            return value
        try:
            return _shift_ipv4(value, src_net, dst_net)  # type: ignore[arg-type]
        except Exception:
            return value

    site_meta = topology.get("site") if isinstance(topology.get("site"), dict) else {}
    site_meta = dict(site_meta)
    site_meta["id"] = new_id
    if infra_range:
        site_meta["infra_range"] = infra_range
    site_meta["description"] = description
    topology["site"] = site_meta

    skip_vlans = {4094, 550}
    for subnet in topology.get("subnets") or []:
        if not isinstance(subnet, dict):
            continue
        if subnet.get("cidr"):
            subnet["cidr"] = remap_ip(str(subnet["cidr"]))
        if subnet.get("gateway"):
            subnet["gateway"] = remap_ip(str(subnet["gateway"]))
        vlan = subnet.get("vlan")
        try:
            vlan_i = int(vlan)
        except (TypeError, ValueError):
            vlan_i = None
        if vlan_i is not None and vlan_i not in skip_vlans and vlan_offset:
            subnet["vlan"] = vlan_i + vlan_offset
        if str(subnet.get("name")) == "DCI" and not dci:
            subnet["description"] = "DCI no solicitado en este wizard; se deja el CIDR de documentación"
    for vlan in topology.get("vlans") or []:
        if not isinstance(vlan, dict):
            continue
        try:
            vlan_i = int(vlan.get("id"))
        except (TypeError, ValueError):
            continue
        if vlan_i not in skip_vlans and vlan_offset:
            vlan["id"] = vlan_i + vlan_offset

    cluster_entries = []
    files: list[dict[str, Any]] = []
    generated_devices: list[dict[str, Any]] = []
    policy = policy_from_site_body(body)
    topology["architecture"] = policy.get("architecture")
    topology["connection_rules"] = policy_block(policy)
    generated_devices.extend(build_fabric_devices(new_name, policy))
    used_ips = reserved_from_topology(topology)
    used_ports = used_ports_by_peer(generated_devices)
    mgmt_switch = mgmt_name(generated_devices, new_name)

    templates = load_templates(repo, sites)
    template_names = {
        str(doc.get("template_name") or doc.get("_stem")) for doc in templates
    }
    template_names |= {template_stem(path) for _prefix, path in library_template_files()}
    copied_templates: set[str] = set()

    def _internal_ip(data: dict[str, Any]) -> str:
        preferred = {"APP_CTRL", "MGMT_SW", "mgmt", "CLOUD_MGMT_SW"}
        for iface in data.get("networking") or []:
            if not isinstance(iface, dict):
                continue
            name = str(iface.get("interface") or "")
            if name in preferred or name.upper() in preferred:
                return str(iface.get("ip") or "").split("/", 1)[0]
        for iface in data.get("networking") or []:
            if isinstance(iface, dict) and iface.get("ip"):
                return str(iface["ip"]).split("/", 1)[0]
        return ""

    def _simple_worker(host: str, cluster: str, role: str) -> dict[str, Any]:
        data = {
            "hostname": host,
            "role": role,
            "k8s_roles": ["worker"],
            "k8s_name": host.lower(),
            "kind": "baremetal",
            "vendor": "generic",
            "model": "compute-2u",
            "cluster_ref": cluster,
            "os": "Ubuntu 22.04 LTS",
            "networking": [],
        }
        mgmt = next(
            (
                item
                for item in topology.get("subnets") or []
                if isinstance(item, dict) and "MGMT" in str(item.get("name") or "").upper()
            ),
            None,
        )
        if mgmt and mgmt.get("cidr"):
            ip_plan = next_ip(str(mgmt["cidr"]), used_ips, after=str(mgmt.get("gateway") or ""))
            used_ips.add(ip_plan.value.split("/", 1)[0])
            data["networking"].append(
                {
                    "interface": str(mgmt.get("name") or "MGMT_SW"),
                    "vlan": mgmt.get("vlan"),
                    "ip": ip_plan.value,
                    "gateway": mgmt.get("gateway") or "",
                }
            )
        return data

    for spec in clusters_spec:
        if not isinstance(spec, dict):
            continue
        cname = str(spec.get("name") or f"{new_id}-edge")
        tref = spec.get("template_ref")
        if tref and str(tref) not in template_names:
            raise WizardError(f"template_ref desconocido: {tref}")
        if tref and str(tref) not in copied_templates:
            located = find_global_template(repo, str(tref))
            if located is not None and is_library_rel(rel_path(located, repo)):
                # El template viene de la biblioteca: se copia al repo de datos
                # para que el clúster nuevo quede versionado con su template.
                files.extend(_library_template_files(repo, str(tref), located))
                copied_templates.add(str(tref))
        workers = int(spec.get("workers") or spec.get("count") or 1)
        role = str(spec.get("role") or "worker")
        cluster_doc = {
            "cluster_name": cname,
            "kind": "kubernetes",
            "template_ref": tref or None,
            "distro": "rke2",
            "nodes": [],
        }
        prefix = f"{new_name}-NODE"
        for idx in range(max(1, workers)):
            host = f"{prefix}{idx + 1:03d}"
            pair = pick_pair(policy, generated_devices, rr_index=idx)
            if pair and len(pair.get("leafs") or []) >= 2:
                try:
                    data = build_compute(
                        host,
                        pair=pair,
                        policy=policy,
                        topology=topology,
                        used_ips=used_ips,
                        used_ports=used_ports,
                        cluster=cname,
                        mgmt_switch=mgmt_switch,
                        role=role,
                    )
                except (AllocatorError, ValueError) as exc:
                    raise WizardError(str(exc)) from exc
            else:
                data = _simple_worker(host, cname, role)
            generated_devices.append(data)
            cluster_doc["nodes"].append(
                {
                    "hostname": host,
                    "k8s_name": host.lower(),
                    "roles": ["worker"],
                    "internal_ip": _internal_ip(data),
                }
            )
        crel = f"sites/{new_id}/clusters/{cname}.yaml"
        ccontent = dump_yaml(cluster_doc)
        files.append({"path": crel, "action": "create", "content": ccontent, "diff": unified_diff(crel, "", ccontent)})
        cluster_entries.append({"name": cname, "kind": "kubernetes", "path": crel})

    if dci:
        dci_subnet = next(
            (
                item
                for item in topology.get("subnets") or []
                if isinstance(item, dict) and str(item.get("name") or "").upper() == "DCI"
            ),
            None,
        )
        peer_docs = _device_docs(repo, dci_peer)
        peer_host = None
        peer_port = "Ethernet32"
        for doc in peer_docs:
            for iface in doc.get("networking") or []:
                if not isinstance(iface, dict):
                    continue
                connected = iface.get("connected_to") if isinstance(iface.get("connected_to"), dict) else {}
                vlan = iface.get("vlan")
                if vlan in {4094, "4094"} or str(connected.get("site") or "") not in {"", dci_peer}:
                    peer_host = str(doc.get("hostname") or "")
                    sample = str(iface.get("interface") or connected.get("port") or "Ethernet31")
                    try:
                        ports = used_ports_by_peer(peer_docs)
                        peer_port = next_port(ports.get(peer_host, set()), sample).value
                    except AllocatorError:
                        peer_port = sample
                    break
            if peer_host:
                break
        if peer_host is None and peer_docs:
            pick = next((doc for doc in peer_docs if "switch" in str(doc.get("role") or "")), peer_docs[0])
            peer_host = str(pick.get("hostname") or "")
        if peer_host:
            gw = next((item for item in generated_devices if str(item.get("role")) == "dcgw"), None)
            if gw is None:
                gw = {
                    "hostname": f"{new_name}-DCGW001",
                    "role": "dcgw",
                    "kind": "network",
                    "vendor": "generic",
                    "model": "leaf-32x100g",
                    "leaf_pair": "border",
                    "os": "nos",
                    "networking": [],
                }
                generated_devices.append(gw)
            dci_ips = reserved_from_topology(topology) | used_ips_from_devices(
                [dev.data for dev in load_devices(repo, sites)]
            ) | used_ips
            dci_ip = None
            if dci_subnet and dci_subnet.get("cidr"):
                try:
                    dci_ip = next_ip(str(dci_subnet["cidr"]), dci_ips, after=str(dci_subnet.get("gateway") or "")).value
                except AllocatorError:
                    dci_ip = None
            networking = list(gw.get("networking") or [])
            networking.append(
                {
                    "interface": "Ethernet31",
                    "vlan": 4094,
                    "ip": dci_ip or "",
                    "connected_to": {"device": peer_host, "site": dci_peer, "port": peer_port},
                }
            )
            gw["networking"] = networking

    for data in generated_devices:
        host = str(data.get("hostname") or "")
        if not host:
            continue
        kind = str(data.get("kind") or "baremetal")
        rel = f"sites/{new_id}/devices/{KIND_DIRS.get(kind, kind)}/{host}.yaml"
        content = dump_yaml(data)
        files.append({"path": rel, "action": "create", "content": content, "diff": unified_diff(rel, "", content)})

    topology["clusters"] = cluster_entries
    topology["undocumented_peers"] = []
    topology["gaps"] = ["Inventario generado por el wizard de nuevo sitio (datos de ejemplo)."]
    topo_rel = f"sites/{new_id}/topology/index.yaml"
    topo_content = dump_yaml(topology)
    files.insert(0, {"path": topo_rel, "action": "create", "content": topo_content, "diff": unified_diff(topo_rel, "", topo_content)})

    catalog_path = repo / "sites.yaml"
    catalog = load_yaml(catalog_path) if catalog_path.is_file() else {"sites": []}
    if not isinstance(catalog, dict):
        catalog = {"sites": []}
    sites_list = list(catalog.get("sites") or [])
    sites_list.append({"id": new_id, "name": new_name, "path": f"sites/{new_id}", "description": description})
    catalog["sites"] = sites_list
    if dci:
        spaces_list = list(catalog.get("address_spaces") or [])
        found = False
        for space in spaces_list:
            if isinstance(space, dict) and space.get("id") == "dci-overlay":
                members = list(space.get("sites") or [])
                if new_id not in members:
                    members.append(new_id)
                space["sites"] = members
                found = True
        if not found:
            spaces_list.append({"id": "dci-overlay", "sites": [dci_peer, new_id], "match": {"subnets": ["DCI"]}})
        catalog["address_spaces"] = spaces_list
    catalog_after = dump_yaml(catalog)
    catalog_before = catalog_path.read_text(encoding="utf-8") if catalog_path.is_file() else ""
    files.append(
        {
            "path": "sites.yaml",
            "action": "modify" if catalog_path.is_file() else "create",
            "content": catalog_after,
            "diff": unified_diff("sites.yaml", catalog_before, catalog_after),
        }
    )
    preview = {
        "kind": "site",
        "site": new_id,
        "name": new_name,
        "template_site": template_site_id,
        "architecture": policy.get("architecture"),
        "connection_rules": policy_block(policy),
        "rules_source": "declared",
        "files": files,
        "hostnames": [str(item.get("hostname")) for item in generated_devices if item.get("hostname")],
        "fields": [
            {"path": "id", "value": new_id, "auto": False, "origin": "wizard"},
            {"path": "name", "value": new_name, "auto": False, "origin": "wizard"},
            {"path": "architecture", "value": policy.get("architecture"), "auto": True, "origin": "wizard"},
            {
                "path": "connection_rules.pair_policy",
                "value": policy.get("pair_policy"),
                "auto": True,
                "origin": "política del sitio",
            },
            {"path": "infra_range", "value": infra_range or src_range, "auto": not bool(infra_range), "origin": "plantilla " + template_site_id},
        ],
    }
    return attach_preview_graph(repo, preview, new_id)


def apply_files(repo: Path, files: list[dict[str, Any]]) -> list[Path]:
    written: list[Path] = []
    for item in files:
        rel = str(item["path"])
        if ".." in rel.split("/"):
            raise WizardError(f"ruta inválida: {rel}")
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(item.get("content") or ""), encoding="utf-8")
        written.append(path)
    return written


SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,120}$")


def unique_wizard_branch(repo: Path, description: str) -> str:
    base = f"wizard/{slugify(description)}"
    name = base
    n = 2
    while True:
        proc = git(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"])
        if proc.returncode != 0:
            return name
        name = f"{base}-{n}"
        n += 1


def save_preview(
    repo: Path, preview: dict[str, Any], *, site: str | None = None, token: str | None = None
) -> dict[str, Any]:
    """Escribe los archivos del preview en el working tree (sin commit).

    Bloquea solo por errores *nuevos* del sitio del wizard (o no atribuibles a
    ningún sitio); si los hay, deja los archivos como estaban.
    """
    from isot.rules.evaluate import diff_findings, evaluate, wizard_scope
    from isot.worktree import WorktreeError, check_paths, restore, save_files, snapshot

    files = preview.get("files") or []
    if not files:
        raise WizardError("preview sin archivos")
    writes = {str(item["path"]): str(item.get("content") or "") for item in files}
    root = repo.resolve()
    check_paths(root, list(writes))
    scope = site or (str(preview.get("site")) if preview.get("site") else None)
    base_findings = evaluate(repo)
    before = snapshot(root, writes)
    result = save_files(repo, writes, [], token=token)
    try:
        added, _ = diff_findings(base_findings, evaluate(repo))
    except Exception as exc:  # noqa: BLE001
        restore(root, before)
        raise WizardError(str(exc)) from exc
    errors = [item for item in wizard_scope(added, scope) if item.severity == "error"]
    if errors:
        restore(root, before)
        raise WorktreeError("validador: " + "; ".join(f"{item.id} {item.message}" for item in errors[:8]))
    return {**result, "files": [item["path"] for item in files]}


