#!/bin/bash
# Installs HybridCLR's patched il2cpp into the shell project (what HybridCLR/Installer does in the editor).
#   ci/hybridclr_install.sh iOS|Android
set -euo pipefail
T="$1"
echo "[hybridclr] il2cpp dirs in the Unity install:"; find /Applications/Unity -maxdepth 6 -type d -name il2cpp -not -path "*/libil2cpp/*" | tee /tmp/il2cpp_dirs
case "$T" in
  iOS) SRC=$(grep -m1 "/iOSSupport/il2cpp$" /tmp/il2cpp_dirs || true) ;;
  Android) SRC=$(grep -m1 "/AndroidPlayer/il2cpp$" /tmp/il2cpp_dirs || grep -m1 "Contents/il2cpp$" /tmp/il2cpp_dirs || grep -m1 "/il2cpp$" /tmp/il2cpp_dirs || true) ;;
esac
[ -n "$SRC" ] && [ -d "$SRC/libil2cpp" ] || { echo "[hybridclr] no il2cpp with libil2cpp found for $T"; exit 1; }
echo "[hybridclr] using $SRC"
DST="$PWD/shell/HybridCLRData/LocalIl2CppData-OSXEditor/il2cpp"
rm -rf "$DST"; mkdir -p "$(dirname "$DST")"; cp -R "$SRC" "$DST"
B="$DST/build"
if [ ! -d "$B/deploy" ]; then
  if [ "$(uname -m)" = arm64 ] && [ -d "$B/deploy_arm64" ]; then cp -R "$B/deploy_arm64" "$B/deploy"; elif [ -d "$B/deploy_x86_64" ]; then cp -R "$B/deploy_x86_64" "$B/deploy"; fi
fi
tools/hybridclr/prepare.sh "$SRC/libil2cpp" "$DST/libil2cpp"
VER=$(python3 -c "import json;print(json.load(open('shell/Packages/com.code-philosophy.hybridclr/package.json'))['version'])")
printf '%s' "$VER" > "$DST/libil2cpp/hybridclr/generated/libil2cpp-version.txt"
echo "HYBRIDCLR=1" >> "${GITHUB_ENV:-/dev/null}"
echo "[hybridclr] installed ($VER) at $DST"
