"""Guardar (working tree) y Commit (rama) del repo de datos.

Toda escritura de la app («Guardar…», «Aplicar…», wizards, «Corregir»,
reindexar) deja los archivos en el **working tree local** del repo de datos,
sin commit. El control global «Cambios» lista lo pendiente (`git status`),
deja ver el diff, descartar y elegir qué entra en **un** commit, que va a la
rama `wizard/…` actual o a una `wizard/<slug>` nueva; nunca a main ni a otra
rama protegida. El índice se regenera justo antes del commit y entra en él.

Protecciones: rutas relativas seguras (sin `..`, `.git`, absolutas ni symlinks
que escapen), solo dentro del repo de datos y nunca en la instalación de ISoT.
Concurrencia: cada vista previa devuelve un `token` (huella de lo que hay en
disco + lo que se escribiría); si al guardar no coincide, se rechaza (409) para
que la UI vuelva a calcular y mostrar el diff nuevo.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Iterable

from isot.gitdiff import git, is_git_toplevel
from isot.wizard import PROTECTED_BRANCHES, SAFE_BRANCH, WizardError, current_branch, unique_wizard_branch

AREAS = ("output", "templates", "renders", "sites", "index", "other")
AREA_LABELS = {
    "output": "Salida de renders",
    "templates": "Templates",
    "renders": "renders.yaml",
    "sites": "Datos de sitios",
    "index": "Índice",
    "other": "Otros",
}
MAX_DIFF_CHARS = 200_000


class WorktreeError(WizardError):
    """Error de guardar/commit (la API lo devuelve como 400)."""


class ConflictError(WorktreeError):
    """El working tree cambió desde la vista previa (la API lo devuelve como 409)."""


# ----------------------------------------------------------------------- rutas


def _root(repo: Path) -> Path:
    from isot.renders import inside_tool

    root = repo.resolve()
    if inside_tool(root):
        raise WorktreeError("no se escribe en la instalación de ISoT: solo en el repo de datos")
    if not is_git_toplevel(repo):
        raise WorktreeError("el repo de datos no es la raíz de un repositorio git")
    return root


def check_paths(root: Path, rels: Iterable[str], prefixes: str | Iterable[str] | None = None) -> None:
    """Valida rutas relativas: seguras, dentro del repo (también tras symlinks) y de `prefixes`."""
    from isot.renders import RenderError, safe_rel_dir

    allowed = [prefixes] if isinstance(prefixes, str) else list(prefixes or [])
    for rel in rels:
        try:
            clean = safe_rel_dir(rel, "archivo")
        except RenderError as exc:
            raise WorktreeError(str(exc)) from exc
        if clean != rel:
            raise WorktreeError(f"ruta no canónica: {rel}")
        if allowed and not any(rel == p or rel.startswith(p.rstrip("/") + "/") for p in allowed):
            raise WorktreeError(f"ruta fuera del destino: {rel}")
        path = root / rel
        parent = path.parent
        while not parent.exists() and parent != root:
            parent = parent.parent
        if not parent.resolve().is_relative_to(root):
            raise WorktreeError(f"ruta fuera del repositorio (symlink): {rel}")
        if path.is_symlink():
            raise WorktreeError(f"no se escribe a través de un symlink: {rel}")


def _hash_file(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    return hashlib.sha1(path.read_bytes()).hexdigest()


def _hash_text(text: str | None) -> str:
    return "-" if text is None else hashlib.sha1(text.encode("utf-8")).hexdigest()


def plan_token(repo: Path, writes: dict[str, str], deletes: Iterable[str] = ()) -> str:
    """Huella de (contenido actual en disco, contenido propuesto) de cada ruta del plan."""
    root = repo.resolve()
    h = hashlib.sha1()
    items = [(rel, writes[rel]) for rel in sorted(writes)] + [(rel, None) for rel in sorted(set(deletes))]
    for rel, new in items:
        h.update(f"{rel}\0{_hash_file(root / rel) or '-'}\0{_hash_text(new)}\n".encode("utf-8", "replace"))
    return h.hexdigest()[:20]


# ---------------------------------------------------------------- git status


def _area(rel: str, render_dirs: list[str]) -> str:
    from isot.indexer import INDEX_FILE_NAMES

    name = rel.rsplit("/", 1)[-1]
    if name in INDEX_FILE_NAMES:
        return "index"
    if name == "renders.yaml":
        return "renders"
    parts = rel.split("/")
    if parts[0] == "manifest-templates" or "cluster-templates" in parts[:-1]:
        return "templates"
    if any(rel.startswith(d + "/") for d in render_dirs) or name == ".isot-render.json":
        return "output"
    if parts[0] in {"sites", "sites.yaml"} or rel == "sites.yaml":
        return "sites"
    return "other"


def status_entries(repo: Path) -> list[dict[str, Any]]:
    """Cambios sin commit del repo (`git status`): ruta, estado y área."""
    from isot.indexer import _render_dirs

    proc = git(repo, ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"])
    if proc.returncode != 0:
        raise WorktreeError((proc.stderr or b"").decode("utf-8", "replace") or "git status falló")
    render_dirs = _render_dirs(repo)
    out: list[dict[str, Any]] = []
    for item in proc.stdout.decode("utf-8", "replace").split("\0"):
        if len(item) < 4:
            continue
        code, rel = item[:2], item[3:]
        if code == "??":
            status = "untracked"
        elif "D" in code:
            status = "deleted"
        elif code[0] == "A":
            status = "added"
        else:
            status = "modified"
        out.append({"path": rel, "status": status, "area": _area(rel, render_dirs), "staged": code[0] not in {" ", "?"}})
    out.sort(key=lambda e: (AREAS.index(e["area"]), e["path"]))
    return out


def summary(repo: Path) -> dict[str, Any]:
    """Estado para la barra superior y el panel «Cambios»."""
    entries = status_entries(repo)
    branch = current_branch(repo)
    on_wizard = _is_wizard(branch)
    head = git(repo, ["rev-parse", "--verify", "--quiet", "HEAD"])
    counts = {area: sum(1 for e in entries if e["area"] == area) for area in AREAS}
    return {
        "branch": branch,
        "head": head.stdout.decode().strip()[:7] if head.returncode == 0 else None,
        "on_wizard": on_wizard,
        "default_target": "current" if on_wizard else "new",
        "files": entries,
        "count": len(entries),
        "pending": sum(1 for e in entries if e["area"] != "index"),
        "counts": counts,
        "areas": [{"id": a, "label": AREA_LABELS[a]} for a in AREAS],
    }


def _head_text(repo: Path, rel: str) -> tuple[str | None, bool]:
    proc = git(repo, ["cat-file", "-p", f"HEAD:{rel}"])
    if proc.returncode != 0:
        return None, False
    try:
        return proc.stdout.decode("utf-8"), False
    except UnicodeDecodeError:
        return None, True


def _disk_text(path: Path) -> tuple[str | None, bool]:
    if not path.is_file() or path.is_symlink():
        return None, False
    data = path.read_bytes()
    try:
        return data.decode("utf-8"), False
    except UnicodeDecodeError:
        return None, True


def file_diff(repo: Path, rel: str) -> dict[str, Any]:
    """Diff HEAD → working tree de una ruta pendiente."""
    from isot.renders import unified

    root = repo.resolve()
    check_paths(root, [rel])
    before, bin_a = _head_text(repo, rel)
    after, bin_b = _disk_text(root / rel)
    binary = bin_a or bin_b
    diff = "" if binary else unified(rel, before, after)
    return {
        "path": rel,
        "binary": binary,
        "diff": diff[:MAX_DIFF_CHARS],
        "truncated": len(diff) > MAX_DIFF_CHARS,
        "token": plan_token(repo, {}, [rel]),
    }


def pending_info(repo: Path, rels: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Rutas del plan que ya tienen cambios sin commit (para avisar antes de sobrescribirlas)."""
    wanted = set(rels)
    if not wanted:
        return {}
    try:
        entries = {e["path"]: e for e in status_entries(repo)}
    except WorktreeError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for rel in sorted(wanted & set(entries)):
        info = file_diff(repo, rel)
        out[rel] = {"status": entries[rel]["status"], "diff": info["diff"], "binary": info["binary"]}
    return out


# ------------------------------------------------------------------- guardar


def _prune_empty(root: Path, start: Path) -> None:
    current = start
    while current != root and current.is_relative_to(root):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def save_files(
    repo: Path,
    writes: dict[str, str],
    deletes: Iterable[str] = (),
    *,
    allowed_prefix: str | Iterable[str] | None = None,
    token: str | None = None,
) -> dict[str, Any]:
    """Escribe/borra en el working tree del repo de datos (sin commit)."""
    root = _root(repo)
    deletes = sorted(set(deletes))
    check_paths(root, [*writes, *deletes], allowed_prefix)
    if token and token != plan_token(root, writes, deletes):
        raise ConflictError("el working tree cambió desde la vista previa; revisa el diff nuevo y confirma otra vez")
    real_writes = {rel: text for rel, text in writes.items() if _disk_text(root / rel)[0] != text}
    real_deletes = [rel for rel in deletes if (root / rel).is_file()]
    if not real_writes and not real_deletes:
        raise WorktreeError("no hay cambios que guardar: el working tree ya tiene ese contenido")
    for rel, content in real_writes.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.parent.resolve().is_relative_to(root) or path.is_symlink():
            raise WorktreeError(f"ruta fuera del repositorio (symlink): {rel}")
        path.write_text(content, encoding="utf-8")
    for rel in real_deletes:
        path = root / rel
        if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
            path.unlink()
            _prune_empty(root, path.parent)
    from isot.indexer import _STATUS_CACHE

    _STATUS_CACHE.pop(str(root), None)
    pending = status_entries(root)
    return {
        "ok": True,
        "saved": True,
        "written": sorted(real_writes),
        "deleted": real_deletes,
        "files": sorted([*real_writes, *real_deletes]),
        "pending": sum(1 for e in pending if e["area"] != "index"),
        "branch": current_branch(root),
    }


def snapshot(root: Path, rels: Iterable[str]) -> dict[str, bytes | None]:
    """Contenido actual de unas rutas (para deshacer un guardado que no pasa el validador)."""
    return {rel: (root / rel).read_bytes() if (root / rel).is_file() else None for rel in rels}


def restore(root: Path, saved: dict[str, bytes | None]) -> None:
    for rel, data in saved.items():
        path = root / rel
        if data is None:
            if path.is_file() and not path.is_symlink():
                path.unlink()
                _prune_empty(root, path.parent)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


# ------------------------------------------------------------------ descartar


def discard(repo: Path, paths: Iterable[str], tokens: dict[str, str] | None = None) -> dict[str, Any]:
    """Descarta cambios sin commit de esas rutas (vuelven a HEAD; las nuevas se borran)."""
    root = _root(repo)
    wanted = sorted(set(paths))
    if not wanted:
        raise WorktreeError("no hay archivos que descartar")
    check_paths(root, wanted)
    entries = {e["path"]: e for e in status_entries(root)}
    for rel in wanted:
        if rel not in entries:
            raise WorktreeError(f"{rel} no tiene cambios sin commit")
        if tokens and rel in tokens and tokens[rel] != plan_token(root, {}, [rel]):
            raise ConflictError(f"{rel} cambió desde que viste su diff; vuelve a revisarlo")
    for rel in wanted:
        in_head = git(root, ["cat-file", "-e", f"HEAD:{rel}"]).returncode == 0
        if in_head:
            proc = git(root, ["checkout", "HEAD", "--", rel])
            if proc.returncode != 0:
                raise WorktreeError((proc.stderr or proc.stdout).decode("utf-8", "replace") or f"no se pudo descartar {rel}")
        else:
            git(root, ["rm", "-q", "--cached", "--ignore-unmatch", "--", rel])
            path = root / rel
            if path.is_file() and not path.is_symlink():
                path.unlink()
                _prune_empty(root, path.parent)
    from isot.indexer import _STATUS_CACHE

    _STATUS_CACHE.pop(str(root), None)
    return {"ok": True, "discarded": wanted, **summary(root)}


# --------------------------------------------------------------------- commit


def _is_wizard(branch: str) -> bool:
    return branch.startswith("wizard/") and branch not in PROTECTED_BRANCHES and bool(SAFE_BRANCH.fullmatch(branch))


def _subset_index_writes(root: Path, selected: list[str]) -> dict[str, str]:
    """Índice de HEAD + solo los archivos elegidos (para que el commit sea coherente)."""
    from isot.indexer import pending_index_writes

    archive = git(root, ["archive", "--format=tar", "HEAD"], timeout=300)
    if archive.returncode != 0:
        raise WorktreeError("no se pudo leer HEAD para regenerar el índice")
    with tempfile.TemporaryDirectory(prefix="isot-index-") as tmp:
        base = Path(tmp)
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(base, filter="data")
        for rel in selected:
            src, dst = root / rel, base / rel
            if src.is_file() and not src.is_symlink():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
            elif dst.is_file():
                dst.unlink()
        return pending_index_writes(base)


def commit(
    repo: Path,
    paths: Iterable[str] | None,
    message: str,
    *,
    new_branch: bool | None = None,
    branch_name: str | None = None,
    stay: bool = True,
) -> dict[str, Any]:
    """Un commit con los archivos elegidos (+ índice regenerado) en la rama wizard/… actual o en una nueva."""
    from isot.indexer import write_indexes

    root = _root(repo)
    if git(root, ["rev-parse", "--verify", "--quiet", "HEAD"]).returncode != 0:
        raise WorktreeError("el repo de datos no tiene ningún commit todavía")
    entries = status_entries(root)
    pending = [e["path"] for e in entries if e["area"] != "index"]
    if paths is None:
        selected = pending
    else:
        selected = sorted(set(paths))
        check_paths(root, selected)
        unknown = [rel for rel in selected if rel not in pending]
        if unknown:
            raise WorktreeError(f"sin cambios pendientes (o es un índice, que se incluye solo): {', '.join(unknown[:5])}")
    index_pending = [e["path"] for e in entries if e["area"] == "index"]
    if not selected and (pending or not index_pending):
        # Solo índice pendiente (p.ej. tras «Regenerar índice») → commit solo del índice.
        raise WorktreeError("no hay cambios seleccionados para el commit")
    text = " ".join(str(message or "").split())[:200] or f"isot: {len(selected)} archivo(s)"
    branch_now = current_branch(root)
    on_wizard = _is_wizard(branch_now)
    if new_branch is None:
        new_branch = not on_wizard
    if not new_branch and not on_wizard:
        raise WorktreeError(f"no se hace commit en {branch_now}: elige una rama wizard/… nueva")
    created = False
    if new_branch:
        label = branch_name or text
        target = unique_wizard_branch(root, label)
        if target in PROTECTED_BRANCHES or not _is_wizard(target):
            raise WorktreeError("nombre de rama inválido")
        proc = git(root, ["checkout", "-q", "-b", target])
        if proc.returncode != 0:
            raise WorktreeError((proc.stderr or proc.stdout).decode("utf-8", "replace") or "no se pudo crear la rama")
        created = True
    else:
        target = branch_now

    def rollback() -> None:
        git(root, ["reset", "-q"])
        if created:
            git(root, ["checkout", "-q", branch_now])
            git(root, ["branch", "-q", "-D", target])

    try:
        git(root, ["reset", "-q"])  # solo entra lo elegido
        added = git(root, ["add", "-A", "--", *selected])
        if added.returncode != 0:
            raise WorktreeError((added.stderr or added.stdout).decode("utf-8", "replace") or "git add falló")
        if set(selected) == set(pending):
            write_indexes(root)
            index_paths = [e["path"] for e in status_entries(root) if e["area"] == "index"]
            if index_paths:
                git(root, ["add", "-A", "--", *index_paths])
            index_files = sorted(index_paths)
        else:
            index_files = []
            for rel, content in sorted(_subset_index_writes(root, selected).items()):
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as handle:
                    handle.write(content)
                    tmp_name = handle.name
                try:
                    blob = git(root, ["hash-object", "-w", tmp_name]).stdout.decode().strip()
                finally:
                    Path(tmp_name).unlink(missing_ok=True)
                proc = git(root, ["update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}"])
                if proc.returncode != 0:
                    raise WorktreeError(f"no se pudo preparar el índice {rel}")
                index_files.append(rel)
        if git(root, ["diff", "--cached", "--quiet"]).returncode == 0:
            raise WorktreeError("sin cambios: la rama ya tiene ese contenido")
        has_identity = git(root, ["config", "user.email"]).returncode == 0
        identity = [] if has_identity else ["-c", "user.name=ISoT", "-c", "user.email=isot@example.net"]
        done = git(root, [*identity, "commit", "-q", "-m", text])
        if done.returncode != 0:
            raise WorktreeError((done.stderr or done.stdout).decode("utf-8", "replace") or "git commit falló")
    except WorktreeError:
        rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        rollback()
        raise WorktreeError(str(exc)) from exc
    sha = git(root, ["rev-parse", "HEAD"]).stdout.decode().strip()
    if index_files and set(selected) != set(pending):
        # El índice es derivado: el working tree queda con el del commit (sin falsos «modificado»).
        git(root, ["checkout", "HEAD", "--", *index_files])
    now, note = target, None
    if created and not stay:
        back = git(root, ["checkout", "-q", branch_now])
        if back.returncode == 0:
            now = branch_now
        else:
            note = f"sigues en {target}: no se pudo volver a {branch_now} sin pisar cambios sin commit"
    from isot.indexer import _STATUS_CACHE

    _STATUS_CACHE.pop(str(root), None)
    left = [e for e in status_entries(root) if e["area"] != "index"]
    return {
        "ok": True,
        "branch": target,
        "created": created,
        "base": branch_now,
        "sha": sha,
        "short": sha[:7],
        "message": text,
        "files": selected,
        "index_files": index_files,
        "current_branch": now,
        "note": note,
        "pending": len(left),
    }
