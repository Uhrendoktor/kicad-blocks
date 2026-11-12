"""Create opaque block assets from ordinary KiCad hierarchical sheets.

The selected sheet's hierarchical labels define the external block interface.
Its nested hierarchical sheets remain internal and are copied into the block
asset tree with their relative references and hierarchy paths rebased.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from .model import BlockPin
from .pcb_layout import _all, _direct, _dump, _head, _Quoted, parse_sexpr

_LABEL_SHAPES = {
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "tri_state": "tristate",
    "passive": "passive",
}
_PIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_./+-]*\\Z")


def _parse_selection(selection_fragment: str) -> list:
    """Parse the clipboard-format fragment returned by Schematic.get_selection_as_string."""
    fragment = str(selection_fragment or "").strip()
    if not fragment:
        raise ValueError(
            "Select exactly one hierarchical sheet symbol in the parent schematic."
        )
    try:
        wrapper = parse_sexpr(f"(selection {fragment})")
    except ValueError as exc:
        raise ValueError(f"KiCad returned an invalid selected-item fragment: {exc}") from exc
    if _head(wrapper) != "selection":
        raise ValueError("KiCad returned an unexpected selected-item format.")
    return wrapper[1:]


def _selected_sheet_node(selection_fragment: str) -> list:
    nodes = _parse_selection(selection_fragment)
    if len(nodes) != 1 or _head(nodes[0]) != "sheet":
        raise ValueError(
            "Select exactly one hierarchical sheet symbol. The block will be built from "
            "that sheet's complete subschematic."
        )
    return nodes[0]


def selected_sheet_file(selection_fragment: str, project_directory: Path) -> Path:
    """Resolve the Sheetfile path from exactly one selected hierarchical sheet symbol."""
    sheet = _selected_sheet_node(selection_fragment)
    sheet_file = None
    for prop in _all(sheet, "property"):
        if len(prop) >= 3 and str(prop[1]) == "Sheetfile":
            sheet_file = str(prop[2]).strip()
            break
    if not sheet_file:
        raise ValueError("The selected hierarchical sheet has no Sheetfile property.")

    candidate = Path(sheet_file).expanduser()
    if not candidate.is_absolute():
        candidate = Path(project_directory).expanduser() / candidate
    candidate = candidate.resolve()
    if candidate.suffix.casefold() != ".kicad_sch" or not candidate.is_file():
        raise FileNotFoundError(
            f"The selected sheet schematic does not exist or is not a .kicad_sch file: {candidate}"
        )
    return candidate


def selected_sheet_instance_uuid(selection_fragment: str) -> str:
    """Return the selected root sheet symbol UUID to disambiguate reused Sheetfile paths."""
    sheet = _selected_sheet_node(selection_fragment)
    node = _direct(sheet, "uuid")
    if node is None or len(node) < 2:
        raise ValueError("The selected hierarchical sheet has no UUID.")
    return str(node[1])


def extract_hierarchical_pins(schematic_tree: list) -> list[BlockPin]:
    """Derive facade pins from labels on the selected block's root sheet only.

    Identically named root-level hierarchical labels represent one interface signal.
    Labels in nested child sheets are internal to the block and do not become facade pins.
    """
    if _head(schematic_tree) != "kicad_sch":
        raise ValueError("The selected source file is not a KiCad schematic document.")
    labels = _all(schematic_tree, "hierarchical_label")
    if not labels:
        raise ValueError(
            "The selected sheet has no hierarchical labels. Add hierarchical labels "
            "to every signal that should appear as a facade pin, then create the block."
        )

    pins: list[BlockPin] = []
    by_name: dict[str, BlockPin] = {}
    for label in labels:
        if len(label) < 2:
            raise ValueError("A hierarchical label has no name.")
        name = str(label[1]).strip()
        if not _PIN_NAME.fullmatch(name):
            raise ValueError(
                f"Hierarchical label {name!r} cannot be used as a facade pin name. "
                "Use letters, numbers, _, ., /, + or -."
            )

        shape_node = _direct(label, "shape")
        if shape_node is None or len(shape_node) < 2:
            raise ValueError(f"Hierarchical label {name!r} has no shape.")
        shape = str(shape_node[1])
        electrical_type = _LABEL_SHAPES.get(shape)
        if electrical_type is None:
            raise ValueError(
                f"Hierarchical label {name!r} uses unsupported shape {shape!r}. "
                f"Supported shapes: {', '.join(_LABEL_SHAPES)}."
            )

        key = name.casefold()
        previous = by_name.get(key)
        if previous is not None:
            if previous.name != name:
                raise ValueError(
                    f"Hierarchical labels {previous.name!r} and {name!r} differ only by "
                    "capitalization; facade pin names must be unique."
                )
            if previous.electrical_type != electrical_type:
                raise ValueError(
                    f"Hierarchical label {name!r} has conflicting shapes "
                    f"{previous.electrical_type!r} and {electrical_type!r}."
                )
            continue

        if len(pins) >= 64:
            raise ValueError("The MVP supports up to 64 facade pins per block.")
        pin = BlockPin(name, electrical_type, str(len(pins) + 1))
        by_name[key] = pin
        pins.append(pin)

    return pins


def _join_hierarchy_path(parent: str, child_uuid: str) -> str:
    parent = str(parent or "/").strip()
    prefix = parent.rstrip("/")
    return f"{prefix}/{child_uuid}" if prefix else f"/{child_uuid}"


def _path_matches(path_value: str, prefix: str) -> bool:
    prefix = prefix.rstrip("/") or "/"
    return path_value == prefix or (
        prefix != "/" and path_value.startswith(prefix + "/")
    )


def _rebase_path_value(path_value: str, old_prefix: str, new_prefix: str) -> str:
    if not _path_matches(path_value, old_prefix):
        return new_prefix
    normalized_old = old_prefix.rstrip("/") or "/"
    suffix = path_value[len(normalized_old):]
    if new_prefix == "/":
        return "/" + suffix.lstrip("/")
    return new_prefix.rstrip("/") + suffix


def _path_nodes(node: list) -> list[list]:
    return [child for child in node[1:] if _head(child) == "path"]


def _normalize_instance_node(
    node: list,
    project_name: str,
    old_prefix: str,
    new_prefix: str,
    *,
    fallback_reference: bool = False,
) -> None:
    instances = _direct(node, "instances")
    if instances is None:
        if not fallback_reference:
            return
        reference = None
        for prop in _all(node, "property"):
            if len(prop) >= 3 and str(prop[1]) == "Reference":
                reference = str(prop[2])
                break
        if not reference:
            return
        unit_node = _direct(node, "unit")
        unit = str(unit_node[1]) if unit_node is not None and len(unit_node) >= 2 else "1"
        node.append([
            "instances",
            ["project", _Quoted(project_name),
             ["path", _Quoted(new_prefix), ["reference", _Quoted(reference)], ["unit", unit]]],
        ])
        return

    projects = [item for item in instances[1:] if _head(item) == "project"]
    for project in projects:
        if len(project) >= 2:
            project[1] = _Quoted(project_name)
        paths = _path_nodes(project)
        if paths:
            matching = [
                path for path in paths
                if len(path) >= 2 and _path_matches(str(path[1]), old_prefix)
            ]
            kept = matching or paths[:1]
            kept_ids = {id(path) for path in kept}
            project[1:] = [
                child for child in project[1:]
                if _head(child) != "path" or id(child) in kept_ids
            ]
            for path in kept:
                if len(path) >= 2:
                    path[1] = _Quoted(
                        _rebase_path_value(str(path[1]), old_prefix, new_prefix)
                    )
    if fallback_reference and not projects:
        node.append([
            "instances",
            ["project", _Quoted(project_name),
             ["path", _Quoted(new_prefix)]],
        ])


def _sheet_instance_paths(tree: list) -> list[list]:
    instances = _direct(tree, "sheet_instances")
    if instances is None:
        return []
    return _path_nodes(instances)


def _select_root_source_prefix(tree: list, selected_sheet_uuid: str | None) -> str:
    paths = _sheet_instance_paths(tree)
    if not paths:
        return "/"
    values = [str(path[1]) for path in paths if len(path) >= 2]
    if selected_sheet_uuid:
        suffix = "/" + str(selected_sheet_uuid).strip("/")
        selected = [value for value in values if value.endswith(suffix)]
        if selected:
            return selected[0]
    return values[0] if values else "/"


def _normalize_sheet_instances(
    tree: list,
    old_prefix: str,
    new_prefix: str,
    *,
    is_root: bool,
) -> None:
    instances = _direct(tree, "sheet_instances")
    if instances is None:
        instances = ["sheet_instances"]
        tree.append(instances)
    paths = _path_nodes(instances)
    matching = [
        path for path in paths
        if len(path) >= 2 and _path_matches(str(path[1]), old_prefix)
    ]
    kept = matching or paths[:1]
    if paths:
        kept_ids = {id(path) for path in kept}
        instances[1:] = [
            child for child in instances[1:]
            if _head(child) != "path" or id(child) in kept_ids
        ]
    else:
        kept = [["path", _Quoted(new_prefix), ["page", _Quoted("1" if is_root else "2")]]]
        instances.extend(kept)
    for path in kept:
        if len(path) >= 2:
            path[1] = _Quoted(new_prefix)
        if is_root:
            page = _direct(path, "page")
            if page is None:
                path.append(["page", _Quoted("1")])
            elif len(page) >= 2:
                page[1] = _Quoted("1")


def _sheet_file_property(sheet: list) -> list:
    for prop in _all(sheet, "property"):
        if len(prop) >= 3 and str(prop[1]) == "Sheetfile":
            return prop
    raise ValueError("A nested hierarchical sheet has no Sheetfile property.")


def _resolve_nested_sheet(parent_file: Path, sheetfile: str) -> Path:
    candidate = Path(sheetfile).expanduser()
    if not candidate.is_absolute():
        candidate = parent_file.parent / candidate
    candidate = candidate.resolve()
    if candidate.suffix.casefold() != ".kicad_sch" or not candidate.is_file():
        raise FileNotFoundError(
            f"Nested Sheetfile does not resolve to an existing .kicad_sch file: {candidate}"
        )
    return candidate


def _prepare_sheet_tree(
    source_file: Path,
    tree: list,
    display_name: str,
    block_id: str,
    project_name: str,
    old_prefix: str,
    new_prefix: str,
    nested_assets: dict[str, str],
    ancestry: set[Path],
    *,
    is_root: bool,
) -> str:
    source_file = Path(source_file).resolve()
    if source_file in ancestry:
        cycle = " -> ".join(path.name for path in (*ancestry, source_file))
        raise ValueError(f"Nested hierarchical sheet cycle detected: {cycle}")
    if _head(tree) != "kicad_sch":
        raise ValueError(f"Source file is not a KiCad schematic: {source_file}")

    ancestry = set(ancestry)
    ancestry.add(source_file)

    document_uuid = _direct(tree, "uuid")
    if document_uuid is not None and len(document_uuid) >= 2:
        document_uuid[1] = _Quoted(str(uuid.uuid4()))

    if is_root:
        title_block = _direct(tree, "title_block")
        if title_block is not None:
            title = _direct(title_block, "title")
            if title is None:
                title_block.append(["title", _Quoted(display_name)])
            elif len(title) >= 2:
                title[1] = _Quoted(display_name)
            comment = next(
                (
                    item for item in title_block[1:]
                    if _head(item) == "comment" and len(item) >= 3
                    and str(item[1]) == "1"
                ),
                None,
            )
            if comment is None:
                title_block.append(["comment", "1", _Quoted("Block ID: " + block_id)])
            else:
                comment[2] = _Quoted("Block ID: " + block_id)

    for node in tree[1:]:
        if _head(node) == "symbol":
            _normalize_instance_node(
                node, project_name, old_prefix, new_prefix, fallback_reference=True
            )
        elif _head(node) == "sheet":
            _normalize_instance_node(
                node, project_name, old_prefix, new_prefix, fallback_reference=False
            )

    _normalize_sheet_instances(tree, old_prefix, new_prefix, is_root=is_root)

    for sheet in [node for node in tree[1:] if _head(node) == "sheet"]:
        sheet_uuid_node = _direct(sheet, "uuid")
        if sheet_uuid_node is None or len(sheet_uuid_node) < 2:
            original_sheet_uuid = str(uuid.uuid4())
            sheet_uuid_node = ["uuid", _Quoted(original_sheet_uuid)]
            sheet.append(sheet_uuid_node)
        else:
            original_sheet_uuid = str(sheet_uuid_node[1])

        replacement_uuid = str(uuid.uuid4())
        sheet_uuid_node[1] = _Quoted(replacement_uuid)
        old_child_prefix = _join_hierarchy_path(old_prefix, original_sheet_uuid)
        new_child_prefix = _join_hierarchy_path(new_prefix, replacement_uuid)

        sheetfile_node = _sheet_file_property(sheet)
        nested_source = _resolve_nested_sheet(source_file, str(sheetfile_node[2]))
        file_name = f"implementation_sheet_{replacement_uuid.replace('-', '')}.kicad_sch"
        sheetfile_node[2] = _Quoted(file_name)

        try:
            nested_tree = parse_sexpr(nested_source.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"Could not read nested sheet {nested_source}: {exc}") from exc
        nested_content = _prepare_sheet_tree(
            nested_source,
            nested_tree,
            display_name,
            block_id,
            project_name,
            old_child_prefix,
            new_child_prefix,
            nested_assets,
            ancestry,
            is_root=False,
        )
        nested_assets[file_name] = nested_content

    return _dump(tree) + "\n"


def prepare_block_implementation(
    source_schematic: Path,
    display_name: str,
    block_id: str,
    project_name: str,
    selected_sheet_uuid: str | None = None,
) -> tuple[list[BlockPin], str, dict[str, str]]:
    """Detach the selected sheet and copy its complete nested hierarchy into the block.

    Only root-level hierarchical labels define facade pins. Child sheets, labels and
    components are preserved as internal implementation assets. Every copied child
    Sheetfile is rewritten to a sibling file owned by the block.
    """
    source_schematic = Path(source_schematic).expanduser().resolve()
    try:
        tree = parse_sexpr(source_schematic.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Could not read source sheet {source_schematic}: {exc}") from exc
    if _head(tree) != "kicad_sch":
        raise ValueError(f"Source file is not a KiCad schematic: {source_schematic}")

    pins = extract_hierarchical_pins(tree)
    old_root_prefix = _select_root_source_prefix(tree, selected_sheet_uuid)
    nested_assets: dict[str, str] = {}
    implementation_content = _prepare_sheet_tree(
        source_schematic,
        tree,
        display_name,
        block_id,
        project_name,
        old_root_prefix,
        "/",
        nested_assets,
        set(),
        is_root=True,
    )
    return pins, implementation_content, nested_assets
