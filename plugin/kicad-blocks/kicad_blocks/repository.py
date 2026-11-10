"""Filesystem-backed block library, local copies, synchronization and nesting."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from .model import (
    BlockPin,
    render_implementation_schematic,
    render_symbol_library,
    revision_for_hashes,
    sha256_file,
    slugify,
)
from .pcb_layout import _dump, _head, parse_sexpr

MANIFEST_NAME = "manifest.json"
SYMBOL_NAME = "block.kicad_sym"
IMPLEMENTATION_NAME = "implementation.kicad_sch"
LAYOUT_NAME = "layout.kicad_pcb"
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
        return self.path / str(self.manifest["implementation_file"])


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


def ensure_stable_block_identity(block: "BlockRecord") -> "BlockRecord":
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
    pins: list[BlockPin],
    *,
    description: str = "",
    reference_prefix: str = "X",
    layout_file: Path | None = None,
) -> BlockRecord:
    """Create a versioned library block with a normal KiCad symbol facade."""
    root = Path(root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    if not name.strip():
        raise ValueError("Block name cannot be empty.")
    if not pins:
        raise ValueError("At least one pin is required.")
    slug = slugify(name)
    block_id = str(uuid.uuid4())
    suffix = block_id.replace("-", "")[:8].lower()
    symbol_name = f"{slug}_{suffix}"
    folder = root / f"{slug}_{block_id[:8]}.kicad_block"
    staging = Path(tempfile.mkdtemp(prefix=f".{slug}.", dir=root))
    folder_created = False
    try:
        symbol_content = render_symbol_library(
            symbol_name, name, block_id, pins, reference_prefix=reference_prefix
        )
        (staging / SYMBOL_NAME).write_text(symbol_content, encoding="utf-8")
        (staging / IMPLEMENTATION_NAME).write_text(
            render_implementation_schematic(name, block_id, pins), encoding="utf-8"
        )
        files = [SYMBOL_NAME, IMPLEMENTATION_NAME]
        if layout_file is not None:
            layout_file = Path(layout_file).expanduser().resolve()
            if not layout_file.is_file() or layout_file.suffix.lower() != ".kicad_pcb":
                raise ValueError("PCB layout must be an existing .kicad_pcb file.")
            shutil.copy2(layout_file, staging / LAYOUT_NAME)
            files.append(LAYOUT_NAME)

        hashes = {file_name: sha256_file(staging / file_name) for file_name in files}
        manifest = {
            "schema_version": 1,
            "block_id": block_id,
            "name": name.strip(),
            "slug": slug,
            "description": description.strip(),
            "version": 1,
            "revision": revision_for_hashes(hashes),
            "created_by": "kicad-blocks",
            "library_nickname": LIBRARY_NICKNAME,
            "symbol_name": symbol_name,
            "catalog_file": LIBRARY_CATALOG_NAME,
            "symbol_file": SYMBOL_NAME,
            "implementation_file": IMPLEMENTATION_NAME,
            "layout_file": LAYOUT_NAME if LAYOUT_NAME in files else None,
            "reference_prefix": reference_prefix,
            "pins": [pin.as_manifest(index + 1) for index, pin in enumerate(pins)],
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


def _asset_hashes(block_path: Path, manifest: dict) -> dict[str, str]:
    result = {}
    for file_name in manifest.get("files", []):
        path = block_path / file_name
        if path.is_file():
            result[file_name] = sha256_file(path)
    return result


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
    entry = data.get("instances", {}).get(str(instance_id))
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
    refreshed_manifest["base_dependencies"] = source_dependencies
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
        "conflicts": [],
    }


def refresh_library_block(block: BlockRecord) -> dict:
    """Recompute asset hashes and publish a new library revision when files changed."""
    manifest = read_manifest(block.path)
    if manifest.get("local_copy"):
        raise ValueError("Publish the source library block, not a project-local copy.")
    current_hashes = _asset_hashes(block.path, manifest)
    previous_hashes = manifest.get("hashes", {})
    changed = sorted(
        name for name in set(current_hashes) | set(previous_hashes)
        if current_hashes.get(name) != previous_hashes.get(name)
    )
    if not changed:
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
    if (
        not push_files
        and not pull_files
        and not converged_files
        and not push_dependencies
        and not pull_dependencies
        and not converged_dependencies
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
            backup_file(source.path.parent, source_backup, catalog_name, source_originals)

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

        source_changed = bool(push_files or push_dependencies)
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
        atomic_write(
            local_manifest_path,
            json.dumps(local_manifest, indent=2, ensure_ascii=False) + "\n",
        )
    except Exception:
        restore_files(source.path, source_backup, source_originals)
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
    return {
        "published": published,
        "pulled": pulled,
        "version": int(source_manifest.get("version", 1)),
        "revision": source_manifest.get("revision"),
        "dependencies_updated": push_dependencies or pull_dependencies or converged_dependencies,
        "converged": converged_files
        + (["manifest.dependencies"] if converged_dependencies else []),
    }


def add_dependency(
    parent: BlockRecord,
    child: BlockRecord,
    *,
    reference: str | None = None,
    position_mm: tuple[float, float] | None = None,
    orientation: int = 0,
    pin_map: dict[str, str] | None = None,
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
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_./+\\-]*", str(net_name)):
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
    return Path.home() / ".config" / "kicad-blocks" / "settings.json"


def default_library_root() -> Path:
    configured = os.environ.get("KICAD_BLOCKS_LIBRARY")
    if configured:
        return Path(configured).expanduser().resolve()
    settings = settings_file()
    if settings.is_file():
        try:
            value = json.loads(settings.read_text(encoding="utf-8")).get("library_root")
            if value:
                return Path(value).expanduser().resolve()
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return (Path.home() / "Documents" / "KiCad" / "Blocks").resolve()


def set_library_root(path: Path) -> Path:
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    atomic_write(settings_file(), json.dumps({"library_root": str(path)}, indent=2) + "\n")
    return path
