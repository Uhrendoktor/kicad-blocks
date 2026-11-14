"""Filesystem-backed block library, local copies, synchronization and nesting."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path

from .extraction import extract_hierarchical_pins, prepare_block_implementation
from .model import (
    BlockPin,
    normalize_parameters,
    normalize_profiles,
    render_implementation_schematic,
    render_symbol_library,
    resolve_variant_implementation,
    revision_for_hashes,
    sha256_file,
    slugify,
)
from .pcb_layout import _all, _direct, _dump, _head, _Quoted, parse_sexpr

MANIFEST_NAME = "manifest.json"
SYMBOL_NAME = "block.kicad_sym"
IMPLEMENTATION_NAME = "implementation.kicad_sch"
LAYOUT_NAME = "layout.kicad_pcb"
LAYOUT_PROJECT_NAME = "layout.kicad_pro"
LIBRARY_NICKNAME = "KBlocks"
LIBRARY_CATALOG_NAME = "KBlocks.kicad_sym"
LOCAL_NICKNAME_PREFIX = "KBLocal_"  # Reserved for migration of pre-catalog projects.
INSTANCE_LINKS_NAME = "instances.json"


@dataclass(frozen=True, slots=True)
class BlockOccurrence:
    """One specific occurrence of a block in a nested implementation tree."""

    record: BlockRecord
    instance_path: tuple[str, ...] = ()
    instance_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BlockRecord:
    path: Path
    manifest: dict

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def symbol_library(self) -> Path:
        """Stable facade catalog shared by every block in this library."""
        if self.manifest.get("local_copy") and self.manifest.get("source_path"):
            library_root = Path(self.manifest["source_path"]).expanduser().resolve().parent
        else:
            library_root = self.path.parent
        return library_root / str(self.manifest.get("catalog_file", LIBRARY_CATALOG_NAME))

    @property
    def implementation(self) -> Path:
        return self.path / resolve_variant_implementation(self.manifest, "")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def merge_symbol_catalog(library_root: Path, symbol_file: Path) -> Path:
    """Insert/replace one facade symbol in the library's stable KiCad catalog."""
    library_root = Path(library_root).expanduser().resolve()
    symbol_file = Path(symbol_file).expanduser().resolve()
    if not symbol_file.is_file():
        raise FileNotFoundError(symbol_file)
    catalog = library_root / LIBRARY_CATALOG_NAME
    incoming = parse_sexpr(symbol_file.read_text(encoding="utf-8"))
    incoming_symbols = [item for item in incoming[1:] if _head(item) == "symbol"]
    if _head(incoming) != "kicad_symbol_lib" or len(incoming_symbols) != 1:
        raise ValueError(f"Expected one KiCad facade symbol in {symbol_file}.")
    symbol = incoming_symbols[0]
    symbol_name = str(symbol[1])
    if catalog.is_file():
        existing = parse_sexpr(catalog.read_text(encoding="utf-8"))
        if _head(existing) != "kicad_symbol_lib":
            raise ValueError(f"Invalid KiCad symbol catalog: {catalog}")
    else:
        existing = incoming
    existing_symbols = [
        (index, item)
        for index, item in enumerate(existing[1:], start=1)
        if _head(item) == "symbol"
    ]
    match = next(
        (index for index, item in existing_symbols if len(item) > 1 and str(item[1]) == symbol_name),
        None,
    )
    if match is None:
        existing.append(symbol)
    else:
        existing[match] = symbol
    atomic_write(catalog, _dump(existing) + "\n")
    return catalog


def _named_symbol_node(symbol_library: list, symbol_name: str, source: Path) -> list:
    """Return exactly one named symbol from a parsed KiCad symbol library."""
    if _head(symbol_library) != "kicad_symbol_lib":
        raise ValueError(f"Invalid KiCad symbol library: {source}")
    matches = [
        item for item in _all(symbol_library, "symbol")
        if len(item) >= 2 and str(item[1]) == symbol_name
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one {symbol_name!r} symbol in {source}; found {len(matches)}."
        )
    return matches[0]


def _symbol_definition_digest(symbol: list) -> str:
    return hashlib.sha256(_dump(symbol).encode("utf-8")).hexdigest()


def _symbol_definition_digest_from_file(path: Path, symbol_name: str) -> str:
    tree = parse_sexpr(Path(path).read_text(encoding="utf-8"))
    return _symbol_definition_digest(_named_symbol_node(tree, symbol_name, Path(path)))


def synchronize_project_symbol_catalog(
    project_catalog: Path,
    source_symbol_file: Path,
    symbol_name: str,
    *,
    baseline_digest: str | None = None,
) -> bool:
    """Refresh one unchanged project-catalog symbol from its library source.

    A small sidecar records the source digest last synchronized into this
    project. If the project symbol differs from that digest, it is treated as
    a project-local edit and is never overwritten. Older catalogs without a
    sidecar use the block manifest's published digest as their baseline.
    """
    project_catalog = Path(project_catalog).expanduser().resolve()
    source_symbol_file = Path(source_symbol_file).expanduser().resolve()
    if project_catalog.name != LIBRARY_CATALOG_NAME:
        raise ValueError(
            f"Project symbol catalog must be named {LIBRARY_CATALOG_NAME!r}."
        )
    if not source_symbol_file.is_file():
        raise FileNotFoundError(source_symbol_file)

    source_tree = parse_sexpr(source_symbol_file.read_text(encoding="utf-8"))
    source_symbol = _named_symbol_node(source_tree, symbol_name, source_symbol_file)
    source_digest = _symbol_definition_digest(source_symbol)

    state_file = project_catalog.with_name(f"{project_catalog.stem}.sync.json")
    if state_file.is_file():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid project symbol synchronization state: {state_file}"
            ) from exc
        if not isinstance(state, dict) or not isinstance(state.get("symbols"), dict):
            raise ValueError(
                f"Invalid project symbol synchronization state: {state_file}"
            )
    else:
        state = {"schema_version": 1, "symbols": {}}
    synchronized = state["symbols"]

    def save_state() -> None:
        atomic_write(
            state_file,
            json.dumps(state, indent=2, ensure_ascii=False) + "\n",
        )

    if not project_catalog.is_file():
        merge_symbol_catalog(project_catalog.parent, source_symbol_file)
        synchronized[symbol_name] = source_digest
        save_state()
        return True

    catalog_tree = parse_sexpr(project_catalog.read_text(encoding="utf-8"))
    project_matches = [
        item for item in _all(catalog_tree, "symbol")
        if len(item) >= 2 and str(item[1]) == symbol_name
    ]
    if not project_matches:
        merge_symbol_catalog(project_catalog.parent, source_symbol_file)
        synchronized[symbol_name] = source_digest
        save_state()
        return True
    if len(project_matches) != 1:
        raise ValueError(
            f"Expected exactly one {symbol_name!r} symbol in {project_catalog}; "
            f"found {len(project_matches)}."
        )

    project_digest = _symbol_definition_digest(project_matches[0])
    if project_digest == source_digest:
        if synchronized.get(symbol_name) != source_digest:
            synchronized[symbol_name] = source_digest
            save_state()
        return False

    previous_digest = synchronized.get(symbol_name)
    if previous_digest is None:
        previous_digest = baseline_digest
    if previous_digest and project_digest == str(previous_digest):
        # Only this symbol is replaced; other project-local symbols remain intact.
        merge_symbol_catalog(project_catalog.parent, source_symbol_file)
        synchronized[symbol_name] = source_digest
        save_state()
        return True

    # The project entry diverged from the last synchronized definition. Keep
    # that project-local edit; a later publish can make it the new source.
    return False


def _import_catalog_facade_edit(block: BlockRecord, manifest: dict) -> bool:
    """Import a Symbol Editor catalog edit into the block's revisioned facade asset.

    KiCad edits the shared KBlocks catalog. The per-block .kicad_sym file is the
    revisioned asset used by Publish and synchronization. Detect which side changed
    since the last published baseline and reject divergent edits instead of silently
    replacing either version.
    """
    symbol_file = str(manifest["symbol_file"])
    symbol_name = str(manifest.get("symbol_name") or "")
    asset_path = block.path / symbol_file
    catalog_path = block.path.parent / str(
        manifest.get("catalog_file", LIBRARY_CATALOG_NAME)
    )
    if not catalog_path.is_file():
        return False
    if not asset_path.is_file():
        raise FileNotFoundError(f"Block facade asset does not exist: {asset_path}")

    catalog_tree = parse_sexpr(catalog_path.read_text(encoding="utf-8"))
    asset_tree = parse_sexpr(asset_path.read_text(encoding="utf-8"))
    catalog_symbol = _named_symbol_node(catalog_tree, symbol_name, catalog_path)
    asset_symbol = _named_symbol_node(asset_tree, symbol_name, asset_path)
    catalog_digest = _symbol_definition_digest(catalog_symbol)
    asset_digest = _symbol_definition_digest(asset_symbol)
    if catalog_digest == asset_digest:
        return False

    baseline_asset_hash = (manifest.get("hashes") or {}).get(symbol_file)
    asset_changed = (
        baseline_asset_hash is not None
        and sha256_file(asset_path) != str(baseline_asset_hash)
    )
    baseline_catalog_digest = manifest.get("catalog_symbol_hash")
    if baseline_catalog_digest is None:
        # Older manifests did not track the catalog symbol separately. If the
        # asset is unchanged, a catalog-only difference is a Symbol Editor edit.
        # If both sides differ, the origin cannot be established safely.
        if asset_changed:
            raise RuntimeError(
                "Facade symbol conflict: both the shared KBlocks catalog and the "
                "per-block facade asset differ, but this block has no catalog baseline. "
                "Reconcile the two symbol definitions manually before publishing."
            )
        catalog_changed = True
    else:
        catalog_changed = catalog_digest != str(baseline_catalog_digest)

    if catalog_changed and asset_changed:
        raise RuntimeError(
            "Facade symbol conflict: both the shared KBlocks catalog and the "
            "per-block facade asset changed since the last publish. Reconcile the "
            "two symbol definitions manually; neither version was overwritten."
        )
    if not catalog_changed:
        # The block asset changed while the catalog is still at its baseline.
        # Publish will update the shared catalog from the asset below.
        return False

    standalone = copy.deepcopy(catalog_tree)
    standalone[:] = [
        child for child in standalone
        if _head(child) != "symbol"
    ]
    standalone.append(copy.deepcopy(catalog_symbol))
    atomic_write(asset_path, _dump(standalone) + "\n")
    return True


def import_project_catalog_facade_edit(
    block: BlockRecord,
    project_catalog: Path,
) -> bool:
    """Import a project Symbol Editor edit into one block's revisioned facade asset.

    KiCad edits the consuming project's combined KBlocks catalog. The shared
    library catalog and per-block symbol file are the published source of truth.
    Compare both against the last-published baseline and reject stale or
    concurrent edits rather than silently overwriting either side.
    """
    project_catalog = Path(project_catalog).expanduser().resolve()
    if not project_catalog.is_file():
        return False

    manifest = read_manifest(block.path)
    symbol_file = str(manifest["symbol_file"])
    symbol_name = str(manifest.get("symbol_name") or "")
    asset_path = block.path / symbol_file
    shared_catalog_path = block.path.parent / str(
        manifest.get("catalog_file", LIBRARY_CATALOG_NAME)
    )
    if not shared_catalog_path.is_file():
        raise FileNotFoundError(
            f"Shared facade catalog does not exist: {shared_catalog_path}"
        )
    if not asset_path.is_file():
        raise FileNotFoundError(f"Block facade asset does not exist: {asset_path}")

    project_tree = parse_sexpr(project_catalog.read_text(encoding="utf-8"))
    shared_tree = parse_sexpr(shared_catalog_path.read_text(encoding="utf-8"))
    asset_tree = parse_sexpr(asset_path.read_text(encoding="utf-8"))
    project_symbol = _named_symbol_node(project_tree, symbol_name, project_catalog)
    shared_symbol = _named_symbol_node(
        shared_tree, symbol_name, shared_catalog_path
    )
    asset_symbol = _named_symbol_node(asset_tree, symbol_name, asset_path)
    project_digest = _symbol_definition_digest(project_symbol)
    shared_digest = _symbol_definition_digest(shared_symbol)
    asset_digest = _symbol_definition_digest(asset_symbol)

    baseline_catalog_digest = manifest.get("catalog_symbol_hash")
    baseline_asset_hash = (manifest.get("hashes") or {}).get(symbol_file)
    if baseline_catalog_digest is None or baseline_asset_hash is None:
        if project_digest == shared_digest == asset_digest:
            return False
        raise RuntimeError(
            "Cannot safely import the edited project facade because this block "
            "has no published catalog baseline. Reconcile the project catalog, "
            "shared catalog, and standalone facade asset manually before publishing."
        )

    baseline_catalog_digest = str(baseline_catalog_digest)
    project_changed = project_digest != baseline_catalog_digest
    shared_changed = shared_digest != baseline_catalog_digest
    asset_changed = sha256_file(asset_path) != str(baseline_asset_hash)

    if not project_changed:
        return False
    if shared_changed and project_digest != shared_digest:
        raise RuntimeError(
            "Facade symbol conflict: the project catalog and shared library both "
            "changed since the last publish. Refresh the project catalog and resolve "
            "the conflicting symbol edits before publishing."
        )
    if asset_changed and project_digest != asset_digest:
        raise RuntimeError(
            "Facade symbol conflict: the project catalog and the block's standalone "
            "facade asset both changed since the last publish. Reconcile those edits "
            "manually; neither version was overwritten."
        )

    standalone = copy.deepcopy(project_tree)
    standalone[:] = [
        child for child in standalone
        if _head(child) != "symbol"
    ]
    standalone.append(copy.deepcopy(project_symbol))
    atomic_write(asset_path, _dump(standalone) + "\n")
    return True



def _save_catalog_symbol_baseline(block: BlockRecord, manifest: dict) -> None:
    """Record the exact facade definition currently published in the shared catalog."""
    catalog_path = block.path.parent / str(
        manifest.get("catalog_file", LIBRARY_CATALOG_NAME)
    )
    if not catalog_path.is_file():
        return
    digest = _symbol_definition_digest_from_file(
        catalog_path, str(manifest.get("symbol_name") or "")
    )
    if manifest.get("catalog_symbol_hash") == digest:
        return
    manifest["catalog_symbol_hash"] = digest
    atomic_write(
        block.path / MANIFEST_NAME,
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )


def rename_block_symbol_asset(block_path: Path, new_symbol_name: str) -> None:
    """Rename a legacy single-symbol asset and its unit sub-symbol identifiers."""
    block_path = Path(block_path).expanduser().resolve()
    manifest = read_manifest(block_path)
    symbol_path = block_path / manifest["symbol_file"]
    root = parse_sexpr(symbol_path.read_text(encoding="utf-8"))
    symbols = [item for item in root[1:] if _head(item) == "symbol"]
    if _head(root) != "kicad_symbol_lib" or len(symbols) != 1:
        raise ValueError(f"Expected one facade symbol in {symbol_path}.")
    symbol = symbols[0]
    old_name = str(symbol[1])
    symbol[1] = type(symbol[1])(new_symbol_name) if isinstance(symbol[1], str) else new_symbol_name
    for child in symbol[1:]:
        if _head(child) == "symbol" and len(child) > 1:
            child_name = str(child[1])
            if child_name.startswith(old_name + "_"):
                suffix = child_name[len(old_name):]
                child[1] = type(child[1])(new_symbol_name + suffix) if isinstance(child[1], str) else new_symbol_name + suffix
    atomic_write(symbol_path, _dump(root) + "\n")


def ensure_stable_block_identity(block: BlockRecord) -> BlockRecord:
    """Migrate an old per-block nickname into the shared KBlocks catalog."""
    manifest = read_manifest(block.path)
    if (
        manifest.get("library_nickname") != LIBRARY_NICKNAME
        or manifest.get("catalog_file") != LIBRARY_CATALOG_NAME
    ):
        suffix = str(manifest["block_id"]).replace("-", "")[:8].lower()
        desired_name = f"{manifest['slug']}_{suffix}"
        if manifest.get("symbol_name") != desired_name:
            rename_block_symbol_asset(block.path, desired_name)
            manifest = read_manifest(block.path)
            manifest["symbol_name"] = desired_name
        manifest["library_nickname"] = LIBRARY_NICKNAME
        manifest["catalog_file"] = LIBRARY_CATALOG_NAME
        hashes = _asset_hashes(block.path, manifest)
        manifest["files"] = sorted(hashes)
        manifest["hashes"] = hashes
        manifest["revision"] = revision_for_hashes(hashes)
        manifest["version"] = int(manifest.get("version", 1)) + 1
        atomic_write(
            block.path / MANIFEST_NAME,
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        )
    merge_symbol_catalog(block.path.parent, block.path / manifest["symbol_file"])
    _save_catalog_symbol_baseline(block, read_manifest(block.path))
    return _record(block.path)


def read_manifest(block_path: Path) -> dict:
    manifest_path = block_path / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"Block manifest not found: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid block manifest: {manifest_path}: {exc}") from exc
    required = ("block_id", "name", "symbol_file", "implementation_file", "pins")
    missing = [key for key in required if key not in manifest]
    if missing:
        raise ValueError(f"Block manifest is missing fields: {', '.join(missing)}")
    manifest["parameters"] = normalize_parameters(manifest.get("parameters", []))
    interface_source = manifest.get("interface_source", "manual")
    if interface_source not in {"manual", "hierarchical_labels"}:
        raise ValueError(
            f"Invalid block interface source {interface_source!r}; "
            "expected 'manual' or 'hierarchical_labels'."
        )
    manifest["interface_source"] = interface_source
    variants = manifest.get("variants", [])
    if not isinstance(variants, list):
        raise TypeError("Block manifest variants must be a list.")
    seen_variant_values: set[str] = set()
    seen_variant_files: set[str] = set()
    for variant in variants:
        if not isinstance(variant, dict):
            raise TypeError("Each block variant must be an object.")
        value = str(variant.get("value", "")).strip()
        file_name = str(variant.get("file", ""))
        if (
            not value or len(value) > 128 or "\n" in value or "\r" in value
            or Path(file_name).name != file_name
            or Path(file_name).suffix.casefold() != ".kicad_sch"
        ):
            raise ValueError("Each block variant needs a single-line value and a safe .kicad_sch filename.")
        if value.casefold() in seen_variant_values:
            raise ValueError(f"Duplicate block variant value: {value!r}")
        if file_name.casefold() in seen_variant_files:
            raise ValueError(f"Duplicate block variant file: {file_name!r}")
        seen_variant_values.add(value.casefold())
        seen_variant_files.add(file_name.casefold())
    manifest["variants"] = variants
    default_variant = manifest.get("default_variant")
    if default_variant:
        default_value = str(default_variant).strip()
        matching_variant = next(
            (
                item for item in variants
                if str(item["value"]).casefold() == default_value.casefold()
            ),
            None,
        )
        if matching_variant is None:
            choices = ", ".join(str(item["value"]) for item in variants)
            raise ValueError(
                f"Default implementation {default_value!r} is not declared. "
                f"Available variants: {choices or 'base implementation only'}. "
            )
        manifest["default_variant"] = str(matching_variant["value"])
    else:
        manifest.pop("default_variant", None)
    layouts = manifest.get("layouts", [])
    if not isinstance(layouts, list):
        raise TypeError("Block manifest layouts must be a list.")
    pin_items = manifest.get("pins", [])
    if not isinstance(pin_items, list) or not pin_items or any(
        not isinstance(pin, dict) or not str(pin.get("name", "")).strip()
        for pin in pin_items
    ):
        raise ValueError("Block manifest pins must be a non-empty list of named pin objects.")
    pin_names = {str(pin["name"]) for pin in pin_items}
    seen_layout_ids: set[str] = set()
    seen_layout_names: set[str] = set()
    seen_layout_files: set[str] = set()
    for layout in layouts:
        if not isinstance(layout, dict):
            raise TypeError("Each block layout must be an object.")
        layout_id = str(layout.get("id", ""))
        layout_name = str(layout.get("name", "")).strip()
        layout_file = str(layout.get("file", ""))
        if (
            not re.fullmatch(r"[a-z0-9_]+", layout_id)
            or not layout_name
            or len(layout_name) > 80
            or not layout_file
            or Path(layout_file).name != layout_file
            or Path(layout_file).suffix.casefold() != ".kicad_pcb"
        ):
            raise ValueError("Each block layout needs a safe id, display name and .kicad_pcb file.")
        if layout_id.casefold() in seen_layout_ids:
            raise ValueError(f"Duplicate block layout id: {layout_id}")
        if layout_name.casefold() in seen_layout_names:
            raise ValueError(f"Duplicate block layout name: {layout_name}")
        if layout_file.casefold() in seen_layout_files:
            raise ValueError(f"Duplicate block layout file: {layout_file}")
        seen_layout_ids.add(layout_id.casefold())
        seen_layout_names.add(layout_name.casefold())
        seen_layout_files.add(layout_file.casefold())
        project_file = layout.get("project_file")
        if project_file:
            project_file = str(project_file)
            if (
                Path(project_file).name != project_file
                or Path(project_file).suffix.casefold() != ".kicad_pro"
            ):
                raise ValueError(f"Invalid project-settings file name for layout {layout_id!r}.")
        ports = layout.get("ports")
        if ports is not None and (
            not isinstance(ports, dict)
            or set(ports) != pin_names
            or any(not isinstance(value, str) or not value.strip() for value in ports.values())
        ):
            raise ValueError(
                f"Layout {layout_id!r} ports must map every facade pin to a non-empty net name."
            )
    for variant in variants:
        linked_layout = variant.get("layout_id")
        if linked_layout and str(linked_layout).casefold() not in seen_layout_ids:
            raise ValueError(
                f"Variant {variant['value']!r} links to unknown PCB layout {linked_layout!r}."
            )
    default_layout = manifest.get("default_layout")
    if default_layout and str(default_layout).casefold() not in seen_layout_ids:
        raise ValueError(f"Block default layout {default_layout!r} is not defined in layouts.")
    if layouts:
        if not default_layout:
            default_layout = str(layouts[0]["id"])
            manifest["default_layout"] = default_layout
        # Older manifests may omit explicit variant links. Use the default
        # layout as their safe fallback instead of leaving an implementation
        # without an associated board.
        for variant in variants:
            if not variant.get("layout_id"):
                variant["layout_id"] = default_layout

        # Every stored board is always associated with one or more implementations.
        variant_values = {
            str(item["value"]).casefold(): str(item["value"])
            for item in variants
        }
        for layout in layouts:
            layout_id = str(layout["id"]).casefold()
            derived_links = []
            if str(default_layout).casefold() == layout_id:
                derived_links.append("Default")
            derived_links.extend(
                str(item["value"])
                for item in variants
                if str(item.get("layout_id", "")).casefold() == layout_id
            )
            raw_links = layout.get("implementations")
            if raw_links is None:
                links = derived_links
            else:
                if not isinstance(raw_links, list):
                    raise TypeError(
                        f"Layout {layout_id!r} implementation links must be a list."
                    )
                links = []
                for raw_link in raw_links:
                    key = str(raw_link).strip().casefold()
                    if key == "default":
                        link = "Default"
                    elif key in variant_values:
                        link = variant_values[key]
                    else:
                        raise ValueError(
                            f"Layout {layout_id!r} links to unknown implementation {raw_link!r}."
                        )
                    if link not in links:
                        links.append(link)
            if not links:
                links = ["Default"]
            layout["implementations"] = links

    manifest["profiles"] = normalize_profiles(
        manifest.get("profiles", []),
        manifest["parameters"],
        variants,
        layouts,
        default_layout,
    )
    return manifest


def _record(path: Path) -> BlockRecord:
    return BlockRecord(path=path, manifest=read_manifest(path))


def list_blocks(root: Path) -> list[BlockRecord]:
    root = Path(root).expanduser().resolve()
    if not root.exists():
        return []
    records = []
    for manifest_path in root.glob("*.kicad_block/manifest.json"):
        try:
            record = _record(manifest_path.parent)
            if not record.manifest.get("local_copy"):
                records.append(record)
        except (OSError, ValueError):
            continue
    return sorted(records, key=lambda item: item.name.casefold())


def find_block_by_id(root: Path, block_id: str) -> BlockRecord:
    for record in list_blocks(root):
        if record.manifest["block_id"] == block_id:
            return record
    raise FileNotFoundError(f"Block {block_id!r} was not found in {root}")


def create_block(
    root: Path,
    name: str,
    pins: list[BlockPin] | None = None,
    *,
    description: str = "",
    reference_prefix: str = "X",
    source_schematic: Path | None = None,
    source_project_name: str | None = None,
    source_sheet_uuid: str | None = None,
    layout_file: Path | None = None,
    layout_project_file: Path | None = None,
    layout_files: list[dict] | None = None,
    parameters: list[dict] | None = None,
    variant_files: list[dict] | None = None,
    profiles: list[dict] | None = None,
) -> BlockRecord:
    """Create a versioned block with typed parameters and named PCB layouts.

    The legacy layout_file/layout_project_file arguments remain supported.
    New callers can provide layout_files entries with name, path, optional
    project_path, and optional facade-pin-to-layout-net ports.
    """
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not name.strip():
        raise ValueError("Block name cannot be empty.")
    block_id = str(uuid.uuid4())
    implementation_content = None
    implementation_assets: dict[str, str] = {}
    if source_schematic is not None:
        if pins is not None:
            raise ValueError(
                "Do not type or pass a pin list when creating from a hierarchical sheet; "
                "the sheet's hierarchical labels define the facade pins."
            )
        pins, implementation_content, implementation_assets = prepare_block_implementation(
            Path(source_schematic),
            name,
            block_id,
            source_project_name or root.name,
            source_sheet_uuid,
        )
    if not pins:
        raise ValueError(
            "At least one facade pin is required. Create the block from a hierarchical "
            "sheet with hierarchical labels."
        )

    if layout_files is not None and (layout_file is not None or layout_project_file is not None):
        raise ValueError("Use layout_files or the legacy single-layout arguments, not both.")
    if layout_files is None:
        if layout_project_file is not None and layout_file is None:
            raise ValueError("A KiCad project-settings sidecar requires a PCB layout.")
        raw_layouts = (
            [{
                "name": Path(layout_file).stem,
                "path": layout_file,
                "project_path": layout_project_file,
                "legacy": True,
            }]
            if layout_file is not None
            else []
        )
    else:
        if not isinstance(layout_files, list):
            raise ValueError("layout_files must be a list of layout definitions.")
        raw_layouts = layout_files

    parameters = normalize_parameters(parameters)
    if variant_files is not None and not isinstance(variant_files, list):
        raise ValueError("variant_files must be a list of variant definitions.")
    if profiles is not None and not isinstance(profiles, list):
        raise ValueError("profiles must be a list of profile definitions.")
    expected_pins = {pin.name for pin in pins}
    default_layout_id = None
    if raw_layouts:
        first_layout = raw_layouts[0]
        if not isinstance(first_layout, dict):
            raise TypeError("Each PCB layout definition must be an object.")
        first_source = first_layout.get("path", first_layout.get("file"))
        if not first_source:
            raise ValueError("First PCB layout is missing its source PCB path.")
        first_name = str(first_layout.get("name") or Path(first_source).stem).strip()
        default_layout_id = str(first_layout.get("id") or slugify(first_name)).casefold()
        if not re.fullmatch(r"[a-z0-9_]+", default_layout_id):
            raise ValueError(f"Invalid default PCB layout id {default_layout_id!r}.")
    slug = slugify(name)
    suffix = block_id.replace("-", "")[:8].lower()
    symbol_name = f"{slug}_{suffix}"
    folder = root / f"{slug}_{block_id[:8]}.kicad_block"
    staging = Path(tempfile.mkdtemp(prefix=f".{slug}.", dir=root))
    folder_created = False
    try:
        symbol_content = render_symbol_library(
            symbol_name,
            name,
            block_id,
            pins,
            reference_prefix=reference_prefix,
            parameters=parameters,
            default_layout_id=default_layout_id,
            include_profile_field=bool(profiles),
        )
        (staging / SYMBOL_NAME).write_text(symbol_content, encoding="utf-8")
        if implementation_content is None:
            implementation_content = render_implementation_schematic(name, block_id, pins)
        (staging / IMPLEMENTATION_NAME).write_text(
            implementation_content, encoding="utf-8"
        )
        files = [SYMBOL_NAME, IMPLEMENTATION_NAME]
        for nested_file, nested_content in implementation_assets.items():
            (staging / nested_file).write_text(nested_content, encoding="utf-8")
        files.extend(sorted(implementation_assets))
        variants: list[dict[str, str]] = []
        seen_variant_values: set[str] = set()
        seen_variant_files: set[str] = set()
        for index, raw_variant in enumerate(variant_files or [], start=1):
            if not isinstance(raw_variant, dict):
                raise TypeError("Each schematic variant definition must be an object.")
            value = str(raw_variant.get("value", "")).strip()
            source_value = raw_variant.get("path", raw_variant.get("file"))
            if not value or len(value) > 128 or "\n" in value or "\r" in value:
                raise ValueError(f"Variant {index} needs a single-line value of 1 to 128 characters.")
            if not source_value:
                raise ValueError(f"Variant {value!r} is missing its source schematic path.")
            source_path = Path(source_value).expanduser().resolve()
            if not source_path.is_file() or source_path.suffix.casefold() != ".kicad_sch":
                raise ValueError(f"Variant implementation must be an existing .kicad_sch file: {source_path}")
            file_name = f"implementation_{slugify(value)}.kicad_sch"
            if value.casefold() in seen_variant_values:
                raise ValueError(f"Duplicate block variant value: {value!r}")
            if file_name.casefold() in seen_variant_files or file_name == IMPLEMENTATION_NAME:
                raise ValueError(f"Duplicate or reserved variant implementation filename: {file_name!r}")
            seen_variant_values.add(value.casefold())
            seen_variant_files.add(file_name.casefold())
            shutil.copy2(source_path, staging / file_name)
            files.append(file_name)
            variant_descriptor = {"value": value, "file": file_name}
            if raw_variant.get("layout_id"):
                variant_descriptor["layout_id"] = str(raw_variant["layout_id"]).casefold()
            variants.append(variant_descriptor)

        layouts: list[dict] = []
        seen_layout_ids: set[str] = set()
        seen_layout_names: set[str] = set()
        for index, raw_layout in enumerate(raw_layouts, start=1):
            if not isinstance(raw_layout, dict):
                raise TypeError("Each PCB layout definition must be an object.")
            source_value = raw_layout.get("path", raw_layout.get("file"))
            if not source_value:
                raise ValueError(f"Layout {index} is missing its source PCB path.")
            source_path = Path(source_value).expanduser().resolve()
            if not source_path.is_file() or source_path.suffix.casefold() != ".kicad_pcb":
                raise ValueError(f"PCB layout must be an existing .kicad_pcb file: {source_path}")
            layout_name = str(raw_layout.get("name") or source_path.stem).strip()
            if not layout_name or len(layout_name) > 80:
                raise ValueError("PCB layout names must contain 1 to 80 characters.")
            layout_id = str(raw_layout.get("id") or slugify(layout_name)).casefold()
            if not re.fullmatch(r"[a-z0-9_]+", layout_id):
                raise ValueError(f"Invalid layout id {layout_id!r}; use letters, numbers and underscores.")
            if layout_id in seen_layout_ids or layout_name.casefold() in seen_layout_names:
                raise ValueError(f"Duplicate PCB layout name or id: {layout_name!r}.")
            seen_layout_ids.add(layout_id)
            seen_layout_names.add(layout_name.casefold())

            is_legacy = bool(raw_layout.get("legacy"))
            board_name = LAYOUT_NAME if is_legacy else f"layout_{layout_id}.kicad_pcb"
            shutil.copy2(source_path, staging / board_name)
            files.append(board_name)

            project_value = raw_layout.get("project_path", raw_layout.get("project_file"))
            project_name = None
            if project_value:
                project_path = Path(project_value).expanduser().resolve()
                if not project_path.is_file() or project_path.suffix.casefold() != ".kicad_pro":
                    raise ValueError(f"PCB project settings must be an existing .kicad_pro file: {project_path}")
                try:
                    project_data = json.loads(project_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise ValueError(f"Could not read KiCad project settings JSON: {exc}") from exc
                if not isinstance(project_data, dict):
                    raise ValueError("KiCad project settings must contain a JSON object.")
                project_name = LAYOUT_PROJECT_NAME if is_legacy else f"layout_{layout_id}.kicad_pro"
                shutil.copy2(project_path, staging / project_name)
                files.append(project_name)

            ports = raw_layout.get("ports")
            if ports is not None:
                if not isinstance(ports, dict) or set(ports) != expected_pins:
                    raise ValueError(
                        f"Layout {layout_name!r} ports must map every block facade pin exactly once."
                    )
                if any(not isinstance(value, str) or not value.strip() for value in ports.values()):
                    raise ValueError(f"Layout {layout_name!r} port mappings cannot contain empty net names.")

            descriptor = {
                "id": layout_id,
                "name": layout_name,
                "file": board_name,
                "project_file": project_name,
            }
            if ports is not None:
                descriptor["ports"] = {str(key): str(value) for key, value in ports.items()}
            layouts.append(descriptor)

        hashes = {file_name: sha256_file(staging / file_name) for file_name in files}
        first_layout = layouts[0] if layouts else {}
        manifest = {
            "schema_version": 1,
            "block_id": block_id,
            "name": name.strip(),
            "slug": slug,
            "description": description.strip(),
            "version": 1,
            "revision": revision_for_hashes(hashes),
            "created_by": "kicad-blocks",
            "interface_source": (
                "hierarchical_labels" if source_schematic is not None else "manual"
            ),
            "library_nickname": LIBRARY_NICKNAME,
            "symbol_name": symbol_name,
            "catalog_file": LIBRARY_CATALOG_NAME,
            "symbol_file": SYMBOL_NAME,
            "implementation_file": IMPLEMENTATION_NAME,
            "layout_file": first_layout.get("file"),
            "layout_project_file": first_layout.get("project_file"),
            "layouts": layouts,
            "default_layout": first_layout.get("id") if layouts else None,
            "reference_prefix": reference_prefix,
            "pins": [pin.as_manifest(index + 1) for index, pin in enumerate(pins)],
            "parameters": parameters,
            "variants": variants,
            "profiles": normalize_profiles(
                profiles, parameters, variants, layouts,
                first_layout.get("id") if layouts else None,
            ),
            "dependencies": [],
            "files": files,
            "hashes": hashes,
        }
        (staging / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        if folder.exists():
            raise FileExistsError(folder)
        os.replace(staging, folder)
        folder_created = True
        merge_symbol_catalog(root, folder / SYMBOL_NAME)
        created = _record(folder)
        _save_catalog_symbol_baseline(created, created.manifest)
        return _record(folder)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        if folder_created:
            shutil.rmtree(folder, ignore_errors=True)
        raise


def _sexpr_string(value: str) -> str:
    return json.dumps(value.replace("\\", "/"), ensure_ascii=False)


def register_project_symbol_library(
    project_dir: Path,
    nickname: str,
    library_path: Path,
    description: str = "KiCad Blocks",
    *,
    prefer_relative: bool = False,
    replace_existing: bool = False,
) -> Path:
    """Add or update one project-scoped symbol library table row."""
    project_dir = Path(project_dir).expanduser().resolve()
    library_path = Path(library_path).expanduser().resolve()
    if not library_path.is_file():
        raise FileNotFoundError(f"Symbol library file does not exist: {library_path}")
    table_path = project_dir / "sym-lib-table"
    if table_path.exists():
        text = table_path.read_text(encoding="utf-8")
        if not text.lstrip().startswith("(sym_lib_table") or not text.rstrip().endswith(")"):
            raise ValueError(f"Invalid KiCad symbol library table: {table_path}")
    else:
        text = "(sym_lib_table\n)\n"
    pattern = re.compile(
        r"\(lib\s+\(name\s+\"" + re.escape(nickname)
        + r"\"\)(.*?)\)\s*\)",
        re.DOTALL,
    )
    existing = pattern.search(text)
    if prefer_relative:
        uri = "${KIPRJMOD}/" + Path(os.path.relpath(library_path, project_dir)).as_posix()
    else:
        try:
            relative_path = library_path.relative_to(project_dir)
            uri = "${KIPRJMOD}/" + relative_path.as_posix()
        except ValueError:
            uri = library_path.as_posix()
    entry = (
        f"  (lib (name {_sexpr_string(nickname)}) (type \"KiCad\") "
        f"(uri {_sexpr_string(uri)}) (options \"\") (descr {_sexpr_string(description)}))"
    )
    if existing:
        current_uri = re.search(r'\(uri\s+"([^"]+)"\)', existing.group(0))
        if current_uri:
            current_value = current_uri.group(1)
            if current_value.startswith("${KIPRJMOD}/"):
                current_path = (
                    project_dir / current_value[len("${KIPRJMOD}/"):]
                ).resolve()
            else:
                current_path = Path(current_value).expanduser().resolve()
            if current_path == library_path:
                return table_path
        if not replace_existing:
            raise ValueError(
                f"Symbol library nickname {nickname!r} is already used by another path."
            )
        updated = text[:existing.start()] + entry + text[existing.end():]
    else:
        close = text.rfind(")")
        updated = text[:close].rstrip() + "\n" + entry + "\n)\n"
    atomic_write(table_path, updated)
    return table_path


def set_project_symbol_defaults(
    catalog_path: Path, symbol_name: str, values: dict[str, str]
) -> Path:
    """Set default user-field values on one project-catalog facade before placement.

    The shared library remains untouched. KiCad copies these values into each
    newly placed instance; subsequent catalog rebuilds restore library defaults.
    """
    catalog_path = Path(catalog_path).expanduser().resolve()
    tree = parse_sexpr(catalog_path.read_text(encoding="utf-8"))
    symbol = _named_symbol_node(tree, symbol_name, catalog_path)
    properties = [
        item for item in symbol[2:]
        if _head(item) == "property" and len(item) >= 3
    ]
    found = set()
    for prop in properties:
        field_name = str(prop[1])
        if field_name in values:
            prop[2] = _Quoted(str(values[field_name]))
            found.add(field_name)
    missing = sorted(set(values) - found)
    if missing:
        raise ValueError(
            f"Facade {symbol_name!r} is missing configurable field(s): "
            + ", ".join(missing)
        )
    atomic_write(catalog_path, _dump(tree) + "\n")
    return catalog_path


def _asset_hashes(block_path: Path, manifest: dict) -> dict[str, str]:
    result = {}
    for file_name in manifest.get("files", []):
        path = block_path / file_name
        if path.is_file():
            result[file_name] = sha256_file(path)
    return result


_LAYOUT_MANIFEST_KEYS = (
    "layouts",
    "default_layout",
    "layout_file",
    "layout_project_file",
    "layout_ports",
)


def _layout_configuration(manifest: dict) -> dict:
    """Return the manifest fields that define a block's PCB implementations."""
    return {
        key: copy.deepcopy(manifest.get(key))
        for key in _LAYOUT_MANIFEST_KEYS
    }


def _set_layout_configuration(manifest: dict, configuration: dict) -> None:
    """Apply a previously validated PCB layout configuration to a manifest."""
    for key in _LAYOUT_MANIFEST_KEYS:
        value = copy.deepcopy(configuration.get(key))
        if value is None:
            manifest.pop(key, None)
        else:
            manifest[key] = value


def _base_layout_configuration(manifest: dict, source_manifest: dict) -> dict:
    """Load the layout three-way baseline, handling local copies created by older versions."""
    if "base_layouts" in manifest:
        return copy.deepcopy(manifest["base_layouts"])
    if manifest.get("source_version", source_manifest.get("version", 1)) != source_manifest.get(
        "version", 1
    ):
        raise RuntimeError(
            "This local copy predates layout-metadata synchronization and its library source "
            "has since changed. Recreate the local copy from the current library block before "
            "publishing or pulling layout changes."
        )
    return _layout_configuration(manifest)


def make_local_copy(source: BlockRecord, project_dir: Path) -> BlockRecord:
    """Copy a library block to a project-owned independent editable block asset."""
    project_dir = Path(project_dir).expanduser().resolve()
    local_root = project_dir / ".kicad_blocks" / "local"
    local_root.mkdir(parents=True, exist_ok=True)
    local_id = str(uuid.uuid4())
    suffix = local_id.replace("-", "")[:8].upper()
    folder = local_root / f"{source.manifest['slug']}_{suffix}.kicad_block"
    shutil.copytree(source.path, folder)
    manifest = read_manifest(folder)
    base_hashes = _asset_hashes(folder, manifest)
    manifest.update(
        {
            "local_copy": True,
            "local_id": local_id,
            "source_block_id": source.manifest["block_id"],
            "source_path": str(source.path.resolve()),
            "source_version": source.manifest.get("version", 1),
            "source_revision": source.manifest.get("revision"),
            "base_hashes": base_hashes,
            "base_dependencies": source.manifest.get("dependencies", []),
            "base_parameters": source.manifest.get("parameters", []),
            "base_variants": source.manifest.get("variants", []),
            "base_profiles": source.manifest.get("profiles", []),
            "base_layouts": _layout_configuration(source.manifest),
            "library_nickname": source.manifest.get("library_nickname", LIBRARY_NICKNAME),
            "symbol_name": source.manifest.get("symbol_name"),
            "catalog_file": source.manifest.get("catalog_file", LIBRARY_CATALOG_NAME),
        }
    )
    atomic_write(
        folder / MANIFEST_NAME,
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    return _record(folder)


def _instance_links_path(project_dir: Path) -> Path:
    return Path(project_dir).expanduser().resolve() / ".kicad_blocks" / INSTANCE_LINKS_NAME


def link_instance_to_local_copy(
    project_dir: Path, instance_id: str, local_block: BlockRecord
) -> Path:
    """Bind one schematic symbol KIID to its independent project-local block asset."""
    if not local_block.manifest.get("local_copy"):
        raise ValueError("The target block asset is not a project-local copy.")
    instance_id = str(instance_id).strip()
    if not instance_id:
        raise ValueError("A schematic instance KIID is required.")
    path = _instance_links_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {"schema_version": 1, "instances": {}}
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid project block-link metadata: {path}") from exc
    entries = data.setdefault("instances", {})
    entries[instance_id] = {
        "local_path": Path(os.path.relpath(local_block.path, Path(project_dir).resolve())).as_posix(),
        "source_block_id": local_block.manifest.get("source_block_id"),
        "updated_at": __import__("datetime").datetime.now().astimezone().isoformat(),
    }
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return path


def local_copy_for_instance(project_dir: Path, instance_id: str) -> BlockRecord | None:
    """Return the local block bound to a symbol KIID, if any."""
    path = _instance_links_path(project_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid project block-link metadata: {path}") from exc
    entry = data.get("instances", {}).get(str(instance_id).strip())
    if entry is None:
        return None
    local_path = (Path(project_dir).expanduser().resolve() / entry["local_path"]).resolve()
    if not local_path.is_dir():
        raise FileNotFoundError(
            f"Project-local block bound to instance {instance_id} is missing: {local_path}"
        )
    record = _record(local_path)
    if not record.manifest.get("local_copy"):
        raise ValueError(f"Instance {instance_id} points at a non-local block asset.")
    if entry.get("source_block_id") != record.manifest.get("source_block_id"):
        raise ValueError(f"Stale local block-link metadata for instance {instance_id}.")
    return record


def unlink_instance_local_copy(project_dir: Path, instance_id: str) -> None:
    path = _instance_links_path(project_dir)
    if not path.is_file():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    data.get("instances", {}).pop(str(instance_id), None)
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def synchronize_local_copy(local_block: BlockRecord) -> dict:
    """Pull upstream changes unless the same file has conflicting local edits.

    A locally modified file is preserved when upstream has not changed it. If
    both copies changed a file since the last sync, the operation is rejected.
    """
    manifest = local_block.manifest
    if not manifest.get("local_copy"):
        raise ValueError("Selected block is not a local project copy.")
    source_path = Path(manifest["source_path"]).resolve()
    source = _record(source_path)
    if source.manifest["block_id"] != manifest.get("source_block_id"):
        raise ValueError("The recorded source path now points to a different block.")
    if source.manifest.get("pins") != manifest.get("pins"):
        raise RuntimeError(
            "The library block's external pin interface changed. Automatic sync is stopped "
            "because changing pin numbers or names could invalidate connected nets. "
            "Create a reviewed replacement instance instead."
        )

    base_hashes = manifest.get("base_hashes", {})
    local_hashes = _asset_hashes(local_block.path, manifest)
    source_hashes = _asset_hashes(source.path, source.manifest)
    base_dependencies = manifest.get("base_dependencies", manifest.get("dependencies", []))
    base_parameters = manifest.get("base_parameters", source.manifest.get("parameters", []))
    base_variants = manifest.get("base_variants", source.manifest.get("variants", []))
    local_variants = manifest.get("variants", [])
    source_variants = source.manifest.get("variants", [])
    base_profiles = manifest.get("base_profiles", source.manifest.get("profiles", []))
    local_profiles = manifest.get("profiles", [])
    source_profiles = source.manifest.get("profiles", [])
    local_profiles_changed = local_profiles != base_profiles
    source_profiles_changed = source_profiles != base_profiles
    profile_conflict = (
        local_profiles_changed
        and source_profiles_changed
        and local_profiles != source_profiles
    )
    local_variants_changed = local_variants != base_variants
    source_variants_changed = source_variants != base_variants
    variant_conflict = local_variants_changed and source_variants_changed and local_variants != source_variants
    base_layouts = _base_layout_configuration(manifest, source.manifest)
    local_layouts = _layout_configuration(manifest)
    source_layouts = _layout_configuration(source.manifest)
    local_layouts_changed = local_layouts != base_layouts
    source_layouts_changed = source_layouts != base_layouts
    layout_conflict = (
        local_layouts_changed
        and source_layouts_changed
        and local_layouts != source_layouts
    )
    local_parameters = manifest.get("parameters", [])
    source_parameters = source.manifest.get("parameters", [])
    local_parameters_changed = local_parameters != base_parameters
    source_parameters_changed = source_parameters != base_parameters
    parameter_conflict = (
        local_parameters_changed
        and source_parameters_changed
        and local_parameters != source_parameters
    )
    local_dependencies = manifest.get("dependencies", [])
    source_dependencies = source.manifest.get("dependencies", [])
    local_dependencies_changed = local_dependencies != base_dependencies
    source_dependencies_changed = source_dependencies != base_dependencies
    dependency_conflict = (
        local_dependencies_changed
        and source_dependencies_changed
        and local_dependencies != source_dependencies
    )
    conflicts = ["manifest.dependencies"] if dependency_conflict else []
    if parameter_conflict:
        conflicts.append("manifest.parameters")
    if variant_conflict:
        conflicts.append("manifest.variants")
    if profile_conflict:
        conflicts.append("manifest.profiles")
    if layout_conflict:
        conflicts.append("manifest.layouts")
    copy_files = []
    for file_name in sorted(set(base_hashes) | set(local_hashes) | set(source_hashes)):
        base = base_hashes.get(file_name)
        local = local_hashes.get(file_name)
        upstream = source_hashes.get(file_name)
        local_changed = local != base
        upstream_changed = upstream != base
        if local_changed and upstream_changed and local != upstream:
            conflicts.append(file_name)
        elif upstream_changed and not local_changed:
            copy_files.append(file_name)

    if conflicts:
        raise RuntimeError(
            "Sync conflict: both the local copy and library changed "
            + ", ".join(conflicts)
            + ". Resolve those files manually; no files were overwritten."
        )

    latest_source = _record(source.path)
    if (
        _asset_hashes(latest_source.path, latest_source.manifest) != source_hashes
        or latest_source.manifest.get("dependencies", []) != source_dependencies
        or latest_source.manifest.get("parameters", []) != source_parameters
        or latest_source.manifest.get("variants", []) != source_variants
        or latest_source.manifest.get("profiles", []) != source_profiles
        or _layout_configuration(latest_source.manifest) != source_layouts
    ):
        raise RuntimeError(
            "The library changed during synchronization. Refresh and retry; "
            "the local copy was not modified by this operation."
        )

    for file_name in copy_files:
        source_file = source.path / file_name
        target_file = local_block.path / file_name
        if source_file.exists():
            target_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, target_file)
        else:
            target_file.unlink(missing_ok=True)

    # Re-read the source state and advance the baseline only after all copies succeed.
    refreshed_source = _record(source.path)
    new_hashes = _asset_hashes(local_block.path, refreshed_source.manifest)
    refreshed_manifest = read_manifest(local_block.path)
    refreshed_manifest["base_hashes"] = _asset_hashes(source.path, refreshed_source.manifest)
    refreshed_manifest["source_version"] = refreshed_source.manifest.get("version", 1)
    refreshed_manifest["source_revision"] = refreshed_source.manifest.get("revision")
    refreshed_manifest["files"] = refreshed_source.manifest.get("files", [])
    # Keep the local manifest's local identity and nickname, but refresh its pin contract.
    refreshed_manifest["pins"] = refreshed_source.manifest["pins"]
    if source_dependencies_changed and not local_dependencies_changed:
        refreshed_manifest["dependencies"] = source_dependencies
    if source_parameters_changed and not local_parameters_changed:
        refreshed_manifest["parameters"] = source_parameters
    if source_variants_changed and not local_variants_changed:
        refreshed_manifest["variants"] = source_variants
    if source_profiles_changed and not local_profiles_changed:
        refreshed_manifest["profiles"] = source_profiles
    if source_layouts_changed and not local_layouts_changed:
        _set_layout_configuration(refreshed_manifest, source_layouts)
    refreshed_manifest["base_dependencies"] = source_dependencies
    refreshed_manifest["base_parameters"] = source_parameters
    refreshed_manifest["base_variants"] = source_variants
    refreshed_manifest["base_profiles"] = source_profiles
    refreshed_manifest["base_layouts"] = source_layouts
    refreshed_manifest["hashes"] = new_hashes
    refreshed_manifest["revision"] = revision_for_hashes(new_hashes)
    atomic_write(
        local_block.path / MANIFEST_NAME,
        json.dumps(refreshed_manifest, indent=2, ensure_ascii=False) + "\n",
    )
    return {
        "copied": copy_files,
        "preserved": sorted(set(local_hashes) - set(copy_files)),
        "dependencies_updated": source_dependencies_changed and not local_dependencies_changed,
        "parameters_updated": source_parameters_changed and not local_parameters_changed,
        "variants_updated": source_variants_changed and not local_variants_changed,
        "profiles_updated": source_profiles_changed and not local_profiles_changed,
        "layouts_updated": source_layouts_changed and not local_layouts_changed,
        "conflicts": [],
    }


def synchronize_facade_pins_from_implementation(block: BlockRecord) -> list[str]:
    """Sync facade pins only when the manifest declares labels as the source.

    Manually declared interfaces remain authoritative even though the renderer
    now uses native hierarchical labels for their visual external ports.
    Existing pin coordinates and numbers are preserved when a name remains.
    Newly declared pins use the normal generated layout as their initial position.
    """
    manifest = read_manifest(block.path)
    if manifest.get("interface_source") != "hierarchical_labels":
        _import_catalog_facade_edit(block, manifest)
        return []

    implementation_path = block.path / manifest["implementation_file"]
    implementation_tree = parse_sexpr(
        implementation_path.read_text(encoding="utf-8")
    )
    if not _all(implementation_tree, "hierarchical_label"):
        raise ValueError(
            "This block derives its facade pins from hierarchical labels, but its "
            "implementation has none. Add at least one hierarchical label or restore "
            "the intended interface before publishing; stale facade pins were not kept."
        )

    _import_catalog_facade_edit(block, manifest)
    extracted = extract_hierarchical_pins(implementation_tree)
    previous = manifest.get("pins", [])
    previous_by_name = {
        str(item.get("name", "")).casefold(): item
        for item in previous
        if isinstance(item, dict)
    }

    used_numbers = {
        str(item.get("number"))
        for item in previous
        if isinstance(item, dict) and str(item.get("number", "")).strip()
    }
    next_number = 1
    normalized_pins = []
    for pin in extracted:
        old = previous_by_name.get(pin.name.casefold())
        if old is not None:
            number = str(old.get("number") or pin.number)
        else:
            while str(next_number) in used_numbers:
                next_number += 1
            number = str(next_number)
            used_numbers.add(number)
            next_number += 1
        normalized_pins.append(
            BlockPin(pin.name, pin.electrical_type, number)
        )

    new_manifest_pins = [
        pin.as_manifest(index + 1)
        for index, pin in enumerate(normalized_pins)
    ]
    if new_manifest_pins == previous:
        return []

    symbol_path = block.path / manifest["symbol_file"]
    symbol_library = parse_sexpr(symbol_path.read_text(encoding="utf-8"))
    symbols = _all(symbol_library, "symbol")
    if len(symbols) != 1:
        raise ValueError(f"Expected exactly one facade symbol in {symbol_path}.")
    symbol = symbols[0]
    symbol_name = str(symbol[1])
    pin_unit_name = symbol_name + "_1_1"
    pin_unit = next(
        (
            item for item in symbol[1:]
            if _head(item) == "symbol" and len(item) >= 2
            and str(item[1]) == pin_unit_name
        ),
        None,
    )
    if pin_unit is None:
        raise ValueError(
            f"Facade symbol {symbol_name!r} has no pin unit {pin_unit_name!r}."
        )

    current_pin_nodes = _all(pin_unit, "pin")
    current_by_name = {}
    for node in current_pin_nodes:
        name = _direct(node, "name")
        if name is not None and len(name) >= 2:
            current_by_name[str(name[1]).casefold()] = node

    default_symbol_tree = parse_sexpr(
        render_symbol_library(
            symbol_name,
            str(manifest["name"]),
            str(manifest["block_id"]),
            normalized_pins,
            reference_prefix=str(manifest.get("reference_prefix", "X")),
            parameters=manifest.get("parameters", []),
            default_layout_id=manifest.get("default_layout"),
            include_profile_field=bool(manifest.get("profiles")),
        )
    )
    default_symbol = _all(default_symbol_tree, "symbol")[0]
    default_pin_unit = next(
        item for item in default_symbol[1:]
        if _head(item) == "symbol" and len(item) >= 2
        and str(item[1]) == pin_unit_name
    )
    default_pin_nodes = _all(default_pin_unit, "pin")
    default_by_name = {}
    for node in default_pin_nodes:
        name = _direct(node, "name")
        if name is not None and len(name) >= 2:
            default_by_name[str(name[1]).casefold()] = node

    updated_pin_nodes = []
    for pin in normalized_pins:
        key = pin.name.casefold()
        existing = current_by_name.get(key)
        if existing is not None:
            node = copy.deepcopy(existing)
            # The hierarchical-label shape is authoritative for electrical type.
            node[1] = _Quoted(
                "tri_state" if pin.electrical_type == "tristate" else pin.electrical_type
            )
            name_node = _direct(node, "name")
            if name_node is not None and len(name_node) >= 2:
                name_node[1] = _Quoted(pin.name)
            number_node = _direct(node, "number")
            if number_node is not None and len(number_node) >= 2:
                number_node[1] = _Quoted(pin.number)
        else:
            node = copy.deepcopy(default_by_name[key])
        updated_pin_nodes.append(node)

    # Keep all symbol-body artwork untouched; only replace the pin unit's pins.
    other_children = [
        child for child in pin_unit[1:]
        if _head(child) != "pin"
    ]
    pin_unit[:] = [pin_unit[0], *other_children, *updated_pin_nodes]
    atomic_write(symbol_path, _dump(symbol_library) + "\n")
    manifest["pins"] = new_manifest_pins
    atomic_write(
        block.path / MANIFEST_NAME,
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    return [
        pin.name for pin in normalized_pins
        if pin.name.casefold() not in current_by_name
    ] + [
        pin.name for pin in normalized_pins
        if pin.name.casefold() in current_by_name
        and str(current_by_name[pin.name.casefold()][1]) != (
            "tri_state" if pin.electrical_type == "tristate" else pin.electrical_type
        )
    ]


def refresh_library_block(block: BlockRecord) -> dict:
    """Recompute asset hashes and publish a new library revision when files changed."""
    manifest = read_manifest(block.path)
    if manifest.get("local_copy"):
        raise ValueError("Publish the source library block, not a project-local copy.")
    synchronize_facade_pins_from_implementation(block)
    manifest = read_manifest(block.path)
    current_hashes = _asset_hashes(block.path, manifest)
    previous_hashes = manifest.get("hashes", {})
    changed = sorted(
        name for name in set(current_hashes) | set(previous_hashes)
        if current_hashes.get(name) != previous_hashes.get(name)
    )
    if not changed:
        _save_catalog_symbol_baseline(block, manifest)
        return {"changed": [], "version": int(manifest.get("version", 1)), "revision": manifest.get("revision")}
    manifest["files"] = sorted(current_hashes)
    manifest["hashes"] = current_hashes
    manifest["revision"] = revision_for_hashes(current_hashes)
    manifest["version"] = int(manifest.get("version", 1)) + 1
    atomic_write(
        block.path / MANIFEST_NAME,
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    )
    if SYMBOL_NAME in changed:
        merge_symbol_catalog(block.path.parent, block.path / SYMBOL_NAME)
    _save_catalog_symbol_baseline(block, read_manifest(block.path))
    return {"changed": changed, "version": manifest["version"], "revision": manifest["revision"]}


def publish_local_copy(local_block: BlockRecord) -> dict:
    """Synchronize edits in both directions using the saved three-way baseline.

    Local-only edits are published to the library, upstream-only edits are
    pulled into the local copy, and divergent concurrent edits are rejected
    before either side is modified. The selected project instance stays local.
    """
    manifest = read_manifest(local_block.path)
    if not manifest.get("local_copy"):
        raise ValueError("Only a project-local block copy can be synchronized.")
    source_path = Path(manifest["source_path"]).expanduser().resolve()
    source = _record(source_path)
    if source.manifest.get("block_id") != manifest.get("source_block_id"):
        raise ValueError("The recorded source path now points to a different block.")
    if manifest.get("pins") != source.manifest.get("pins"):
        raise RuntimeError(
            "The local block's external pin interface differs from the library. "
            "Interface changes require an explicit migration and cannot be synchronized."
        )

    base_hashes = manifest.get("base_hashes", {})
    local_hashes = _asset_hashes(local_block.path, manifest)
    source_hashes = _asset_hashes(source.path, source.manifest)
    base_dependencies = manifest.get(
        "base_dependencies", source.manifest.get("dependencies", [])
    )
    base_parameters = manifest.get("base_parameters", source.manifest.get("parameters", []))
    base_variants = manifest.get("base_variants", source.manifest.get("variants", []))
    local_variants = manifest.get("variants", [])
    source_variants = source.manifest.get("variants", [])
    local_variants_changed = local_variants != base_variants
    base_profiles = manifest.get("base_profiles", source.manifest.get("profiles", []))
    local_profiles = manifest.get("profiles", [])
    source_profiles = source.manifest.get("profiles", [])
    local_profiles_changed = local_profiles != base_profiles
    source_profiles_changed = source_profiles != base_profiles
    profile_conflict = (
        local_profiles_changed
        and source_profiles_changed
        and local_profiles != source_profiles
    )
    source_variants_changed = source_variants != base_variants
    variant_conflict = local_variants_changed and source_variants_changed and local_variants != source_variants
    base_layouts = _base_layout_configuration(manifest, source.manifest)
    local_layouts = _layout_configuration(manifest)
    source_layouts = _layout_configuration(source.manifest)
    local_layouts_changed = local_layouts != base_layouts
    source_layouts_changed = source_layouts != base_layouts
    layout_conflict = (
        local_layouts_changed
        and source_layouts_changed
        and local_layouts != source_layouts
    )
    local_parameters = manifest.get("parameters", [])
    source_parameters = source.manifest.get("parameters", [])
    local_parameters_changed = local_parameters != base_parameters
    source_parameters_changed = source_parameters != base_parameters
    parameter_conflict = (
        local_parameters_changed
        and source_parameters_changed
        and local_parameters != source_parameters
    )
    local_dependencies = manifest.get("dependencies", [])
    source_dependencies = source.manifest.get("dependencies", [])
    local_dependencies_changed = local_dependencies != base_dependencies
    source_dependencies_changed = source_dependencies != base_dependencies
    dependency_conflict = (
        local_dependencies_changed
        and source_dependencies_changed
        and local_dependencies != source_dependencies
    )

    conflicts = ["manifest.dependencies"] if dependency_conflict else []
    if parameter_conflict:
        conflicts.append("manifest.parameters")
    if variant_conflict:
        conflicts.append("manifest.variants")
    if profile_conflict:
        conflicts.append("manifest.profiles")
    if layout_conflict:
        conflicts.append("manifest.layouts")
    push_files: list[str] = []
    pull_files: list[str] = []
    converged_files: list[str] = []
    for file_name in sorted(set(base_hashes) | set(local_hashes) | set(source_hashes)):
        base_hash = base_hashes.get(file_name)
        local_hash = local_hashes.get(file_name)
        upstream_hash = source_hashes.get(file_name)
        local_changed = local_hash != base_hash
        upstream_changed = upstream_hash != base_hash
        if local_changed and upstream_changed and local_hash != upstream_hash:
            conflicts.append(file_name)
        elif local_changed and upstream_changed and local_hash == upstream_hash:
            converged_files.append(file_name)
        elif local_changed and not upstream_changed:
            push_files.append(file_name)
        elif upstream_changed and not local_changed:
            pull_files.append(file_name)

    if conflicts:
        raise RuntimeError(
            "Synchronization conflict: both the local copy and library changed "
            + ", ".join(conflicts)
            + ". Resolve those changes manually; neither side was modified."
        )

    push_dependencies = local_dependencies_changed and not source_dependencies_changed
    pull_dependencies = source_dependencies_changed and not local_dependencies_changed
    converged_dependencies = (
        local_dependencies_changed
        and source_dependencies_changed
        and local_dependencies == source_dependencies
    )
    push_parameters = local_parameters_changed and not source_parameters_changed
    pull_parameters = source_parameters_changed and not local_parameters_changed
    converged_parameters = (
        local_parameters_changed
        and source_parameters_changed
        and local_parameters == source_parameters
    )
    push_variants = local_variants_changed and not source_variants_changed
    pull_variants = source_variants_changed and not local_variants_changed
    converged_variants = (
        local_variants_changed and source_variants_changed
        and local_variants == source_variants
    )
    push_profiles = local_profiles_changed and not source_profiles_changed
    pull_profiles = source_profiles_changed and not local_profiles_changed
    converged_profiles = (
        local_profiles_changed
        and source_profiles_changed
        and local_profiles == source_profiles
    )
    push_layouts = local_layouts_changed and not source_layouts_changed
    pull_layouts = source_layouts_changed and not local_layouts_changed
    converged_layouts = (
        local_layouts_changed
        and source_layouts_changed
        and local_layouts == source_layouts
    )
    if (
        not push_files
        and not pull_files
        and not converged_files
        and not push_dependencies
        and not pull_dependencies
        and not converged_dependencies
        and not push_parameters
        and not pull_parameters
        and not converged_parameters
        and not push_variants
        and not pull_variants
        and not converged_variants
        and not push_profiles
        and not pull_profiles
        and not converged_profiles
        and not push_layouts
        and not pull_layouts
        and not converged_layouts
    ):
        return {
            "published": [],
            "pulled": [],
            "version": int(source.manifest.get("version", 1)),
            "revision": source.manifest.get("revision"),
            "dependencies_updated": False,
            "converged": [],
        }

    # Optimistic concurrency check before the first mutation.
    latest_source = _record(source.path)
    if (
        _asset_hashes(latest_source.path, latest_source.manifest) != source_hashes
        or latest_source.manifest.get("dependencies", []) != source_dependencies
        or latest_source.manifest.get("parameters", []) != source_parameters
        or latest_source.manifest.get("variants", []) != source_variants
        or latest_source.manifest.get("profiles", []) != source_profiles
        or _layout_configuration(latest_source.manifest) != source_layouts
    ):
        raise RuntimeError(
            "The library changed during synchronization. Refresh and retry; "
            "neither side was modified by this operation."
        )

    source_backup = Path(
        tempfile.mkdtemp(prefix=".kicad-block-source-backup-", dir=source.path.parent)
    )
    local_backup = Path(
        tempfile.mkdtemp(prefix=".kicad-block-local-backup-", dir=local_block.path.parent)
    )
    source_originals: dict[str, bool] = {}
    local_originals: dict[str, bool] = {}
    catalog_originals: dict[str, bool] = {}
    source_manifest_path = source.path / MANIFEST_NAME
    local_manifest_path = local_block.path / MANIFEST_NAME
    shutil.copy2(source_manifest_path, source_backup / MANIFEST_NAME)
    shutil.copy2(local_manifest_path, local_backup / MANIFEST_NAME)

    def backup_file(root: Path, backup_root: Path, name: str, originals: dict[str, bool]) -> None:
        path = root / name
        existed = path.is_file()
        originals[name] = existed
        if existed:
            backup = backup_root / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)

    def restore_files(
        root: Path, backup_root: Path, originals: dict[str, bool]
    ) -> None:
        for name, existed in originals.items():
            path = root / name
            backup = backup_root / name
            if existed and backup.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, path)
            elif not existed:
                path.unlink(missing_ok=True)

    try:
        for file_name in push_files:
            backup_file(source.path, source_backup, file_name, source_originals)
        for file_name in pull_files:
            backup_file(local_block.path, local_backup, file_name, local_originals)
        if SYMBOL_NAME in push_files:
            catalog_name = str(source.manifest.get("catalog_file", LIBRARY_CATALOG_NAME))
            backup_file(source.path.parent, source_backup, catalog_name, catalog_originals)

        for file_name in push_files:
            source_file = source.path / file_name
            local_file = local_block.path / file_name
            if local_file.is_file():
                source_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(local_file, source_file)
            else:
                source_file.unlink(missing_ok=True)
        if SYMBOL_NAME in push_files:
            merge_symbol_catalog(source.path.parent, source.path / SYMBOL_NAME)

        for file_name in pull_files:
            source_file = source.path / file_name
            local_file = local_block.path / file_name
            if source_file.is_file():
                local_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, local_file)
            else:
                local_file.unlink(missing_ok=True)

        source_manifest = read_manifest(source.path)
        local_manifest = read_manifest(local_block.path)
        if push_dependencies:
            source_manifest["dependencies"] = local_dependencies
        elif pull_dependencies:
            local_manifest["dependencies"] = source_dependencies
        if push_parameters:
            source_manifest["parameters"] = local_parameters
        elif pull_parameters:
            local_manifest["parameters"] = source_parameters
        if push_variants:
            source_manifest["variants"] = local_variants
        elif pull_variants:
            local_manifest["variants"] = source_variants
        if push_profiles:
            source_manifest["profiles"] = local_profiles
        elif pull_profiles:
            local_manifest["profiles"] = source_profiles
        if push_layouts:
            _set_layout_configuration(source_manifest, local_layouts)
        elif pull_layouts:
            _set_layout_configuration(local_manifest, source_layouts)

        candidate_files = set(source_manifest.get("files", []))
        candidate_files.update(local_manifest.get("files", []))
        final_files = sorted(
            name
            for name in candidate_files
            if (source.path / name).is_file() and (local_block.path / name).is_file()
        )
        source_manifest["files"] = final_files
        local_manifest["files"] = final_files
        source_hashes_after = _asset_hashes(source.path, source_manifest)
        local_hashes_after = _asset_hashes(local_block.path, local_manifest)
        if source_hashes_after != local_hashes_after:
            raise RuntimeError(
                "Synchronization did not produce matching source and local assets."
            )

        source_changed = bool(
            push_files or push_dependencies or push_parameters or push_variants or push_profiles or push_layouts
        )
        if source_changed:
            source_manifest["hashes"] = source_hashes_after
            source_manifest["revision"] = revision_for_hashes(source_hashes_after)
            source_manifest["version"] = int(source_manifest.get("version", 1)) + 1
            atomic_write(
                source_manifest_path,
                json.dumps(source_manifest, indent=2, ensure_ascii=False) + "\n",
            )
        else:
            source_manifest = read_manifest(source.path)

        local_manifest["hashes"] = local_hashes_after
        local_manifest["revision"] = revision_for_hashes(local_hashes_after)
        local_manifest["source_version"] = source_manifest.get("version", 1)
        local_manifest["source_revision"] = source_manifest.get("revision")
        local_manifest["base_hashes"] = dict(source_manifest.get("hashes", {}))
        local_manifest["base_dependencies"] = source_manifest.get("dependencies", [])
        local_manifest["base_parameters"] = source_manifest.get("parameters", [])
        local_manifest["base_variants"] = source_manifest.get("variants", [])
        local_manifest["base_profiles"] = source_manifest.get("profiles", [])
        local_manifest["base_layouts"] = _layout_configuration(source_manifest)
        atomic_write(
            local_manifest_path,
            json.dumps(local_manifest, indent=2, ensure_ascii=False) + "\n",
        )
    except Exception:
        restore_files(source.path, source_backup, source_originals)
        restore_files(source.path.parent, source_backup, catalog_originals)
        restore_files(local_block.path, local_backup, local_originals)
        shutil.copy2(source_backup / MANIFEST_NAME, source_manifest_path)
        shutil.copy2(local_backup / MANIFEST_NAME, local_manifest_path)
        raise
    finally:
        shutil.rmtree(source_backup, ignore_errors=True)
        shutil.rmtree(local_backup, ignore_errors=True)

    published = list(push_files)
    pulled = list(pull_files)
    if push_dependencies:
        published.append("manifest.dependencies")
    if pull_dependencies:
        pulled.append("manifest.dependencies")
    if push_parameters:
        published.append("manifest.parameters")
    if pull_parameters:
        pulled.append("manifest.parameters")
    if push_variants:
        published.append("manifest.variants")
    if pull_variants:
        pulled.append("manifest.variants")
    if push_profiles:
        published.append("manifest.profiles")
    if pull_profiles:
        pulled.append("manifest.profiles")
    if push_layouts:
        published.append("manifest.layouts")
    if pull_layouts:
        pulled.append("manifest.layouts")
    return {
        "published": published,
        "pulled": pulled,
        "version": int(source_manifest.get("version", 1)),
        "revision": source_manifest.get("revision"),
        "dependencies_updated": push_dependencies or pull_dependencies or converged_dependencies,
        "parameters_updated": push_parameters or pull_parameters or converged_parameters,
        "variants_updated": push_variants or pull_variants or converged_variants,
        "profiles_updated": push_profiles or pull_profiles or converged_profiles,
        "layouts_updated": push_layouts or pull_layouts or converged_layouts,
        "converged": converged_files
        + (["manifest.dependencies"] if converged_dependencies else [])
        + (["manifest.parameters"] if converged_parameters else [])
        + (["manifest.variants"] if converged_variants else [])
        + (["manifest.profiles"] if converged_profiles else [])
        + (["manifest.layouts"] if converged_layouts else []),
    }


def add_dependency(
    parent: BlockRecord,
    child: BlockRecord,
    *,
    reference: str | None = None,
    position_mm: tuple[float, float] | None = None,
    orientation: int = 0,
    pin_map: dict[str, str | None] | None = None,
) -> BlockRecord:
    """Add one nestable block *instance* to a parent implementation.

    The same reusable child block may be instantiated more than once. Each
    occurrence receives its own reference, UUID, placement, and external-pin
    to parent-net mapping.
    """
    if parent.manifest["block_id"] == child.manifest["block_id"]:
        raise ValueError("A block cannot depend on itself.")
    if _depends_on(child.path, parent.manifest["block_id"], set()):
        raise ValueError("This dependency would create a cycle.")

    dependencies = list(parent.manifest.get("dependencies", []))
    used_references = {
        str(item.get("reference", "")).casefold()
        for item in dependencies
        if item.get("reference")
    }
    if reference is None:
        index = 1
        while f"B{index}".casefold() in used_references:
            index += 1
        reference = f"B{index}"
    reference = reference.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", reference):
        raise ValueError("Nested block reference must start with a letter and contain only letters and digits.")
    if reference.casefold() in used_references:
        raise ValueError(f"Nested block reference {reference!r} is already used by this parent.")

    if orientation not in (0, 90, 180, 270):
        raise ValueError("Nested block orientation must be 0, 90, 180 or 270 degrees.")
    if position_mm is None:
        index = len(dependencies)
        position_mm = (100.0 + (index % 2) * 65.0, 160.0 + (index // 2) * 50.0)
    if len(position_mm) != 2 or not all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in position_mm
    ):
        raise ValueError("Nested block position must contain two finite millimetre coordinates.")

    child_pins = {
        str(pin["name"])
        for pin in child.manifest.get("pins", [])
    }
    mapping = dict(pin_map) if pin_map is not None else {name: name for name in child_pins}
    if set(mapping) != child_pins:
        missing = sorted(child_pins - set(mapping))
        extra = sorted(set(mapping) - child_pins)
        raise ValueError(
            "Nested block pin map must map every child pin exactly once. "
            f"Missing: {missing}; unknown: {extra}."
        )
    for child_pin, net_name in mapping.items():
        if net_name is not None and not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_./+\\-]*", str(net_name)
        ):
            raise ValueError(
                f"Invalid parent net name {net_name!r} for child pin {child_pin!r}."
            )

    dependencies.append(
        {
            "instance_id": str(uuid.uuid4()),
            "reference": reference,
            "position_mm": [float(position_mm[0]), float(position_mm[1])],
            "orientation": orientation,
            "pin_map": mapping,
            "block_id": child.manifest["block_id"],
            "name": child.manifest["name"],
            "path": os.path.relpath(
                child.path.resolve(),
                _dependency_anchor(parent.path, parent.manifest),
            ),
            "revision": child.manifest.get("revision"),
        }
    )
    updated = dict(parent.manifest)
    updated["dependencies"] = dependencies
    updated["version"] = int(updated.get("version", 1)) + 1
    atomic_write(
        parent.path / MANIFEST_NAME,
        json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
    )
    return _record(parent.path)


def synchronize_dependencies(
    parent: BlockRecord,
    occurrences: list[dict],
) -> BlockRecord:
    """Replace a block's dependency graph with occurrences found in its schematic.

    The schematic is authoritative: removed facade symbols remove dependencies,
    while unchanged reference/child pairs keep their stable occurrence IDs.
    A None pin-map value represents a deliberately unconnected facade pin.
    """
    manifest = read_manifest(parent.path)
    existing_by_reference = {
        str(item.get("reference", "")).casefold(): item
        for item in manifest.get("dependencies", [])
        if item.get("reference")
    }
    dependencies: list[dict] = []
    used_references: set[str] = set()
    anchor = _dependency_anchor(parent.path, manifest)

    for occurrence in occurrences:
        child = occurrence["child"]
        reference = str(occurrence.get("reference", "")).strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", reference):
            raise ValueError(
                f"Detected child reference {reference!r} is invalid; annotate the schematic."
            )
        key = reference.casefold()
        if key in used_references:
            raise ValueError(f"Nested block reference {reference!r} is used more than once.")
        used_references.add(key)

        if child.manifest["block_id"] == manifest["block_id"]:
            raise ValueError("A block cannot depend on itself.")
        if _depends_on(child.path, manifest["block_id"], set()):
            raise ValueError(
                f"Adding {child.name!r} to {parent.name!r} would create a dependency cycle."
            )

        orientation = int(occurrence.get("orientation", 0))
        if orientation not in (0, 90, 180, 270):
            raise ValueError(
                "Nested block orientation must be 0, 90, 180 or 270 degrees."
            )
        raw_position = occurrence.get("position_mm")
        if not isinstance(raw_position, (tuple, list)) or len(raw_position) != 2:
            raise ValueError(f"Invalid placement coordinates for nested block {reference!r}.")
        position = (float(raw_position[0]), float(raw_position[1]))
        if not all(math.isfinite(value) for value in position):
            raise ValueError(f"Placement for nested block {reference!r} must be finite.")

        pin_names = {str(pin["name"]) for pin in child.manifest.get("pins", [])}
        raw_mapping = occurrence.get("pin_map", {})
        if not isinstance(raw_mapping, dict) or set(raw_mapping) != pin_names:
            missing = sorted(pin_names - set(raw_mapping or {}))
            extra = sorted(set(raw_mapping or {}) - pin_names)
            raise ValueError(
                f"Detected pin map for {reference!r} must include every child pin. "
                f"Missing: {missing}; unknown: {extra}."
            )
        mapping: dict[str, str | None] = {}
        for pin_name, raw_net in raw_mapping.items():
            if raw_net is None or not str(raw_net).strip():
                mapping[str(pin_name)] = None
                continue
            net_name = str(raw_net).strip()
            if len(net_name) > 512 or any(ord(char) < 32 for char in net_name):
                raise ValueError(
                    f"Net name for {reference}.{pin_name} must be a single-line value "
                    "of at most 512 characters."
                )
            mapping[str(pin_name)] = net_name

        previous = existing_by_reference.get(key, {})
        same_occurrence = previous.get("block_id") == child.manifest["block_id"]
        dependencies.append(
            {
                "instance_id": (
                    str(previous.get("instance_id") or uuid.uuid4())
                    if same_occurrence
                    else str(uuid.uuid4())
                ),
                "reference": reference,
                "position_mm": [position[0], position[1]],
                "orientation": orientation,
                "pin_map": mapping,
                "block_id": child.manifest["block_id"],
                "name": child.manifest["name"],
                "path": os.path.relpath(child.path.resolve(), anchor),
                "revision": child.manifest.get("revision"),
            }
        )

    dependencies.sort(key=lambda item: item["reference"].casefold())
    if dependencies != manifest.get("dependencies", []):
        manifest["dependencies"] = dependencies
        manifest["version"] = int(manifest.get("version", 1)) + 1
        atomic_write(
            parent.path / MANIFEST_NAME,
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        )
    return _record(parent.path)


def _dependency_anchor(block_path: Path, manifest: dict) -> Path:
    """Return the canonical source directory for resolving relative children."""
    source_path = manifest.get("source_path") if manifest.get("local_copy") else None
    return Path(source_path).expanduser().resolve() if source_path else Path(block_path).resolve()


def _dependency_path(block_path: Path, manifest: dict, dependency: dict) -> Path | None:
    raw_path = dependency.get("path")
    if not raw_path:
        return None
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = _dependency_anchor(block_path, manifest) / candidate
    return candidate.resolve()


def _depends_on(block_path: Path, target_id: str, visited: set[str]) -> bool:
    block_path = Path(block_path).expanduser().resolve()
    manifest = read_manifest(block_path)
    block_id = manifest["block_id"]
    if block_id == target_id:
        return True
    if block_id in visited:
        return False
    visited.add(block_id)
    for dependency in manifest.get("dependencies", []):
        if dependency.get("block_id") == target_id:
            return True
        child_path = _dependency_path(block_path, manifest, dependency)
        if child_path is not None and child_path.is_dir() and _depends_on(
            child_path, target_id, visited
        ):
            return True
    return False


def resolve_dependency_occurrences(
    block: BlockRecord, library_root: Path | None = None
) -> list[BlockOccurrence]:
    """Resolve every nested *instance* depth-first, preserving repetitions.

    Cycle detection uses the current ancestry rather than a global visited
    set, because reusing the same child block at two references is valid.
    """
    source_path = (
        Path(block.manifest["source_path"]).expanduser().resolve()
        if block.manifest.get("local_copy")
        else block.path.resolve()
    )
    root = Path(library_root).expanduser().resolve() if library_root else source_path.parent
    records = {record.manifest["block_id"]: record for record in list_blocks(root)}
    records[block.manifest["block_id"]] = block
    ordered: list[BlockOccurrence] = []
    visiting: set[str] = set()

    def visit(
        current: BlockRecord,
        instance_path: tuple[str, ...],
        instance_ids: tuple[str, ...],
    ) -> None:
        block_id = current.manifest["block_id"]
        if block_id in visiting:
            raise ValueError(f"Nested block dependency cycle detected at {current.name!r}.")
        visiting.add(block_id)
        for index, dependency in enumerate(current.manifest.get("dependencies", []), start=1):
            child_id = dependency.get("block_id")
            child = records.get(child_id)
            if child is None:
                child_path = _dependency_path(current.path, current.manifest, dependency)
                if child_path is not None and child_path.is_dir():
                    child = _record(child_path)
                    records[child_id] = child
            if child is None:
                raise FileNotFoundError(
                    f"Nested block {dependency.get('name', child_id)!r} "
                    f"({child_id}) referenced by {current.name!r} was not found."
                )
            expected_revision = dependency.get("revision")
            actual_revision = child.manifest.get("revision")
            if expected_revision and actual_revision != expected_revision:
                raise RuntimeError(
                    f"Nested block {child.name!r} changed revision "
                    f"({expected_revision} -> {actual_revision}); update the dependency "
                    "reference deliberately before using this composition."
                )
            reference = str(dependency.get("reference") or f"B{index}")
            instance_id = str(dependency.get("instance_id") or f"{block_id}:{index}")
            visit(
                child,
                instance_path + (reference,),
                instance_ids + (instance_id,),
            )
        visiting.remove(block_id)
        ordered.append(BlockOccurrence(current, instance_path, instance_ids))

    visit(block, (), ())
    return ordered


def resolve_dependency_graph(
    block: BlockRecord, library_root: Path | None = None
) -> list[BlockRecord]:
    """Resolve nested child block definitions in depth-first instance order."""
    occurrences = resolve_dependency_occurrences(block, library_root)
    return [occurrence.record for occurrence in occurrences[:-1]]


def settings_file() -> Path:
    """Return the per-user config location selected by platformdirs."""
    return user_config_path("kicad-blocks", appauthor=False) / "settings.json"


def _read_library_settings() -> dict:
    settings = settings_file()
    if not settings.is_file():
        return {}
    try:
        value = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_library_settings(value: dict) -> None:
    atomic_write(settings_file(), json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def library_roots() -> list[Path]:
    """Return every registered functional-block library, with the default first."""
    settings = _read_library_settings()
    candidates = []
    configured = os.environ.get("KICAD_BLOCKS_LIBRARY")
    if configured:
        candidates.append(Path(configured).expanduser())

    default = settings.get("library_root")
    if default:
        candidates.append(Path(default).expanduser())

    registered = settings.get("library_roots", [])
    if isinstance(registered, list):
        candidates.extend(
            Path(item).expanduser()
            for item in registered
            if isinstance(item, str) and item.strip()
        )

    candidates.append(Path.home() / "Documents" / "KiCad" / "Blocks")
    result = []
    seen = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            result.append(resolved)
    return result


def default_library_root() -> Path:
    """Return the preferred destination for newly-created functional blocks."""
    settings = _read_library_settings()
    configured = os.environ.get("KICAD_BLOCKS_LIBRARY")
    if configured:
        return Path(configured).expanduser().resolve()
    value = settings.get("library_root")
    if value:
        return Path(value).expanduser().resolve()
    registered = library_roots()
    return registered[0]


def _normalized_library_settings_roots(settings: dict) -> list[Path]:
    """Read the optional library-roots setting without trusting malformed JSON data."""
    raw_roots = settings.get("library_roots", [])
    if not isinstance(raw_roots, list):
        return []
    roots = []
    seen = set()
    for item in raw_roots:
        if not isinstance(item, str) or not item.strip():
            continue
        resolved = Path(item).expanduser().resolve()
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            roots.append(resolved)
    return roots


def add_library_root(path: Path, *, make_default: bool = False) -> list[Path]:
    """Register another shared functional-block library directory."""
    path = Path(path).expanduser().resolve()
    if not path.is_dir():
        raise NotADirectoryError(f"Functional-block library directory does not exist: {path}")
    settings = _read_library_settings()
    roots = _normalized_library_settings_roots(settings)
    if path not in roots:
        roots.append(path)
    settings["library_roots"] = [str(item) for item in roots]
    if make_default or not settings.get("library_root"):
        settings["library_root"] = str(path)
    _write_library_settings(settings)
    return library_roots()


def remove_library_root(path: Path) -> list[Path]:
    """Unregister a library; its files are never deleted."""
    target = Path(path).expanduser().resolve()
    environment_root = os.environ.get("KICAD_BLOCKS_LIBRARY")
    if environment_root and Path(environment_root).expanduser().resolve() == target:
        raise ValueError(
            "This library is fixed by KICAD_BLOCKS_LIBRARY and cannot be removed here."
        )

    current_roots = library_roots()
    if target not in current_roots:
        raise ValueError(f"This functional-block library is not registered: {target}")
    remaining = [item for item in current_roots if item != target]
    if not remaining:
        raise ValueError("At least one functional-block library must remain registered.")

    settings = _read_library_settings()
    settings["library_roots"] = [str(item) for item in remaining]
    current_default = settings.get("library_root")
    if not current_default or Path(current_default).expanduser().resolve() == target:
        settings["library_root"] = str(remaining[0])
    _write_library_settings(settings)
    return library_roots()


def set_library_root(path: Path) -> Path:
    """Register a folder and make it default unless an environment override is active."""
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    settings = _read_library_settings()
    roots = _normalized_library_settings_roots(settings)
    if path not in roots:
        roots.append(path)
    settings["library_roots"] = [str(item) for item in roots]
    if not os.environ.get("KICAD_BLOCKS_LIBRARY"):
        settings["library_root"] = str(path)
    _write_library_settings(settings)
    return path


def build_project_symbol_catalog(project_dir: Path, roots: list[Path]) -> Path:
    """Build the project's single KBlocks catalog from all registered libraries."""
    project_dir = Path(project_dir).expanduser().resolve()
    symbols_by_name: dict[str, list] = {}
    header = None

    for root in roots:
        for record in list_blocks(root):
            source = record.symbol_library
            if not source.is_file():
                raise FileNotFoundError(
                    f"Shared facade catalog for block {record.name!r} is missing: {source}"
                )
            tree = parse_sexpr(source.read_text(encoding="utf-8"))
            symbol_name = str(record.manifest.get("symbol_name") or "")
            symbol = _named_symbol_node(tree, symbol_name, source)
            existing = symbols_by_name.get(symbol_name)
            if existing is not None:
                if _symbol_definition_digest(existing) != _symbol_definition_digest(symbol):
                    raise ValueError(
                        f"Two registered block libraries define conflicting facade symbol "
                        f"{symbol_name!r}. Resolve the duplicate block ID before placing it."
                    )
                continue
            if header is None:
                header = [copy.deepcopy(item) for item in tree[1:] if _head(item) != "symbol"]
            symbols_by_name[symbol_name] = copy.deepcopy(symbol)

    if header is None:
        catalog_tree = parse_sexpr(
            '(kicad_symbol_lib (version 20231120) (generator "kicad_blocks"))'
        )
    else:
        catalog_tree = ["kicad_symbol_lib", *header]
    for name in sorted(symbols_by_name, key=str.casefold):
        catalog_tree.append(symbols_by_name[name])

    destination = project_dir / ".kicad_blocks" / LIBRARY_CATALOG_NAME
    atomic_write(destination, _dump(catalog_tree) + "\n")
    return destination
