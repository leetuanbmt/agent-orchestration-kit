#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [[ ! -x .venv/bin/python ]]; then
    echo "Missing .venv/bin/python. Run uv sync first." >&2
    exit 1
fi

with_health=false
if [[ "${1:-}" == "--with-health" ]]; then
    with_health=true
    shift
fi

.venv/bin/python -m pytest -q "$@"

if [[ "$with_health" == true ]]; then
    .venv/bin/python -m bridge health | .venv/bin/python -c 'import json, sys; sys.exit(0 if json.load(sys.stdin).get("ok") is True else 1)'
fi
