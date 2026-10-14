#!/bin/sh
# Linux/macOS equivalent of build_sandbox_image.ps1
set -eu
DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
docker build -t tire-scout-sandbox:py312 -f "$DIR/Dockerfile.sandbox" "$DIR"
docker image inspect tire-scout-sandbox:py312 >/dev/null
echo "Sandbox image tire-scout-sandbox:py312 ready."
