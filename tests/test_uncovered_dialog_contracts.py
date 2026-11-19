"""Direct behavior contracts for previously uncovered native dialog call sites."""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TypedDict, cast

import pytest
from _pytest.monkeypatch import MonkeyPatch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin" / "kicad-blocks"))

from kicad_blocks import plugin as plugin_actions
from kicad_blocks.pcb_types import (
    BatchBlockLayoutMergeResult,
    BatchBlockLayoutPlacementSource,
    BlockLayoutMergeResult,
    BlockLayoutPlacementSource,
)
from kicad_blocks.repository import BlockRecord
from ui_contracts import Misc


type _RecordedValue = (
    str | int | float | bool | None | Path | Misc | SimpleNamespace | dict[str, str] | tuple[float, float]
)
type _RecordedKwargs = dict[str, _RecordedValue]


class _SingleLayoutCalls(TypedDict):
    prompts: list[tuple[str, str, _RecordedKwargs]]
    merge: list[tuple[Path, BlockRecord, Path, _RecordedKwargs]]
    sidecar: list[tuple[Path, BlockLayoutPlacementSource]]
    open: list[Path]
    confirm: list[tuple[str, str, _RecordedKwargs]]
    board_picker: list[_RecordedKwargs]
    save_picker: list[_RecordedKwargs]


class _BatchLayoutCalls(TypedDict):
    prompts: list[tuple[str, str, _RecordedKwargs]]
    merge: list[tuple[Path, list[plugin_actions.BlockLayoutPlacement], Path]]
    sidecar: list[tuple[Path, BatchBlockLayoutPlacementSource]]
    open: list[Path]
    confirm: list[tuple[str, str, _RecordedKwargs]]
    board_picker: list[_RecordedKwargs]
    save_picker: list[_RecordedKwargs]


# Native dialog call-site contracts patch the functions and do not need Tk installed.
def _unpatched_native_dialog(
    *_args: _RecordedValue, **_kwargs: _RecordedValue
) -> None:
    raise AssertionError("A native dialog call was not patched by this test.")


if plugin_actions.simpledialog is None:
    plugin_actions.simpledialog = SimpleNamespace(
        askstring=_unpatched_native_dialog,
    )
if plugin_actions.messagebox is None:
    plugin_actions.messagebox = SimpleNamespace(
        askyesno=_unpatched_native_dialog,
        showerror=_unpatched_native_dialog,
        showwarning=_unpatched_native_dialog,
        showinfo=_unpatched_native_dialog,
    )


def _instance(reference: str) -> plugin_actions.SchematicSymbol:
    return cast(plugin_actions.SchematicSymbol, SimpleNamespace(reference=reference))


def _block(name: str) -> BlockRecord:
    return cast(
        BlockRecord,
        SimpleNamespace(
            name=name,
            manifest={"pins": [{"name": "VIN"}, {"name": "GND"}]},
        ),
    )


def _pcb_placement_entry(
    instance_reference: str,
    block_name: str,
    item_uuid: str,
    *,
    layout_id: str = "standard",
    layout_name: str | None = "Standard",
    rotation: float = 0.0,
) -> BlockLayoutMergeResult:
    return {
        "output": "unused-stage.kicad_pcb",
        "block": block_name,
        "layout_id": layout_id,
        "layout_name": layout_name,
        "instance_reference": instance_reference,
        "rotation_deg": rotation,
        "project_file": None,
        "merged_netclasses": [],
        "assigned_project_nets": [],
        "ignored_project_patterns": False,
        "applied_project_patterns": [],
        "unmatched_project_patterns": [],
        "ignored_project_chain_classes": False,
        "merged_project_chain_names": [],
        "unmatched_project_chains": [],
        "merged_board_chains": [],
        "footprints": [f"{instance_reference}_1"],
        "interactive_item_uuids": [item_uuid],
        "footprint_count": 1,
        "object_count": 1,
        "new_net_count": 0,
        "new_nets": [],
        "skipped_top_level_items": [],
    }


def _pcb_merge_result(
    output: Path, *, rotation: float = 0.0, block_count: int = 1
) -> BlockLayoutMergeResult | BatchBlockLayoutMergeResult:
    placements: list[BlockLayoutMergeResult] = [
        _pcb_placement_entry(
            f"U{index}",
            f"Power stage U{index}" if block_count > 1 else "Power stage",
            f"placement-{index}",
            rotation=rotation if block_count == 1 else (45.0 if index == 1 else 90.0),
        )
        for index in range(1, block_count + 1)
    ]
    if block_count == 1:
        placement = placements[0]
        placement["output"] = str(output)
        return placement
    result: BatchBlockLayoutMergeResult = {
        "output": str(output),
        "project_file": None,
        "block_count": block_count,
        "placements": placements,
        "footprints": [reference for item in placements for reference in item["footprints"]],
        "footprint_count": sum(item["footprint_count"] for item in placements),
        "object_count": sum(item["object_count"] for item in placements),
        "new_net_count": block_count,
        "new_nets": ["VBUS", "VBAT"][:block_count],
        "merged_netclasses": [],
        "assigned_project_nets": [],
        "applied_project_patterns": [],
        "unmatched_project_patterns": [],
        "merged_project_chain_names": [],
        "unmatched_project_chains": [],
        "merged_board_chains": [],
        "skipped_top_level_items": [],
        "ignored_project_patterns": False,
        "ignored_project_chain_classes": False,
    }
    return result


def _pcb_placement_source(
    result: BlockLayoutMergeResult,
) -> BlockLayoutPlacementSource:
    source: BlockLayoutPlacementSource = {
        "instance_reference": result["instance_reference"],
        "interactive_item_uuids": result["interactive_item_uuids"],
        "block": result["block"],
    }
    if result["layout_name"] is not None:
        source["layout_name"] = result["layout_name"]
    return source


def _pcb_batch_placement_source(
    result: BatchBlockLayoutMergeResult,
) -> BatchBlockLayoutPlacementSource:
    return {"placements": [_pcb_placement_source(item) for item in result["placements"]]}


def _single_layout_context(
    monkeypatch: MonkeyPatch, tmp_path: Path, *, open_after_merge: bool
) -> tuple[Misc, plugin_actions.SchematicSymbol, BlockRecord, Path, Path, Path, _SingleLayoutCalls]:
    parent = cast(Misc, object())
    instance = _instance("U1")
    block = _block("Power stage")
    board = tmp_path / "project.kicad_pcb"
    output = tmp_path / "project_U1_blocks.kicad_pcb"
    sidecar = tmp_path / "project_U1_blocks.placement.json"
    board.write_text("(kicad_pcb)\n", encoding="utf-8")

    calls: _SingleLayoutCalls = {
        "prompts": [],
        "merge": [],
        "sidecar": [],
        "open": [],
        "confirm": [],
        "board_picker": [],
        "save_picker": [],
    }
    answers = iter(["VIN:VBUS, GND:GND", "1.5,2.5", "90"])

    def askstring(title: str, prompt: str, **kwargs: _RecordedValue) -> str | None:
        calls["prompts"].append((title, prompt, kwargs))
        return next(answers)

    monkeypatch.setattr(plugin_actions.simpledialog, "askstring", askstring)
    monkeypatch.setattr(
        plugin_actions,
        "_selected_symbol",
        lambda _schematic: instance,
    )
    monkeypatch.setattr(
        plugin_actions,
        "_block_record_for_instance",
        lambda *_args: block,
    )
    monkeypatch.setattr(
        plugin_actions,
        "_choose_block_layout",
        lambda *_args: {"id": "standard", "name": "Standard"},
    )
    monkeypatch.setattr(
        plugin_actions,
        "_pick_kicad_file",
        lambda _parent, **kwargs: calls["board_picker"].append(kwargs) or board,
    )
    monkeypatch.setattr(
        plugin_actions,
        "_pick_save_file",
        lambda _parent, **kwargs: calls["save_picker"].append(kwargs) or output,
    )
    monkeypatch.setattr(
        plugin_actions,
        "placement_manifest_path",
        lambda _path: sidecar,
    )

    def merge(
        source: Path,
        selected_block: BlockRecord,
        target: Path,
        **kwargs: _RecordedValue,
    ) -> BlockLayoutMergeResult:
        calls["merge"].append((source, selected_block, target, kwargs))
        return cast(BlockLayoutMergeResult, _pcb_merge_result(output, rotation=float(cast(float, kwargs["rotation_deg"]))))

    monkeypatch.setattr(plugin_actions, "merge_block_layout", merge)
    monkeypatch.setattr(
        plugin_actions,
        "write_pcb_placement_manifest",
        lambda target, result: calls["sidecar"].append(
            (target, _pcb_placement_source(cast(BlockLayoutMergeResult, result)))
        )
        or sidecar,
    )
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "askyesno",
        lambda title, message, **kwargs: (
            calls["confirm"].append((title, message, kwargs)) or open_after_merge
        ),
    )
    monkeypatch.setattr(
        plugin_actions,
        "_open_generated_pcb_copy",
        lambda _kicad, target: calls["open"].append(target),
    )
    return parent, instance, block, board, output, sidecar, calls


@pytest.mark.parametrize("open_after_merge", [False, True])
def test_single_pcb_layout_prompts_map_anchor_rotation_and_confirms_open(
    monkeypatch: MonkeyPatch, tmp_path: Path, open_after_merge: bool
):
    (
        parent,
        instance,
        block,
        board,
        output,
        _sidecar,
        calls,
    ) = _single_layout_context(monkeypatch, tmp_path, open_after_merge=open_after_merge)

    plugin_actions.apply_block_pcb_layout(
        cast(plugin_actions.Schematic, object()),
        cast(plugin_actions.KiCadApp, object()),
        tmp_path,
        tmp_path / "library",
        parent
    )

    assert [item[0] for item in calls["prompts"]] == [
        "Map block facade pins to board nets",
        "Stored PCB layout origin",
        "PCB block rotation",
    ]
    assert all(item[2]["parent"] is parent for item in calls["prompts"])
    assert calls["prompts"][0][2]["initialvalue"] == "VIN:VIN, GND:GND"
    assert calls["prompts"][1][2]["initialvalue"] == "0,0"
    assert calls["prompts"][2][2]["initialvalue"] == "0"

    assert len(calls["merge"]) == 1
    source, merged_block, target, kwargs = calls["merge"][0]
    assert source == board
    assert merged_block is block
    assert target == output
    assert kwargs == {
        "instance_reference": instance.reference,
        "position_mm": (100.0, 100.0),
        "pin_to_board_net": {"VIN": "VBUS", "GND": "GND"},
        "layout_anchor_mm": (1.5, 2.5),
        "rotation_deg": 90.0,
        "layout_id": "standard",
    }
    assert calls["board_picker"][0]["title"] == "Select the existing project PCB board"
    assert calls["board_picker"][0]["suffix"] == ".kicad_pcb"
    assert calls["save_picker"][0]["title"] == "Save merged PCB board copy"
    assert calls["save_picker"][0]["initial_path"] == (tmp_path / "project_U1_blocks.kicad_pcb")
    assert calls["sidecar"] == [
        (output.resolve(), _pcb_placement_source(_pcb_merge_result(output, rotation=90.0)))
    ]
    assert len(calls["confirm"]) == 1
    title, message, kwargs = calls["confirm"][0]
    assert title == "Open merged PCB board"
    assert "without modifying the original" in message
    assert kwargs["parent"] is parent
    assert calls["open"] == ([output.resolve()] if open_after_merge else [])


@pytest.mark.parametrize("cancel_at", [0, 1, 2])
def test_single_pcb_layout_cancelling_any_mapping_prompt_stops_before_merge(
    monkeypatch: MonkeyPatch, tmp_path: Path, cancel_at: int
):
    parent = cast(Misc, object())
    board = tmp_path / "project.kicad_pcb"
    board.write_text("(kicad_pcb)\n", encoding="utf-8")
    prompts: list[tuple[str, _RecordedKwargs]] = []
    responses: list[str | None] = ["VIN:VBUS,GND:GND", "1,2", "90"]
    responses[cancel_at] = None
    response_iter = iter(responses)
    merge_calls: list[bool] = []
    save_calls: list[bool] = []

    monkeypatch.setattr(
        plugin_actions,
        "_selected_symbol",
        lambda _schematic: _instance("U1"),
    )
    monkeypatch.setattr(
        plugin_actions,
        "_block_record_for_instance",
        lambda *_args: _block("Power stage"),
    )
    monkeypatch.setattr(
        plugin_actions,
        "_choose_block_layout",
        lambda *_args: {"id": "standard"},
    )
    monkeypatch.setattr(
        plugin_actions,
        "_pick_kicad_file",
        lambda *_args, **_kwargs: board,
    )
    monkeypatch.setattr(
        plugin_actions,
        "_pick_save_file",
        lambda *_args, **_kwargs: save_calls.append(True),
    )
    monkeypatch.setattr(
        plugin_actions.simpledialog,
        "askstring",
        lambda title, prompt, **kwargs: prompts.append((title, kwargs)) or next(response_iter),
    )
    monkeypatch.setattr(
        plugin_actions,
        "merge_block_layout",
        lambda *_args, **_kwargs: merge_calls.append(True),
    )
    confirm_calls = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "askyesno",
        lambda *args, **kwargs: confirm_calls.append(args),
    )

    assert (
        plugin_actions.apply_block_pcb_layout(
            cast(plugin_actions.Schematic, object()),
            cast(plugin_actions.KiCadApp, object()),
            tmp_path,
            tmp_path / "library",
            parent
        )
        is None
    )
    assert len(prompts) == cancel_at + 1
    assert all(item[1]["parent"] is parent for item in prompts)
    assert merge_calls == []
    assert save_calls == []
    assert confirm_calls == []


def _batch_context(
    monkeypatch: MonkeyPatch, tmp_path: Path, *, open_after_merge: bool
) -> tuple[
    Misc,
    list[plugin_actions.SchematicSymbol],
    dict[str, BlockRecord],
    Path,
    Path,
    Path,
    _BatchLayoutCalls,
]:
    parent = cast(Misc, object())
    instances = [_instance("U1"), _instance("U2")]
    blocks = {item.reference: _block(f"Power stage {item.reference}") for item in instances}
    board = tmp_path / "project.kicad_pcb"
    output = tmp_path / "project_2blocks.kicad_pcb"
    sidecar = tmp_path / "project_2blocks.placement.json"
    board.write_text("(kicad_pcb)\n", encoding="utf-8")
    responses = iter(
        [
            "VIN:VBUS, GND:GND",
            "1,2",
            "45",
            "VIN:VBAT, GND:GND",
            "3,4",
            "90",
        ]
    )
    calls: _BatchLayoutCalls = {
        "prompts": [],
        "merge": [],
        "confirm": [],
        "open": [],
        "sidecar": [],
        "board_picker": [],
        "save_picker": [],
    }

    def askstring(title: str, prompt: str, **kwargs: _RecordedValue) -> str | None:
        calls["prompts"].append((title, prompt, kwargs))
        return next(responses)

    monkeypatch.setattr(plugin_actions.simpledialog, "askstring", askstring)
    monkeypatch.setattr(
        plugin_actions,
        "_selected_symbols",
        lambda _schematic: instances,
    )
    monkeypatch.setattr(
        plugin_actions,
        "_block_record_for_instance",
        lambda _library, _project, instance: blocks[instance.reference],
    )
    monkeypatch.setattr(
        plugin_actions,
        "_choose_block_layout",
        lambda block, *_args: {"id": f"layout-{block.name[-2:]}"},
    )
    monkeypatch.setattr(
        plugin_actions,
        "_pick_kicad_file",
        lambda _parent, **kwargs: calls["board_picker"].append(kwargs) or board,
    )
    monkeypatch.setattr(
        plugin_actions,
        "_pick_save_file",
        lambda _parent, **kwargs: calls["save_picker"].append(kwargs) or output,
    )
    monkeypatch.setattr(plugin_actions, "placement_manifest_path", lambda _path: sidecar)

    def merge(
        source: Path,
        placements: list[plugin_actions.BlockLayoutPlacement],
        target: Path,
    ) -> BatchBlockLayoutMergeResult:
        calls["merge"].append((source, placements, target))
        return cast(
            BatchBlockLayoutMergeResult,
            _pcb_merge_result(output, rotation=90.0, block_count=2),
        )

    monkeypatch.setattr(plugin_actions, "merge_block_layouts", merge)
    monkeypatch.setattr(
        plugin_actions,
        "write_pcb_placement_manifest",
        lambda target, result: calls["sidecar"].append(
            (target, _pcb_batch_placement_source(cast(BatchBlockLayoutMergeResult, result)))
        )
        or sidecar,
    )
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "askyesno",
        lambda title, message, **kwargs: (
            calls["confirm"].append((title, message, kwargs)) or open_after_merge
        ),
    )
    monkeypatch.setattr(
        plugin_actions,
        "_open_generated_pcb_copy",
        lambda _kicad, target: calls["open"].append(target),
    )
    return parent, instances, blocks, board, output, sidecar, calls


@pytest.mark.parametrize("open_after_merge", [False, True])
def test_batch_pcb_layout_prompts_all_instances_and_confirms_open(
    monkeypatch: MonkeyPatch, tmp_path: Path, open_after_merge: bool
):
    parent, _instances, blocks, board, output, _sidecar, calls = _batch_context(
        monkeypatch, tmp_path, open_after_merge=open_after_merge
    )

    plugin_actions._apply_selected_block_pcb_layouts(
        cast(plugin_actions.Schematic, object()),
        cast(plugin_actions.KiCadApp, object()),
        tmp_path,
        tmp_path / "library",
        parent
    )

    assert [item[0] for item in calls["prompts"]] == [
        "PCB nets — U1",
        "PCB anchor — U1",
        "PCB rotation — U1",
        "PCB nets — U2",
        "PCB anchor — U2",
        "PCB rotation — U2",
    ]
    assert all(item[2]["parent"] is parent for item in calls["prompts"])
    assert [item[2]["initialvalue"] for item in calls["prompts"]] == [
        "VIN:VIN, GND:GND",
        "0,0",
        "0",
        "VIN:VIN, GND:GND",
        "0,0",
        "0",
    ]

    assert len(calls["merge"]) == 1
    source, placements, target = calls["merge"][0]
    assert source == board
    assert target == output
    assert len(placements) == 2
    assert placements[0].instance_reference == "U1"
    assert placements[0].position_mm == (100.0, 100.0)
    assert placements[0].pin_to_board_net == {"VIN": "VBUS", "GND": "GND"}
    assert placements[0].layout_anchor_mm == (1.0, 2.0)
    assert placements[0].rotation_deg == 45.0
    assert placements[1].instance_reference == "U2"
    assert placements[1].position_mm == (180.0, 100.0)
    assert placements[1].pin_to_board_net == {"VIN": "VBAT", "GND": "GND"}
    assert placements[1].layout_anchor_mm == (3.0, 4.0)
    assert placements[1].rotation_deg == 90.0
    assert placements[0].block is blocks["U1"]
    assert placements[1].block is blocks["U2"]

    assert calls["board_picker"][0]["title"] == "Select the existing project PCB board"
    assert calls["save_picker"][0]["title"] == "Save combined PCB block layout copy"
    assert calls["save_picker"][0]["initial_path"] == output
    assert calls["sidecar"] == [
        (
            output.resolve(),
            _pcb_batch_placement_source(_pcb_merge_result(output, rotation=90.0, block_count=2)),
        )
    ]
    assert len(calls["confirm"]) == 1
    assert calls["confirm"][0][0] == "Open combined PCB board"
    assert "Applied 2 block layouts" in calls["confirm"][0][1]
    assert calls["confirm"][0][2]["parent"] is parent
    assert calls["open"] == ([output.resolve()] if open_after_merge else [])


@pytest.mark.parametrize("cancel_at", [0, 1, 2, 3, 4, 5])
def test_batch_pcb_layout_cancelling_any_mapping_prompt_prevents_merge(
    monkeypatch: MonkeyPatch, tmp_path: Path, cancel_at: int
):
    parent = cast(Misc, object())
    instances = [_instance("U1"), _instance("U2")]
    blocks = {item.reference: _block(f"Power stage {item.reference}") for item in instances}
    prompts: list[tuple[str, _RecordedKwargs]] = []
    responses: list[str | None] = [
        "VIN:VBUS,GND:GND",
        "1,2",
        "45",
        "VIN:VBAT,GND:GND",
        "3,4",
        "90",
    ]
    responses[cancel_at] = None
    response_iter = iter(responses)
    merge_calls: list[bool] = []
    board_picks: list[bool] = []

    monkeypatch.setattr(plugin_actions, "_selected_symbols", lambda _s: instances)
    monkeypatch.setattr(
        plugin_actions,
        "_block_record_for_instance",
        lambda _library, _project, instance: blocks[instance.reference],
    )
    monkeypatch.setattr(
        plugin_actions,
        "_choose_block_layout",
        lambda block, *_args: {"id": f"layout-{block.name[-2:]}"},
    )
    monkeypatch.setattr(
        plugin_actions.simpledialog,
        "askstring",
        lambda title, prompt, **kwargs: prompts.append((title, kwargs)) or next(response_iter),
    )
    monkeypatch.setattr(
        plugin_actions,
        "_pick_kicad_file",
        lambda *_args, **_kwargs: board_picks.append(True),
    )
    monkeypatch.setattr(
        plugin_actions,
        "merge_block_layouts",
        lambda *_args, **_kwargs: merge_calls.append(True),
    )

    assert (
        plugin_actions._apply_selected_block_pcb_layouts(
            cast(plugin_actions.Schematic, object()),
            cast(plugin_actions.KiCadApp, object()),
            tmp_path,
            tmp_path / "library",
            parent
        )
        is None
    )
    assert len(prompts) == cancel_at + 1
    assert all(item[1]["parent"] is parent for item in prompts)
    assert board_picks == []
    assert merge_calls == []


class _ActionRoot:
    def __init__(self) -> None:
        self.window_title = ""
        self.withdrawn = False
        self.destroyed = False

    def title(self, value: str) -> None:
        self.window_title = value

    def withdraw(self) -> None:
        self.withdrawn = True

    def destroy(self) -> None:
        self.destroyed = True


def _patch_action_environment(
    monkeypatch: MonkeyPatch, *, schematic: plugin_actions.Schematic | None
) -> tuple[_ActionRoot, plugin_actions.KiCadApp]:
    root = _ActionRoot()
    def get_schematic() -> plugin_actions.Schematic | None:
        return schematic

    kicad = cast(plugin_actions.KiCadApp, SimpleNamespace(get_schematic=get_schematic))
    monkeypatch.setattr(plugin_actions, "_load_tkinter", lambda: None)
    monkeypatch.setattr(
        plugin_actions,
        "tk",
        SimpleNamespace(Tk=lambda: root, TclError=RuntimeError),
    )
    def no_theme(
        _root: Misc,
        _kicad: plugin_actions.KiCadApp | None = None,
    ) -> None:
        return None

    def no_attach(_root: Misc, _kicad: plugin_actions.KiCadApp) -> None:
        return None

    monkeypatch.setattr(plugin_actions, "_configure_tk_theme", no_theme)
    monkeypatch.setattr(plugin_actions, "_attach_tk_window_to_editor", no_attach)
    kipy_module = ModuleType("kipy")

    def create_kicad() -> plugin_actions.KiCadApp:
        return kicad

    setattr(kipy_module, "KiCad", create_kicad)
    monkeypatch.setitem(sys.modules, "kipy", kipy_module)
    return root, kicad


def test_run_action_missing_schematic_shows_attention_warning_and_closes_root(monkeypatch: MonkeyPatch):
    root, _kicad = _patch_action_environment(monkeypatch, schematic=None)
    warnings: list[tuple[str, str, _RecordedKwargs]] = []
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showwarning",
        lambda title, message, **kwargs: warnings.append((title, message, kwargs)),
    )

    plugin_actions.run_action("manage-library")

    assert len(warnings) == 1
    title, message, kwargs = warnings[0]
    assert title == "Action needs attention"
    assert "No active schematic was found" in message
    assert "selection or project context" in message
    assert kwargs["parent"] is root
    assert root.destroyed


def test_run_action_unexpected_failure_shows_error_and_closes_root(monkeypatch: MonkeyPatch, tmp_path: Path):
    schematic = cast(plugin_actions.Schematic, object())
    root, _kicad = _patch_action_environment(monkeypatch, schematic=schematic)
    library = tmp_path / "library"
    errors: list[tuple[str, str, _RecordedKwargs]] = []
    monkeypatch.setattr(plugin_actions, "project_directory", lambda _s: tmp_path)
    monkeypatch.setattr(plugin_actions, "configured_library_roots", lambda: [library])
    monkeypatch.setattr(plugin_actions, "default_library_root", lambda: library)

    def fail_publish(*_args: _RecordedValue, **_kwargs: _RecordedValue) -> None:
        raise RuntimeError("simulated publish failure")

    monkeypatch.setattr(plugin_actions, "_publish_block", fail_publish)
    monkeypatch.setattr(
        plugin_actions.messagebox,
        "showerror",
        lambda title, message, **kwargs: errors.append((title, message, kwargs)),
    )

    plugin_actions.run_action("publish-block")

    assert len(errors) == 1
    title, message, kwargs = errors[0]
    assert title == "KiCad Blocks"
    assert "simulated publish failure" in message
    assert "action could not be completed" in message
    assert kwargs["parent"] is root
    assert root.destroyed
    assert library.is_dir()
