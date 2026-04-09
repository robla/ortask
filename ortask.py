#!/usr/bin/env python3
"""Inspect org-mode style TODO checklists in this workspace.

The current default behavior is read-only: print TODO items from README.org.
The parsing and rendering helpers are separated so future edit commands can
reuse the same model safely.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_ORG_FILE = Path(__file__).with_name("README.org")
TODO_PATTERN = re.compile(
    r"^(?P<stars>\*+)\s+\[(?P<state>[Xx ]?)\]\s+(?P<text>.+?)\s*$"
)


@dataclass(frozen=True)
class TodoItem:
    level: int
    done: bool
    text: str

    def render(self) -> str:
        checkbox = "[X]" if self.done else "[]"
        indent = "  " * (self.level - 1)
        return f"{indent}{checkbox} {self.text}"


def parse_todo_items(text: str) -> list[TodoItem]:
    items: list[TodoItem] = []
    for line in text.splitlines():
        match = TODO_PATTERN.match(line)
        if not match:
            continue
        items.append(
            TodoItem(
                level=len(match.group("stars")),
                done=match.group("state").upper() == "X",
                text=match.group("text"),
            )
        )
    return items


def limit_items(items: Iterable[TodoItem], max_items: int | None) -> list[TodoItem]:
    if max_items is None:
        return list(items)
    if max_items < 0:
        raise ValueError("--items must be 0 or greater")
    return list(items)[:max_items]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Print org-mode TODO items from README.org."
    )
    parser.add_argument(
        "--items",
        type=int,
        default=None,
        help="limit output to the first N TODO items",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=DEFAULT_ORG_FILE,
        help=f"org file to read (default: {DEFAULT_ORG_FILE.name})",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    text = args.file.read_text(encoding="utf-8")
    items = parse_todo_items(text)
    for item in limit_items(items, args.items):
        print(item.render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
