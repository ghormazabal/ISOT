"""Diff de archivos entre dos refs del mismo repo de datos (solo lectura).

`base` y `head` son `local` (working tree) o rama/tag/commit (ver `isot.refs`).
Lista los archivos cambiados (`git diff --name-status`, más los no
rastreados cuando un lado es `local`) y devuelve el contenido de ambos lados
con un diff unificado. Nunca escribe en ningún repositorio.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from isot.gitdiff import RefError, git
from isot.refs import RefSpec, resolve_ref

MAX_FILE_BYTES = 1_000_000
MAX_FILES = 2000
SAFE_PATH = re.compile(r"^[A-Za-z0-9_@+=,.\-][A-Za-z0-9_@+=,.\- /]*$")
STATUS = {"A": "added", "D": "removed", "M": "modified", "T": "modified"}
INVERT = {"added": "removed", "removed": "added", "modified": "modified"}


def safe_repo_path(value: str) -> str:
    """Ruta relativa al repo sin `..`, absolutas, `.git` ni caracteres raros."""
    raw = (value or "").strip()
    text = raw.rstrip("/")
    if not text or raw.startswith("/") or len(text) > 400 or not SAFE_PATH.fullmatch(text):
        raise RefError("ruta inválida")
    parts = PurePosixPath(text).parts
    if any(part in {"..", "."} or part == ".git" for part in parts):
        raise RefError("ruta inválida")
    return "/".join(parts)


def _prefixes(values: Iterable[str] | str | None) -> list[str]:
    if values is None:
        return []
    items = [values] if isinstance(values, str) else list(values)
    return [safe_repo_path(item) for item in items if item and item.strip("/")]


def _kind(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix == ".json":
        return "json"
    if suffix == ".j2":
        return "template"
    return "file"


def _name_status(repo: Path, args: list[str], prefixes: list[str]) -> dict[str, str]:
    proc = git(repo, ["diff", "--no-renames", "--name-status", "-z", *args, "--", *prefixes])
    if proc.returncode != 0:
        raise RefError((proc.stderr or b"").decode("utf-8", "replace").strip() or "git diff falló")
    out: dict[str, str] = {}
    fields = proc.stdout.decode("utf-8", "replace").split("\0")
    for code, path in zip(fields[0::2], fields[1::2]):
        if code and path:
            out[path] = STATUS.get(code[0], "modified")
    return out


def _untracked(repo: Path, prefixes: list[str]) -> list[str]:
    proc = git(repo, ["ls-files", "--others", "--exclude-standard", "-z", "--", *prefixes])
    if proc.returncode != 0:
        return []
    return [item for item in proc.stdout.decode("utf-8", "replace").split("\0") if item]


def changed_files(repo: Path, base: str | RefSpec, head: str | RefSpec, prefix: Iterable[str] | str | None = None) -> dict[str, Any]:
    """Archivos que cambian de `base` a `head` (añadido / eliminado / modificado)."""
    b = base if isinstance(base, RefSpec) else resolve_ref(repo, base)
    h = head if isinstance(head, RefSpec) else resolve_ref(repo, head)
    prefixes = _prefixes(prefix)
    statuses: dict[str, str] = {}
    if b.local and h.local:
        statuses = {}
    elif not b.local and not h.local:
        statuses = _name_status(repo, [b.sha, h.sha], prefixes)
    else:
        commit = h if b.local else b
        if commit.sha:
            # git diff <sha> = de ese commit al working tree (preparado o no).
            statuses = _name_status(repo, [commit.sha], prefixes)
        for path in _untracked(repo, prefixes):
            statuses.setdefault(path, "added")
        if b.local:
            statuses = {path: INVERT[status] for path, status in statuses.items()}
    files = [
        {"path": path, "status": status, "kind": _kind(path)}
        for path, status in sorted(statuses.items())
        if not path.startswith(".git/")
    ]
    counts = {key: sum(1 for item in files if item["status"] == key) for key in ("added", "removed", "modified")}
    return {
        "base": b.meta(),
        "head": h.meta(),
        "prefix": prefixes,
        "files": files[:MAX_FILES],
        "truncated": len(files) > MAX_FILES,
        "counts": counts,
    }


def read_at(repo: Path, spec: RefSpec, path: str) -> bytes | None:
    """Contenido de `path` en la ref (None si no existe). Local: sin seguir symlinks fuera del repo."""
    rel = safe_repo_path(path)
    if spec.local:
        target = repo / rel
        if target.is_symlink() or not target.is_file():
            return None
        if not target.resolve().is_relative_to(repo.resolve()):
            return None
        if target.stat().st_size > MAX_FILE_BYTES:
            raise RefError(f"{rel}: archivo demasiado grande para comparar")
        return target.read_bytes()
    kind = git(repo, ["cat-file", "-t", f"{spec.sha}:{rel}"])
    if kind.returncode != 0 or kind.stdout.strip() != b"blob":
        return None
    size = git(repo, ["cat-file", "-s", f"{spec.sha}:{rel}"])
    if size.returncode == 0 and int(size.stdout.strip() or 0) > MAX_FILE_BYTES:
        raise RefError(f"{rel}: archivo demasiado grande para comparar")
    proc = git(repo, ["cat-file", "blob", f"{spec.sha}:{rel}"])
    return proc.stdout if proc.returncode == 0 else None


def _text(data: bytes | None) -> tuple[str | None, bool]:
    if data is None:
        return None, False
    if b"\0" in data[:8192]:
        return None, True
    return data.decode("utf-8", "replace"), False


def file_diff(repo: Path, base: str | RefSpec, head: str | RefSpec, path: str) -> dict[str, Any]:
    b = base if isinstance(base, RefSpec) else resolve_ref(repo, base)
    h = head if isinstance(head, RefSpec) else resolve_ref(repo, head)
    rel = safe_repo_path(path)
    before, before_binary = _text(read_at(repo, b, rel))
    after, after_binary = _text(read_at(repo, h, rel))
    binary = before_binary or after_binary
    if before is None and after is None and not binary:
        raise RefError(f"{rel} no existe en {b.short} ni en {h.short}")
    if before_binary and after_binary:
        status = "modified"
    elif before is None and not before_binary:
        status = "added"
    elif after is None and not after_binary:
        status = "removed"
    else:
        status = "unchanged" if before == after else "modified"
    diff = "" if binary or status == "unchanged" else "".join(
        difflib.unified_diff(
            (before or "").splitlines(keepends=True),
            (after or "").splitlines(keepends=True),
            fromfile=f"a/{rel}" if before is not None else "/dev/null",
            tofile=f"b/{rel}" if after is not None else "/dev/null",
        )
    )
    return {
        "path": rel,
        "status": status,
        "binary": binary,
        "before": before,
        "after": after,
        "diff": diff,
        "base": b.meta(),
        "head": h.meta(),
    }
