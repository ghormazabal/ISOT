"""Rutas de la herramienta y del repositorio de datos.

La herramienta (este paquete) trae ejemplos y plantillas; el inventario vive en
un repositorio de datos aparte, que se elige con `isot.yaml` (ver
`isot.config`) o `--repo`.
"""

from __future__ import annotations

import os
from pathlib import Path

CATALOG_NAME = "sites.yaml"
DEMO_REPO_REL = Path("examples") / "demo-repo"


def tool_root() -> Path:
    """Raíz de la herramienta (donde están `templates/` y `examples/`).

    `ISOT_HOME` la fija explícitamente (p.ej. en la imagen Docker); si no, es la
    carpeta que contiene el paquete `isot`.
    """
    env = os.environ.get("ISOT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def bundled_templates_dir() -> Path:
    return tool_root() / "templates"


def demo_repo_dir() -> Path:
    return tool_root() / DEMO_REPO_REL


def is_data_repo(path: Path) -> bool:
    """¿Parece un repositorio de datos ISoT? (tiene sites.yaml o sites/)."""
    return (path / CATALOG_NAME).is_file() or (path / "sites").is_dir()


def find_repo_root(start: Path | None = None) -> Path:
    """Repositorio de datos a partir de un directorio (el propio o un ancestro).

    Solo se usa como último recurso: lo normal es `data_repo.path` en la
    configuración o `--repo`. La carpeta de la herramienta no cuenta como repo
    de datos aunque contenga ejemplos.
    """
    origin = (start or Path.cwd()).resolve()
    for candidate in (origin, *origin.parents):
        if is_data_repo(candidate):
            return candidate
    raise FileNotFoundError(
        "No hay repositorio de datos configurado. Copia isot.example.yaml a isot.yaml "
        "y define data_repo.path, o ejecuta `isot init`."
    )
