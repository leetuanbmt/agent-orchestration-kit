#!/usr/bin/env bash
# Run an audit reproduction file against this checkout in an isolated environment:
# fake agent CLIs first on PATH, throwaway HOME / AGENT_BRIDGE_HOME / CODEX_HOME /
# CLAUDE_CONFIG_DIR under a fresh temp dir, which is removed afterwards.
# Usage: docs/audits/evidence/run_audit_repro.sh <test file> [pytest args...]
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/home" "$work/bridge-home" "$work/codex-home" "$work/claude-config"
target="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
shift

cd "$work"
env -u AGENT_BRIDGE_AGENT \
  HOME="$work/home" AGENT_BRIDGE_HOME="$work/bridge-home" \
  CODEX_HOME="$work/codex-home" CLAUDE_CONFIG_DIR="$work/claude-config" \
  PATH="$repo/tests/fakes:$PATH" PYTHONPATH="$repo" \
  "$repo/.venv/bin/python" -m pytest "$target" -p tests.conftest --rootdir="$work" \
  -c /dev/null -o addopts="" --basetemp="$work/pt" -p no:cacheprovider -q -rA "$@"
