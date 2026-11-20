# Copyright (c) 2026 Uhrendoktor
from typing import TypedDict


class AccessibleNode(TypedDict, total=False):
    name: str | None
    role: str | None
    value: str | None
    children: list["AccessibleNode"]


class XA11yError(Exception): ...


class _AccessibilityApp:
    def tree(self, *, max_depth: int = ...) -> AccessibleNode: ...
    def dump(self, *, max_depth: int = ...) -> str: ...


class App:
    @staticmethod
    def by_pid(process_id: int, *, timeout: float = ...) -> _AccessibilityApp: ...
