"""Regression coverage for test assertion-oracle enforcement."""

from __future__ import annotations

import ast

from scripts.check_test_quality import _assertion_oracle, _trivial_assertion


def _function(source: str) -> ast.FunctionDef:
    module = ast.parse(source)
    node = module.body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


def test_recognizes_language_and_unittest_assertion_oracles() -> None:
    """Recognize assert statements and unittest assertion methods."""
    assert _assertion_oracle(_function("def test_case():\n    assert 2 + 2 == 4\n"))
    assert _assertion_oracle(
        _function("def test_case(self):\n    self.assertEqual(2 + 2, 4)\n")
    )


def test_recognizes_mock_and_pytest_exception_oracles() -> None:
    """Recognize mock call assertions and pytest.raises as behavior checks."""
    assert _assertion_oracle(
        _function("def test_case(mock):\n    mock.assert_called_once_with('ready')\n")
    )
    assert _assertion_oracle(
        _function("def test_case():\n    with pytest.raises(ValueError):\n        parse('bad')\n")
    )


def test_rejects_tests_without_any_oracle() -> None:
    """Reject a test body that merely calls production code without checking the result."""
    assert not _assertion_oracle(_function("def test_case():\n    perform_action()\n"))


def test_detects_constant_true_and_self_equality_assertions() -> None:
    """Reject assertions that cannot distinguish correct from incorrect behavior."""
    assert _trivial_assertion(_function("def test_case():\n    assert True\n"))
    assert _trivial_assertion(
        _function("def test_case(self, value):\n    self.assertEqual(value, value)\n")
    )


def test_accepts_assertions_against_independent_expected_values() -> None:
    """Keep comparisons against independently specified expected results."""
    assert _trivial_assertion(
        _function("def test_case(self, actual, expected):\n    self.assertEqual(actual, expected)\n")
    ) is None
