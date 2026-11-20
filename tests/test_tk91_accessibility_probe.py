"""Probe real Tk 9.1 widget accessibility through the X11 AT-SPI bridge."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

WINDOW_TITLE = "KiCad Blocks Tk 9.1 Accessibility Probe"


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

    app = xa11y.App.by_pid(process_id, timeout=15.0)
    payload = {
        "snapshot": app.tree(max_depth=10),
        "dump": app.dump(max_depth=16),
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _assert_accessible_tree(payload: dict) -> None:
    snapshot = payload["snapshot"]
    dump = str(payload.get("dump") or "")
    print("--- Tk 9.1 AT-SPI tree ---", flush=True)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2)[:30000], flush=True)
    print(dump[:30000], flush=True)
    print("--- end Tk 9.1 AT-SPI tree ---", flush=True)

    nodes = list(_walk(snapshot))
    title_nodes = [node for node in nodes if node.get("name") == WINDOW_TITLE]
    button_nodes = [node for node in nodes if node.get("name") == "Save block"]
    label_nodes = [node for node in nodes if node.get("name") == "Block name"]
    if not title_nodes:
        raise AssertionError(
            f"AT-SPI tree did not expose the Tk window {WINDOW_TITLE!r}."
        )
    if not button_nodes:
        raise AssertionError(
            "AT-SPI tree did not expose the ttk.Button name 'Save block'."
        )
    if not label_nodes:
        raise AssertionError(
            "AT-SPI tree did not expose the ttk.Label name 'Block name'."
        )

    button_roles = {
        str(node.get("role") or "").casefold().replace(" ", "_")
        for node in button_nodes
    }
    if not any("button" in role for role in button_roles):
        raise AssertionError(
            f"Save block was exposed without a button role: {button_roles!r}"
        )

    summary = {
        "window": title_nodes[0].get("role"),
        "label": label_nodes[0].get("role"),
        "button_roles": sorted(button_roles),
        "tree_nodes": len(nodes),
    }
    print(
        "Tk 9.1 accessibility assertions passed:",
        json.dumps(summary, ensure_ascii=False),
        flush=True,
    )
    result_path = os.environ.get("TK91_A11Y_RESULT")
    if not result_path:
        raise RuntimeError("TK91_A11Y_RESULT must name the accessibility summary file.")
    Path(result_path).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\\n",
        encoding="utf-8",
    )


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
    ttk.Label(content, text="Block name").pack(anchor="w")
    ttk.Entry(content, name="block_name").pack(fill="x", pady=(4, 10))
    ttk.Button(content, text="Save block").pack(anchor="e")

    # Tk 9.1's ATK bridge exposes the registered top-level window and core
    # ttk widgets. Keep Tk's main loop active while another process queries
    # AT-SPI; synchronous same-thread discovery starves Tk's event callbacks.
    root.tk.call("tk", "accessible", "add_acc_object", root._w)
    root.update_idletasks()
    root.update()

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
    failures: list[BaseException] = []

    def poll_helper() -> None:
        try:
            if helper.poll() is None:
                root.after(100, poll_helper)
                return

            stdout, stderr = helper.communicate()
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
            _assert_accessible_tree(payload)
        except BaseException as exc:
            failures.append(exc)
        finally:
            if helper.poll() is None:
                helper.terminate()
            root.destroy()

    root.after(250, poll_helper)
    root.mainloop()
    if failures:
        raise failures[0]


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--inspect":
        _inspect_process(int(sys.argv[2]))
    else:
        main()
