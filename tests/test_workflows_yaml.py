"""A small standard-library reader for the YAML subset used by .github/workflows, and its own tests.

tests/test_workflows.py parses the workflows with :func:`parse_yaml`. Supported: block mappings and sequences
(``- key: value`` items included), comments, plain/single-quoted/double-quoted one-line scalars, ``|``/``|-``/``>``/``>-``
block scalars and one-line flow sequences (``[a, b]``) and ``{}``/``[]``. Every scalar stays a string (``"true"``,
``"30"``); ``on`` stays the key ``"on"``. Anything else (anchors, tags, multi-line flow or plain scalars, tabs,
duplicate keys, odd indentation) raises :class:`YamlSubsetError`, so an unsupported construct fails a test instead of
being misread.
"""

from __future__ import annotations

import json
import re
import unittest
from typing import Any

KEY = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-/]*):(?: (.*))?$")
BLOCK_STYLES = ("|", "|-", ">", ">-")
UNSUPPORTED_START = ("&", "*", "!", "%", "@", "`", "{", "?")


class YamlSubsetError(ValueError):
    pass


def _without_comment(text: str) -> str:
    cut = text.find(" #")
    return (text[:cut] if cut >= 0 else text).strip()


def parse_scalar(text: str, line: int = 0) -> Any:
    """One-line scalar (or empty/one-line flow collection) after a key or ``- ``; comments removed."""
    text = text.strip()
    if text.startswith("'"):
        match = re.match(r"'((?:[^']|'')*)'(.*)$", text)
        if not match or _without_comment(match.group(2)):
            raise YamlSubsetError(f"line {line}: unterminated or trailing single-quoted scalar")
        return match.group(1).replace("''", "'")
    if text.startswith('"'):
        try:
            value, end = json.JSONDecoder().raw_decode(text)
        except ValueError:
            raise YamlSubsetError(f"line {line}: unsupported double-quoted scalar") from None
        if _without_comment(text[end:]) or not isinstance(value, str):
            raise YamlSubsetError(f"line {line}: trailing text after a double-quoted scalar")
        return value
    value = _without_comment(text)
    if value in ("{}", "[]"):
        return {} if value == "{}" else []
    if value.startswith("[") and value.endswith("]"):
        return [parse_scalar(part, line) for part in value[1:-1].split(",")]
    if not value or value.startswith(UNSUPPORTED_START + ("[", "|", ">", "'", '"')):
        raise YamlSubsetError(f"line {line}: unsupported scalar {text!r}")
    return value


class _Parser:
    def __init__(self, text: str):
        if "\t" in text:
            raise YamlSubsetError("tabs are not supported")
        self.lines = text.split("\n")

    def _content(self, index: int) -> bool:
        stripped = self.lines[index].strip()
        return bool(stripped) and not stripped.startswith("#")

    def next_content(self, index: int) -> int:
        while index < len(self.lines) and not self._content(index):
            index += 1
        return index

    def indent(self, index: int) -> int:
        line = self.lines[index]
        return len(line) - len(line.lstrip(" "))

    def parse(self) -> Any:
        start = self.next_content(0)
        if start >= len(self.lines):
            return None
        value, end = self.block(start, self.indent(start))
        if self.next_content(end) < len(self.lines):
            raise YamlSubsetError(f"line {self.next_content(end) + 1}: unexpected content after the document")
        return value

    def block(self, index: int, indent: int) -> tuple[Any, int]:
        content = self.lines[index][indent:]
        if content == "-" or content.startswith("- "):
            return self.sequence(index, indent)
        return self.mapping(index, indent)

    def mapping(self, index: int, indent: int) -> tuple[dict, int]:
        result: dict[str, Any] = {}
        while True:
            index = self.next_content(index)
            if index >= len(self.lines) or self.indent(index) < indent:
                return result, index
            if self.indent(index) > indent:
                raise YamlSubsetError(f"line {index + 1}: unexpected indentation")
            match = KEY.match(self.lines[index][indent:])
            if not match:
                raise YamlSubsetError(f"line {index + 1}: expected 'key: value', got {self.lines[index].strip()!r}")
            key = match.group(1)
            if key in result:
                raise YamlSubsetError(f"line {index + 1}: duplicate key {key!r}")
            result[key], index = self.value(match.group(2) or "", index, indent)

    def value(self, rest: str, index: int, indent: int) -> tuple[Any, int]:
        head = _without_comment(rest)
        if not head:
            following = self.next_content(index + 1)
            if following < len(self.lines) and self.indent(following) > indent:
                return self.block(following, self.indent(following))
            return None, index + 1
        if head in BLOCK_STYLES:
            return self.block_scalar(head, index + 1, indent)
        return parse_scalar(rest, index + 1), index + 1

    def sequence(self, index: int, indent: int) -> tuple[list, int]:
        items: list[Any] = []
        while True:
            index = self.next_content(index)
            if index >= len(self.lines) or self.indent(index) < indent:
                return items, index
            content = self.lines[index][indent:]
            if self.indent(index) > indent or not (content == "-" or content.startswith("- ")):
                raise YamlSubsetError(f"line {index + 1}: expected a '- ' sequence item")
            item = content[2:]
            if not item.strip():
                following = self.next_content(index + 1)
                if following >= len(self.lines) or self.indent(following) <= indent:
                    raise YamlSubsetError(f"line {index + 1}: empty sequence item")
                value, index = self.block(following, self.indent(following))
            elif item.startswith(" "):
                raise YamlSubsetError(f"line {index + 1}: use exactly one space after '-'")
            elif KEY.match(item):
                self.lines[index] = " " * (indent + 2) + item  # the item is a mapping starting at the item's column
                value, index = self.mapping(index, indent + 2)
            else:
                value, index = parse_scalar(item, index + 1), index + 1
            items.append(value)

    def block_scalar(self, style: str, index: int, parent: int) -> tuple[str, int]:
        lines: list[str] = []
        width = None
        while index < len(self.lines):
            line = self.lines[index]
            if not line.strip():
                lines.append("")
                index += 1
                continue
            current = len(line) - len(line.lstrip(" "))
            if current <= parent:
                break
            width = current if width is None else width
            if current < width:
                raise YamlSubsetError(f"line {index + 1}: block scalar line is less indented than its first line")
            lines.append(line[width:])
            index += 1
        while lines and not lines[-1]:
            lines.pop()
        if style.startswith(">"):
            if any(line.startswith(" ") for line in lines):
                raise YamlSubsetError("more-indented lines in a folded scalar are not supported")
            paragraphs, current_lines = [], []
            for line in lines + [""]:
                if line:
                    current_lines.append(line)
                elif current_lines:
                    paragraphs.append(" ".join(current_lines))
                    current_lines = []
            text = "\n".join(paragraphs)
        else:
            text = "\n".join(lines)
        return (text if style.endswith("-") else text + "\n"), index


def parse_yaml(text: str) -> Any:
    return _Parser(text).parse()


SAMPLE = """\
# comment
name: Sample   # trailing comment
on:
  push:
  workflow_dispatch:
    inputs:
      sha:
        required: true
permissions: {}
jobs:
  build:
    needs: [a, b]
    if: >-
      x == 'y' &&
      z == 'w'
    steps:
      - name: First
        uses: owner/repo@0123 # v1.2.3
        with:
          path: ${{ runner.temp }}/x
      - name: 'It''s quoted'
        run: |
          echo "# not a comment"

          exit 0
      # a comment between items
      - run: "double \\"quoted\\""
      - plain item
"""


class ParserTests(unittest.TestCase):
    def test_sample(self):
        data = parse_yaml(SAMPLE)
        self.assertEqual(list(data), ["name", "on", "permissions", "jobs"])
        self.assertEqual(data["name"], "Sample")
        self.assertEqual(data["on"], {"push": None, "workflow_dispatch": {"inputs": {"sha": {"required": "true"}}}})
        self.assertEqual(data["permissions"], {})
        build = data["jobs"]["build"]
        self.assertEqual(build["needs"], ["a", "b"])
        self.assertEqual(build["if"], "x == 'y' && z == 'w'")
        steps = build["steps"]
        self.assertEqual(steps[0], {"name": "First", "uses": "owner/repo@0123", "with": {"path": "${{ runner.temp }}/x"}})
        self.assertEqual(steps[1], {"name": "It's quoted", "run": 'echo "# not a comment"\n\nexit 0\n'})
        self.assertEqual(steps[2], {"run": 'double "quoted"'})
        self.assertEqual(steps[3], "plain item")
        self.assertEqual(len(steps), 4)

    def test_unsupported_constructs_fail_loudly(self):
        for text in ("a: &anchor 1\n", "a: *alias\n", "a: !tag x\n", "a:\n\tb: 1\n", "a: 1\na: 2\n",
                     "a:\n  b: 1\n   c: 2\n", "a: {b: 1}\n", "a: [b,\n  c]\n", "a: b\n  continued\n",
                     "- a\nb: 1\n", "a: 'open\n", "a: |+\n  x\n", "a:\n  -  b: 1\n", "a: 1\n- b\n"):
            with self.subTest(text=text):
                with self.assertRaises(YamlSubsetError):
                    parse_yaml(text)


if __name__ == "__main__":
    unittest.main()
