# Org-Mode Libraries & Bespoke Editing in `ortasklib`

This document details the design and architecture of `ortasklib`'s bespoke Org-mode editing model, technical trade-offs regarding AST round-tripping, a survey of existing external Python Org-mode libraries, and the proposed `orglib` interface design.

## Bespoke Org Editing in `ortasklib`

`ortask` manages tasks and project configuration directly in `.org` files without an external database. The editing approach is designed around **file fidelity and surgical modification**.

### Design Principles

1. **Surgical Line-Level Patching:**
   Rather than loading a full document into an Abstract Syntax Tree (AST) and reserializing the entire file, `ortasklib` operates as a targeted section addresser. It locates specific heading boundaries (such as `* Tasks` or `* Directories`), parses heading metadata (IDs, TODO/DONE states, priorities, tags), and performs surgical line-level edits.
2. **Preservation of Surrounding Context:**
   Prose outside addressed sections, task bodies, property drawers, logbooks, blank lines, table structures, and arbitrary user formatting are preserved exactly as written.
3. **Bounded Opaque Content:**
   `ortasklib` parses Org headings and structural hierarchies while treating task body content and non-task subtrees as opaque text. It deliberately does not interpret tables, babel blocks, inline formatting, or LaTeX fragments.
4. **Atomic Writes:**
   Mutations are written to a temporary file in the same directory and atomically renamed over the target file, preventing corrupted or half-written states upon interruption.

## Trade-offs of AST Round-Tripping

In Org-mode tooling, there is a fundamental architectural trade-off between full AST document modeling and surgical line-level patching:

- **Full AST Parsers & Serializers:**
  Constructing a comprehensive document object model allows rich programmatic manipulation of all Org elements (tables, blocks, lists, markup). However, regenerating a file from an AST often normalizes whitespace, blank lines, tag alignment, or indentation. For users who maintain custom file layouts, this can introduce unintended diffs.
- **Surgical Line Patching:**
  Focusing strictly on heading lines and targeted blocks guarantees byte-level preservation of all untouched lines. The trade-off is that the tool does not provide a general-purpose parser for arbitrary Org syntax elements.

## External Python Org Libraries (as of August 2026)

The following libraries were surveyed to evaluate different parsing and serialization approaches:

| Library | Latest | Dependencies | Python | License | Notes |
|---|---|---|---|---|---|
| [orgmunge](https://pypi.org/project/orgmunge/) | 0.3.1 (Jul 2025) | `ply` | ≥3.10 | MIT | Grammar-based parser designed for tree modification and write-back. Re-serializes the document. |
| [orgparse](https://pypi.org/project/orgparse/) | 0.4.20251020 (Oct 2025) | **none** | ≥3.9 | BSD-2 | Read-only tree representation. Widely used, zero-dependency, stable reference parser. |
| [panflute](https://pypi.org/project/panflute/) | 2.3.1 (Aug 2026) | `pandoc` (system binary) | ≥3.6 | BSD-3 | Pythonic wrapper for Pandoc filters; parses and modifies the Pandoc AST. |
| [orgformat](https://pypi.org/project/orgformat/) | 2026.6.6.1 (Jun 2026) | none | **≥3.13** | **GPL-3** | Specialized formatting helpers for timestamps and links by Karl Voit (not a full document parser). |
| [org-rw](https://pypi.org/project/org-rw/) | 0.0.2 (Jul 2024) | none | — | Apache-2 | Retains source locations and includes round-trip checks; early-stage / under investigation. |
| [org-parser](https://github.com/Idorobots/org-parser) | 0.28.0 (May 2026) | `tree-sitter`, `tree-sitter-org` | **≥3.12** | MIT | Tree-sitter binding for Org-mode. Requires Python ≥3.12 and compiled dependencies. |
| [PyOrgMode](https://github.com/bjonnh/PyOrgMode) | 0.1 (2014) | none | — | unclear | Historical parser; unmaintained since 2014. |

### Technical & Environment Notes

- **Python Version Compatibility:** Standard environments running Python 3.11 cannot install the latest releases of `orgformat` (`≥3.13`) or `org-parser` (`≥3.12`).
- **Licensing Considerations:** `orgformat` is licensed under GPL-3, which requires consideration depending on project distribution plans.
- **Canonical Parsing:** The reference implementation of Org-mode syntax is GNU Emacs' built-in `org-element` (accessible in batch mode via `emacs --batch`).

## Architecture & Integration Strategy

The default engine remains bespoke and stdlib-only to preserve zero-dependency distribution and exact line-level fidelity. To support experimentation and testing with external libraries, an abstract interface layer (`orglib`) is planned.

### The `orglib` Interface

The `orglib` abstraction provides a common protocol between `ortask` commands and underlying Org implementations:

```python
# ortasklib/orglib/__init__.py
from typing import Protocol
from ortasklib.core import TodoItem

def parse(text: str) -> "Document": ...

class Document(Protocol):
    def tasks(self) -> list[TodoItem]: ...
    def directories(self, project: str | None = None) -> list[str] | None: ...
    def set_state(self, task_id: str, state: str) -> None: ...
    def add_task(self, parent: str | None, state: str, text: str) -> str: ...
    def render(self) -> str: ...
```

Key aspects of the interface:
- **Decoupled Data Structures:** Returns native `TodoItem` objects rather than library-specific node classes.
- **In-Memory Text Transformation:** Accepts text and returns rendered text, allowing caller modules to manage atomic file I/O uniformly.
- **Backend Selection:** Supports runtime selection (e.g. `ORTASK_ORGLIB=orgmunge`), defaulting to the bespoke implementation when unspecified.

### Testing & Validation with `orgparse`

`orgparse` is an ideal optional dependency for test suite validation:
- **Independent Conformance:** Validates that files generated or modified by `ortask` parse cleanly in standard Org tooling.
- **Clean Skip Handling:** Uses `pytest.importorskip("orgparse")` so that the base test suite continues to run with zero dependencies installed.
- **Keyword Configuration:** Configures custom keywords (`MOOT`, `TODO`, `DONE`) via `env.add_todo_keys(...)` to ensure matching state interpretation.

## Assessments by LLMs

### ChatGPT
*(Reserved for ChatGPT's assessment and design notes.)*

### Claude
*(Reserved for Claude's assessment and design notes.)*

### Gemini
From an architectural standpoint, maintaining `ortasklib`'s bespoke, stdlib-only section addressing is the right default for the core CLI. It preserves exact file fidelity without risk of unintended reformatting, avoids Python version friction (such as `≥3.13` constraints on Python 3.11 systems), and eliminates compiled binary dependencies.

Introducing the `orglib` protocol provides clean architectural decoupling: it allows `ortask` to keep its core commands independent of parsing details, enables controlled experimentation with libraries like `orgmunge` behind a fidelity check, and makes `orgparse` a valuable optional conformance oracle in the test suite without complicating runtime requirements.
