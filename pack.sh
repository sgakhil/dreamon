#!/usr/bin/env bash
# Build the zip that extensions.gnome.org accepts:
#   dreamon@sgakhil.github.io.shell-extension.zip
set -euo pipefail
cd "$(dirname "$0")"
gnome-extensions pack --force \
    --extra-source=icons \
    --extra-source=LICENSE \
    .
echo "Built: $(ls ./*.shell-extension.zip)"
echo "Install it locally with: gnome-extensions install --force ./*.shell-extension.zip"
