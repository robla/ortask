# ORTASK(1)

## NAME

ortask.py - inspect and update org-mode TODO tasks in local Org files

## SYNOPSIS

```
ortask.py [<subcommand>] [<options>]
ortask.py list [--todo | --done | --all] [--root-only] [--items N] [--format FORMAT] [--file FILE]
ortask.py show <id> [--file FILE]
ortask.py add <title> [--parent ID] [--file FILE]
ortask.py done <id> [--file FILE]
ortask.py open <id> [--file FILE]
ortask.py repair [--dry-run | --fix] [--file FILE]
ortask.py apply [--template NAME] [--week WEEK] [--date YYYY-MM-DD] [--dry-run] [--file FILE]
ortask.py -i | --interactive [--file FILE]
```

## DESCRIPTION

**ortask.py** reads and edits TODO/DONE task headings in a local org-mode file.
If a top-level `* Tasks` section exists, parsing is scoped to that subtree;
otherwise valid task headings are read from the whole file. By default it
prefers dedicated task files such as `tasks.org`,
`task.org`, or `NAME.task.org`, walking upward from the current directory before
falling back to compatibility names and unambiguous local Org files.
Tasks are org headings with TODO/DONE keywords and stable IDs such as
`t0001`, `t0001.1`, `tw26W24`, and `tw26W24.1`. See `--file` under
GLOBAL OPTIONS for the file resolution order.

When invoked with no subcommand, **list** is assumed.

## SUBCOMMANDS

### list

Print tasks.  With no flags, prints all tasks in indented plain text.

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

### show

```
ortask.py show t0001
```

Print a single task by ID, including its body text, properties drawer,
deadlines, and any subtasks.

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

### done

```
ortask.py done t0002
```

Change a task's keyword from TODO to DONE.  Only the matched heading
line is modified; all other file content is preserved.

### open

```
ortask.py open t0002
```

Change a task's keyword from DONE back to TODO.

### repair

```
ortask.py repair
ortask.py repair --dry-run
ortask.py repair --fix
```

Scan the task tree for problems — duplicate IDs, gaps in numbering,
subtask IDs that don't match their parent heading, or headings under
`* Tasks` that are missing IDs — and fix them.

**--fix**
:   Apply repairs to the file (default).

**--dry-run**
:   Report what would be changed without modifying the file.

### apply

```
ortask.py apply
ortask.py apply --week 2026W26 --dry-run
ortask.py apply --date 2026-06-25
```

Instantiate the file's single top-level `* Template` subtree as new tasks under
`* Tasks`, substituting weekly placeholders (`twYYWNN`, `Month Day`, etc.) for a
target week.  Existing tasks are left untouched.  See `docs/templates.md` for
the placeholder set and insertion rules.

**--template** *NAME*
:   Template profile to apply.  Defaults to `weekly`, currently the only
    supported profile.

**--week** *WEEK*
:   Target ISO week, e.g. `2026W26` or `26W26`.  Defaults to the current week.

**--date** *YYYY-MM-DD*
:   Target date.  Derives the ISO week when `--week` is omitted; when both are
    given the date must fall inside the week.

**--dry-run**
:   Print the Org content that would be inserted without modifying the file.

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
    alphabetically.

**-i, --interactive**
:   Open `projtui.py`'s task menu for the resolved local Org file instead of
    listing tasks. This bypasses the global project registry and uses the same
    file lookup rules as other `ortask.py` commands.

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

Write operations (`add`, `done`, `open`, `repair --fix`) follow these rules:

- Edits touch only selected task headings, inserted task headings, or the
  `* Tasks` subtree when a command explicitly inserts there; all other content
  is preserved.
- For state changes, only the matched heading line is rewritten.
- For `add`, the new heading is appended at the end of the task subtree
  (or after the last sibling under the parent for subtasks).
- For `repair --fix`, only lines with detected problems are rewritten.
- Writes go to a temporary file first, then atomically replace the original.

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

The script registers completion for `ortask.py`, `./ortask.py`, and the common
`ort` alias. For example, `ortask.py ad<Tab>` and `ort ad<Tab>` complete to
`add`. A Debian package can install the same file under
`/usr/share/bash-completion/completions/`.
