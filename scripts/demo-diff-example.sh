#!/usr/bin/env bash
# Crea la rama demo/diff-example en un repo de demo (creado con `isot demo`).
# Uso: scripts/demo-diff-example.sh DIR_DEMO
set -euo pipefail
exec python3 -m isot demo-diff "$@"
