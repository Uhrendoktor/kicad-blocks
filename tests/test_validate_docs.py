"""Regression tests for the documentation link and accessibility checker."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import validate_docs


class DocumentationValidationTests(unittest.TestCase):
    def _validate(self, content: str) -> list[str]:
        with tempfile.TemporaryDirectory(
            prefix="kicad-blocks-doc-check-",
            dir=validate_docs.ROOT / "docs",
        ) as temporary:
            path = Path(temporary) / "fixture.md"
            path.write_text(content, encoding="utf-8")
            return validate_docs.validate_file(path)

    def test_checks_markdown_and_html_local_links_and_heading_anchors(self):
        with tempfile.TemporaryDirectory(
            prefix="kicad-blocks-doc-targets-",
            dir=validate_docs.ROOT / "docs",
        ) as temporary:
            directory = Path(temporary)
            (directory / "target.md").write_text(
                "# Existing section\\n",
                encoding="utf-8",
            )
            source = directory / "fixture.md"
            source.write_text(
                "# Fixture\\n"
                "[Valid Markdown link](target.md#existing-section)\\n"
                "<a href='target.md#missing-section'>Broken HTML anchor</a>\\n"
                "[Missing page](missing.md)\\n",
                encoding="utf-8",
            )
            errors = validate_docs.validate_file(source)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("missing heading anchor #missing-section" in item for item in errors))
        self.assertTrue(any("local target does not exist: missing.md" in item for item in errors))

    def test_requires_alt_text_for_markdown_and_html_images(self):
        errors = self._validate(
            "![ ](diagram.png)\\n"
            "<img src='diagram.png'>\\n"
            "<img src='diagram.png' alt=''>\\n"
            "<img src='diagram.png' alt='Circuit diagram'>\\n"
        )
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("Markdown image is missing alt text" in item for item in errors))
        self.assertEqual(
            sum("HTML image is missing alt text" in item for item in errors),
            2,
        )

    def test_ignores_links_and_images_inside_fenced_code(self):
        fence = chr(96) * 3
        errors = self._validate(
            "# Fixture\\n"
            f"{fence}html\\n"
            "[missing page](missing.md)\\n"
            "<a href='missing.md'>missing link</a>\\n"
            "<img src='missing.png'>\\n"
            f"{fence}\\n"
        )
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
