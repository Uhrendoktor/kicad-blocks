from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import parse_pins
from kicad_blocks.repository import (
    add_library_root,
    create_block,
    ensure_library_gitignore,
    make_local_copy,
)


class LibraryGitignoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_ignore_file_covers_generated_content_and_preserves_custom_rules(self) -> None:
        library = self.root / "shared-library"
        library.mkdir()
        ignore_file = library / ".gitignore"
        ignore_file.write_text("# My library-specific rules\ncustom-output/\n", encoding="utf-8")

        first = ensure_library_gitignore(library)
        first_content = first.read_text(encoding="utf-8")
        second = ensure_library_gitignore(library)

        self.assertEqual(first, second)
        self.assertEqual(first_content, second.read_text(encoding="utf-8"))
        self.assertTrue(first_content.startswith("# My library-specific rules\ncustom-output/\n"))
        self.assertEqual(first_content.count("# BEGIN KiCad Blocks generated-file exclusions"), 1)
        for pattern in (
            "previews/",
            ".cache/",
            "reports/",
            "*.kicad_prl",
            "*-backups/",
            "*.lck",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, first_content)

    def test_registering_existing_library_initializes_ignore_file(self) -> None:
        library = self.root / "shared-library"
        library.mkdir()
        settings = self.root / "settings.json"
        with (
            patch("kicad_blocks.repository.settings_file", return_value=settings),
            patch("kicad_blocks.repository.Path.home", return_value=self.root),
            patch.dict(os.environ, {"KICAD_BLOCKS_LIBRARY": str(library)}),
        ):
            add_library_root(library)

        self.assertTrue((library / ".gitignore").is_file())
        self.assertIn("previews/", (library / ".gitignore").read_text(encoding="utf-8"))

    def test_block_creation_initializes_an_unregistered_library(self) -> None:
        library = self.root / "new-library"
        create_block(library, "Test Block", parse_pins("IN:input, OUT:output"))

        self.assertTrue((library / ".gitignore").is_file())
        self.assertTrue(next(library.glob("*.kicad_block/manifest.json")).is_file())

    def test_project_local_copy_initializes_its_gitignore(self) -> None:
        library = self.root / "shared-library"
        source = create_block(library, "Local Copy", parse_pins("IN:input, OUT:output"))
        project = self.root / "project"
        project.mkdir()

        local = make_local_copy(source, project)

        self.assertTrue((project / ".kicad_blocks" / ".gitignore").is_file())
        self.assertTrue((local.path / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
