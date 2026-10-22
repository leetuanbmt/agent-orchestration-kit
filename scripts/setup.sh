#!/usr/bin/env bash
# Same as `agent-bridge init <target_dir> [--force]`, without installing the tool first.
set -euo pipefail
KIT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHONPATH="$KIT_DIR" exec python3 -m bridge.install setup "$@"
