#!/usr/bin/env bash
# Un solo comando: construye el frontend y sirve API + UI en :8000.
# El repositorio de datos sale de isot.yaml (ver isot.example.yaml) o de --repo DIR.
# Opciones: --edit, --config RUTA, --repo DIR, --port N
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
exec python3 -m isot web --host 0.0.0.0 --port 8000 "$@"
