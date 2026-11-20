"""Probe real Tk 9.1 widget accessibility through the X11 AT-SPI bridge."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TypedDict, cast

WINDOW_TITLE = "KiCad Blocks Tk 9.1 Accessibility Probe"
EXPECTED_WIDGETS: dict[str, tuple[str, str]] = {
    "Block name": ("static_text", "Identifies the functional block name input."),
    "Block name input": ("text_field", "Text field for the functional block name."),
    "Save block": ("button", "Saves the current functional block."),
}


class _AccessibleNode(TypedDict, total=False):
    """Normalized JSON-safe AT-SPI node from either accessibility adapter."""

    name: str | None
    role: str | None
    value: str | None
    description: str | None
    children: list["_AccessibleNode"]


class _PyAtSpiPayload(TypedDict):
    tree: _AccessibleNode


class _InspectorPayload(TypedDict, total=False):
    snapshot: _AccessibleNode
    dump: str
    pyatspi: _PyAtSpiPayload


class _WidgetSummary(TypedDict):
    role: str
    native_role: str
    description: str


class _WindowSummary(TypedDict):
    name: str | None
    role: str | None


class _AccessibilitySummary(TypedDict):
    window: _WindowSummary
    widgets: dict[str, _WidgetSummary]
    tree_nodes: int


class _TkAttributes(TypedDict):
    role: str
    name: str
    description: str


class _RegisteredAttributes(TypedDict):
    window: _TkAttributes
    widgets: dict[str, _TkAttributes]


def _walk(node: _AccessibleNode | list[_AccessibleNode]) -> Iterator[_AccessibleNode]:
    if isinstance(node, list):
        for child in node:
            yield from _walk(child)
        return
    yield node
    for child in node.get("children", []):
        yield from _walk(child)


def _inspect_process(process_id: int) -> None:
    """Query AT-SPI from a helper process while Tk's event loop stays live."""
    import xa11y

    print(f"Connecting xa11y to Tk probe PID {process_id}.", file=sys.stderr, flush=True)
    app = xa11y.App.by_pid(process_id, timeout=15.0)
    payload: _InspectorPayload = {
        "snapshot": app.tree(max_depth=10),
        "dump": app.dump(max_depth=16),
    }
    pyatspi_script = Path(__file__).with_name("inspect_tk91_atspi_pyatspi.py")
    direct = subprocess.run(
        ["/usr/bin/python3", str(pyatspi_script), str(process_id)],
        capture_output=True,
        text=True,
        timeout=15.0,
        check=True,
    )
    payload["pyatspi"] = cast(_PyAtSpiPayload, json.loads(direct.stdout))
    print(
        "GNOME pyatspi cross-check:",
        direct.stdout.strip(),
        file=sys.stderr,
        flush=True,
    )
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _role_matches(node: _AccessibleNode, expected: str) -> bool:
    role = str(node.get("role") or "").casefold().replace(" ", "_")
    return expected in role


def _assert_accessible_tree(payload: _InspectorPayload) -> _AccessibilitySummary:
    snapshot = payload.get("snapshot")
    if snapshot is None:
        raise AssertionError("AT-SPI inspector omitted its normalized snapshot.")
    nodes = list(_walk(snapshot))
    title_nodes = [node for node in nodes if node.get("name") == WINDOW_TITLE]
    if not title_nodes:
        observed: list[dict[str, str | None]] = [
            {
                "name": node.get("name"),
                "role": node.get("role"),
                "description": node.get("description"),
            }
            for node in nodes
        ]
        raise AssertionError(
            f"AT-SPI did not expose the named Tk top-level window {WINDOW_TITLE!r}. "
            f"Snapshot: {json.dumps(snapshot, ensure_ascii=False)[:16000]}. "
            f"Observed accessible nodes: {json.dumps(observed, ensure_ascii=False)[:16000]}. "
            f"Raw AT-SPI dump: {str(payload.get('dump') or '')[:16000]}"
        )

    widget_summary: dict[str, _WidgetSummary] = {}
    for name, (expected_role, expected_description) in EXPECTED_WIDGETS.items():
        matching = [node for node in nodes if node.get("name") == name]
        if not matching:
            raise AssertionError(
                f"AT-SPI tree did not expose accessible name {name!r}. "
                f"Observed names: {[node.get('name') for node in nodes if node.get('name')]!r}"
            )

        node = matching[0]
        if not _role_matches(node, expected_role):
            raise AssertionError(
                f"Accessible object {name!r} had role {node.get('role')!r}; "
                f"expected a role containing {expected_role!r}."
            )
        native_payload = payload.get("pyatspi")
        if native_payload is None:
            raise AssertionError("Independent PyAT-SPI payload is missing.")
        pyatspi_tree = native_payload["tree"]
        pyatspi_nodes = list(_walk(pyatspi_tree))
        native_matches = [
            item
            for item in pyatspi_nodes
            if item.get("name") == name and item.get("description") is not None
        ]
        if not native_matches:
            raise AssertionError(
                f"PyAT-SPI did not expose a native accessible node named {name!r}. "
                f"PyAT-SPI tree: {json.dumps(pyatspi_tree, ensure_ascii=False)[:16000]}"
            )
        native_node = native_matches[0]
        description = str(native_node.get("description") or "").strip()
        if description != expected_description:
            raise AssertionError(
                f"Native AT-SPI description for {name!r} was {description!r}; "
                f"expected {expected_description!r}. "
                f"Native node: {json.dumps(native_node, ensure_ascii=False)}"
            )
        widget_summary[name] = {
            "role": str(node.get("role") or ""),
            "native_role": str(native_node.get("role") or ""),
            "description": description,
        }

    result: _AccessibilitySummary = {
        "window": {
            "name": title_nodes[0].get("name"),
            "role": title_nodes[0].get("role"),
        },
        "widgets": widget_summary,
        "tree_nodes": len(nodes),
    }
    print("--- Tk 9.1 AT-SPI accessibility tree ---", flush=True)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2)[:24000], flush=True)
    print(str(payload.get("dump") or "")[:24000], flush=True)
    print(
        "Tk 9.1 accessibility assertions passed:",
        json.dumps(result, ensure_ascii=False),
        flush=True,
    )
    return result


def main() -> None:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.geometry("420x180+20+20")

    content = ttk.Frame(root, padding=12)
    content.pack(fill="both", expand=True)

    label = ttk.Label(content, text="Block name")
    label.pack(anchor="w")
    entry = ttk.Entry(content, name="block_name")
    entry.pack(fill="x", pady=(4, 10))
    save_button = ttk.Button(content, text="Save block")
    save_button.pack(anchor="e")

    root.update_idletasks()
    root.update()

    screen_reader_running = str(root.tk.call("::tk::accessible::check_screenreader"))
    if screen_reader_running not in {"1", "true"}:
        raise AssertionError(
            "Tk 9.1 did not detect the running Orca screen reader before widget "
            f"registration (check_screenreader returned {screen_reader_running!r})."
        )

    accessible_widgets = (
        (root, "Toplevel"),
        (label, "Label"),
        (entry, "Entry"),
        (save_button, "Button"),
    )
    for widget, role in accessible_widgets:
        root.tk.call("tk", "accessible", "set_acc_role", widget._w, role)

    root.tk.call("tk", "accessible", "set_acc_name", root._w, WINDOW_TITLE)
    root.tk.call(
        "tk",
        "accessible",
        "set_acc_description",
        root._w,
        "Tk 9.1 screen-reader accessibility feasibility window.",
    )

    for widget, name, description in (
        (label, "Block name", EXPECTED_WIDGETS["Block name"][1]),
        (entry, "Block name input", EXPECTED_WIDGETS["Block name input"][1]),
        (save_button, "Save block", EXPECTED_WIDGETS["Save block"][1]),
    ):
        root.tk.call("tk", "accessible", "set_acc_name", widget._w, name)
        root.tk.call("tk", "accessible", "set_acc_description", widget._w, description)

    root.tk.call("tk", "accessible", "set_acc_value", label._w, "Block name")
    root.tk.call("tk", "accessible", "add_acc_object", root._w)
    root.update_idletasks()
    root.update()
    registered: _RegisteredAttributes = {
        "window": {
            "role": str(root.tk.call("tk", "accessible", "get_acc_role", root._w)),
            "name": str(root.tk.call("tk", "accessible", "get_acc_name", root._w)),
            "description": str(root.tk.call("tk", "accessible", "get_acc_description", root._w)),
        },
        "widgets": {
            name: {
                "role": str(widget.tk.call("tk", "accessible", "get_acc_role", widget._w)),
                "name": str(widget.tk.call("tk", "accessible", "get_acc_name", widget._w)),
                "description": str(
                    widget.tk.call("tk", "accessible", "get_acc_description", widget._w)
                ),
            }
            for widget, name in (
                (label, "Block name"),
                (entry, "Block name input"),
                (save_button, "Save block"),
            )
        },
    }
    for label_name, attrs in registered["widgets"].items():
        expected = EXPECTED_WIDGETS[label_name]
        if attrs["name"] != label_name:
            raise AssertionError(f"Tk accessibility name for {label_name!r} is {attrs['name']!r}.")
        if attrs["description"] != expected[1]:
            raise AssertionError(
                f"Tk accessibility description for {label_name!r} is {attrs['description']!r}."
            )
    if registered["window"]["name"] != WINDOW_TITLE:
        raise AssertionError(f"Tk accessibility window name is {registered['window']['name']!r}.")
    print("Tk 9.1 registered accessibility attributes:", json.dumps(registered), flush=True)
    print(
        "Tk 9.1 probe started:",
        json.dumps(
            {
                "pid": os.getpid(),
                "tcl": tk.TclVersion,
                "tk": tk.TkVersion,
                "screen_reader_running": screen_reader_running,
                "window": WINDOW_TITLE,
                "widgets": list(EXPECTED_WIDGETS),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    helper = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--inspect",
            str(os.getpid()),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    failures: list[Exception] = []
    helper_deadline = time.monotonic() + 25.0
    helper_completed = False

    def poll_helper() -> None:
        nonlocal helper_completed
        if helper.poll() is None and time.monotonic() < helper_deadline:
            root.after(100, poll_helper)
            return

        try:
            if helper.poll() is None:
                helper.terminate()
                try:
                    stdout, stderr = helper.communicate(timeout=2.0)
                except subprocess.TimeoutExpired:
                    helper.kill()
                    stdout, stderr = helper.communicate()
                raise RuntimeError(
                    "Timed out waiting for xa11y to inspect the Tk 9.1 window; "
                    f"stdout={stdout[-12000:]}; stderr={stderr[-12000:]}"
                )

            stdout, stderr = helper.communicate()
            print(
                f"Tk 9.1 AT-SPI inspector exited {helper.returncode}; "
                f"stdout_bytes={len(stdout.encode('utf-8'))}; "
                f"stderr={stderr[-4000:]!r}",
                flush=True,
            )
            print("Tk 9.1 raw xa11y payload:", stdout[-16000:], flush=True)
            if helper.returncode != 0:
                raise RuntimeError(
                    "AT-SPI helper failed: "
                    f"exit={helper.returncode}; stdout={stdout[-12000:]}; "
                    f"stderr={stderr[-12000:]}"
                )
            payload = cast(_InspectorPayload, json.loads(stdout))
            summary = _assert_accessible_tree(payload)

            result_path = os.environ.get("TK91_A11Y_RESULT")
            if not result_path:
                raise RuntimeError("TK91_A11Y_RESULT must name the accessibility summary file.")
            Path(result_path).write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"Validated Tk 9.1 accessibility summary written to {result_path}.", flush=True)
        except (
            AssertionError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            subprocess.SubprocessError,
        ) as exc:
            failures.append(exc)

        helper_completed = True
        if helper.poll() is None:
            helper.terminate()
        root.destroy()

    print("Entering Tk 9.1 mainloop for AT-SPI inspection.", flush=True)
    root.after(250, poll_helper)
    root.mainloop()
    print(
        "Tk 9.1 mainloop exited; "
        f"helper_completed={helper_completed}; helper_returncode={helper.poll()}.",
        flush=True,
    )
    if not helper_completed:
        if helper.poll() is None:
            helper.terminate()
        raise RuntimeError(
            "Tk 9.1 mainloop exited before the AT-SPI inspection callback completed."
        )
    if failures:
        raise failures[0]


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--inspect":
        _inspect_process(int(sys.argv[2]))
    else:
        main()
