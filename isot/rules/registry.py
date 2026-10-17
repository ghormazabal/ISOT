"""Registro de reglas: id estable → spec."""

from __future__ import annotations

from typing import Any, Callable

from isot.rules.model import Finding, RuleSpec, Severity

RULES: dict[str, RuleSpec] = {}


def rule(
    rule_id: str,
    *,
    severity: Severity,
    description: str,
    entity: str,
    recommendation: str,
) -> Callable[[Callable], Callable]:
    def decorator(fn: Callable) -> Callable:
        spec = RuleSpec(
            id=rule_id,
            default_severity=severity,
            description=description,
            entity=entity,
            recommendation=recommendation,
            evaluate=fn,
        )
        RULES[rule_id] = spec
        return fn

    return decorator


def catalog() -> list[dict[str, str]]:
    rows = []
    for spec in RULES.values():
        rows.append(
            {
                "id": spec.id,
                "severity": spec.default_severity,
                "description": spec.description,
                "entity": spec.entity,
                "recommendation": spec.recommendation,
            }
        )
    rows.sort(key=lambda item: item["id"])
    return rows


def emit(
    spec: RuleSpec,
    *,
    entity_kind: str,
    entity_id: str,
    message: str,
    site: str | None = None,
    cluster: str | None = None,
    path: str | None = None,
    graph_id: str | None = None,
    severity: str | None = None,
    remediation: dict[str, Any] | None = None,
) -> Finding:
    sev = severity or spec.default_severity
    return Finding(
        id=spec.id,
        severity=sev,
        default_severity=spec.default_severity,
        entity_kind=entity_kind,
        entity_id=entity_id,
        message=message,
        recommendation=spec.recommendation,
        site=site,
        cluster=cluster,
        path=path,
        graph_id=graph_id,
        remediation=remediation,
    )
