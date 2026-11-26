Greymeat - native Switch build for Switchroot L4T Ubuntu
mobile port by piece89 (Discord)

Install
  1. Copy Greymeat-L4T.tar.gz to the Switch (L4T), then in a terminal:
       tar -xzf Greymeat-L4T.tar.gz
       cd Greymeat-L4T
       ./Greymeat.sh
  2. If it says libraries are missing:
       sudo apt install libsdl2-2.0-0 libzip-dev libbsd0

Controls
  Handheld: touchscreen buttons (same as the phone version). Docked: USB/Bluetooth keyboard + mouse,
  or a controller. "-" opens the pause menu.

Files
  Saves, settings, maps, replays, screenshots and mods are in conf/ (mods: conf/Mods).
  log.txt holds the last run's log - send it if something breaks.

Runs the game's own arm64 Unity player through Bogodroid NEO (GPL-3.0, see BOGODROID-LICENSE.md):
no Android, no emulation, the Switch's own NVIDIA driver draws it.
