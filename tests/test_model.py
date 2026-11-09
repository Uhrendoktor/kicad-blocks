from __future__ import annotations

from pathlib import Path
import sys
import types
import uuid

PLUGIN = Path(__file__).resolve().parents[1] / "plugin" / "kicad-blocks"
sys.path.insert(0, str(PLUGIN))

from kicad_blocks.model import block_geometry, parse_pins, slugify  # noqa: E402


def test_parse_pins_and_aliases():
    pins = parse_pins("VIN:input, GND, SDA:bidir, ALERT:tristate")
    assert [pin.shape_token for pin in pins] == [
        "input", "passive", "bidirectional", "tri_state"
    ]
    assert [(pin.name, pin.electrical_type) for pin in pins] == [
        ("VIN", "input"),
        ("GND", "passive"),
        ("SDA", "bidirectional"),
        ("ALERT", "tristate"),
    ]


def test_pin_validation():
    for bad in ("", "A:unknown", "VIN:input,vin:output", "bad name:input"):
        try:
            parse_pins(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Expected invalid pin list: {bad}")


def test_slugify():
    assert slugify("DC/DC Converter 3.3V") == "dc_dc_converter_3_3v"


def test_block_geometry():
    height, left, right = block_geometry(5)
    assert height >= 20.32
    assert (left, right) == (3, 2)


def _install_fake_kipy(monkeypatch):
    """Minimal IPC wrapper doubles; exercise the plugin's complete API call sequence."""
    class Vector2:
        def __init__(self, x=0, y=0):
            self.x, self.y = x, y

        @classmethod
        def from_xy_mm(cls, x, y):
            return cls(x, y)

    class Enum:
        HA_LEFT = 1
        VA_BOTTOM = 2
        VA_TOP = 3
        SLSH_INPUT = 10
        SLSH_OUTPUT = 11
        SLSH_BIDI = 12
        SLSH_TRISTATE = 13
        SLSH_PASSIVE = 14
        SLSS_RIGHT = 20
        SLSS_LEFT = 21
        SHS_LEFT = 30
        SHS_RIGHT = 31

    class TextAttributes:
        def __init__(self):
            self.size = None
            self.horizontal_alignment = None
            self.vertical_alignment = None

    class Text:
        def __init__(self):
            self.value = ""
            self.position = None
            self.attributes = TextAttributes()

    class Field:
        def __init__(self):
            self.name = ""
            self.text = Text()
            self.visible = False

    class SheetPin:
        def __init__(self):
            self.text = Text()
            self.position = None
            self.side = None
            self.spin_style = None
            self.shape = None

    class SheetSymbol:
        def __init__(self):
            self.id = types.SimpleNamespace(value=str(uuid.uuid4()))
            self.name_field = Field()
            self.filename_field = Field()
            self.position = None
            self.size = None
            self.pins = []

    class HierarchicalLabel:
        def __init__(self):
            self.text = Text()
            self.position = None
            self.spin_style = None
            self.shape = None

    class FakeChild:
        def __init__(self):
            self.labels = []

        def begin_commit(self):
            return object()

        def create_items(self, items):
            self.labels.extend(items)
            return items

        def push_commit(self, commit, message):
            return None

        def drop_commit(self, commit):
            return None

    class FakeSchematic:
        def __init__(self, project_dir):
            self.project_dir = Path(project_dir)
            self.sheet = None
            self.child = FakeChild()
            self.saved = False
            self.commits = []

        def get_project(self):
            return types.SimpleNamespace(path=str(self.project_dir))

        def get_sheet_symbols(self):
            return [self.sheet] if self.sheet else []

        def begin_commit(self):
            token = object()
            self.commits.append(token)
            return token

        def create_items(self, items):
            self.sheet = items[0]
            return [self.sheet]

        def update_items(self, items):
            self.sheet = items[0]
            return [self.sheet]

        def push_commit(self, commit, message):
            return None

        def drop_commit(self, commit):
            return None

        def get_hierarchy(self):
            return [
                types.SimpleNamespace(
                    name=self.sheet.name_field.text.value,
                    filename=self.sheet.filename_field.text.value,
                )
            ]

        def for_sheet(self, instance):
            assert instance.filename == self.sheet.filename_field.text.value
            return self.child

        def save(self):
            self.saved = True
            output = self.project_dir / self.sheet.filename_field.text.value
            output.write_text("(kicad_sch)\\n", encoding="utf-8")

        def remove_items(self, items):
            self.sheet = None

    kipy = types.ModuleType("kipy")
    geometry = types.ModuleType("kipy.geometry")
    geometry.Vector2 = Vector2
    schematic_types = types.ModuleType("kipy.schematic_types")
    schematic_types.SheetPin = SheetPin
    schematic_types.SheetSymbol = SheetSymbol
    schematic_types.HierarchicalLabel = HierarchicalLabel

    proto = types.ModuleType("kipy.proto")
    common = types.ModuleType("kipy.proto.common")
    common_types = types.ModuleType("kipy.proto.common.types")
    common_types.HorizontalAlignment = Enum
    common_types.VerticalAlignment = Enum
    proto_schematic = types.ModuleType("kipy.proto.schematic")
    schematic_pb = types.ModuleType("kipy.proto.schematic.schematic_types_pb2")
    schematic_pb.SchematicLabelShape = Enum
    schematic_pb.SchematicLabelSpinStyle = Enum
    schematic_pb.SheetSide = Enum

    modules = {
        "kipy": kipy,
        "kipy.geometry": geometry,
        "kipy.schematic_types": schematic_types,
        "kipy.proto": proto,
        "kipy.proto.common": common,
        "kipy.proto.common.types": common_types,
        "kipy.proto.schematic": proto_schematic,
        "kipy.proto.schematic.schematic_types_pb2": schematic_pb,
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    return FakeSchematic


def test_plugin_creates_native_sheet_pins_and_child_labels(tmp_path, monkeypatch):
    FakeSchematic = _install_fake_kipy(monkeypatch)
    from kicad_blocks.plugin import create_encapsulated_block

    schematic = FakeSchematic(tmp_path)
    pins = parse_pins("VIN:input, GND:passive, VOUT:output, EN:input")

    child_path = create_encapsulated_block(
        schematic, "Buck Converter", pins, 100.0, 80.0
    )

    assert child_path.is_file()
    assert child_path.parent == tmp_path
    assert child_path.name == "buck_converter_block_01.kicad_sch"
    assert schematic.saved is True
    assert schematic.sheet.filename_field.text.value == child_path.name
    assert schematic.sheet.name_field.text.value == "Buck Converter"
    assert len(schematic.sheet.pins) == 4
    assert [pin.text.value for pin in schematic.sheet.pins] == [
        "VIN", "GND", "VOUT", "EN"
    ]
    assert [label.text.value for label in schematic.child.labels] == [
        "VIN", "GND", "VOUT", "EN"
    ]
    assert [label.shape for label in schematic.child.labels] == [10, 14, 11, 10]
    assert schematic.sheet.pins[0].side == 30
    assert schematic.sheet.pins[-1].side == 31


def test_plugin_refuses_duplicate_sheet_name(tmp_path, monkeypatch):
    FakeSchematic = _install_fake_kipy(monkeypatch)
    from kicad_blocks.plugin import create_encapsulated_block

    schematic = FakeSchematic(tmp_path)
    pins = parse_pins("VIN:input")
    create_encapsulated_block(schematic, "Power", pins, 80.0, 80.0)

    try:
        create_encapsulated_block(schematic, "Power", pins, 150.0, 80.0)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("Expected duplicate sheet name to be rejected")
