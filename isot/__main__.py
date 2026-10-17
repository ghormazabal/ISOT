"""Punto de entrada: `isot <comando>` o `python -m isot <comando>`."""

from __future__ import annotations

import sys

USAGE = """uso: isot <comando> [opciones]

  web         sirve la API y la UI (--config, --repo repetible, --edit, --host, --port)
  validate    valida repositorios de datos (--repo, --repo-id, --all, --config)
  index       regenera los _*_index.json (--check para CI; --repo-id, --all)
  init        crea isot.yaml (--path DIR) o un repo de datos vacío (--scaffold DIR)
  demo        copia la demo a un directorio aparte con git init y la sirve
  demo-diff   crea la rama demo/diff-example en un repo demo
  config      muestra la configuración y el estado de cada repositorio
  leakcheck   patrones prohibidos (repo de la herramienta; --repo para otro árbol)
"""


def _config_main(argv: list[str]) -> int:
    import argparse

    from isot import config as isot_config

    parser = argparse.ArgumentParser(prog="isot config")
    parser.add_argument("--config", default=None)
    parser.add_argument("--repo", action="append", default=None, help="Repositorio de datos (repetible).")
    args = parser.parse_args(argv)
    try:
        cfg, repos, default_id, origin = isot_config.resolve_repos(repo=args.repo, config_path=args.config)
    except isot_config.ConfigMissing:
        print(isot_config.missing_message(), file=sys.stderr)
        return 2
    except isot_config.ConfigError as exc:
        print(f"error de configuración: {exc}", file=sys.stderr)
        return 2
    print(f"origen:       {origin}")
    print(f"repositorios: {len(repos)} (por defecto: {default_id})")
    bad = 0
    for item in repos:
        mark = "*" if item.id == default_id else " "
        error = item.error()
        print(f" {mark} {item.id:<16} {item.label}")
        print(f"     ruta:     {isot_config.display_path(item.path)}")
        if error:
            bad += 1
            print(f"     estado:   NO DISPONIBLE: {error}")
            continue
        status = isot_config.repo_status(item.path, cfg, item)
        print(
            f"     git:      {'sí' if status['git'] else 'no'}  rama={status['branch']}  "
            f"HEAD={status['short']}  dirty={status['dirty']}"
        )
        if status["remote_url"]:
            print(f"     remote:   {status['remote']} {status['remote_url']}")
        if item.edit is False:
            print("     edición:  solo lectura (repos[].edit: false)")
    print(f"templates:    {', '.join(str(p) for p in cfg.templates) or '(solo los de la herramienta)'}")
    print(f"edición:      enabled={cfg.edit.enabled} push={cfg.edit.push} push_remote={cfg.edit.push_remote}")
    return 1 if bad == len(repos) else 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print(USAGE)
        return 0
    command, rest = args[0], args[1:]
    if command == "web":
        from isot import web

        return int(web.main(rest))
    if command in {"validate", "validator"}:
        from isot import validator

        return int(validator.main(rest))
    if command in {"index", "indexer"}:
        from isot import indexer

        return int(indexer.main(rest))
    if command == "leakcheck":
        from isot import leakcheck

        return int(leakcheck.main(rest))
    if command == "init":
        from isot import bootstrap

        return int(bootstrap.init_main(rest))
    if command == "demo":
        from isot import bootstrap

        return int(bootstrap.demo_main(rest))
    if command == "demo-diff":
        from isot import bootstrap

        return int(bootstrap.diff_example_main(rest))
    if command == "config":
        return _config_main(rest)
    print(f"comando desconocido: {command}", file=sys.stderr)
    print(USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
