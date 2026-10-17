"""Reglas de control de clústeres Kubernetes."""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from isot.helm import charts_from_doc, load_chart_values
from isot.inventory import Device
from isot.rules.context import RuleContext
from isot.rules.model import Finding
from isot.rules.registry import RULES, emit, rule
from isot.validator import _ip_in_cidr, _ip_only, _parse_address_token, _topology_maps

VERSION_RE = re.compile(r"v?(\d+)\.(\d+)")
IP_IN_TEXT = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
TUNNEL_OVERHEAD = {"vxlan": 50, "ipip": 20, "ipipcrosssubnet": 20, "native": 0, "none": 0, "bgp": 0}
CP_ROLES = {"control-plane", "etcd", "master"}
WORKER_ROLES = {"worker"}
EOL_PATH = Path(__file__).resolve().parent / "k8s_eol.yaml"


def _k8s_clusters(ctx: RuleContext) -> list[dict[str, Any]]:
    return [item for item in ctx.clusters if item.get("kind") == "kubernetes"]


def _cluster_meta(cluster: dict[str, Any]) -> tuple[str, str, str]:
    site = str(cluster.get("_site") or "")
    name = str(cluster.get("cluster_name") or cluster.get("_stem") or "")
    rel = str(cluster.get("_path") or f"sites/{site}/clusters/{name}.yaml")
    return site, name, rel


def _graph_cluster(site: str, name: str) -> str:
    return f"cluster:{site}:{name}"


def _nodes(cluster: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (cluster.get("nodes") or []) if isinstance(item, dict)]


def _roles(node: dict[str, Any]) -> set[str]:
    raw = node.get("roles") or []
    if isinstance(raw, str):
        raw = [raw]
    return {str(item).lower() for item in raw}


def _is_cp(node: dict[str, Any]) -> bool:
    return bool(_roles(node) & CP_ROLES)


def _is_worker(node: dict[str, Any]) -> bool:
    return "worker" in _roles(node)


def _is_hosted(cluster: dict[str, Any], template: dict[str, Any] | None) -> bool:
    tokens = " ".join(
        [
            str(cluster.get("distro") or ""),
            str(cluster.get("template_ref") or ""),
            str(cluster.get("product") or ""),
            str((template or {}).get("distro") or ""),
            str((template or {}).get("template_name") or ""),
        ]
    ).lower()
    return "hosted" in tokens or "hosted-controlplane" in tokens or "external etcd" in tokens


def _device_for_node(ctx: RuleContext, site: str, node: dict[str, Any]) -> Device | None:
    name = str(node.get("hostname") or node.get("name") or "")
    if not name:
        return None
    return ctx.device(site, name)


def _leaf_key(ctx: RuleContext, device: Device | None) -> tuple[str, ...] | None:
    if device is None:
        return None
    hyper = device.data.get("hypervisor") if isinstance(device.data.get("hypervisor"), dict) else None
    if hyper and hyper.get("device"):
        host = ctx.device(device.site_id, str(hyper["device"]))
        if host:
            return _leaf_key(ctx, host)
    peers: set[str] = set()
    for bond in device.data.get("bonds") or []:
        if not isinstance(bond, dict):
            continue
        leaf_pc = bond.get("leaf_pc") or {}
        if isinstance(leaf_pc, dict):
            peers.update(str(item) for item in leaf_pc if item)
    if device.data.get("leaf_pair"):
        return (str(device.data["leaf_pair"]),)
    if peers:
        return tuple(sorted(peers))
    return None


def _parse_cidr(value: Any) -> ipaddress.IPv4Network | ipaddress.IPv6Network | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return ipaddress.ip_network(text, strict=False)
    except ValueError:
        return None


def _parse_minor(version: Any) -> tuple[int, int] | None:
    match = VERSION_RE.search(str(version or ""))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _load_eol() -> dict[str, date]:
    data = yaml.safe_load(EOL_PATH.read_text(encoding="utf-8")) if EOL_PATH.is_file() else {}
    table: dict[str, date] = {}
    for key, value in (data.get("eol") or {}).items():
        try:
            table[str(key)] = date.fromisoformat(str(value))
        except ValueError:
            continue
    return table


def _nested(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _cluster_values(ctx: RuleContext, cluster: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    template = ctx.template_for(cluster)
    if template:
        defaults = _nested(template, "cni", "defaults")
        if isinstance(defaults, dict):
            merged.setdefault("cniConfig", {})
            if isinstance(merged["cniConfig"], dict):
                merged["cniConfig"].update(defaults)
        path = ctx.root / str(template.get("_path") or "")
        for chart in charts_from_doc(template):
            values, _source, _rel, error = load_chart_values(path, chart)
            if error or not isinstance(values, dict):
                continue
            _deep_merge(merged, values)
    for item in cluster.get("values_overrides") or []:
        if not isinstance(item, dict):
            continue
        inline = item.get("values_inline")
        if isinstance(inline, dict):
            _deep_merge(merged, inline)
    return merged


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> None:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _uses_longhorn_or_ceph(cluster: dict[str, Any], values: dict[str, Any]) -> tuple[bool, bool]:
    longhorn = False
    ceph = False
    storage = values.get("storage") if isinstance(values.get("storage"), dict) else {}
    lh = storage.get("longhorn") if isinstance(storage, dict) else None
    if lh is True or (isinstance(lh, dict) and lh.get("enabled") not in (False, "disabled", "false")):
        if lh is not False:
            longhorn = True
    if isinstance(lh, str) and lh.lower() in {"enabled", "true"}:
        longhorn = True
    if isinstance(lh, str) and lh.lower() in {"disabled", "false"}:
        longhorn = False
    for item in cluster.get("storage_classes") or []:
        if not isinstance(item, dict):
            continue
        blob = " ".join(str(item.get(key) or "") for key in ("name", "provisioner")).lower()
        if "longhorn" in blob:
            longhorn = True
        if "ceph" in blob:
            ceph = True
    note = str(cluster.get("storage_note") or "").lower()
    if "sin storageclass" in note or "sin csi" in note:
        longhorn = False
        ceph = False
    return longhorn, ceph


def _replica_count(cluster: dict[str, Any], values: dict[str, Any]) -> tuple[int | None, str]:
    candidates = [
        ("values.storage.longhorn.replicaCount", _nested(values, "storage", "longhorn", "replicaCount")),
        ("values.defaultSettings.defaultReplicaCount", _nested(values, "defaultSettings", "defaultReplicaCount")),
        (
            "values.persistence.defaultClassReplicaCount",
            _nested(values, "persistence", "defaultClassReplicaCount"),
        ),
        ("values.ceph.replicas", _nested(values, "ceph", "replicas")),
        ("values.ceph.size", _nested(values, "ceph", "size")),
        ("ficha.storage.replicas", _nested(cluster, "storage", "replicas")),
        ("ficha.longhorn.replicas", _nested(cluster, "longhorn", "replicas")),
        ("ficha.ceph.replicas", _nested(cluster, "ceph", "replicas")),
    ]
    for origin, raw in candidates:
        if raw is None or raw == "":
            continue
        try:
            return int(raw), origin
        except (TypeError, ValueError):
            continue
    return None, ""


@rule(
    "K8S-CP-001",
    severity="warning",
    description="Tamaño del control plane: <3 (sin quórum HA de etcd), par (2/4/6, no suma tolerancia) o >7. Exentos hosted/etcd externo.",
    entity="cluster",
    recommendation="Usa 3, 5 o 7 control planes con etcd embebido. Hosted control plane (etcd externo) está exento.",
)
def rule_cp_count(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-CP-001"]
    findings: list[Finding] = []
    for cluster in _k8s_clusters(ctx):
        if _is_hosted(cluster, ctx.template_for(cluster)):
            continue
        site, name, rel = _cluster_meta(cluster)
        cps = [node for node in _nodes(cluster) if _is_cp(node)]
        count = len(cps)
        message = None
        if count < 3:
            message = (
                f"{name} tiene {count} control plane(s): sin quórum HA de etcd "
                f"(se recomiendan 3, 5 o 7)"
            )
        elif count > 7:
            message = f"{name} tiene {count} control planes: más de 7 no mejora el quórum y añade latencia a etcd"
        elif count % 2 == 0:
            message = (
                f"{name} tiene {count} control planes (cantidad par): "
                f"no suma tolerancia a fallos frente a {count - 1}"
            )
        if not message:
            continue
        findings.append(
            emit(
                spec,
                entity_kind="cluster",
                entity_id=f"{site}/{name}",
                site=site,
                cluster=name,
                path=rel,
                graph_id=_graph_cluster(site, name),
                message=message,
            )
        )
    return findings


@rule(
    "K8S-CP-002",
    severity="error",
    description="Control planes VM en el mismo host físico (error) o todos colgando del mismo par de leafs (warning, SPOF de red).",
    entity="cluster",
    recommendation="Reparte las VMs de control plane en hypervisors distintos y dual-home a pares de leafs diferentes si el clúster es HA.",
)
def rule_cp_placement(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-CP-002"]
    findings: list[Finding] = []
    for cluster in _k8s_clusters(ctx):
        site, name, rel = _cluster_meta(cluster)
        cps = [node for node in _nodes(cluster) if _is_cp(node)]
        hosts: dict[str, list[str]] = defaultdict(list)
        leafs: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for node in cps:
            device = _device_for_node(ctx, site, node)
            hostname = str(node.get("hostname") or "")
            if device is None:
                continue
            kind = str(device.data.get("kind") or device.kind or "").lower()
            hyper = device.data.get("hypervisor") if isinstance(device.data.get("hypervisor"), dict) else None
            if kind in {"vm", "virtual-machines"} or hyper:
                host = str((hyper or {}).get("device") or "")
                if host:
                    hosts[host].append(hostname or device.hostname)
            key = _leaf_key(ctx, device)
            if key:
                leafs[key].append(hostname or device.hostname)
        for host, members in hosts.items():
            if len(members) >= 2:
                findings.append(
                    emit(
                        spec,
                        entity_kind="cluster",
                        entity_id=f"{site}/{name}",
                        site=site,
                        cluster=name,
                        path=rel,
                        graph_id=_graph_cluster(site, name),
                        message=(
                            f"{name}: control planes VM {', '.join(members)} "
                            f"comparten el hypervisor {host}"
                        ),
                    )
                )
        if len(cps) >= 2 and len(leafs) == 1:
            key, members = next(iter(leafs.items()))
            if len(members) == len(cps):
                findings.append(
                    emit(
                        spec,
                        severity="warning",
                        entity_kind="cluster",
                        entity_id=f"{site}/{name}",
                        site=site,
                        cluster=name,
                        path=rel,
                        graph_id=_graph_cluster(site, name),
                        message=(
                            f"{name}: todos los control planes ({', '.join(members)}) "
                            f"cuelgan del mismo par de leafs {','.join(key)} (SPOF de red)"
                        ),
                    )
                )
    return findings


@rule(
    "K8S-NET-001",
    severity="error",
    description="pod_cidr y service_cidr no deben solaparse entre sí, con subredes de nodos, otros clústeres del sitio ni el DCI. Si son desconocidos, info.",
    entity="cluster",
    recommendation="Elige CIDRs privados no usados en topology/index.yaml ni en el resto de clústeres del sitio.",
)
def rule_cidrs(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-NET-001"]
    findings: list[Finding] = []
    by_site: dict[str, list[tuple[str, str, Any]]] = defaultdict(list)
    for cluster in _k8s_clusters(ctx):
        site, name, rel = _cluster_meta(cluster)
        pod = _parse_cidr(cluster.get("pod_cidr"))
        svc = _parse_cidr(cluster.get("service_cidr"))
        if pod is None and svc is None:
            findings.append(
                emit(
                    spec,
                    severity="info",
                    entity_kind="cluster",
                    entity_id=f"{site}/{name}",
                    site=site,
                    cluster=name,
                    path=rel,
                    graph_id=_graph_cluster(site, name),
                    message=f"{name}: pod_cidr y service_cidr desconocidos",
                )
            )
            continue
        if pod is None or svc is None:
            which = "pod_cidr" if pod is None else "service_cidr"
            findings.append(
                emit(
                    spec,
                    severity="info",
                    entity_kind="cluster",
                    entity_id=f"{site}/{name}",
                    site=site,
                    cluster=name,
                    path=rel,
                    graph_id=_graph_cluster(site, name),
                    message=f"{name}: {which} desconocido",
                )
            )
        if pod and svc and pod.overlaps(svc):
            findings.append(
                emit(
                    spec,
                    entity_kind="cluster",
                    entity_id=f"{site}/{name}",
                    site=site,
                    cluster=name,
                    path=rel,
                    graph_id=_graph_cluster(site, name),
                    message=f"{name}: pod_cidr {pod} solapa con service_cidr {svc}",
                )
            )
        if pod:
            by_site[site].append((name, rel, pod))
        if svc:
            by_site[site].append((name, rel, svc))
    dci_cidrs: dict[str, list[Any]] = defaultdict(list)
    for space in ctx.spaces:
        for sid in space.sites:
            _, subnets = _topology_maps(ctx.topologies.get(sid) or {})
            for sub_name in space.match_subnets:
                cidr = _parse_cidr((subnets.get(sub_name) or {}).get("cidr"))
                if cidr:
                    dci_cidrs[sid].append(cidr)
    for cluster in _k8s_clusters(ctx):
        site, name, rel = _cluster_meta(cluster)
        nets = [(_parse_cidr(cluster.get("pod_cidr")), "pod_cidr"), (_parse_cidr(cluster.get("service_cidr")), "service_cidr")]
        _, subnets = _topology_maps(ctx.topologies.get(site) or {})
        node_cidrs = [_parse_cidr(item.get("cidr")) for item in subnets.values()]
        node_cidrs = [item for item in node_cidrs if item]
        for net, label in nets:
            if net is None:
                continue
            for other in node_cidrs:
                if net.overlaps(other):
                    findings.append(
                        emit(
                            spec,
                            entity_kind="cluster",
                            entity_id=f"{site}/{name}",
                            site=site,
                            cluster=name,
                            path=rel,
                            graph_id=_graph_cluster(site, name),
                            message=f"{name}: {label} {net} solapa con la subred de nodos {other}",
                        )
                    )
            for extra in dci_cidrs.get(site) or []:
                if net.overlaps(extra):
                    findings.append(
                        emit(
                            spec,
                            entity_kind="cluster",
                            entity_id=f"{site}/{name}",
                            site=site,
                            cluster=name,
                            path=rel,
                            graph_id=_graph_cluster(site, name),
                            message=f"{name}: {label} {net} solapa con el DCI {extra}",
                        )
                    )
        others = [(oname, orel, onet) for oname, orel, onet in by_site.get(site) or [] if oname != name]
        for net, label in nets:
            if net is None:
                continue
            for oname, _orel, onet in others:
                if net.overlaps(onet):
                    findings.append(
                        emit(
                            spec,
                            entity_kind="cluster",
                            entity_id=f"{site}/{name}",
                            site=site,
                            cluster=name,
                            path=rel,
                            graph_id=_graph_cluster(site, name),
                            message=f"{name}: {label} {net} solapa con {oname} ({onet})",
                        )
                    )
    return findings


@rule(
    "K8S-LB-001",
    severity="error",
    description="Pool MetalLB (IPAddressPool) y VIP del API deben caer en su subred y no chocar con IPs de devices, gateway o VRRP.",
    entity="cluster",
    recommendation="Recorta el pool para dejar fuera gateway/VRRP/IPs de hosts, o mueve el VIP a un hueco libre de la subred.",
)
def rule_metallb(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-LB-001"]
    findings: list[Finding] = []
    reserved: dict[str, dict[str, str]] = defaultdict(dict)
    for device in ctx.devices:
        for iface in device.interfaces:
            ip_text = iface.ip_address
            if ip_text:
                reserved[device.site_id][ip_text] = f"{device.hostname} {iface.name or ''}".strip()
        oob = device.data.get("oob") if isinstance(device.data.get("oob"), dict) else None
        if oob:
            oob_ip = _ip_only(str(oob.get("ip") or ""))
            if oob_ip:
                reserved[device.site_id][oob_ip] = f"{device.hostname} oob"
    for cluster in _k8s_clusters(ctx):
        site, name, rel = _cluster_meta(cluster)
        _, subnets = _topology_maps(ctx.topologies.get(site) or {})
        blocked: dict[str, str] = dict(reserved.get(site) or {})
        for sub in subnets.values():
            gw = _ip_only(str(sub.get("gateway") or ""))
            if gw:
                blocked[gw] = f"gateway {sub.get('name')}"
            vrrp = _ip_only(str(sub.get("vrrp") or sub.get("vrrp_ip") or ""))
            if vrrp:
                blocked[vrrp] = f"vrrp {sub.get('name')}"
        metallb = cluster.get("metallb") if isinstance(cluster.get("metallb"), dict) else {}
        vip_tokens: list[str] = []
        vips = metallb.get("vips")
        if isinstance(vips, dict):
            vip_tokens.extend(str(value) for value in vips.values() if value)
        elif isinstance(vips, list):
            for vip in vips:
                if isinstance(vip, dict):
                    vip_tokens.extend(str(value) for value in vip.values() if value)
                elif vip:
                    vip_tokens.append(str(vip))
        services = metallb.get("services")
        if isinstance(services, dict):
            vip_tokens.extend(str(value) for value in services.values() if value)
        for token in vip_tokens:
            extracted = IP_IN_TEXT.findall(token) or _parse_address_token(token)
            for ip_text in extracted:
                if not ip_text:
                    continue
                _check_lb_ip(findings, spec, site, name, rel, ip_text, subnets, blocked, label="VIP")
        for pool in metallb.get("pools") or []:
            if not isinstance(pool, dict):
                continue
            pool_name = str(pool.get("name") or "?")
            subnet_name = pool.get("subnet")
            subnet = subnets.get(str(subnet_name)) if subnet_name else None
            cidr = str((subnet or {}).get("cidr") or "")
            for spec_addr in pool.get("addresses") or []:
                for ip_text in _expand_pool_ips(str(spec_addr)):
                    if cidr and not _ip_in_cidr(ip_text, cidr):
                        findings.append(
                            emit(
                                spec,
                                entity_kind="cluster",
                                entity_id=f"{site}/{name}",
                                site=site,
                                cluster=name,
                                path=rel,
                                graph_id=_graph_cluster(site, name),
                                message=(
                                    f"{name}: pool MetalLB {pool_name} {ip_text} "
                                    f"fuera de {subnet_name} ({cidr})"
                                ),
                            )
                        )
                    owner = blocked.get(ip_text)
                    if owner:
                        findings.append(
                            emit(
                                spec,
                                entity_kind="cluster",
                                entity_id=f"{site}/{name}",
                                site=site,
                                cluster=name,
                                path=rel,
                                graph_id=_graph_cluster(site, name),
                                message=(
                                    f"{name}: pool MetalLB {pool_name} {ip_text} "
                                    f"choca con {owner}"
                                ),
                            )
                        )
    return findings


def _check_lb_ip(
    findings: list[Finding],
    spec: Any,
    site: str,
    name: str,
    rel: str,
    ip_text: str,
    subnets: dict[str, dict],
    blocked: dict[str, str],
    *,
    label: str,
) -> None:
    matched = any(s.get("cidr") and _ip_in_cidr(ip_text, str(s["cidr"])) for s in subnets.values())
    if subnets and not matched:
        findings.append(
            emit(
                spec,
                entity_kind="cluster",
                entity_id=f"{site}/{name}",
                site=site,
                cluster=name,
                path=rel,
                graph_id=_graph_cluster(site, name),
                message=f"{name}: {label} {ip_text} no cae en ninguna subred del sitio",
            )
        )
    owner = blocked.get(ip_text)
    if owner:
        findings.append(
            emit(
                spec,
                entity_kind="cluster",
                entity_id=f"{site}/{name}",
                site=site,
                cluster=name,
                path=rel,
                graph_id=_graph_cluster(site, name),
                message=f"{name}: {label} {ip_text} choca con {owner}",
            )
        )


def _expand_pool_ips(spec: str) -> list[str]:
    spec = spec.strip()
    if not spec:
        return []
    if "-" in spec and "/" not in spec.split("-", 1)[0]:
        left, right = spec.split("-", 1)
        start = _ip_only(left)
        end = _ip_only(right)
        if not start or not end:
            return [item for item in (start, end) if item]
        try:
            a = ipaddress.ip_address(start)
            b = ipaddress.ip_address(end)
        except ValueError:
            return [start, end]
        if int(b) - int(a) > 4096:
            return [start, end]
        return [str(ipaddress.ip_address(value)) for value in range(int(a), int(b) + 1)]
    single = _ip_only(spec)
    return [single] if single else []


@rule(
    "K8S-CNI-001",
    severity="warning",
    description="El MTU del CNI debe ser ≤ MTU de la interfaz menos el overhead del tunnelProtocol (VXLAN 50, IPIP 20, nativo 0).",
    entity="cluster",
    recommendation="Baja cniConfig.mtu o sube el MTU del bond/NAD. VXLAN resta 50, IPIP 20.",
)
def rule_cni_mtu(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-CNI-001"]
    findings: list[Finding] = []
    for cluster in _k8s_clusters(ctx):
        site, name, rel = _cluster_meta(cluster)
        values = _cluster_values(ctx, cluster)
        cni = values.get("cniConfig") if isinstance(values.get("cniConfig"), dict) else {}
        mtu = cni.get("mtu") if cni else _nested(cluster, "cni", "mtu")
        proto = str(cni.get("tunnelProtocol") or _nested(cluster, "cni", "tunnelProtocol") or cluster.get("cni") or "")
        if isinstance(cluster.get("cni"), dict) and not cni.get("tunnelProtocol"):
            proto = str(cluster["cni"].get("primary") or proto)
        try:
            cni_mtu = int(mtu)
        except (TypeError, ValueError):
            continue
        proto_key = proto.lower()
        overhead = 0
        for key, value in TUNNEL_OVERHEAD.items():
            if key in proto_key:
                overhead = value
                break
        iface_mtu = _cluster_iface_mtu(ctx, cluster, site)
        if iface_mtu is None:
            continue
        if cni_mtu > iface_mtu - overhead:
            findings.append(
                emit(
                    spec,
                    entity_kind="cluster",
                    entity_id=f"{site}/{name}",
                    site=site,
                    cluster=name,
                    path=rel,
                    graph_id=_graph_cluster(site, name),
                    message=(
                        f"{name}: MTU CNI {cni_mtu} > interfaz {iface_mtu} − overhead {overhead} "
                        f"({proto or 'nativo'})"
                    ),
                )
            )
    return findings


def _cluster_iface_mtu(ctx: RuleContext, cluster: dict[str, Any], site: str) -> int | None:
    mtus: list[int] = []
    for nad in cluster.get("nads") or []:
        if isinstance(nad, dict) and nad.get("mtu"):
            try:
                mtus.append(int(nad["mtu"]))
            except (TypeError, ValueError):
                pass
    for node in _nodes(cluster):
        device = _device_for_node(ctx, site, node)
        if device is None:
            continue
        for iface in device.data.get("networking") or []:
            if isinstance(iface, dict) and iface.get("mtu"):
                try:
                    mtus.append(int(iface["mtu"]))
                except (TypeError, ValueError):
                    pass
    return min(mtus) if mtus else None


@rule(
    "K8S-NAD-001",
    severity="error",
    description="Cada NAD debe referenciar una VLAN de la topología presente en el bond/interfaz de los nodos del clúster.",
    entity="cluster",
    recommendation="Alinea vlan del NAD con topology/index.yaml y allowed_vlans / parent del bond en los nodos.",
)
def rule_nad(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-NAD-001"]
    findings: list[Finding] = []
    for cluster in _k8s_clusters(ctx):
        site, name, rel = _cluster_meta(cluster)
        vlans, _subnets = _topology_maps(ctx.topologies.get(site) or {})
        node_vlans = _node_vlan_set(ctx, cluster, site)
        for nad in cluster.get("nads") or []:
            if not isinstance(nad, dict):
                continue
            label = f"{nad.get('namespace', '')}/{nad.get('name', '')}"
            vlan = nad.get("vlan")
            if vlan is None or vlan == "":
                findings.append(
                    emit(
                        spec,
                        entity_kind="cluster",
                        entity_id=f"{site}/{name}",
                        site=site,
                        cluster=name,
                        path=rel,
                        graph_id=_graph_cluster(site, name),
                        message=f"{name}: NAD {label} sin vlan",
                    )
                )
                continue
            try:
                vlan_key: Any = int(vlan)
            except (TypeError, ValueError):
                vlan_key = vlan
            if vlan_key not in vlans and vlan not in vlans:
                findings.append(
                    emit(
                        spec,
                        entity_kind="cluster",
                        entity_id=f"{site}/{name}",
                        site=site,
                        cluster=name,
                        path=rel,
                        graph_id=_graph_cluster(site, name),
                        message=f"{name}: NAD {label} vlan={vlan} no está en la topología",
                    )
                )
                continue
            if node_vlans is not None and vlan_key not in node_vlans and str(vlan) not in node_vlans:
                findings.append(
                    emit(
                        spec,
                        entity_kind="cluster",
                        entity_id=f"{site}/{name}",
                        site=site,
                        cluster=name,
                        path=rel,
                        graph_id=_graph_cluster(site, name),
                        message=(
                            f"{name}: NAD {label} vlan={vlan} no está en el bond/interfaz "
                            f"de los nodos del clúster"
                        ),
                    )
                )
    return findings


def _node_vlan_set(ctx: RuleContext, cluster: dict[str, Any], site: str) -> set[Any] | None:
    found: set[Any] = set()
    saw_device = False
    for node in _nodes(cluster):
        device = _device_for_node(ctx, site, node)
        if device is None:
            continue
        saw_device = True
        for bond in device.data.get("bonds") or []:
            if not isinstance(bond, dict):
                continue
            for vlan in bond.get("allowed_vlans") or []:
                found.add(vlan)
                try:
                    found.add(int(vlan))
                except (TypeError, ValueError):
                    pass
            for vlan in bond.get("extra_host_vlans") or []:
                found.add(vlan)
                try:
                    found.add(int(vlan))
                except (TypeError, ValueError):
                    pass
        for iface in device.data.get("networking") or []:
            if not isinstance(iface, dict):
                continue
            vlan = iface.get("vlan")
            if vlan is None or vlan == "":
                continue
            found.add(vlan)
            try:
                found.add(int(vlan))
            except (TypeError, ValueError):
                pass
    return found if saw_device else None


@rule(
    "K8S-WK-001",
    severity="warning",
    description="Menos de 2 workers, o menos de 3 si el clúster usa Ceph o Longhorn.",
    entity="cluster",
    recommendation="Añade workers. Con Ceph/Longhorn hacen falta ≥3 nodos de storage/worker para réplicas.",
)
def rule_workers(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-WK-001"]
    findings: list[Finding] = []
    for cluster in _k8s_clusters(ctx):
        site, name, rel = _cluster_meta(cluster)
        workers = [node for node in _nodes(cluster) if _is_worker(node)]
        count = len(workers)
        values = _cluster_values(ctx, cluster)
        longhorn, ceph = _uses_longhorn_or_ceph(cluster, values)
        if count < 2:
            findings.append(
                emit(
                    spec,
                    entity_kind="cluster",
                    entity_id=f"{site}/{name}",
                    site=site,
                    cluster=name,
                    path=rel,
                    graph_id=_graph_cluster(site, name),
                    message=f"{name} tiene {count} worker(s); se recomiendan al menos 2",
                )
            )
        elif (longhorn or ceph) and count < 3:
            kind = "Longhorn" if longhorn else "Ceph"
            findings.append(
                emit(
                    spec,
                    entity_kind="cluster",
                    entity_id=f"{site}/{name}",
                    site=site,
                    cluster=name,
                    path=rel,
                    graph_id=_graph_cluster(site, name),
                    message=f"{name} usa {kind} con {count} workers; se recomiendan al menos 3",
                )
            )
    return findings


@rule(
    "K8S-ST-001",
    severity="error",
    description="Réplicas de Longhorn o Ceph (values o ficha) mayores que los nodos de storage elegibles.",
    entity="cluster",
    recommendation="Baja defaultReplicaCount / size al número de nodos worker/storage, o añade nodos.",
)
def rule_storage_replicas(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-ST-001"]
    findings: list[Finding] = []
    for cluster in _k8s_clusters(ctx):
        site, name, rel = _cluster_meta(cluster)
        values = _cluster_values(ctx, cluster)
        replicas, origin = _replica_count(cluster, values)
        if replicas is None:
            continue
        longhorn, ceph = _uses_longhorn_or_ceph(cluster, values)
        eligible = [node for node in _nodes(cluster) if _is_worker(node) or _is_cp(node)]
        if not longhorn and not ceph:
            continue
        if replicas > len(eligible):
            findings.append(
                emit(
                    spec,
                    entity_kind="cluster",
                    entity_id=f"{site}/{name}",
                    site=site,
                    cluster=name,
                    path=rel,
                    graph_id=_graph_cluster(site, name),
                    message=(
                        f"{name}: {replicas} réplicas ({origin}) > {len(eligible)} "
                        f"nodos elegibles"
                    ),
                )
            )
    return findings


@rule(
    "K8S-VER-001",
    severity="warning",
    description="Versión de Kubernetes sin soporte (tabla EOL en isot/rules/k8s_eol.yaml).",
    entity="cluster",
    recommendation="Planifica el upgrade a una minor aún soportada. Actualiza isot/rules/k8s_eol.yaml al publicar versiones.",
)
def rule_k8s_eol(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-VER-001"]
    table = _load_eol()
    findings: list[Finding] = []
    for cluster in _k8s_clusters(ctx):
        site, name, rel = _cluster_meta(cluster)
        minor = _parse_minor(cluster.get("version"))
        if minor is None:
            continue
        key = f"{minor[0]}.{minor[1]}"
        eol = table.get(key)
        if eol and ctx.as_of > eol:
            findings.append(
                emit(
                    spec,
                    entity_kind="cluster",
                    entity_id=f"{site}/{name}",
                    site=site,
                    cluster=name,
                    path=rel,
                    graph_id=_graph_cluster(site, name),
                    message=f"{name} usa Kubernetes {key} (EOL {eol.isoformat()})",
                )
            )
    return findings


@rule(
    "K8S-VER-002",
    severity="warning",
    description="Desfase de más de una minor entre el template y el clúster.",
    entity="cluster",
    recommendation="Alinea cluster.version con template.kubernetes_version (como mucho una minor de diferencia).",
)
def rule_version_skew(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-VER-002"]
    findings: list[Finding] = []
    for cluster in _k8s_clusters(ctx):
        template = ctx.template_for(cluster)
        if template is None:
            continue
        site, name, rel = _cluster_meta(cluster)
        cluster_minor = _parse_minor(cluster.get("version"))
        template_minor = _parse_minor(template.get("kubernetes_version") or template.get("version"))
        if not cluster_minor or not template_minor:
            continue
        skew = abs(cluster_minor[1] - template_minor[1]) + 100 * abs(cluster_minor[0] - template_minor[0])
        if skew > 1:
            findings.append(
                emit(
                    spec,
                    entity_kind="cluster",
                    entity_id=f"{site}/{name}",
                    site=site,
                    cluster=name,
                    path=rel,
                    graph_id=_graph_cluster(site, name),
                    message=(
                        f"{name} está en {cluster_minor[0]}.{cluster_minor[1]} y el template "
                        f"{template.get('template_name')} en {template_minor[0]}.{template_minor[1]} "
                        f"(desfase {skew})"
                    ),
                )
            )
    return findings


@rule(
    "K8S-VER-003",
    severity="warning",
    description="Charts Helm del template sin versión fijada.",
    entity="template",
    recommendation="Fija charts[].version (no dejes la cadena vacía) para que el alta sea reproducible.",
)
def rule_unpinned_charts(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-VER-003"]
    findings: list[Finding] = []
    seen: set[str] = set()
    for cluster in _k8s_clusters(ctx):
        template = ctx.template_for(cluster)
        if template is None:
            continue
        rel = str(template.get("_path") or "")
        if rel in seen:
            continue
        seen.add(rel)
        missing = [chart["name"] for chart in charts_from_doc(template) if not chart.get("version")]
        if not missing:
            continue
        t_site = str(template.get("_site") or "global")
        t_name = str(template.get("template_name") or template.get("_stem"))
        findings.append(
            emit(
                spec,
                entity_kind="template",
                entity_id=f"{t_site}/{t_name}",
                site=None if t_site == "global" else t_site,
                path=rel,
                graph_id=f"template:{t_site}:{t_name}",
                message=f"template {t_name}: charts sin versión fijada: {', '.join(missing)}",
            )
        )
    return findings


@rule(
    "K8S-TPL-001",
    severity="info",
    description="Clúster Kubernetes sin template_ref.",
    entity="cluster",
    recommendation="Enlaza un cluster-template o documenta por qué el clúster es custom.",
)
def rule_missing_template(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-TPL-001"]
    findings: list[Finding] = []
    for cluster in _k8s_clusters(ctx):
        ref = cluster.get("template_ref")
        if ref:
            continue
        site, name, rel = _cluster_meta(cluster)
        findings.append(
            emit(
                spec,
                entity_kind="cluster",
                entity_id=f"{site}/{name}",
                site=site,
                cluster=name,
                path=rel,
                graph_id=_graph_cluster(site, name),
                message=f"{name} no declara template_ref",
            )
        )
    return findings
