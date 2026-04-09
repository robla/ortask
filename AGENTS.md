# Repository Guidelines

## Project Structure & Module Organization
`ortask.py` is the only executable source file. It currently holds the CLI, parser, data model, and rendering logic. `README.org` is the default input file and main quick start. Use `docs/ortask.md` as the desired CLI contract and `docs/*.org` for design rationale. Put new tests under `tests/`.

## Build, Test, and Development Commands
Use the script directly:

- `./ortask.py` lists TODO items from `README.org`.
- `./ortask.py --items 5` limits output for quick checks.
- `./ortask.py --file path/to/file.org` runs against another Org file.
- `python3 -m py_compile ortask.py` performs a fast syntax check.
- `python3 -m pytest -q` is the expected test command once a test suite exists.

The repository is stdlib-only; there is no build step or dependency install.

## Current State & Contribution Priorities
This repo is specification-first: the docs describe an Org-heading task manager, while `ortask.py` still parses the older checkbox format and supports only listing plus `--items`. Treat `README.org` as the main fixture, and treat the design docs as guidance rather than executable truth.

When extending the tool, work in this order:

- update parsing to support `TODO`/`DONE` headings with stable IDs like `T0001`
- keep checkbox compatibility if it stays cheap
- add `argparse` subparsers with `list` as the default behavior
- implement read-only features before write commands
- defer repair/sync-style commands until parse and write paths are tested

## Coding Style & Naming Conventions
Follow existing Python style: 4-space indentation, type hints, `dataclass` models where helpful, and small functions with explicit names. Prefer `snake_case` for functions and variables, `UPPER_SNAKE_CASE` for module constants, and short imperative subcommand names such as `list` or `done`. Keep a parser/query-writer split and prefer minimal line-level rewrites over reserializing the whole file.

## Testing Guidelines
There is no committed test suite yet. New behavior should include `pytest` tests with small fixture Org documents that cover parsing, item limits, ID handling, and round-trip edits. Name tests by behavior, for example `test_limit_items_rejects_negative_values`. Prioritize mixed old/new heading parsing, ID allocation, and edits that preserve surrounding prose, drawers, blank lines, and non-task sections. Until CI exists, include the exact manual commands you ran in the change description.

## Commit & Pull Request Guidelines
Current history uses short, imperative commit subjects. Keep that pattern: `Add TODO heading parser` is better than `changes`. Pull requests should summarize user-visible behavior, note any README or docs updates, and include before/after CLI examples when output changes. Link related issues when available, and call out any gaps such as untested file-write paths.
