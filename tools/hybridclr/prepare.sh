#!/bin/bash
# Builds HybridCLR's libil2cpp for Unity 6000.6.0f1 (officially supported only up to 6000.5):
#   prepare.sh <Unity's stock il2cpp/libil2cpp dir> <output libil2cpp dir>
# = stock libil2cpp + il2cpp_plus's hybridclr changes rebased onto 6000.6 (libil2cpp-6000.6.patch)
# + the hybridclr interpreter (branch 6000.5.x at HCLR_COMMIT) with the 6000.6 API fixes (hybridclr.patch).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$1"; OUT="$2"
HCLR_COMMIT=da2cb06aa09625ad633f6ef0b85d6038d31970d1
W="$(mktemp -d)"
mkdir -p "$W/plus" && cp -R "$SRC" "$W/plus/libil2cpp"
git -C "$W/plus" init -q && git -C "$W/plus" apply --whitespace=nowarn "$HERE/libil2cpp-6000.6.patch"
git -C "$W" clone -q --filter=blob:none -b 6000.5.x https://github.com/focus-creative-games/hybridclr hclr
git -C "$W/hclr" -c advice.detachedHead=false checkout -q "$HCLR_COMMIT"
git -C "$W/hclr" apply --whitespace=nowarn "$HERE/hybridclr.patch"
rm -rf "$W/plus/libil2cpp/hybridclr" && cp -R "$W/hclr/hybridclr" "$W/plus/libil2cpp/hybridclr"
rm -rf "$OUT" && mkdir -p "$(dirname "$OUT")" && cp -R "$W/plus/libil2cpp" "$OUT"
rm -rf "$W"
echo "[hybridclr] libil2cpp ready at $OUT"
