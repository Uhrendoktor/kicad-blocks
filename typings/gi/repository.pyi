"""Narrow GLib error contract used by the independent AT-SPI inspector."""

class Error(Exception): ...

class _GLibModule:
    Error: type[Error]

GLib: _GLibModule
