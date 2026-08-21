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

## Direction

The survey above concluded "no dependency, ever." That is refined here rather
than reversed. The **default** stays bespoke and stdlib-only, because the
zero-dependency property is worth keeping and because nothing surveyed
round-trips exactly. What changes is that the bespoke implementation stops being
the *only* possible implementation.

The vehicle is `orglib`: one API, several backends, chosen at runtime. Three
things follow, and the rest of this document covers them in turn.

1. **The `orglib` shim** — an API that is honest about being an API, so that
   dropping the bespoke code later is a backend swap rather than a rewrite.
2. **A slow migration to orgmunge** — read first, write last, gated on measured
   fidelity rather than on hope.
3. **`orgparse` as an optional test dependency** — an independent opinion about
   whether our output is really Org, that never becomes required to run tests.

## The `orglib` shim

The goal, stated as a test: **if the bespoke backend were deleted tomorrow and
`orglib` became a thin wrapper over orgmunge, would any caller outside
`orglib` need to change?** If yes, the API has leaked. Everything below follows
from making the answer "no."

### What the API must not expose

Three leaks would each be fatal, and all three are easy to commit by accident:

- **Backend node objects.** If `orglib` hands back an `orgmunge.Heading`,
  every caller is now written against orgmunge and the shim is decorative.
  Return the suite's own frozen dataclasses — `core.TodoItem` already is one,
  which is a good sign rather than a coincidence.
- **File paths.** orgmunge's entry point is `Org(path, from_file=True)`. If that
  shape reaches the API, each backend gets to impose its own I/O policy, and
  ortask's atomic-write-and-resolve-symlinks discipline becomes unenforceable.
  `orglib` takes **text in and returns text out**; files stay the caller's
  business.
- **Library exceptions.** A `ply` parse error must surface as an `orglib` error
  type, or every caller ends up importing the backend to write an `except`.

### The surface

Small on purpose. It is the set of things ortask actually does, and adding to it
should feel expensive:

```python
# ortasklib/orglib/__init__.py

def parse(text: str) -> Document: ...

class Document(Protocol):
    def tasks(self) -> list[TodoItem]: ...
    def directories(self, project: str | None = None) -> list[str] | None: ...
    def set_state(self, task_id: str, state: str) -> None: ...
    def add_task(self, parent: str | None, state: str, text: str) -> str: ...
    def render(self) -> str: ...
```

`render()` returning text — rather than a `save()` taking a path — is what keeps
atomic writes, symlink resolution, and the "only touch `* Tasks`" rule in one
place instead of once per backend.

Note that `directories()` takes an optional project name. That is the registry
index file's requirement (`docs/config.md`) appearing in the API, and it is
worth having the shim absorb it: whether directories live in one central file or
one file per entry becomes a question the callers no longer ask.

### Backend selection

Runtime-selectable, with the default never in doubt:

```console
$ ORTASK_ORGLIB=orgmunge ort list      # opt in for one command
$ ort list                             # bespoke, always, unless asked
```

Two rules make this safe to live with:

- **An explicit request that cannot be honored is an error, never a fallback.**
  If `ORTASK_ORGLIB=orgmunge` is set and orgmunge is missing, fail loudly.
  Silently falling back to bespoke would mean the backend you thought you were
  testing was never exercised — which defeats the entire migration.
- **No request means bespoke.** Not "best available." The default must not
  change when someone happens to `pip install` something.

### Fidelity belongs in the contract

This is the part that is tempting to leave implicit, and the part that matters
most. Backends are not interchangeable on the axis ortask cares about: the
bespoke code preserves bytes it did not intend to change, and orgmunge does not
(measured below). So the shim should say so:

```python
class Backend(Protocol):
    name: str
    fidelity: Literal["exact", "normalizing"]
```

`"exact"` means every byte outside the edited region survives. `"normalizing"`
means the backend re-serializes the document and may rewrite untouched text.
Callers that must not reformat can then ask, rather than assume — and a
`normalizing` backend can still be perfectly good for read-only work like
`list`, `show`, and the TUI, which is most of what the suite does.

### The conformance suite is the specification

An API you can stand behind is one that is written down as executable
assertions. So: one test module, parameterized over every available backend,
asserting identical results.

```python
@pytest.fixture(params=orglib.available_backends())
def backend(request):
    return request.param
```

Two properties make this the real deliverable:

- **A backend is not "supported" until it passes.** That is the whole gate, and
  it is objective.
- **It is also the deletion criterion for the bespoke code.** If orgmunge ever
  passes the full suite including the fidelity assertions, the bespoke backend
  has no remaining job, and the suite is the evidence. That is exactly the
  outcome the shim is meant to make reachable.

Write the suite against the bespoke backend first, while it is the only one. A
conformance suite that was written after a second backend exists tends to encode
that backend's quirks as the specification.

## Migrating to orgmunge, slowly

### What orgmunge does to a file today

Measured against this repo's real files and a probe covering ortask's
constructs, with orgmunge 0.3.1. It **preserves** heading text, task IDs,
TODO/DONE states, priority cookies, tag names, body lines, property drawers,
list bullets and their indentation, tables, and prose. It **changes** exactly
three things:

| Deviation | Example |
|---|---|
| Downcases keyword lines | `#+TITLE: Probe` → `#+title: Probe` |
| Renormalizes tag padding | `Completed subtask     :research:` → `Completed subtask    :research:` |
| Drops blank lines before top-level headings | one blank line removed per occurrence |

On `todo.org` that is a single deleted blank line in 6.5 KB. On `README.org` it
is eleven hunks. Nothing is corrupted; the file is simply no longer byte-identical.

Two operational notes. PLY generates its LALR tables on first import and caches
them next to the installed package (`parsetab.py`, `parser.out`); nothing is
written to the working directory, which was the risk worth checking. A cold
import costs about 47 ms against 17 ms warm, and prints a shift/reduce-conflict
warning to stderr once. In a read-only or immutable install the tables cannot be
cached, so that cost and that warning would recur on every invocation.

### The mutation footgun

The obvious way to change a task's state does not work, and fails silently:

```python
heading.todo = "DONE"          # reads back as "DONE" — and changes nothing
heading.headline.todo = "DONE" # the real path
```

`Heading` has no `todo` attribute of its own; reads reach the `Headline` through
delegation, while writes land on the instance and shadow nothing. The in-memory
object then disagrees with its own serialized output, so a round-trip test that
checks the object rather than the text will pass while the file is untouched.

This is the single best argument for the shim. Behind `set_state()` this is one
line, written once, tested once. Spread across call sites it is a bug waiting
for each of them.

### The phases

Each phase is independently valuable and independently revertible, and no phase
requires committing to the next.

**Phase 0 — the shim, bespoke only.** Introduce `orglib` with one backend that
delegates to today's `core`/`tasks` functions, and route all callers through it.
No behavior change, no dependency, and the conformance suite gets written. This
is the largest phase and the only one that touches every caller. It is worth
doing even if orgmunge is never adopted, because it is what makes the question
answerable later.

**Phase 1 — orgparse in tests.** Independent validation of our output, described
in the next section. No runtime change.

**Phase 2 — orgmunge as a read-only backend.** Implement `tasks()`,
`directories()`, and `render()`; make write operations raise. Then run it
differentially: for every Org file in the repo and in the registry, assert that
the orgmunge backend and the bespoke backend report identical task lists. Read
paths are the safe place to discover that a grammar disagrees with a regex,
because being wrong costs a wrong listing rather than a damaged file.

**Phase 3 — orgmunge writes, gated per file.** See below.

**Phase 4 — decide.** Either the corpus is normalized to orgmunge's canonical
form and it becomes byte-exact, or bespoke stays the writer permanently. Both
are acceptable endings. The point of the shim is that the choice stays open and
cheap, not that orgmunge necessarily wins.

### The per-file fidelity gate

Phase 3's safety mechanism, and the reason writing through a normalizing backend
can be attempted at all. Before orgmunge is allowed to *write* a given file,
prove that it round-trips that file exactly:

```python
def orgmunge_may_write(text: str) -> bool:
    """True when orgmunge reproduces this file byte-for-byte untouched."""
    return orglib.parse_with("orgmunge", text).render() == text
```

If it does not, fall back to the bespoke writer for that file. This turns an
unbounded risk ("orgmunge might reformat something") into a bounded, per-file,
pre-flight check, and it costs one parse. Files that pass are exactly the files
where a normalizing backend is indistinguishable from an exact one.

It also gives the migration a progress metric: the fraction of real files that
pass the gate. Today `todo.org` fails it by one blank line. If that fraction
reaches 100% across the corpus, Phase 4 has answered itself.

The three deviations are all fixable on our side rather than orgmunge's —
lowercase keyword lines, single-space tag separation, no blank line before a
top-level heading. Conforming the corpus is a legitimate strategy, but it should
be a deliberate decision recorded here, because it means the tool's file
conventions are being chosen to suit a library.

## `orgparse` as an optional test dependency

orgparse is read-only, has zero dependencies and a BSD-2 license, and is the
closest thing to a reference implementation. That makes it ideal for answering a
question our own tests cannot: *is the file we produced really Org, or merely
something our own regexes accept?* A test suite that validates a parser with the
same parser proves nothing.

### Skipping cleanly

Module-level, so the whole file disappears when orgparse is absent:

```python
# tests/test_orgparse_conformance.py
import pytest
orgparse = pytest.importorskip("orgparse")
```

This keeps orgparse out of the hard requirements: `python3 -m pytest tests/`
must keep working on a machine with nothing installed, which is the current
promise and worth keeping.

### Keeping a skipped test honest

A soft dependency that is never installed is a test that never runs, and it will
rot without anyone noticing. Give the skip an off switch:

```python
if os.environ.get("ORTASK_REQUIRE_ORGPARSE"):
    import orgparse          # ImportError here is the point
else:
    orgparse = pytest.importorskip("orgparse")
```

Then one CI job — or one documented local command — sets
`ORTASK_REQUIRE_ORGPARSE=1`, and a missing orgparse becomes a failure there
while staying a skip everywhere else. Without something like this, "optional"
degrades into "dead."

### Teach it our keywords

orgparse recognizes only `TODO` and `DONE` by default. ortask also uses `MOOT`
as a terminal state, and an unconfigured orgparse silently parses
`** MOOT t0007 Abandoned` as an ordinary heading whose text begins with "MOOT" —
so the validator would disagree with us for a reason that is not a bug in
either. Verified; configure it explicitly:

```python
env = orgparse.loads("").env
env.add_todo_keys(todos=["TODO", "MOOT"], dones=["DONE"])
root = orgparse.loads(text, env=env)
```

Worth noting what this implies beyond the tests: Emacs would make the same
mistake. A `#+TODO: TODO MOOT | DONE` line in the file fixes it for orgparse,
for Emacs, and for any other reader at once, which is the more idiomatic fix and
belongs in the `docs/format.md` conversation rather than this one.

### What to assert

Not "orgparse can read it" — that is too weak to fail usefully. Assert
*agreement*, per file, over the tuple ortask actually claims to control:

```python
ours = [(t.level, t.state, f"{t.id} {t.text}", t.priority,
         tuple(sorted(t.tags.split(":"))) if t.tags else ())
        for t in core.parse_org(text)]
theirs = [(n.level, n.todo, n.heading, n.priority, tuple(sorted(n.tags)))
          for n in orgparse.loads(text, env=env)[1:] if n.todo]
assert ours == theirs
```

The two normalizations in there are not incidental, and getting them wrong is
how this test fails for reasons that are not bugs. `TodoItem` keeps `id` and
`text` in separate fields while orgparse's `heading` holds both, and
`TodoItem.tags` is a raw `str | None` (`"research:tag2"`) while orgparse gives a
set. Anything the two libraries merely *represent* differently has to be
normalized away, or the test measures modeling choices instead of correctness.

Run it over every fixture the suite already builds and over the repo's own
`todo.org`, which currently gives 31 tasks under both implementations — that
agreement is the baseline this test is protecting.

The highest-value moment for this check is immediately after a write: take the
text `ortask.py` just produced and confirm an independent parser sees the tasks
we think we wrote. That is where a malformed heading would otherwise escape.
