# orgmgr.py

`orgmgr.py` is a proposed manager for Org files across the user's filesystem.
It is distinct from `ortask.py`.

`ortask.py` is local: it reads or edits one selected Org task file. Its verbs
such as `list`, `show`, `add`, `done`, and `open` operate against a specific
file resolved by `--file`, `ORTASK_FILE`, or local file lookup.

`orgmgr.py` is global: it knows about many Org files, groups them into
projects, and can build or maintain an index of the Org files the user wants
managed. It should eventually support project-level verbs such as `projadd`
and `projrm`.

## First Verb: list

`orgmgr.py list` should list all known projects and the top-level tasks within
each project.

```sh
orgmgr.py list
orgmgr.py list --projdir ~/tmpsorta/proj2026
orgmgr.py list --all
orgmgr.py list --format plain
orgmgr.py list --format json
```

The first implementation should use the same project list as `projtui.py`.
That means `--projdir` overrides configuration, the global config can point to
`~/tmpsorta/proj2026`, and the default is `~/Projects`.

## Project Discovery

For now, reuse `projtui.py` discovery rules:

- scan immediate subdirectories of the configured project directory
- skip hidden directories and infrastructure directories such as `.git`,
  `docs`, and `__pycache__`
- choose each project's Org file using the same naming order as `projtui.py`
- do not recurse arbitrarily through project trees

Longer term, `orgmgr.py` should maintain its own project registry or database
of Org files. That database can grow beyond a single project directory, but
the first version should not invent a separate discovery model.

## Task Selection

For each discovered project, show only top-level tasks:

- parse the selected Org file with the same parser as `ortask.py`
- include only `TODO` tasks by default
- include only direct children of `* Tasks` (`level == 2`)
- omit subtasks from the main list output
- with `--all`, include top-level `DONE` tasks too

If a project has an Org file but no parseable `* Tasks` section, show the
project with a short note in plain output and include a warning field in JSON.

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

## Future Verbs

Potential future verbs:

- `projadd`: add a project or Org file to the global registry
- `projrm`: remove a project or Org file from the registry
- `scan`: refresh the registry from configured roots
- `doctor`: report missing files, duplicate IDs, parser failures, or stale
  registry entries

These verbs should manage the global Org-file inventory. They should not
replace local task editing verbs in `ortask.py`.

## Safety

`orgmgr.py list` must be read-only. It must not run `repair`, create missing
files, add IDs, or rewrite Org content. If duplicate IDs are found within a
project file, report that project as invalid and continue listing other
projects.
