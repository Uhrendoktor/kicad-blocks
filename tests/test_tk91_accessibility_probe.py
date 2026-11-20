"""Probe real Tk 9.1 widget accessibility through the X11 AT-SPI bridge."""

from __future__ import annotations

import json
import os
import time
import tkinter as tk
from tkinter import ttk

import xa11y


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


def main() -> None:
    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.geometry("420x180+20+20")

    content = ttk.Frame(root, padding=12)
    content.pack(fill="both", expand=True)
    ttk.Label(content, text="Block name").pack(anchor="w")
    ttk.Entry(content, name="block_name").pack(fill="x", pady=(4, 10))
    ttk.Button(content, text="Save block").pack(anchor="e")

    # Tk 9.1's ATK/MSAA/NSAccessibility bridge exposes the top-level window
    # when it is registered as an accessible object.
    root.tk.call("tk", "accessible", "add_acc_object", root._w)
    root.update_idletasks()
    root.update()
    time.sleep(0.5)

    app = xa11y.App.by_pid(os.getpid(), timeout=15.0)
    snapshot = app.tree(max_depth=10)
    dump = app.dump(max_depth=16)
    print("--- Tk 9.1 AT-SPI tree ---", flush=True)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2)[:30000], flush=True)
    print(dump[:30000], flush=True)
    print("--- end Tk 9.1 AT-SPI tree ---", flush=True)

    nodes = list(_walk(snapshot))
    title_nodes = [node for node in nodes if node.get("name") == WINDOW_TITLE]
    button_nodes = [node for node in nodes if node.get("name") == "Save block"]
    label_nodes = [node for node in nodes if node.get("name") == "Block name"]
    if not title_nodes:
        raise AssertionError(f"AT-SPI tree did not expose the Tk window {WINDOW_TITLE!r}.")
    if not button_nodes:
        raise AssertionError("AT-SPI tree did not expose the ttk.Button name 'Save block'.")
    if not label_nodes:
        raise AssertionError("AT-SPI tree did not expose the ttk.Label name 'Block name'.")

    button_roles = {
        str(node.get("role") or "").casefold().replace(" ", "_")
        for node in button_nodes
    }
    if not any("button" in role for role in button_roles):
        raise AssertionError(f"Save block was exposed without a button role: {button_roles!r}")

    print(
        "Tk 9.1 accessibility assertions passed:",
        json.dumps(
            {
                "window": title_nodes[0].get("role"),
                "label": label_nodes[0].get("role"),
                "button_roles": sorted(button_roles),
                "tree_nodes": len(nodes),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    root.destroy()


if __name__ == "__main__":
    main()
