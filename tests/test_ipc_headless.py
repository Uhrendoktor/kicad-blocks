"""Exercise real KiCad 11 schematic IPC placement without launching a GUI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import (
    facade_pin_connection_points,
    parse_parameters,
    parse_pins,
    render_implementation_schematic,
    rotate_label_spin_style,
    schematic_symbol_orientation,
    transform_symbol_point_to_schematic,
)
from kicad_blocks.pcb_layout import parse_sexpr
from kicad_blocks.plugin import (
    _register_block,
    _write_instance_configuration,
    make_selected_local,
    sync_selected_local,
)
from kicad_blocks.repository import (
    create_block,
    ensure_stable_block_identity,
    local_copy_for_instance,
    read_manifest,
    refresh_library_block,
)
from kipy import KiCad
from kipy.geometry import Vector2
from kipy.proto.common.types import DocumentType


def _find_cli() -> str:
    configured = os.environ.get("KICAD_CLI")
    candidates = [configured] if configured else []
    candidates.extend(("kicad-cli-nightly", "kicad-cli"))
    for candidate in candidates:
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    raise RuntimeError("KiCad CLI was not found; set KICAD_CLI or add it to PATH.")


def main() -> None:
    """Start KiCad headlessly, load a project symbol library and place its facade."""
    cli = _find_cli()
    with tempfile.TemporaryDirectory(prefix="kicad-blocks-ipc-") as temp:
        root = Path(temp)
        project_dir = root / "project"
        library_root = root / "shared-library"
        project_dir.mkdir()

        schematic_path = project_dir / "facade_only.kicad_sch"
        schematic_path.write_text(
            render_implementation_schematic("Facade-only IPC Test", "ipc-root", []),
            encoding="utf-8",
        )
        # KiCad resolves project-scoped symbol libraries through the active
        # project. Opening a standalone schematic without its matching
        # .kicad_pro leaves LTS_PROJECT unbound, so reload_library cannot see
        # the project sym-lib-table created below.
        project_path = project_dir / "facade_only.kicad_pro"
        project_path.write_text("{}\n", encoding="utf-8")
        default_layout = root / "default.kicad_pcb"
        default_layout.write_text("(kicad_pcb)\\n", encoding="utf-8")
        block_source = root / "block_source.kicad_sch"
        initial_interface = parse_pins("VIN:input, GND:passive, VOUT:output")
        block_source.write_text(
            render_implementation_schematic(
                "Headless IPC Smoke Block",
                "headless-ipc-source",
                initial_interface,
            ),
            encoding="utf-8",
        )
        block = create_block(
            library_root,
            "Headless IPC Smoke Block",
            source_schematic=block_source,
            source_project_name=project_dir.name,
            parameters=parse_parameters("OUTPUT_VOLTAGE:number=3.3, ENABLE:boolean=true"),
            layout_files=[{"id": "default", "name": "Default", "path": default_layout}],
            profiles=[{
                "id": "default_profile",
                "name": "Default configuration",
                "parameters": {"OUTPUT_VOLTAGE": "3.3", "ENABLE": "true"},
                "layout": "default",
            }],
        )
        # Match the application's startup registration path before placing
        # the first instance. A legacy block is migrated to the shared KBlocks
        # nickname/catalog here, so later publication reloads the same library
        # identifier rather than leaving X1 on an obsolete per-block nickname.
        block = ensure_stable_block_identity(block)
        nickname = block.manifest["library_nickname"]
        symbol_name = block.manifest["symbol_name"]
        # Open the project before its schematic. Preloading only a .kicad_sch
        # starts Eeschema without loading the project and its sym-lib-table;
        # registering a project-scoped library later then fails with
        # "Library 'KBlocks' not found", even though both files are present.
        kicad = KiCad(
            headless=True,
            kicad_cli_path=cli,
            file_path=str(project_path),
            timeout_ms=30000,
        )
        try:
            kicad.open_document(
                str(schematic_path), DocumentType.DOCTYPE_SCHEMATIC
            )
            schematic = kicad.get_schematic()
            if schematic is None:
                raise RuntimeError("KiCad IPC did not load the test schematic.")

            # Match the application's registration order: build the unified
            # project-local catalog, register that catalog, and reload it
            # before the first placement. Keeping the URI stable is important
            # when the facade is published again later in this same session.
            kicad.load_all_libraries(types=[1])
            block = _register_block(project_dir, block, kicad, [library_root])
            nickname = block.manifest["library_nickname"]
            symbol_name = block.manifest["symbol_name"]
            kicad.wait_for_libraries(types=[1], timeout_s=120.0)

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
                name: field for name, field in instance_fields.items()
                if name.startswith("KB_PARAM_")
            }
            if set(parameter_fields) != {"KB_PARAM_OUTPUT_VOLTAGE", "KB_PARAM_ENABLE"}:
                raise AssertionError(
                    f"Expected typed block parameter fields, got {sorted(parameter_fields)}."
                )
            if "KB_LAYOUT" not in instance_fields or instance_fields["KB_LAYOUT"].text.value != "default":
                raise AssertionError("The generated facade did not expose its default PCB layout field.")
            if "KB_PROFILE" not in instance_fields or instance_fields["KB_PROFILE"].text.value != "":
                raise AssertionError("The generated facade did not expose its blank profile selector.")
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
                raise AssertionError(
                    f"Expected per-instance custom field defaults to persist after save: "
                    f"{saved_parameter_values!r}"
                )

            # Use the fully re-fetched document item, as the UI action does
            # through get_selection(), rather than the create response wrapper.
            instance = symbols[0]

            # Edit fields on the selected facade through the same transactional
            # IPC helper used by the manager's configuration form. The KIID and
            # library identity must remain stable.
            original_configured_id = str(instance.id)
            _write_instance_configuration(
                schematic,
                instance,
                {
                    "KB_PARAM_OUTPUT_VOLTAGE": "1.8",
                    "KB_PARAM_ENABLE": "false",
                    "KB_LAYOUT": "default",
                    "KB_PROFILE": "default_profile",
                },
            )
            configured_symbols = list(schematic.get_symbols())
            if len(configured_symbols) != 1:
                raise AssertionError("Instance configuration duplicated the facade.")
            instance = configured_symbols[0]
            configured_values = {
                name: next(
                    field.text.value
                    for field in instance.user_fields
                    if field.name == name
                )
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
                raise AssertionError(
                    f"KiCad IPC did not persist the edited instance fields: {configured_values!r}"
                )
            if str(instance.id) != original_configured_id or instance.reference != "X1":
                raise AssertionError("Configuration changed the facade KIID or reference.")
            if len(symbols) != 1:
                raise AssertionError(f"Expected one facade symbol; found {len(symbols)}.")
            if len(sheet_symbols) != 0:
                raise AssertionError(
                    f"Expected zero hierarchical sheet symbols; found {len(sheet_symbols)}."
                )
            if instance.reference != "X1":
                raise AssertionError(f"Unexpected reference designator: {instance.reference!r}.")
            if (
                instance.definition.id.library != nickname
                or instance.definition.id.name != symbol_name
            ):
                raise AssertionError("Placed facade has the wrong KiCad library identifier.")

            original_instance_id = str(instance.id)
            selection = SimpleNamespace(get_selection=lambda: [instance])
            local_path = make_selected_local(
                selection, kicad, project_dir, library_root
            )
            linked_local = local_copy_for_instance(project_dir, original_instance_id)
            link_file = project_dir / ".kicad_blocks" / "instances.json"
            link_data = json.loads(link_file.read_text(encoding="utf-8"))
            link_entry = link_data.get("instances", {}).get(original_instance_id)
            if linked_local is None:
                raise AssertionError(
                    f"KIID {original_instance_id!r} has no local-copy mapping; "
                    f"stored entries={list(link_data.get('instances', {}))!r}; "
                    f"sidecar={link_file.read_text(encoding='utf-8')}"
                )
            local = linked_local
            if linked_local.path.resolve() != Path(local_path).resolve():
                raise AssertionError(
                    f"Local-copy action returned {local_path!r}, but KIID mapping resolved "
                    f"to {linked_local.path.resolve()}; entry={link_entry!r}"
                )
            symbols_after_local = list(schematic.get_symbols())
            if (
                len(symbols_after_local) != 1
                or symbols_after_local[0].reference != "X1"
                or str(symbols_after_local[0].id) != original_instance_id
            ):
                raise AssertionError("Local-copy binding changed or duplicated the facade instance.")
            if (
                symbols_after_local[0].definition.id.library != nickname
                or symbols_after_local[0].definition.id.name != symbol_name
            ):
                raise AssertionError("Local-copy binding unexpectedly changed the facade library ID.")

            local_text = local.implementation.read_text(encoding="utf-8")
            local.implementation.write_text(
                local_text + "\n; local IPC publish test\n",
                encoding="utf-8",
            )
            published = sync_selected_local(selection, kicad, project_dir)
            if "implementation.kicad_sch" not in published["published"]:
                raise AssertionError(f"Local implementation edit was not published: {published!r}")
            if "; local IPC publish test" not in block.implementation.read_text(encoding="utf-8"):
                raise AssertionError("Publishing a local implementation did not update its library source.")

            source_text = block.implementation.read_text(encoding="utf-8")
            block.implementation.write_text(
                source_text + "\n; upstream IPC pull test\n",
                encoding="utf-8",
            )
            pulled = sync_selected_local(selection, kicad, project_dir)
            if "implementation.kicad_sch" not in pulled["pulled"]:
                raise AssertionError(f"Upstream implementation edit was not pulled: {pulled!r}")
            if "; upstream IPC pull test" not in local.implementation.read_text(encoding="utf-8"):
                raise AssertionError("Synchronizing did not pull the library implementation edit.")

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
                sync_selected_local(selection, kicad, project_dir)
            except RuntimeError as conflict_error:
                if "Synchronization conflict" not in str(conflict_error):
                    raise
            else:
                raise AssertionError("Divergent local/library edits were not rejected.")
            if "; local concurrent edit" not in local.implementation.read_text(encoding="utf-8"):
                raise AssertionError("Conflict detection overwrote the local version.")
            if "; library concurrent edit" not in block.implementation.read_text(encoding="utf-8"):
                raise AssertionError("Conflict detection overwrote the library version.")

            symbols_after_sync = list(schematic.get_symbols())
            if (
                len(symbols_after_sync) != 1
                or str(symbols_after_sync[0].id) != original_instance_id
                or symbols_after_sync[0].reference != "X1"
            ):
                raise AssertionError("Local synchronization changed the facade KIID/reference.")

            # Update the implementation interface after X1 is already
            # placed in the parent schematic. Publishing the regenerated
            # facade and reloading the project library must not be confused
            # with refreshing the embedded copy stored in that existing
            # schematic instance.
            original_parent_pin_names = {
                pin.name for pin in instance.definition.pins
            }
            if original_parent_pin_names != {"VIN", "GND", "VOUT"}:
                raise AssertionError(
                    f"Unexpected original parent facade pins: {original_parent_pin_names!r}"
                )
            changed_interface = parse_pins("VIN:input, VOUT:output, ENABLE:input")
            block.implementation.write_text(
                render_implementation_schematic(
                    "Headless IPC Smoke Block",
                    block.manifest["block_id"],
                    changed_interface,
                ),
                encoding="utf-8",
            )
            refresh_library_block(block)
            changed_manifest = read_manifest(block.path)
            if {pin["name"] for pin in changed_manifest["pins"]} != {
                "VIN", "VOUT", "ENABLE"
            }:
                raise AssertionError(
                    "Publishing the changed implementation did not regenerate the block facade pins."
                )
            _register_block(project_dir, block, kicad, [library_root])

            # Capture the exact serialized catalog shape before KiCad reloads it.
            # This distinguishes a malformed source-library catalog from a bad
            # project-catalog aggregation or a KiCad IPC cache issue.
            shared_catalog_text = block.symbol_library.read_text(encoding="utf-8")
            project_catalog_path = (
                project_dir / ".kicad_blocks" / "KBlocks.kicad_sym"
            )
            project_catalog_text = project_catalog_path.read_text(encoding="utf-8")
            shared_tree = parse_sexpr(shared_catalog_text)
            project_tree = parse_sexpr(project_catalog_text)
            print(
                "KBlocks catalog structural diagnostics: "
                f"shared top-level={[str(item[0]) for item in shared_tree[1:] if isinstance(item, list) and item]!r}; "
                f"project top-level={[str(item[0]) for item in project_tree[1:] if isinstance(item, list) and item]!r}"
            )
            print("KBlocks project catalog before IPC reload:\\n" + project_catalog_text)
            sym_table_text = (project_dir / "sym-lib-table").read_text(encoding="utf-8")
            print("Project sym-lib-table after registration:\\n" + sym_table_text)

            library_definition = kicad.get_library_symbol(
                f"{nickname}:{symbol_name}",
                document=schematic.document,
            )
            library_pin_names = {pin.name for pin in library_definition.pins}
            if library_pin_names != {"VIN", "VOUT", "ENABLE"}:
                raise AssertionError(
                    "The reloaded project library does not expose the regenerated facade pins: "
                    f"{library_pin_names!r}"
                )

            # The existing parent instance is an embedded snapshot. Reloading
            # its library row refreshes future library lookups but does not
            # silently mutate the already-placed symbol's pin geometry.
            parent_symbols_after_library_reload = list(schematic.get_symbols())
            parent_x1 = next(
                item for item in parent_symbols_after_library_reload
                if item.reference == "X1"
            )
            parent_pin_names_after_reload = {
                pin.name for pin in parent_x1.definition.pins
            }
            if parent_pin_names_after_reload != original_parent_pin_names:
                raise AssertionError(
                    "KiCad unexpectedly changed the existing parent facade when the library "
                    "was reloaded; inspect whether an implicit symbol update occurred: "
                    f"{parent_pin_names_after_reload!r}"
                )
            if str(parent_x1.id) != original_instance_id:
                raise AssertionError("The parent X1 KIID changed during library reload.")

            # New placements use the updated library facade, while the old
            # parent instance remains unchanged until the user explicitly runs
            # KiCad's Update Symbols from Library command.
            new_instance = schematic.place_symbol_from_library(
                f"{nickname}:{symbol_name}",
                Vector2.from_xy_mm(180.0, 100.0),
                reference="X3",
            )
            if {pin.name for pin in new_instance.definition.pins} != library_pin_names:
                raise AssertionError("A new parent placement did not use the updated facade pins.")
            schematic.save()
            print(
                "KiCad IPC confirmed: library facade updated to VIN/VOUT/ENABLE; "
                "existing parent X1 remains VIN/GND/VOUT; new X3 uses updated pins."
            )

            # Test the plugin's actual dynamic registration path: this library
            # did not exist in sym-lib-table when KiCad was started.
            late_block = create_block(
                library_root,
                "Runtime-Registered Block",
                parse_pins("A:input, B:output"),
            )
            late_block = _register_block(project_dir, late_block, kicad)
            late_instance = schematic.place_symbol_from_library(
                f"{late_block.manifest['library_nickname']}:{late_block.manifest['symbol_name']}",
                Vector2.from_xy_mm(140.0, 100.0),
                reference="X2",
            )
            schematic.save()
            if late_instance.definition.id.library != nickname:
                raise AssertionError("A newly added facade did not reload through the stable KBlocks nickname.")
        finally:
            kicad.close()

        project_schematics = sorted(path.name for path in project_dir.glob("*.kicad_sch"))
        if project_schematics != [schematic_path.name]:
            raise AssertionError(
                "Block placement created an unexpected project schematic file: "
                + repr(project_schematics)
            )
        saved_text = schematic_path.read_text(encoding="utf-8")
        expected_lib_id = f'(lib_id "{nickname}:{symbol_name}")'
        if expected_lib_id not in saved_text:
            raise AssertionError("Saved project schematic does not contain the facade library ID.")
        if "(sheet " in saved_text:
            raise AssertionError("Saved parent schematic unexpectedly contains a child sheet.")
        if f'(lib_id "{nickname}:{late_block.manifest["symbol_name"]}")' not in saved_text:
            raise AssertionError("The runtime-registered facade was not saved into the parent schematic.")

        print(
            "KiCad IPC passed: one stable catalog, local-instance metadata, runtime block addition, "
            "zero sheet symbols, and no project-local child schematic."
        )
        # Compose a library child into a parent implementation as a facade symbol
        # plus external global labels. No hierarchical sheet is created.
        from kicad_blocks.composition import compose_block_implementation
        from kicad_blocks.repository import add_dependency

        nested_library = root / "nested-library"
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
        composition = compose_block_implementation(parent, nested_library, cli)
        composed_names = {item["block"] for item in composition["composed"]}
        if composed_names != {"Nested Child", "Nested Parent"}:
            raise AssertionError(
                "Recursive nested composition did not process child before parent: "
                + repr(composed_names)
            )

        parent_kicad = KiCad(
            headless=True,
            kicad_cli_path=cli,
            file_path=str(parent.implementation),
            timeout_ms=30000,
        )
        try:
            parent_schematic = parent_kicad.get_schematic()
            if parent_schematic is None:
                raise RuntimeError("Could not reopen the composed parent implementation.")
            parent_kicad.load_all_libraries(types=[1])
            parent_kicad.wait_for_libraries(types=[1], timeout_s=120.0)
            nested_symbols = list(parent_schematic.get_symbols())
            nested_sheets = list(parent_schematic.get_sheet_symbols())
            symbols_by_reference = {symbol.reference: symbol for symbol in nested_symbols}
            if set(symbols_by_reference) != {"B1", "B2"}:
                raise AssertionError(
                    f"Expected two independent instances of the same child block; "
                    f"found {sorted(symbols_by_reference)}."
                )
            if nested_sheets:
                raise AssertionError(
                    f"Nested composition created {len(nested_sheets)} hierarchical sheets."
                )
            expected_instances = {
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
            for reference, expected in expected_instances.items():
                symbol = symbols_by_reference[reference]
                rotation = expected["orientation"]
                if int(symbol.transform.orientation) != schematic_symbol_orientation(rotation):
                    raise AssertionError(
                        f"Nested facade {reference} was not placed at {rotation} degrees."
                    )

            labels_by_name = {
                label.text.value: label
                for label in parent_schematic.get_labels()
            }
            expected_names = {
                net_name
                for item in expected_instances.values()
                for net_name in item["pin_map"].values()
            }
            if not expected_names.issubset(labels_by_name):
                raise AssertionError(
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
                    expected_point = Vector2.from_xy_mm(
                        origin_x + offset_x, origin_y + offset_y
                    )
                    actual_label = labels_by_name[pin_map[pin_name]]
                    if (
                        int(actual_label.position.x) != int(expected_point.x)
                        or int(actual_label.position.y) != int(expected_point.y)
                    ):
                        raise AssertionError(
                            f"Label {pin_map[pin_name]} is not on the transformed pin endpoint: "
                            f"got {actual_label.position}, expected {expected_point}."
                        )
                    if int(actual_label.spin_style) != rotate_label_spin_style(spin, rotation):
                        raise AssertionError(
                            f"Label {pin_map[pin_name]} has the wrong orientation "
                            f"for {rotation}-degree rotation."
                        )
        finally:
            parent_kicad.close()

        # Rebuild dependency metadata from the actual placed child facades and
        # KiCad's exported electrical netlist, without using the manual add action.
        from kicad_blocks.auto_dependencies import synchronize_block_dependencies

        child_manifest_path = child.path / "manifest.json"
        child_manifest = json.loads(child_manifest_path.read_text(encoding="utf-8"))
        child_manifest["dependencies"] = []
        child_manifest_path.write_text(
            json.dumps(child_manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        detected_child = synchronize_block_dependencies(child, [nested_library], cli)
        child_dependencies = detected_child["block"].manifest["dependencies"]
        if len(child_dependencies) != 1 or child_dependencies[0]["reference"] != "B9":
            raise AssertionError(
                "Automatic dependency discovery did not detect the child's leaf facade: "
                + repr(child_dependencies)
            )
        if child_dependencies[0]["orientation"] != 270:
            raise AssertionError("Automatic dependency discovery lost the leaf facade rotation.")
        if child_dependencies[0]["pin_map"] != {"A": "IN", "GND": "GND", "B": "OUT"}:
            raise AssertionError(
                "Automatic dependency discovery did not use the child's actual connected nets: "
                + repr(child_dependencies[0]["pin_map"])
            )

        parent_manifest_path = parent.path / "manifest.json"
        parent_manifest = json.loads(parent_manifest_path.read_text(encoding="utf-8"))
        parent_manifest["dependencies"] = []
        parent_manifest_path.write_text(
            json.dumps(parent_manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        detected_parent = synchronize_block_dependencies(parent, [nested_library], cli)
        parent_dependencies = detected_parent["block"].manifest["dependencies"]
        if [item["reference"] for item in parent_dependencies] != ["B1", "B2"]:
            raise AssertionError(
                "Automatic dependency discovery did not detect both child occurrences: "
                + repr(parent_dependencies)
            )
        expected_pin_maps = {
            "B1": {"IN": "VIN_CHILD", "GND": "GND_CHILD", "OUT": "VOUT_CHILD"},
            "B2": {"IN": "VIN_CHILD2", "GND": "GND_CHILD2", "OUT": "VOUT_CHILD2"},
        }
        for dependency in parent_dependencies:
            if dependency["pin_map"] != expected_pin_maps[dependency["reference"]]:
                raise AssertionError(
                    f"Automatic pin-to-net mapping is wrong for {dependency['reference']}: "
                    + repr(dependency["pin_map"])
                )
        if {item["orientation"] for item in parent_dependencies} != {0, 90}:
            raise AssertionError("Automatic dependency discovery lost child facade orientations.")

        parent_schematics = sorted(path.name for path in parent.path.glob("*.kicad_sch"))
        if parent_schematics != ["implementation.kicad_sch"]:
            raise AssertionError(
                "Nested composition generated an unexpected schematic file: "
                + repr(parent_schematics)
            )
        nested_svg_dir = Path(temp) / "nested-parent-svg"
        nested_svg_dir.mkdir()
        subprocess.run(
            [
                cli,
                "sch",
                "export",
                "svg",
                "--output",
                str(nested_svg_dir),
                str(parent.implementation),
            ],
            check=True,
        )
        if not list(nested_svg_dir.glob("*.svg")):
            raise AssertionError("KiCad CLI could not export the composed parent implementation.")
        # Verify the second level independently: the child implementation
        # must contain its rotated leaf facade, while the parent still contains
        # only the child facade. Both remain ordinary symbols on their own
        # library implementation documents, not hierarchical sheet instances.
        child_kicad = KiCad(
            headless=True,
            kicad_cli_path=cli,
            file_path=str(child.implementation),
            timeout_ms=30000,
        )
        try:
            child_schematic = child_kicad.get_schematic()
            if child_schematic is None:
                raise RuntimeError("Could not reopen the recursively composed child implementation.")
            child_kicad.load_all_libraries(types=[1])
            child_kicad.wait_for_libraries(types=[1], timeout_s=120.0)
            leaf_symbols = list(child_schematic.get_symbols())
            if len(leaf_symbols) != 1:
                raise AssertionError(
                    f"Expected one leaf facade in child implementation; found {len(leaf_symbols)}."
                )
            if leaf_symbols[0].reference != "B9":
                raise AssertionError(
                    f"Expected nested leaf reference B9; found {leaf_symbols[0].reference!r}."
                )
            if int(leaf_symbols[0].transform.orientation) != schematic_symbol_orientation(270):
                raise AssertionError("Nested leaf facade was not placed at 270 degrees.")
            if child_schematic.get_sheet_symbols():
                raise AssertionError("Second-level composition created a hierarchical sheet.")

            child_labels = list(child_schematic.get_labels())
            leaf_pin_points = facade_pin_connection_points(
                parse_pins("A:input, GND:passive, B:output")
            )
            leaf_pin_map = {"A": "IN", "GND": "GND", "B": "OUT"}
            for pin_name, (local_x, local_y, spin) in leaf_pin_points.items():
                delta_x, delta_y = transform_symbol_point_to_schematic(
                    local_x, local_y, 270
                )
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
                    raise AssertionError(
                        f"Second-level label {leaf_pin_map[pin_name]} is not at the rotated "
                        f"leaf pin endpoint {expected_point}."
                    )
                if int(matching[0].spin_style) != expected_spin:
                    raise AssertionError(
                        f"Second-level label {leaf_pin_map[pin_name]} has the wrong spin style."
                    )
        finally:
            child_kicad.close()

        child_schematics = sorted(path.name for path in child.path.glob("*.kicad_sch"))
        if child_schematics != ["implementation.kicad_sch"]:
            raise AssertionError(
                "Second-level composition generated a child schematic file: "
                + repr(child_schematics)
            )
        child_svg_dir = Path(temp) / "nested-child-svg"
        child_svg_dir.mkdir()
        subprocess.run(
            [
                cli,
                "sch",
                "export",
                "svg",
                "--output",
                str(child_svg_dir),
                str(child.implementation),
            ],
            check=True,
        )
        if not list(child_svg_dir.glob("*.svg")):
            raise AssertionError("KiCad CLI could not export the second-level implementation.")
        print(
            "Nested block composition passed: two recursive levels (90° child and 270° leaf facades), "
            "zero hierarchical sheets and no copied child schematic."
        )



if __name__ == "__main__":
    main()
