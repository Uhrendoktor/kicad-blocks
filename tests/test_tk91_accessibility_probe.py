"""Probe real Tk 9.1 widget accessibility through the X11 AT-SPI bridge."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

WINDOW_TITLE = "KiCad Blocks Tk 9.1 Accessibility Probe"
EXPECTED_WIDGETS = {
    "Block name": ("label", "Identifies the functional block name input."),
    "Block name input": ("entry", "Text field for the functional block name."),
    "Save block": ("button", "Saves the current functional block."),
}


def _walk(node):
    if isinstance(node, dict):
        yield node
        children = node.get("children", [])
        if isinstance(children, list):
            for child in children:
                yield from _walk(child)
    elif isinstance(node, list):
        for child in node:
            yield from _walk(child)


def _inspect_process(process_id: int) -> None:
    """Query AT-SPI from a helper process while Tk's event loop stays live."""
    import xa11y

    print(f"Connecting xa11y to Tk probe PID {process_id}.", flush=True)
    app = xa11y.App.by_pid(process_id, timeout=15.0)
    payload = {
        "snapshot": app.tree(max_depth=10),
        "dump": app.dump(max_depth=16),
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _role_matches(node: dict, expected: str) -> bool:
    role = str(node.get("role") or "").casefold().replace(" ", "_")
    return expected in role


def _assert_accessible_tree(payload: dict) -> dict:
    snapshot = payload["snapshot"]
    nodes = list(_walk(snapshot))
    title_nodes = [node for node in nodes if node.get("name") == WINDOW_TITLE]
    if not title_nodes:
        raise AssertionError(
            f"AT-SPI did not expose the named Tk top-level window {WINDOW_TITLE!r}."
        )

    widget_summary: dict[str, dict[str, str | None]] = {}
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
        description = str(node.get("description") or "").strip()
        if description != expected_description:
            raise AssertionError(
                f"Accessible object {name!r} had description {description!r}; "
                f"expected {expected_description!r}."
            )
        widget_summary[name] = {
            "role": str(node.get("role") or ""),
            "description": description,
        }

    result = {
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
    print("--- end Tk 9.1 AT-SPI accessibility tree ---", flush=True)
    print(
        "Tk 9.1 accessibility assertions passed:",
        json.dumps(result, ensure_ascii=False),
        flush=True,
    )
    return result


def main() -> None:
    # Import only inside the probe: standard CI Python interpreters need not
    # ship tkinter, while this file is still collected during the full suite.
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

    # Tk 9.1's default accessible name is the widget role. Visible -text
    # content is generally a description, so assign explicit useful names,
    # roles and descriptions before registering each real widget with AT-SPI.
    root.tk.call(
        "tk", "accessible", "set_acc_name", root._w, WINDOW_TITLE
    )
    root.tk.call(
        "tk", "accessible", "set_acc_description",
        root._w, "Tk 9.1 screen-reader accessibility feasibility window."
    )
    root.tk.call("tk", "accessible", "add_acc_object", root._w)

    for widget, role, name, description in (
        (label, "Label", "Block name", EXPECTED_WIDGETS["Block name"][1]),
        (entry, "Entry", "Block name input", EXPECTED_WIDGETS["Block name input"][1]),
        (save_button, "Button", "Save block", EXPECTED_WIDGETS["Save block"][1]),
    ):
        root.tk.call("tk", "accessible", "set_acc_role", widget._w, role)
        root.tk.call("tk", "accessible", "set_acc_name", widget._w, name)
        root.tk.call("tk", "accessible", "set_acc_description", widget._w, description)
        root.tk.call("tk", "accessible", "add_acc_object", widget._w)

    root.update_idletasks()
    root.update()
    print(
        "Tk 9.1 probe started:",
        json.dumps(
            {
                "pid": os.getpid(),
                "tcl": tk.TclVersion,
                "tk": tk.TkVersion,
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
        try:
            if helper.poll() is None and time.monotonic() < helper_deadline:
                root.after(100, poll_helper)
                return
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
            if helper.returncode != 0:
                raise RuntimeError(
                    "AT-SPI helper failed: "
                    f"exit={helper.returncode}; stdout={stdout[-12000:]}; "
                    f"stderr={stderr[-12000:]}"
                )
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "AT-SPI helper returned invalid JSON: "
                    f"stdout={stdout[-12000:]}; stderr={stderr[-12000:]}"
                ) from exc
            summary = _assert_accessible_tree(payload)

            result_path = os.environ.get("TK91_A11Y_RESULT")
            if not result_path:
                raise RuntimeError(
                    "TK91_A11Y_RESULT must name the accessibility summary file."
                )
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
        finally:
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
