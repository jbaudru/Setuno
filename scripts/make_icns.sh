#!/usr/bin/env bash
# Build assets/icon.icns from assets/icon.png. macOS only (uses sips + iconutil).
# Run before packaging the .app bundle: bash scripts/make_icns.sh
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="assets/icon.png"
ICONSET="assets/icon.iconset"

rm -rf "$ICONSET"
mkdir -p "$ICONSET"

for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$SRC" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" "$SRC" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done

iconutil -c icns "$ICONSET" -o assets/icon.icns
rm -rf "$ICONSET"
echo "Wrote assets/icon.icns"
