#!/bin/bash
# Installs HybridCLR's patched il2cpp into the shell project (what HybridCLR/Installer does in the editor).
#   ci/hybridclr_install.sh iOS|Android
set -euo pipefail
T="$1"
echo "[hybridclr] il2cpp dirs in the Unity install:"; find /Applications/Unity -maxdepth 6 -type d -name il2cpp -not -path "*/libil2cpp/*" | tee /tmp/il2cpp_dirs
echo "[hybridclr] libil2cpp dirs:"; find /Applications/Unity -type d -name libil2cpp -not -path "*/libil2cpp/*" | tee /tmp/libil2cpp_dirs
case "$T" in
  iOS) PAT="/iOSSupport/" ;;
  Android) PAT="/AndroidPlayer/" ;;
esac
LIB=$(grep -m1 "$PAT" /tmp/libil2cpp_dirs || grep -m1 "Scripting/il2cpp/libil2cpp$" /tmp/libil2cpp_dirs || head -1 /tmp/libil2cpp_dirs || true)
[ -n "$LIB" ] || { echo "[hybridclr] no libil2cpp found for $T"; for d in $(cat /tmp/il2cpp_dirs); do echo "== $d"; ls "$d"; done; exit 1; }
SRC=$(dirname "$LIB")
# the il2cpp tool dir must hold build/ (il2cpp.exe deploy); fall back to the editor's one
[ -d "$SRC/build" ] || SRC=$(grep -m1 "Scripting/il2cpp$" /tmp/il2cpp_dirs)
echo "[hybridclr] using $SRC"
DST="$PWD/shell/HybridCLRData/LocalIl2CppData-OSXEditor/il2cpp"
rm -rf "$DST"; mkdir -p "$(dirname "$DST")"; cp -R "$SRC" "$DST"
B="$DST/build"
if [ ! -d "$B/deploy" ]; then
  if [ "$(uname -m)" = arm64 ] && [ -d "$B/deploy_arm64" ]; then cp -R "$B/deploy_arm64" "$B/deploy"; elif [ -d "$B/deploy_x86_64" ]; then cp -R "$B/deploy_x86_64" "$B/deploy"; fi
fi
rm -rf "$DST/libil2cpp"
bash tools/hybridclr/prepare.sh "$LIB" "$DST/libil2cpp"
VER=$(python3 -c "import json;print(json.load(open('shell/Packages/com.code-philosophy.hybridclr/package.json'))['version'])")
printf '%s' "$VER" > "$DST/libil2cpp/hybridclr/generated/libil2cpp-version.txt"
echo "HYBRIDCLR=1" >> "${GITHUB_ENV:-/dev/null}"
echo "[hybridclr] installed ($VER) at $DST"
