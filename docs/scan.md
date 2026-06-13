# ortask.py scan

`scan` is a proposed read-only subcommand for listing projects and their
top-level open tasks.

## Goal

`ortask.py scan` should answer: "What projects does ortask know about, and
what is the top-level work in each one?"

It should not replace `list`, `show`, or `projtui.py`. `list` remains the
single-file task listing command. `projtui.py` remains the interactive
project menu. `scan` is the non-interactive, scriptable overview.

## Command Shape

```sh
ortask.py scan
ortask.py scan --projdir ~/tmpsorta/proj2026
ortask.py scan --all
ortask.py scan --format plain
ortask.py scan --format json
```

`--projdir` overrides configured/default project discovery. The default
project directory should match `projtui.py`: command-line `--projdir`, then
`~/.config/ortask/projtui.ini`, then `~/Projects`.

## Project Discovery

`scan` should use the same project list as `projtui.py`:

- scan immediate subdirectories of the configured project directory
- skip hidden directories and infrastructure directories such as `.git`,
  `docs`, and `__pycache__`
- choose each project's Org file using the same naming order as `projtui.py`
- do not recurse arbitrarily through project trees

For now, keeping discovery shared with `projtui.py` matters more than making
`scan` independently clever.

## Task Selection

For each discovered project, show only top-level open tasks:

- parse the selected Org file with the same parser as `ortask.py list`
- include only `TODO` tasks by default
- include only direct children of `* Tasks` (`level == 2`)
- omit subtasks from the main scan output
- with `--all`, include top-level `DONE` tasks too

If a project has an Org file but no parseable `* Tasks` section, show the
project with a short note in plain output, and include a warning field in JSON.

## Plain Output

Suggested default:

```text
elweek  elweek/TODO-ElWeek.org
  [TODO] tw26W24 Week of June 8's tasks for ElectoramaWeekly

ortask  ortask/todo.org
  [TODO] t0001 Remove AI slop from docs/taskwarrior.md
  [TODO] t0002 create .org file if none exist in directory when using 'ort add'
```

Use one blank line between projects. Keep task IDs visible.

## JSON Output

`--format json` should return structured data suitable for scripts:

```json
[
  {
    "project": "elweek",
    "file": "elweek/TODO-ElWeek.org",
    "tasks": [
      {"id": "tw26W24", "state": "TODO", "title": "Week of June 8's tasks for ElectoramaWeekly"}
    ]
  }
]
```

## Safety

`scan` must be read-only. It must not run `repair`, create missing files, add
IDs, or rewrite Org content. If the parser finds duplicate IDs, report that
project as invalid and continue scanning other projects.

## Implementation Notes

The first implementation should reuse or extract the discovery functions from
`projtui.py`: `default_config_path`, `read_config_projdir`,
`resolve_projdir`, `choose_org_file`, and `discover_projects`.

Longer term, those helpers should move into a shared module so `ortask.py scan`
and `projtui.py` cannot drift apart.
