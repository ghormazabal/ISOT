"""Refs comparables del repositorio de datos.

Todo lo que se puede comparar en ISoT (grafos, inventario, hallazgos, IPAM,
archivos YAML y renders) acepta como `base` o `head`:

- `local`: el working tree del repo de datos (incluye cambios sin commit);
- una rama, un tag o un commit (SHA completo o corto, o `HEAD~N`).

Las refs se validan con una expresión segura y luego con
`git rev-parse --verify` (lista de argumentos, nunca shell). Comparar es
siempre de solo lectura: los commits se materializan con `git archive` en un
directorio temporal **fuera** del repo de ISoT y del repo de datos, cacheados
por SHA (un SHA es inmutable) y borrados al salir o al desalojarlos de la caché.
"""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import io
import os
import shutil
import tarfile
import tempfile
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from isot.gitdiff import RefError, git, is_git_toplevel, resolve_sha, validate_ref

LOCAL = "local"
LOCAL_ALIASES = frozenset({"local", "working", "worktree", "working-tree"})
LOCAL_LABEL = "Local (working tree)"
TREE_CACHE_SIZE = 8
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv"})


def is_local(value: str | None) -> bool:
    return (value or "").strip().lower() in LOCAL_ALIASES


def parse_ref(value: str | None) -> str:
    """`local` (o sus alias) o una ref con sintaxis segura; si no, RefError."""
    if is_local(value):
        return LOCAL
    return validate_ref(value or "")


@dataclass(frozen=True)
class RefSpec:
    """Una ref ya resuelta. `key` identifica el contenido (SHA, o huella del working tree)."""

    name: str
    sha: str
    local: bool
    key: str

    @property
    def short(self) -> str:
        return LOCAL if self.local else self.sha[:7]

    def meta(self) -> dict[str, Any]:
        return {
            "ref": self.name,
            "sha": self.sha,
            "short": self.short,
            "local": self.local,
            "label": LOCAL_LABEL if self.local else self.name,
        }


def _head_sha(repo: Path) -> str:
    proc = git(repo, ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"])
    return proc.stdout.decode("utf-8", "replace").strip() if proc.returncode == 0 else ""


def worktree_fingerprint(repo: Path) -> str:
    """Huella barata del working tree (HEAD + ruta/tamaño/mtime de cada archivo)."""
    digest = hashlib.sha1(_head_sha(repo).encode())
    base = repo.resolve()
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            path = Path(current) / name
            try:
                stat = path.lstat()
            except OSError:
                continue
            rel = path.relative_to(base).as_posix()
            digest.update(f"{rel}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode("utf-8", "replace"))
    return digest.hexdigest()[:16]


def resolve_ref(repo: Path, value: str | None) -> RefSpec:
    name = parse_ref(value)
    if name == LOCAL:
        if not repo.is_dir():
            raise RefError("el repositorio de datos no existe")
        sha = _head_sha(repo) if is_git_toplevel(repo) else ""
        return RefSpec(LOCAL, sha, True, f"local:{worktree_fingerprint(repo)}")
    sha = resolve_sha(repo, name)
    return RefSpec(name, sha, False, sha)


def describe_ref(repo: Path, value: str | None) -> dict[str, Any]:
    """Meta de una ref para la UI (valida y resuelve; incluye el asunto del commit)."""
    spec = resolve_ref(repo, value)
    meta = spec.meta()
    if spec.sha:
        proc = git(repo, ["log", "-1", "--format=%s\t%an\t%aI", spec.sha, "--"])
        if proc.returncode == 0:
            subject, _, rest = proc.stdout.decode("utf-8", "replace").strip().partition("\t")
            author, _, date = rest.partition("\t")
            meta.update({"subject": subject, "author": author, "date": date})
    if spec.local:
        status = git(repo, ["status", "--porcelain", "--untracked-files=all"])
        lines = [line for line in status.stdout.decode("utf-8", "replace").splitlines() if line.strip()] if status.returncode == 0 else []
        meta["dirty"] = len(lines)
    return meta


# ---------------------------------------------------------------- caché de árboles


def _outside_tool(path: Path) -> bool:
    from isot.paths import tool_root

    resolved = path.resolve()
    return not resolved.is_relative_to(tool_root().resolve())


class TreeCache:
    """Árboles de commits materializados, por (repo, SHA), con LRU y recuento de uso."""

    def __init__(self, size: int = TREE_CACHE_SIZE) -> None:
        self.size = size
        self._lock = threading.Lock()
        self._base: Path | None = None
        self._trees: OrderedDict[tuple[str, str], Path] = OrderedDict()
        self._users: dict[tuple[str, str], int] = {}

    @property
    def base(self) -> Path:
        if self._base is None or not self._base.is_dir():
            base = Path(tempfile.mkdtemp(prefix="isot-refs-"))
            if not _outside_tool(base):  # p.ej. TMPDIR apuntando dentro de ISoT
                shutil.rmtree(base, ignore_errors=True)
                base = Path(tempfile.mkdtemp(prefix="isot-refs-", dir="/tmp"))
            self._base = base
        return self._base

    def _extract(self, repo: Path, sha: str, dest: Path) -> None:
        proc = git(repo, ["archive", "--format=tar", sha], timeout=60)
        if proc.returncode != 0:
            detail = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
            raise RefError(detail or f"git archive falló para {sha}")
        partial = Path(tempfile.mkdtemp(prefix=f".{sha[:12]}-", dir=self.base))
        try:
            with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as archive:
                archive.extractall(partial, filter="data")
            os.replace(partial, dest)
        finally:
            shutil.rmtree(partial, ignore_errors=True)

    @contextlib.contextmanager
    def acquire(self, repo: Path, sha: str) -> Iterator[Path]:
        if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
            raise RefError("se esperaba un SHA completo")
        repo_key = str(repo.resolve())
        key = (repo_key, sha)
        with self._lock:
            path = self._trees.get(key)
            if path is None or not path.is_dir():
                tag = hashlib.sha1(repo_key.encode()).hexdigest()[:8]
                path = self.base / f"{tag}-{sha}"
                if not path.is_dir():
                    self._extract(repo, sha, path)
                self._trees[key] = path
            self._trees.move_to_end(key)
            self._users[key] = self._users.get(key, 0) + 1
            self._evict()
        try:
            yield path
        finally:
            with self._lock:
                self._users[key] = max(0, self._users.get(key, 1) - 1)
                self._evict()

    def _evict(self) -> None:
        for key in list(self._trees):
            if len(self._trees) <= self.size:
                break
            if self._users.get(key):
                continue
            shutil.rmtree(self._trees.pop(key), ignore_errors=True)
            self._users.pop(key, None)

    def paths(self) -> list[Path]:
        with self._lock:
            return list(self._trees.values())

    def clear(self) -> None:
        with self._lock:
            for key in [k for k in self._trees if not self._users.get(k)]:
                shutil.rmtree(self._trees.pop(key), ignore_errors=True)
            if not self._trees and self._base is not None:
                shutil.rmtree(self._base, ignore_errors=True)
                self._base = None


TREES = TreeCache()
atexit.register(TREES.clear)


@contextlib.contextmanager
def tree_for(repo: Path, spec: RefSpec | str | None) -> Iterator[Path]:
    """Directorio de solo lectura con el contenido de la ref (`local` = el propio repo)."""
    resolved = spec if isinstance(spec, RefSpec) else resolve_ref(repo, spec)
    if resolved.local:
        yield repo
        return
    with TREES.acquire(repo, resolved.sha) as path:
        yield path
