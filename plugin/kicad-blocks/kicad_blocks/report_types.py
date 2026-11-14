"""Typed result contracts for KiCad Blocks user-facing reports."""

from __future__ import annotations

from typing import Literal, TypedDict


class BlockCompositionStepResult(TypedDict):
    """Outcome of composing one block's direct child instances."""

    placed: list[str]
    labels: int


class BlockCompositionItem(TypedDict):
    """Composition result for one block in a recursive composition operation."""

    block: str
    placed: list[str]
    labels: int


class BlockCompositionReport(TypedDict):
    """Summary of a complete recursive block-composition operation."""

    root: str
    composed: list[BlockCompositionItem]


class BomExportReport(TypedDict):
    """Generated recursive BOM artifact and row counts."""

    output: str
    blocks: list[str]
    block_count: int
    row_count: int


class BlockErcReport(TypedDict):
    """KiCad ERC result for one block implementation."""

    block: str
    block_id: str
    exit_code: int
    violations: bool
    report: str


class BlockValidationItem(TypedDict):
    """Manifest, asset, dependency and optional ERC results for one block."""

    path: str
    name: str
    block_id: str
    status: Literal["passed", "failed", "warnings"]
    errors: list[str]
    warnings: list[str]
    missing_assets: list[str]
    hash_mismatches: list[str]
    erc: list[BlockErcReport]
    dependency_count: int


class LibraryValidationReport(TypedDict):
    """Aggregate result written by the library validation command."""

    library: str
    report: str
    block_count: int
    passed_count: int
    warning_count: int
    failed_count: int
    status: Literal["passed", "failed", "warnings"]
    blocks: list[BlockValidationItem]
