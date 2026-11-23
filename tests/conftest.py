"""Pytest collection guard for the repository's test modules."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.test_inventory import STANDALONE_TESTS


def pytest_collection_finish(session: pytest.Session) -> None:
    """Fail full-suite collection when a test module silently contributes no tests."""
    if session.exitstatus != pytest.ExitCode.OK:
        return

    root = Path(str(session.config.rootpath)).resolve()
    test_root = root / "tests"
    discovered_modules = tuple(sorted(test_root.glob("test_*.py")))

    requested: list[Path] = []
    for raw_argument in session.config.args:
        argument = str(raw_argument).split("::", maxsplit=1)[0]
        candidate = Path(argument)
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()
        if candidate.exists():
            requested.append(candidate)

    if requested:
        scope = tuple(
            module
            for module in discovered_modules
            if any(
                candidate in (root, test_root, module) or candidate in module.parents
                for candidate in requested
            )
        )
    else:
        scope = discovered_modules

    collected_paths = {
        Path(str(item.path)).resolve()
        for item in session.items
        if getattr(item, "path", None) is not None
    }
    registered_standalone = {(root / relative).resolve() for relative in STANDALONE_TESTS}
    empty_modules = [
        module
        for module in scope
        if module.resolve() not in collected_paths and module.resolve() not in registered_standalone
    ]
    if not empty_modules:
        return

    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    message = (
        "Test modules collected zero pytest cases and are not registered standalone "
        "acceptance runners:\n"
        + "\n".join(f"  - {path.relative_to(root).as_posix()}" for path in empty_modules)
        + "\nAdd pytest cases or register a real main()-based acceptance runner in "
        "scripts/test_inventory.py."
    )
    if reporter is not None:
        reporter.write_sep("=", "pytest collection contract failed", red=True)
        reporter.write_line(message, red=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
