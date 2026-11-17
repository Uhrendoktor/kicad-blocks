"""Exercise real KiCad 11 schematic IPC placement without launching a GUI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NoReturn, TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.auto_dependencies import synchronize_block_dependencies
from kicad_blocks.composition import compose_block_implementation
from kicad_blocks.model import (
    facade_pin_connection_points,
    parse_parameters,
    parse_pins,
    render_implementation_schematic,
    rotate_label_spin_style,
    schematic_symbol_orientation,
    transform_symbol_point_to_schematic,
)
from kicad_blocks.plugin import (
    KiCadApp,
    Schematic,
    SchematicSymbol,
    make_selected_local,
    register_block,
    sync_selected_local,
    write_instance_configuration,
)
from kicad_blocks.repository import (
    BlockRecord,
    add_dependency,
    create_block,
    local_copy_for_instance,
    register_project_symbol_library,
)
from kipy import KiCad
from kipy.geometry import Vector2


class _ExpectedNestedInstance(TypedDict):
    """Expected placement metadata for one nested child block."""

    orientation: int
    origin: tuple[float, float]
    pin_map: dict[str, str]


def _fail(message: str) -> NoReturn:
    """Fail an acceptance invariant without embedding messages in exception constructors."""
    _fail(message)


def _find_cli() -> str:
    configured = os.environ.get("KICAD_CLI")
    candidates = [configured] if configured else []
    candidates.extend(("kicad-cli-nightly", "kicad-cli"))
    for candidate in candidates:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    raise RuntimeError("KiCad CLI was not found; set KICAD_CLI or add it to PATH.")




class _SelectionFixture:
    """Small typed adapter exposing the selected facade to production IPC actions."""

    def __init__(self, instance: SchematicSymbol) -> None:
        self.instance = instance

    def get_selection(self) -> list[SchematicSymbol]:
        return [self.instance]


def _create_facade_fixture(
    root: Path,
) -> tuple[Path, Path, Path, BlockRecord, str, str]:
    project_dir = root / "project"
    library_root = root / "shared-library"
    project_dir.mkdir()
    schematic_path = project_dir / "facade_only.kicad_sch"
    schematic_path.write_text(
        render_implementation_schematic("Facade-only IPC Test", "ipc-root", []),
        encoding="utf-8",
    )
    default_layout = root / "default.kicad_pcb"
    default_layout.write_text("(kicad_pcb)\\n", encoding="utf-8")
    block = create_block(
        library_root,
        "Headless IPC Smoke Block",
        parse_pins("VIN:input, GND:passive, VOUT:output"),
        parameters=parse_parameters("OUTPUT_VOLTAGE:number=3.3, ENABLE:boolean=true"),
        layout_files=[{"id": "default", "name": "Default", "path": default_layout}],
        profiles=[
            {
                "id": "default_profile",
                "name": "Default configuration",
                "parameters": {"OUTPUT_VOLTAGE": "3.3", "ENABLE": "true"},
                "layout": "default",
            }
        ],
    )
    nickname = block.manifest["library_nickname"]
    symbol_name = block.manifest["symbol_name"]
    register_project_symbol_library(
        project_dir,
        nickname,
        block.symbol_library,
        description="KiCad Blocks shared facade catalog",
    )
    return project_dir, library_root, schematic_path, block, nickname, symbol_name


def _place_initial_facade(
    schematic: Schematic,
    nickname: str,
    symbol_name: str,
) -> SchematicSymbol:
    instance = schematic.place_symbol_from_library(
        f"{nickname}:{symbol_name}",
        Vector2.from_xy_mm(100.0, 100.0),
        reference="X1",
    )
    schematic.save()

    # Parameter defaults are normal KiCad symbol custom fields, so
    # instance values can be edited without replacing the facade ID.
    instance_fields = {field.name: field for field in instance.user_fields}
    parameter_fields = {
        name: field
        for name, field in instance_fields.items()
        if name.startswith("KB_PARAM_")
    }
    if set(parameter_fields) != {"KB_PARAM_OUTPUT_VOLTAGE", "KB_PARAM_ENABLE"}:
        _fail(
            f"Expected typed block parameter fields, got {sorted(parameter_fields)}."
        )
    if (
        "KB_LAYOUT" not in instance_fields
        or instance_fields["KB_LAYOUT"].text.value != "default"
    ):
        _fail(
            "The generated facade did not expose its default PCB layout field."
        )
    if (
        "KB_PROFILE" not in instance_fields
        or instance_fields["KB_PROFILE"].text.value
    ):
        _fail(
            "The generated facade did not expose its blank profile selector."
        )
    schematic.save()

    symbols = list(schematic.get_symbols())
    sheet_symbols = list(schematic.get_sheet_symbols())
    saved_fields = {field.name: field for field in symbols[0].user_fields}
    saved_parameter_values = {
        name: saved_fields[name].text.value
        for name in ("KB_PARAM_OUTPUT_VOLTAGE", "KB_PARAM_ENABLE", "KB_LAYOUT")
    }
    expected_parameter_values = {
        "KB_PARAM_OUTPUT_VOLTAGE": "3.3",
        "KB_PARAM_ENABLE": "true",
        "KB_LAYOUT": "default",
    }
    if saved_parameter_values != expected_parameter_values:
        _fail(
            f"Expected per-instance custom field defaults to persist after save: "
            f"{saved_parameter_values!r}"
        )

    if len(symbols) != 1:
        _fail(f"Expected one facade symbol; found {len(symbols)}.")
    if len(sheet_symbols) != 0:
        _fail(
            f"Expected zero hierarchical sheet symbols; found {len(sheet_symbols)}."
        )

    # Use the fully re-fetched document item, as the UI action does
    # through get_selection(), rather than the create response wrapper.
    instance = symbols[0]
    if instance.reference != "X1":
        _fail(f"Unexpected reference designator: {instance.reference!r}.")
    if (
        instance.definition.id.library != nickname
        or instance.definition.id.name != symbol_name
    ):
        _fail("Placed facade has the wrong KiCad library identifier.")
    return symbols[0]


def _configure_facade_instance(
    schematic: Schematic,
    instance: SchematicSymbol,
    nickname: str,
    symbol_name: str,
) -> SchematicSymbol:
    # IPC helper used by the manager's configuration form. The KIID and
    # library identity must remain stable.
    original_configured_id = str(instance.id)
    write_instance_configuration(
        cast("Schematic", schematic),
        cast("SchematicSymbol", instance),
        {
            "KB_PARAM_OUTPUT_VOLTAGE": "1.8",
            "KB_PARAM_ENABLE": "false",
            "KB_LAYOUT": "default",
            "KB_PROFILE": "default_profile",
        },
    )
    configured_symbols = list(schematic.get_symbols())
    if len(configured_symbols) != 1:
        _fail("Instance configuration duplicated the facade.")
    instance = configured_symbols[0]
    configured_values = {
        name: next(field.text.value for field in instance.user_fields if field.name == name)
        for name in (
            "KB_PARAM_OUTPUT_VOLTAGE",
            "KB_PARAM_ENABLE",
            "KB_LAYOUT",
            "KB_PROFILE",
        )
    }
    if configured_values != {
        "KB_PARAM_OUTPUT_VOLTAGE": "1.8",
        "KB_PARAM_ENABLE": "false",
        "KB_LAYOUT": "default",
        "KB_PROFILE": "default_profile",
    }:
        _fail(
            f"KiCad IPC did not persist the edited instance fields: {configured_values!r}"
        )
    if str(instance.id) != original_configured_id or instance.reference != "X1":
        _fail("Configuration changed the facade KIID or reference.")
    if (
        instance.definition.id.library != nickname
        or instance.definition.id.name != symbol_name
    ):
        _fail("Configuration changed the facade library identifier.")
    return instance


def _create_local_facade_copy(
    schematic: Schematic,
    kicad: KiCadApp,
    instance: SchematicSymbol,
    project_dir: Path,
    library_root: Path,
    nickname: str,
    symbol_name: str,
) -> tuple[_SelectionFixture, BlockRecord, str]:
    original_instance_id = str(instance.id)
    selection = _SelectionFixture(instance)
    local_path = make_selected_local(
        cast("Schematic", selection), cast("KiCadApp", kicad), project_dir, library_root
    )
    linked_local = local_copy_for_instance(project_dir, original_instance_id)
    link_file = project_dir / ".kicad_blocks" / "instances.json"
    link_data = json.loads(link_file.read_text(encoding="utf-8"))
    link_entry = link_data.get("instances", {}).get(original_instance_id)
    if linked_local is None:
        _fail(
            f"KIID {original_instance_id!r} has no local-copy mapping; "
            f"stored entries={list(link_data.get('instances', {}))!r}; "
            f"sidecar={link_file.read_text(encoding='utf-8')}"
        )
    local = linked_local
    if linked_local.path.resolve() != Path(local_path).resolve():
        _fail(
            f"Local-copy action returned {local_path!r}, but KIID mapping resolved "
            f"to {linked_local.path.resolve()}; entry={link_entry!r}"
        )
    symbols_after_local = list(schematic.get_symbols())
    if (
        len(symbols_after_local) != 1
        or symbols_after_local[0].reference != "X1"
        or str(symbols_after_local[0].id) != original_instance_id
    ):
        _fail(
            "Local-copy binding changed or duplicated the facade instance."
        )
    if (
        symbols_after_local[0].definition.id.library != nickname
        or symbols_after_local[0].definition.id.name != symbol_name
    ):
        _fail(
            "Local-copy binding unexpectedly changed the facade library ID."
        )

    return selection, local, original_instance_id


def _assert_local_sync_conflict(
    block: BlockRecord,
    local: BlockRecord,
    selection: _SelectionFixture,
    kicad: KiCadApp,
    project_dir: Path,
) -> None:
    local_before_conflict = local.implementation.read_text(encoding="utf-8")
    source_before_conflict = block.implementation.read_text(encoding="utf-8")
    local.implementation.write_text(
        local_before_conflict + "\n; local concurrent edit\n",
        encoding="utf-8",
    )
    block.implementation.write_text(
        source_before_conflict + "\n; library concurrent edit\n",
        encoding="utf-8",
    )
    try:
        sync_selected_local(cast("Schematic", selection), cast("KiCadApp", kicad), project_dir)
    except RuntimeError as conflict_error:
        if "Synchronization conflict" not in str(conflict_error):
            raise
    else:
        _fail("Divergent local/library edits were not rejected.")
    if "; local concurrent edit" not in local.implementation.read_text(encoding="utf-8"):
        _fail("Conflict detection overwrote the local version.")
    if "; library concurrent edit" not in block.implementation.read_text(encoding="utf-8"):
        _fail("Conflict detection overwrote the library version.")




def _validate_local_sync(
    block: BlockRecord,
    local: BlockRecord,
    selection: _SelectionFixture,
    kicad: KiCadApp,
    project_dir: Path,
    schematic: Schematic,
    original_instance_id: str,
) -> None:
    local_text = local.implementation.read_text(encoding="utf-8")
    local.implementation.write_text(
        local_text + "\n; local IPC publish test\n",
        encoding="utf-8",
    )
    published = sync_selected_local(
        cast("Schematic", selection), cast("KiCadApp", kicad), project_dir
    )
    if "implementation.kicad_sch" not in published["published"]:
        _fail(f"Local implementation edit was not published: {published!r}")
    if "; local IPC publish test" not in block.implementation.read_text(encoding="utf-8"):
        _fail(
            "Publishing a local implementation did not update its library source."
        )

    source_text = block.implementation.read_text(encoding="utf-8")
    block.implementation.write_text(
        source_text + "\n; upstream IPC pull test\n",
        encoding="utf-8",
    )
    pulled = sync_selected_local(
        cast("Schematic", selection), cast("KiCadApp", kicad), project_dir
    )
    if "implementation.kicad_sch" not in pulled["pulled"]:
        _fail(f"Upstream implementation edit was not pulled: {pulled!r}")
    if "; upstream IPC pull test" not in local.implementation.read_text(encoding="utf-8"):
        _fail("Synchronizing did not pull the library implementation edit.")

    _assert_local_sync_conflict(block, local, selection, kicad, project_dir)
    symbols_after_sync = list(schematic.get_symbols())
    if (
        len(symbols_after_sync) != 1
        or str(symbols_after_sync[0].id) != original_instance_id
        or symbols_after_sync[0].reference != "X1"
    ):
        _fail("Local synchronization changed the facade KIID/reference.")


def _register_runtime_facade(
    schematic: Schematic,
    kicad: KiCadApp,
    project_dir: Path,
    library_root: Path,
    nickname: str,
) -> BlockRecord:
    # Test the plugin's actual dynamic registration path: this library
    # did not exist in sym-lib-table when KiCad was started.
    late_block = create_block(
        library_root,
        "Runtime-Registered Block",
        parse_pins("A:input, B:output"),
    )
    late_block = register_block(project_dir, late_block, cast("KiCadApp", kicad))
    late_instance = schematic.place_symbol_from_library(
        f"{late_block.manifest['library_nickname']}:{late_block.manifest['symbol_name']}",
        Vector2.from_xy_mm(140.0, 100.0),
        reference="X2",
    )
    schematic.save()
    if late_instance.definition.id.library != nickname:
        _fail(
            "A newly added facade did not reload through the stable KBlocks nickname."
        )
    return late_block


def _validate_saved_parent_schematic(
    project_dir: Path,
    schematic_path: Path,
    nickname: str,
    symbol_name: str,
    late_block: BlockRecord,
) -> None:
    project_schematics = sorted(path.name for path in project_dir.glob("*.kicad_sch"))
    if project_schematics != [schematic_path.name]:
        _fail(
            "Block placement created an unexpected project schematic file: "
            + repr(project_schematics)
        )
    saved_text = schematic_path.read_text(encoding="utf-8")
    expected_lib_id = f'(lib_id "{nickname}:{symbol_name}")'
    if expected_lib_id not in saved_text:
        _fail("Saved project schematic does not contain the facade library ID.")
    if "(sheet " in saved_text:
        _fail("Saved parent schematic unexpectedly contains a child sheet.")
    if f'(lib_id "{nickname}:{late_block.manifest["symbol_name"]}")' not in saved_text:
        _fail(
            "The runtime-registered facade was not saved into the parent schematic."
        )

    print(
        "KiCad IPC passed: one stable catalog, local-instance metadata, runtime block addition, "
        "zero sheet symbols, and no project-local child schematic."
    )


def _run_facade_ipc_scenario(cli: str, root: Path) -> None:
    project_dir, library_root, schematic_path, block, nickname, symbol_name = (
        _create_facade_fixture(root)
    )
    kicad = KiCad(
        headless=True,
        kicad_cli_path=cli,
        file_path=str(schematic_path),
        timeout_ms=30000,
    )
    try:
        schematic = kicad.get_schematic()
        kicad.load_all_libraries()
        kicad.wait_for_libraries(timeout_s=120.0)
        instance = _place_initial_facade(schematic, nickname, symbol_name)
        instance = _configure_facade_instance(schematic, instance, nickname, symbol_name)
        selection, local, instance_id = _create_local_facade_copy(
            schematic, kicad, instance, project_dir, library_root, nickname, symbol_name
        )
        _validate_local_sync(
            block, local, selection, kicad, project_dir, schematic, instance_id
        )
        late_block = _register_runtime_facade(
            schematic, kicad, project_dir, library_root, nickname
        )
    finally:
        kicad.close()
    _validate_saved_parent_schematic(
        project_dir, schematic_path, nickname, symbol_name, late_block
    )


def _create_nested_blocks(nested_library: Path) -> tuple[BlockRecord, BlockRecord]:
    leaf = create_block(
        nested_library,
        "Nested Leaf",
        parse_pins("A:input, GND:passive, B:output"),
    )
    child = create_block(
        nested_library,
        "Nested Child",
        parse_pins("IN:input, GND:passive, OUT:output"),
    )
    child = add_dependency(
        child,
        leaf,
        reference="B9",
        position_mm=(160.0, 100.0),
        orientation=270,
        pin_map={"A": "IN", "GND": "GND", "B": "OUT"},
    )
    parent = create_block(
        nested_library,
        "Nested Parent",
        parse_pins("VIN:input, GND:passive, VOUT:output"),
    )
    parent = add_dependency(
        parent,
        child,
        reference="B1",
        position_mm=(100.0, 100.0),
        orientation=90,
        pin_map={"IN": "VIN_CHILD", "GND": "GND_CHILD", "OUT": "VOUT_CHILD"},
    )
    parent = add_dependency(
        parent,
        child,
        reference="B2",
        position_mm=(240.0, 100.0),
        orientation=0,
        pin_map={"IN": "VIN_CHILD2", "GND": "GND_CHILD2", "OUT": "VOUT_CHILD2"},
    )
    return child, parent


def _compose_nested_blocks(parent: BlockRecord, nested_library: Path, cli: str) -> None:
    composition = compose_block_implementation(parent, nested_library, cli)
    composed_names = {item["block"] for item in composition["composed"]}
    if composed_names != {"Nested Child", "Nested Parent"}:
        _fail(
            "Recursive nested composition did not process child before parent: "
            + repr(composed_names)
        )


def _expected_nested_instances() -> dict[str, _ExpectedNestedInstance]:
    return {
        "B1": {
            "orientation": 90,
            "origin": (100.0, 100.0),
            "pin_map": {
                "IN": "VIN_CHILD",
                "GND": "GND_CHILD",
                "OUT": "VOUT_CHILD",
            },
        },
        "B2": {
            "orientation": 0,
            "origin": (240.0, 100.0),
            "pin_map": {
                "IN": "VIN_CHILD2",
                "GND": "GND_CHILD2",
                "OUT": "VOUT_CHILD2",
            },
        },
    }


def _assert_nested_parent_symbols(
    schematic: Schematic,
    expected_instances: dict[str, _ExpectedNestedInstance],
) -> None:
    nested_symbols = list(schematic.get_symbols())
    nested_sheets = list(schematic.get_sheet_symbols())
    symbols_by_reference = {symbol.reference: symbol for symbol in nested_symbols}
    if set(symbols_by_reference) != {"B1", "B2"}:
        _fail(
            f"Expected two independent instances of the same child block; "
            f"found {sorted(symbols_by_reference)}."
        )
    if nested_sheets:
        _fail(
            f"Nested composition created {len(nested_sheets)} hierarchical sheets."
        )
    for reference, expected in expected_instances.items():
        symbol = symbols_by_reference[reference]
        rotation = expected["orientation"]
        if int(symbol.transform.orientation) != schematic_symbol_orientation(rotation):
            _fail(
                f"Nested facade {reference} was not placed at {rotation} degrees."
            )



def _assert_nested_parent_labels(
    schematic: Schematic,
    expected_instances: dict[str, _ExpectedNestedInstance],
) -> None:
    labels_by_name = {label.text.value: label for label in schematic.get_labels()}
    expected_names = {
        net_name
        for item in expected_instances.values()
        for net_name in item["pin_map"].values()
    }
    if not expected_names.issubset(labels_by_name):
        _fail(
            f"Mapped nested labels missing: {sorted(expected_names - set(labels_by_name))}"
        )

    pin_points = facade_pin_connection_points(
        parse_pins("IN:input, GND:passive, OUT:output")
    )
    for expected in expected_instances.values():
        rotation = expected["orientation"]
        origin_x, origin_y = expected["origin"]
        pin_map = expected["pin_map"]
        for pin_name, (local_x, local_y, spin) in pin_points.items():
            offset_x, offset_y = transform_symbol_point_to_schematic(
                local_x, local_y, rotation
            )
            expected_point = Vector2.from_xy_mm(origin_x + offset_x, origin_y + offset_y)
            actual_label = labels_by_name[pin_map[pin_name]]
            if int(actual_label.position.x) != int(expected_point.x) or int(
                actual_label.position.y
            ) != int(expected_point.y):
                _fail(
                    f"Label {pin_map[pin_name]} is not on the transformed pin endpoint: "
                    f"got {actual_label.position}, expected {expected_point}."
                )
            if int(actual_label.spin_style) != rotate_label_spin_style(spin, rotation):
                _fail(
                    f"Label {pin_map[pin_name]} has the wrong orientation "
                    f"for {rotation}-degree rotation."
                )


def _verify_nested_parent(
    cli: str,
    parent: BlockRecord,
    expected_instances: dict[str, _ExpectedNestedInstance],
) -> None:
    parent_kicad = KiCad(
        headless=True,
        kicad_cli_path=cli,
        file_path=str(parent.implementation),
        timeout_ms=30000,
    )
    try:
        parent_schematic = parent_kicad.get_schematic()
        parent_kicad.load_all_libraries()
        parent_kicad.wait_for_libraries(timeout_s=120.0)
        _assert_nested_parent_symbols(parent_schematic, expected_instances)
        _assert_nested_parent_labels(parent_schematic, expected_instances)
    finally:
        parent_kicad.close()


def _clear_block_dependencies(block: BlockRecord) -> None:
    manifest_path = block.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["dependencies"] = []
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\\n",
        encoding="utf-8",
    )


def _verify_child_dependency_discovery(
    child: BlockRecord,
    nested_library: Path,
    cli: str,
) -> None:
    _clear_block_dependencies(child)
    detected_child = synchronize_block_dependencies(child, [nested_library], cli)
    child_dependencies = detected_child["block"].manifest["dependencies"]
    if len(child_dependencies) != 1 or child_dependencies[0]["reference"] != "B9":
        _fail(
            "Automatic dependency discovery did not detect the child's leaf facade: "
            + repr(child_dependencies)
        )
    if child_dependencies[0]["orientation"] != 270:
        _fail("Automatic dependency discovery lost the leaf facade rotation.")
    if child_dependencies[0]["pin_map"] != {"A": "IN", "GND": "GND", "B": "OUT"}:
        _fail(
            "Automatic dependency discovery did not use the child's actual connected nets: "
            + repr(child_dependencies[0]["pin_map"])
        )


def _verify_parent_dependency_discovery(
    parent: BlockRecord,
    nested_library: Path,
    cli: str,
) -> None:
    _clear_block_dependencies(parent)
    detected_parent = synchronize_block_dependencies(parent, [nested_library], cli)
    parent_dependencies = detected_parent["block"].manifest["dependencies"]
    if [item["reference"] for item in parent_dependencies] != ["B1", "B2"]:
        _fail(
            "Automatic dependency discovery did not detect both child occurrences: "
            + repr(parent_dependencies)
        )
    expected_pin_maps = {
        "B1": {"IN": "VIN_CHILD", "GND": "GND_CHILD", "OUT": "VOUT_CHILD"},
        "B2": {"IN": "VIN_CHILD2", "GND": "GND_CHILD2", "OUT": "VOUT_CHILD2"},
    }
    for dependency in parent_dependencies:
        if dependency["pin_map"] != expected_pin_maps[dependency["reference"]]:
            _fail(
                f"Automatic pin-to-net mapping is wrong for {dependency['reference']}: "
                + repr(dependency["pin_map"])
            )
    if {item["orientation"] for item in parent_dependencies} != {0, 90}:
        _fail("Automatic dependency discovery lost child facade orientations.")


def _verify_nested_child(cli: str, child: BlockRecord) -> None:
    child_kicad = KiCad(
        headless=True,
        kicad_cli_path=cli,
        file_path=str(child.implementation),
        timeout_ms=30000,
    )
    try:
        child_schematic = child_kicad.get_schematic()
        child_kicad.load_all_libraries()
        child_kicad.wait_for_libraries(timeout_s=120.0)
        leaf_symbols = list(child_schematic.get_symbols())
        if len(leaf_symbols) != 1:
            _fail(
                f"Expected one leaf facade in child implementation; found {len(leaf_symbols)}."
            )
        if leaf_symbols[0].reference != "B9":
            _fail(
                f"Expected nested leaf reference B9; found {leaf_symbols[0].reference!r}."
            )
        if int(leaf_symbols[0].transform.orientation) != schematic_symbol_orientation(270):
            _fail("Nested leaf facade was not placed at 270 degrees.")
        if child_schematic.get_sheet_symbols():
            _fail("Second-level composition created a hierarchical sheet.")

        child_labels = list(child_schematic.get_labels())
        leaf_pin_points = facade_pin_connection_points(
            parse_pins("A:input, GND:passive, B:output")
        )
        leaf_pin_map = {"A": "IN", "GND": "GND", "B": "OUT"}
        for pin_name, (local_x, local_y, spin) in leaf_pin_points.items():
            delta_x, delta_y = transform_symbol_point_to_schematic(local_x, local_y, 270)
            expected_point = Vector2.from_xy_mm(160.0 + delta_x, 100.0 + delta_y)
            expected_spin = rotate_label_spin_style(spin, 270)
            matching = [
                label
                for label in child_labels
                if label.text.value == leaf_pin_map[pin_name]
                and int(label.position.x) == int(expected_point.x)
                and int(label.position.y) == int(expected_point.y)
            ]
            if not matching:
                _fail(
                    f"Second-level label {leaf_pin_map[pin_name]} is not at the rotated "
                    f"leaf pin endpoint {expected_point}."
                )
            if int(matching[0].spin_style) != expected_spin:
                _fail(
                    f"Second-level label {leaf_pin_map[pin_name]} has the wrong spin style."
                )
    finally:
        child_kicad.close()


def _assert_implementation_export(
    block: BlockRecord,
    root: Path,
    cli: str,
    export_directory: str,
    description: str,
) -> None:
    schematics = sorted(path.name for path in block.path.glob("*.kicad_sch"))
    if schematics != ["implementation.kicad_sch"]:
        _fail(
            f"{description} generated an unexpected schematic file: {schematics!r}"
        )
    svg_dir = root / export_directory
    svg_dir.mkdir()
    subprocess.run(
        [
            cli,
            "sch",
            "export",
            "svg",
            "--output",
            str(svg_dir),
            str(block.implementation),
        ],
        check=True,
    )
    if not list(svg_dir.glob("*.svg")):
        _fail(f"KiCad CLI could not export the {description} implementation.")


def _run_nested_ipc_scenario(cli: str, root: Path) -> None:
    nested_library = root / "nested-library"
    child, parent = _create_nested_blocks(nested_library)
    _compose_nested_blocks(parent, nested_library, cli)
    expected_instances = _expected_nested_instances()
    _verify_nested_parent(cli, parent, expected_instances)
    _verify_child_dependency_discovery(child, nested_library, cli)
    _verify_parent_dependency_discovery(parent, nested_library, cli)
    _assert_implementation_export(
        parent, root, cli, "nested-parent-svg", "Nested parent"
    )
    _verify_nested_child(cli, child)
    _assert_implementation_export(
        child, root, cli, "nested-child-svg", "Second-level child"
    )
    print(
        "Nested block composition passed: two recursive levels (90° child and 270° leaf facades), "
        "zero hierarchical sheets and no copied child schematic."
    )


def main() -> None:
    """Run the live KiCad IPC and recursive composition acceptance scenarios."""
    cli = _find_cli()
    with tempfile.TemporaryDirectory(prefix="kicad-blocks-ipc-") as temporary:
        root = Path(temporary)
        _run_facade_ipc_scenario(cli, root)
        _run_nested_ipc_scenario(cli, root)




if __name__ == "__main__":
    main()
