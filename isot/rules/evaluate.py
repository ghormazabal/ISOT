"""Evalúa el registro de reglas sobre un árbol de inventario."""

from __future__ import annotations

import tempfile
from collections import Counter
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from isot.gitdiff import RefError, resolve_sha, validate_ref
from isot.rules.context import RuleContext, load_context
from isot.rules.inventory import collect_integrity_findings
from isot.rules.model import Finding, RANK
from isot.rules.overrides import apply_overrides, parse_overrides
from isot.rules.registry import RULES, catalog as registry_catalog
from isot.rules.remediation import fill_default_remediations

# Import side effects: register rules.
import isot.rules.inventory  # noqa: F401
import isot.rules.k8s  # noqa: F401


class FindingList(list):
    """Lista de hallazgos que recuerda los sitios del inventario evaluado."""

    site_ids: list[str]

    def __init__(self, items: Iterable[Finding] = (), site_ids: Iterable[str] = ()) -> None:
        super().__init__(items)
        self.site_ids = list(site_ids)


def catalog() -> list[dict[str, str]]:
    return registry_catalog()


def _template_users(ctx: RuleContext) -> dict[str, dict[str, Any]]:
    """{"<t_site>/<t_name>": {"dir": ..., "users": [(site, cluster), ...]}} para templates en uso."""
    out: dict[str, dict[str, Any]] = {}
    for cluster in ctx.clusters:
        template = ctx.template_for(cluster)
        if template is None:
            continue
        t_site = str(template.get("_site") or "global")
        t_name = str(template.get("template_name") or template.get("_stem") or "")
        rel = str(template.get("_path") or "")
        entry = out.setdefault(
            f"{t_site}/{t_name}",
            {"dir": rel.rsplit("/", 1)[0] if "/" in rel else rel, "users": []},
        )
        site_id = str(cluster.get("_site") or "")
        name = str(cluster.get("cluster_name") or cluster.get("_stem") or "")
        if site_id and name and (site_id, name) not in entry["users"]:
            entry["users"].append((site_id, name))
    return out


def _match_template(finding: Finding, users: dict[str, dict[str, Any]]) -> str | None:
    if finding.entity_kind != "template" or finding.cluster or finding.source_template:
        return None
    if finding.entity_id in users:
        return finding.entity_id
    if finding.path:
        for key, entry in users.items():
            base = entry["dir"]
            if base and (finding.path == base or finding.path.startswith(base + "/")):
                return key
    return None


def attribute_template_findings(findings: list[Finding], ctx: RuleContext) -> list[Finding]:
    """Un hallazgo de template se replica en cada clúster que usa ese template.

    La copia queda en el sitio del clúster (``site``/``cluster``), apunta al nodo del
    clúster en el grafo y conserva el origen en ``source_template``. Un template
    que ningún clúster usa queda tal cual: global (``site=None``) o de su sitio.
    """
    users = _template_users(ctx)
    out: list[Finding] = []
    for finding in findings:
        key = _match_template(finding, users)
        if key is None or not users[key]["users"]:
            out.append(finding)
            continue
        for site_id, name in users[key]["users"]:
            out.append(
                replace(
                    finding,
                    site=site_id,
                    cluster=name,
                    graph_id=f"cluster:{site_id}:{name}",
                    source_template=key,
                )
            )
    return out


def scope_findings(findings: Iterable[Finding], site: str | None) -> list[Finding]:
    """Filtro estricto por sitio (sin hallazgos globales)."""
    if not site:
        return list(findings)
    return [item for item in findings if item.site == site]


def evaluate(
    root: Path,
    *,
    site: str | None = None,
    as_of: date | None = None,
    include_silenced: bool = False,
) -> FindingList:
    ctx = load_context(root, as_of=as_of)
    findings: list[Finding] = []
    tables: list[tuple[str | None, str | None, dict[str, dict[str, str]]]] = []

    topo_global: dict[str, dict[str, str]] = {}
    for site_obj in ctx.sites:
        topo = ctx.topologies.get(site_obj.id) or {}
        applied, extra = parse_overrides(
            topo.get("rule_overrides"),
            path=f"sites/{site_obj.id}/topology/index.yaml",
            site=site_obj.id,
            entity_kind="site",
            entity_id=site_obj.id,
        )
        findings.extend(extra)
        if applied:
            tables.append((site_obj.id, None, applied))
            topo_global.update(applied)

    for cluster in ctx.clusters:
        site_id = str(cluster.get("_site") or "")
        name = str(cluster.get("cluster_name") or cluster.get("_stem") or "")
        rel = str(cluster.get("_path") or "")
        applied, extra = parse_overrides(
            cluster.get("rule_overrides"),
            path=rel,
            site=site_id,
            entity_kind="cluster",
            entity_id=f"{site_id}/{name}",
        )
        findings.extend(extra)
        if applied:
            tables.append((site_id, f"{site_id}/{name}", applied))

    for device in ctx.devices:
        applied, extra = parse_overrides(
            device.data.get("rule_overrides"),
            path=device.rel_path,
            site=device.site_id,
            entity_kind="device",
            entity_id=f"{device.site_id}/{device.hostname}",
        )
        findings.extend(extra)
        if applied:
            tables.append((device.site_id, f"{device.site_id}/{device.hostname}", applied))

    findings.extend(collect_integrity_findings(ctx))
    for spec in RULES.values():
        if spec.id.startswith("INV-") and spec.id != "INV-CAB-001":
            continue
        if spec.id == "RULE-OVR-001":
            continue
        findings.extend(spec.evaluate(ctx))

    findings = attribute_template_findings(findings, ctx)
    entity_first = [item for item in tables if item[1]]
    site_level = [item for item in tables if item[1] is None]
    ordered = entity_first + site_level
    findings = apply_overrides(findings, ordered)
    if not include_silenced:
        findings = [item for item in findings if item.severity != "off"]
    findings = scope_findings(findings, site)
    fill_default_remediations(findings)
    findings.sort(
        key=lambda item: (-RANK.get(item.severity, 0), item.id, item.site or "", item.entity_id, item.cluster or "")
    )
    return FindingList(findings, site_ids=[item.id for item in ctx.sites])


def evaluate_overlay(
    root: Path,
    files: list[dict[str, Any]],
    *,
    site: str | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Hallazgos añadidos/resueltos por un wizard.

    Con ``site`` solo cuentan los hallazgos de ese sitio, más los nuevos que no
    se pueden atribuir a ningún sitio (``site=None``). Los errores de otros
    sitios nunca bloquean.
    """
    from isot.wizard import _copy_inventory, apply_files

    base = evaluate(root, as_of=as_of)
    with tempfile.TemporaryDirectory(prefix="isot-rules-") as tmp:
        dest = Path(tmp)
        _copy_inventory(root, dest)
        apply_files(dest, files)
        head = evaluate(dest, as_of=as_of)
    added, resolved = diff_findings(base, head)
    added = wizard_scope(added, site)
    resolved = wizard_scope(resolved, site)
    current = scope_findings(head, site)
    return {
        "site": site,
        "added": [item.to_dict() for item in added],
        "resolved": [item.to_dict() for item in resolved],
        "current": [item.to_dict() for item in current],
        "counts": finding_counts(current),
        "blocked": any(item.severity == "error" for item in added),
    }


def wizard_scope(findings: Iterable[Finding], site: str | None) -> list[Finding]:
    """Hallazgos que importan a un wizard de ``site``: los del sitio y los no atribuibles."""
    if not site:
        return list(findings)
    return [item for item in findings if item.site == site or item.site is None]


def evaluate_ref(repo: Path, ref: str, *, site: str | None = None, as_of: date | None = None) -> FindingList:
    from isot.gitdiff import _materialize

    name = validate_ref(ref)
    sha = resolve_sha(repo, name)
    for tree in _materialize(repo, sha):
        return evaluate(tree, site=site, as_of=as_of)
    raise RefError(f"no se pudo materializar {name}")


def diff_findings(base: Iterable[Finding], head: Iterable[Finding]) -> tuple[list[Finding], list[Finding]]:
    base_map = {item.key(): item for item in base}
    head_map = {item.key(): item for item in head}
    added = [head_map[key] for key in head_map if key not in base_map]
    resolved = [base_map[key] for key in base_map if key not in head_map]
    added.sort(key=lambda item: (-RANK.get(item.severity, 0), item.id, item.entity_id))
    resolved.sort(key=lambda item: (-RANK.get(item.severity, 0), item.id, item.entity_id))
    return added, resolved


def finding_counts(findings: Iterable[Finding]) -> dict[str, int]:
    counter = Counter(item.severity for item in findings if item.severity in RANK)
    return {
        "error": int(counter.get("error", 0)),
        "warning": int(counter.get("warning", 0)),
        "info": int(counter.get("info", 0)),
        "total": sum(int(counter.get(key, 0)) for key in ("error", "warning", "info")),
    }


def counts_by_site(findings: Iterable[Finding], site_ids: Iterable[str] = ()) -> dict[str, dict[str, int]]:
    """Contadores por sitio (incluye sitios sin hallazgos). Los globales no entran."""
    buckets: dict[str, list[Finding]] = {str(item): [] for item in site_ids}
    for item in findings:
        if item.site:
            buckets.setdefault(item.site, []).append(item)
    return {key: finding_counts(buckets[key]) for key in sorted(buckets)}


def payload(findings: list[Finding], *, site: str | None = None) -> dict[str, Any]:
    """Payload de la API.

    Sin ``site``: todos los hallazgos, ``by_site`` con contadores de cada sitio y
    ``global`` con los no atribuibles a un sitio (templates sin uso, inventario).
    Con ``site``: solo ese sitio; ``by_site`` trae solo esa entrada.
    """
    items = scope_findings(findings, site)
    known = list(getattr(findings, "site_ids", []) or [])
    if site:
        site_ids = [site]
        by_site = counts_by_site(items, site_ids)
        by_site = {site: by_site.get(site, finding_counts([]))}
    else:
        by_site = counts_by_site(items, known)
    return {
        "scope": site or "all",
        "counts": finding_counts(items),
        "by_site": by_site,
        "global": finding_counts([item for item in items if not item.site]),
        "findings": [item.to_dict() for item in items],
        "rules": catalog(),
    }
