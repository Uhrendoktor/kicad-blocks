#!/usr/bin/python3
"""Independent AT-SPI inspection using GNOME's pyatspi bindings."""
from __future__ import annotations

import json
import sys


def _read(node: object, depth: int = 0) -> dict:
    result = {
        "name": node.get_name(),
        "role": node.get_role_name(),
        "description": node.get_description(),
    }
    try:
        result["pid"] = node.get_process_id()
    except Exception:
        result["pid"] = None
    children = []
    if depth < 12:
        try:
            count = min(int(node.get_child_count()), 100)
        except Exception:
            count = 0
        for index in range(count):
            try:
                child = node.get_child_at_index(index)
                if child is not None:
                    children.append(_read(child, depth + 1))
            except Exception as exc:
                children.append({"error": f"{type(exc).__name__}: {exc}"})
    result["children"] = children
    return result


def main() -> int:
    import pyatspi

    target_pid = int(sys.argv[1])
    desktop = pyatspi.Registry.getDesktop(0)
    applications = []
    selected = None
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
        except Exception:
            continue
    if selected is None:
        raise RuntimeError(
            f"No AT-SPI application matched PID {target_pid}; "
            f"visible applications: {applications!r}"
        )
    print(json.dumps({"applications": applications, "tree": _read(selected)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
