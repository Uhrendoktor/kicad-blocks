"""Tests for the shared KiCad IPC entrypoint bootstrap."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugin" / "kicad-blocks"
ENTRYPOINTS = PLUGIN_ROOT / "entrypoints"
BOOTSTRAP_PATH = ENTRYPOINTS / "_bootstrap.py"
SPEC = importlib.util.spec_from_file_location("kicad_blocks_entrypoint_bootstrap_test", BOOTSTRAP_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load the shared entrypoint bootstrap.")
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class TestEntrypointBootstrap(unittest.TestCase):
    def test_plugin_root_is_inserted_once_before_loading_the_package(self):
        original_path = list(sys.path)
        isolated_path = [item for item in original_path if item != str(PLUGIN_ROOT)]
        try:
            with (
                patch.object(sys, "path", isolated_path),
                patch.object(bootstrap, "import_module", return_value=object()) as import_module,
            ):
                first = bootstrap.load_plugin()
                second = bootstrap.load_plugin()
                self.assertIs(first, second)
                self.assertEqual(sys.path.count(str(PLUGIN_ROOT)), 1)
                import_module.assert_has_calls(
                    [unittest.mock.call("kicad_blocks.plugin")] * 2
                )
        finally:
            sys.path[:] = original_path

    def test_action_dispatch_uses_the_shared_plugin_loader(self):
        plugin = Mock()
        with patch.object(bootstrap, "load_plugin", return_value=plugin) as load_plugin:
            bootstrap.run_action("create-block")
        load_plugin.assert_called_once_with()
        plugin.run_action.assert_called_once_with("create-block")

    def test_manifest_entrypoints_import_the_single_shared_bootstrap(self):
        expected = {"action_hub.py", "browse_blocks.py", "create_block.py", "open_implementation.py"}
        for filename in expected:
            source = (ENTRYPOINTS / filename).read_text(encoding="utf-8")
            with self.subTest(entrypoint=filename):
                self.assertIn("_bootstrap", source)
                self.assertNotIn("sys.path.insert", source)


if __name__ == "__main__":
    unittest.main()
