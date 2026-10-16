#!/usr/bin/env bash
# Un solo comando: construye el frontend y sirve API + UI en :8000
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 -m pip install -e ".[dev]" -q
if [ ! -d web/node_modules ]; then
  (cd web && npm install)
fi
(cd web && npm run build)
exec python3 -m uvicorn isot.web:app --host 0.0.0.0 --port 8000
