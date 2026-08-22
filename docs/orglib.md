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

**Implemented so far (2026-08-21):** `orglib/` exists with a single
bespoke backend and a read-only surface — `parse(text) -> Document`,
`Document.tasks()`, `Document.directories(project)`, and `Document.render()`.
The directory lookup returns immutable source spans and distinguishes missing
project, missing section, and empty section. `manager.summarize_projects()` and
`projmgr._project_tasks()` are routed through the task side; registry directory
consumers use the source-backed project lookup. The other `parse_org()` call
sites have not moved by design. Generic mutation, backend selection, and the
fidelity declaration below are still design, not code.

`orglib` is a **peer of `ortasklib`, not a member of it** — a top-level package
imported as `import orglib`. Side by side, the boundary between them is a public
interface rather than an internal detail. Moving it while the package was 60
lines with three importers cost one directory rename plus four import lines.

As of 2026-08-21 the dependency runs one way only: `ortasklib` imports `orglib`,
and `orglib` imports nothing outside the standard library. The Org syntax —
heading regexes, `TodoItem`, `find_tasks_range()`, `parse_org()`,
`parse_directories()`, and the newer `parse_project_directories()` — live in
`orglib/syntax.py`. `core.py` re-exports the names that predate the package
split, so existing `parse_org()` and top-level directory callers remain
untouched.

A subprocess test (`test_orglib_imports_without_ortasklib`) imports `orglib`
with `ortasklib` blocked at the meta-path and asserts `ortasklib` never enters
`sys.modules`. Without it, an `from ortasklib import ...` added to `orglib`
later would restore the old direction silently.

Two tests hold the current contract. One asserts the bespoke backend and `core`
report the same tasks; the other asserts `parse(text).render() == text` byte for
byte. That equality remains the baseline for an untouched document, but it is
not enough to qualify an editing backend. Region edits also need assertions
about their intended change and every byte outside it.

The fuller shape below is the target, not the current state:

```python
# orglib/__init__.py
from typing import Protocol

def parse(text: str) -> "Document": ...

class Document(Protocol):
    def tasks(self) -> list[TodoItem]: ...
    def region(self, selector: RegionSelector) -> "Region": ...
    def apply(self, edit: "RegionEdit") -> "Document": ...
    def render(self) -> str: ...

class Region(Protocol):
    span: TextRange
    text: str
    base_level: int
    context: OrgContext
    source_revision: str
```

Key aspects of the interface:
- **Decoupled Data Structures:** Returns `orglib` values rather than
  library-specific node classes.
- **In-Memory Text Transformation:** Accepts text and returns text; file
  discovery, symlink resolution, atomic I/O, and transactions remain outside.
- **Capability-Based Backends:** A backend may read whole documents, parse
  regions, produce exact patches, or replace bounded regions. Unsupported
  operations fail explicitly rather than falling back silently.

### Regions and Partial-Document Editing

A top-level subtree such as `* Tasks` is often valid input to a full Org parser,
but it is not necessarily a self-contained document. Its interpretation can
depend on file-level `#+TODO`, `#+FILETAGS`, `#+PROPERTY`, `#+LINK`, and
`#+SETUPFILE` declarations, as well as tags and properties inherited from
ancestor headings. Nested subtrees also carry an original heading depth. The
[Org manual](https://orgmode.org/manual/In_002dbuffer-Settings.html) documents
the file-wide settings, and its
[tag inheritance](https://orgmode.org/manual/Tag-Inheritance.html) rules show
why extracted text alone is insufficient.

`orglib` should therefore broker **regions**, not hand a backend an unqualified
substring. A `Region` identifies an exact span in the original source and
carries a context envelope: relevant file keywords, ancestor context, base
heading level, and a revision or preimage check. For a backend that expects a
standalone document, `orglib` may synthesize a prologue and rebase headings,
then map the backend's result back to the original span. Synthetic context is
never written into the source accidentally.

Backends may return one of two edit forms:

- **Patch:** one or more replacements relative to the region. Text outside the
  patches, including untouched text inside the region, remains exact.
- **Bounded replacement:** a serialized replacement for the entire region.
  Text outside the region remains exact, while normalization inside it is an
  explicit, reviewable consequence.

The broker validates the source revision, rejects edits outside the selected
span, and applies the result to the original text. This makes a normalizing
library such as orgmunge potentially useful without granting it permission to
rewrite the entire file. It does not make normalization harmless: replacing
`* Tasks` could still reformat every task, so exact patches should remain the
default and bounded replacement should require an explicit fidelity policy.

In Org terminology, external libraries do not all use “section” consistently.
`Region` is the umbrella term here; selectors can identify a top-level named
subtree, one heading subtree, a task body, or eventually a whole document.

The registry-index read side in `t0026.1` is the first incremental use of this
model. `orglib.parse(text).directories(project)` locates one top-level project
subtree and its unique direct-child `Directories` region, retaining source
offsets and distinguishing missing from empty. `pmgr migrate` uses those
boundaries to validate the new index; `pmgr set-dirs` later replaces only that
bounded region. This moves the directory-parsing slice of `t0020` without
making `t0026` wait for the complete generic region API in `t0028`.

### Testing & Validation with `orgparse`

`orgparse` is an ideal optional dependency for test suite validation:
- **Independent Conformance:** Validates that files generated or modified by `ortask` parse cleanly in standard Org tooling.
- **Clean Skip Handling:** Uses `pytest.importorskip("orgparse")` so that the base test suite continues to run with zero dependencies installed.
- **Keyword Configuration:** Treats `TODO` as active and `DONE`, `MOOT`, and `SUPERSEDED` as terminal, either from `#+TODO: TODO | DONE MOOT SUPERSEDED` or via `env.add_todo_keys(...)`.

## Assessments by LLMs

> **Guideline for contributing models:** Keep assessments concise and technical
> (target 1,000–2,000 characters). Describe the preferred long-term architecture
> and a practical path toward it. Long-term speculation is welcome; label its
> assumptions, ground it in the current code and measured library behavior, and
> identify the evidence needed before committing to it.

### ChatGPT
Long-term, `orglib` should be the stable boundary between ortask's task model and
Org implementation details. CLI and TUI code should use ortask-owned values and
operations; backends should locate Org structures and produce text edits. File
discovery, atomic writes, and transactions should remain outside the backend.
This permits three outcomes: a bespoke engine, an external parser with the
surgical writer, or an external read/write engine that proves equally safe.

The interface should be capability-based rather than assuming every library is
a complete `Document` replacement. A reader can expose tasks and source ranges;
an editor can additionally implement mutations, which must fail explicitly when
unsupported. This shape could accommodate `orgparse` as an oracle, Tree-sitter
as a source locator, and `orgmunge` as a full backend without leaking library
node types into the application.

`orgmunge` is the most interesting long-term replacement, but version 0.3.1 is
not yet a safe production writer: local tests found normalization across the
repository corpus and a parse failure on inactive timestamps in
`docs/llm-log.org`. These are reasons to measure and engage upstream, not to
close the option. Adoption should require semantic agreement for every
supported operation and byte preservation outside declared edit ranges; an
untouched round trip alone is not enough.

The route forward is to establish a real-file conformance corpus, add optional
`orgparse` and orgmunge differential tests, and extract the current parsing and
patch planning behind `orglib` as feature work reaches it. Orgmunge and direct
Tree-sitter adapters can then run under the same tests. Runtime selection should
come only after a backend covers the mutation matrix and fails closed on
unsupported syntax. Until then, the bespoke writer remains the default while
the architecture makes replacing it a measured decision rather than a rewrite.

### Claude

Measured with orgmunge 0.3.1 and orgparse 0.4.20251020 against this repository
and the live registry.

**Where the corpus stands.** Six distinct files are ones ortask reads or writes
(task files plus the registry's private directory files). Two round-trip through
orgmunge byte-identically — both `directories-private.org` — three are
normalized, and one could not be read at all because `elweek/TODO-ElWeek.org` is
still a dangling link, which is a pre-existing registry problem rather than a
library one. Of the normalized three, `todo.org` differs by a single blank line,
`jobhunt2026/todo.org` by three hunks, and `mwsync/tasks.org` by 77. Nothing
under `docs/` round-trips unchanged, and both copies of `llm-log.org` fail to
parse: our log headings (`** Claude [2026-04-08 Wed 21:36]: …`) place an
inactive timestamp where orgmunge's grammar does not expect one. That file is
documentation ortask never edits, so it does not block anything, but it is a
small reproducible case that would make a reasonable upstream report if we want
to open a conversation with those maintainers.

The normalizations are consistent and enumerable: `#+KEYWORD:` lines are
lowercased, tag padding is recomputed, and blank lines before top-level headings
are dropped. This is canonical-form serialization working as designed, not
breakage.

**A correction to my own earlier draft.** I proposed a per-file preflight —
parse, re-render, compare to the original — as the mechanism that would make
orgmunge writes safe, and presented it as a way to phase writes in. As a safety
guard it holds up. As a progress metric it does not: on today's corpus it passes
two machine-generated files and no task file. It gates writes off; it does not
phase them in.

**Where I would move faster than ChatGPT, and where I would not.** I agree that
bespoke stays the sole production writer, and that the conformance corpus is the
first deliverable rather than a backend selector. The asymmetry I would lean on
is that reading is reversible and writing is not. A wrong read costs one bad
listing; a wrong write costs a file. So I would adopt orgmunge now as a
test-only differential *reader* — assert it and `core.parse_org` report the same
tasks across the real corpus — without waiting for `orglib` to exist or for the
mutation matrix to be covered. That is a contained experiment that starts
producing evidence immediately, and it is reversible by deleting a test file.

I would also note that the two files that already round-trip exactly are small,
tool-owned, and contain no user prose. If orgmunge is ever to write anything,
the registry index (`docs/config.md`) is a better first candidate than a task
file, for the same reasons.

**The constraint neither other assessment weighs: maintainer interest.** The
sequencing risk here is that the largest and least visible task — routing every
caller through a new boundary — lands first and delivers nothing the user can
see. For a tool maintained for its own usefulness, that ordering is the main way
this stalls. Cheap and useful first, structural work when it pays for itself:

1. **Add `#+TODO: TODO | DONE MOOT SUPERSEDED` to task files.** One line. Verified to make
   orgmunge, orgparse, and Emacs all read `MOOT` as a terminal state instead of
   as heading text. This is a real fix for anyone opening the file in Emacs, and
   it happens to unblock every library question at once.
2. **The orgparse differential test.** No refactor, no runtime change.
3. **orgmunge as a test-only reader**, as above.
4. **Let `orglib` emerge** from steps 2–3 rather than being specified ahead of
   them. ChatGPT is right that the sketched `Document` protocol is already
   short of ortask's real operations; the way to find the true surface is to
   have two implementations and see what they need in common.
5. **Runtime selection last**, if the evidence supports it.

Steps 1–3 are each an evening's work and none of them require the bespoke code
to change.

### Gemini

**Core stance: Zero-dependency runtime, surgical line-level editing as the sole production writer.**
The primary strength of `ortask` is that it treats user `.org` files with strict fidelity. AST-based serializers (like `orgmunge`) introduce canonical normalizations (tag spacing, keyword case, top-level blank lines) that may be standard for general Org toolsets, but violate `ortask`'s non-destructive contract. The bespoke line-patching engine must remain the default and sole writer.

**Where I align with ChatGPT and Claude:**
1. **Defer a speculative `orglib` protocol:** Building an extensive `Document` abstraction ahead of concrete needs risks over-engineering. An internal shim should only be extracted once two implementations (bespoke and a test-only reader) demand a shared boundary.
2. **Immediate low-hanging wins:** Adding `#+TODO: TODO | DONE MOOT SUPERSEDED` to task files is high-leverage and eliminates keyword discrepancies across Emacs, `orgparse`, and `orgmunge` without changing code.
3. **Differential testing before runtime wiring:** Using `orgparse` (via `pytest.importorskip`) gives us an independent, read-only conformance check against our generated outputs with zero runtime dependencies. Testing `orgmunge` as a read-only harness across repo fixtures safely identifies edge cases (such as the inactive timestamps in `llm-log.org`) without endangering user files.

**Pragmatic roadmap:**
Prioritize visible CLI functionality and simple fixture-based testing over parser refactoring. If external parsers are explored, confine them strictly to optional validation fixtures until an external option demonstrably matches `ortask`'s byte-exact preservation guarantees.
