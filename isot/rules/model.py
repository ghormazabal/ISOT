"""Tipos estables del motor de reglas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

Severity = Literal["error", "warning", "info"]
OverrideSeverity = Literal["error", "warning", "info", "off"]
SEVERITIES: tuple[str, ...] = ("error", "warning", "info")
OVERRIDE_SEVERITIES: tuple[str, ...] = ("error", "warning", "info", "off")
RANK = {"error": 3, "warning": 2, "info": 1, "off": 0}


@dataclass(frozen=True)
class RuleSpec:
    id: str
    default_severity: Severity
    description: str
    entity: str
    recommendation: str
    evaluate: Callable[[Any], list["Finding"]] = field(repr=False, compare=False)


@dataclass
class Finding:
    id: str
    severity: str
    entity_kind: str
    entity_id: str
    message: str
    recommendation: str
    site: str | None = None
    cluster: str | None = None
    path: str | None = None
    graph_id: str | None = None
    default_severity: str = ""
    silenced: bool = False
    override_reason: str | None = None

    def key(self) -> tuple[str, str, str, str, str]:
        return (self.id, self.site or "", self.entity_kind, self.entity_id, self.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "severity": self.severity,
            "default_severity": self.default_severity or self.severity,
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "site": self.site,
            "cluster": self.cluster,
            "message": self.message,
            "recommendation": self.recommendation,
            "path": self.path,
            "graph_id": self.graph_id,
            "silenced": self.silenced,
            "override_reason": self.override_reason,
        }


def finding_from_dict(data: dict[str, Any]) -> Finding:
    return Finding(
        id=str(data.get("id") or ""),
        severity=str(data.get("severity") or "info"),
        entity_kind=str(data.get("entity_kind") or "cluster"),
        entity_id=str(data.get("entity_id") or ""),
        message=str(data.get("message") or ""),
        recommendation=str(data.get("recommendation") or ""),
        site=data.get("site"),
        cluster=data.get("cluster"),
        path=data.get("path"),
        graph_id=data.get("graph_id"),
        default_severity=str(data.get("default_severity") or ""),
        silenced=bool(data.get("silenced")),
        override_reason=data.get("override_reason"),
    )
