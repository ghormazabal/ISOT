"""Punto de entrada: python -m isot [validate|index|web|leakcheck]."""

from __future__ import annotations

import sys

from isot import indexer, leakcheck, validator, web

_COMMANDS = {
    "validate": validator.main,
    "index": indexer.main,
    "web": web.main,
    "leakcheck": leakcheck.main,
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        print("uso: python -m isot {validate|index|web|leakcheck} [opciones]")
        return 0
    command = args[0]
    if command not in _COMMANDS:
        print(f"comando desconocido: {command}", file=sys.stderr)
        print("uso: python -m isot {validate|index|web|leakcheck} [opciones]", file=sys.stderr)
        return 2
    return int(_COMMANDS[command](args[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
