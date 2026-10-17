"""Configuración de ISoT (`isot.yaml`): qué repositorio de datos se visualiza/edita.

ISoT es un visualizador y editor del repositorio de datos al que el usuario ya
tiene acceso. No guarda credenciales ni clona con tokens: lee el clon local con
los permisos del usuario y cada operación git (refs, diff, ramas del wizard,
push opcional) corre sobre ese clon con su `git config` y su credential helper.

Orden de búsqueda del archivo de configuración:

1. `--config RUTA`
2. variable de entorno `ISOT_CONFIG`
3. `./isot.yaml` (directorio actual)
4. `~/.config/isot/config.yaml`

`--repo DIR` (repetible; o `ISOT_ROOT`, varias rutas separadas por `:`) fuerza
los repositorios de datos sin archivo; el id de cada uno es el nombre de su
carpeta.

Cada repositorio se valida por separado y de forma perezosa: uno inválido
(ruta inexistente, carpeta sin sites.yaml) queda como no disponible con su
mensaje de error, sin romper la configuración ni los demás.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from isot.paths import find_repo_root, is_data_repo

NO_CONFIG_MESSAGE = (
    "No hay repositorio de datos configurado. Copia isot.example.yaml a isot.yaml "
    "y define data_repo.path, o ejecuta `isot init`."
)
HINTS = [
    "cp isot.example.yaml isot.yaml   # y define data_repo.path (clon local de tu repo de datos)",
    "isot init --path ~/repos/mi-inventario   # escribe un isot.yaml mínimo",
    "isot init --scaffold ~/repos/mi-inventario   # empezar desde cero: repo vacío con git init",
    "isot demo   # copia el inventario de ejemplo a un directorio temporal y lo sirve",
]
CONFIG_FILENAME = "isot.yaml"
USER_CONFIG = Path("~/.config/isot/config.yaml")
TOP_KEYS = {"version", "data_repo", "repos", "default_repo", "templates", "edit", "ui"}
DATA_REPO_KEYS = {"path", "remote", "branch"}
REPO_KEYS = {"id", "name", "path", "remote", "branch", "edit"}
SAFE_REPO_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
EDIT_KEYS = {"enabled", "push", "push_remote"}
UI_KEYS = {"title"}
SAFE_REMOTE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")


class ConfigError(ValueError):
    """Configuración inválida (mensaje pensado para el usuario)."""


class ConfigMissing(ConfigError):
    """No hay configuración ni repositorio de datos."""

    def __init__(self, message: str = NO_CONFIG_MESSAGE) -> None:
        super().__init__(message)


@dataclass
class DataRepoConfig:
    path: Path
    remote: str = "origin"
    branch: str | None = None
    id: str = "default"
    name: str | None = None
    # None: hereda edit.enabled; False: este repositorio es solo lectura.
    edit: bool | None = None

    @property
    def label(self) -> str:
        return self.name or self.path.name

    def error(self) -> str | None:
        """Mensaje si el repositorio no está disponible (se evalúa al pedirlo)."""
        try:
            validate_data_repo_path(self.path, f"{self.id}: path")
        except ConfigError as exc:
            return str(exc)
        return None


@dataclass
class EditConfig:
    enabled: bool = False
    push: bool = False
    push_remote: str = "origin"


@dataclass
class UiConfig:
    title: str | None = None


@dataclass
class Config:
    """`repos` es la forma general (lista con id); `data_repo` es la abreviatura
    de un único repositorio. Hoy se usa el `default_repo`; la lista ya queda
    lista para un selector de repositorios en la UI."""

    source: Path | None
    repos: list[DataRepoConfig] = field(default_factory=list)
    default_repo: str | None = None
    templates: list[Path] = field(default_factory=list)
    edit: EditConfig = field(default_factory=EditConfig)
    ui: UiConfig = field(default_factory=UiConfig)

    def repo(self, repo_id: str | None = None) -> DataRepoConfig | None:
        if not self.repos:
            return None
        wanted = repo_id or self.default_repo
        if wanted:
            return next((item for item in self.repos if item.id == wanted), None)
        return self.repos[0]

    @property
    def data_repo(self) -> DataRepoConfig | None:
        return self.repo()


def _expand(value: str, base: Path) -> Path:
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def config_candidates(cli_path: str | Path | None = None) -> list[tuple[str, Path, bool]]:
    """(origen, ruta, obligatoria): las explícitas deben existir."""
    out: list[tuple[str, Path, bool]] = []
    if cli_path:
        out.append(("--config", Path(cli_path).expanduser(), True))
    env = os.environ.get("ISOT_CONFIG")
    if env:
        out.append(("ISOT_CONFIG", Path(env).expanduser(), True))
    out.append(("./isot.yaml", Path.cwd() / CONFIG_FILENAME, False))
    out.append(("~/.config/isot/config.yaml", USER_CONFIG.expanduser(), False))
    return out


def find_config_file(cli_path: str | Path | None = None) -> Path | None:
    for origin, path, required in config_candidates(cli_path):
        if path.is_file():
            return path.resolve()
        if required:
            raise ConfigError(f"{origin} apunta a {path}, que no existe")
    return None


def _check_keys(data: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ConfigError(
            f"{where}: clave(s) desconocida(s) {', '.join(unknown)}; válidas: {', '.join(sorted(allowed))}"
        )


def _bool(value: Any, where: str) -> bool:
    if isinstance(value, bool):
        return value
    raise ConfigError(f"{where} debe ser true o false")


def _optional_text(value: Any, where: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ConfigError(f"{where} debe ser texto")
    text = str(value).strip()
    return text or None


def validate_data_repo_path(path: Path, where: str = "data_repo.path") -> Path:
    shown = display_path(path)
    if not path.exists():
        raise ConfigError(
            f"{where}={shown} no existe. Clona ahí tu repositorio de datos o usa "
            "`isot init --scaffold DIR` para empezar desde cero."
        )
    if not path.is_dir():
        raise ConfigError(f"{where}={shown} no es un directorio")
    if not is_data_repo(path):
        raise ConfigError(
            f"{where}={shown} no parece un repositorio de datos ISoT (falta sites.yaml y sites/). "
            "Si está vacío, ejecuta `isot init --scaffold DIR`."
        )
    return path


def parse_config(data: Any, source: Path | None) -> Config:
    where = str(source) if source else "configuración"
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: el archivo debe ser un mapa YAML (clave: valor)")
    _check_keys(data, TOP_KEYS, where)
    version = data.get("version", 1)
    if version != 1:
        raise ConfigError(f"{where}: version={version!r} no soportada (usa version: 1)")
    base = source.parent if source else Path.cwd()

    if data.get("data_repo") is not None and data.get("repos") is not None:
        raise ConfigError(f"{where}: usa data_repo (un repositorio) o repos (lista), no ambos")
    repos: list[DataRepoConfig] = []
    raw_repo = data.get("data_repo")
    if raw_repo is not None:
        if not isinstance(raw_repo, dict):
            raise ConfigError(f"{where}: data_repo debe ser un mapa con path (y opcional remote, branch)")
        _check_keys(raw_repo, DATA_REPO_KEYS, f"{where}: data_repo")
        repos.append(_parse_repo(raw_repo, base, where, "data_repo"))
    raw_repos = data.get("repos")
    if raw_repos is not None:
        if not isinstance(raw_repos, list) or not raw_repos:
            raise ConfigError(f"{where}: repos debe ser una lista no vacía de {{id, path, …}}")
        for index, item in enumerate(raw_repos):
            label = f"repos[{index}]"
            if not isinstance(item, dict):
                raise ConfigError(f"{where}: {label} debe ser un mapa (id, name, path, remote, branch)")
            _check_keys(item, REPO_KEYS, f"{where}: {label}")
            repos.append(_parse_repo(item, base, where, label))
        ids = [item.id for item in repos]
        dupes = sorted({item for item in ids if ids.count(item) > 1})
        if dupes:
            raise ConfigError(f"{where}: ids de repos duplicados: {', '.join(dupes)}")
    default_repo = _optional_text(data.get("default_repo"), "default_repo")
    if default_repo and not any(item.id == default_repo for item in repos):
        raise ConfigError(
            f"{where}: default_repo={default_repo!r} no está en repos ({', '.join(item.id for item in repos) or 'vacío'})"
        )
    repo_cfg = repos[0] if repos and not default_repo else next((r for r in repos if r.id == default_repo), None)

    templates: list[Path] = []
    raw_templates = data.get("templates") or []
    if not isinstance(raw_templates, list):
        raise ConfigError(f"{where}: templates debe ser una lista de carpetas")
    for index, item in enumerate(raw_templates):
        text = _optional_text(item, f"templates[{index}]")
        if not text:
            raise ConfigError(f"templates[{index}] está vacío")
        folder = _expand(text, base)
        if not folder.is_dir():
            raise ConfigError(f"templates[{index}]={folder} no existe o no es una carpeta")
        templates.append(folder)

    del repo_cfg
    edit = EditConfig()
    raw_edit = data.get("edit")
    if raw_edit is not None:
        if not isinstance(raw_edit, dict):
            raise ConfigError(f"{where}: edit debe ser un mapa (enabled, push, push_remote)")
        _check_keys(raw_edit, EDIT_KEYS, f"{where}: edit")
        if "enabled" in raw_edit:
            edit.enabled = _bool(raw_edit["enabled"], "edit.enabled")
        if "push" in raw_edit:
            edit.push = _bool(raw_edit["push"], "edit.push")
        remote = _optional_text(raw_edit.get("push_remote"), "edit.push_remote")
        if remote:
            if not SAFE_REMOTE.fullmatch(remote):
                raise ConfigError("edit.push_remote debe ser el nombre de un remote git (p.ej. origin)")
            edit.push_remote = remote
        elif repos:
            edit.push_remote = (next((r for r in repos if r.id == default_repo), None) or repos[0]).remote

    ui = UiConfig()
    raw_ui = data.get("ui")
    if raw_ui is not None:
        if not isinstance(raw_ui, dict):
            raise ConfigError(f"{where}: ui debe ser un mapa")
        _check_keys(raw_ui, UI_KEYS, f"{where}: ui")
        ui.title = _optional_text(raw_ui.get("title"), "ui.title")

    return Config(source=source, repos=repos, default_repo=default_repo, templates=templates, edit=edit, ui=ui)


def _parse_repo(raw: dict[str, Any], base: Path, where: str, label: str) -> DataRepoConfig:
    raw_path = _optional_text(raw.get("path"), f"{label}.path")
    if not raw_path:
        raise ConfigError(f"{where}: falta {label}.path (ruta al clon local del repositorio de datos)")
    remote = _optional_text(raw.get("remote"), f"{label}.remote") or "origin"
    if not SAFE_REMOTE.fullmatch(remote):
        raise ConfigError(f"{label}.remote debe ser el nombre de un remote git (p.ej. origin)")
    # La ruta no se valida aquí: un repositorio inválido no debe romper los demás
    # (ver DataRepoConfig.error y resolve()).
    path = _expand(raw_path, base)
    repo_id = _optional_text(raw.get("id"), f"{label}.id")
    if repo_id is None:
        if label != "data_repo":
            raise ConfigError(f"{where}: falta {label}.id")
        repo_id = repo_slug(path)
    if not SAFE_REPO_ID.fullmatch(repo_id):
        raise ConfigError(f"{label}.id={repo_id!r} inválido (minúsculas, números, . _ -)")
    edit = None
    if "edit" in raw:
        if label == "data_repo":
            raise ConfigError(f"{where}: data_repo no admite edit (usa edit.enabled)")
        edit = _bool(raw["edit"], f"{label}.edit")
    return DataRepoConfig(
        path=path,
        remote=remote,
        branch=_optional_text(raw.get("branch"), f"{label}.branch"),
        id=repo_id,
        name=_optional_text(raw.get("name"), f"{label}.name"),
        edit=edit,
    )


def repo_slug(path: Path) -> str:
    """Id de repositorio a partir del nombre de la carpeta."""
    return re.sub(r"[^a-z0-9._-]+", "-", path.name.lower()).strip("-.")[:63] or "repo"


def repos_from_paths(paths: list[str | Path]) -> list[DataRepoConfig]:
    """Repositorios dados por --repo / ISOT_ROOT: id = carpeta (con sufijo si se repite)."""
    out: list[DataRepoConfig] = []
    seen: set[str] = set()
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if any(item.path == path for item in out):
            continue
        base = repo_slug(path)
        repo_id, n = base, 2
        while repo_id in seen:
            repo_id, n = f"{base}-{n}", n + 1
        seen.add(repo_id)
        out.append(DataRepoConfig(path=path, id=repo_id))
    return out


def env_repo_paths() -> list[str]:
    raw = os.environ.get("ISOT_ROOT") or ""
    return [item for item in raw.split(os.pathsep) if item.strip()]


def load_config_file(path: Path) -> Config:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: YAML inválido: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: no se pudo leer ({exc})") from exc
    return parse_config(data, path)


def load_config(cli_path: str | Path | None = None) -> Config | None:
    path = find_config_file(cli_path)
    return load_config_file(path) if path else None


@dataclass
class Resolved:
    """Repositorio de datos elegido y de dónde salió."""

    repo: Path
    origin: str
    config: Config
    repo_id: str = ""
    # Todos los repositorios de esta resolución (el elegido incluido).
    repos: list[DataRepoConfig] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.repo_id:
            self.repo_id = repo_slug(self.repo)
        if not self.repos:
            self.repos = [DataRepoConfig(path=self.repo, id=self.repo_id)]


def resolve_repos(
    *,
    repo: str | Path | list[str | Path] | None = None,
    config_path: str | Path | None = None,
    allow_cwd: bool = True,
) -> tuple[Config, list[DataRepoConfig], str | None, str]:
    """(config, repositorios, id por defecto, origen) sin validar cada repositorio.

    Orden: --repo (repetible), ISOT_ROOT, configuración, directorio actual.
    """
    config = load_config(config_path)
    base_config = config or Config(source=None)
    paths = [repo] if isinstance(repo, (str, Path)) else list(repo or [])
    if paths:
        repos = repos_from_paths(paths)
        return base_config, repos, repos[0].id, "--repo"
    env = env_repo_paths()
    if env:
        repos = repos_from_paths(env)
        return base_config, repos, repos[0].id, "ISOT_ROOT"
    if config and config.repos:
        default = config.data_repo or config.repos[0]
        return config, list(config.repos), default.id, str(config.source)
    if allow_cwd:
        try:
            root = find_repo_root(Path.cwd())
        except FileNotFoundError:
            pass
        else:
            repos = repos_from_paths([root])
            return base_config, repos, repos[0].id, "directorio actual"
    raise ConfigMissing()


def resolve(
    *,
    repo: str | Path | list[str | Path] | None = None,
    config_path: str | Path | None = None,
    allow_cwd: bool = True,
    repo_id: str | None = None,
) -> Resolved:
    """Repositorio de datos: --repo, ISOT_ROOT, configuración, o el directorio actual.

    `repo_id` elige uno de la lista (por defecto, default_repo). Lanza
    ConfigMissing (con instrucciones) si no hay ninguno, y ConfigError si la
    configuración o el repositorio elegido son inválidos.
    """
    config, repos, default_id, origin = resolve_repos(repo=repo, config_path=config_path, allow_cwd=allow_cwd)
    wanted = repo_id or default_id
    chosen = next((item for item in repos if item.id == wanted), None)
    if chosen is None:
        raise ConfigError(f"repositorio {wanted!r} no está configurado ({', '.join(item.id for item in repos)})")
    if origin in {"--repo", "ISOT_ROOT"}:
        validate_data_repo_path(chosen.path, origin)
    else:
        error = chosen.error()
        if error:
            raise ConfigError(error)
    return Resolved(chosen.path, origin, config, chosen.id, repos)


def apply(config: Config) -> None:
    """Aplica lo que la configuración cambia en el proceso (biblioteca de templates)."""
    from isot.inventory import set_template_library

    set_template_library(config.templates)


def missing_message() -> str:
    lines = [NO_CONFIG_MESSAGE, "", "Opciones:"]
    lines.extend(f"  {item}" for item in HINTS)
    lines.append("")
    lines.append("Se busca la configuración en: --config, $ISOT_CONFIG, ./isot.yaml, ~/.config/isot/config.yaml")
    return "\n".join(lines)


def cli_resolve(
    repo: str | Path | list[str | Path] | None,
    config_path: str | Path | None,
    repo_id: str | None = None,
) -> Resolved | None:
    """Para los comandos de consola: imprime las instrucciones en vez de fallar."""
    import sys

    try:
        resolved = resolve(repo=repo, config_path=config_path, repo_id=repo_id)
    except ConfigMissing:
        print(missing_message(), file=sys.stderr)
        return None
    except ConfigError as exc:
        print(f"error de configuración: {exc}", file=sys.stderr)
        return None
    apply(resolved.config)
    return resolved


# ---------------------------------------------------------------------------
# Estado git del repositorio de datos (para la cabecera de la UI y /api/config)
# ---------------------------------------------------------------------------

_USERINFO = re.compile(r"^([a-z][a-z0-9+.-]*://)[^/@]*@", re.IGNORECASE)


def sanitize_remote_url(url: str | None) -> str | None:
    """Quita usuario/token de una URL de remote (`https://user:tok@host` → `https://host`)."""
    if not url:
        return None
    text = url.strip()
    text = _USERINFO.sub(r"\1", text)
    return text or None


def _git(repo: Path, args: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode("utf-8", errors="replace").strip()


def cli_resolve_all(
    repo: str | Path | list[str | Path] | None,
    config_path: str | Path | None,
) -> tuple[Config, list[DataRepoConfig]] | None:
    """Todos los repositorios (para `--all`); imprime instrucciones si no hay."""
    import sys

    try:
        config, repos, _default, _origin = resolve_repos(repo=repo, config_path=config_path)
    except ConfigMissing:
        print(missing_message(), file=sys.stderr)
        return None
    except ConfigError as exc:
        print(f"error de configuración: {exc}", file=sys.stderr)
        return None
    apply(config)
    return config, repos


def repo_status(
    repo: Path,
    config: Config | None = None,
    entry: DataRepoConfig | None = None,
) -> dict[str, Any]:
    """Rama, HEAD, dirty y remote (saneado). Nunca incluye credenciales."""
    if entry is None and config:
        entry = next((item for item in config.repos if item.path == repo.resolve()), None) or config.data_repo
    remote_name = (entry.remote if entry else None) or "origin"
    expected = entry.branch if entry else None
    top = _git(repo, ["rev-parse", "--show-toplevel"])
    is_git = top is not None and Path(top).resolve() == repo.resolve()
    info: dict[str, Any] = {
        "id": entry.id if entry else repo_slug(repo),
        "name": (entry.name if entry and entry.name else None) or repo.name,
        "path": str(repo),
        "git": is_git,
        "branch": None,
        "head": None,
        "short": None,
        "dirty": False,
        "remote": remote_name,
        "remote_url": None,
        "expected_branch": expected,
    }
    if not is_git:
        return info
    branch = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    info["branch"] = branch if branch and branch != "HEAD" else None
    head = _git(repo, ["rev-parse", "--verify", "--quiet", "HEAD"])
    if head:
        info["head"] = head
        info["short"] = head[:7]
    status = _git(repo, ["status", "--porcelain"])
    info["dirty"] = bool(status)
    info["remote_url"] = sanitize_remote_url(_git(repo, ["remote", "get-url", remote_name]))
    return info


# ---------------------------------------------------------------------------
# Argumentos comunes de consola (validador, indexer, isot config)
# ---------------------------------------------------------------------------


def add_repo_arguments(parser: Any) -> None:
    parser.add_argument(
        "--repo",
        "--root",
        dest="root",
        action="append",
        type=Path,
        default=None,
        help="Repositorio de datos (repetible). Por defecto: los de isot.yaml.",
    )
    parser.add_argument("--repo-id", default=None, help="Id de un repositorio de la configuración.")
    parser.add_argument(
        "--all",
        dest="all_repos",
        action="store_true",
        help="Todos los repositorios configurados (los no disponibles cuentan como fallo).",
    )
    parser.add_argument("--config", type=Path, default=None, help="Archivo de configuración isot.yaml.")


@dataclass
class CliTarget:
    id: str
    path: Path
    error: str | None = None


def cli_targets(args: Any) -> list[CliTarget] | None:
    """Repositorios a procesar según --repo / --repo-id / --all. None: ya se imprimió el error."""
    import sys

    if getattr(args, "all_repos", False):
        if args.repo_id:
            print("error: usa --repo-id o --all, no ambos", file=sys.stderr)
            return None
        found = cli_resolve_all(args.root, args.config)
        if found is None:
            return None
        _config, repos = found
        return [CliTarget(item.id, item.path, item.error()) for item in repos]
    resolved = cli_resolve(args.root, args.config, repo_id=args.repo_id)
    if resolved is None:
        return None
    return [CliTarget(resolved.repo_id, resolved.repo)]



def display_path(path: Path) -> str:
    """Ruta con `~` si está bajo el home (para mensajes, la UI e isot.yaml)."""
    resolved = path.expanduser().resolve()
    try:
        return "~/" + resolved.relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()
