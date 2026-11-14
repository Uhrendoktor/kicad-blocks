"""Domain schemas for KiCad PCB project settings and block placement."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class KiCadNetclassDefinition(TypedDict, total=False):
    """Named KiCad netclass fields used by the block-layout importer."""

    name: str
    priority: int
    nets: list[str]
    clearance: float
    trace_width: float
    via_diameter: float
    via_drill: float
    diff_pair_width: float
    diff_pair_gap: float
    microvia_diameter: float
    microvia_drill: float
    microvia_min_height: float
    track_widths: list[float]
    via_sizes: list[dict[str, float]]
    microvia_sizes: list[dict[str, float]]


class KiCadNetclassPattern(TypedDict):
    """One project rule assigning nets matching a pattern to a netclass."""

    pattern: str
    netclass: str


class KiCadNetSettings(TypedDict, total=False):
    """Net-specific subset of a KiCad project-settings document."""

    classes: list[KiCadNetclassDefinition]
    netclass_assignments: dict[str, str | list[str]]
    netclass_patterns: list[KiCadNetclassPattern]
    net_chain_classes: dict[str, str]
    net_chain_netclasses: dict[str, str]


class KiCadProjectMetadata(TypedDict, total=False):
    """Project metadata fields updated when a board copy is written."""

    filename: str
    version: int


class KiCadProjectData(TypedDict, total=False):
    """KiCad project-settings sections read or written by the layout importer."""

    meta: KiCadProjectMetadata
    net_settings: KiCadNetSettings


class AppliedNetclassPattern(TypedDict):
    """A source netclass pattern expanded onto matching destination nets."""

    pattern: str
    netclass: str
    resolved_nets: list[str]


class ProjectSettingsMergeResult(TypedDict):
    """Merged project-settings data plus observable mapping decisions."""

    data: KiCadProjectData
    output: str
    merged_classes: list[str]
    assigned_nets: list[str]
    ignored_patterns: bool
    applied_patterns: list[AppliedNetclassPattern]
    unmatched_patterns: list[str]
    ignored_chain_classes: bool
    merged_chain_names: list[str]
    unmatched_chains: list[str]


class ResolvedBlockLayout(TypedDict):
    """Selected PCB layout asset, including legacy single-layout metadata."""

    id: str | None
    name: str
    file: str
    project_file: str | None
    ports: NotRequired[dict[str, str] | None]


class BlockLayoutMergeResult(TypedDict):
    """One imported block layout and the board objects created for it."""

    output: str
    block: str
    layout_id: str | None
    layout_name: str | None
    instance_reference: str
    rotation_deg: float
    project_file: str | None
    merged_netclasses: list[str]
    assigned_project_nets: list[str]
    ignored_project_patterns: bool
    applied_project_patterns: list[AppliedNetclassPattern]
    unmatched_project_patterns: list[str]
    ignored_project_chain_classes: bool
    merged_project_chain_names: list[str]
    unmatched_project_chains: list[str]
    merged_board_chains: list[str]
    footprints: list[str]
    interactive_item_uuids: list[str]
    footprint_count: int
    object_count: int
    new_net_count: int
    new_nets: list[str]
    skipped_top_level_items: list[str]


class BatchBlockLayoutMergeResult(TypedDict):
    """Aggregate result from applying multiple block layouts to one board copy."""

    output: str
    project_file: str | None
    block_count: int
    placements: list[BlockLayoutMergeResult]
    footprints: list[str]
    footprint_count: int
    object_count: int
    new_net_count: int
    new_nets: list[str]
    merged_netclasses: list[str]
    assigned_project_nets: list[str]
    applied_project_patterns: list[AppliedNetclassPattern]
    unmatched_project_patterns: list[str]
    merged_project_chain_names: list[str]
    unmatched_project_chains: list[str]
    merged_board_chains: list[str]
    skipped_top_level_items: list[str]
    ignored_project_patterns: bool
    ignored_project_chain_classes: bool


class PCBPlacementEntry(TypedDict):
    """One independently selectable imported block group on a board."""

    id: str
    instance_reference: str
    block: str
    layout_name: str
    item_uuids: list[str]


class PCBPlacementManifest(TypedDict):
    """Board-bound placement sidecar used by the native move action."""

    schema_version: int
    board_path: str
    placements: list[PCBPlacementEntry]
