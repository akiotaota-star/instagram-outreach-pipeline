#!/bin/sh
set -eu
REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
cd "$REPO_DIR"
# Verify this user's external volume BEFORE creating task data on it.
case "$REPO_DIR" in
  /Volumes/Lexar/*)
    diskutil info -plist /Volumes/Lexar | /usr/bin/python3 -B -c 'import plistlib,sys; v=plistlib.loads(sys.stdin.buffer.read()); assert v.get("VolumeUUID")=="9137BA89-076A-3325-A6D7-6B8E7CF19086" and v.get("MountPoint")=="/Volumes/Lexar" and not v.get("Internal",True) and v.get("WritableVolume"), "Lexar ist nicht verifiziert eingebunden"'
    ;;
esac
export PYTHONDONTWRITEBYTECODE=1
export OUTREACH_DATA_DIR=${OUTREACH_DATA_DIR:-"$REPO_DIR/.local/data"}
case "$REPO_DIR" in
  /Volumes/Lexar/*)
    /usr/bin/python3 -B -c 'from pathlib import Path; import sys; p=Path(sys.argv[1]).resolve(); assert str(p).startswith("/Volumes/Lexar/"), "OUTREACH_DATA_DIR muss auf Lexar liegen"' "$OUTREACH_DATA_DIR"
    ;;
esac
export TMPDIR="$REPO_DIR/.local/tmp"
export XDG_CACHE_HOME="$REPO_DIR/.local/cache"
mkdir -p "$OUTREACH_DATA_DIR" "$TMPDIR" "$XDG_CACHE_HOME"
exec python3 -B -m outreach "$@"
