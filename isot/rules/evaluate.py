"""Evalúa el registro de reglas sobre un árbol de inventario."""

from __future__ import annotations

import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from isot.gitdiff import RefError, resolve_sha, validate_ref
from isot.rules.context import RuleContext, load_context
from isot.rules.inventory import collect_integrity_findings
from isot.rules.model import Finding, RANK
from isot.rules.overrides import apply_overrides, parse_overrides
from isot.rules.registry import RULES, catalog as registry_catalog

# Import side effects: register rules.
import isot.rules.inventory  # noqa: F401
import isot.rules.k8s  # noqa: F401


def catalog() -> list[dict[str, str]]:
    return registry_catalog()


def evaluate(
    root: Path,
    *,
    site: str | None = None,
    as_of: date | None = None,
    include_silenced: bool = False,
) -> list[Finding]:
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

    entity_first = [item for item in tables if item[1]]
    site_level = [item for item in tables if item[1] is None]
    ordered = entity_first + site_level
    findings = apply_overrides(findings, ordered)
    if not include_silenced:
        findings = [item for item in findings if item.severity != "off"]
    if site:
        findings = [item for item in findings if item.site == site or item.site is None]
    findings.sort(key=lambda item: (-RANK.get(item.severity, 0), item.id, item.site or "", item.entity_id))
    return findings


def evaluate_overlay(
    root: Path,
    files: list[dict[str, Any]],
    *,
    site: str | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    from isot.wizard import _copy_inventory, apply_files

    base = evaluate(root, site=site, as_of=as_of)
    with tempfile.TemporaryDirectory(prefix="isot-rules-") as tmp:
        dest = Path(tmp)
        _copy_inventory(root, dest)
        apply_files(dest, files)
        head = evaluate(dest, site=site, as_of=as_of)
    added, resolved = diff_findings(base, head)
    return {
        "added": [item.to_dict() for item in added],
        "resolved": [item.to_dict() for item in resolved],
        "current": [item.to_dict() for item in head],
        "blocked": any(item.severity == "error" for item in added),
    }


def evaluate_ref(repo: Path, ref: str, *, site: str | None = None, as_of: date | None = None) -> list[Finding]:
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


def payload(findings: list[Finding]) -> dict[str, Any]:
    return {
        "counts": finding_counts(findings),
        "findings": [item.to_dict() for item in findings],
        "rules": catalog(),
    }
