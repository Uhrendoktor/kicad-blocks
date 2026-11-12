"""Dispatch one native KiCad IPC plugin action."""

from __future__ import annotations

import sys


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("KiCad Blocks action identifier was not supplied.")
    from kicad_blocks.plugin import run_action

    run_action(sys.argv[1])


if __name__ == "__main__":
    main()
