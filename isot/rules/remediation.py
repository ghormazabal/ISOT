"""Acciones de remediación de los hallazgos (legibles por máquina).

Dos tipos:

- ``add_device``: el hallazgo se corrige agregando componentes. La UI abre el
  wizard «Agregar device» con ``site``, ``cluster``, ``role``, ``device_kind`` y
  ``count`` precargados; el direccionamiento lo asigna el allocator como siempre.
- ``open_entity``: no se corrige agregando algo; la UI navega a la entidad
  (``target``: cluster | template | values | device | site).
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from isot.rules.model import Finding

DEFAULT_CP_ROLE = "control-plane"
DEFAULT_WORKER_ROLE = "worker"
DEFAULT_LEAF_ROLE = "server-leaf"
WIZARD_KINDS = {"baremetal", "vm", "network"}

# Reglas cuyo arreglo natural está en los values del clúster (vista «Diff values»).
VALUES_RULES = {"K8S-CNI-001"}


def add_device(
    *,
    site: str,
    cluster: str | None,
    role: str,
    device_kind: str,
    count: int,
    reason: str,
    label: str,
    followup: str | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {
        "kind": "add_device",
        "site": site,
        "cluster": cluster,
        "role": role,
        "device_kind": device_kind if device_kind in WIZARD_KINDS else "baremetal",
        "count": max(1, min(int(count), 32)),
        "reason": reason,
        "label": label,
    }
    if followup:
        action["followup"] = followup
    return action


def open_entity(*, target: str, site: str | None, name: str, label: str) -> dict[str, Any]:
    return {"kind": "open_entity", "target": target, "site": site, "name": name, "label": label}


def wizard_kind(raw: Any) -> str:
    text = str(raw or "").lower()
    if text in {"vm", "virtual-machine", "virtual-machines"}:
        return "vm"
    if text == "network":
        return "network"
    return "baremetal"


def dominant(values: Iterable[tuple[str, str]], default: tuple[str, str]) -> tuple[str, str]:
    """(role, kind) más frecuente; empate → el primero visto."""
    items = [item for item in values if item[0]]
    if not items:
        return default
    counts = Counter(items)
    best = max(counts.values())
    for item in items:
        if counts[item] == best:
            return item
    return default


def default_remediation(finding: Finding) -> dict[str, Any] | None:
    """open_entity a partir de la entidad del hallazgo cuando la regla no declara otra cosa."""
    if finding.source_template or finding.entity_kind == "template":
        t_site, _, t_name = (finding.source_template or finding.entity_id).partition("/")
        if t_name:
            return open_entity(target="template", site=t_site or "global", name=t_name, label=f"Abrir template {t_name}")
    if finding.entity_kind == "cluster" and finding.site and finding.cluster:
        if finding.id in VALUES_RULES:
            return open_entity(
                target="values", site=finding.site, name=finding.cluster, label=f"Abrir values de {finding.cluster}"
            )
        return open_entity(target="cluster", site=finding.site, name=finding.cluster, label=f"Abrir {finding.cluster}")
    if finding.entity_kind == "device" and finding.site:
        host = finding.entity_id.split("/", 1)[-1]
        return open_entity(target="device", site=finding.site, name=host, label=f"Abrir {host}")
    if finding.site:
        return open_entity(target="site", site=finding.site, name=finding.site, label=f"Abrir {finding.site}")
    return None


def fill_default_remediations(findings: list[Finding]) -> list[Finding]:
    for item in findings:
        if item.remediation is None:
            item.remediation = default_remediation(item)
    return findings


def finding_identity(item: dict[str, Any] | Finding) -> tuple[str, str, str, str, str]:
    """Identidad de un hallazgo sin el mensaje (el mensaje cambia al corregir parcialmente)."""
    get = item.get if isinstance(item, dict) else lambda key: getattr(item, key, None)
    return (
        str(get("id") or ""),
        str(get("site") or ""),
        str(get("entity_kind") or ""),
        str(get("entity_id") or ""),
        str(get("cluster") or ""),
    )


def fix_status(target: dict[str, Any] | None, current: list[dict[str, Any]], resolved: list[dict[str, Any]]) -> dict[str, Any] | None:
    """¿El estado propuesto resuelve el hallazgo ``target``? (comparando antes/después)."""
    if not isinstance(target, dict) or not target.get("id"):
        return None
    key = finding_identity(target)
    remaining = [item for item in current if finding_identity(item) == key]
    also = sorted(
        {
            str(item.get("id"))
            for item in resolved
            if finding_identity(item) != key
            and not any(finding_identity(other) == finding_identity(item) for other in current)
        }
    )
    return {
        "id": target.get("id"),
        "site": target.get("site"),
        "cluster": target.get("cluster"),
        "resolved": not remaining,
        "remaining": remaining,
        "also_resolves": also,
    }
