"""Reglas de renders (REN-*) y K8S-NAD-002 (VLAN del NAD en el trunk del leaf)."""

from __future__ import annotations

from typing import Any

from isot.renders import RENDERS_FILE, RenderService, expand_vlans
from isot.rules.context import RuleContext
from isot.rules.model import Finding
from isot.rules.registry import RULES, emit, rule


def _service(ctx: RuleContext) -> RenderService | None:
    cached = getattr(ctx, "_render_service", None)
    if cached is not None:
        return cached
    if not (ctx.root / RENDERS_FILE).is_file() and not any((s.root / RENDERS_FILE).is_file() for s in ctx.sites):
        return None
    service = RenderService(ctx.root, ctx)
    setattr(ctx, "_render_service", service)
    return service


def _graph_site(site: str | None) -> str | None:
    return f"site:{site}" if site else None


@rule(
    "REN-001",
    severity="error",
    description="Una instancia de render no se puede generar (template inexistente, YAML inválido, variable indefinida con StrictUndefined, error de sintaxis Jinja o ruta de salida inválida).",
    entity="site",
    recommendation="Abre Renders → Plantillas, corrige el template (la vista previa muestra archivo y línea) o la instancia en renders.yaml.",
)
def rule_render_errors(ctx: RuleContext) -> list[Finding]:
    spec = RULES["REN-001"]
    service = _service(ctx)
    if service is None:
        return []
    findings: list[Finding] = []
    for message in service.config.errors:
        findings.append(
            emit(spec, entity_kind="site", entity_id="renders", message=f"renders.yaml: {message}", path=RENDERS_FILE)
        )
    known_sites = {s.id for s in ctx.sites}
    for inst in service.instances():
        if inst.site not in known_sites:
            findings.append(
                emit(
                    spec,
                    entity_kind="site",
                    entity_id=inst.site,
                    message=f"render {inst.id}: sitio desconocido {inst.site}",
                    path=inst.source,
                )
            )
            continue
        result, template = service.render(inst)
        for error in result.errors[:5]:
            where = f"{error.get('file')}:{error.get('line')}" if error.get("line") else str(error.get("file") or "")
            findings.append(
                emit(
                    spec,
                    entity_kind="cluster" if inst.cluster else "site",
                    entity_id=f"{inst.site}/{inst.cluster}" if inst.cluster else inst.site,
                    site=inst.site,
                    cluster=inst.cluster,
                    path=(template.path if template and template.source == "repo" else inst.source),
                    graph_id=f"cluster:{inst.site}:{inst.cluster}" if inst.cluster else _graph_site(inst.site),
                    message=f"render {inst.id} ({inst.template}): {where} {error.get('message')}".strip(),
                )
            )
    return findings


@rule(
    "REN-002",
    severity="warning",
    description="La salida de un render parece contener un secreto (password, token, community, clave privada).",
    entity="site",
    recommendation="ISoT no renderiza secretos: usa una referencia (${VAR}, <secreto>, vault:ruta, secretRef) en el inventario o en params.",
)
def rule_render_secrets(ctx: RuleContext) -> list[Finding]:
    spec = RULES["REN-002"]
    service = _service(ctx)
    if service is None:
        return []
    findings: list[Finding] = []
    known_sites = {s.id for s in ctx.sites}
    for inst in service.instances():
        if inst.site not in known_sites:
            continue
        result, _template = service.render(inst)
        for warning in result.warnings[:5]:
            findings.append(
                emit(
                    spec,
                    entity_kind="cluster" if inst.cluster else "site",
                    entity_id=f"{inst.site}/{inst.cluster}" if inst.cluster else inst.site,
                    site=inst.site,
                    cluster=inst.cluster,
                    path=inst.source,
                    message=f"render {inst.id}: {warning.get('file')}:{warning.get('line')} {warning.get('message')}",
                )
            )
    return findings


LEAF_VLAN_KEYS = ("allowed_vlans", "trunk_vlans", "vlans")


def _leaf_vlans(device: Any) -> set[int] | None:
    for key in LEAF_VLAN_KEYS:
        if key in device.data and device.data.get(key) not in (None, ""):
            return expand_vlans(device.data.get(key))
    return None


def _uplink_leafs(ctx: RuleContext, site: str, device: Any, depth: int = 0) -> list[Any]:
    """Leafs a los que llega el nodo (bonds.leaf_pc, connected_to; VMs vía su hypervisor)."""
    if device is None or depth > 2:
        return []
    names: list[str] = []
    hyper = device.data.get("hypervisor") if isinstance(device.data.get("hypervisor"), dict) else None
    if hyper and hyper.get("device"):
        host = ctx.device(site, str(hyper["device"]))
        return _uplink_leafs(ctx, site, host, depth + 1)
    for bond in device.data.get("bonds") or []:
        if isinstance(bond, dict) and isinstance(bond.get("leaf_pc"), dict):
            names.extend(str(name) for name in bond["leaf_pc"])
    for iface in device.interfaces:
        if iface.connected_to_device:
            names.append(iface.connected_to_device)
    out = []
    for name in dict.fromkeys(names):
        peer = ctx.device(site, name.split("/")[-1])
        if peer is not None and "leaf" in str(peer.role or "").lower():
            out.append(peer)
    return out


@rule(
    "K8S-NAD-002",
    severity="warning",
    description="La VLAN de un NAD no está permitida en el trunk (allowed_vlans / vlans) del leaf al que se conectan los nodos del clúster.",
    entity="cluster",
    recommendation="Agrega la VLAN al trunk del leaf (o del par de leafs) o corrige la vlan del NAD.",
)
def rule_nad_leaf_trunk(ctx: RuleContext) -> list[Finding]:
    spec = RULES["K8S-NAD-002"]
    findings: list[Finding] = []
    for cluster in ctx.clusters:
        if cluster.get("kind") != "kubernetes":
            continue
        site = str(cluster.get("_site") or "")
        name = str(cluster.get("cluster_name") or cluster.get("_stem") or "")
        leafs: dict[str, Any] = {}
        for node in cluster.get("nodes") or []:
            if not isinstance(node, dict) or not node.get("hostname"):
                continue
            for leaf in _uplink_leafs(ctx, site, ctx.device(site, str(node["hostname"]))):
                leafs[leaf.hostname] = leaf
        trunks = {host: vlans for host, leaf in leafs.items() if (vlans := _leaf_vlans(leaf)) is not None}
        if not trunks:
            continue
        for nad in cluster.get("nads") or []:
            if not isinstance(nad, dict) or nad.get("vlan") in (None, ""):
                continue
            try:
                vlan = int(nad["vlan"])
            except (TypeError, ValueError):
                continue
            missing = sorted(host for host, vlans in trunks.items() if vlan not in vlans)
            if not missing:
                continue
            label = f"{nad.get('namespace', '')}/{nad.get('name', '')}"
            findings.append(
                emit(
                    spec,
                    entity_kind="cluster",
                    entity_id=f"{site}/{name}",
                    site=site,
                    cluster=name,
                    path=str(cluster.get("_path") or ""),
                    graph_id=f"cluster:{site}:{name}",
                    message=f"{name}: NAD {label} vlan={vlan} no está en el trunk de {', '.join(missing)}",
                )
            )
    return findings
