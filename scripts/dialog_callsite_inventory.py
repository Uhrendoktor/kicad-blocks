"""Generate and verify the native Tk message-dialog call-site inventory."""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "plugin" / "kicad-blocks" / "kicad_blocks" / "plugin.py"
CATALOG_PATH = ROOT / "tests" / "dialog_callsite_catalog.json"
DEFAULT_OUTPUT = ROOT / "docs" / "dialog-inventory.md"
DIALOG_METHODS = {
    "messagebox.showerror",
    "messagebox.showwarning",
    "messagebox.showinfo",
    "messagebox.askyesno",
    "messagebox.askokcancel",
    "messagebox.askretrycancel",
    "messagebox.askyesnocancel",
    "simpledialog.askstring",
}
COVERAGE_LABELS = {
    "tested": "Direct native-dialog result and downstream behavior are asserted",
    "partial": "The owning action is tested; this dialog branch is not directly asserted",
    "uncovered": "No direct native-dialog path contract exists yet",
}

_CATALOG_OBJECT_ERROR = "Native dialog catalog must be a JSON object."
_CATALOG_VERSION_ERROR = "Unsupported native dialog catalog schema_version."
_CALL_SITES_LIST_ERROR = "Native dialog catalog call_sites must be a list."
_CALL_SITE_RECORD_ERROR = "Native dialog catalog entries must be JSON objects."
_CALL_SITE_IDS_ERROR = "Native dialog call-site IDs must be unique."
_CATALOG_SOURCE_ERROR = "Native dialog catalog differs from plugin.py call sites."


class DialogCallSite(TypedDict):
    """One native-dialog call discovered from the production plugin AST."""

    id: str
    owner: str
    method: str
    line: int


class DialogCatalogEntry(TypedDict):
    """Coverage evidence and rationale for one dialog call site."""

    id: str
    owner: str
    method: str
    line: int
    coverage: str
    evidence: list[str]
    rationale: str


class DialogCatalog(TypedDict):
    """Typed top-level structure of the committed native-dialog catalog."""

    schema_version: int
    source: str
    call_sites: list[DialogCatalogEntry]


def _dotted_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted_name(node.value)}.{node.attr}"
    return ""


class DialogCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.function_stack: list[str] = []
        self.counts: dict[tuple[str, str], int] = {}
        self.calls: list[DialogCallSite] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in node.args.defaults:
            self.visit(default)
        self.function_stack.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in node.args.defaults:
            self.visit(default)
        self.function_stack.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        method = _dotted_name(node.func)
        if method in DIALOG_METHODS:
            owner = ".".join(self.function_stack) or "<module>"
            key = (owner, method)
            ordinal = self.counts.get(key, 0) + 1
            self.counts[key] = ordinal
            self.calls.append(
                {
                    "id": f"{owner}::{method}#{ordinal}",
                    "owner": owner,
                    "method": method,
                    "line": node.lineno,
                }
            )
        self.generic_visit(node)


def discover_dialog_calls(source_path: Path = SOURCE_PATH) -> list[DialogCallSite]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    visitor = DialogCallVisitor()
    visitor.visit(tree)
    return sorted(visitor.calls, key=lambda item: item["line"])


def _read_catalog(path: Path) -> DialogCatalog:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError(_CATALOG_OBJECT_ERROR)
    return cast("DialogCatalog", loaded)


def _catalog_calls(catalog: DialogCatalog) -> list[DialogCatalogEntry]:
    if catalog.get("schema_version") != 1:
        raise ValueError(_CATALOG_VERSION_ERROR)
    calls = catalog.get("call_sites")
    if not isinstance(calls, list):
        raise TypeError(_CALL_SITES_LIST_ERROR)
    return calls


def _validate_catalog_entry(item: DialogCatalogEntry) -> None:
    if not isinstance(item, dict):
        raise TypeError(_CALL_SITE_RECORD_ERROR)
    if not item.get("owner") or item.get("method") not in DIALOG_METHODS:
        message = f"Invalid native dialog call-site record: {item!r}"
        raise ValueError(message)
    if not str(item.get("rationale", "")).strip():
        message = f"Dialog call site {item['id']!r} needs a rationale."
        raise ValueError(message)
    evidence = item.get("evidence", [])
    for path in evidence:
        if not (ROOT / path).is_file():
            message = f"Dialog call site {item['id']!r} references missing evidence {path!r}."
            raise ValueError(message)
    if item["coverage"] == "tested" and not evidence:
        message = f"Tested dialog call site {item['id']!r} needs test evidence."
        raise ValueError(message)


def _validate_catalog_entries(calls: list[DialogCatalogEntry]) -> None:
    ids = [item.get("id") for item in calls]
    if len(ids) != len(set(ids)):
        raise ValueError(_CALL_SITE_IDS_ERROR)
    invalid = sorted({item.get("coverage") for item in calls} - set(COVERAGE_LABELS))
    if invalid:
        message = f"Unknown dialog call-site coverage: {invalid!r}"
        raise ValueError(message)
    for item in calls:
        _validate_catalog_entry(item)


def _validate_catalog_matches_source(calls: list[DialogCatalogEntry]) -> None:
    actual = discover_dialog_calls()
    expected_ids = [item["id"] for item in calls]
    actual_ids = [item["id"] for item in actual]
    if actual_ids != expected_ids:
        missing = sorted(set(actual_ids) - set(expected_ids))
        stale = sorted(set(expected_ids) - set(actual_ids))
        message = f"{_CATALOG_SOURCE_ERROR} New/unregistered: {missing!r}; stale: {stale!r}."
        raise ValueError(message)


def load_catalog(path: Path = CATALOG_PATH) -> DialogCatalog:
    catalog = _read_catalog(path)
    calls = _catalog_calls(catalog)
    _validate_catalog_entries(calls)
    _validate_catalog_matches_source(calls)
    return catalog


def render_inventory(catalog: DialogCatalog | None = None) -> str:
    catalog = catalog if catalog is not None else load_catalog()
    calls = discover_dialog_calls()
    by_id: dict[str, DialogCatalogEntry] = {item["id"]: item for item in catalog["call_sites"]}
    counts = {
        status: sum(item["coverage"] == status for item in catalog["call_sites"])
        for status in COVERAGE_LABELS
    }
    lines = [
        "<!-- Generated by scripts/dialog_callsite_inventory.py; edit tests/dialog_callsite_catalog.json instead. -->",
        "# Native message-dialog inventory",
        "",
        "This inventory is generated from the production plugin's Python AST. It records every messagebox and simpledialog call site, its owning operation, the available automated evidence, and the remaining gap. These are native OS/Tk dialogs rather than controls inside the reusable in-process window contract.",
        "",
        f"Source: {catalog['source']} — {len(calls)} call sites.",
        "",
        "| Coverage | Call sites | Meaning |",
        "|---|---:|---|",
        f"| Tested | {counts['tested']} | {COVERAGE_LABELS['tested']}. |",
        f"| Partial | {counts['partial']} | {COVERAGE_LABELS['partial']}. |",
        f"| Uncovered | {counts['uncovered']} | {COVERAGE_LABELS['uncovered']}. |",
        "",
        "## Call sites",
        "",
        "| Source line | Owning operation | Dialog API | Coverage | Automated evidence |",
        "|---:|---|---|---|---|",
    ]
    for call in calls:
        record = by_id[call["id"]]
        evidence = ", ".join(record.get("evidence", [])) or "—"
        lines.append(
            f"| {call['line']} | {call['owner']} | {call['method']} | "
            f"{record['coverage']} | {evidence} |"
        )
    lines.extend(["", "## Coverage boundaries", ""])
    for status in ("tested", "partial", "uncovered"):
        selected = [item for item in catalog["call_sites"] if item["coverage"] == status]
        if not selected:
            continue
        lines.append(f"### {status.title()}")
        lines.append("")
        for item in selected:
            lines.append(f"- {item['id']} — {item['rationale']}")
        lines.append("")
    lines.extend(
        [
            "## Interpretation",
            "",
            "- Tested means a test patches the native dialog function, asserts the prompt/choice or resulting branch, and checks the application effect. It does not claim the native OS window was rendered or its platform-specific focus/ownership was verified.",
            "- Partial means the owning action has tests, but the specific error/confirmation/prompt path is not directly exercised.",
            "- Uncovered means no direct call-site behavior contract exists yet.",
            "",
            f"Coverage is recorded for all {len(calls)} native message-dialog call sites; uncovered paths are identified explicitly. "
            "These in-process tests do not replace native desktop accessibility, "
            "OS-specific dialog ownership, or cross-platform rendering checks.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        catalog = load_catalog()
        rendered = render_inventory(catalog)
    except (OSError, ValueError, json.JSONDecodeError, SyntaxError) as exc:
        print(f"Native dialog inventory failed: {exc}", file=sys.stderr)
        return 1
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != rendered:
            print(
                f"{args.output} is stale; run scripts/dialog_callsite_inventory.py.",
                file=sys.stderr,
            )
            return 1
        print(f"Native dialog inventory is current: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"Generated native dialog inventory: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
