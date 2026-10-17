"""Reglas sobre `_ingest/` (formato, procedencia, huecos) y secretos en el inventario."""

from __future__ import annotations

from isot.ingest import format_issues, reference_issues, secret_issues
from isot.rules.context import RuleContext
from isot.rules.model import Finding
from isot.rules.registry import RULES, emit, rule


def _target(entity: str | None) -> tuple[str, str, str | None, str | None]:
    """(entity_kind, entity_id, site, graph_id) a partir de `<tipo>:<sitio>/<nombre>`."""
    if not entity or ":" not in entity:
        return "site", "ingest", None, None
    kind, _, ref = entity.partition(":")
    if kind == "site":
        return "site", ref, ref, f"site:{ref}"
    if kind == "address_space":
        return "site", ref, None, None
    if kind == "template":
        site, _, name = ref.rpartition("/")
        return "template", f"{site or 'global'}/{name}", site or None, f"template:{site or 'global'}:{name}"
    site, _, name = ref.partition("/")
    if kind == "device":
        return "device", f"{site}/{name}", site, f"{site}:{name}"
    if kind == "cluster":
        return "cluster", f"{site}/{name}", site, f"cluster:{site}:{name}"
    return "site", f"{site}/{kind}:{name}", site, f"site:{site}"


def _emit(rule_id: str, issue, severity: str | None = None) -> Finding:
    kind, entity_id, site, graph_id = _target(issue.entity)
    return emit(
        RULES[rule_id],
        entity_kind=kind,
        entity_id=entity_id,
        site=site,
        path=issue.path,
        graph_id=graph_id,
        message=issue.message,
        severity=severity,
    )


@rule(
    "ING-FMT-001",
    severity="error",
    description="Archivo de _ingest/ (state, gaps, provenance) con formato inválido.",
    entity="site",
    recommendation="Sigue el formato de docs/llm/INGEST.md (schemas/ingest-*.schema.json).",
)
def rule_ingest_format(ctx: RuleContext) -> list[Finding]:
    return [
        _emit("ING-FMT-001", issue, None if issue.severity == "error" else issue.severity)
        for issue in format_issues(ctx.root)
    ]


@rule(
    "ING-REF-001",
    severity="warning",
    description="_ingest/ cita una entidad o fuente que no existe.",
    entity="site",
    recommendation="Corrige la clave <tipo>:<sitio>/<nombre> o declara la fuente en _ingest/state.yaml.",
)
def rule_ingest_refs(ctx: RuleContext) -> list[Finding]:
    return [_emit("ING-REF-001", issue) for issue in reference_issues(ctx.root, ctx) if not issue.gap_status]


@rule(
    "ING-GAP-001",
    severity="info",
    description="Dato desconocido (info) o conflicto entre fuentes pendiente de una persona (warning) en _ingest/gaps.yaml.",
    entity="site",
    recommendation="Consigue el dato o la decisión de una persona; luego actualiza el YAML y marca el hueco resolved con resolution.",
)
def rule_ingest_gaps(ctx: RuleContext) -> list[Finding]:
    return [
        _emit("ING-GAP-001", issue, issue.severity)
        for issue in reference_issues(ctx.root, ctx)
        if issue.gap_status
    ]


@rule(
    "ING-SEC-001",
    severity="error",
    description="Posible secreto (password, community, token, clave privada) en el repositorio de datos.",
    entity="site",
    recommendation="Borra el valor (y del historial si ya se publicó) y deja una referencia: ${VAR}, <secreto> o vault:ruta.",
)
def rule_secrets(ctx: RuleContext) -> list[Finding]:
    return [_emit("ING-SEC-001", issue) for issue in secret_issues(ctx.root)]
