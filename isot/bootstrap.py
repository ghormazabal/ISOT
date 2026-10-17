"""`isot init` y `isot demo`: crear la configuración, un repo de datos vacío o la demo.

Nada de esto toca la carpeta de la herramienta: la demo se copia a un directorio
aparte (temporal por defecto) con su propio `git init`.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from isot import config as isot_config
from isot.indexer import write_indexes
from isot.paths import bundled_templates_dir, demo_repo_dir, is_data_repo

DIFF_EXAMPLE_BRANCH = "demo/diff-example"
FALLBACK_IDENTITY = ["-c", "user.name=ISoT", "-c", "user.email=isot@example.net"]

SCAFFOLD_README = """# Repositorio de datos ISoT

Inventario (sitios, devices, clústeres y cluster-templates) que visualiza y
edita [ISoT](https://github.com/ghormazabal/ISOT). La herramienta no guarda
credenciales: el acceso a este repositorio (quién lo lee, quién aprueba los
PR) lo controla este mismo repositorio.

```
sites.yaml                 # catálogo de sitios y espacios de direcciones
sites/<id>/topology/       # subnets, VLANs, reglas de cableado
sites/<id>/devices/        # baremetal, network, virtual-machines
sites/<id>/clusters/       # clústeres (template_ref → cluster-templates)
cluster-templates/         # templates propios (prioridad sobre los de ISoT)
site-templates/            # plantillas de sitio propias para el wizard
```

El primer sitio se crea con el wizard «Nuevo sitio» (`isot web --edit`).
Los `_*_index.json` se regeneran con `isot index` y el validador corre con
`isot validate` (también en CI, ver `.github/workflows/isot-validate.yml`).
"""

SCAFFOLD_GITIGNORE = """# Configuración local de ISoT (rutas de tu máquina)
isot.yaml
__pycache__/
.DS_Store
"""

SCAFFOLD_WORKFLOW = """name: Validar inventario ISoT

on:
  pull_request:
  push:
    branches: [main]

jobs:
  isot:
    uses: ghormazabal/ISOT/.github/workflows/validate-data-repo.yml@main
"""


def display_path(path: Path) -> str:
    """Ruta para escribir en un isot.yaml: con `~` si está bajo el home."""
    resolved = path.expanduser().resolve()
    home = Path.home().resolve()
    try:
        return "~/" + resolved.relative_to(home).as_posix()
    except ValueError:
        return resolved.as_posix()


def render_config(repo_path: Path, *, edit: bool = False) -> str:
    return (
        "# Configuración de ISoT (generada por `isot init`). Ver isot.example.yaml.\n"
        "version: 1\n"
        "data_repo:\n"
        f"  path: {display_path(repo_path)}\n"
        "edit:\n"
        f"  enabled: {'true' if edit else 'false'}\n"
        "  push: false\n"
    )


def write_config(output: Path, repo_path: Path, *, edit: bool, force: bool) -> Path:
    output = output.expanduser()
    if output.exists() and not force:
        raise FileExistsError(f"{output} ya existe (usa --force para sobrescribirlo)")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_config(repo_path, edit=edit), encoding="utf-8")
    return output


def _git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(["git", "-C", str(repo), *args], check=False, capture_output=True, timeout=60)


def _has_identity(repo: Path) -> bool:
    return _git(repo, ["config", "user.email"]).returncode == 0


def git_init_commit(repo: Path, message: str, *, fallback_identity: bool) -> str | None:
    """git init + commit inicial. Usa la identidad git del usuario; si no tiene y
    `fallback_identity`, una genérica. Devuelve el SHA o None si no pudo commitear."""
    if _git(repo, ["init", "-q", "-b", "main"]).returncode != 0:
        _git(repo, ["init", "-q"])
        _git(repo, ["checkout", "-q", "-b", "main"])
    _git(repo, ["add", "-A"])
    identity = [] if _has_identity(repo) else (FALLBACK_IDENTITY if fallback_identity else None)
    if identity is None:
        return None
    proc = _git(repo, [*identity, "commit", "-q", "-m", message])
    if proc.returncode != 0:
        return None
    return _git(repo, ["rev-parse", "HEAD"]).stdout.decode().strip()


def scaffold(target: Path, *, site_template: str = "edge", commit: bool = True) -> dict[str, object]:
    """Repo de datos vacío: sites.yaml, sites/, cluster-templates/, site-templates/<plantilla>."""
    target = target.expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"{target} existe y no está vacío")
    source = bundled_templates_dir() / "sites" / site_template
    if not (source / "topology" / "index.yaml").is_file():
        raise FileNotFoundError(f"no existe la plantilla de sitio {site_template!r} en templates/sites/")
    target.mkdir(parents=True, exist_ok=True)
    (target / "sites.yaml").write_text(
        "# Catálogo de sitios. El wizard «Nuevo sitio» agrega las entradas.\nsites: []\naddress_spaces: []\n",
        encoding="utf-8",
    )
    (target / "sites").mkdir(exist_ok=True)
    (target / "cluster-templates").mkdir(exist_ok=True)
    shutil.copytree(source, target / "site-templates" / site_template)
    (target / "README.md").write_text(SCAFFOLD_README, encoding="utf-8")
    (target / ".gitignore").write_text(SCAFFOLD_GITIGNORE, encoding="utf-8")
    workflow = target / ".github" / "workflows" / "isot-validate.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(SCAFFOLD_WORKFLOW, encoding="utf-8")
    write_indexes(target)
    sha = git_init_commit(target, "Esqueleto inicial del repositorio de datos ISoT", fallback_identity=False) if commit else None
    return {"path": target, "sha": sha}


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

LAB_SUBNET = """  - name: LAB_NET
    cidr: 10.10.31.0/24
    vlan: 2099
    gateway: 10.10.31.1
    vrf: ""
    description: "VLAN de laboratorio (ejemplo para el modo Comparar)"
"""


def create_diff_example(repo: Path) -> str:
    """Rama demo/diff-example: VLAN 2099 LAB, se quita site1-jump001, mtu 1440 → 1500."""
    repo = repo.resolve()
    current = _git(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).stdout.decode().strip() or "main"
    if _git(repo, ["rev-parse", "--verify", "--quiet", f"refs/heads/{DIFF_EXAMPLE_BRANCH}"]).returncode == 0:
        return _git(repo, ["rev-parse", DIFF_EXAMPLE_BRANCH]).stdout.decode().strip()
    if _git(repo, ["status", "--porcelain"]).stdout.strip():
        raise RuntimeError("working tree sucio: confirma o descarta los cambios antes de crear la rama demo")
    if _git(repo, ["checkout", "-q", "-b", DIFF_EXAMPLE_BRANCH]).returncode != 0:
        raise RuntimeError("no se pudo crear la rama demo/diff-example")
    try:
        values = repo / "cluster-templates" / "rke2-rancher-hybrid-calico" / "values" / "rke2-rancher-hybrid-calico.yaml"
        if values.is_file():
            values.write_text(values.read_text(encoding="utf-8").replace("mtu: 1440", "mtu: 1500", 1), encoding="utf-8")
        topo = repo / "sites" / "site1" / "topology" / "index.yaml"
        if topo.is_file():
            text = topo.read_text(encoding="utf-8")
            text = text.replace("\nvlans:\n", "\n" + LAB_SUBNET.rstrip("\n") + "\n\nvlans:\n", 1) if "LAB_NET" not in text else text
            text = text.replace(
                "  - {id: 4094, name: DCI, subnet: DCI}\n",
                "  - {id: 4094, name: DCI, subnet: DCI}\n  - {id: 2099, name: LAB, subnet: LAB_NET}\n",
                1,
            )
            text = text.replace("\n\n\nvlans:", "\n\nvlans:")
            topo.write_text(text, encoding="utf-8")
        jump = repo / "sites" / "site1" / "devices" / "virtual-machines" / "site1-jump001.yaml"
        if jump.is_file():
            jump.unlink()
        write_indexes(repo)
        _git(repo, ["add", "-A"])
        identity = [] if _has_identity(repo) else FALLBACK_IDENTITY
        proc = _git(repo, [*identity, "commit", "-q", "-m", "Demo: cambios de ejemplo para probar el diff"])
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", errors="replace") or "git commit falló")
        return _git(repo, ["rev-parse", "HEAD"]).stdout.decode().strip()
    finally:
        _git(repo, ["checkout", "-q", current])


def create_demo(target: Path | None = None, *, diff_example: bool = True) -> Path:
    """Copia examples/demo-repo a `target` (o a un temporal) y hace git init."""
    source = demo_repo_dir()
    if not is_data_repo(source):
        raise FileNotFoundError(f"no se encontró la demo en {source}")
    if target is None:
        target = Path(tempfile.mkdtemp(prefix="isot-demo-"))
    target = target.expanduser().resolve()
    if target.exists() and any(target.iterdir()):
        if is_data_repo(target) and (target / ".git").exists():
            return target
        raise FileExistsError(f"{target} existe y no está vacío")
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if git_init_commit(target, "Inventario de ejemplo ISoT", fallback_identity=True) is None:
        raise RuntimeError("no se pudo crear el commit inicial de la demo")
    if diff_example:
        create_diff_example(target)
    return target


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def init_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="isot init",
        description="Crea isot.yaml (y opcionalmente un repositorio de datos vacío).",
    )
    parser.add_argument("--path", type=Path, help="Clon local de tu repositorio de datos.")
    parser.add_argument("--scaffold", type=Path, metavar="DIR", help="Crea un repo de datos vacío en DIR (git init).")
    parser.add_argument("--site-template", default="edge", help="Plantilla de sitio a copiar (templates/sites/).")
    parser.add_argument("--output", type=Path, default=Path(isot_config.CONFIG_FILENAME), help="Dónde escribir la configuración.")
    parser.add_argument("--no-config", action="store_true", help="Con --scaffold: no escribe isot.yaml.")
    parser.add_argument("--edit", action="store_true", help="Escribe edit.enabled: true.")
    parser.add_argument("--force", action="store_true", help="Sobrescribe un isot.yaml existente.")
    args = parser.parse_args(argv)
    if not args.path and not args.scaffold:
        parser.print_help()
        print("\nEjemplos:\n  isot init --path ~/repos/mi-inventario\n  isot init --scaffold ~/repos/mi-inventario")
        return 2
    repo_path = args.scaffold or args.path
    if args.scaffold:
        try:
            result = scaffold(args.scaffold, site_template=args.site_template)
        except (FileExistsError, FileNotFoundError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Repositorio de datos creado en {result['path']}")
        if result["sha"]:
            print(f"Commit inicial {str(result['sha'])[:7]} en main (con tu identidad git).")
        else:
            print(
                "Archivos creados y en stage, sin commit: configura tu identidad git "
                "(git config user.name / user.email) y ejecuta `git commit -m inicial` en ese directorio."
            )
        if args.no_config:
            return 0
    else:
        try:
            isot_config.validate_data_repo_path(args.path.expanduser().resolve(), "--path")
        except isot_config.ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    try:
        written = write_config(args.output, repo_path, edit=args.edit or bool(args.scaffold), force=args.force)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Configuración escrita en {written}. Inicia la UI con: isot web" + (" --edit" if not args.edit and not args.scaffold else ""))
    return 0


def demo_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="isot demo",
        description="Copia el inventario de ejemplo a un directorio aparte (git init) y lo sirve.",
    )
    parser.add_argument("--dir", type=Path, default=None, help="Destino (por defecto, un directorio temporal).")
    parser.add_argument("--no-serve", action="store_true", help="Solo crea el repo de la demo.")
    parser.add_argument("--no-diff-example", action="store_true", help="No crea la rama demo/diff-example.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        target = create_demo(args.dir, diff_example=not args.no_diff_example)
    except (FileExistsError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Demo lista en {target} (repo git propio; la carpeta de la herramienta no se toca).")
    if not args.no_diff_example:
        print(f"Rama {DIFF_EXAMPLE_BRANCH} creada: Comparar → base main, head {DIFF_EXAMPLE_BRANCH}.")
    print(f"Para usarla después: isot web --repo {target} --edit   (o data_repo.path en isot.yaml)")
    if args.no_serve:
        return 0
    from isot import web

    return web.main(["--repo", str(target), "--edit", "--host", args.host, "--port", str(args.port)])


def diff_example_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="isot demo-diff", description="Crea la rama demo/diff-example en un repo demo.")
    parser.add_argument("repo", type=Path)
    args = parser.parse_args(argv)
    try:
        sha = create_diff_example(args.repo.expanduser())
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"{DIFF_EXAMPLE_BRANCH} → {sha[:7]}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(init_main())
