#!/usr/bin/env bash
# Same as `agent-bridge uninstall <target_dir> [--purge]`.
set -euo pipefail
KIT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
PYTHONPATH="$KIT_DIR" exec python3 -m bridge.install uninstall "$@"
