"""Metadatos de ingesta (`_ingest/`) de un repositorio de datos.

Un agente (persona o LLM) que construye el inventario a partir de fuentes
desordenadas deja en `_ingest/` tres archivos opcionales:

- `state.yaml`: qué revisión de cada fuente se procesó (para calcular cambios).
- `provenance.yaml`: de qué archivo/línea/hoja sale cada entidad.
- `gaps.yaml`: datos desconocidos y conflictos entre fuentes.

Los cargadores de inventario ignoran `_ingest/` (empieza por `_`); este módulo
valida su formato, que las entidades citadas existan y que el inventario no
contenga secretos. Ver `docs/llm/INGEST.md`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import yaml

INGEST_DIR = "_ingest"
STATE_FILE = "state.yaml"
GAPS_FILE = "gaps.yaml"
PROVENANCE_FILE = "provenance.yaml"
FORMAT_VERSION = 1

GAP_KINDS: tuple[str, ...] = ("unknown", "conflict", "ambiguous")
GAP_STATUSES: tuple[str, ...] = ("open", "needs-human", "resolved")
# Entidades que pueden citarse en provenance/gaps.
SITE_SCOPED_KINDS: tuple[str, ...] = ("device", "cluster", "subnet", "vlan")
GLOBAL_KINDS: tuple[str, ...] = ("site", "address_space", "template")
ENTITY_KINDS: tuple[str, ...] = GLOBAL_KINDS + SITE_SCOPED_KINDS
NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
ENTITY_PATTERN = (
    rf"^(?:(?:site|address_space):{NAME}"
    rf"|template:(?:{NAME}/)?{NAME}"
    rf"|(?:device|cluster|subnet|vlan):{NAME}/{NAME})$"
)
ENTITY_RE = re.compile(ENTITY_PATTERN)
ID_PATTERN = rf"^{NAME}$"
ID_RE = re.compile(ID_PATTERN)
SHA256_PATTERN = r"^[0-9a-f]{64}$"
SHA256_RE = re.compile(SHA256_PATTERN)

# Claves cuyo valor no debe estar en el repo de datos (comparación exacta, sin mayúsculas).
SECRET_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "secret_key",
        "enable_secret",
        "community",
        "snmp_community",
        "token",
        "api_token",
        "auth_token",
        "access_key",
        "private_key",
        "ssh_private_key",
        "psk",
        "pre_shared_key",
    }
)
# Valores aceptados en una clave secreta: referencias o marcadores, nunca el secreto.
SECRET_PLACEHOLDER_RE = re.compile(r"^(?:\$\{[^}]+\}|<[^>]+>|\*+|REDACTED|redacted|vault:.+|ref\+.+|secretRef:.+)$")
PEM_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
SECRET_SCAN_DIRS = ("sites", "cluster-templates", "site-templates", INGEST_DIR)
YAML_SUFFIXES = {".yaml", ".yml"}


@dataclass
class IngestIssue:
    path: str
    message: str
    entity: str | None = None
    severity: str = "error"
    gap_status: str | None = None


def tool_version() -> str:
    try:
        from importlib.metadata import version

        return version("isot")
    except Exception:  # pragma: no cover - paquete sin instalar
        return "0.0.0"


def jsonable(value: Any) -> Any:
    """YAML → tipos JSON (fechas a ISO), para validar con JSON Schema."""
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def ingest_dir(repo: Path) -> Path:
    return repo / INGEST_DIR


def _load(path: Path) -> tuple[Any, str | None]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return None, f"YAML inválido: {str(exc).splitlines()[0]}"
    return data, None


def load_ingest(repo: Path) -> dict[str, tuple[Any, str | None]]:
    """{archivo: (datos, error)} de los archivos de `_ingest/` presentes."""
    out: dict[str, tuple[Any, str | None]] = {}
    base = ingest_dir(repo)
    for name in (STATE_FILE, GAPS_FILE, PROVENANCE_FILE):
        path = base / name
        if path.is_file():
            out[name] = _load(path)
    return out


def _is_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _check_source_ref(ref: Any, where: str, out: list[str]) -> None:
    if not isinstance(ref, dict):
        out.append(f"{where}: debe ser un objeto {{source, file, locator?}}")
        return
    for key in ("source", "file"):
        if not _is_str(ref.get(key)):
            out.append(f"{where}: falta {key}")
    for key in ("locator", "commit", "note"):
        if key in ref and ref[key] is not None and not isinstance(ref[key], (str, int)):
            out.append(f"{where}: {key} debe ser texto")
    fields = ref.get("fields")
    if fields is not None and not (isinstance(fields, list) and all(_is_str(item) for item in fields)):
        out.append(f"{where}: fields debe ser una lista de nombres de campo")


def _check_version(data: dict[str, Any], out: list[str]) -> None:
    if data.get("version") != FORMAT_VERSION:
        out.append(f"version debe ser {FORMAT_VERSION}")


def check_state(data: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(data, dict):
        return ["debe ser un objeto con version, isot_version, processed_at y sources"]
    _check_version(data, out)
    if not _is_str(data.get("isot_version")):
        out.append("falta isot_version")
    processed = data.get("processed_at")
    if not (isinstance(processed, (dt.date, dt.datetime)) or _is_str(processed)):
        out.append("falta processed_at (fecha ISO, p.ej. 2026-01-31)")
    elif isinstance(processed, str):
        try:
            dt.date.fromisoformat(processed[:10])
        except ValueError:
            out.append(f"processed_at={processed!r} no es una fecha ISO")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        out.append("sources debe ser una lista con al menos una fuente")
        return out
    seen: set[str] = set()
    for index, item in enumerate(sources):
        where = f"sources[{index}]"
        if not isinstance(item, dict):
            out.append(f"{where}: debe ser un objeto")
            continue
        sid = item.get("id")
        if not (isinstance(sid, str) and ID_RE.fullmatch(sid)):
            out.append(f"{where}: id inválido (letras, números, . _ -)")
        elif sid in seen:
            out.append(f"{where}: id {sid!r} duplicado")
        else:
            seen.add(sid)
        if not _is_str(item.get("repo")):
            out.append(f"{where}: falta repo (URL o ruta relativa, sin credenciales)")
        elif re.match(r"^[a-z][a-z0-9+.-]*://[^/@]*@", str(item["repo"]), re.IGNORECASE):
            out.append(f"{where}: repo no puede llevar credenciales en la URL")
        commit = item.get("commit")
        if commit is not None and not (isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{7,64}", commit)):
            out.append(f"{where}: commit debe ser un SHA git (o null)")
        files = item.get("files")
        if files is not None:
            if not isinstance(files, list):
                out.append(f"{where}: files debe ser una lista")
            else:
                for f_index, entry in enumerate(files):
                    f_where = f"{where}.files[{f_index}]"
                    if not isinstance(entry, dict) or not _is_str(entry.get("path")):
                        out.append(f"{f_where}: falta path")
                        continue
                    if not (isinstance(entry.get("sha256"), str) and SHA256_RE.fullmatch(entry["sha256"])):
                        out.append(f"{f_where}: sha256 inválido")
        if commit is None and not files:
            out.append(f"{where}: indica commit (fuente en git) o files[] con sha256")
    return out


def check_gaps(data: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(data, dict):
        return ["debe ser un objeto con version y gaps"]
    _check_version(data, out)
    gaps = data.get("gaps")
    if not isinstance(gaps, list):
        out.append("gaps debe ser una lista (vacía si no hay huecos)")
        return out
    seen: set[str] = set()
    for index, item in enumerate(gaps):
        where = f"gaps[{index}]"
        if not isinstance(item, dict):
            out.append(f"{where}: debe ser un objeto")
            continue
        gid = item.get("id")
        if not (isinstance(gid, str) and ID_RE.fullmatch(gid)):
            out.append(f"{where}: id inválido")
        elif gid in seen:
            out.append(f"{where}: id {gid!r} duplicado")
        else:
            seen.add(gid)
        entity = item.get("entity")
        if not (isinstance(entity, str) and ENTITY_RE.fullmatch(entity)):
            out.append(f"{where}: entity {entity!r} no sigue la convención <tipo>:<sitio>/<nombre>")
        if item.get("kind") not in GAP_KINDS:
            out.append(f"{where}: kind debe ser {' | '.join(GAP_KINDS)}")
        status = item.get("status")
        if status not in GAP_STATUSES:
            out.append(f"{where}: status debe ser {' | '.join(GAP_STATUSES)}")
        if not _is_str(item.get("detail")):
            out.append(f"{where}: falta detail")
        if "field" in item and not _is_str(item.get("field")):
            out.append(f"{where}: field debe ser texto (ruta de campo, p.ej. oob.ip)")
        for s_index, ref in enumerate(item.get("sources") or []):
            _check_source_ref(ref, f"{where}.sources[{s_index}]", out)
        if item.get("sources") is not None and not isinstance(item.get("sources"), list):
            out.append(f"{where}: sources debe ser una lista")
        candidates = item.get("candidates")
        if item.get("kind") == "conflict":
            if not isinstance(candidates, list) or len(candidates) < 2:
                out.append(f"{where}: un conflicto necesita candidates con al menos 2 valores")
        if isinstance(candidates, list):
            for c_index, cand in enumerate(candidates):
                c_where = f"{where}.candidates[{c_index}]"
                if not isinstance(cand, dict) or "value" not in cand:
                    out.append(f"{c_where}: falta value")
                    continue
                _check_source_ref(cand.get("source"), f"{c_where}.source", out)
        if status == "resolved" and not _is_str(item.get("resolution")):
            out.append(f"{where}: un hueco resolved necesita resolution")
    return out


def check_provenance(data: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(data, dict):
        return ["debe ser un objeto con version y entities"]
    _check_version(data, out)
    entities = data.get("entities")
    if not isinstance(entities, dict):
        out.append("entities debe ser un mapa <entidad>: [referencias]")
        return out
    for entity, refs in entities.items():
        if not (isinstance(entity, str) and ENTITY_RE.fullmatch(entity)):
            out.append(f"entidad {entity!r} no sigue la convención <tipo>:<sitio>/<nombre>")
            continue
        if not isinstance(refs, list) or not refs:
            out.append(f"{entity}: debe tener al menos una referencia de fuente")
            continue
        for index, ref in enumerate(refs):
            _check_source_ref(ref, f"{entity}[{index}]", out)
    return out


CHECKERS = {STATE_FILE: check_state, GAPS_FILE: check_gaps, PROVENANCE_FILE: check_provenance}


def format_issues(repo: Path) -> list[IngestIssue]:
    issues: list[IngestIssue] = []
    base = ingest_dir(repo)
    if not base.is_dir():
        return issues
    for path in sorted(base.iterdir()):
        if path.is_file() and path.suffix.lower() in YAML_SUFFIXES and path.name not in CHECKERS:
            issues.append(
                IngestIssue(
                    f"{INGEST_DIR}/{path.name}",
                    f"{INGEST_DIR}/{path.name}: archivo desconocido (se esperan {', '.join(CHECKERS)})",
                    severity="warning",
                )
            )
    for name, (data, error) in load_ingest(repo).items():
        rel = f"{INGEST_DIR}/{name}"
        if error:
            issues.append(IngestIssue(rel, f"{rel}: {error}"))
            continue
        for message in CHECKERS[name](data):
            issues.append(IngestIssue(rel, f"{rel}: {message}"))
    return issues


@dataclass
class Known:
    """Entidades existentes en el inventario, con la clave de `_ingest/`."""

    keys: set[str]

    def __contains__(self, entity: str) -> bool:
        if entity in self.keys:
            return True
        if entity.startswith("template:") and "/" in entity:
            return f"template:{entity.split('/', 1)[1]}" in self.keys
        return False


def known_entities(ctx: Any) -> Known:
    keys: set[str] = set()
    for site in ctx.sites:
        keys.add(f"site:{site.id}")
        topo = ctx.topologies.get(site.id) or {}
        for subnet in topo.get("subnets") or []:
            if isinstance(subnet, dict) and subnet.get("name"):
                keys.add(f"subnet:{site.id}/{subnet['name']}")
        for vlan in topo.get("vlans") or []:
            if isinstance(vlan, dict) and vlan.get("id") is not None:
                keys.add(f"vlan:{site.id}/{vlan['id']}")
    for space in ctx.spaces:
        keys.add(f"address_space:{space.id}")
    for device in ctx.devices:
        keys.add(f"device:{device.site_id}/{device.hostname}")
    for cluster in ctx.clusters:
        name = cluster.get("cluster_name") or cluster.get("_stem")
        keys.add(f"cluster:{cluster.get('_site')}/{name}")
    for template in ctx.templates:
        name = template.get("template_name") or template.get("_stem")
        site = template.get("_site") or "global"
        keys.add(f"template:{name}" if site == "global" else f"template:{site}/{name}")
    return Known(keys)


def reference_issues(repo: Path, ctx: Any) -> list[IngestIssue]:
    """Entidades/fuentes citadas que no existen, y huecos abiertos (como info/warning)."""
    loaded = load_ingest(repo)
    known = known_entities(ctx)
    state, state_err = loaded.get(STATE_FILE, (None, None))
    source_ids: set[str] | None = None
    if isinstance(state, dict) and not state_err and isinstance(state.get("sources"), list):
        source_ids = {str(item.get("id")) for item in state["sources"] if isinstance(item, dict)}
    issues: list[IngestIssue] = []

    def check_ref(ref: Any, rel: str, entity: str) -> None:
        if source_ids is None or not isinstance(ref, dict) or not ref.get("source"):
            return
        if str(ref["source"]) not in source_ids:
            issues.append(
                IngestIssue(
                    rel,
                    f"{rel}: {entity} cita la fuente {ref['source']!r}, que no está en {INGEST_DIR}/{STATE_FILE}",
                    entity=entity,
                    severity="warning",
                )
            )

    prov, prov_err = loaded.get(PROVENANCE_FILE, (None, None))
    if isinstance(prov, dict) and not prov_err and isinstance(prov.get("entities"), dict):
        rel = f"{INGEST_DIR}/{PROVENANCE_FILE}"
        for entity, refs in prov["entities"].items():
            if not isinstance(entity, str) or not ENTITY_RE.fullmatch(entity):
                continue
            if entity not in known:
                issues.append(
                    IngestIssue(rel, f"{rel}: {entity} no existe en el inventario", entity=entity, severity="warning")
                )
            for ref in refs if isinstance(refs, list) else []:
                check_ref(ref, rel, entity)
        if source_ids is None:
            issues.append(
                IngestIssue(rel, f"{rel}: hay procedencia pero falta {INGEST_DIR}/{STATE_FILE}", severity="warning")
            )
    gaps, gaps_err = loaded.get(GAPS_FILE, (None, None))
    if isinstance(gaps, dict) and not gaps_err and isinstance(gaps.get("gaps"), list):
        rel = f"{INGEST_DIR}/{GAPS_FILE}"
        for item in gaps["gaps"]:
            if not isinstance(item, dict):
                continue
            entity = item.get("entity")
            if not isinstance(entity, str) or not ENTITY_RE.fullmatch(entity):
                continue
            if entity not in known:
                issues.append(
                    IngestIssue(rel, f"{rel}: {item.get('id')}: {entity} no existe en el inventario", entity=entity, severity="warning")
                )
            for ref in item.get("sources") or []:
                check_ref(ref, rel, entity)
            for cand in item.get("candidates") or []:
                if isinstance(cand, dict):
                    check_ref(cand.get("source"), rel, entity)
            status = item.get("status")
            if status in ("open", "needs-human"):
                field = f" ({item['field']})" if item.get("field") else ""
                label = {"conflict": "conflicto", "ambiguous": "dato ambiguo"}.get(str(item.get("kind")), "dato desconocido")
                issues.append(
                    IngestIssue(
                        rel,
                        f"{item.get('id')}: {label}{field}: {item.get('detail')}",
                        entity=entity,
                        severity="warning" if status == "needs-human" else "info",
                        gap_status=str(status),
                    )
                )
    return issues


def _walk_secrets(node: Any, trail: str, out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{trail}.{key}" if trail else str(key)
            if str(key).lower() in SECRET_KEYS and isinstance(value, (str, int)) and str(value).strip():
                if not SECRET_PLACEHOLDER_RE.fullmatch(str(value).strip()):
                    out.append(here)
                    continue
            _walk_secrets(value, here, out)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _walk_secrets(item, f"{trail}[{index}]", out)
    elif isinstance(node, str) and PEM_RE.search(node):
        out.append(trail or "(documento)")


def iter_data_yaml(repo: Path) -> Iterator[Path]:
    catalog = repo / "sites.yaml"
    if catalog.is_file():
        yield catalog
    for folder in SECRET_SCAN_DIRS:
        base = repo / folder
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix.lower() in YAML_SUFFIXES:
                yield path


def secret_issues(repo: Path) -> list[IngestIssue]:
    issues: list[IngestIssue] = []
    for path in iter_data_yaml(repo):
        rel = path.relative_to(repo).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        hits: list[str] = []
        if PEM_RE.search(text):
            hits.append("(clave privada PEM)")
        try:
            _walk_secrets(yaml.safe_load(text), "", hits)
        except yaml.YAMLError:
            pass
        for trail in dict.fromkeys(hits):
            issues.append(
                IngestIssue(
                    rel,
                    f"{rel}: posible secreto en {trail}; quítalo o usa una referencia (${{VAR}}, <secreto>, vault:...)",
                )
            )
    return issues


# --- Cambios en la fuente desde la última ingesta ---------------------------------

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}


def hash_tree(source: Path) -> dict[str, str]:
    """{ruta relativa: sha256} de los archivos de la fuente (sin .git ni ocultos)."""
    out: dict[str, str] = {}
    for path in sorted(source.rglob("*")):
        rel_parts = path.relative_to(source).parts
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel_parts):
            continue
        if path.is_file():
            out[path.relative_to(source).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _git_changes(source: Path, commit: str) -> dict[str, list[str]] | None:
    proc = subprocess.run(
        ["git", "-C", str(source), "diff", "--name-status", "--no-renames", commit, "HEAD", "--"],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        return None
    changes: dict[str, list[str]] = {"added": [], "modified": [], "removed": []}
    for line in proc.stdout.splitlines():
        status, _, name = line.partition("\t")
        key = {"A": "added", "D": "removed"}.get(status[:1], "modified")
        changes[key].append(name)
    return changes


def source_status(repo: Path, source: Path, source_id: str | None = None) -> dict[str, Any]:
    data, error = load_ingest(repo).get(STATE_FILE, (None, f"no existe {INGEST_DIR}/{STATE_FILE}"))
    if error or not isinstance(data, dict):
        raise ValueError(error or f"{INGEST_DIR}/{STATE_FILE} inválido")
    sources = [item for item in data.get("sources") or [] if isinstance(item, dict)]
    if source_id:
        sources = [item for item in sources if item.get("id") == source_id]
    if len(sources) != 1:
        raise ValueError("indica --source-id: el state tiene varias fuentes (o ninguna con ese id)")
    entry = sources[0]
    result: dict[str, Any] = {"source": entry.get("id"), "method": None, "added": [], "modified": [], "removed": []}
    files = entry.get("files")
    if isinstance(files, list) and files:
        recorded = {str(item.get("path")): str(item.get("sha256")) for item in files if isinstance(item, dict)}
        current = hash_tree(source)
        result["method"] = "sha256"
        result["added"] = sorted(set(current) - set(recorded))
        result["removed"] = sorted(set(recorded) - set(current))
        result["modified"] = sorted(path for path in set(current) & set(recorded) if current[path] != recorded[path])
    elif entry.get("commit"):
        changes = _git_changes(source, str(entry["commit"]))
        if changes is None:
            raise ValueError(f"no se pudo hacer git diff desde {entry['commit']} en la fuente")
        result["method"] = "git"
        result.update({key: sorted(value) for key, value in changes.items()})
    else:
        raise ValueError("la fuente no tiene commit ni files[]")
    result["changed"] = bool(result["added"] or result["modified"] or result["removed"])
    return result


def files_block(source: Path) -> list[dict[str, str]]:
    return [{"path": path, "sha256": digest} for path, digest in hash_tree(source).items()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="isot ingest", description="Ayudas para la ingesta (ver docs/llm/INGEST.md).")
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="Qué cambió en la fuente desde _ingest/state.yaml (exit 1 si hay cambios).")
    status.add_argument("--repo", required=True, help="Repositorio de datos ISoT.")
    status.add_argument("--source", required=True, help="Carpeta de la fuente desordenada.")
    status.add_argument("--source-id", default=None)
    status.add_argument("--format", dest="fmt", choices=("text", "json"), default="text")
    hashes = sub.add_parser("hash", help="Bloque files[] (path + sha256) para _ingest/state.yaml.")
    hashes.add_argument("--source", required=True)
    check = sub.add_parser("check", help="Solo valida el formato de _ingest/ (sin el resto del validador).")
    check.add_argument("--repo", required=True)
    args = parser.parse_args(argv)
    if args.command == "hash":
        sys.stdout.write(yaml.safe_dump({"files": files_block(Path(args.source))}, sort_keys=False, allow_unicode=True))
        return 0
    if args.command == "check":
        issues = format_issues(Path(args.repo))
        for issue in issues:
            print(f"[{issue.severity.upper()}] {issue.message}")
        if not issues:
            print("_ingest/: formato OK")
        return 1 if any(issue.severity == "error" for issue in issues) else 0
    try:
        result = source_status(Path(args.repo), Path(args.source), args.source_id)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.fmt == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"fuente {result['source']} (método {result['method']})")
        for key, label in (("added", "nuevo"), ("modified", "modificado"), ("removed", "eliminado")):
            for path in result[key]:
                print(f"  {label}: {path}")
        if not result["changed"]:
            print("  sin cambios desde la última ingesta")
    return 1 if result["changed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
