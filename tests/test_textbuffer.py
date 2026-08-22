from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import textbuffer


ROOT = Path(__file__).resolve().parents[1]


def test_textbuffer_imports_without_ortask_or_tui_packages() -> None:
    # The reusable buffer must not acquire Org, ortask, or prompt-toolkit imports.
    program = """
import importlib.abc
import sys

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ("orglib", "ortasklib", "prompt_toolkit")
        if fullname in roots or fullname.startswith(tuple(root + "." for root in roots)):
            raise ImportError(f"blocked dependency: {fullname}")
        return None

sys.meta_path.insert(0, Block())
import textbuffer
assert not any(
    name == root or name.startswith(root + ".")
    for name in sys.modules
    for root in ("orglib", "ortasklib", "prompt_toolkit")
)
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_textbuffer_uses_explicit_write_autosave_and_save_hooks(
    tmp_path: Path,
) -> None:
    # The generic state machine delegates application-specific I/O policy.
    source = tmp_path / "notes.txt"
    source.write_text("before\n", encoding="utf-8")
    autosave = tmp_path / ".notes.txt.autosave"
    writes: list[tuple[Path, str]] = []
    saves: list[tuple[str, str, Path]] = []

    def atomic_write(path: Path, text: str) -> None:
        writes.append((path, text))
        path.write_text(text, encoding="utf-8")

    buffer = textbuffer.TextFileBuffer(
        source,
        autosave_path=autosave,
        atomic_write=atomic_write,
        on_save=lambda before, after, path: saves.append((before, after, path)),
    )

    assert buffer.apply_text("after\n", description="Revise notes") is True
    assert source.read_text(encoding="utf-8") == "before\n"
    assert autosave.read_text(encoding="utf-8") == "after\n"
    assert writes == [(autosave, "after\n")]

    buffer.save()

    assert source.read_text(encoding="utf-8") == "after\n"
    assert saves == [("before\n", "after\n", source)]
    assert writes[-1] == (source, "after\n")
    assert buffer.dirty is False
    assert not autosave.exists()
