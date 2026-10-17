"""Reglas de integridad ya existentes (IP, hostname, puerto, refs, cableado, Helm)."""

from __future__ import annotations

from collections import defaultdict

from isot.cabling import collect_connection_warnings
from isot.rules.context import RuleContext
from isot.rules.model import Finding
from isot.rules.registry import RULES, emit, rule


@rule(
    "INV-IP-001",
    severity="error",
    description="IPs duplicadas en un sitio o en un address_space compartido.",
    entity="device",
    recommendation="Asigna otra IP libre de la misma subred o documenta el solape si es un anycast deliberado (override con reason).",
)
def rule_ip(_ctx: RuleContext) -> list[Finding]:
    return []


@rule(
    "INV-HOST-001",
    severity="error",
    description="Hostnames duplicados dentro de un sitio.",
    entity="device",
    recommendation="Renombra uno de los YAML; el hostname es la clave del dispositivo.",
)
def rule_host(_ctx: RuleContext) -> list[Finding]:
    return []


@rule(
    "INV-PORT-001",
    severity="error",
    description="Mismo puerto de un peer usado por dos dispositivos.",
    entity="device",
    recommendation="Elige el siguiente PortChannel/Ethernet libre en ese leaf o switch.",
)
def rule_port(_ctx: RuleContext) -> list[Finding]:
    return []


@rule(
    "INV-REF-001",
    severity="error",
    description="Referencias rotas (cluster_ref, connected_to, template_ref, nodos de clúster).",
    entity="device",
    recommendation="Corrige el puntero o crea el YAML destino. No dejes refs a hostnames que no existen.",
)
def rule_ref(_ctx: RuleContext) -> list[Finding]:
    return []


@rule(
    "INV-HELM-001",
    severity="error",
    description="Charts Helm con values_ref inválido, duplicados o values_overrides a un release inexistente.",
    entity="template",
    recommendation="Ajusta charts[] / values_ref o el release del override para que coincida con el template.",
)
def rule_helm(_ctx: RuleContext) -> list[Finding]:
    return []


@rule(
    "INV-CAB-001",
    severity="warning",
    description="El device no cumple las connection_rules del sitio.",
    entity="device",
    recommendation="Revisa dual-home, par de leafs, bond y OOB según topology/index.yaml, o guarda las reglas inferidas.",
)
def rule_cabling(ctx: RuleContext) -> list[Finding]:
    spec = RULES["INV-CAB-001"]
    by_site: dict[str, list] = defaultdict(list)
    for device in ctx.devices:
        by_site[device.site_id].append(device.data)
    findings: list[Finding] = []
    for site in ctx.sites:
        for warning in collect_connection_warnings(by_site.get(site.id) or [], ctx.topologies.get(site.id)):
            host = ""
            for token in warning.split():
                if token.isupper() or "-" in token:
                    host = token.strip(":")
                    break
            device = ctx.device(site.id, host) if host else None
            entity_id = f"{site.id}/{device.hostname}" if device else site.id
            findings.append(
                emit(
                    spec,
                    entity_kind="device" if device else "site",
                    entity_id=entity_id,
                    site=site.id,
                    path=device.rel_path if device else f"sites/{site.id}/topology/index.yaml",
                    graph_id=f"{site.id}:{device.hostname}" if device else f"site:{site.id}",
                    message=warning,
                )
            )
    return findings


@rule(
    "RULE-OVR-001",
    severity="warning",
    description="rule_overrides inválido (sin reason, severity desconocida o tipo incorrecto).",
    entity="cluster",
    recommendation="Añade reason y usa severity info|warning|error|off. Sin reason el override no aplica.",
)
def rule_ovr(_ctx: RuleContext) -> list[Finding]:
    return []


def collect_integrity_findings(ctx: RuleContext) -> list[Finding]:
    from isot.validator import collect_issues

    findings: list[Finding] = []
    for issue in collect_issues(ctx.root, ctx.devices):
        if issue.startswith("IP duplicada"):
            spec = RULES["INV-IP-001"]
            kind = "site"
        elif "hostname duplicado" in issue:
            spec = RULES["INV-HOST-001"]
            kind = "site"
        elif issue.startswith("puerto duplicado"):
            spec = RULES["INV-PORT-001"]
            kind = "device"
        elif any(
            token in issue
            for token in ("values_ref", "values_overrides", "Chart duplicado", "depends_on=")
        ):
            spec = RULES["INV-HELM-001"]
            kind = "template"
        else:
            spec = RULES["INV-REF-001"]
            kind = "device"
        site_id = _guess_site(issue, ctx)
        findings.append(
            emit(
                spec,
                entity_kind=kind,
                entity_id=site_id or "inventory",
                site=site_id,
                path=_guess_path(issue),
                graph_id=f"site:{site_id}" if site_id else None,
                message=issue,
            )
        )
    return findings


def _guess_site(issue: str, ctx: RuleContext) -> str | None:
    for site in ctx.sites:
        token = f"{site.id}/"
        if token in issue or f"en {site.id}" in issue or f"sites/{site.id}/" in issue:
            return site.id
    return None


def _guess_path(issue: str) -> str | None:
    for token in issue.split():
        cleaned = token.strip("():,")
        if cleaned.endswith(".yaml") or cleaned.endswith(".yml"):
            return cleaned
    return None
