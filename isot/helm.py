"""Charts Helm de un cluster-template: carga perezosa de values y diff.

El YAML del template declara `charts[]` (nombre, repo, chart, versión,
namespace, orden, depends_on). Los values van inline o en
`values/<release>.yaml` vía `values_ref`. El clúster declara
`values_overrides` por release. Nada se inventa: si no hay values, el
origen es `missing`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from isot.inventory import (
    abs_path,
    is_library_rel,
    rel_path as inventory_rel_path,
    GLOBAL_SITE,
    load_yaml,
    parse_ref,
    resolve_template_path,
    template_dir,
)

VALUES_DIRNAME = "values"


def charts_from_doc(doc: dict[str, Any]) -> list[dict[str, Any]]:
    raw = doc.get("charts")
    if not isinstance(raw, list):
        return []
    charts: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        depends = item.get("depends_on") or []
        if not isinstance(depends, list):
            depends = []
        charts.append(
            {
                "name": str(name),
                "repo": _blank_str(item.get("repo")),
                "chart": _blank_str(item.get("chart")) or str(name),
                "version": _blank_str(item.get("version")),
                "namespace": _blank_str(item.get("namespace")),
                "order": item.get("order"),
                "depends_on": [str(dep) for dep in depends],
                "values_ref": _blank_str(item.get("values_ref")),
                "values_inline": item.get("values_inline") if "values_inline" in item else None,
                "has_inline": "values_inline" in item and item.get("values_inline") is not None,
                "note": _blank_str(item.get("note")),
            }
        )
    charts.sort(key=lambda item: (_order_key(item.get("order")), item["name"]))
    return charts


def chart_meta(chart: dict[str, Any]) -> dict[str, Any]:
    """Metadatos para la API: sin el payload de values."""
    source = values_source(chart)
    return {
        "name": chart["name"],
        "repo": chart["repo"] or None,
        "chart": chart["chart"],
        "version": chart["version"] or None,
        "namespace": chart["namespace"] or None,
        "order": chart.get("order"),
        "depends_on": list(chart.get("depends_on") or []),
        "values_ref": chart.get("values_ref") or None,
        "values_source": source,
        "has_values": source != "missing",
        "note": chart.get("note") or None,
    }


def values_source(chart: dict[str, Any]) -> str:
    if chart.get("values_ref"):
        return "ref"
    if chart.get("has_inline"):
        return "inline"
    return "missing"


def strip_charts_payload(doc: dict[str, Any]) -> dict[str, Any]:
    """Copia del YAML del template sin `values_inline` (carga perezosa)."""
    data = dict(doc)
    charts = data.get("charts")
    if not isinstance(charts, list):
        return data
    stripped: list[Any] = []
    for item in charts:
        if not isinstance(item, dict):
            stripped.append(item)
            continue
        copy = {key: value for key, value in item.items() if key != "values_inline"}
        stripped.append(copy)
    data["charts"] = stripped
    return data


def values_path(template_path: Path, values_ref: str) -> Path:
    return (template_dir(template_path) / values_ref).resolve()


def load_chart_values(
    template_path: Path, chart: dict[str, Any]
) -> tuple[Any, str, str | None, str | None]:
    """Devuelve (values, source, relpath, error)."""
    source = values_source(chart)
    if source == "ref":
        ref = str(chart["values_ref"])
        path = values_path(template_path, ref)
        base = template_dir(template_path).resolve()
        if not _is_relative_to(path, base):
            return None, source, ref, f"values_ref={ref!r} sale de la carpeta del template"
        if not path.is_file():
            return None, source, ref, f"no existe {ref}"
        try:
            data = load_yaml(path)
        except yaml.YAMLError as exc:
            return None, source, ref, f"YAML inválido en {ref}: {exc}"
        rel = path.relative_to(base).as_posix()
        return data, source, rel, None
    if source == "inline":
        return chart.get("values_inline"), source, None, None
    return None, source, None, None


def overrides_for_release(cluster: dict[str, Any], release: str) -> dict[str, Any] | None:
    for item in cluster.get("values_overrides") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("release") or "") == release:
            if "values_inline" in item and item.get("values_inline") is not None:
                data = item.get("values_inline")
                return data if isinstance(data, dict) else {"_value": data}
            return {}
    return None


def merge_values(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for key, value in override.items():
            if key in out:
                out[key] = merge_values(out[key], value)
            else:
                out[key] = value
        return out
    return override


def flatten_values(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Árbol de values → lista (ruta, valor). Listas se expanden por índice."""
    if isinstance(obj, dict):
        if not obj:
            return [(prefix, {})] if prefix else []
        rows: list[tuple[str, Any]] = []
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(flatten_values(value, path))
        return rows
    if isinstance(obj, list):
        if not obj:
            return [(prefix, [])] if prefix else []
        rows: list[tuple[str, Any]] = []
        for index, value in enumerate(obj):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            rows.extend(flatten_values(value, path))
        return rows
    return [(prefix, obj)] if prefix else []


def diff_maps(template_values: Any, override_values: Any) -> list[dict[str, Any]]:
    tmpl = template_values if isinstance(template_values, dict) else {}
    over = override_values if isinstance(override_values, dict) else {}
    effective = merge_values(tmpl, over) if over else tmpl
    tmpl_flat = dict(flatten_values(tmpl))
    over_flat = dict(flatten_values(over)) if over else {}
    eff_flat = dict(flatten_values(effective if isinstance(effective, dict) else {}))
    paths = sorted(set(tmpl_flat) | set(over_flat) | set(eff_flat))
    rows: list[dict[str, Any]] = []
    for path in paths:
        in_t = path in tmpl_flat
        in_o = path in over_flat
        if in_t and in_o:
            status = "overridden"
        elif in_t:
            status = "inherited"
        else:
            status = "added"
        rows.append(
            {
                "path": path,
                "template": tmpl_flat.get(path, None) if in_t else None,
                "override": over_flat.get(path, None) if in_o else None,
                "effective": eff_flat.get(path, over_flat.get(path, tmpl_flat.get(path))),
                "status": status,
            }
        )
    return rows


def cluster_values_diff(
    repo_root: Path,
    cluster: dict[str, Any],
    template_path: Path,
    template_doc: dict[str, Any],
) -> dict[str, Any]:
    charts = charts_from_doc(template_doc)
    chart_names = {item["name"] for item in charts}
    releases: list[dict[str, Any]] = []
    for chart in charts:
        values, source, rel, error = load_chart_values(template_path, chart)
        override = overrides_for_release(cluster, chart["name"])
        entries = diff_maps(values if isinstance(values, dict) else {}, override or {})
        if override is None:
            for entry in entries:
                entry["override"] = None
                if entry["status"] == "added":
                    entry["status"] = "inherited"
        releases.append(
            {
                "release": chart["name"],
                "chart": chart["chart"],
                "version": chart["version"] or None,
                "values_source": source,
                "values_path": rel,
                "has_override": override is not None,
                "error": error,
                "entries": entries,
            }
        )
    unknown: list[str] = []
    for item in cluster.get("values_overrides") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("release") or "")
        if name and name not in chart_names:
            unknown.append(name)
    site = str(cluster.get("_site") or "")
    parsed = parse_ref(str(cluster.get("template_ref") or ""), site or GLOBAL_SITE)
    t_site, t_name = parsed if parsed else (GLOBAL_SITE, str(cluster.get("template_ref") or ""))
    rel_path = inventory_rel_path(template_path, repo_root)
    if rel_path.startswith("cluster-templates/") or is_library_rel(rel_path):
        t_site = GLOBAL_SITE
    elif rel_path.startswith("sites/"):
        t_site = rel_path.split("/", 2)[1]
    return {
        "cluster": str(cluster.get("cluster_name") or cluster.get("_stem")),
        "cluster_site": site,
        "template": {"site": t_site, "name": t_name, "path": rel_path},
        "unknown_releases": unknown,
        "releases": releases,
    }


def collect_chart_issues(
    repo_root: Path,
    sites: list[Any],
    templates: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
) -> list[str]:
    issues: list[str] = []
    templates_by_path = {str(doc.get("_path")): doc for doc in templates}

    for doc in templates:
        rel = str(doc.get("_path") or "")
        path = abs_path(repo_root, rel)
        charts = charts_from_doc(doc)
        names = [item["name"] for item in charts]
        dupes = {name for name in names if names.count(name) > 1}
        for name in sorted(dupes):
            issues.append(f"Chart duplicado name={name!r} en {rel}")
        for chart in charts:
            for dep in chart.get("depends_on") or []:
                if dep not in names:
                    issues.append(
                        f"depends_on={dep!r} del chart {chart['name']!r} en {rel} no existe en charts[]"
                    )
            if not chart.get("values_ref"):
                continue
            ref = str(chart["values_ref"])
            values, _source, _rel, error = load_chart_values(path, chart)
            del values
            if error:
                issues.append(f"values_ref={ref!r} del chart {chart['name']!r} en {rel}: {error}")

    for cluster in clusters:
        name = str(cluster.get("cluster_name") or cluster.get("_stem"))
        site_id = str(cluster.get("_site"))
        rel = str(cluster.get("_path") or f"sites/{site_id}/clusters/{name}.yaml")
        overrides = cluster.get("values_overrides")
        if not overrides:
            continue
        if not isinstance(overrides, list):
            issues.append(f"values_overrides en {rel} debe ser una lista")
            continue
        template_ref = cluster.get("template_ref")
        if not template_ref:
            issues.append(
                f"values_overrides en {rel} sin template_ref: no hay charts de template que sobrescribir"
            )
            continue
        expected = resolve_template_path(repo_root, sites, site_id, str(template_ref))
        if expected is None:
            continue
        trel = inventory_rel_path(expected, repo_root)
        tdoc = templates_by_path.get(trel)
        if tdoc is None:
            try:
                loaded = load_yaml(expected)
            except yaml.YAMLError:
                loaded = {}
            tdoc = loaded if isinstance(loaded, dict) else {}
        chart_names = {item["name"] for item in charts_from_doc(tdoc)}
        for item in overrides:
            if not isinstance(item, dict):
                issues.append(f"values_overrides en {rel} tiene un elemento que no es un mapa")
                continue
            release = str(item.get("release") or "")
            if not release:
                issues.append(f"values_overrides en {rel} sin campo release")
                continue
            if release not in chart_names:
                issues.append(
                    f"values_overrides.release={release!r} en {rel} no es un chart de "
                    f"template_ref={template_ref!r} ({trel})"
                )
            inline = item.get("values_inline")
            if "values_inline" in item and inline is not None and not isinstance(inline, (dict, list)):
                issues.append(
                    f"values_overrides.release={release!r} en {rel}: values_inline debe ser YAML mapa/lista"
                )
    return issues


def _blank_str(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _order_key(value: Any) -> tuple[int, int]:
    if isinstance(value, int):
        return (0, value)
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, 0)


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False
