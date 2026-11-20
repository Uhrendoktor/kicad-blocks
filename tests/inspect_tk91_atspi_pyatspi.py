#!/usr/bin/python3
"""Independent AT-SPI inspection using GNOME's pyatspi bindings."""
from __future__ import annotations

import json
import sys
from typing import Protocol, TypedDict, cast

from gi.repository import GLib


class _AccessibleNode(Protocol):
    """Small PyAT-SPI accessible-node surface consumed by the tree inspector."""

    def get_name(self) -> str: ...

    def get_role_name(self) -> str: ...

    def get_description(self) -> str: ...

    def get_process_id(self) -> int: ...

    def get_child_count(self) -> int: ...

    def get_child_at_index(self, index: int) -> _AccessibleNode | None: ...


class _AccessibleSnapshot(TypedDict, total=False):
    """JSON-safe snapshot of one AT-SPI node or a child-inspection error."""

    name: str
    role: str
    description: str
    pid: int | None
    children: list["_AccessibleSnapshot"]
    error: str


def _read(node: _AccessibleNode, depth: int = 0) -> _AccessibleSnapshot:
    result: _AccessibleSnapshot = {
        "name": node.get_name(),
        "role": node.get_role_name(),
        "description": node.get_description(),
    }
    try:
        result["pid"] = node.get_process_id()
    except (GLib.Error, AttributeError, TypeError, ValueError):
        result["pid"] = None
    children: list[_AccessibleSnapshot] = []
    if depth < 12:
        try:
            count = min(int(node.get_child_count()), 100)
        except (GLib.Error, AttributeError, TypeError, ValueError):
            count = 0
        for index in range(count):
            try:
                child = node.get_child_at_index(index)
                if child is not None:
                    children.append(_read(child, depth + 1))
            except (GLib.Error, AttributeError, TypeError, ValueError) as exc:
                children.append({"error": f"{type(exc).__name__}: {exc}"})
    result["children"] = children
    return result


def main() -> int:
    import pyatspi

    target_pid = int(sys.argv[1])
    desktop = cast(_AccessibleNode, pyatspi.Registry.getDesktop(0))
    applications: list[dict[str, str | int]] = []
    selected: _AccessibleNode | None = None
    count = int(desktop.get_child_count())
    for index in range(count):
        try:
            app = desktop.get_child_at_index(index)
            if app is None:
                continue
            app_pid = int(app.get_process_id())
            applications.append({"name": app.get_name(), "pid": app_pid})
            if app_pid == target_pid:
                selected = app
        except (GLib.Error, AttributeError, TypeError, ValueError) as exc:
            applications.append(
                {"index": index, "error": f"{type(exc).__name__}: {exc}"}
            )
    if selected is None:
        raise RuntimeError(
            f"No AT-SPI application matched PID {target_pid}; "
            f"visible applications: {applications!r}"
        )
    print(json.dumps({"applications": applications, "tree": _read(selected)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
