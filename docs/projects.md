# Projects and the Registry

This is the multi-project counterpart to `docs/format.md`. That document settles
how one Org task file is found and written. This one settles what a *project*
is, how the suite enumerates the set of them, and what may live alongside them.

The same rule applies here as everywhere else in ortask: plain files a human can
read, list, and repair with ordinary shell tools, with only enough convention
for tooling to help.

The project-layer command is `projmgr.py`, aliased `pmgr`. See
`docs/projmgr.md` for its command reference and `docs/ecosystem.md` for how the
tools divide.

## The registry

The registry is one real directory on disk. Each project is one immediate
subdirectory of it, holding symlinks to the real project directory and,
optionally, to that project's Org task file.

```text
~/tmpsorta/proj2026/
  elweek/
    electorama-weekly -> /home/robla/tmpsorta/electorama-weekly
    TODO-ElWeek.org   -> electorama-weekly/TODO-ElWeek.org
  ortask/
    ortask   -> /home/robla/src/ortask
    todo.org -> ortask/todo.org
```

Its location lives in `ortask.ini`, honoring `$XDG_CONFIG_HOME`:

```ini
[projects]
registry = ~/tmpsorta/proj2026
```

Resolution order is `--registry`, then `[projects] registry`, then `~/Projects`.

There is no index file, no database, and no cache. The directory *is* the
record. `ls -la` shows the entire model, `ln -s` adds to it, and `rm -r` removes
from it. The tools are a convenience layer over that, and must stay correct when
the registry is edited by hand.

### Why symlinks, and not a scan

Three models have been tried. `ortask.ini` first held a name-to-path table; a
recursive `scan` verb was specified in a since-deleted `docs/scan.md` and folded
away into `projmgr.py list`; the directory of symlinks replaced both. The
reasons the symlink registry won are worth keeping written down, because this
question has re-opened twice:

- **Membership is explicit.** A project is in the set because you put it there,
  not because a heuristic found a `.org` file somewhere under `$HOME`.
- **Enumeration needs no parsing.** Listing projects is `iterdir()`. Reading
  task files is a second, separable step that a fast project list can skip.
- **It is repairable without the tools.** A broken registry is a broken
  symlink, which every user already knows how to fix.
- **Order, naming, and grouping are filesystem operations.** No tool has to own
  a rename.
- **Version control is the user's choice.** The registry can be a git
  repository or not, and the suite never has to care.

Scanning is not planned. The friction it was meant to relieve — the cost of
registering a project — is relieved instead by making registration one word,
run from inside the project. See *Adding a project*.

## What makes a directory a project

A registry entry is a project when it **points outward**: it contains a symlink
to a directory, or failing that a symlink to an Org file. A directory symlink is
the project, and its target is the project root; a task-file symlink alone is
still a deliberate registration, and the project root is wherever that file
really lives.

This is a positive marker, and it is the whole test. It replaced a blocklist of
directory names that had accumulated `docs` because one real registry keeps its
own notes there. A registry may hold a `README.md`, an `AGENTS.md`, a `docs/`
directory, a `.git`, or anything else its owner wants; none of them become
projects, because none of them point anywhere outside the registry.

- Exactly one directory symlink per entry. Two or more is ambiguity: resolution
  stops rather than choosing alphabetically, per `docs/format.md`, and the entry
  is listed with a warning instead of a project path.
- The entry directory's name is the project name. Renaming a project is `mv`.
- A dangling symlink is a *broken* project, not a missing one, when it aimed
  where a project link aims — at a directory or an Org file. List it with a
  warning so it can be fixed; do not let it silently vanish. A stale link to
  something else (a `.md` note, say) does not make an entry a project at all.

## A task file is optional

A project with no discoverable task file is still a project, and must still
appear in the project list.

This reverses the earlier behavior, where a project without a task file was
invisible. That was wrong in both directions that matter here: a project you
just started has no tasks yet, which is exactly when you most need it in the
list; and directory-stack navigation (`docs/projdirs.md`) works on directories,
which every project has, rather than on tasks, which some do not.

Task-file resolution for a registered project:

1. a `.org` symlink or file inside the registry entry, excluding private files
   (below);
2. otherwise, `docs/format.md` discovery inside the real project directory;
3. otherwise, none.

A project record therefore carries a name, a project path, a task file *or*
nothing, and an optional warning. Every project-level display must have
something to show for each of those four, including the empty ones.

## What may live in a registry entry

Pointers — plus per-project data that must not enter the project's own
repository. Everything else belongs in the project's Org file, where the user
can see and edit it directly.

`directories-private.org` (see `docs/cdproj.md`) is the current example. It
holds absolute machine-local paths that would be noise, or leakage, in a shared
repo. It is the exception that defines the rule: it lives in the registry
because it has nowhere else to live, not because the registry is a convenient
place to put things.

Private files use a `*-private.org` suffix, so one line covers all of them,
present and future, in a registry that is under version control:

```gitignore
*-private.org
```

Private files are never candidates for task-file resolution.

Tools must never require the registry to be a git repository, and must never
run git themselves. One real registry is a git repository with its own README
and agent instructions; that is a legitimate use of the directory and none of
the suite's business.

## Adding a project

Registration is one command, run from inside the project:

```sh
cd ~/src/whatever
pmgr add
```

That creates `<registry>/whatever/` containing a symlink to the project and, if
a task file is discovered, a symlink to it.

- With no argument, `add` walks upward for the project root — the nearest
  ancestor holding a task file or a VCS directory — so running it from
  `~/src/ortask/docs` registers `~/src/ortask`. It prints what it chose.
- With an explicit path, `add` registers exactly that path and walks nothing.
- `--name` overrides the entry name, `--file` names the task file instead of
  discovering one, `--force` repoints links in an existing entry, and
  `--dry-run` prints the plan without touching the registry.
- `add` never writes Org content, and never fails for want of a task file.

Removal is `pmgr rm NAME`, which deletes the registry entry and nothing else.
Because everything in an entry is normally a symlink, that destroys nothing; a
regular file in the entry needs `--force`, and a real subdirectory is refused
outright. Deleting the entry directory by hand is equally valid.

`pmgr doctor` reports what is broken, ambiguous, unreadable, or duplicated, and
notes which subdirectories it ignored as non-projects. It changes nothing and
exits 2 when it finds a problem.

## Safety

- The registry is the only thing project-level tools write, apart from
  `ortask.ini` and files named by an explicit `--out`.
- Org task content belongs to `ortask.py`. Project-level tools read it; they do
  not edit it.
- Symlink targets are never modified. Links are created, repointed, or removed,
  and only inside the registry.
