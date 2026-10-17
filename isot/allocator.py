"""IPAM y hostnames deterministas para los wizards de ISoT.

No inventa rangos: solo elige el siguiente valor libre a partir de lo
ya declarado (hermanos del mismo rol, topology, MetalLB, gateways).
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

HOST_TAIL_RE = re.compile(r"^(.*?)(\d+)$")
PORT_TAIL_RE = re.compile(r"^(.*?)(\d+)$")
HOST_TOKEN_RE = re.compile(r"([A-Za-z]+)[^A-Za-z]*$")

# Convenio de hostname por rol (ajustado a los roles reales del inventario).
ROLE_HOSTNAME_TOKEN: dict[str, str] = {
    "apps-worker": "NODE",
    "worker": "NODE",
    "k8s-worker": "NODE",
    "tenant": "NODE",
    "compute": "NODE",
    "control": "CTRL",
    "control-plane": "CTRL",
    "edge-server": "CTRL",
    "platform-server": "CTRL",
    "apps-control": "CTRL",
    "platform-manager": "CTRL",
    "storage": "STOR",
    "storage-server": "STOR",
    "leaf": "LEAF",
    "server-leaf": "LEAF",
    "tor": "LEAF",
    "pair-leaf": "LEAF",
    "spine": "SPNE",
    "mgmt": "MGMT",
    "mgmt-switch": "MGMT",
    "management-switch": "MGMT",
    "dcgw": "DCGW",
}
KNOWN_HOSTNAME_TOKENS = frozenset(ROLE_HOSTNAME_TOKEN.values())


class AllocatorError(ValueError):
    """No hay un siguiente valor libre o el patrón no se puede inferir."""


@dataclass
class HostnamePlan:
    value: str
    origin: str
    prefix: str
    number: int
    width: int


@dataclass
class IpPlan:
    value: str
    cidr: str
    origin: str
    interface: str | None = None
    vlan: Any = None


@dataclass
class PortPlan:
    value: str
    peer: str
    origin: str


@dataclass
class AutoField:
    path: str
    value: Any
    auto: bool
    origin: str


def parse_hostname(hostname: str) -> tuple[str, int, int] | None:
    text = str(hostname).strip()
    match = HOST_TAIL_RE.match(text)
    if not match:
        return None
    prefix, digits = match.group(1), match.group(2)
    return prefix, int(digits), len(digits)


def format_hostname(prefix: str, number: int, width: int) -> str:
    return f"{prefix}{number:0{width}d}"


def token_from_hostname(hostname: str) -> str | None:
    """Último tramo alfabético del prefijo: SITE2-CTRL002 → CTRL."""
    parts = parse_hostname(hostname)
    if parts is None:
        return None
    prefix = parts[0].rstrip("-_")
    match = HOST_TOKEN_RE.search(prefix)
    return match.group(1).upper() if match else None


def token_for_role(role: str | None) -> str | None:
    text = str(role or "").strip().lower()
    return ROLE_HOSTNAME_TOKEN.get(text)


def roles_for_token(token: str) -> list[str]:
    needle = str(token or "").upper()
    return sorted({role for role, item in ROLE_HOSTNAME_TOKEN.items() if item == needle})


def same_role_family(left: str | None, right: str | None) -> bool:
    a = str(left or "").strip().lower()
    b = str(right or "").strip().lower()
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = token_for_role(a), token_for_role(b)
    return bool(ta and tb and ta == tb)


def convention_prefix(site_name: str, role: str | None, token: str | None = None) -> str:
    piece = (token or token_for_role(role) or "NODE").upper()
    name = str(site_name or "SITE").strip() or "SITE"
    return f"{name}-{piece}"


def hostname_role_warning(hostname: str, role: str | None) -> str | None:
    """Si el prefijo del hostname pertenece a otro rol, avisar (no bloquear)."""
    if not role:
        return None
    token = token_from_hostname(hostname)
    expected = token_for_role(role)
    if not token or not expected or token == expected:
        return None
    if token not in KNOWN_HOSTNAME_TOKENS:
        return None
    others = ", ".join(roles_for_token(token)[:4]) or token
    return (
        f"el prefijo {token} corresponde a otro rol ({others}), no a {role}; "
        f"el convenio de {role} es {expected}"
    )


def next_hostname(
    existing: Iterable[str],
    *,
    sibling: str | None = None,
    prefix: str | None = None,
    width: int | None = None,
) -> HostnamePlan:
    """Siguiente hostname: número más bajo libre de ese prefijo (rellena huecos).

    El patrón sale de `sibling`, o de `prefix`+`width` (convenio / otro sitio).
    """
    names = [str(item) for item in existing if str(item).strip()]
    parsed = [(name, parse_hostname(name)) for name in names]
    parsed = [(name, parts) for name, parts in parsed if parts]
    chosen_width = int(width or 3)
    if sibling:
        sib_parts = parse_hostname(sibling)
        if sib_parts is None:
            raise AllocatorError(f"hostname {sibling!r} no tiene cola numérica")
        prefix, _, chosen_width = sib_parts
    elif prefix:
        prefix = str(prefix)
    elif parsed:
        prefix, _, chosen_width = parsed[-1][1]
    else:
        raise AllocatorError("no hay patrón de hostname que heredar")
    numbers = sorted({parts[1] for _, parts in parsed if parts[0] == prefix})
    if not numbers:
        number = 1
        origin = f"primer hostname del patrón {prefix}{'#' * chosen_width}"
    else:
        holes = [item for item in range(numbers[0], numbers[-1]) if item not in numbers]
        if holes:
            number = holes[0]
            origin = f"hueco {format_hostname(prefix, number, chosen_width)} (patrón {sibling or prefix})"
        else:
            number = numbers[-1] + 1
            origin = f"siguiente a {format_hostname(prefix, numbers[-1], chosen_width)}"
    value = format_hostname(prefix, number, chosen_width)
    if value in names:
        raise AllocatorError(f"hostname {value} ya existe")
    return HostnamePlan(value=value, origin=origin, prefix=prefix, number=number, width=chosen_width)


def _ip_set(values: Iterable[str]) -> set[ipaddress.IPv4Address]:
    found: set[ipaddress.IPv4Address] = set()
    for raw in values:
        if not raw:
            continue
        text = str(raw).split("/", 1)[0].strip()
        try:
            addr = ipaddress.ip_address(text)
        except ValueError:
            continue
        if isinstance(addr, ipaddress.IPv4Address):
            found.add(addr)
    return found


def expand_range_token(token: str) -> list[str]:
    text = str(token).strip()
    if not text:
        return []
    if "-" in text and not text.startswith("-"):
        left, right = text.split("-", 1)
        try:
            start = ipaddress.ip_address(left.split("/", 1)[0].strip())
            end = ipaddress.ip_address(right.split("/", 1)[0].strip())
        except ValueError:
            return []
        if int(end) < int(start):
            return []
        return [str(ipaddress.ip_address(value)) for value in range(int(start), int(end) + 1)]
    single = text.split("/", 1)[0].strip()
    try:
        ipaddress.ip_address(single)
    except ValueError:
        return []
    return [single]


def next_ip(
    cidr: str,
    used: Iterable[str],
    *,
    reserved: Iterable[str] = (),
    after: str | None = None,
) -> IpPlan:
    try:
        network = ipaddress.ip_network(str(cidr), strict=False)
    except ValueError as exc:
        raise AllocatorError(f"CIDR inválido: {cidr}") from exc
    if not isinstance(network, ipaddress.IPv4Network):
        raise AllocatorError("solo se autoasignan IPv4")
    blocked = _ip_set(used) | _ip_set(reserved)
    blocked.add(network.network_address)
    if network.broadcast_address != network.network_address:
        blocked.add(network.broadcast_address)
    hosts = list(network.hosts())
    if not hosts:
        raise AllocatorError(f"subred {cidr} sin hosts usables")
    start_index = 0
    if after:
        try:
            after_addr = ipaddress.ip_address(str(after).split("/", 1)[0].strip())
        except ValueError as exc:
            raise AllocatorError(f"IP de referencia inválida: {after}") from exc
        if after_addr in network:
            start_index = hosts.index(after_addr) + 1 if after_addr in hosts else 0
    ordered = hosts[start_index:] + hosts[:start_index]
    for candidate in ordered:
        if candidate not in blocked:
            origin = (
                f"siguiente IP libre en {cidr} después de {after}"
                if after
                else f"siguiente IP libre en {cidr}"
            )
            return IpPlan(value=f"{candidate}/{network.prefixlen}", cidr=str(network), origin=origin)
    raise AllocatorError(f"subred llena: {cidr}")


def parse_port(port: str) -> tuple[str, int, int] | None:
    match = PORT_TAIL_RE.match(str(port).strip())
    if not match:
        return None
    prefix, digits = match.group(1), match.group(2)
    return prefix, int(digits), len(digits)


def next_port(used: Iterable[str], sibling: str) -> PortPlan:
    parts = parse_port(sibling)
    if parts is None:
        raise AllocatorError(f"puerto {sibling!r} no tiene cola numérica")
    prefix, _, width = parts
    taken = set()
    numbers: list[int] = []
    for item in used:
        parsed = parse_port(str(item))
        if parsed and parsed[0] == prefix:
            numbers.append(parsed[1])
            taken.add(parsed[1])
    start = parse_port(sibling)[1] + 1
    number = start
    while number in taken:
        number += 1
    value = f"{prefix}{number:0{width}d}"
    origin = f"siguiente puerto libre después de {sibling}"
    return PortPlan(value=value, peer="", origin=origin)


def next_port_for_peers(
    used_by_peer: dict[str, set[str]],
    peers: list[str],
    sibling_port: str,
) -> str:
    """Mismo número de PortChannel/Ethernet libre en todos los peers del par."""
    parts = parse_port(sibling_port)
    if parts is None:
        raise AllocatorError(f"puerto {sibling_port!r} no tiene cola numérica")
    prefix, start_n, width = parts
    number = start_n + 1
    while True:
        candidate = f"{prefix}{number:0{width}d}"
        if all(candidate not in used_by_peer.get(peer, set()) for peer in peers):
            return candidate
        number += 1
        if number - start_n > 4096:
            raise AllocatorError(f"no hay puerto libre para {peers} después de {sibling_port}")


def next_port_bundle(
    used_by_peer: dict[str, set[str]],
    peers: list[str],
    sibling_ports: list[str],
) -> int:
    """Delta que mantiene el offset entre PortChannels del hermano.

    Si el hermano usa 37-42 y esos números están ocupados, el siguiente
    bloque libre es +6 → 43-48, no un único PC reutilizado en todos los bonds.
    """
    parsed: list[tuple[str, int, int]] = []
    for port in sibling_ports:
        parts = parse_port(port)
        if parts is None:
            raise AllocatorError(f"puerto {port!r} no tiene cola numérica")
        parsed.append(parts)
    if not parsed:
        raise AllocatorError("no hay puertos de hermano para el bloque")
    delta = 1
    while delta <= 4096:
        taken = False
        for prefix, number, width in parsed:
            candidate = f"{prefix}{number + delta:0{width}d}"
            if any(candidate in used_by_peer.get(peer, set()) for peer in peers):
                taken = True
                break
        if not taken:
            return delta
        delta += 1
    raise AllocatorError(f"no hay bloque de puertos libre para {peers}")


def reserved_from_topology(topology: dict[str, Any]) -> set[str]:
    reserved: set[str] = set()
    for subnet in topology.get("subnets") or []:
        if not isinstance(subnet, dict):
            continue
        gw = subnet.get("gateway")
        if gw:
            reserved.add(str(gw).split("/", 1)[0])
    return reserved


def reserved_from_clusters(clusters: Iterable[dict[str, Any]]) -> set[str]:
    reserved: set[str] = set()
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        for node in cluster.get("nodes") or []:
            if isinstance(node, dict) and node.get("internal_ip"):
                reserved.add(str(node["internal_ip"]).split("/", 1)[0])
        metallb = cluster.get("metallb") or {}
        if not isinstance(metallb, dict):
            continue
        for pool in metallb.get("pools") or []:
            if not isinstance(pool, dict):
                continue
            for spec in pool.get("addresses") or []:
                reserved.update(expand_range_token(str(spec)))
        services = metallb.get("services") or {}
        if isinstance(services, dict):
            for value in services.values():
                reserved.add(str(value).split("/", 1)[0])
    return reserved


def used_ips_from_devices(devices: Iterable[dict[str, Any]]) -> set[str]:
    used: set[str] = set()
    for device in devices:
        if not isinstance(device, dict):
            continue
        oob = device.get("oob")
        if isinstance(oob, dict) and oob.get("ip"):
            used.add(str(oob["ip"]).split("/", 1)[0])
        for iface in device.get("networking") or []:
            if isinstance(iface, dict) and iface.get("ip"):
                used.add(str(iface["ip"]).split("/", 1)[0])
    return used


def used_ports_by_peer(devices: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    used: dict[str, set[str]] = {}
    def add(peer: str | None, port: str | None) -> None:
        if not peer or not port:
            return
        used.setdefault(str(peer), set()).add(str(port))

    for device in devices:
        if not isinstance(device, dict):
            continue
        oob = device.get("oob")
        if isinstance(oob, dict):
            add(oob.get("switch"), oob.get("port"))
        for bond in device.get("bonds") or []:
            if not isinstance(bond, dict):
                continue
            leaf_pc = bond.get("leaf_pc") or {}
            if isinstance(leaf_pc, dict):
                for peer, port in leaf_pc.items():
                    add(str(peer), str(port))
        for iface in device.get("networking") or []:
            if not isinstance(iface, dict):
                continue
            connected = iface.get("connected_to") or {}
            if isinstance(connected, dict):
                add(connected.get("device"), connected.get("port"))
    return used


def subnet_for_vlan(topology: dict[str, Any], vlan: Any) -> dict[str, Any] | None:
    for item in topology.get("subnets") or []:
        if isinstance(item, dict) and item.get("vlan") == vlan:
            return item
        if isinstance(item, dict):
            try:
                if int(item.get("vlan")) == int(vlan):
                    return item
            except (TypeError, ValueError):
                pass
    return None


@dataclass
class DeviceDraft:
    hostname: str
    kind: str
    rel_path: str
    data: dict[str, Any]
    fields: list[AutoField] = field(default_factory=list)
    sibling: str | None = None
    pattern_source: str = "sibling"
    pattern_note: str | None = None
    warnings: list[str] = field(default_factory=list)
