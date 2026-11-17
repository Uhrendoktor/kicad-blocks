"""Typing surface for the optional xa11y test diagnostic package."""

class XA11yError(Exception): ...

class _AccessibilityApp:
    def dump(self, *, max_depth: int = ...) -> str: ...

class App:
    @staticmethod
    def by_pid(process_id: int, *, timeout: float = ...) -> _AccessibilityApp: ...
