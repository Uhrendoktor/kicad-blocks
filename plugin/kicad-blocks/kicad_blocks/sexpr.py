"""Typed parser and tree operations for KiCad's recursive S-expression format.

The tree model distinguishes bare atoms from quoted strings. Structural
helpers expose only list nodes and keep the parser's recursive shape explicit,
so schematic and PCB transformations can be checked without untyped lists.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

type SExpr = str | list[SExpr]
type SExprList = list[SExpr]


class QuotedSExpr(str):
    """One quoted S-expression atom, distinct from a bare token."""


def _lex(source: str) -> Iterator[str | QuotedSExpr]:
    """Yield bare tokens and quoted atoms while preserving escaped content."""
    index = 0
    while index < len(source):
        character = source[index]
        if character.isspace():
            index += 1
            continue
        if character == ";":
            end = source.find("\n", index)
            index = len(source) if end < 0 else end + 1
            continue
        if character in "()":
            yield character
            index += 1
            continue
        if character == '"':
            index += 1
            escaped = False
            output: list[str] = []
            while index < len(source):
                current = source[index]
                if escaped:
                    if current == "x":
                        digits = source[index + 1 : index + 3]
                        if len(digits) != 2 or any(
                            digit not in "0123456789abcdefABCDEF" for digit in digits
                        ):
                            raise ValueError("Invalid hexadecimal escape in KiCad S-expression.")
                        output.append(f"\\u00{digits}")
                        index += 2
                    elif current in "01234567":
                        digits = source[index : index + 3]
                        if len(digits) != 3 or any(digit not in "01234567" for digit in digits):
                            raise ValueError("Invalid octal escape in KiCad S-expression.")
                        output.append(f"\\u{int(digits, 8):04x}")
                        index += 2
                    else:
                        output.append("\\" + current)
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    break
                else:
                    output.append(current)
                index += 1
            if index >= len(source):
                raise ValueError("Unterminated quoted string in KiCad S-expression.")
            try:
                decoded: object = json.loads('"' + "".join(output) + '"')
            except json.JSONDecodeError as error:
                raise ValueError(f"Invalid quoted string in KiCad S-expression: {error}") from error
            if not isinstance(decoded, str):
                raise ValueError("A quoted KiCad S-expression atom must decode to text.")
            yield QuotedSExpr(decoded)
            index += 1
            continue

        start = index
        while index < len(source) and not source[index].isspace() and source[index] not in "()":
            index += 1
        yield source[start:index]


def parse_sexpr(source: str) -> SExprList:
    """Parse one balanced S-expression document into a recursive list tree."""
    root: SExprList = []
    stack: list[SExprList] = [root]
    for token in _lex(source):
        if token == "(" and not isinstance(token, QuotedSExpr):
            node: SExprList = []
            stack[-1].append(node)
            stack.append(node)
        elif token == ")" and not isinstance(token, QuotedSExpr):
            if len(stack) == 1:
                raise ValueError("Unexpected ')' in KiCad S-expression.")
            stack.pop()
        else:
            stack[-1].append(token)
    if len(stack) != 1 or len(root) != 1 or not isinstance(root[0], list):
        raise ValueError("Malformed KiCad S-expression document.")
    return root[0]


def dump_sexpr(value: SExpr) -> str:
    """Serialize a recursive S-expression tree without changing quoted atoms."""
    if isinstance(value, list):
        return "(" + " ".join(dump_sexpr(item) for item in value) + ")"
    if isinstance(value, QuotedSExpr):
        return sexpr_quote(str(value))
    return value


def sexpr_head(node: SExpr | None) -> str | None:
    """Return a list node's leading atom, or None for a scalar/empty node."""
    if isinstance(node, list) and node and isinstance(node[0], str):
        return str(node[0])
    return None


def sexpr_direct(node: SExprList | None, tag: str) -> SExprList | None:
    """Return the first direct child list with the requested leading atom."""
    if node is None:
        return None
    return next(
        (child for child in node[1:] if isinstance(child, list) and sexpr_head(child) == tag),
        None,
    )


def sexpr_all(node: SExprList | None, tag: str) -> list[SExprList]:
    """Return all direct child lists with the requested leading atom."""
    if node is None:
        return []
    return [
        child
        for child in node[1:]
        if isinstance(child, list) and sexpr_head(child) == tag
    ]
