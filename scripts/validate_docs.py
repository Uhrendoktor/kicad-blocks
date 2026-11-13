#!/usr/bin/env python3
"""Validate local Markdown links, heading fragments, and image alt text."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_ROOTS = (ROOT / "docs", ROOT / "examples")
ROOT_MARKDOWN = (ROOT / "README.md", ROOT / "CHANGELOG.md")

LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")


def markdown_files() -> list[Path]:
    """Return the Markdown sources covered by the documentation check."""
    files = list(ROOT_MARKDOWN)
    for directory in MARKDOWN_ROOTS:
        if directory.is_dir():
            files.extend(directory.rglob("*.md"))
    return sorted({path.resolve() for path in files if path.is_file()})


def strip_fenced_code(text: str) -> str:
    """Replace fenced-code contents with blank lines while preserving line numbers."""
    result: list[str] = []
    fence_char: str | None = None
    fence_size = 0
    for line in text.splitlines():
        match = FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if fence_char is None:
                fence_char = marker[0]
                fence_size = len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_size:
                fence_char = None
                fence_size = 0
            result.append("")
        elif fence_char is not None:
            result.append("")
        else:
            result.append(line)
    return "\n".join(result)


def slugify_heading(text: str) -> str:
    """Approximate GitHub's automatic heading slug for ordinary Markdown headings."""
    value = re.sub(r"`", "", text).strip().casefold()
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"[^\w\- ]", "", value, flags=re.UNICODE)
    return re.sub(r"\s+", "-", value)


def heading_slugs(path: Path) -> set[str]:
    """Collect heading slugs, including GitHub's duplicate-heading suffixes."""
    source = strip_fenced_code(path.read_text(encoding="utf-8"))
    used: dict[str, int] = {}
    slugs: set[str] = set()
    for line in source.splitlines():
        match = HEADING_RE.match(line)
        if not match:
            continue
        slug = slugify_heading(match.group(1))
        count = used.get(slug, 0)
        used[slug] = count + 1
        slugs.add(slug if count == 0 else f"{slug}-{count}")
    return slugs


def destination(raw: str) -> str:
    """Normalize a simple Markdown link destination and remove an optional title."""
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        value = value[1:value.index(">")]
    else:
        value = value.split(maxsplit=1)[0]
    return unquote(value)


def validate_file(path: Path) -> list[str]:
    """Return documentation errors found in one Markdown source."""
    errors: list[str] = []
    source = strip_fenced_code(path.read_text(encoding="utf-8"))
    source_slugs = heading_slugs(path)
    for line_number, line in enumerate(source.splitlines(), start=1):
        for image_match in IMAGE_RE.finditer(line):
            if not image_match.group(1).strip():
                errors.append(
                    f"{path.relative_to(ROOT)}:{line_number}: image is missing alt text"
                )
        for match in LINK_RE.finditer(line):
            target = destination(match.group(1))
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or target.startswith("//"):
                continue
            resolved = path if not parsed.path else (path.parent / parsed.path).resolve()
            if not resolved.is_file():
                errors.append(
                    f"{path.relative_to(ROOT)}:{line_number}: local target does not exist: {target}"
                )
                continue
            if parsed.fragment:
                target_slugs = source_slugs if resolved == path else heading_slugs(resolved)
                if parsed.fragment not in target_slugs:
                    errors.append(
                        f"{path.relative_to(ROOT)}:{line_number}: missing heading anchor "
                        f"#{parsed.fragment} in {resolved.relative_to(ROOT)}"
                    )
    return errors


def main() -> int:
    """Validate all project Markdown files without making network requests."""
    files = markdown_files()
    errors = [error for path in files for error in validate_file(path)]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(
        f"Documentation checks passed: {len(files)} Markdown files, "
        "local links, heading anchors, and image alt text."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
