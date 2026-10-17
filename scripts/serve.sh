#!/usr/bin/env bash
# Un solo comando: construye el frontend y sirve API + UI en 127.0.0.1:8000
# (--host 0.0.0.0 para exponerlo a la red, bajo tu responsabilidad).
# El repositorio de datos sale de isot.yaml (ver isot.example.yaml) o de --repo DIR.
# Opciones: --edit, --config RUTA, --repo DIR (repetible), --host H, --port N
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 -m pip install -e ".[dev]" -q
if [ ! -d web/node_modules ]; then
  (cd web && npm install)
fi
(cd web && npm run build)
if [ "${ISOT_EDIT:-0}" = "1" ]; then
  case " $* " in *" --edit "*) ;; *) set -- --edit "$@" ;; esac
fi
exec python3 -m isot web --port 8000 "$@"
