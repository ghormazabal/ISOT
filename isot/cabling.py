"""Política de cableado (connection_rules) para wizards y validador.

No inventa hardware: usa un modelo genérico (puertos por leaf) y los
roles ya declarados. Sitios sin `connection_rules` se infieren de los
hermanos (pares en leaf_pc, dual-home, OOB).
"""

from __future__ import annotations

from typing import Any

from isot.allocator import next_ip, next_port

ARCH_SPINE_LEAF = "spine-leaf"
ARCH_COLLAPSED = "collapsed-core"
ARCH_CUSTOM = "custom"

COMPUTE_ROLES = {"worker", "apps-worker", "k8s-worker", "tenant", "compute", "edge-server"}
LEAF_ROLES = {"server-leaf", "leaf", "tor", "pair-leaf"}
SPINE_ROLES = {"spine"}


def default_policy(
    architecture: str = ARCH_SPINE_LEAF,
    *,
    spines: int = 2,
    leaf_pairs: int = 2,
    unpaired_leafs: int = 0,
    mgmt_switches: int = 1,
    dcgw: int = 1,
    ports_per_leaf: int = 48,
    pair_policy: str = "fill-first",
) -> dict[str, Any]:
    arch = architecture if architecture in {ARCH_SPINE_LEAF, ARCH_COLLAPSED, ARCH_CUSTOM} else ARCH_SPINE_LEAF
    if arch == ARCH_COLLAPSED:
        spines = 0
        leaf_pairs = max(1, int(leaf_pairs) or 1)
        unpaired_leafs = 0
    if arch == ARCH_CUSTOM:
        spines = max(0, int(spines))
        leaf_pairs = max(0, int(leaf_pairs))
    rules: list[dict[str, Any]] = []
    if arch != ARCH_CUSTOM:
        rules.append(
            {
                "id": "compute-mlag",
                "roles": sorted(COMPUTE_ROLES),
                "attach": "leaf-pair",
                "leafs_per_node": 2,
                "bond": "lacp",
                "bond_name": "bondC",
                "description": "Cada worker/tenant/compute se dual-homea a 2 leafs de un mismo par (MLAG o EVPN-ESI) con bond LACP",
            }
        )
        rules.append(
            {
                "id": "storage-bond",
                "roles": sorted(COMPUTE_ROLES),
                "attach": "leaf-pair",
                "leafs_per_node": 2,
                "bond": "lacp",
                "bond_name": "bondS",
                "purpose": "storage",
                "description": "Storage usa bondS dedicado al mismo par de leafs",
            }
        )
        if spines:
            rules.append(
                {
                    "id": "leaf-to-spine",
                    "roles": sorted(LEAF_ROLES),
                    "attach": "all-spines",
                    "description": "Cada leaf se conecta a todos los spines",
                }
            )
        rules.append(
            {
                "id": "oob-bmc",
                "roles": sorted(COMPUTE_ROLES | LEAF_ROLES | SPINE_ROLES),
                "attach": "mgmt",
                "via": "oob",
                "description": "OOB/BMC va al switch MGMT",
            }
        )
    return {
        "architecture": arch,
        "pair_policy": pair_policy if pair_policy in {"fill-first", "round-robin"} else "fill-first",
        "ports_per_leaf": int(ports_per_leaf) or 48,
        "fabric": {
            "spines": max(0, int(spines)),
            "leaf_pairs": max(0, int(leaf_pairs)),
            "unpaired_leafs": max(0, int(unpaired_leafs)),
            "mgmt_switches": max(0, int(mgmt_switches)),
            "dcgw": max(0, int(dcgw)),
        },
        "rules": rules,
    }


def policy_block(policy: dict[str, Any]) -> dict[str, Any]:
    """Bloque persistible en topology/index.yaml (sin pares derivados)."""
    return {
        "architecture": policy.get("architecture"),
        "pair_policy": policy.get("pair_policy"),
        "ports_per_leaf": policy.get("ports_per_leaf"),
        "fabric": dict(policy.get("fabric") or {}),
        "rules": [dict(item) for item in (policy.get("rules") or []) if isinstance(item, dict)],
    }


def policy_from_topology(topology: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(topology, dict):
        return None
    block = topology.get("connection_rules")
    if isinstance(block, dict) and (block.get("rules") or block.get("fabric") or block.get("architecture")):
        merged = default_policy(str(block.get("architecture") or topology.get("architecture") or ARCH_SPINE_LEAF))
        merged.update({key: value for key, value in block.items() if key != "rules"})
        if isinstance(block.get("rules"), list):
            merged["rules"] = [item for item in block["rules"] if isinstance(item, dict)]
        if isinstance(block.get("fabric"), dict):
            merged["fabric"] = {**(merged.get("fabric") or {}), **block["fabric"]}
        return merged
    return None


def load_or_infer(topology: dict[str, Any] | None, devices: list[dict[str, Any]]) -> dict[str, Any]:
    declared = policy_from_topology(topology)
    if declared is not None:
        declared["inferred"] = False
        if not declared.get("pairs"):
            declared["pairs"] = discover_pairs(devices)
        return declared
    inferred = infer_policy(devices, topology)
    inferred["inferred"] = True
    return inferred


def policy_from_site_body(body: dict[str, Any]) -> dict[str, Any]:
    arch = str(body.get("architecture") or ARCH_SPINE_LEAF)
    raw = body.get("connection_rules")
    if isinstance(raw, dict):
        merged = policy_from_topology({"architecture": arch, "connection_rules": raw})
        if merged:
            return merged
    defaults = {
        ARCH_SPINE_LEAF: {"spines": 2, "leaf_pairs": 2, "unpaired_leafs": 0, "mgmt_switches": 1, "dcgw": 1},
        ARCH_COLLAPSED: {"spines": 0, "leaf_pairs": 1, "unpaired_leafs": 0, "mgmt_switches": 1, "dcgw": 0},
        ARCH_CUSTOM: {"spines": 0, "leaf_pairs": 0, "unpaired_leafs": 0, "mgmt_switches": 0, "dcgw": 0},
    }
    base = defaults.get(arch, defaults[ARCH_SPINE_LEAF])

    def take(name: str, fallback: int) -> int:
        value = body.get(name)
        if value is None:
            return fallback
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    return default_policy(
        arch,
        spines=take("spines", base["spines"]),
        leaf_pairs=take("leaf_pairs", base["leaf_pairs"]),
        unpaired_leafs=take("unpaired_leafs", base["unpaired_leafs"]),
        mgmt_switches=take("mgmt_switches", base["mgmt_switches"]),
        dcgw=take("dcgw", base["dcgw"]),
        ports_per_leaf=take("ports_per_leaf", 48),
        pair_policy=str(body.get("pair_policy") or "fill-first"),
    )


def role_matches(role: str | None, roles: list[str] | None, extra: list[str] | None = None) -> bool:
    pool = {str(item).lower() for item in (roles or [])}
    names = {str(role or "").lower()}
    for item in extra or []:
        names.add(str(item).lower())
    names.discard("")
    if not pool:
        return False
    return bool(names & pool)


def is_leaf_role(role: str | None) -> bool:
    text = str(role or "").lower()
    return text in LEAF_ROLES or "leaf" in text


def leafs_of(device: dict[str, Any]) -> list[str]:
    found: list[str] = []

    def add(name: str | None) -> None:
        text = str(name or "").strip()
        if text and text not in found:
            found.append(text)

    for bond in device.get("bonds") or []:
        if not isinstance(bond, dict):
            continue
        leaf_pc = bond.get("leaf_pc") or {}
        if isinstance(leaf_pc, dict):
            for peer in leaf_pc:
                add(str(peer))
    for iface in device.get("networking") or []:
        if not isinstance(iface, dict):
            continue
        connected = iface.get("connected_to") if isinstance(iface.get("connected_to"), dict) else {}
        add(connected.get("device"))
    return found


def discover_pairs(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pares MLAG a partir de leaf_pc de 2 peers o del campo pair/leaf_pair."""
    seen: dict[tuple[str, ...], str] = {}
    order: list[tuple[str, ...]] = []
    declared: dict[str, list[str]] = {}
    skip_ids = {"spines", "mgmt", "border", "unpaired", ""}

    def add_pair(peers: list[str], pair_id: str | None) -> None:
        cleaned = tuple(sorted({item for item in peers if item}))
        if len(cleaned) < 2 or cleaned in seen:
            return
        seen[cleaned] = str(pair_id or f"PAIR{len(order) + 1}")
        order.append(cleaned)

    for doc in devices:
        host = str(doc.get("hostname") or "")
        role = str(doc.get("role") or "")
        pair_id = str(doc.get("leaf_pair") or "")
        mate = str(doc.get("pair") or "")
        if is_leaf_role(role) and pair_id not in skip_ids:
            bucket = declared.setdefault(pair_id, [])
            if host and host not in bucket:
                bucket.append(host)
        if is_leaf_role(role) and mate:
            add_pair([host, mate], pair_id if pair_id not in skip_ids else None)
        for bond in doc.get("bonds") or []:
            if not isinstance(bond, dict):
                continue
            leaf_pc = bond.get("leaf_pc") or {}
            if not isinstance(leaf_pc, dict) or len(leaf_pc) < 2:
                continue
            add_pair([str(item) for item in leaf_pc.keys()], pair_id if pair_id not in skip_ids else None)
    for pair_id, members in declared.items():
        add_pair(members, pair_id)
    pairs = [{"id": seen[peers], "leafs": list(peers)} for peers in order]
    if not pairs:
        leafs = [
            str(doc.get("hostname"))
            for doc in devices
            if is_leaf_role(str(doc.get("role") or ""))
            or (str(doc.get("kind")) == "network" and "leaf" in str(doc.get("hostname") or "").lower())
        ]
        for index in range(0, len(leafs) - 1, 2):
            peers = [leafs[index], leafs[index + 1]]
            pairs.append({"id": f"PAIR{len(pairs) + 1}", "leafs": peers})
    return pairs


def infer_policy(devices: list[dict[str, Any]], topology: dict[str, Any] | None = None) -> dict[str, Any]:
    """Infiera connection_rules a partir de hermanos (pares, dual-home, OOB)."""
    del topology
    pairs = discover_pairs(devices)
    spines = [doc for doc in devices if str(doc.get("role") or "").lower() in SPINE_ROLES]
    mgmt = [
        doc
        for doc in devices
        if "mgmt" in str(doc.get("role") or "").lower() or "mgmt" in str(doc.get("hostname") or "").lower()
    ]
    dcgw = [doc for doc in devices if "dcgw" in str(doc.get("role") or "").lower() or "dcgw" in str(doc.get("hostname") or "").lower()]
    architecture = ARCH_SPINE_LEAF if spines else ARCH_COLLAPSED
    policy = default_policy(
        architecture,
        spines=len(spines) or (2 if architecture == ARCH_SPINE_LEAF else 0),
        leaf_pairs=max(len(pairs), 1),
        mgmt_switches=max(len(mgmt), 1),
        dcgw=len(dcgw),
    )
    policy["inferred"] = True
    policy["pairs"] = pairs
    computes = [doc for doc in devices if role_matches(str(doc.get("role")), list(COMPUTE_ROLES), doc.get("k8s_roles") or [])]
    sequence = []
    for doc in sorted(computes, key=lambda item: str(item.get("hostname") or "")):
        peers = tuple(sorted(leafs_of(doc)[:2]))
        sequence.append(peers)
    unique_seq: list[tuple[str, ...]] = []
    for peers in sequence:
        if not unique_seq or unique_seq[-1] != peers:
            unique_seq.append(peers)
    if len(unique_seq) >= 2 and len(sequence) >= 4:
        alternates = unique_seq[0] != unique_seq[1] and sequence[0] != sequence[1] and sequence[1] != sequence[2]
        policy["pair_policy"] = "round-robin" if alternates else "fill-first"
    return policy


def host_capacity(policy: dict[str, Any]) -> int:
    uplinks = int((policy.get("fabric") or {}).get("spines") or 0)
    bonds = sum(1 for rule in policy.get("rules") or [] if rule.get("attach") == "leaf-pair")
    bonds = max(int(bonds), 1)
    ports = int(policy.get("ports_per_leaf") or 48)
    return max(1, (ports - uplinks) // bonds)


def pair_occupancy(pair: dict[str, Any], devices: list[dict[str, Any]]) -> int:
    leafs = set(pair.get("leafs") or [])
    hosts = set()
    for doc in devices:
        if not role_matches(str(doc.get("role")), list(COMPUTE_ROLES), doc.get("k8s_roles") or []):
            continue
        if leafs and leafs <= set(leafs_of(doc)):
            hosts.add(str(doc.get("hostname")))
        elif str(doc.get("leaf_pair")) == str(pair.get("id")):
            hosts.add(str(doc.get("hostname")))
    return len(hosts)


def pick_pair(
    policy: dict[str, Any],
    devices: list[dict[str, Any]],
    *,
    rr_index: int = 0,
) -> dict[str, Any] | None:
    pairs = list(policy.get("pairs") or [])
    if not pairs:
        pairs = discover_pairs(devices)
        policy["pairs"] = pairs
    if not pairs:
        return None
    cap = host_capacity(policy)
    mode = str(policy.get("pair_policy") or "fill-first")
    if mode == "round-robin":
        return pairs[int(rr_index) % len(pairs)]
    for pair in pairs:
        if pair_occupancy(pair, devices) < cap:
            return pair
    return pairs[-1]


def retarget_leafs(data: dict[str, Any], new_leafs: list[str]) -> dict[str, str]:
    """Reescribe leaf_pc y connected_to hacia un par nuevo. Devuelve mapa viejo→nuevo."""
    old = leafs_of(data)
    mapping: dict[str, str] = {}
    if not new_leafs:
        return mapping
    if len(old) >= 2 and len(new_leafs) >= 2:
        mapping[old[0]] = new_leafs[0]
        mapping[old[1]] = new_leafs[1]
        for extra in old[2:]:
            mapping[extra] = extra
    elif old:
        mapping[old[0]] = new_leafs[0]
        for index, name in enumerate(old[1:], start=1):
            mapping[name] = new_leafs[index % len(new_leafs)]
    for bond in data.get("bonds") or []:
        if not isinstance(bond, dict) or not isinstance(bond.get("leaf_pc"), dict):
            continue
        bond["leaf_pc"] = {mapping.get(str(peer), str(peer)): port for peer, port in bond["leaf_pc"].items()}
    for iface in data.get("networking") or []:
        if not isinstance(iface, dict):
            continue
        connected = iface.get("connected_to")
        if isinstance(connected, dict) and connected.get("device"):
            connected["device"] = mapping.get(str(connected["device"]), connected["device"])
    if mapping:
        data["leaf_pair"] = data.get("leaf_pair")
    return mapping


def check_device(device: dict[str, Any], policy: dict[str, Any], pairs: list[dict[str, Any]]) -> list[str]:
    """Warnings (no errores) si el device viola connection_rules."""
    warnings: list[str] = []
    hostname = str(device.get("hostname") or "?")
    role = str(device.get("role") or "")
    extras = device.get("k8s_roles") if isinstance(device.get("k8s_roles"), list) else []
    peers = leafs_of(device)
    pair_sets = [set(item.get("leafs") or []) for item in pairs]
    for rule in policy.get("rules") or []:
        if not isinstance(rule, dict) or not role_matches(role, rule.get("roles"), extras):
            continue
        attach = str(rule.get("attach") or "")
        rid = str(rule.get("id") or attach)
        if attach == "leaf-pair":
            needed = int(rule.get("leafs_per_node") or 2)
            if len(peers) < needed:
                warnings.append(
                    f"{hostname} viola {rid}: conectado a {len(peers)} leaf(s), la regla pide {needed} (par MLAG/ESI)"
                )
            elif needed >= 2 and pair_sets and not any(
                set(peers[:needed]) == item or set(peers[:needed]) <= item for item in pair_sets if item
            ):
                warnings.append(f"{hostname} viola {rid}: {', '.join(peers[:needed]) or '—'} no es un par declarado")
        if attach == "mgmt" and rule.get("via") == "oob":
            oob = device.get("oob") if isinstance(device.get("oob"), dict) else {}
            if not oob.get("switch"):
                warnings.append(f"{hostname} viola {rid}: no declara oob.switch hacia MGMT")
        if attach == "all-spines":
            spines_needed = int((policy.get("fabric") or {}).get("spines") or 0)
            spine_peers = [item for item in peers if "spine" in item.lower() or "spne" in item.lower()]
            if spines_needed and len(spine_peers) < spines_needed and len(peers) < spines_needed:
                warnings.append(
                    f"{hostname} viola {rid}: {len(spine_peers) or len(peers)} uplink(s) a spine, se esperaban {spines_needed}"
                )
    return warnings


def collect_connection_warnings(
    devices: list[dict[str, Any]],
    topology: dict[str, Any] | None,
) -> list[str]:
    policy = policy_from_topology(topology)
    if policy is None:
        return []
    pairs = list(policy.get("pairs") or []) or discover_pairs(devices)
    warnings: list[str] = []
    for doc in devices:
        warnings.extend(check_device(doc, policy, pairs))
    return warnings


def mgmt_name(devices: list[dict[str, Any]], site_name: str) -> str | None:
    for doc in devices:
        role = str(doc.get("role") or "").lower()
        host = str(doc.get("hostname") or "")
        if "mgmt" in role or "MGMT" in host:
            return host
    fabric_mgmt = [doc for doc in devices if str(doc.get("role")) == "mgmt-switch"]
    if fabric_mgmt:
        return str(fabric_mgmt[0].get("hostname"))
    return f"{site_name}-MGMT001"


def build_fabric_devices(site_name: str, policy: dict[str, Any]) -> list[dict[str, Any]]:
    """Spines, pares de leafs, MGMT y DCGW genéricos según la arquitectura."""
    fabric = policy.get("fabric") or {}
    n_spines = int(fabric.get("spines") or 0)
    n_pairs = int(fabric.get("leaf_pairs") or 0)
    n_unpaired = int(fabric.get("unpaired_leafs") or 0)
    n_mgmt = int(fabric.get("mgmt_switches") or 0)
    n_dcgw = int(fabric.get("dcgw") or 0)
    devices: list[dict[str, Any]] = []
    spines = [f"{site_name}-SPINE{index:03d}" for index in range(1, n_spines + 1)]
    for host in spines:
        devices.append(
            {
                "hostname": host,
                "role": "spine",
                "kind": "network",
                "vendor": "generic",
                "model": "spine-32x100g",
                "leaf_pair": "spines",
                "os": "nos",
            }
        )
    pairs: list[dict[str, Any]] = []
    leaf_index = 1
    for pair_n in range(1, n_pairs + 1):
        a = f"{site_name}-LEAF{leaf_index:03d}"
        b = f"{site_name}-LEAF{leaf_index + 1:03d}"
        first = leaf_index
        leaf_index += 2
        pair_id = f"PAIR{pair_n}"
        pairs.append({"id": pair_id, "leafs": [a, b]})
        for offset, host in enumerate((a, b)):
            mate = b if offset == 0 else a
            networking = []
            for s_i, spine in enumerate(spines, start=1):
                networking.append(
                    {
                        "interface": f"Ethernet{s_i}",
                        "connected_to": {"device": spine, "port": f"Ethernet{first + offset}"},
                    }
                )
            devices.append(
                {
                    "hostname": host,
                    "role": "server-leaf",
                    "kind": "network",
                    "vendor": "generic",
                    "model": "leaf-48x25g",
                    "pair": mate,
                    "leaf_pair": pair_id,
                    "os": "nos",
                    "networking": networking,
                }
            )
    for _ in range(n_unpaired):
        host = f"{site_name}-LEAF{leaf_index:03d}"
        leaf_index += 1
        networking = []
        for s_i, spine in enumerate(spines, start=1):
            networking.append(
                {
                    "interface": f"Ethernet{s_i}",
                    "connected_to": {"device": spine, "port": f"Ethernet{leaf_index - 1}"},
                }
            )
        devices.append(
            {
                "hostname": host,
                "role": "server-leaf",
                "kind": "network",
                "vendor": "generic",
                "model": "leaf-48x25g",
                "leaf_pair": "unpaired",
                "os": "nos",
                "networking": networking,
            }
        )
    for index in range(1, n_mgmt + 1):
        devices.append(
            {
                "hostname": f"{site_name}-MGMT{index:03d}",
                "role": "mgmt-switch",
                "kind": "network",
                "vendor": "generic",
                "model": "mgmt-48x1g",
                "leaf_pair": "mgmt",
                "os": "nos",
            }
        )
    for index in range(1, n_dcgw + 1):
        devices.append(
            {
                "hostname": f"{site_name}-DCGW{index:03d}",
                "role": "dcgw",
                "kind": "network",
                "vendor": "generic",
                "model": "leaf-32x100g",
                "leaf_pair": "border",
                "os": "nos",
            }
        )
    policy["pairs"] = pairs
    return devices


def build_compute(
    hostname: str,
    *,
    pair: dict[str, Any],
    policy: dict[str, Any],
    topology: dict[str, Any],
    used_ips: set[str],
    used_ports: dict[str, set[str]],
    cluster: str | None,
    mgmt_switch: str | None,
    role: str = "worker",
) -> dict[str, Any]:
    leafs = list(pair.get("leafs") or [])
    if len(leafs) < 2:
        raise ValueError("el par de leafs necesita 2 miembros")
    a, b = leafs[0], leafs[1]
    ports_needed = [rule for rule in policy.get("rules") or [] if rule.get("attach") == "leaf-pair"]
    pc_numbers: list[int] = []
    taken = set()
    for leaf in leafs:
        for port in used_ports.get(leaf, set()):
            text = str(port)
            if "PortChannel" not in text and "port-channel" not in text.lower():
                continue
            digits = "".join(ch for ch in text if ch.isdigit())
            if digits:
                taken.add(int(digits[-2:]) if len(digits) >= 2 else int(digits))
    next_n = 1
    while len(pc_numbers) < max(len(ports_needed), 1):
        if next_n not in taken:
            pc_numbers.append(next_n)
        next_n += 1
    bonds = []
    for index, rule in enumerate(ports_needed):
        pc = f"PortChannel{pc_numbers[index]}"
        bonds.append(
            {
                "name": str(rule.get("bond_name") or f"bond{index}"),
                "leaf_pc": {a: pc, b: pc},
                "allowed_vlans": [2000] if rule.get("purpose") == "storage" else [],
            }
        )
        for leaf in leafs:
            used_ports.setdefault(leaf, set()).add(pc)

    def subnet_named(*needles: str) -> dict[str, Any] | None:
        for item in topology.get("subnets") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").upper()
            if any(needle.upper() in name for needle in needles):
                return item
        return None

    networking: list[dict[str, Any]] = []
    mgmt = subnet_named("MGMT_SW", "MGMT")
    stor = subnet_named("STOR", "CLOUD_STOR")
    data_net = subnet_named("APP_DATA", "DATA", "POD")
    ctrl = subnet_named("APP_CTRL", "CTRL")
    bond_c = next((item for item in bonds if item["name"] != "bondS"), bonds[0] if bonds else None)
    bond_s = next((item for item in bonds if item["name"] == "bondS"), None)

    def add_iface(name: str, subnet: dict[str, Any] | None, bond: dict[str, Any] | None) -> None:
        if not subnet or not subnet.get("cidr"):
            return
        ip_plan = next_ip(str(subnet["cidr"]), used_ips, after=str(subnet.get("gateway") or ""))
        used_ips.add(ip_plan.value.split("/", 1)[0])
        iface: dict[str, Any] = {
            "interface": str(subnet.get("name") or name),
            "vlan": subnet.get("vlan"),
            "ip": ip_plan.value,
            "gateway": subnet.get("gateway") or "",
        }
        if bond:
            iface["parent"] = bond["name"]
            pc = next(iter(bond["leaf_pc"].values()))
            iface["connected_to"] = {"device": a, "port": pc}
        networking.append(iface)

    add_iface("MGMT_SW", mgmt, bond_c)
    add_iface("APP_CTRL", ctrl, bond_c)
    add_iface("APP_DATA", data_net, bond_c)
    add_iface("CLOUD_STOR", stor, bond_s)
    oob_net = subnet_named("MGMT_HW", "OOB", "CLOUD_MGMT_HW")
    oob: dict[str, Any] | None = None
    if oob_net and oob_net.get("cidr") and mgmt_switch:
        ip_plan = next_ip(str(oob_net["cidr"]), used_ips, after=str(oob_net.get("gateway") or ""))
        used_ips.add(ip_plan.value.split("/", 1)[0])
        port_plan = next_port(used_ports.get(mgmt_switch, set()), "Ethernet1")
        used_ports.setdefault(mgmt_switch, set()).add(port_plan.value)
        oob = {"type": "bmc", "ip": ip_plan.value, "switch": mgmt_switch, "port": port_plan.value}
    data: dict[str, Any] = {
        "hostname": hostname,
        "role": role,
        "k8s_roles": ["worker"],
        "k8s_name": hostname.lower(),
        "kind": "baremetal",
        "vendor": "generic",
        "model": "compute-2u",
        "os": "Ubuntu 22.04 LTS",
        "leaf_pair": pair.get("id"),
        "bonds": bonds,
        "networking": networking,
    }
    if cluster:
        data["cluster_ref"] = cluster
    if oob:
        data["oob"] = oob
    return data
