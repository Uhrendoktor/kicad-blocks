"""Enforce repository-wide Python typing, annotation, documentation, and architecture rules."""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)
REVIEWED_MODULES = (
    Path("plugin/kicad-blocks/kicad_blocks/json_types.py"),
    Path("plugin/kicad-blocks/kicad_blocks/model.py"),
    Path("plugin/kicad-blocks/kicad_blocks/pcb_placement.py"),
)
PURE_MODULE_IMPORTS = {
    Path("plugin/kicad-blocks/kicad_blocks/model.py"): frozenset(
        {".plugin", ".repository", ".operations", ".composition"}
    ),
    Path("plugin/kicad-blocks/kicad_blocks/pcb_placement.py"): frozenset(
        {".plugin", ".repository", ".operations", ".composition"}
    ),
}


def _git_output(arguments: tuple[str, ...], root: Path = ROOT) -> str:
    """Run a bounded Git query and return its UTF-8 standard output."""
    result = subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def tracked_python_files(root: Path = ROOT) -> tuple[Path, ...]:
    """Return all Git-tracked Python sources and stubs in deterministic order."""
    paths = _git_output(("ls-files", "-z", "--", "*.py", "*.pyi"), root)
    return tuple(root / value for value in sorted(item for item in paths.split("\0") if item))


def _typing_imports(tree: ast.Module) -> tuple[set[str], set[str], set[str], set[str], list[str]]:
    """Resolve aliases used for typing.Any and the built-in object type."""
    any_names: set[str] = set()
    typing_modules: set[str] = set()
    builtins_modules: set[str] = set()
    object_names: set[str] = set()
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"typing", "typing_extensions"}:
                    typing_modules.add(alias.asname or alias.name)
                elif alias.name == "builtins":
                    builtins_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module in {"typing", "typing_extensions"}:
            for alias in node.names:
                if alias.name == "Any":
                    any_names.add(alias.asname or alias.name)
                elif alias.name == "*":
                    violations.append(
                        f"{node.lineno}: wildcard import from {node.module} hides type-policy references"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            for alias in node.names:
                if alias.name == "object":
                    object_names.add(alias.asname or alias.name)
    return any_names, typing_modules, builtins_modules, object_names, violations


def _annotation_roots(tree: ast.Module) -> tuple[ast.AST, ...]:
    """Collect annotations and PEP 695 type-alias expressions."""
    roots: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None:
            roots.append(node.annotation)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns is not None:
            roots.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            roots.append(node.annotation)
        elif hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
            roots.append(node.value)
    return tuple(roots)


def _expanded_expression_nodes(root: ast.AST) -> tuple[ast.AST, ...]:
    """Expand quoted forward annotations while preserving the surrounding syntax."""
    found: list[ast.AST] = []
    pending = [root]
    while pending:
        node = pending.pop()
        found.append(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                nested = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                nested = None
            if nested is not None:
                pending.append(nested)
        pending.extend(ast.iter_child_nodes(node))
    return tuple(found)


def _type_comment_roots(comment: str) -> tuple[ast.AST, ...]:
    """Parse assignment comments and legacy function-signature type comments."""
    try:
        return (ast.parse(comment, mode="eval").body,)
    except SyntaxError:
        pass
    if "->" not in comment:
        return ()
    arguments, result = comment.rsplit("->", maxsplit=1)
    arguments = arguments.strip()
    if arguments.startswith("(") and arguments.endswith(")"):
        arguments = arguments[1:-1].strip()
    roots: list[ast.AST] = []
    for expression in (arguments, result.strip()):
        if not expression:
            continue
        try:
            roots.append(ast.parse(expression, mode="eval").body)
        except SyntaxError:
            continue
    return tuple(roots)


def python_policy_violations(source: str, filename: str = "<memory>") -> tuple[str, ...]:
    """Reject explicit Any references and generic object annotations in Python syntax."""
    try:
        tree = ast.parse(source, filename=filename, type_comments=True)
    except SyntaxError as error:
        return (f"{filename}:{error.lineno or 1}: invalid Python syntax: {error.msg}",)

    any_names, typing_modules, builtins_modules, object_names, violations = _typing_imports(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in any_names:
            violations.append(
                f"{filename}:{node.lineno}: explicit Any reference is prohibited; use a concrete "
                "type, a narrow Protocol, or a structured value type"
            )
        elif (
            isinstance(node, ast.Attribute)
            and node.attr == "Any"
            and isinstance(node.value, ast.Name)
            and node.value.id in typing_modules
        ):
            violations.append(
                f"{filename}:{node.lineno}: {node.value.id}.Any is prohibited; use a concrete "
                "type, a narrow Protocol, or a structured value type"
            )

    roots = list(_annotation_roots(tree))
    for node in ast.walk(tree):
        type_comment = getattr(node, "type_comment", None)
        if isinstance(type_comment, str):
            roots.extend(_type_comment_roots(type_comment))

    for root in roots:
        for node in _expanded_expression_nodes(root):
            if isinstance(node, ast.Name) and (node.id == "object" or node.id in object_names):
                violations.append(
                    f"{filename}:{getattr(node, 'lineno', 1)}: generic object annotation is "
                    "prohibited; use a concrete type, a narrow Protocol, or a structured value type"
                )
            elif (
                isinstance(node, ast.Attribute)
                and node.attr == "object"
                and isinstance(node.value, ast.Name)
                and node.value.id in builtins_modules
            ):
                violations.append(
                    f"{filename}:{getattr(node, 'lineno', 1)}: builtins.object annotation is "
                    "prohibited; use a concrete type, a narrow Protocol, or a structured value type"
                )
    return tuple(sorted(set(violations)))


def annotation_debt(source: str, filename: str = "<memory>") -> Counter[str]:
    """Count missing parameter and return annotations per file."""
    tree = ast.parse(source, filename=filename, type_comments=True)
    debt: Counter[str] = Counter()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parameters = [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]
        if node.args.vararg is not None:
            parameters.append(node.args.vararg)
        if node.args.kwarg is not None:
            parameters.append(node.args.kwarg)
        debt["missing-parameter-annotation"] += sum(
            parameter.annotation is None and parameter.arg not in {"self", "cls"}
            for parameter in parameters
        )
        debt["missing-return-annotation"] += node.returns is None
    return debt


def _merge_base(base_ref: str) -> str:
    """Resolve the common ancestor used by the changed-file annotation ratchet."""
    return _git_output(("merge-base", base_ref, "HEAD")).strip()


def annotation_ratchet_violations(base_ref: str, root: Path = ROOT) -> tuple[str, ...]:
    """Prevent per-file annotation debt from increasing relative to the merge base."""
    base_sha = _merge_base(base_ref)
    changed = _git_output(
        ("diff", "--name-only", "--diff-filter=ACMR", f"{base_sha}...HEAD", "--", "*.py", "*.pyi"),
        root,
    )
    violations: list[str] = []
    for relative in sorted(item for item in changed.splitlines() if item.endswith((".py", ".pyi"))):
        path = root / relative
        if not path.is_file():
            continue
        previous = subprocess.run(
            ("git", "show", f"{base_sha}:{relative}"),
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        before = Counter() if previous.returncode else annotation_debt(previous.stdout, relative)
        current = annotation_debt(path.read_text(encoding="utf-8"), relative)
        for rule in sorted(set(before) | set(current)):
            if current[rule] > before[rule]:
                violations.append(
                    f"{relative}: {rule} increased from {before[rule]} to {current[rule]} "
                    f"relative to {base_sha[:12]}"
                )
    return tuple(violations)


def _public_documentation_violations(path: Path, root: Path) -> list[str]:
    """Require module and public API documentation on the reviewed production surface."""
    relative = path.relative_to(root).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    violations: list[str] = []
    module_doc = ast.get_docstring(tree)
    if module_doc is None or len(module_doc.split()) < 5:
        violations.append(f"{relative}: module docstring must explain the module's responsibility")

    def check_public_node(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        if node.name.startswith("_"):
            return
        docstring = ast.get_docstring(node)
        if docstring is None or len(docstring.split()) < 5:
            violations.append(
                f"{relative}:{node.lineno}: public {node.__class__.__name__.lower()} "
                f"{node.name} needs substantive documentation"
            )

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            check_public_node(node)
            if isinstance(node, ast.ClassDef):
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        check_public_node(member)
    return violations


def architecture_violations(root: Path = ROOT) -> tuple[str, ...]:
    """Protect the deterministic model/placement layer from GUI and repository cycles."""
    violations: list[str] = []
    for relative, forbidden in PURE_MODULE_IMPORTS.items():
        path = root / relative
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative.as_posix())
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = "." * node.level + module
            elif isinstance(node, ast.Import):
                module = ",".join(alias.name for alias in node.names)
            if any(module == item or module.startswith(item + ".") for item in forbidden):
                violations.append(
                    f"{relative.as_posix()}:{node.lineno}: forbidden inward dependency on {module}"
                )
    return tuple(violations)


def main() -> int:
    """Run the repository-wide Python policy and optional changed-file annotation ratchet."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref",
        default="",
        help="Git ref used for the changed-file annotation ratchet (for example origin/main).",
    )
    args = parser.parse_args()

    files = tracked_python_files()
    violations: list[str] = []
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        try:
            violations.extend(
                python_policy_violations(path.read_text(encoding="utf-8"), relative)
            )
        except (OSError, UnicodeError) as error:
            violations.append(f"{relative}: unable to read Python source: {error}")

    for relative in REVIEWED_MODULES:
        path = ROOT / relative
        if not path.is_file():
            violations.append(f"{relative.as_posix()}: reviewed production module is missing")
        else:
            violations.extend(_public_documentation_violations(path, ROOT))
    violations.extend(architecture_violations())

    if args.base_ref:
        try:
            violations.extend(annotation_ratchet_violations(args.base_ref))
        except (OSError, subprocess.SubprocessError) as error:
            violations.append(f"annotation ratchet could not inspect {args.base_ref!r}: {error}")

    if violations:
        print("python-quality-contract=failed", file=sys.stderr)
        print("\n".join(sorted(violations)), file=sys.stderr)
        return 1
    print(
        "python-quality-contract=passed "
        f"tracked_python_files={len(files)} reviewed_modules={len(REVIEWED_MODULES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
