#!/usr/bin/env bash
# Run the Tk 9.1 X11 accessibility probe with a real Orca screen-reader process.
# Tk's Unix accessibility library checks "pgrep -x orca" during Tk initialization.
set -euo pipefail

if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  echo "Tk 9.1 accessibility probe must run inside dbus-run-session." >&2
  exit 2
fi
if [[ -z "${DISPLAY:-}" ]]; then
  echo "Tk 9.1 accessibility probe must run inside the Xvfb display." >&2
  exit 2
fi
command -v orca >/dev/null
command -v pgrep >/dev/null
if [[ $# -eq 0 ]]; then
  echo "Expected the probe Python command after the Orca launcher." >&2
  exit 2
fi

orca_log="${TK91_ORCA_LOG:-${TMPDIR:-/tmp}/kicad-blocks-tk91-orca.log}"
: > "$orca_log"
orca --replace --disable=speech --disable=braille --disable=braille-monitor >"$orca_log" 2>&1 &
orca_launcher_pid=$!

cleanup() {
  # The Orca launcher may hand off to a longer-lived process. This session is
  # private to the CI job, so stop any exact-name Orca instance it started.
  if pgrep -x orca >/dev/null 2>&1; then
    pkill -x orca || true
  fi
  if kill -0 "$orca_launcher_pid" >/dev/null 2>&1; then
    kill "$orca_launcher_pid" >/dev/null 2>&1 || true
  fi
  wait "$orca_launcher_pid" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

detected=false
for _attempt in $(seq 1 100); do
  if pgrep -x orca >/dev/null 2>&1; then
    detected=true
    break
  fi
  if ! kill -0 "$orca_launcher_pid" >/dev/null 2>&1; then
    wait "$orca_launcher_pid" || true
    echo "Orca exited before Tk could detect the screen reader." >&2
    tail -n 80 "$orca_log" >&2 || true
    exit 1
  fi
  sleep 0.1
done
if [[ "$detected" != true ]]; then
  echo "Orca did not appear as an exact-name process for Tk's X11 screen-reader probe." >&2
  tail -n 80 "$orca_log" >&2 || true
  exit 1
fi

echo "Orca screen reader detected by pgrep -x orca:" >&2
pgrep -a -x orca >&2
"$@"
