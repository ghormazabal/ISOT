"""Overrides de severidad en YAML de clúster, device o sitio."""

from __future__ import annotations

from typing import Any

from isot.rules.model import OVERRIDE_SEVERITIES, Finding
from isot.rules.registry import RULES, emit

OVR_ID = "RULE-OVR-001"


def parse_overrides(raw: Any, *, path: str, site: str | None, entity_kind: str, entity_id: str) -> tuple[dict[str, dict[str, str]], list[Finding]]:
    """Devuelve {rule_id: {severity, reason}} y hallazgos por overrides inválidos."""
    applied: dict[str, dict[str, str]] = {}
    findings: list[Finding] = []
    spec = RULES.get(OVR_ID)
    if not isinstance(raw, list):
        if raw:
            if spec:
                findings.append(
                    emit(
                        spec,
                        entity_kind=entity_kind,
                        entity_id=entity_id,
                        site=site,
                        path=path,
                        graph_id=_graph_id(entity_kind, site, entity_id),
                        message=f"rule_overrides en {path} debe ser una lista; se ignora",
                    )
                )
        return applied, findings
    for item in raw:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("id") or "").strip()
        severity = str(item.get("severity") or "").strip().lower()
        reason = str(item.get("reason") or "").strip()
        if not rule_id:
            continue
        if not reason:
            if spec:
                findings.append(
                    emit(
                        spec,
                        entity_kind=entity_kind,
                        entity_id=entity_id,
                        site=site,
                        path=path,
                        graph_id=_graph_id(entity_kind, site, entity_id),
                        message=f"override de {rule_id} en {path} sin reason; se ignora",
                    )
                )
            continue
        if severity not in OVERRIDE_SEVERITIES:
            if spec:
                findings.append(
                    emit(
                        spec,
                        entity_kind=entity_kind,
                        entity_id=entity_id,
                        site=site,
                        path=path,
                        graph_id=_graph_id(entity_kind, site, entity_id),
                        message=f"override de {rule_id} en {path} con severity={severity!r} inválida; se ignora",
                    )
                )
            continue
        applied[rule_id] = {"severity": severity, "reason": reason}
    return applied, findings


def _graph_id(kind: str, site: str | None, entity_id: str) -> str | None:
    if kind == "site" and site:
        return f"site:{site}"
    if kind == "cluster" and site:
        name = entity_id.split("/", 1)[-1]
        return f"cluster:{site}:{name}"
    if kind == "device" and site:
        host = entity_id.split("/", 1)[-1]
        return f"{site}:{host}"
    if kind == "template":
        return f"template:{entity_id.replace('/', ':')}"
    return None


def apply_overrides(findings: list[Finding], tables: list[tuple[str | None, str | None, dict[str, dict[str, str]]]]) -> list[Finding]:
    """tables: (site_id or None, entity_id or None, overrides). Más específico gana.

    Orden de consulta: entity → site → global.
    """
    out: list[Finding] = []
    for finding in findings:
        match: dict[str, str] | None = None
        entities = {finding.entity_id}
        if finding.source_template and finding.site and finding.cluster:
            # Un hallazgo de template atribuido a un clúster acepta overrides de ese clúster.
            entities.add(f"{finding.site}/{finding.cluster}")
        for scope_site, scope_entity, table in tables:
            if finding.id not in table:
                continue
            if scope_entity and scope_entity not in entities:
                continue
            if scope_site and scope_site != finding.site:
                continue
            match = table[finding.id]
            if scope_entity:
                break
        if not match:
            out.append(finding)
            continue
        severity = match["severity"]
        if severity == "off":
            finding.silenced = True
            finding.override_reason = match["reason"]
            finding.severity = "off"
            continue
        finding.severity = severity
        finding.override_reason = match["reason"]
        out.append(finding)
    return out
