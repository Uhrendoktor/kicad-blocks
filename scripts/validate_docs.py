#!/usr/bin/env python3
"""Validate local Markdown/HTML links, heading fragments, and image alt text."""

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
HTML_LINK_RE = re.compile(
    r"""<a\b[^>]*?\bhref\s*=\s*(?:"([^"]+)"|'([^']+)')""",
    re.IGNORECASE | re.DOTALL,
)
HTML_IMAGE_RE = re.compile(r"<img\b([^>]*)>", re.IGNORECASE | re.DOTALL)
HTML_ALT_RE = re.compile(
    r"""(?:^|\s)alt\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""",
    re.IGNORECASE,
)
HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(
    r"^ {0,3}(" + re.escape(chr(96)) + r"{3,}|~{3,})"
)


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
    value = text.replace(chr(96), "").strip().casefold()
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
    """Normalize a simple Markdown or HTML link destination."""
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        value = value[1:value.index(">")]
    else:
        value = value.split(maxsplit=1)[0]
    return unquote(value)


def _line_number(source: str, offset: int) -> int:
    """Return the one-based source line for a regex match offset."""
    return source.count("\n", 0, offset) + 1


def _validate_target(
    target: str,
    *,
    path: Path,
    line_number: int,
    source_slugs: set[str],
) -> list[str]:
    """Validate one local link target and optional heading fragment."""
    errors: list[str] = []
    normalized = destination(target)
    parsed = urlsplit(normalized)
    if parsed.scheme or parsed.netloc or normalized.startswith("//"):
        return errors

    resolved = path if not parsed.path else (path.parent / parsed.path).resolve()
    if not resolved.is_file():
        errors.append(
            f"{path.relative_to(ROOT)}:{line_number}: local target does not exist: {target}"
        )
        return errors

    if parsed.fragment:
        target_slugs = source_slugs if resolved == path else heading_slugs(resolved)
        if parsed.fragment not in target_slugs:
            errors.append(
                f"{path.relative_to(ROOT)}:{line_number}: missing heading anchor "
                f"#{parsed.fragment} in {resolved.relative_to(ROOT)}"
            )
    return errors


def validate_file(path: Path) -> list[str]:
    """Return documentation errors found in one Markdown source."""
    errors: list[str] = []
    source = strip_fenced_code(path.read_text(encoding="utf-8"))
    source_slugs = heading_slugs(path)

    errors.extend(
        [
            f"{path.relative_to(ROOT)}:{_line_number(source, match.start())}: "
            "Markdown image is missing alt text"
            for match in IMAGE_RE.finditer(source)
            if not match.group(1).strip()
        ]
    )

    for match in HTML_IMAGE_RE.finditer(source):
        attributes = match.group(1)
        alt_match = HTML_ALT_RE.search(attributes)
        alt_text = ""
        if alt_match:
            alt_text = next(
                (value for value in alt_match.groups() if value is not None),
                "",
            ).strip()
        if not alt_match or not alt_text:
            errors.append(
                f"{path.relative_to(ROOT)}:{_line_number(source, match.start())}: "
                "HTML image is missing alt text"
            )

    for match in LINK_RE.finditer(source):
        errors.extend(
            _validate_target(
                match.group(1),
                path=path,
                line_number=_line_number(source, match.start()),
                source_slugs=source_slugs,
            )
        )

    for match in HTML_LINK_RE.finditer(source):
        target = next(
            (value for value in match.groups() if value is not None),
            "",
        )
        errors.extend(
            _validate_target(
                target,
                path=path,
                line_number=_line_number(source, match.start()),
                source_slugs=source_slugs,
            )
        )

    return errors


def main() -> int:
    """Validate all project Markdown sources without making network requests."""
    files = markdown_files()
    errors = [error for path in files for error in validate_file(path)]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(
        f"Documentation checks passed: {len(files)} Markdown files, "
        "Markdown/HTML local links, heading anchors, and Markdown/HTML image alt text."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
