"""Real Tk wrapper contracts for native file and directory chooser hand-offs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))

from kicad_blocks import plugin as plugin_actions
from ui_contracts import Misc, MonkeyPatch, TkOption

pytestmark = pytest.mark.skipif(
    sys.platform == "linux" and not os.environ.get("DISPLAY"),
    reason="Linux Tk file-dialog wrapper contracts require an X display (run under xvfb-run).",
)


class _ParentHandle(Misc):
    """Identity token passed through to the stubbed native chooser callback."""

    def destroy(self) -> None:
        """Keep the test cleanup interface without creating a Tcl interpreter."""


def _parent() -> _ParentHandle:
    # The workflow separately creates one real Tk root to verify the platform
    # runtime. These tests stub the chooser callback and only verify that the
    # wrapper forwards the caller's parent handle and correct file arguments.
    plugin_actions.load_tkinter()
    return _ParentHandle()


def test_open_kicad_file_uses_existing_file_parent_name_filter_and_parent(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    source_dir = tmp_path / "schematics"
    source_dir.mkdir()
    initial = source_dir / "implementation.kicad_sch"
    initial.write_text("(kicad_sch)\n", encoding="utf-8")
    selected = source_dir / "selected.kicad_sch"
    parent = _parent()
    calls: list[dict[str, TkOption]] = []

    def choose(**kwargs: TkOption) -> str:
        calls.append(kwargs)
        return str(selected)

    monkeypatch.setattr(plugin_actions.filedialog, "askopenfilename", choose)
    try:
        result = plugin_actions.pick_kicad_file(
            parent,
            title="Choose implementation schematic",
            suffix=".kicad_sch",
            initial_path=initial,
        )
        assert result == str(selected)
        assert calls == [
            {
                "title": "Choose implementation schematic",
                "initialdir": str(source_dir),
                "initialfile": "implementation.kicad_sch",
                "filetypes": [
                    ("KiCad KICAD_SCH files", "*.kicad_sch"),
                    ("All files", "*.*"),
                ],
                "parent": parent,
            }
        ]
    finally:
        parent.destroy()


def test_open_kicad_file_falls_back_to_cwd_for_missing_initial_path(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    parent = _parent()
    calls = []
    selected = tmp_path / "board.kicad_pcb"
    monkeypatch.setattr(
        plugin_actions.filedialog,
        "askopenfilename",
        lambda **kwargs: calls.append(kwargs) or str(selected),
    )
    try:
        result = plugin_actions.pick_kicad_file(
            parent,
            title="Choose PCB layout",
            suffix=".kicad_pcb",
            initial_path=tmp_path / "deleted-folder" / "missing.kicad_pcb",
        )
        assert result == str(selected)
        assert calls[0]["initialdir"] == str(tmp_path)
        assert calls[0]["initialfile"] == ""
        assert calls[0]["filetypes"] == [
            ("KiCad KICAD_PCB files", "*.kicad_pcb"),
            ("All files", "*.*"),
        ]
        assert calls[0]["parent"] is parent
    finally:
        parent.destroy()


def test_open_kicad_file_cancel_returns_none(monkeypatch: MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    parent = _parent()
    calls = []
    monkeypatch.setattr(
        plugin_actions.filedialog,
        "askopenfilename",
        lambda **kwargs: calls.append(kwargs) or "",
    )
    try:
        assert (
            plugin_actions.pick_kicad_file(
                parent,
                title="Choose schematic",
                suffix=".kicad_sch",
            )
            is None
        )
        assert calls[0]["initialdir"] == str(tmp_path)
        assert calls[0]["initialfile"] == ""
        assert calls[0]["parent"] is parent
    finally:
        parent.destroy()


def test_save_file_uses_suggested_name_extension_filter_and_parent(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    suggested = tmp_path / "release" / "functional_block.kicad_sch"
    suggested.parent.mkdir(parents=True, exist_ok=True)
    parent = _parent()
    calls = []
    selected = tmp_path / "release" / "renamed.kicad_sch"

    def save(**kwargs: TkOption) -> str:
        calls.append(kwargs)
        return str(selected)

    monkeypatch.setattr(plugin_actions.filedialog, "asksaveasfilename", save)
    filetypes = [
        ("KiCad schematic files", "*.kicad_sch"),
        ("All files", "*.*"),
    ]
    try:
        result = plugin_actions.pick_save_file(
            parent,
            title="Save implementation copy",
            initial_path=suggested,
            defaultextension=".kicad_sch",
            filetypes=filetypes,
        )
        assert result == str(selected)
        assert calls == [
            {
                "title": "Save implementation copy",
                "initialdir": str(suggested.parent),
                "initialfile": suggested.name,
                "defaultextension": ".kicad_sch",
                "filetypes": filetypes,
                "parent": parent,
            }
        ]
    finally:
        parent.destroy()


def test_save_file_without_suffix_uses_directory_and_cancel_returns_none(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    parent = _parent()
    calls = []
    monkeypatch.setattr(
        plugin_actions.filedialog,
        "asksaveasfilename",
        lambda **kwargs: calls.append(kwargs) or "",
    )
    try:
        assert (
            plugin_actions.pick_save_file(
                parent,
                title="Save report",
                initial_path=tmp_path,
                defaultextension=".json",
                filetypes=[("JSON", "*.json")],
            )
            is None
        )
        assert calls[0]["initialdir"] == str(tmp_path)
        assert calls[0]["initialfile"] == ""
        assert calls[0]["defaultextension"] == ".json"
        assert calls[0]["parent"] is parent
    finally:
        parent.destroy()


def test_directory_picker_uses_existing_file_parent_and_requires_existing_directory(
    monkeypatch, tmp_path
):
    initial_dir = tmp_path / "libraries"
    initial_dir.mkdir()
    initial_file = initial_dir / "example.kicad_sch"
    initial_file.write_text("(kicad_sch)\n", encoding="utf-8")
    selected = tmp_path / "selected-library"
    parent = _parent()
    calls = []
    monkeypatch.setattr(
        plugin_actions.filedialog,
        "askdirectory",
        lambda **kwargs: calls.append(kwargs) or str(selected),
    )
    try:
        result = plugin_actions.pick_directory(
            parent,
            title="Add functional-block library",
            initial_path=initial_file,
        )
        assert result == str(selected)
        assert calls == [
            {
                "title": "Add functional-block library",
                "initialdir": str(initial_dir),
                "mustexist": True,
                "parent": parent,
            }
        ]
    finally:
        parent.destroy()


def test_directory_picker_falls_back_to_cwd_and_cancel_returns_none(
    monkeypatch: MonkeyPatch, tmp_path: Path
):
    monkeypatch.chdir(tmp_path)
    parent = _parent()
    calls = []
    monkeypatch.setattr(
        plugin_actions.filedialog,
        "askdirectory",
        lambda **kwargs: calls.append(kwargs) or "",
    )
    try:
        assert (
            plugin_actions.pick_directory(
                parent,
                title="Choose library location",
                initial_path=tmp_path / "missing",
            )
            is None
        )
        assert calls[0]["initialdir"] == str(tmp_path)
        assert calls[0]["mustexist"] is True
        assert calls[0]["parent"] is parent
    finally:
        parent.destroy()
