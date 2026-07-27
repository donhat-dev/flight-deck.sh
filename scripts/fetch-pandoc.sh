#!/usr/bin/env bash
# Pinned static pandoc into ~/.flightdeck/bin (no root needed).
set -euo pipefail
VERSION=3.10.1
DEST="${HOME}/.flightdeck/bin"
mkdir -p "$DEST" "${TMPDIR:-/tmp}/pandoc-dl"
cd "${TMPDIR:-/tmp}/pandoc-dl"
URL="https://github.com/jgm/pandoc/releases/download/${VERSION}/pandoc-${VERSION}-linux-amd64.tar.gz"
curl -sSL "$URL" -o pandoc.tar.gz
tar xzf pandoc.tar.gz
install -m 0755 "pandoc-${VERSION}/bin/pandoc" "$DEST/pandoc"
"$DEST/pandoc" --version | head -1
