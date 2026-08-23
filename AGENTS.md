# Repository Guidelines

## Project Structure & Module Organization
`ortask.py` is the local, single-Org-file CLI; `projmgr.py` manages projects in
a project registry. In `ortasklib/`, `core.py` provides discovery, `tasks.py`
owns task queries and edits, `manager.py` owns registry behavior, `log.py` owns
activity events, `viewstate.py` owns what a list shows and in what order, and
`menu.py`/`taskui.py` implement the bounded TUI. Peer
`textbuffer.py` owns format-neutral transactional file editing;
`orglib/` owns Org syntax. Neither imports `ortasklib`. Tests are in `tests/`;
design context is in `docs/`; shell integration is in `misc/`.

## Build, Test, and Development Commands
There is no build step. Use Python 3.10+ directly:

- `./ortask.py --help` lists local task commands.
- `./ortask.py --file /tmp/sample.org list` exercises a fixture.
- `./projmgr.py --help` lists registry and project commands.
- `python3 -m py_compile textbuffer.py ortask.py projmgr.py ortasklib/*.py orglib/*.py` checks syntax.
- `python3 -m pytest -q` runs the regression suite.

Core CLI behavior is stdlib-only. `prompt_toolkit` and Rich enhance the TUI;
`pytest` runs tests. Optional-package fallbacks must remain functional.

## Org Files, Style, and Naming
Unless `--file` or `ORTASK_FILE` overrides discovery, it walks upward for
`tasks.org`, `task.org`, exactly one `*.task.org`, then compatibility names
`TODO.org`, `TODO*.org`, and `todo.org`. As a final current-directory fallback,
exactly one generic `*.org` is accepted; ambiguity is an error.

Use 4-space indentation, type hints, explicit functions, and useful
dataclasses. Use `snake_case` and `UPPER_SNAKE_CASE`. Register subcommands
alphabetically across parsers, dispatch,
completion, help, and command references. Keep `orglib` generic; ortask policy
belongs above it. Keep `textbuffer.py` free of Org, project, logging, and TUI
policy; supply those through adapters and hooks. Preserve Org source formatting
with bounded line edits and atomic writes rather than whole-file reserialization.

## Testing and Data Safety
Add focused pytest coverage with temporary Org and registry fixtures. Start each
test with a short comment explaining its contract. Cover semantic results and
source preservation, especially surrounding prose, headings, drawers, and
unrelated registry sections.

Never mutate real repository files or the configured registry for
verification. Use temporary fixtures, `--help`, syntax checks, tests, or dry
runs. Ask before commands such as
`ortask.py add`, `archive`, `done`, or `repair` on real data.

## Agent Logs, Commits, and Reviews
For every user-requested repository change, append one concise, timestamped
entry to `docs/llm-log.org` using the actual model name and existing Org format.
Do not log investigation-only turns. Keep commit subjects short and imperative;
include the task ID when applicable. Pull requests should describe visible
behavior, tests run, documentation changes, and any untested write paths.
