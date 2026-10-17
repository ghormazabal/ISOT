"""Validador de integridad referencial e IPAM del inventario ISoT.

Comprueba, por sitio:
  * IPs duplicadas (dirección sin prefijo CIDR, incluye oob).
  * hostnames duplicados.
  * puertos duplicados (connected_to, leaf_pc, oob) sobre el mismo peer.
  * cluster_ref → sites/<sitio>/clusters/<ref>.yaml
  * connected_to.device (+ site opcional) → hostname en ese sitio
  * template_ref → cluster-templates/ (global o del sitio)
  * values_ref de charts existen, no salen de la carpeta y son YAML válido
  * values_overrides apuntan a releases que el template declara
  * NADs y pools MetalLB caen en VLANs/subredes de la topology del sitio
  * nodos listados en un clúster kubernetes existen y tienen cluster_ref coherente
  * colisiones de IP entre sitios que declaran el mismo address_space

`vendor`, `model` y `os` son cadenas opacas: no hay enumeración de marcas
ni de NOS de fabricante.
"""

from __future__ import annotations

import argparse
import ipaddress
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from isot.cabling import collect_connection_warnings
from isot.helm import collect_chart_issues
from isot.inventory import (
    AddressSpace,
    Device,
    Site,
    cluster_file,
    load_catalog,
    load_clusters,
    load_devices,
    load_templates,
    load_topology,
    parse_ref,
    resolve_template_path,
    site_by_id,
)
from isot.paths import find_repo_root

OK_EXIT = 0
FAIL_EXIT = 1


def _ip_only(value: str) -> str | None:
    text = str(value).strip()
    if not text:
        return None
    return text.split("/", 1)[0].strip() or None


def _parse_address_token(token: str) -> list[str]:
    token = token.strip()
    if not token:
        return []
    if "-" in token and not token.startswith("-"):
        left, right = token.split("-", 1)
        start = _ip_only(left)
        end = _ip_only(right)
        return [item for item in (start, end) if item]
    single = _ip_only(token)
    return [single] if single else []


def _ip_in_cidr(ip_text: str, cidr: str) -> bool:
    try:
        return ipaddress.ip_address(ip_text) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def _topology_maps(topology: dict[str, Any]) -> tuple[dict[Any, dict], dict[str, dict]]:
    vlans: dict[Any, dict] = {}
    for item in topology.get("vlans") or []:
        if isinstance(item, dict) and item.get("id") is not None:
            vlans[item["id"]] = item
            try:
                vlans[int(item["id"])] = item
            except (TypeError, ValueError):
                pass
    subnets: dict[str, dict] = {}
    for item in topology.get("subnets") or []:
        if isinstance(item, dict) and item.get("name"):
            subnets[str(item["name"])] = item
    return vlans, subnets


def collect_issues(repo_root: Path, devices: list[Device] | None = None) -> list[str]:
    sites, spaces = load_catalog(repo_root)
    devices = devices if devices is not None else load_devices(repo_root, sites)
    issues: list[str] = []
    clusters = load_clusters(repo_root, sites)
    templates = load_templates(repo_root, sites)
    topologies = {site.id: load_topology(site) for site in sites}

    hosts_by_site: dict[str, dict[str, Device]] = defaultdict(dict)
    hosts_lower: dict[str, dict[str, Device]] = defaultdict(dict)
    for device in devices:
        hosts_by_site[device.site_id][device.hostname] = device
        hosts_lower[device.site_id][device.hostname.lower()] = device

    template_keys = {
        (str(doc.get("_site")), str(doc.get("template_name") or doc.get("_stem"))) for doc in templates
    }

    ip_owners: dict[tuple[str, str], list[str]] = defaultdict(list)
    hostname_owners: dict[tuple[str, str], list[str]] = defaultdict(list)
    port_owners: dict[tuple[str, str, str], dict[str, str]] = defaultdict(dict)

    def add_port(device: Device, peer: str | None, port: str | None, peer_site: str | None, via: str) -> None:
        if not peer or not port:
            return
        parsed = parse_ref(str(peer), device.site_id)
        if parsed is None:
            p_site, p_host = peer_site or device.site_id, str(peer)
        else:
            p_site, p_host = parsed
        if peer_site:
            p_site = peer_site
        key = (p_site, p_host, str(port))
        if device.hostname not in port_owners[key]:
            port_owners[key][device.hostname] = (
                f"{device.site_id}/{device.hostname} ({device.rel_path}) {via}"
            )

    for device in devices:
        hostname_owners[(device.site_id, device.hostname.lower())].append(
            f"{device.site_id}/{device.hostname} ({device.rel_path})"
        )
        oob = device.data.get("oob") if isinstance(device.data.get("oob"), dict) else None
        if oob:
            oob_ip = _ip_only(str(oob.get("ip") or ""))
            if oob_ip:
                ip_owners[(device.site_id, oob_ip)].append(
                    f"{device.site_id}/{device.hostname} ({device.rel_path}) oob"
                )
            add_port(device, oob.get("switch"), oob.get("port"), None, "oob")
        for bond in device.data.get("bonds") or []:
            if not isinstance(bond, dict):
                continue
            leaf_pc = bond.get("leaf_pc") or {}
            if not isinstance(leaf_pc, dict):
                continue
            for switch, port in leaf_pc.items():
                add_port(device, str(switch), None if port is None else str(port), None, f"bond {bond.get('name')}")
        for iface in device.interfaces:
            address = iface.ip_address
            if not address:
                continue
            label = (
                f"{device.site_id}/{device.hostname} ({device.rel_path}) "
                f"interfaz {iface.name or '?'}"
            )
            ip_owners[(device.site_id, address)].append(label)

        if device.cluster_ref:
            target_site_id = device.cluster_ref_site or device.site_id
            target_site = site_by_id(sites, target_site_id)
            if target_site is None:
                issues.append(
                    f"Referencia rota cluster_ref={device.cluster_ref!r} "
                    f"site={target_site_id!r} en {device.hostname} ({device.rel_path}): "
                    f"sitio desconocido"
                )
            else:
                expected = cluster_file(target_site, device.cluster_ref)
                if not expected.is_file():
                    issues.append(
                        f"Referencia rota cluster_ref={device.cluster_ref!r} en "
                        f"{device.site_id}/{device.hostname} ({device.rel_path}): "
                        f"no existe {expected.relative_to(repo_root).as_posix()}"
                    )

        for iface in device.interfaces:
            peer = iface.connected_to_device
            if not peer:
                continue
            peer_site = iface.connected_to_site or device.site_id
            if peer not in hosts_by_site.get(peer_site, {}):
                port = iface.connected_to_port or "?"
                issues.append(
                    f"Referencia rota connected_to.device={peer!r} site={peer_site!r} en "
                    f"{device.site_id}/{device.hostname} interfaz {iface.name or '?'} "
                    f"({device.rel_path}) → {peer_site}/{peer}:{port}. "
                    f"No hay ningún YAML de dispositivo con ese hostname en ese sitio"
                )
            add_port(device, peer, iface.connected_to_port, iface.connected_to_site, f"interfaz {iface.name or '?'}")

    for (site_id, key), owners in sorted(hostname_owners.items()):
        if len(owners) > 1:
            lines = "\n".join(f"    - {owner}" for owner in owners)
            issues.append(f"hostname duplicado en {site_id}: {key}\n{lines}")

    for (site_id, address), owners in sorted(ip_owners.items()):
        if len(owners) > 1:
            lines = "\n".join(f"    - {owner}" for owner in owners)
            issues.append(f"IP duplicada en {site_id}: {address}\n{lines}")

    for (peer_site, peer, port), owners in sorted(port_owners.items()):
        if len(owners) > 1:
            lines = "\n".join(f"    - {label}" for label in owners.values())
            issues.append(f"puerto duplicado en {peer_site}/{peer}:{port}\n{lines}")

    issues.extend(_shared_space_collisions(spaces, sites, topologies, devices))

    for cluster in clusters:
        name = str(cluster.get("cluster_name") or cluster.get("_stem"))
        site_id = str(cluster.get("_site"))
        rel = cluster.get("_path", f"sites/{site_id}/clusters/{name}.yaml")
        template_ref = cluster.get("template_ref")
        if template_ref:
            template_ref = str(template_ref).strip()
        if template_ref:
            expected = resolve_template_path(repo_root, sites, site_id, template_ref)
            if expected is None:
                issues.append(
                    f"Referencia rota template_ref={template_ref!r} en {rel}: "
                    f"no existe en cluster-templates/ del sitio ni en el catálogo global"
                )
            else:
                parsed = parse_ref(template_ref, site_id)
                t_site, t_name = parsed if parsed else (site_id, template_ref)
                if t_site == "global":
                    t_site = "global"
                elif (t_site, t_name) not in template_keys and ("global", t_name) not in template_keys:
                    issues.append(
                        f"template_ref={template_ref!r} en {rel} no declara template_name coincidente"
                    )

        topology = topologies.get(site_id) or {}
        vlans, subnets = _topology_maps(topology)
        topo_rel = f"sites/{site_id}/topology/index.yaml"

        for nad in cluster.get("nads") or []:
            if not isinstance(nad, dict):
                continue
            label = f"{nad.get('namespace', '')}/{nad.get('name', '')}"
            vlan = nad.get("vlan")
            if vlan is not None and vlan != "":
                try:
                    vlan_key: Any = int(vlan)
                except (TypeError, ValueError):
                    vlan_key = vlan
                if vlan_key not in vlans and vlan not in vlans:
                    issues.append(
                        f"NAD {label} en {rel} usa vlan={vlan} que no está en {topo_rel}"
                    )
            subnet_name = nad.get("subnet")
            if subnet_name and str(subnet_name) not in subnets:
                issues.append(
                    f"NAD {label} en {rel} usa subnet={subnet_name!r} que no está en {topo_rel}"
                )

        metallb = cluster.get("metallb") or {}
        if isinstance(metallb, dict):
            for pool in metallb.get("pools") or []:
                if not isinstance(pool, dict):
                    continue
                pool_name = pool.get("name") or "?"
                subnet_name = pool.get("subnet")
                subnet = subnets.get(str(subnet_name)) if subnet_name else None
                if subnet_name and subnet is None:
                    issues.append(
                        f"Pool MetalLB {pool_name} en {rel} usa subnet={subnet_name!r} "
                        f"que no está en {topo_rel}"
                    )
                cidr = (subnet or {}).get("cidr") if subnet else ""
                for spec in pool.get("addresses") or []:
                    for ip_text in _parse_address_token(str(spec)):
                        if cidr and not _ip_in_cidr(ip_text, str(cidr)):
                            issues.append(
                                f"Pool MetalLB {pool_name} en {rel}: {ip_text} "
                                f"no cae en {subnet_name} ({cidr})"
                            )
                        elif not subnet_name:
                            matched = any(
                                s.get("cidr") and _ip_in_cidr(ip_text, str(s["cidr"]))
                                for s in subnets.values()
                            )
                            if subnets and not matched:
                                issues.append(
                                    f"Pool MetalLB {pool_name} en {rel}: {ip_text} "
                                    f"no cae en ninguna subred de {topo_rel}"
                                )

        if cluster.get("kind") != "kubernetes":
            continue
        missing_ok = {str(item) for item in (cluster.get("hosts_missing") or [])}
        for node in cluster.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            node_name = node.get("hostname") or node.get("name")
            if not node_name:
                continue
            node_name = str(node_name)
            if node_name in missing_ok:
                continue
            device = hosts_lower[site_id].get(node_name.lower())
            if device is None:
                issues.append(
                    f"Nodo {node_name} listado en {rel} no existe en sites/{site_id}/devices/"
                )
                continue
            if device.cluster_ref != name or (device.cluster_ref_site or device.site_id) != site_id:
                issues.append(
                    f"Nodo {device.hostname} listado en {rel} tiene "
                    f"cluster_ref={device.cluster_ref!r} "
                    f"(se esperaba {name!r} en {site_id})"
                )

    issues.extend(collect_chart_issues(repo_root, sites, templates, clusters))
    return issues


def collect_warnings(repo_root: Path, devices: list[Device] | None = None) -> list[str]:
    """Avisos opcionales (connection_rules). No fallan el validador."""
    sites, _ = load_catalog(repo_root)
    devices = devices if devices is not None else load_devices(repo_root, sites)
    by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for device in devices:
        by_site[device.site_id].append(device.data)
    topologies = {site.id: load_topology(site) for site in sites}
    warnings: list[str] = []
    for site in sites:
        warnings.extend(collect_connection_warnings(by_site.get(site.id) or [], topologies.get(site.id)))
    return warnings


def _shared_space_collisions(
    spaces: list[AddressSpace],
    sites: list[Site],
    topologies: dict[str, dict[str, Any]],
    devices: list[Device],
) -> list[str]:
    issues: list[str] = []
    site_ids = {site.id for site in sites}
    for space in spaces:
        members = [sid for sid in space.sites if sid in site_ids]
        if len(members) < 2:
            continue
        cidrs: list[str] = []
        for sid in members:
            _, subnets = _topology_maps(topologies.get(sid) or {})
            for name in space.match_subnets:
                cidr = (subnets.get(name) or {}).get("cidr")
                if cidr:
                    cidrs.append(str(cidr))
        if not cidrs and not space.match_vlans:
            continue
        owners: dict[str, list[str]] = defaultdict(list)
        for device in devices:
            if device.site_id not in members:
                continue
            for iface in device.interfaces:
                address = iface.ip_address
                if not address:
                    continue
                in_space = any(_ip_in_cidr(address, cidr) for cidr in cidrs)
                if not in_space and space.match_vlans:
                    in_space = iface.vlan in space.match_vlans
                if not in_space:
                    continue
                label = (
                    f"{device.site_id}/{device.hostname} ({device.rel_path}) "
                    f"interfaz {iface.name or '?'}"
                )
                owners[address].append(label)
        for address, found in sorted(owners.items()):
            sites_present = {item.split("/", 1)[0] for item in found}
            if len(found) > 1 and len(sites_present) > 1:
                lines = "\n".join(f"    - {owner}" for owner in found)
                issues.append(
                    f"IP duplicada en address_space {space.id}: {address}\n{lines}"
                )
    return issues


def format_report(
    repo_root: Path,
    devices: list[Device],
    issues: list[str],
    warnings: list[str] | None = None,
    findings: list | None = None,
) -> str:
    from isot.rules.evaluate import evaluate, finding_counts

    if findings is None:
        findings = evaluate(repo_root)
    counts = finding_counts(findings)
    warnings = warnings or []
    ip_count = sum(1 for d in devices for i in d.interfaces if i.ip_address)
    sites = sorted({device.site_id for device in devices})
    header = [
        "ISoT validador de integridad",
        "============================",
        f"Raíz: {repo_root}",
        f"Sitios: {', '.join(sites) or '—'}",
        f"Dispositivos analizados: {len(devices)}",
        f"Interfaces con IP: {ip_count}",
        f"Hallazgos: {counts['error']} error(es), {counts['warning']} aviso(s), {counts['info']} info",
        "",
    ]
    grouped: dict[str, list[str]] = {"error": [], "warning": [], "info": []}
    for finding in findings:
        if finding.severity not in grouped:
            continue
        label = {"error": "ERROR", "warning": "WARN", "info": "INFO"}[finding.severity]
        entity = finding.entity_id
        if getattr(finding, "source_template", None) and finding.site and finding.cluster:
            entity = f"{entity} (vía {finding.site}/{finding.cluster})"
        grouped[finding.severity].append(f"[{label}] {finding.id} {entity}: {finding.message}")
    body: list[str] = []
    for key in ("error", "warning", "info"):
        body.extend(grouped[key])
    del issues, warnings
    if counts["error"]:
        body.append("")
        body.append(
            f"Resultado: FALLÓ ({counts['error']} error(es), {counts['warning']} aviso(s), {counts['info']} info)"
        )
        return "\n".join(header + body) + "\n"
    extra = []
    if counts["warning"]:
        extra.append(f"{counts['warning']} aviso(s)")
    if counts["info"]:
        extra.append(f"{counts['info']} info")
    if extra:
        body.append("")
        body.append(f"Resultado: OK ({', '.join(extra)})")
        return "\n".join(header + body) + "\n"
    header.append("Resultado: OK (sin colisiones de IP ni referencias rotas)")
    return "\n".join(header) + "\n"


def findings_json(repo_root: Path) -> dict:
    from isot.rules.evaluate import payload as findings_payload, evaluate

    return findings_payload(evaluate(repo_root))


def github_annotations(repo_root: Path) -> str:
    from isot.rules.evaluate import evaluate

    lines: list[str] = []
    level = {"error": "error", "warning": "warning", "info": "notice"}
    seen: set[str] = set()
    for finding in evaluate(repo_root):
        kind = level.get(finding.severity)
        if not kind:
            continue
        path = finding.path or "sites.yaml"
        title = finding.id
        message = finding.message.replace("%", "%25").replace("\n", "%0A")
        line = f"::{kind} file={path},title={title}::{message}"
        # Un template usado por varios clústeres genera una sola annotation en su archivo.
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def validate(repo_root: Path, *, strict: bool = False, fmt: str = "text") -> int:
    from isot.rules.evaluate import evaluate, finding_counts

    devices = load_devices(repo_root)
    findings = evaluate(repo_root)
    counts = finding_counts(findings)
    if fmt == "json":
        import json

        sys.stdout.write(json.dumps(findings_json(repo_root), ensure_ascii=False, indent=2) + "\n")
    else:
        issues = [item.message for item in findings if item.severity == "error"]
        warnings = [item.message for item in findings if item.severity == "warning"]
        sys.stdout.write(format_report(repo_root, devices, issues, warnings, findings=findings))
        import os

        if os.environ.get("GITHUB_ACTIONS") and fmt != "json":
            sys.stdout.write(github_annotations(repo_root))
    if counts["error"]:
        return FAIL_EXIT
    if strict and counts["warning"]:
        return FAIL_EXIT
    return OK_EXIT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Valida IPs únicas, referencias lógicas y reglas de control de ISoT."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Raíz del repositorio ISoT (por defecto: detección automática).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="También falla si hay warnings.",
    )
    parser.add_argument(
        "--format",
        dest="fmt",
        choices=("text", "json"),
        default="text",
        help="Salida text (por defecto) o json.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = args.root.resolve() if args.root else find_repo_root()
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return FAIL_EXIT
    return validate(root, strict=args.strict, fmt=args.fmt)


if __name__ == "__main__":
    raise SystemExit(main())
