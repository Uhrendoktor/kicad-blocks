"""Concrete contracts for persisted KiCad Blocks manifests and IPC reports.

These TypedDicts describe the fields the application actually reads and writes.
Untrusted JSON is validated at its file boundary before it is exposed as one of
these domain records; callers do not pass arbitrary recursive JSON values
through the application.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, NotRequired, TypedDict

ParameterKind = Literal["string", "number", "integer", "boolean"]
ParameterValue = str | int | float | bool
InterfaceSource = Literal["manual", "hierarchical_labels"]
ReportStatus = Literal["passed", "warnings", "failed"]


class PinManifest(TypedDict):
    """Stable facade pin identity persisted in a block manifest."""

    name: str
    number: str
    electrical_type: NotRequired[str]


class ParameterInput(TypedDict):
    """User-supplied parameter declaration accepted by block creation."""

    name: str
    type: NotRequired[str]
    default: NotRequired[str]
    description: NotRequired[str]


class ParameterDefinition(TypedDict):
    """Validated parameter declaration stored in a block manifest."""

    name: str
    type: ParameterKind
    default: str
    description: str


class VariantInput(TypedDict, total=False):
    """Variant source supplied when a block is created."""

    value: str
    path: str | Path
    file: str | Path
    layout_id: str


class VariantDefinition(TypedDict):
    """Validated implementation variant stored in a block manifest."""

    value: str
    file: str
    layout_id: NotRequired[str]


class LayoutInput(TypedDict, total=False):
    """PCB layout source and associations supplied when a block is created."""

    id: str
    name: str
    path: str | Path
    file: str | Path
    project_path: str | Path
    project_file: str | Path
    ports: dict[str, str]
    legacy: bool


class LayoutDefinition(TypedDict):
    """PCB asset and implementation associations stored in a block manifest."""

    id: str
    name: str
    file: str
    project_file: str | None
    ports: NotRequired[dict[str, str]]
    implementations: NotRequired[list[str]]


class ProfileInput(TypedDict, total=False):
    """Configuration profile supplied by a block author."""

    id: str
    name: str
    description: str
    variant: str | None
    parameters: dict[str, ParameterValue]
    layout: str | None


class ProfileDefinition(TypedDict):
    """Validated profile fields stored in a block manifest."""

    id: str
    name: str
    description: str
    variant: str | None
    parameters: dict[str, str]
    layout: str | None


class DependencyManifest(TypedDict):
    """One child-block instance recorded in a parent block manifest."""

    instance_id: str
    reference: str
    position_mm: list[float]
    orientation: int
    pin_map: dict[str, str | None]
    block_id: str
    name: str
    path: str
    revision: NotRequired[str | None]


class LayoutConfiguration(TypedDict, total=False):
    """The subset of a block manifest that defines PCB layout behavior."""

    layouts: list[LayoutDefinition]
    default_layout: str | None
    layout_file: str | None
    layout_project_file: str | None
    layout_ports: dict[str, str]


class _RequiredBlockManifest(TypedDict):
    block_id: str
    name: str
    symbol_file: str
    implementation_file: str
    pins: list[PinManifest]


class BlockManifest(_RequiredBlockManifest, total=False):
    """Validated persisted block metadata and its typed implementation assets."""

    schema_version: int
    slug: str
    description: str
    version: int
    revision: str
    created_by: str
    interface_source: InterfaceSource
    library_nickname: str
    symbol_name: str
    catalog_file: str
    layout_file: str | None
    layout_project_file: str | None
    layouts: list[LayoutDefinition]
    default_layout: str | None
    reference_prefix: str
    parameters: list[ParameterDefinition]
    variants: list[VariantDefinition]
    default_variant: str
    profiles: list[ProfileDefinition]
    dependencies: list[DependencyManifest]
    files: list[str]
    hashes: dict[str, str]
    local_copy: bool
    source_path: str
    source_block_id: str
    source_version: int
    base_hashes: dict[str, str]
    base_dependencies: list[DependencyManifest]
    base_parameters: list[ParameterDefinition]
    base_variants: list[VariantDefinition]
    base_profiles: list[ProfileDefinition]
    base_layouts: LayoutConfiguration


class MergePlacement(TypedDict):
    """Imported board-item identifiers belonging to one schematic block instance."""

    instance_reference: str
    block: str
    layout_name: str
    interactive_item_uuids: list[str]


class PCBMergeResult(TypedDict, total=False):
    """KiCad PCB layout merge result accepted by the placement sidecar writer."""

    placements: list[MergePlacement]
    instance_reference: str
    block: str
    layout_name: str
    interactive_item_uuids: list[str]


class PlacementEntry(TypedDict):
    """Validated board-item group stored in a placement sidecar."""

    id: str
    instance_reference: str
    block: str
    layout_name: str
    item_uuids: list[str]


class PlacementManifest(TypedDict):
    """Versioned sidecar binding a generated board to imported block items."""

    schema_version: int
    board_path: str
    placements: list[PlacementEntry]


class NetClass(TypedDict, total=False):
    """KiCad project netclass fields used for signature and merge decisions."""

    name: str
    priority: int
    nets: list[str]
    clearance: float
    track_width: float
    via_diameter: float
    via_drill: float
    diff_pair_width: float
    diff_pair_gap: float
    diff_pair_via_gap: float
    microvia_diameter: float
    microvia_drill: float
    wire_width: float
    bus_width: float
    line_style: str
    pcb_color: str
    schematic_color: str


class NetClassPattern(TypedDict):
    """One source netclass pattern in a KiCad project file."""

    pattern: str
    netclass: str


NetClassAssignment = str | list[str]


class NetSettings(TypedDict, total=False):
    """KiCad project net settings read or updated during PCB layout merging."""

    classes: list[NetClass]
    netclass_assignments: dict[str, NetClassAssignment]
    netclass_patterns: list[NetClassPattern]


class ProjectMeta(TypedDict, total=False):
    """KiCad project metadata preserved when writing a copied settings file."""

    filename: str
    version: int


class KiCadProjectSettings(TypedDict, total=False):
    """Project JSON fields that the PCB layout merger reads and modifies."""

    meta: ProjectMeta
    net_settings: NetSettings


class ProjectNetSettingsMerge(TypedDict):
    """Summary of netclasses and exact assignments merged into a project."""

    classes: list[str]
    assigned_nets: list[str]
    applied_patterns: list[dict[str, str | list[str]]]
    unmatched_patterns: list[str]


class CompositionStepReport(TypedDict):
    """Composition outcome for one block in a nested dependency tree."""

    block: str
    placed: list[str]
    labels: int


class CompositionReport(TypedDict):
    """Composition outcome for the requested root block."""

    root: str
    composed: list[CompositionStepReport]


class BOMExportReport(TypedDict):
    """Paths and row counts produced by a recursive BOM export."""

    output: str
    blocks: list[str]
    block_count: int
    row_count: int


class ERCReport(TypedDict):
    """KiCad ERC result for one block implementation."""

    block: str
    block_id: str
    exit_code: int
    violations: bool
    report: str


class BlockValidationReport(TypedDict):
    """Machine-readable validation outcome for one library block."""

    path: str
    name: str
    block_id: str
    status: ReportStatus
    errors: list[str]
    warnings: list[str]
    missing_assets: list[str]
    hash_mismatches: list[str]
    erc: list[ERCReport]
    dependency_count: NotRequired[int]


class LibraryValidationReport(TypedDict):
    """Aggregate validation result written for a complete block library."""

    library: str
    report: str
    block_count: int
    passed_count: int
    warning_count: int
    failed_count: int
    status: ReportStatus
    blocks: list[BlockValidationReport]
