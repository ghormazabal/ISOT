"""IDX-001: el índice commiteado falta o no refleja las fuentes del repo."""

from __future__ import annotations

from isot.indexer import MANIFEST_NAME, index_status
from isot.rules.context import RuleContext
from isot.rules.model import Finding
from isot.rules.registry import RULES, emit, rule


@rule(
    "IDX-001",
    severity="warning",
    description="El índice del repo de datos (sites/_sites_index.json, _device_index.json, _artifacts_index.json, _index_manifest.json…) falta o no coincide con los archivos: la web lo recalcula en memoria (más lento) y el CI falla.",
    entity="site",
    recommendation="Ejecuta `isot index` (o «Reindexar» en la web, que crea una rama wizard/…) y confirma los índices en el mismo commit que los archivos.",
)
def rule_index_fresh(ctx: RuleContext) -> list[Finding]:
    spec = RULES["IDX-001"]
    status = index_status(ctx.root)
    if status["fresh"]:
        return []
    parts = []
    if not status["manifest"]:
        parts.append(f"falta {MANIFEST_NAME}")
    if status["missing"]:
        parts.append("faltan " + ", ".join(status["missing"][:5]))
    if status["manifest"] and status["stale"]:
        parts.append("cambiaron " + ", ".join(status["stale"][:6]) + (" …" if len(status["stale"]) > 6 else ""))
    return [
        emit(
            spec,
            entity_kind="site",
            entity_id="index",
            message="índice desactualizado: " + "; ".join(parts or ["no coincide"]),
            path=MANIFEST_NAME,
        )
    ]
