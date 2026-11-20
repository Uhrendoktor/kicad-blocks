#!/usr/bin/env bash
# Prepare a private X11/D-Bus session for the Linux KiCad accessibility smoke test.
set -euo pipefail

if [[ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ]]; then
  echo "AT-SPI smoke tests must run inside dbus-run-session." >&2
  exit 2
fi
command -v dbus-send >/dev/null
if [[ $# -eq 0 ]]; then
  echo "Expected a GUI test command after accessibility setup." >&2
  exit 2
fi

# Calling GetAddress activates at-spi-bus-launcher on the session bus.
a11y_address="$(dbus-send --session --print-reply --dest=org.a11y.Bus \
  /org/a11y/bus org.a11y.Bus.GetAddress)"
if ! grep -Eq 'string "unix:' <<<"$a11y_address"; then
  echo "AT-SPI bus launcher did not return a Unix socket address:" >&2
  printf '%s\n' "$a11y_address" >&2
  exit 1
fi

dbus-send --session --print-reply --dest=org.a11y.Bus /org/a11y/bus \
  org.freedesktop.DBus.Properties.Set \
  string:org.a11y.Status string:IsEnabled variant:boolean:true >/dev/null
a11y_status="$(dbus-send --session --print-reply --dest=org.a11y.Bus \
  /org/a11y/bus org.freedesktop.DBus.Properties.Get \
  string:org.a11y.Status string:IsEnabled)"
if ! grep -Eq 'boolean[[:space:]]+true' <<<"$a11y_status"; then
  echo "AT-SPI session status did not become enabled:" >&2
  printf '%s\n' "$a11y_status" >&2
  exit 1
fi

# GTK's ATK bridge must be loaded when Eeschema starts, not enabled afterward.
case ":${GTK_MODULES:-}:" in
  *:atk-bridge:*) ;;
  *)
    if [[ -n "${GTK_MODULES:-}" ]]; then
      export GTK_MODULES="${GTK_MODULES}:atk-bridge"
    else
      export GTK_MODULES="atk-bridge"
    fi
    ;;
esac
unset NO_AT_BRIDGE || true
export KICAD_BLOCKS_REQUIRE_A11Y=1

exec "$@"
