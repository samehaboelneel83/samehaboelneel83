"""Tokenizer for the problem language."""

from __future__ import annotations

import re
from dataclasses import dataclass

from psp.dsl.errors import LexError

KEYWORDS = {
    "problem", "set", "param", "var", "constraint", "objective",
    "minimize", "maximize", "assume", "scenario", "structure",
    "forall", "where", "sum", "for", "in", "and", "or", "not",
    "binary", "integer", "continuous", "int", "label",
    "default", "unit", "labels", "means", "because", "category", "soft", "penalty",
    "weight", "scale", "by", "to", "inf", "of", "affects",
}

# Longest first, so '<=' is never read as '<' followed by '='.
SYMBOLS = ["<=", ">=", "==", "!=", "..", "<", ">", "=", "+", "-", "*", "/",
           "(", ")", "[", "]", "{", "}", ",", ":"]

_NUMBER = re.compile(r"\d+(\.\d+)?([eE][+-]?\d+)?")
_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9.]*")


@dataclass
class Token:
    kind: str  # keyword | name | number | string | symbol | newline | end
    value: str
    line: int
    column: int

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.kind}({self.value!r})@{self.line}:{self.column}"


def tokenize(text: str) -> list[Token]:
    """Break source into tokens.

    Newlines are significant — they end a declaration — but a line ending in an
    operator, or one inside brackets, continues, so a long constraint can be
    written across several lines without ceremony.
    """
    tokens: list[Token] = []
    line, column, i = 1, 1, 0
    depth = 0

    def last_meaningful() -> Token | None:
        for token in reversed(tokens):
            if token.kind != "newline":
                return token
        return None

    while i < len(text):
        ch = text[i]

        if ch == "\n":
            previous = last_meaningful()
            continues = depth > 0 or (
                previous is not None
                and previous.kind == "symbol"
                and previous.value in {"+", "-", "*", "/", ",", ":", "=", "<=", ">=", "==", "!="}
            )
            if not continues and tokens and tokens[-1].kind != "newline":
                tokens.append(Token("newline", "\\n", line, column))
            i += 1
            line += 1
            column = 1
            continue

        if ch in " \t\r":
            i += 1
            column += 1
            continue

        if ch == "#":  # comment to end of line
            while i < len(text) and text[i] != "\n":
                i += 1
            continue

        if ch in "\"'":
            quote, j = ch, i + 1
            buffer = []
            while j < len(text) and text[j] != quote:
                if text[j] == "\\" and j + 1 < len(text):
                    buffer.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == "\n":
                    raise LexError("string is not closed before the end of the line",
                                   line, column, text)
                buffer.append(text[j])
                j += 1
            if j >= len(text):
                raise LexError("string is not closed", line, column, text)
            tokens.append(Token("string", "".join(buffer), line, column))
            column += j - i + 1
            i = j + 1
            continue

        number = _NUMBER.match(text, i)
        # '..' is a range, so 0..9 must not lex as the number '0.' followed by '.9'.
        trailing_dot = (
            number and number.group().endswith(".") and text.startswith(".", number.end())
        )
        if number and not trailing_dot:
            literal = number.group()
            if literal.endswith("."):
                literal = literal[:-1]
                number_end = i + len(literal)
            else:
                number_end = number.end()
            tokens.append(Token("number", literal, line, column))
            column += number_end - i
            i = number_end
            continue

        name = _NAME.match(text, i)
        if name:
            word = name.group()
            kind = "keyword" if word in KEYWORDS else "name"
            tokens.append(Token(kind, word, line, column))
            column += len(word)
            i = name.end()
            continue

        for symbol in SYMBOLS:
            if text.startswith(symbol, i):
                if symbol in "([{":
                    depth += 1
                elif symbol in ")]}":
                    depth = max(0, depth - 1)
                tokens.append(Token("symbol", symbol, line, column))
                column += len(symbol)
                i += len(symbol)
                break
        else:
            raise LexError(f"unexpected character {ch!r}", line, column, text)

    if tokens and tokens[-1].kind != "newline":
        tokens.append(Token("newline", "\\n", line, column))
    tokens.append(Token("end", "", line, column))
    return tokens
