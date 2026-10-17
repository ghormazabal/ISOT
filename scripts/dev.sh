#!/usr/bin/env bash
# Desarrollo: API en :8000 (--reload) y Vite (proxy /api) en :5173.
# El repositorio de datos sale de isot.yaml o de ISOT_ROOT / ISOT_CONFIG.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 -m pip install -e ".[dev]" -q
if [ ! -d web/node_modules ]; then
  (cd web && npm install)
fi
EDIT="${ISOT_EDIT:-0}"
for arg in "$@"; do
  if [ "$arg" = "--edit" ]; then EDIT=1; fi
done
export ISOT_EDIT="$EDIT"
python3 -m isot config || true
python3 -m uvicorn isot.web:app --host 127.0.0.1 --port 8000 --reload &
API_PID=$!
cleanup() { kill "$API_PID" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
cd web
npm run dev -- --host 127.0.0.1 --port 5173
