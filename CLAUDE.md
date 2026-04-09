# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

ortask is a Python CLI tool for querying and editing TODO tasks in org-mode files. It operates on a `* Tasks` subtree within an org file, using standard `TODO`/`DONE` keywords and stable task IDs (`t0001`, `t0001.1`, etc.).

The tool is a single-file script (`ortask.py`) with no external dependencies (Python 3.10+, stdlib only).

## Running

```sh
./ortask.py                        # list all tasks (default: README.org)
./ortask.py list --state todo      # open tasks only
./ortask.py --file /path/to.org    # operate on a different file
```

The intended shell alias is `ort`.

## Current state vs. planned state

`ortask.py` implements the TODO/DONE keyword parser and all subcommands from the spec: `list`, `show`, `add`, `done`, `open`, `repair`. The `repair` subcommand detects problems (duplicate IDs, missing IDs, mismatched subtask prefixes) but auto-fix of renumbering is not yet implemented — it reports and exits. See `docs/ortask.md` for the full subcommand reference.

## Key files

- `ortask.py` — the entire tool (single file, ~90 lines currently)
- `README.org` — both project docs and the default task data file (contains `* Tasks` subtree with real tasks)
- `docs/ortask.md` — the target subcommand spec; treat as the source of truth for planned behavior

## Architecture

The code should maintain three layers:

1. **Parser**: `parse_org(text) -> list[TodoItem]` — regex-based, tracks source line numbers
2. **Query/mutate**: filter, search, modify the in-memory model
3. **Writer**: minimal text patching (change only matched heading lines, preserve everything else)

Write operations use atomic file replacement (write to temp file, then rename).

## Task heading format

```org
** TODO t0005 Some task title
** TODO [#A] t0006 Urgent task with priority
** DONE t0001.2 Completed subtask       :research:
```

The heading regex that the parser should match:

```
^\*+\s+(?P<state>TODO|DONE)\s+(?:\[#(?P<priority>[A-C])\]\s+)?(?P<id>t\d{4}(?:\.\d+)*)\s+(?P<text>.*?)(?:\s+:(?P<tags>[\w:]+):)?\s*$
```

## Testing

No test suite exists yet. When adding tests, use pytest with fixture org documents covering: heading parsing with/without priorities and tags, `--items` limits, ID allocation, and round-trip edits that preserve unrelated lines.

## Design docs

- `docs/ortask.md` — man-page-style subcommand reference
- `docs/claude-ortask-design.org` — architecture and format spec
- `docs/codex-ortask-design.org` — phased implementation, testing emphasis
- `docs/gemini-ortask-design.org` — LLM integration, robust regex, atomic writes
- `docs/adjacent-trackers.md` — survey of Taskwarrior, todo.txt-cli, dstask, etc.
- `docs/naming.md` — how the name was chosen

## Conventions

- Subcommands are verbs: `list`, `show`, `add`, `done`, `open`, `repair`. This aligns with Taskwarrior conventions.
- File edits are conservative: only touch the `* Tasks` subtree, only rewrite matched lines, never reformat the whole file.
- Task IDs (`t0001`–`t9999`) are permanent and never reused.
