# ORTASK(1)

## NAME

ortask.py - inspect and update org-mode TODO tasks in README.org

## SYNOPSIS

```
ortask.py [<subcommand>] [<options>]
ortask.py list [--todo | --done | --all] [--root-only] [--items N] [--format FORMAT] [--file FILE]
ortask.py show <id> [--file FILE]
ortask.py add <title> [--parent ID] [--file FILE]
ortask.py done <id> [--file FILE]
ortask.py open <id> [--file FILE]
ortask.py repair [--dry-run | --fix] [--file FILE]
```

## DESCRIPTION

**ortask.py** reads and edits the `* Tasks` section of an org-mode
file (by default `README.org` in the current working directory).
Tasks are org headings with TODO/DONE keywords and stable IDs of the
form `t0001`, `t0001.1`, etc.

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
:   Show only top-level tasks (direct children of `* Tasks`), hiding subtasks.

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
ortask.py add "Research FooCorp"
ortask.py add "Check Glassdoor reviews" --parent t0003
```

Append a new task heading to the `* Tasks` section.  The next
available ID is assigned automatically (zero-padded to 4 digits).

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

## GLOBAL OPTIONS

**--file** *FILE*
:   Org file to operate on.  Default: `README.org` in the current working directory.

## TASK ID FORMAT

IDs are assigned sequentially and never reused:

| Level    | Format       | Example  |
|----------|--------------|----------|
| Top-level| `t` + 4 digits | `t0001`  |
| Subtask  | parent + `.N`  | `t0001.3`|
| Nested   | parent + `.N`  | `t0001.3.1` |

IDs appear immediately after the TODO keyword (and optional priority
cookie) in the org heading:

```
** TODO t0005 Some task title
** TODO [#A] t0006 Urgent task with priority
** DONE t0001.2 Completed subtask       :research:
```

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

- Only the `* Tasks` subtree is touched; all other content is preserved.
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
./ortask.py list --state todo
```

Add a task and mark it done:
```
./ortask.py add "Apply to FooCorp"
./ortask.py done t0004
```

Get JSON for scripting:
```
./ortask.py list --format json | jq '.[] | select(.state == "TODO") | .title'
```

Preview repairs without changing the file:
```
./ortask.py repair --dry-run
```

Fix ID problems:
```
./ortask.py repair
```

## SEE ALSO

docs/claude-ortask-design.org, docs/codex-ortask-design.org,
docs/gemini-ortask-design.org
