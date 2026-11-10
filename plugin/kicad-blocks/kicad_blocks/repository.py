"""Filesystem-backed block library, local copies, synchronization and nesting."""

from __future__ import annotations

import json
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

MANIFEST_NAME = "manifest.json"
SYMBOL_NAME = "block.kicad_sym"
IMPLEMENTATION_NAME = "implementation.kicad_sch"
LAYOUT_NAME = "layout.kicad_pcb"
LIBRARY_NICKNAME_PREFIX = "KBlocks_"
LOCAL_NICKNAME_PREFIX = "KBLocal_"


@dataclass(frozen=True, slots=True)
class BlockRecord:
    path: Path
    manifest: dict

    @property
    def name(self) -> str:
        return str(self.manifest["name"])

    @property
    def symbol_library(self) -> Path:
        return self.path / str(self.manifest["symbol_file"])

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
    folder = root / f"{slug}_{block_id[:8]}.kicad_block"
    nickname = LIBRARY_NICKNAME_PREFIX + block_id.replace("-", "")[:8].upper()
    staging = Path(tempfile.mkdtemp(prefix=f".{slug}.", dir=root))
    try:
        symbol_content = render_symbol_library(
            slug, name, block_id, pins, reference_prefix=reference_prefix
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
            "library_nickname": nickname,
            "symbol_name": slug,
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
        return _record(folder)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _sexpr_string(value: str) -> str:
    return json.dumps(value.replace("\\", "/"), ensure_ascii=False)


def register_project_symbol_library(
    project_dir: Path, nickname: str, library_path: Path, description: str = "KiCad Blocks"
) -> Path:
    """Add or verify one project-scoped symbol library table row."""
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
    pattern = re.compile(r"\(lib\s+\(name\s+\"" + re.escape(nickname) + r"\"\)(.*?)\)\s*\)", re.DOTALL)
    existing = pattern.search(text)
    try:
        relative_path = library_path.relative_to(project_dir)
        uri = "${KIPRJMOD}/" + relative_path.as_posix()
    except ValueError:
        uri = library_path.as_posix()
    if existing:
        current_uri = re.search(r'\(uri\s+"([^"]+)"\)', existing.group(0))
        if current_uri:
            current_value = current_uri.group(1)
            if current_value.startswith("${KIPRJMOD}/"):
                current_path = (project_dir / current_value[len("${KIPRJMOD}/"):]).resolve()
            else:
                current_path = Path(current_value).expanduser().resolve()
            if current_path == library_path:
                return table_path
        raise ValueError(f"Symbol library nickname {nickname!r} is already used by another path.")
    entry = (
        f"  (lib (name {_sexpr_string(nickname)}) (type \"KiCad\") "
        f"(uri {_sexpr_string(uri)}) (options \"\") (descr {_sexpr_string(description)}))"
    )
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
            "library_nickname": LOCAL_NICKNAME_PREFIX + suffix,
        }
    )
    atomic_write(folder / MANIFEST_NAME, json.dumps(manifest, indent=2) + "\n")
    return _record(folder)


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
    return {"changed": changed, "version": manifest["version"], "revision": manifest["revision"]}


def publish_local_copy(local_block: BlockRecord) -> dict:
    """Publish local edits to the source library using the saved three-way baseline.

    A file or dependency is pushed only when the source has not changed since
    the last synchronization. Concurrent divergent edits are rejected before
    any source file is modified. The local copy remains local after publishing.
    """
    manifest = read_manifest(local_block.path)
    if not manifest.get("local_copy"):
        raise ValueError("Only a project-local block copy can be published.")
    source_path = Path(manifest["source_path"]).expanduser().resolve()
    source = _record(source_path)
    if source.manifest.get("block_id") != manifest.get("source_block_id"):
        raise ValueError("The recorded source path now points to a different block.")
    if manifest.get("pins") != source.manifest.get("pins"):
        raise RuntimeError(
            "The local block's external pin interface differs from the library. "
            "Publishing pin/interface changes is blocked pending an explicit migration."
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
    publish_files: list[str] = []
    for file_name in sorted(set(base_hashes) | set(local_hashes) | set(source_hashes)):
        base_hash = base_hashes.get(file_name)
        local_hash = local_hashes.get(file_name)
        upstream_hash = source_hashes.get(file_name)
        local_changed = local_hash != base_hash
        upstream_changed = upstream_hash != base_hash
        if local_changed and upstream_changed and local_hash != upstream_hash:
            conflicts.append(file_name)
        elif local_changed and not upstream_changed:
            publish_files.append(file_name)

    if conflicts:
        raise RuntimeError(
            "Publish conflict: both the local copy and library changed "
            + ", ".join(conflicts)
            + ". Resolve those changes manually; the library was not modified."
        )

    push_dependencies = local_dependencies_changed and not source_dependencies_changed
    changes = sorted(publish_files)
    if push_dependencies:
        changes.append("manifest.dependencies")
    if not changes:
        return {
            "published": [],
            "version": int(source.manifest.get("version", 1)),
            "revision": source.manifest.get("revision"),
            "upstream_changed": sorted(
                file_name
                for file_name in sorted(set(base_hashes) | set(source_hashes))
                if source_hashes.get(file_name) != base_hashes.get(file_name)
            ),
        }

    # Optimistic concurrency check: ensure the source still matches the exact
    # snapshot analysed above before copying any local edits into it.
    latest_source = _record(source.path)
    latest_hashes = _asset_hashes(latest_source.path, latest_source.manifest)
    latest_dependencies = latest_source.manifest.get("dependencies", [])
    if latest_hashes != source_hashes or latest_dependencies != source_dependencies:
        raise RuntimeError(
            "The library changed during publish. Refresh the local copy and retry; "
            "the library was not modified by this operation."
        )

    backup_dir = Path(
        tempfile.mkdtemp(prefix=".kicad-block-publish-backup-", dir=source.path.parent)
    )
    original_manifest = source.path / MANIFEST_NAME
    shutil.copy2(original_manifest, backup_dir / MANIFEST_NAME)
    original_files: dict[str, bool] = {}
    try:
        for file_name in publish_files:
            source_file = source.path / file_name
            local_file = local_block.path / file_name
            existed = source_file.is_file()
            original_files[file_name] = existed
            if existed:
                backup_file = backup_dir / file_name
                backup_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, backup_file)
            if local_file.is_file():
                source_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(local_file, source_file)
            else:
                source_file.unlink(missing_ok=True)

        source_manifest = read_manifest(source.path)
        if push_dependencies:
            source_manifest["dependencies"] = local_dependencies
        candidate_files = set(source_manifest.get("files", []))
        candidate_files.update(manifest.get("files", []))
        source_manifest["files"] = sorted(
            file_name
            for file_name in candidate_files
            if (source.path / file_name).is_file()
        )
        new_hashes = _asset_hashes(source.path, source_manifest)
        source_manifest["hashes"] = new_hashes
        source_manifest["revision"] = revision_for_hashes(new_hashes)
        source_manifest["version"] = int(source_manifest.get("version", 1)) + 1
        atomic_write(
            original_manifest,
            json.dumps(source_manifest, indent=2, ensure_ascii=False) + "\n",
        )
    except Exception:
        # Restore source files and its manifest on any partial write failure.
        for file_name, existed in original_files.items():
            source_file = source.path / file_name
            backup_file = backup_dir / file_name
            if existed and backup_file.is_file():
                source_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_file, source_file)
            elif not existed:
                source_file.unlink(missing_ok=True)
        shutil.copy2(backup_dir / MANIFEST_NAME, original_manifest)
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)

    # Advance the local three-way baseline only after the library update commits.
    local_manifest = read_manifest(local_block.path)
    local_manifest["files"] = source_manifest["files"]
    local_manifest["hashes"] = _asset_hashes(local_block.path, local_manifest)
    local_manifest["revision"] = revision_for_hashes(local_manifest["hashes"])
    local_manifest["source_version"] = source_manifest["version"]
    local_manifest["source_revision"] = source_manifest["revision"]
    local_manifest["base_hashes"] = dict(source_manifest["hashes"])
    local_manifest["base_dependencies"] = source_manifest.get("dependencies", [])
    atomic_write(
        local_block.path / MANIFEST_NAME,
        json.dumps(local_manifest, indent=2, ensure_ascii=False) + "\n",
    )
    return {
        "published": changes,
        "version": source_manifest["version"],
        "revision": source_manifest["revision"],
        "upstream_changed": [],
    }


def add_dependency(parent: BlockRecord, child: BlockRecord) -> BlockRecord:
    """Add a nested block reference, refusing self-links and dependency cycles."""
    if parent.manifest["block_id"] == child.manifest["block_id"]:
        raise ValueError("A block cannot depend on itself.")
    dependencies = list(parent.manifest.get("dependencies", []))
    if any(item.get("block_id") == child.manifest["block_id"] for item in dependencies):
        return parent
    if _depends_on(child.path, parent.manifest["block_id"], set()):
        raise ValueError("This dependency would create a cycle.")
    dependencies.append(
        {
            "block_id": child.manifest["block_id"],
            "name": child.manifest["name"],
            "path": str(child.path.resolve()),
            "revision": child.manifest.get("revision"),
        }
    )
    updated = dict(parent.manifest)
    updated["dependencies"] = dependencies
    updated["version"] = int(updated.get("version", 1)) + 1
    atomic_write(parent.path / MANIFEST_NAME, json.dumps(updated, indent=2) + "\n")
    return _record(parent.path)


def _depends_on(block_path: Path, target_id: str, visited: set[str]) -> bool:
    manifest = read_manifest(block_path)
    block_id = manifest["block_id"]
    if block_id in visited:
        return False
    visited.add(block_id)
    for dependency in manifest.get("dependencies", []):
        if dependency.get("block_id") == target_id:
            return True
        child_path = dependency.get("path")
        if child_path and Path(child_path).is_dir() and _depends_on(Path(child_path), target_id, visited):
            return True
    return False


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
