# ORTASK(1)

## NAME

ortask.py - inspect and update org-mode TODO tasks in local Org files

## SYNOPSIS

```
ortask.py [<subcommand>] [<options>]
ortask.py add <title> [--parent ID] [--file FILE]
ortask.py apply [--template NAME] [--week WEEK] [--date YYYY-MM-DD] [--dry-run] [--file FILE]
ortask.py archive [<id>] [--file FILE]
ortask.py done <id> [--file FILE]
ortask.py help
ortask.py [--file FILE] info [--file | --format FORMAT]
ortask.py [--file FILE] init
ortask.py list [--todo | --done | --all] [--root-only] [--items N] [--format FORMAT] [--file FILE]
ortask.py [--file FILE] log [--since WHEN] [--until WHEN] [--all] [--limit N] [--day-start HH:MM] [--format FORMAT]
ortask.py open <id> [--file FILE]
ortask.py repair [--dry-run | --fix] [--file FILE]
ortask.py show <id> [--file FILE]
ortask.py -i | --interactive [--file FILE] [list --todo | --done | --all]
```

## DESCRIPTION

**ortask.py** reads and edits Org task headings in a local org-mode file.
If a top-level `* Tasks` section exists, parsing is scoped to that subtree;
otherwise valid task headings are read from the whole file. By default it
prefers dedicated task files such as `tasks.org`,
`task.org`, or `NAME.task.org`, walking upward from the current directory before
falling back to compatibility names and unambiguous local Org files.
Tasks are Org headings with stable IDs such as
`t0001`, `t0001.1`, `tw26W24`, and `tw26W24.1`. See `--file` under
GLOBAL OPTIONS for the file resolution order.
The CLI creates and toggles `TODO`/`DONE`; shared workflow tools may also use
terminal `MOOT`, which list/show parsing treats as completed work. The former
`SUPERSEDED` spelling remains a terminal compatibility alias.

When invoked with no subcommand, **list** is assumed.

## SUBCOMMANDS

Subcommands are registered and documented alphabetically. Keep parser help,
dispatch tables, shell completions, and this reference in the same order.

### add

```
ortask.py add "Research storage formats"
ortask.py add "Determine CommonMark's suitability for FooProj" --parent t0003
```

Append a new top-level task heading to the `* Tasks` section.  The next
available ID is assigned automatically (zero-padded to 4 digits). With
`--parent`, add a child under an existing parsed task even if the file has no
`* Tasks` section.
If the file has no `* Tasks` section, `add` refuses to modify existing prose
files. It only bootstraps the section automatically for an empty dedicated task
file such as `tasks.org`, `task.org`, `todo.org`, or `*.task.org`.
If no task file is discovered and no `--file` is supplied, `add` creates
`tasks.org` in the current directory.

**--parent** *ID*
:   Create a subtask under the given parent instead of a top-level task.
    The subtask ID is derived from the parent (e.g. `t0003.1`, `t0003.2`).

### apply

```
ortask.py apply
ortask.py apply --week 2026W26 --dry-run
ortask.py apply --date 2026-06-25
```

Instantiate the file's single top-level `* Template` subtree as new tasks under
`* Tasks`, substituting weekly placeholders (`twYYWNN`, `Month Day`, etc.) for a
target week. Existing tasks are left untouched. See `docs/templates.md` for
the placeholder set and insertion rules.

**--template** *NAME*
:   Template profile to apply. Defaults to `weekly`, currently the only
    supported profile.

**--week** *WEEK*
:   Target ISO week, e.g. `2026W26` or `26W26`. Defaults to the current week.

**--date** *YYYY-MM-DD*
:   Target date. Derives the ISO week when `--week` is omitted; when both are
    given the date must fall inside the week.

**--dry-run**
:   Print the Org content that would be inserted without modifying the file.

### archive

```
ortask.py archive
ortask.py archive t0007
```

With no ID, move every `DONE` task subtree to the stock Org archive path:
`tasks.org` becomes `tasks.org_archive`, `todo.org` becomes
`todo.org_archive`, and so on. `MOOT` and `SUPERSEDED` are not selected by the
default sweep. When a DONE parent and its descendants are all candidates, the
parent subtree is moved once. A DONE child of an open parent is archived on its
own.

With an ID, move only that task's whole subtree, regardless of its current
state. Archived roots become level-1 headings because the stock `%s_archive::`
location has no container heading. Ortask adds Org's standard archive-context
properties, including the time, source file, former outline path, category,
TODO state, and inherited tags.

New archives include an Org mode line and the source file's `#+TODO:` workflow
declaration. Existing declarations are merged so old and current workflow
keywords remain parseable. The destination is written first and restored if
the source write fails. Archived IDs remain reserved by later `add` commands.

### done

```
ortask.py done t0002
```

Change a task's keyword from TODO to DONE.  Only the matched heading
line is modified; all other file content is preserved.

### help

Print the top-level command help. Bare `ortask.py` still defaults to `list`;
use `ortask.py help` or `ortask.py --help` to display the command inventory.

### info

**Status: specified, not implemented (`t0032`).**

```
ortask.py info
ortask.py info --file
ortask.py info --format json
```

Report what `ortask.py` knows about the active task file, and change nothing.

`ort info` answers about *a file*; `pmgr info` answers about *a project*. That
split follows the layering in `docs/architecture.md`, and it is the reason
`ort info` deliberately does **not** report a project name: the registry is the
project layer's subject, and two commands printing the same string would leave a
caller guessing which to call. Use `pmgr info --name` for that.

Output includes:

- **Task file** — the canonical path, and how it was found: `--file`,
  `$ORTASK_FILE`, or the upward walk (naming the directory the walk stopped in).
  `resolve_org_file()` returns a bare path today and will have to carry that
  provenance.
- **Tasks subtree** — the heading depth of `* Tasks` and the line range it spans.
- **Counts** — `TODO`, `DONE`, and `MOOT` totals, using `core.TERMINAL_STATES`
  rather than a second list of state names.
- **IDs** — which allocation schemes the file uses (numeric `tNNNN`, weekly
  `twYYWNN`) and the highest assigned ID in each.
- **Keywords** — the `#+TODO:` line the file declares. No task file declares one
  yet (`t0023`), so until that lands this reports "none declared" rather than
  looking broken.
- **Archive** — the stock archive path (`<file>_archive`), and whether it exists.

**--file**
:   Print only the canonical task file path, one line, and exit 0. Prints
    nothing and exits 1 when no file resolves. The single-field rules in
    `docs/projmgr.md` under "The single-field forms" apply here too: one bare
    line on stdout, silence on stderr, and mutually exclusive with `--format`.

**--format** *FORMAT*
:   `plain` (default) or `json`.

### init

```
ortask.py init
ortask.py --file bashfuncs.task.org init
```

Create an empty dedicated task file containing exactly:

```org
* Tasks
```

Without an override, `init` always targets `./tasks.org`; it does not walk
upward or select a generic Org file. `--file` and `ORTASK_FILE` may choose
`tasks.org`, `task.org`, `TODO.org`, `todo.org`, or a name ending in
`*.task.org`. The parent directory must already exist. A missing file is
created exclusively, an empty existing file is initialized, and a nonempty
file is refused without modification. Use `add` instead when the first task is
already known.

### list

Print tasks. With no flags, prints all tasks in indented plain text.

**--todo**
:   Show only open tasks (default).

**--done**
:   Show only completed tasks.

**--all**
:   Show all tasks regardless of state.

**--root-only**
:   Show only top-level parsed tasks, hiding subtasks. In a `* Tasks` file this
    means direct children of `* Tasks`; otherwise it means the shallowest parsed
    task heading level.

**--items** *N*
:   Limit output to the first *N* matching tasks.

**--format** *FORMAT*
:   Output format: `plain` (default), `json`, or `org`.

### log

```
ortask.py log --since today
ortask.py log --since 2026-08-21 --until 2026-08-22 --format org
ortask.py log --all --limit 20
```

Read the disposable activity log described in `docs/logging.md`. By default,
events are scoped to the resolved task file's registered project; an
unregistered file is matched by canonical path. `--all` reads every project
and does not require a local task file. Empty or missing logs produce no output
and exit successfully.

**--since**, **--until** *WHEN*
:   Inclusive start and exclusive end. `WHEN` accepts `today`, `yesterday`,
    `week`, `NNd`, `NNh`, `YYYY-MM-DD`, or an ISO timestamp. `week` begins at
    the current Monday workday boundary.

**--all**
:   Do not restrict events to the local task file or its registered project.

**--limit** *N*
:   Select the newest *N* matches, then print them chronologically.

**--day-start** *HH:MM*
:   Move named day boundaries and Org date grouping from midnight.

**--format** *FORMAT*
:   `plain` (default), unchanged source `json` lines, or grouped `org` output.

### open

```
ortask.py open t0002
```

Change a task's keyword from DONE back to TODO.

### repair

**Status: reporting is implemented; fixing, the prompt, and `--force` are
specified but not built (`t0032`, and `t0004`/`t0008` for the fixes
themselves).** Today `repair` reports and stops, and its subparser help still
claims "find and fix ID problems".

```
ortask.py repair
ortask.py repair --force
ortask.py repair --dry-run
```

Scan the task tree for problems — duplicate IDs, subtask IDs that do not match
their parent heading, headings under `* Tasks` missing an ID — then repair what
it safely can, asking first.

The contract matches `projmgr.py repair` exactly; the two verbs differ only in
subject, one a task file and one a registry.

**(no flag)**
:   Report every problem, then prompt before each fix and apply only what was
    confirmed.

**--force**
:   Apply every available fix without prompting. Without a TTY and without
    `--force`, `repair` refuses and exits 1 rather than hanging on a prompt or
    silently deciding to write.

**--dry-run**
:   Report and stop. Never prompts, never writes.

Exit status is 0 when no problems are found, 2 when problems remain once the
command finishes, and 1 when the file cannot be read. A run that fixes
everything exits 0; one that fixes some exits 2, because something is still
wrong. `--dry-run` fixes nothing, so any problem leaves it at 2.

> This replaces the earlier behavior where bare `repair` exited 0 with problems
> outstanding while `--dry-run` exited 2 — the same condition reported two ways.

### show

```
ortask.py show t0001
```

Print a single task by ID, including its body text, properties drawer,
deadlines, and any subtasks.

## GLOBAL OPTIONS

**--file** *FILE*
:   Org file to operate on. Default resolution order:
    1. `--file` itself wins over all automatic discovery
    2. `ORTASK_FILE` environment variable
    3. From the current directory upward, nearest directory first:
       `tasks.org`, then `task.org`
    4. In the same upward walk: exactly one `*.task.org`; multiple matches in
       one directory are ambiguous. This tier is preferred over generic
       `*.org` files such as `foo.org`.
    5. Compatibility names in the same upward walk: `TODO.org`, exactly one other
       `TODO*.org`, then `todo.org`
    6. Exactly one generic `*.org` in the original current directory only

    Ambiguous tiers produce an error instead of silently choosing
    alphabetically. `init` is the exception to automatic discovery: absent an
    explicit or environment override, it uses `./tasks.org` directly. `log
    --all` is the other exception and needs no task file because it reads the
    registry-wide activity stream.

**-i, --interactive**
:   Open the shared `taskui` task menu for the resolved local Org file instead of
    listing tasks. This bypasses the global project registry and uses the same
    file lookup rules as other `ortask.py` commands.

    The menu starts on open tasks, the same selection `list` makes by default: a
    task file is mostly finished work, and the reason to open it is what is
    left. `C-t` cycles visibility through `all -> TODO -> DONE+` and `v` opens
    the view options screen, so the other states are one keystroke away.

    `list`'s state flags also reach the menu when both are given —
    `ortask.py -i list --all` starts showing every state, and `--done` starts on
    terminal ones.

## TASK ID FORMAT

IDs are assigned sequentially and never reused:

| Level    | Format       | Example  |
|----------|--------------|----------|
| Top-level| `t` + 4 digits | `t0001`  |
| Weekly   | `tw` + week    | `tw26W24` |
| Subtask  | parent + `.N`  | `t0001.3`|
| Nested   | parent + `.N`  | `t0001.3.1` |

IDs appear immediately after the TODO keyword (and optional priority
cookie) in the org heading:

```
** TODO t0005 Some task title
** TODO tw26W24 Promote this week's episode
** TODO [#A] t0006 Urgent task with priority
** DONE t0001.2 Completed subtask       :research:
```

Weekly IDs are for recurring week-scoped work. Accepted week forms are
`tw26W24`, `tw26w24`, `tw2026W24`, and `tw2026w24`; command input may
omit the `tw` prefix, so `ortask.py show 26W24` resolves to `tw26W24`.
Two- and four-digit year forms compare as the same week for lookup, so
`ortask.py show 2026w24` also resolves to `tw26W24`.

## OUTPUT FORMATS

### plain (default)

```
[DONE] t0001 Initialize LLMs in this directory
  [DONE] t0001.1 ChatGPT/codex
  [DONE] t0001.2 Gemini
  [DONE] t0001.3 Claude
[TODO] t0002 Build tool that updates this TODO list
[TODO] t0003 Find core links for jobhunt
```

### json

```json
[
  {
    "id": "t0001",
    "state": "DONE",
    "title": "Initialize LLMs in this directory",
    "level": 2,
    "subtasks": [
      {"id": "t0001.1", "state": "DONE", "title": "ChatGPT/codex", "level": 3},
      {"id": "t0001.2", "state": "DONE", "title": "Gemini", "level": 3},
      {"id": "t0001.3", "state": "DONE", "title": "Claude", "level": 3}
    ]
  },
  {"id": "t0002", "state": "TODO", "title": "Build tool that updates this TODO list", "level": 2},
  {"id": "t0003", "state": "TODO", "title": "Find core links for jobhunt", "level": 2}
]
```

### org

Re-emits matching tasks as valid org-mode headings (useful after
filtering with `--state` or `--root-only`).

## FILE MODIFICATION

Write operations (`add`, `apply`, `archive`, `done`, `init`, `open`,
`repair --fix`)
follow these rules:

- Edits touch only selected task headings, inserted task headings, or the
  `* Tasks` subtree when a command explicitly inserts there; all other content
  is preserved.
- For state changes, only the matched heading line is rewritten.
- For `add`, the new heading is appended at the end of the task subtree
  (or after the last sibling under the parent for subtasks).
- For `init`, a missing file is created exclusively; only an existing empty
  dedicated task file may be replaced.
- For `archive`, complete subtrees move to the adjacent `.org_archive` file;
  both replacements are atomic and the archive write is rolled back if the
  source replacement fails.
- For `repair --fix`, only lines with detected problems are rewritten.
- Existing-file replacements go to a temporary file first, then atomically
  replace the original.

## EXIT STATUS

- **0** — success
- **1** — task ID not found, invalid arguments, or file not readable
- **2** — `repair --dry-run` found problems (with details on stderr)

## EXAMPLES

List open tasks:
```
./ortask.py list --todo
```

Add a task, automatically assinging it a task number:
```
./ortask.py add "Write tests for FooProj"
```

...and mark it done (assuming it was assigned "t004"):
```
./ortask.py done t0004
```

Get JSON for scripting:
```
./ortask.py list --format json | jq '.[] | select(.state == "TODO") | .title'
```

Preview "repairs" without changing the file ("repairs" are making the file adhere to ortask.py norms):
```
./ortask.py repair --dry-run
```

Fix ortask.py incompatibilities (e.g. adding ortask.py IDs where missing):
```
./ortask.py repair
```

## BASH COMPLETION

Source the bundled completion script to complete subcommands and options:

```sh
source /path/to/ortask/misc/ortask-completion.bash
```

The script registers completion for `ortask.py`, `./ortask.py`, the common
`ort` alias, `projmgr.py`, `./projmgr.py`, the `pmgr`/`ptui` aliases, and the
`cdproj` shell function from `misc/cdproj.func.sh`. For example,
`ortask.py ad<Tab>` and `ort ad<Tab>` complete to `add`, while `pmgr -<Tab>`
includes `-i` and `--interactive`. `cdproj <Tab>` and `pmgr rm <Tab>` complete
registered project names; see `docs/projmgr.md` for how. A Debian package can
install the same file under
`/usr/share/bash-completion/completions/`.
