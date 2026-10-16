"""Resolución de la raíz del repositorio ISoT."""

from __future__ import annotations

from pathlib import Path

CATALOG_NAME = "sites.yaml"


def find_repo_root(start: Path | None = None) -> Path:
    """Localiza la raíz del inventario (sites.yaml o sites/)."""
    candidates: list[Path] = []
    origin = (start or Path.cwd()).resolve()
    candidates.append(origin)
    candidates.extend(origin.parents)

    package_root = Path(__file__).resolve().parent.parent
    candidates.append(package_root)

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / CATALOG_NAME).is_file() or (candidate / "sites").is_dir():
            return candidate

    raise FileNotFoundError(
        "No se encontró la raíz ISoT (se esperaba sites.yaml o sites/). "
        "Ejecuta los comandos desde el repositorio o pasa --root."
    )
