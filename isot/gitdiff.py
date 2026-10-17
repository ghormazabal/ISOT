"""Diff semántico entre dos refs del repo de datos (sin checkout).

Cada lado es `local` (working tree) o una rama/tag/commit. Los commits se leen
con `git archive` (lista de argumentos, sin shell) en un árbol temporal de solo
lectura cacheado por SHA (`isot.refs.TREES`); se comparan entidades del
inventario: sitios, devices, VLANs/subredes y espacios IPAM, clústeres,
templates, releases Helm y values. El resultado se cachea por par de claves
(SHA o huella del working tree).
"""

from __future__ import annotations

import dataclasses
import re
import subprocess
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any, Iterator

from isot.helm import charts_from_doc, flatten_values, load_chart_values
from isot.indexer import build_sites_index, build_template_index_all
from isot.inventory import (
    abs_path,
    load_catalog,
    load_clusters,
    load_devices,
    load_templates,
    load_topology,
)

SAFE_REF = re.compile(
    r"^(?:"
    r"HEAD"
    r"|[0-9a-fA-F]{4,40}"
    r"|[A-Za-z0-9][A-Za-z0-9._/-]*"
    r")(?:[~^][0-9]+)*$"
)
COMMIT_LOG_LIMIT = 80
COMMIT_FORMAT = "%H\t%h\t%s\t%an\t%aI"
KIND_ORDER = ("site", "device", "network", "cluster", "template", "release", "values")
CACHE_SIZE = 16
SNAPSHOT_CACHE_SIZE = 12
GIT_TIMEOUT = 30

_diff_cache: OrderedDict[tuple[str, str, str, str], dict[str, Any]] = OrderedDict()
_snapshot_cache: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()


class RefError(ValueError):
    """Ref inexistente o con sintaxis inválida."""


def validate_ref(value: str) -> str:
    text = (value or "").strip()
    if not text or len(text) > 200 or ".." in text or not SAFE_REF.fullmatch(text):
        raise RefError("ref inválida")
    return text


_TOPLEVEL_OK: set[str] = set()
NOT_TOPLEVEL = (
    "el repositorio de datos no es la raíz de un repo git "
    "(ejecuta `git init` en él o apunta data_repo.path a la raíz del clon)"
)


def is_git_toplevel(repo: Path) -> bool:
    """¿`repo` es la raíz de un repo git? Evita operar sobre un repo que lo contiene
    (p.ej. la propia herramienta cuando se apunta a examples/demo-repo)."""
    key = str(repo.resolve())
    if key in _TOPLEVEL_OK:
        return True
    try:
        proc = subprocess.run(
            ["git", "-C", key, "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    top = Path(proc.stdout.decode("utf-8", errors="replace").strip()).resolve()
    if top == Path(key):
        _TOPLEVEL_OK.add(key)
        return True
    return False


def git(repo: Path, args: list[str], *, timeout: int = GIT_TIMEOUT) -> subprocess.CompletedProcess[bytes]:
    """git sobre el repositorio de datos, con la configuración y credenciales del usuario."""
    command = ["git", "-C", str(repo), *args]
    if not is_git_toplevel(repo):
        return subprocess.CompletedProcess(command, 128, b"", NOT_TOPLEVEL.encode("utf-8"))
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        timeout=timeout,
    )


def resolve_sha(repo: Path, ref: str) -> str:
    """Resuelve una ref a SHA completo con `git rev-parse --verify`."""
    name = validate_ref(ref)
    proc = git(repo, ["rev-parse", "--verify", "--end-of-options", f"{name}^{{commit}}"])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", errors="replace").strip()
        raise RefError(detail or f"no existe la ref {name}")
    sha = proc.stdout.decode("utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RefError(f"SHA inesperado para {name}")
    return sha


def _parse_log_line(line: str) -> dict[str, Any] | None:
    parts = line.split("\t")
    if len(parts) < 3:
        return None
    return {
        "name": parts[1],
        "sha": parts[0],
        "short": parts[1],
        "kind": "commit",
        "subject": parts[2] if len(parts) > 2 else "",
        "author": parts[3] if len(parts) > 3 else "",
        "date": parts[4] if len(parts) > 4 else "",
    }


def list_commits(repo: Path, rev: str, *, limit: int = COMMIT_LOG_LIMIT) -> dict[str, Any]:
    """Historial reciente de una rama, tag, SHA o expresión (`HEAD~3`)."""
    name = validate_ref(rev)
    sha = resolve_sha(repo, name)
    count = max(1, min(int(limit), 200))
    proc = git(
        repo,
        ["log", "-n", str(count), f"--format={COMMIT_FORMAT}", "--end-of-options", name, "--"],
    )
    if proc.returncode != 0:
        detail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RefError(detail or f"no se pudo leer el historial de {name}")
    commits: list[dict[str, Any]] = []
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        parsed = _parse_log_line(line)
        if parsed:
            commits.append(parsed)
    return {"ref": name, "sha": sha, "short": sha[:7], "commits": commits}


def list_refs(repo: Path) -> dict[str, Any]:
    if not is_git_toplevel(repo):
        inside = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
            check=False,
            capture_output=True,
            timeout=GIT_TIMEOUT,
        )
        raise RefError(NOT_TOPLEVEL if inside.returncode == 0 else "el directorio no es un repositorio git")
    branches: list[dict[str, Any]] = []
    tags: list[dict[str, Any]] = []
    proc = git(
        repo,
        [
            "for-each-ref",
            "--sort=-committerdate",
            "--format=%(objectname)\t%(objecttype)\t%(refname:short)\t%(refname)",
            "refs/heads",
            "refs/remotes",
            "refs/tags",
        ],
    )
    if proc.returncode != 0:
        raise RefError((proc.stderr or b"").decode("utf-8", errors="replace").strip() or "git for-each-ref falló")
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        sha, _kind, short, full = parts[0], parts[1], parts[2], parts[3]
        if full.endswith("/HEAD") or short in {"origin", "HEAD"}:
            continue
        entry = {"name": short, "sha": sha, "kind": "tag" if full.startswith("refs/tags/") else "branch"}
        if full.startswith("refs/tags/"):
            entry["remote"] = False
            tags.append(entry)
        else:
            entry["remote"] = full.startswith("refs/remotes/")
            entry["kind"] = "branch"
            branches.append(entry)
    history = list_commits(repo, "HEAD")
    return {
        "branches": branches,
        "tags": tags,
        "commits": history["commits"],
        "head": {"ref": "HEAD", "sha": history["sha"], "short": history["short"]},
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _clean_doc(doc: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in doc.items() if not str(key).startswith("_")}


def _entity(
    kind: str,
    entity_id: str,
    label: str,
    site: str | None,
    payload: Any,
    graph_ids: list[str] | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": entity_id,
        "label": label,
        "site": site,
        "payload": _jsonable(payload),
        "graph_ids": list(graph_ids or []),
        "path": path,
    }


def snapshot_inventory(root: Path) -> dict[str, Any]:
    """Índices + entidades canónicas de un árbol materializado."""
    sites, spaces = load_catalog(root)
    devices = load_devices(root, sites)
    clusters = load_clusters(root, sites)
    templates = load_templates(root, sites)
    entities: list[dict[str, Any]] = []

    for site in sites:
        entities.append(
            _entity(
                "site",
                site.id,
                site.name,
                site.id,
                {"name": site.name, "description": site.description, "path": site.rel_path},
                graph_ids=[f"site:{site.id}"],
                path="sites.yaml",
            )
        )
        topology = load_topology(site)
        for vlan in topology.get("vlans") or []:
            if not isinstance(vlan, dict) or vlan.get("id") is None:
                continue
            vid = vlan["id"]
            entities.append(
                _entity(
                    "network",
                    f"{site.id}:vlan:{vid}",
                    f"VLAN {vid} {vlan.get('name') or ''}".strip(),
                    site.id,
                    vlan,
                    graph_ids=[f"network:{site.id}:vlan:{vid}"],
                    path=f"{site.rel_path}/topology/index.yaml",
                )
            )
        for subnet in topology.get("subnets") or []:
            if not isinstance(subnet, dict) or not subnet.get("name"):
                continue
            name = str(subnet["name"])
            entities.append(
                _entity(
                    "network",
                    f"{site.id}:subnet:{name}",
                    name,
                    site.id,
                    subnet,
                    graph_ids=[f"network:{site.id}:subnet:{name}"],
                    path=f"{site.rel_path}/topology/index.yaml",
                )
            )

    for space in spaces:
        raw = dataclasses.asdict(space) if dataclasses.is_dataclass(space) else {"id": str(space)}
        space_id = str(raw.get("id") or "")
        if not space_id:
            continue
        entities.append(_entity("network", f"global:space:{space_id}", f"IPAM {space_id}", "global", raw, path="sites.yaml"))

    for device in devices:
        payload = _clean_doc(device.data)
        entities.append(
            _entity(
                "device",
                f"{device.site_id}:{device.hostname}",
                device.hostname,
                device.site_id,
                payload,
                graph_ids=[f"{device.site_id}:{device.hostname}"],
                path=device.rel_path,
            )
        )

    for cluster in clusters:
        name = str(cluster.get("cluster_name") or cluster.get("_stem"))
        site_id = str(cluster.get("_site"))
        payload = _clean_doc(cluster)
        entities.append(
            _entity(
                "cluster",
                f"{site_id}:{name}",
                name,
                site_id,
                payload,
                graph_ids=[f"cluster:{site_id}:{name}"],
                path=str(cluster.get("_path") or "") or None,
            )
        )
        for item in cluster.get("values_overrides") or []:
            if not isinstance(item, dict):
                continue
            release = str(item.get("release") or "")
            if not release:
                continue
            raw = item.get("values_inline")
            values = raw if isinstance(raw, dict) else ({"_value": raw} if raw is not None else {})
            entities.append(
                _entity(
                    "values",
                    f"cluster:{site_id}:{name}:{release}",
                    f"{site_id}/{name} {release}",
                    site_id,
                    values,
                )
            )

    for tmpl in templates:
        name = str(tmpl.get("template_name") or tmpl.get("_stem"))
        site_id = str(tmpl.get("_site") or "global")
        rel = str(tmpl.get("_path") or "")
        path = abs_path(root, rel)
        charts = charts_from_doc(tmpl)
        meta = {key: value for key, value in _clean_doc(tmpl).items() if key not in {"charts", "values_inline"}}
        meta["chart_names"] = [item["name"] for item in charts]
        entities.append(
            _entity(
                "template",
                f"{site_id}:{name}",
                name,
                site_id,
                meta,
                graph_ids=[f"template:{site_id}:{name}"],
                path=rel or None,
            )
        )
        for chart in charts:
            release = chart["name"]
            rid = f"{site_id}:{name}:{release}"
            entities.append(
                _entity(
                    "release",
                    rid,
                    f"{name}/{release}",
                    site_id,
                    {
                        "chart": chart.get("chart"),
                        "version": chart.get("version"),
                        "namespace": chart.get("namespace"),
                        "repo": chart.get("repo"),
                        "order": chart.get("order"),
                        "depends_on": chart.get("depends_on"),
                        "values_ref": chart.get("values_ref"),
                    },
                    graph_ids=[f"release:{rid}"],
                )
            )
            values, _source, _rel, _error = load_chart_values(path, chart)
            payload: Any
            if isinstance(values, dict):
                payload = values
            elif values is None:
                payload = {}
            else:
                payload = {"_value": values}
            entities.append(
                _entity(
                    "values",
                    f"template:{rid}",
                    f"{name}/{release}",
                    site_id,
                    payload,
                )
            )

    # Índices por ref: si el árbol trae un índice al día (manifiesto), se reutiliza;
    # el snapshot entero queda cacheado por SHA (o huella del working tree).
    from isot.indexer import plan_builder

    builder = plan_builder(root)
    device_index = sorted(
        (item for site in builder.sites for item in builder.devices(site)),
        key=lambda item: (item.get("path") or "", item.get("hostname") or ""),
    )
    cluster_index = sorted(
        (item for site in builder.sites for item in builder.clusters(site)),
        key=lambda item: (item.get("path") or "", item.get("name") or ""),
    )
    return {
        "entities": {item["id"]: item for item in entities},
        "sites": build_sites_index(root, builder),
        "devices": device_index,
        "clusters": cluster_index,
        "templates": build_template_index_all(root),
    }


def _materialize(repo: Path, sha: str) -> Iterator[Path]:
    """Árbol de solo lectura de un commit (caché por SHA fuera de ISoT, ver isot.refs)."""
    from isot.refs import TREES

    with TREES.acquire(repo, sha) as tree:
        yield tree


def snapshot_for(repo: Path, spec: Any) -> dict[str, Any]:
    """Snapshot de una `isot.refs.RefSpec`: working tree (por huella) o commit (por SHA)."""
    if not spec.local:
        return snapshot_ref(repo, spec.sha)
    key = (str(repo.resolve()), spec.key)
    cached = _snapshot_cache.get(key)
    if cached is not None:
        _snapshot_cache.move_to_end(key)
        return cached
    snap = snapshot_inventory(repo)
    _snapshot_cache[key] = snap
    while len(_snapshot_cache) > SNAPSHOT_CACHE_SIZE:
        _snapshot_cache.popitem(last=False)
    return snap


def snapshot_ref(repo: Path, sha: str) -> dict[str, Any]:
    key = (str(repo.resolve()), sha)
    cached = _snapshot_cache.get(key)
    if cached is not None:
        _snapshot_cache.move_to_end(key)
        return cached
    for tree in _materialize(repo, sha):
        snap = snapshot_inventory(tree)
        break
    else:
        raise RefError(f"no se pudo materializar {sha}")
    _snapshot_cache[key] = snap
    while len(_snapshot_cache) > SNAPSHOT_CACHE_SIZE:
        _snapshot_cache.popitem(last=False)
    return snap


def _diff_payloads(before: Any, after: Any) -> list[dict[str, Any]]:
    before_map = dict(flatten_values(before if isinstance(before, dict) else {"_value": before}))
    after_map = dict(flatten_values(after if isinstance(after, dict) else {"_value": after}))
    if not before_map and not isinstance(before, dict):
        before_map = {"_value": _jsonable(before)} if before is not None else {}
    if not after_map and not isinstance(after, dict):
        after_map = {"_value": _jsonable(after)} if after is not None else {}
    rows: list[dict[str, Any]] = []
    for path in sorted(set(before_map) | set(after_map)):
        in_b = path in before_map
        in_a = path in after_map
        left = _jsonable(before_map.get(path)) if in_b else None
        right = _jsonable(after_map.get(path)) if in_a else None
        if in_b and in_a and left == right:
            continue
        if in_b and not in_a:
            status = "removed"
        elif in_a and not in_b:
            status = "added"
        else:
            status = "modified"
        rows.append({"path": path, "before": left, "after": right, "status": status})
    return rows


def _matches_site(entity: dict[str, Any], site: str | None) -> bool:
    if not site:
        return True
    entity_site = entity.get("site")
    if entity_site == site:
        return True
    if entity_site in {None, "global"} and entity.get("kind") in {"template", "release", "values"}:
        return True
    return False


def _compare_entities(
    base_map: dict[str, dict[str, Any]],
    head_map: dict[str, dict[str, Any]],
    site: str | None,
) -> list[dict[str, Any]]:
    ids = set(base_map) | set(head_map)
    out: list[dict[str, Any]] = []
    for entity_id in ids:
        before = base_map.get(entity_id)
        after = head_map.get(entity_id)
        sample = after or before
        if sample is None or not _matches_site(sample, site):
            continue
        if before and not after:
            status = "removed"
            changes = _diff_payloads(before["payload"], {})
            for row in changes:
                row["status"] = "removed"
                row["after"] = None
        elif after and not before:
            status = "added"
            changes = _diff_payloads({}, after["payload"])
            for row in changes:
                row["status"] = "added"
                row["before"] = None
        else:
            assert before is not None and after is not None
            changes = _diff_payloads(before["payload"], after["payload"])
            status = "modified" if changes else "unchanged"
        meta = after or before
        assert meta is not None
        out.append(
            {
                "kind": meta["kind"],
                "id": entity_id,
                "label": meta["label"],
                "site": meta.get("site"),
                "status": status,
                "graph_ids": list(meta.get("graph_ids") or []),
                "path": meta.get("path"),
                "changes": changes,
                "before": None if before is None else before["payload"],
                "after": None if after is None else after["payload"],
            }
        )
    out.sort(key=lambda item: (KIND_ORDER.index(item["kind"]) if item["kind"] in KIND_ORDER else 99, item["id"]))
    return out


def _child_ref(entity: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": entity["id"],
        "kind": entity["kind"],
        "label": entity["label"],
        "status": entity["status"],
        "site": entity.get("site"),
    }


def _payload_of(entity: dict[str, Any]) -> dict[str, Any]:
    for key in ("after", "before"):
        value = entity.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _parent_ids(entity: dict[str, Any]) -> list[str]:
    kind = entity["kind"]
    entity_id = str(entity["id"])
    site = entity.get("site")
    parents: list[str] = []
    if kind != "site" and site and site not in {"global", ""}:
        parents.append(str(site))
    if kind == "device":
        cluster_ref = _payload_of(entity).get("cluster_ref")
        if cluster_ref and site:
            parents.append(f"{site}:{cluster_ref}")
    elif kind == "release":
        parts = entity_id.split(":")
        if len(parts) >= 2:
            parents.append(f"{parts[0]}:{parts[1]}")
    elif kind == "values":
        if entity_id.startswith("template:"):
            rest = entity_id[len("template:") :]
            parents.append(rest)
            parts = rest.split(":")
            if len(parts) >= 2:
                parents.append(f"{parts[0]}:{parts[1]}")
        elif entity_id.startswith("cluster:"):
            rest = entity_id[len("cluster:") :]
            parts = rest.split(":")
            if len(parts) >= 2:
                parents.append(f"{parts[0]}:{parts[1]}")
    return list(dict.fromkeys(parents))


def _propagate_parents(entities: list[dict[str, Any]]) -> None:
    """Un padre con hijos cambiados pasa a modified y acumula +N −N ~N."""
    by_id = {item["id"]: item for item in entities}
    for item in entities:
        item.setdefault("inherited", False)
        item.setdefault("children", [])
        item.setdefault("child_counts", {"added": 0, "removed": 0, "modified": 0})

    queue: deque[dict[str, Any]] = deque(item for item in entities if item["status"] != "unchanged")
    seen: set[tuple[str, str]] = set()
    while queue:
        child = queue.popleft()
        for parent_id in _parent_ids(child):
            parent = by_id.get(parent_id)
            if parent is None or parent["id"] == child["id"]:
                continue
            edge = (str(parent["id"]), str(child["id"]))
            if edge in seen:
                continue
            seen.add(edge)
            parent["children"].append(_child_ref(child))
            if parent["status"] == "unchanged":
                parent["status"] = "modified"
                parent["inherited"] = True
                queue.append(parent)

    for item in entities:
        counts = {"added": 0, "removed": 0, "modified": 0}
        children = item.get("children") or []
        children.sort(
            key=lambda row: (
                {"added": 0, "removed": 1, "modified": 2, "unchanged": 3}.get(row["status"], 9),
                KIND_ORDER.index(row["kind"]) if row["kind"] in KIND_ORDER else 99,
                row["id"],
            )
        )
        item["children"] = children
        for child in children:
            status = child.get("status")
            if status in counts:
                counts[status] += 1
        item["child_counts"] = counts


def _counts_by_graph_id(entities: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    mapping: dict[str, dict[str, int]] = {}
    for entity in entities:
        counts = entity.get("child_counts") or {}
        if not any(counts.get(key, 0) for key in ("added", "removed", "modified")):
            continue
        payload = {"added": int(counts.get("added") or 0), "removed": int(counts.get("removed") or 0), "modified": int(counts.get("modified") or 0)}
        for graph_id in entity.get("graph_ids") or []:
            mapping[str(graph_id)] = payload
        if entity["kind"] == "site":
            mapping[f"site:{entity['id']}"] = payload
        if entity["kind"] == "cluster":
            mapping[f"cluster:{entity['id']}"] = payload
        if entity["kind"] == "template":
            mapping[f"template:{entity['id']}"] = payload
    return mapping


def _coerce_vlan(value: Any) -> Any:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _vlan_status_map(entities: list[dict[str, Any]]) -> dict[tuple[str, Any], str]:
    mapping: dict[tuple[str, Any], str] = {}
    for entity in entities:
        if entity.get("kind") != "network":
            continue
        entity_id = str(entity["id"])
        parts = entity_id.split(":")
        if len(parts) < 3 or parts[1] != "vlan":
            continue
        status = str(entity["status"])
        mapping[(parts[0], _coerce_vlan(parts[2]))] = status
        mapping[(parts[0], parts[2])] = status
    return mapping


def _site_from_graph_id(node_id: str) -> str | None:
    if node_id.startswith(("template:", "cluster:", "release:", "network:")):
        parts = node_id.split(":")
        return parts[1] if len(parts) > 1 else None
    if node_id.startswith("site:"):
        rest = node_id.split(":", 1)[1]
        return rest.split("--", 1)[0]
    if ":" in node_id:
        return node_id.split(":", 1)[0]
    return None


def _graph_node_from_entity(entity: dict[str, Any], graph_id: str) -> dict[str, Any]:
    kind = str(entity["kind"])
    return {
        "id": graph_id,
        "hostname": entity["label"],
        "site": entity.get("site"),
        "role": kind,
        "ip": None,
        "kind": kind,
        "cluster_ref": None,
        "path": None,
        "entity": kind,
        "diff_status": entity["status"],
        "diff_counts": entity.get("child_counts") or {"added": 0, "removed": 0, "modified": 0},
    }


def _augment_graph(
    graph: dict[str, Any],
    entities: list[dict[str, Any]],
    status_by_id: dict[str, str],
    counts_by_id: dict[str, dict[str, int]],
    vlan_status: dict[tuple[str, Any], str],
    view: str,
) -> dict[str, Any]:
    nodes = {str(node["id"]): dict(node) for node in graph.get("nodes") or []}
    for node in nodes.values():
        nid = str(node["id"])
        if nid in status_by_id:
            node["diff_status"] = status_by_id[nid]
        counts = counts_by_id.get(nid)
        if counts:
            node["diff_counts"] = counts
    inject_kinds = {"network"} if view == "network" else {"release"} if view == "kubernetes" else set()
    for entity in entities:
        if entity["kind"] not in inject_kinds or entity["status"] == "unchanged":
            continue
        for graph_id in entity.get("graph_ids") or []:
            if graph_id in nodes:
                nodes[graph_id]["diff_status"] = entity["status"]
                continue
            nodes[graph_id] = _graph_node_from_entity(entity, str(graph_id))

    edges = {str(edge["id"]): dict(edge) for edge in graph.get("edges") or []}
    if view == "kubernetes":
        for entity in entities:
            if entity["kind"] != "release" or entity["status"] == "unchanged":
                continue
            parts = str(entity["id"]).split(":")
            if len(parts) < 2:
                continue
            template_id = f"template:{parts[0]}:{parts[1]}"
            for graph_id in entity.get("graph_ids") or []:
                if graph_id not in nodes:
                    continue
                if template_id not in nodes:
                    continue
                edge_id = f"{template_id}--{graph_id}"
                if edge_id in edges:
                    edges[edge_id]["diff_status"] = entity["status"]
                    continue
                edges[edge_id] = {
                    "id": edge_id,
                    "source": template_id,
                    "target": graph_id,
                    "vlans": [],
                    "ports": ["chart"],
                    "diff_status": entity["status"],
                }
    if view == "network":
        for edge in edges.values():
            site = _site_from_graph_id(str(edge.get("source") or ""))
            statuses = [vlan_status.get((site or "", _coerce_vlan(vlan))) for vlan in edge.get("vlans") or []]
            statuses = [item for item in statuses if item and item != "unchanged"]
            current = edge.get("diff_status") or "unchanged"
            if statuses and current == "unchanged":
                edge["diff_status"] = "modified" if "modified" in statuses else statuses[0]
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "view": view}


def _filter_index(entries: list[dict[str, Any]], site: str | None, site_key: str = "site") -> list[dict[str, Any]]:
    if not site:
        return entries
    filtered: list[dict[str, Any]] = []
    for item in entries:
        value = item.get(site_key) or item.get("id")
        scope = item.get("scope")
        if value == site or item.get("id") == site:
            filtered.append(item)
        elif scope == "global" or value == "global":
            filtered.append(item)
    return filtered


def graphs_from_snapshot(snap: dict[str, Any], site: str | None) -> dict[str, Any]:
    from isot.web import global_sites_graph, graph_from_index, kubernetes_graph

    devices = _filter_index(snap["devices"], site)
    clusters = _filter_index(snap["clusters"], site)
    templates = _filter_index(snap["templates"], site)
    sites = _filter_index(snap["sites"], site, "id")
    if site:
        return {
            "network": graph_from_index(devices, "network"),
            "kubernetes": kubernetes_graph(devices, clusters, templates),
        }
    return {
        "global": global_sites_graph(sites),
        "network": graph_from_index(devices, "network"),
        "kubernetes": kubernetes_graph(devices, clusters, templates),
    }


def _status_lookup(entities: list[dict[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for entity in entities:
        status = str(entity["status"])
        for graph_id in entity.get("graph_ids") or []:
            prev = mapping.get(graph_id)
            if prev == "modified" or status == "unchanged" and prev:
                continue
            if prev in {"added", "removed"} and status == "unchanged":
                continue
            mapping[graph_id] = status
        if entity["kind"] == "device":
            mapping.setdefault(entity["id"], status)
        if entity["kind"] == "site":
            mapping.setdefault(f"site:{entity['id']}", status)
    return mapping


def _union_graph(
    base: dict[str, Any] | None,
    head: dict[str, Any] | None,
    status_by_id: dict[str, str],
    view: str,
) -> dict[str, Any]:
    base = base or {"nodes": [], "edges": []}
    head = head or {"nodes": [], "edges": []}
    nodes: dict[str, dict[str, Any]] = {}
    for node in base.get("nodes") or []:
        item = dict(node)
        item["diff_status"] = "removed"
        nodes[str(item["id"])] = item
    for node in head.get("nodes") or []:
        item = dict(node)
        nid = str(item["id"])
        if nid in nodes:
            status = status_by_id.get(nid, "unchanged")
            if status == "removed":
                status = "modified"
            item["diff_status"] = status
            nodes[nid] = item
        else:
            item["diff_status"] = status_by_id.get(nid, "added")
            nodes[nid] = item
    edges: dict[str, dict[str, Any]] = {}
    for edge in base.get("edges") or []:
        item = dict(edge)
        item["diff_status"] = "removed"
        edges[str(item["id"])] = item
    for edge in head.get("edges") or []:
        item = dict(edge)
        eid = str(item["id"])
        if eid in edges:
            previous = edges[eid]
            same = previous.get("vlans") == item.get("vlans") and previous.get("ports") == item.get("ports")
            src = status_by_id.get(str(item.get("source")), "unchanged")
            dst = status_by_id.get(str(item.get("target")), "unchanged")
            if not same:
                item["diff_status"] = "modified"
            elif src in {"added", "removed", "modified"} or dst in {"added", "removed", "modified"}:
                item["diff_status"] = "modified" if src == "modified" or dst == "modified" else src if src != "unchanged" else dst
            else:
                item["diff_status"] = "unchanged"
            edges[eid] = item
        else:
            item["diff_status"] = "added"
            edges[eid] = item
    return {"nodes": list(nodes.values()), "edges": list(edges.values()), "view": view}


def diff_from_snapshots(
    base_snap: dict[str, Any],
    head_snap: dict[str, Any],
    site: str | None = None,
    *,
    base_meta: dict[str, Any] | None = None,
    head_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compara dos snapshots ya materializados (git archive o overlay en memoria)."""
    entities = _compare_entities(base_snap["entities"], head_snap["entities"], site or None)
    summary = {
        "added": sum(1 for item in entities if item["status"] == "added"),
        "removed": sum(1 for item in entities if item["status"] == "removed"),
        "modified": sum(1 for item in entities if item["status"] == "modified"),
        "unchanged": sum(1 for item in entities if item["status"] == "unchanged"),
    }
    _propagate_parents(entities)
    status_by_id = _status_lookup(entities)
    counts_by_id = _counts_by_graph_id(entities)
    vlan_status = _vlan_status_map(entities)
    base_graphs = graphs_from_snapshot(base_snap, site or None)
    head_graphs = graphs_from_snapshot(head_snap, site or None)
    graphs: dict[str, Any] = {}
    for view in ("global", "network", "kubernetes"):
        if view in base_graphs or view in head_graphs:
            graphs[view] = _augment_graph(
                _union_graph(base_graphs.get(view), head_graphs.get(view), status_by_id, view),
                entities,
                status_by_id,
                counts_by_id,
                vlan_status,
                view,
            )
    return {
        "base": dict(base_meta or {"ref": "working", "sha": "", "short": "working"}),
        "head": dict(head_meta or {"ref": "preview", "sha": "", "short": "preview"}),
        "site": site or None,
        "summary": summary,
        "entities": entities,
        "graphs": graphs,
    }


def semantic_diff(repo: Path, base_ref: str, head_ref: str, site: str | None = None) -> dict[str, Any]:
    """Diff semántico base → head; cualquiera de los dos puede ser `local` (working tree)."""
    from isot.refs import resolve_ref

    site_key = site or ""
    if site_key and not re.fullmatch(r"^[A-Za-z0-9._-]+$", site_key):
        raise RefError("id de sitio inválido")
    base = resolve_ref(repo, base_ref)
    head = resolve_ref(repo, head_ref)
    cache_key = (str(repo.resolve()), base.key, head.key, site_key)
    cached = _diff_cache.get(cache_key)
    if cached is not None:
        _diff_cache.move_to_end(cache_key)
        return cached
    base_snap = snapshot_for(repo, base)
    head_snap = snapshot_for(repo, head)
    payload = diff_from_snapshots(
        base_snap,
        head_snap,
        site,
        base_meta=base.meta(),
        head_meta=head.meta(),
    )
    _diff_cache[cache_key] = payload
    while len(_diff_cache) > CACHE_SIZE:
        _diff_cache.popitem(last=False)
    return payload


def clear_caches(repo: Path | None = None) -> None:
    """Vacía las cachés de diff/snapshot; con `repo`, solo las de ese repositorio."""
    from isot import indexer

    indexer._STATUS_CACHE.clear()
    indexer._LIVE_CACHE.clear()
    if repo is None:
        _diff_cache.clear()
        _snapshot_cache.clear()
        return
    key = str(repo.resolve())
    # Los árboles por SHA no se invalidan: un commit es inmutable.
    for cache in (_diff_cache, _snapshot_cache):
        for item in [k for k in cache if k[0] == key]:
            del cache[item]
