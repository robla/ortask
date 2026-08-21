# Org-Mode Libraries & Bespoke Editing in `ortasklib`

This document details the design and architecture of `ortasklib`'s bespoke Org-mode editing model, why full AST round-tripping is intentionally avoided, and an evaluation of existing external Python Org-mode libraries.

## Bespoke Org Editing in `ortasklib`

`ortask` manages tasks and project configuration directly in `.org` files without an external database. The editing philosophy centers on **file fidelity and surgical modification**.

### Key Principles

1. **Surgical Line-Level Patching:**
   Rather than parsing an entire document into an Abstract Syntax Tree (AST) and reserializing it, `ortasklib` acts as a *section addresser*. It locates specific heading boundaries (e.g., `* Tasks` or `* Directories`), parses heading metadata (IDs, TODO/DONE states, priorities, tags), and performs targeted line-level modifications.
2. **Preservation of Surrounding Context:**
   Prose outside addressed sections, task bodies, property drawers, logbooks, blank lines, table structures, and arbitrary user markup remain byte-for-byte untouched.
3. **Bounded Opaque Content:**
   `ortasklib` understands Org headings and structural hierarchies, treating task body content and non-task subtrees as opaque text. It deliberately does not interpret tables, babel blocks, inline formatting, or LaTeX fragments.
4. **Atomic Writes:**
   All mutations are written to a temporary file in the same filesystem directory and atomically renamed over the target file, ensuring immunity against corrupted or half-written states upon interruption.

## The Problem with AST Round-Tripping

The suite's central promise is that **your Org file stays yours**.

Most full-featured parse-and-serialize libraries construct an in-memory AST and generate text anew upon serialization. By construction, this normalizes whitespace, alters indentation, standardizes list bullet styles, and reformats property drawers or prose. For a developer or writer with an existing, carefully formatted Org document, destructive reformatting is unacceptable.

A library is only appropriate for mutation if it guarantees exact source byte-range retention or supports non-destructive surgical edits.

## External Python Org Libraries (as of August 2026)

The following libraries were surveyed to assess whether adopting an external dependency would provide value over our bespoke `ortasklib` approach:

| Library | Latest | Dependencies | Python | License | Notes |
|---|---|---|---|---|---|
| [orgmunge](https://pypi.org/project/orgmunge/) | 0.3.1 (Jul 2025) | `ply` | ≥3.10 | MIT | A real grammar; exists specifically to modify and write back. Re-serializes. |
| [orgparse](https://pypi.org/project/orgparse/) | 0.4.20251020 (Oct 2025) | **none** | ≥3.9 | BSD-2 | Read-only tree. The de facto standard; stable rather than abandoned. |
| [panflute](https://pypi.org/project/panflute/) | 2.3.1 (Aug 2026) | `pandoc` (system binary) | ≥3.6 | BSD-3 | A pythonic wrapper for Pandoc filters; parses/modifies the Pandoc AST. |
| [orgformat](https://pypi.org/project/orgformat/) | 2026.6.6.1 (Jun 2026) | none | **≥3.13** | **GPL-3** | Timestamp/link formatting helpers, not a parser. Created by Karl Voit. |
| [org-rw](https://pypi.org/project/org-rw/) | 0.0.2 (Jul 2024) | none | — | Apache-2 | Conceptually closest to `ortask` needs (retains source locations, round-trip checks), but immature (drops Org block delimiters). Under investigation. |
| [org-parser](https://github.com/Idorobots/org-parser) | 0.28.0 (May 2026) | `tree-sitter`, `tree-sitter-org` | **≥3.12** | MIT | Rejected: requires Python ≥3.12 (incompatible with target 3.11 environment) and has very low adoption (~2 stars). |
| [PyOrgMode](https://github.com/bjonnh/PyOrgMode) | 0.1 (2014) | none | — | unclear | Rejected: completely unmaintained since 2014. |

### Technical & Environment Constraints

- **Python Version Compatibility:** Standard environments running Python 3.11 cannot install the latest releases of `orgformat` (`≥3.13`) or `org-parser` (`≥3.12`). Attempting `pip install orgformat` on Python 3.11 falls back to an obsolete release from 2019.
- **Licensing:** `orgformat` is GPL-3 licensed, presenting constraints for permissive integration.
- **Canonical Parsing:** The only 100% authoritative Org parser is `org-element` within GNU Emacs (accessible via `emacs --batch`), but requiring an Emacs runtime contradicts our standalone CLI goals.

## Recommendations

1. **Maintain Zero Runtime Dependencies:**
   Retain `ortasklib`'s lightweight, stdlib-only section addressing and surgical line manipulation.
2. **Reject Unnecessary Grammar Complexity:**
   Do not build or import a general-purpose Org grammar for runtime operations. The rule of thumb: *if a proposed feature requires parsing beyond heading boundaries and properties, it is likely out of scope for ortask.*
3. **Leverage `orgparse` for Validation in Tests:**
   Use `orgparse` as an optional test dependency to independently verify that documents modified or generated by `ortask` remain valid, standard Org trees.
