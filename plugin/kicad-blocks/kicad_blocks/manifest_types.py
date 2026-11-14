"""Domain-specific schemas for persisted KiCad Blocks data.

These contracts describe the fields the application reads and writes. They
intentionally do not define a recursive catch-all JSON value: external JSON is
validated at its file boundary and then represented by one of these schemas.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal, NotRequired, Required, TypedDict

if TYPE_CHECKING:
    from .repository import BlockRecord

ParameterType = Literal["string", "number", "integer", "boolean"]
InterfaceSource = Literal["manual", "hierarchical_labels"]
FileSystemPath = str | Path
ParameterScalar = str | int | float | bool | None


class BlockPinManifest(TypedDict):
    """One external facade pin and its stable KiCad pin number."""

    name: str
    number: str
    electrical_type: str


class BlockParameterInput(TypedDict, total=False):
    """Parameter declaration accepted by creation and configuration APIs."""

    name: Required[str]
    type: NotRequired[str]
    default: NotRequired[ParameterScalar]
    description: NotRequired[str]


class BlockParameter(TypedDict):
    """Validated, normalized parameter declaration stored in a manifest."""

    name: str
    type: ParameterType
    default: str
    description: str


class BlockVariantInput(TypedDict, total=False):
    """Variant source accepted when creating a block."""

    value: Required[str]
    path: FileSystemPath
    file: FileSystemPath
    layout_id: str


class BlockVariantManifest(TypedDict):
    """Persisted implementation variant and its optional PCB association."""

    value: str
    file: str
    layout_id: NotRequired[str]


class BlockLayoutInput(TypedDict, total=False):
    """PCB layout source accepted when creating a block."""

    id: str
    name: str
    path: FileSystemPath
    file: FileSystemPath
    project_path: FileSystemPath
    project_file: FileSystemPath
    ports: dict[str, str]
    legacy: bool


class BlockLayoutManifest(TypedDict):
    """Persisted PCB layout asset and its implementation associations."""

    id: str
    name: str
    file: str
    project_file: str | None
    ports: NotRequired[dict[str, str]]
    implementations: NotRequired[list[str]]


class BlockProfileInput(TypedDict, total=False):
    """Named implementation/profile configuration accepted by the API."""

    name: Required[str]
    id: str
    description: str
    variant: str
    parameters: dict[str, ParameterScalar]
    layout: str


class BlockProfileManifest(TypedDict):
    """Validated per-instance configuration profile stored in a manifest."""

    id: str
    name: str
    description: str
    variant: str | None
    parameters: dict[str, str]
    layout: str | None


class ResolvedProfileConfiguration(TypedDict):
    """Complete field values resolved from one stored profile."""

    id: str
    variant: str
    parameters: dict[str, str]
    layout: str


class BlockDependencyOccurrence(TypedDict):
    """One child block occurrence detected in a parent implementation."""

    child: BlockRecord
    reference: str
    position_mm: tuple[float, float]
    orientation: int
    pin_map: dict[str, str | None]


class BlockDependencyManifest(TypedDict):
    """Persisted identity, placement and connectivity for one child block."""

    instance_id: str
    reference: str
    position_mm: list[float]
    orientation: int
    pin_map: dict[str, str | None]
    block_id: str
    name: str
    path: str
    revision: NotRequired[str | None]


class BlockLayoutConfiguration(TypedDict):
    """Fields copied together when synchronizing PCB layout metadata."""

    layouts: list[BlockLayoutManifest]
    default_layout: str | None
    layout_file: str | None
    layout_project_file: str | None
    layout_ports: NotRequired[dict[str, str]]


class BlockManifest(TypedDict):
    """Validated on-disk manifest for one shared or project-local block."""

    schema_version: int
    block_id: str
    name: str
    slug: str
    description: str
    version: int
    revision: str
    created_by: str
    interface_source: InterfaceSource
    library_nickname: str
    symbol_name: str
    catalog_file: str
    catalog_symbol_hash: NotRequired[str]
    symbol_file: str
    implementation_file: str
    layout_file: str | None
    layout_project_file: str | None
    layouts: list[BlockLayoutManifest]
    default_layout: str | None
    reference_prefix: str
    pins: list[BlockPinManifest]
    parameters: list[BlockParameter]
    variants: list[BlockVariantManifest]
    profiles: list[BlockProfileManifest]
    dependencies: list[BlockDependencyManifest]
    files: list[str]
    hashes: dict[str, str]
    default_variant: NotRequired[str]
    layout_ports: NotRequired[dict[str, str]]
    local_copy: NotRequired[bool]
    local_id: NotRequired[str]
    source_block_id: NotRequired[str]
    source_path: NotRequired[str]
    source_version: NotRequired[int]
    source_revision: NotRequired[str | None]
    base_hashes: NotRequired[dict[str, str]]
    base_dependencies: NotRequired[list[BlockDependencyManifest]]
    base_parameters: NotRequired[list[BlockParameter]]
    base_variants: NotRequired[list[BlockVariantManifest]]
    base_profiles: NotRequired[list[BlockProfileManifest]]
    base_layouts: NotRequired[BlockLayoutConfiguration]
