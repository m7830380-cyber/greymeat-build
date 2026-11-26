#!/bin/bash
# Greymeat for Switchroot L4T Ubuntu (native arm64). Run from a terminal or the file manager.
cd "$(dirname "$(readlink -f "$0")")"
mkdir -p conf cache support_files
# Unity reads the CPU layout through these; without them it sees a single core
cat /sys/devices/system/cpu/present  > support_files/cpu_present.txt  2>/dev/null
cat /sys/devices/system/cpu/possible > support_files/cpu_possible.txt 2>/dev/null
# use the bundled SDL2/libzip/libbsd only if the system doesn't have them
if ldd ./unityloader | grep -q "not found"; then
  export LD_LIBRARY_PATH="$PWD/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  ldd ./unityloader | grep "not found" && echo "missing libraries above: sudo apt install libsdl2-2.0-0 libzip-dev libbsd0"
fi
export SDL_VIDEO_X11_FORCE_EGL=1
exec > >(tee log.txt) 2>&1
exec ./unityloader game.toml
