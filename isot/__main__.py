"""Punto de entrada: `isot <comando>` o `python -m isot <comando>`."""

from __future__ import annotations

import sys

USAGE = """uso: isot <comando> [opciones]

  web         sirve la API y la UI (--config, --repo, --edit, --port)
  validate    valida el repositorio de datos (--repo, --config)
  index       regenera los _*_index.json (--check para CI)
  init        crea isot.yaml (--path DIR) o un repo de datos vacío (--scaffold DIR)
  demo        copia la demo a un directorio aparte con git init y la sirve
  demo-diff   crea la rama demo/diff-example en un repo demo
  config      muestra qué configuración y repo de datos se usan
  leakcheck   patrones prohibidos (repo de la herramienta; --repo para otro árbol)
"""


def _config_main(argv: list[str]) -> int:
    import argparse

    from isot import config as isot_config

    parser = argparse.ArgumentParser(prog="isot config")
    parser.add_argument("--config", default=None)
    parser.add_argument("--repo", default=None)
    args = parser.parse_args(argv)
    resolved = isot_config.cli_resolve(args.repo, args.config)
    if resolved is None:
        return 2
    status = isot_config.repo_status(resolved.repo, resolved.config)
    print(f"origen:       {resolved.origin}")
    print(f"repo datos:   {resolved.repo}")
    print(f"git:          {'sí' if status['git'] else 'no'}  rama={status['branch']}  HEAD={status['short']}  dirty={status['dirty']}")
    if status["remote_url"]:
        print(f"remote:       {status['remote']} {status['remote_url']}")
    cfg = resolved.config
    print(f"templates:    {', '.join(str(p) for p in cfg.templates) or '(solo los de la herramienta)'}")
    print(f"edición:      enabled={cfg.edit.enabled} push={cfg.edit.push} push_remote={cfg.edit.push_remote}")
    return 0


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
