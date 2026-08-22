# Repository Guidelines

## Project Structure & Module Organization
`ortask.py` is the local, single-Org-file CLI; `projmgr.py` manages projects in
a project registry. Shared code lives in `ortasklib/`: `core.py` provides
discovery, `tasks.py` owns task queries and surgical edits,
`manager.py` owns registry behavior, and `menu.py`/`taskui.py` implement the
bounded TUI. `orglib/` is a standalone Org syntax package and must not import
`ortasklib`. Tests are in `tests/test_ortask_suite.py`; specifications and design
context are in `docs/`; shell integration is in `misc/`.

## Build, Test, and Development Commands
There is no build step. Use Python 3.10+ directly:

- `./ortask.py --help` lists local task commands.
- `./ortask.py --file /tmp/sample.org list` exercises a fixture.
- `./projmgr.py --help` lists registry and project commands.
- `python3 -m py_compile ortask.py projmgr.py ortasklib/*.py orglib/*.py` checks syntax.
- `python3 -m pytest -q` runs the regression suite.

Core CLI behavior is stdlib-only. `prompt_toolkit` enables the full interactive
UI, Rich improves numbered tables, and `pytest` is required for tests; TUI code
must retain fallback behavior when optional packages are absent.

## Org Files, Style, and Naming
Unless `--file` or `ORTASK_FILE` overrides discovery, it walks upward for
`tasks.org`, `task.org`, exactly one `*.task.org`, then compatibility names
`TODO.org`, `TODO*.org`, and `todo.org`. As a final current-directory fallback,
exactly one generic `*.org` is accepted; ambiguity is an error.

Use 4-space indentation, type hints, explicit functions, and dataclasses
where useful. Use `snake_case` names and `UPPER_SNAKE_CASE`
constants. Register subcommands alphabetically across parsers, dispatch,
completion, help, and command references. Keep `orglib` generic; ortask policy
belongs in `ortasklib`. Preserve Org source formatting with bounded line edits
and atomic writes rather than whole-file reserialization.

## Testing and Data Safety
Add focused pytest coverage with temporary Org and registry fixtures. Start each
test with a short comment explaining its contract. Cover semantic results and
source preservation, especially surrounding prose, headings, drawers, and
unrelated registry sections.

Never run a mutating command against real repository files or the configured
registry merely to verify an implementation. Use temporary fixtures, `--help`,
syntax checks, tests, or documented dry runs. Ask before commands such as
`ortask.py add`, `archive`, `done`, or `repair` on real data.

## Agent Logs, Commits, and Reviews
For every user-requested repository change, append one concise, timestamped
entry to `docs/llm-log.org` using the actual model name and existing Org format.
Do not log investigation-only turns. Keep commit subjects short and imperative;
include the task ID when applicable. Pull requests should describe visible
behavior, tests run, documentation changes, and any untested write paths.
