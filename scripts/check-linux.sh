#!/usr/bin/env bash
# Run the ubuntu CI jobs locally in Docker: the committed HEAD, as a non-root user, with Landlock
# and bwrap available, end-to-end tests (tests/qa, dashboard in Chromium) included. Run it (and
# scripts/check.sh) before every push; hosted CI is not the first Linux run. Extra arguments go
# to pytest. Needs network for the image, apt, npm and uv.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
command -v docker >/dev/null || { echo "docker is not installed" >&2; exit 1; }
# The Codex CLI the permission-profile probes were run with (tests/integration/test_secret_hygiene.py).
codex_version=${CODEX_VERSION:-0.169.0}
playwright_version=1.63.0  # .github/workflows/ci.yml, e2e job
node_version=22.12.0        # CI uses Node 22; bookworm's Node 18 is too old for Playwright
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git bundle create -q "$work/kit.bundle" HEAD
echo "$*" > "$work/pytest-args"  # plain pytest arguments (paths, -k NAME); no quoting

cat > "$work/run.sh" <<EOF
set -e
apt-get update -qq >/dev/null
apt-get install -y -qq git bubblewrap xz-utils >/dev/null
arch=\$(dpkg --print-architecture); [ "\$arch" = amd64 ] && arch=x64
curl -fsSL https://nodejs.org/dist/v${node_version}/node-v${node_version}-linux-\$arch.tar.xz | tar -xJ -C /usr/local --strip-components=1
npm install -g -s @openai/codex@${codex_version} >/dev/null 2>&1 || echo "codex not installed: its probe tests skip"
npm install -g -s playwright@${playwright_version} >/dev/null
npx --yes playwright@${playwright_version} install-deps chromium >/dev/null
pip install -q --root-user-action=ignore uv
useradd -m u
su u -c 'set -e
git config --global user.email t@t; git config --global user.name t
git clone -q /in/kit.bundle ~/kit && cd ~/kit
uv sync -q --frozen
npx --yes playwright@${playwright_version} install chromium >/dev/null
export NODE_PATH="\$(npm root -g)" QA_E2E=1
uv run --frozen pytest -q -p no:cacheprovider --basetemp=/tmp/pt \$(cat /in/pytest-args)'
EOF

# seccomp/apparmor unconfined: bwrap (Codex and Claude sandboxes) needs user namespaces, which
# Docker's default profile refuses; a Linux VM allows them.
docker run --rm --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
    -v "$work:/in:ro" python:3.12-bookworm bash /in/run.sh
