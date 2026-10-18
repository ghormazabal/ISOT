"""API «Cambios» (`/api/worktree/...`, también bajo `/api/repos/{id}/`).

Lo que «Guardar» dejó en el working tree del repo de datos, su diff, descartar
y **un** commit con los archivos elegidos (índice regenerado e incluido) en la
rama `wizard/…` actual o en una nueva. Nunca en main/master ni push.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query

from isot.gitdiff import clear_caches
from isot.wizard import WizardError


def register(
    app: FastAPI,
    *,
    registry: Any,
    resolved_root: Callable[[], Path],
    require_edit: Callable[[], None],
    edit_for: Callable[[Any], bool],
    edit_globally: Callable[[], bool],
) -> None:
    from isot import worktree

    def can_edit() -> bool:
        return bool(edit_globally() and registry.configured and edit_for(registry.entry()))

    def run(fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return fn()
        except worktree.ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WizardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/worktree")
    def worktree_status() -> dict[str, Any]:
        """Cambios sin commit del repo de datos, por área (solo lectura)."""
        payload = run(lambda: worktree.summary(resolved_root()))
        payload["edit"] = can_edit()
        return payload

    @app.get("/api/worktree/diff")
    def worktree_diff(path: str = Query(...)) -> dict[str, Any]:
        return run(lambda: worktree.file_diff(resolved_root(), path))

    @app.post("/api/worktree/discard")
    def worktree_discard(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        paths = body.get("paths")
        tokens = body.get("tokens") if isinstance(body.get("tokens"), dict) else None
        if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
            raise HTTPException(status_code=400, detail="paths debe ser una lista de rutas")
        result = run(lambda: worktree.discard(resolved_root(), paths, tokens))
        clear_caches(resolved_root())
        return result

    @app.post("/api/worktree/commit")
    def worktree_commit(body: dict[str, Any]) -> dict[str, Any]:
        require_edit()
        paths = body.get("paths")
        if paths is not None and (not isinstance(paths, list) or not all(isinstance(p, str) for p in paths)):
            raise HTTPException(status_code=400, detail="paths debe ser una lista de rutas")
        target = body.get("target")
        if target not in (None, "current", "new"):
            raise HTTPException(status_code=400, detail="target debe ser current o new")
        result = run(
            lambda: worktree.commit(
                resolved_root(),
                paths,
                str(body.get("message") or ""),
                new_branch=None if target is None else target == "new",
                branch_name=str(body.get("branch_name") or "") or None,
                stay=body.get("stay") is not False,
            )
        )
        clear_caches(resolved_root())
        return result
