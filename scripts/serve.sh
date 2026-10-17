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
EDIT="${ISOT_EDIT:-0}"
for arg in "$@"; do
  if [ "$arg" = "--edit" ]; then EDIT=1; fi
done
export ISOT_EDIT="$EDIT"
exec python3 -m uvicorn isot.web:app --host 0.0.0.0 --port 8000
