"""Unit tests for KiCad PCB net-reference serialization rules."""

from pathlib import Path
import sys
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.pcb_layout import _Quoted, _dump, _rewrite_nets, parse_sexpr


class PCBNetRewriteTests(unittest.TestCase):
    def test_named_pad_net_keeps_mapped_code_and_net_name(self):
        pad = parse_sexpr('(pad "1" smd rect (net 7 "SW"))')
        _rewrite_nets(pad, {7: (2, "VIN_BOARD")}, include_net_name=True)
        self.assertEqual(_dump(pad), '(pad "1" smd rect (net 2 "VIN_BOARD"))')
        self.assertIsInstance(pad[4][2], _Quoted)

    def test_track_net_remains_numeric_only(self):
        segment = parse_sexpr('(segment (start 1 2) (end 3 4) (net 7))')
        _rewrite_nets(segment, {7: (9, "X1_SW")}, include_net_name=False)
        self.assertEqual(
            _dump(segment),
            '(segment (start 1 2) (end 3 4) (net 9))',
        )

    def test_unknown_net_code_is_rejected(self):
        segment = parse_sexpr('(segment (net 99))')
        with self.assertRaisesRegex(ValueError, "undefined source net code 99"):
            _rewrite_nets(segment, {7: (9, "X1_SW")}, include_net_name=False)


if __name__ == "__main__":
    unittest.main()
