# Configuration

Everything the suite reads at startup, and where each piece of it lives.

The organizing rule: **there is exactly one machine-global setting, and it is
the location of the registry.** Everything else is a file in the tree, found by
looking where you already are. That is deliberate. A tool whose behavior depends
on hidden state is one you cannot reason about from a shell prompt, and the
registry is the smallest possible seed from which the rest can be discovered.

See `docs/projects.md` for what a registry *is*, `docs/cdproj.md` for the
directory stack, and `docs/format.md` for the Org conventions themselves.

## What is configured where

| Setting | Lives in | Read by | Shared or machine-local |
|---|---|---|---|
| Where the registry is | `~/.config/ortask/ortask.ini` | both tools | machine-local |
| Which projects exist | symlinks in registry subdirectories | `projmgr.py` | the registry's own repo, if it has one |
| Which task file a project uses | the entry's `.org` symlink, else discovery | both tools | same |
| A project's shared directory stack | `* Directories` in its task file | `projmgr.py cdproj` | the project's own repo |
| A project's private directory stack | `<entry>/directories-private.org` | `projmgr.py cdproj` | never the project's repo |
| Which file `ort` acts on | `--file`, `$ORTASK_FILE`, or an upward walk | `ortask.py` | n/a |

Read the table top to bottom and the scope narrows: one global file, then one
directory of pointers, then per-project files that sit next to the thing they
describe. Nothing in the lower rows can be set from the upper ones.

## `ortask.ini`

The only global configuration file. It honors `$XDG_CONFIG_HOME`, defaulting to
`~/.config/ortask/ortask.ini`, and it currently holds exactly one option:

```ini
[projects]
registry = ~/tmpsorta/proj2026
```

Resolution order for the registry is `--registry` on the command line, then
`[projects] registry`, then the built-in default `~/Projects`. A `~` the user
typed is stored as typed rather than expanded, so the file stays portable
between machines and readable by a human.

`projmgr.py init` writes it (`--dry-run` to preview, `--force` to overwrite an
existing value). Writing goes through the same atomic replace as Org edits, so
an interrupted write cannot leave a half-file. Hand-editing is equally
supported — it is an ini file, and nothing caches it.

There is no other section and no other option. If a future setting has a
sensible per-project answer, it belongs in the project's Org file instead;
`ortask.ini` is for what must be known *before* any project can be found.

## Environment variables

| Variable | Effect |
|---|---|
| `ORTASK_FILE` | The task file `ortask.py` acts on. Beats the upward walk; loses to `--file`. |
| `XDG_CONFIG_HOME` | Relocates `ortask.ini` (to `$XDG_CONFIG_HOME/ortask/`). |
| `ORTASK_PROJMGR` | Path to `projmgr.py` for `misc/cdproj.func.sh` and the completion script, when it is not on `PATH`. Shell-side only; no Python reads it. |
| `VISUAL`, `EDITOR` | Which editor `ortask.py open` launches, `VISUAL` first. |

Note the asymmetry: there is an `ORTASK_FILE` but no `ORTASK_REGISTRY`. The
registry is settable only by `--registry` or `ortask.ini`. Nothing depends on
that gap; it simply has not been needed, and the per-invocation override already
exists.

## Task-file resolution

`ortask.py` finds its file by walking up from the current directory. In one
directory, the first of these that exists wins:

1. `tasks.org`
2. `task.org`
3. exactly one `*.task.org`
4. `TODO.org`
5. exactly one other `TODO*.org`
6. `todo.org`

Ambiguity within a tier is an error, never an alphabetical guess. The whole
ladder is tried in the current directory before moving to its parent, so a
nested project never inherits its parent's file by accident. If the walk finds
nothing, exactly one generic `*.org` in the *original* directory is accepted as
a last resort.

`projmgr.py` uses the same ladder in two stricter forms: `discover_org_file`
probes a single directory without walking up (so `add` cannot register a
parent's file), and `preferred_task_file_in` additionally refuses the generic
`*.org` fallback, which makes it usable as the question "does this directory
look like a project root?"

## Per-project configuration

This is the literate layer, and the one still under active design.

A project's configuration is not centralized anywhere. It lives in two files
that sit next to the project itself, both plain Org, both meaningful to a reader
who has never run the tools:

```text
~/tmpsorta/proj2026/elusync/
  elusync                 -> /home/robla/src/elusync      # the pointer
  directories-private.org                                 # machine-local config
```

```org
* Directories
** ~/tmpsorta/proj2026/elusync
** ~/src/elusync
```

The same `* Directories` section may appear in the project's own task file,
where it is shared with everyone who clones the project. When both exist the
private list wins outright and sets the order, and any entry present only in the
shared list is reported as a warning rather than merged in — the full rule, with
its rationale, is in `docs/cdproj.md`.

An Org heading was chosen over an ini section for a reason worth restating: the
file a user already opens to read their tasks is the file that should carry the
project's settings. A second syntax would mean a second thing to learn and a
second thing to keep valid.

### What "private" means here

It means *not in the project's own repository*. It does not mean secret, and it
does not mean untracked.

`directories-private.org` holds absolute, machine-local paths — scratch
directories, checkout locations, a sibling repo's path. Those are noise in a
shared project repo and would conflict on every machine. Putting them in the
registry entry is the only place they can live that is neither the project's
repo nor a global config file.

Whether they are then version-controlled is the registry owner's business. A
registry that is itself a git repo may legitimately track them, as the current
one does — that is one person's private-to-the-project-but-shared-across-their-machines
data, which is a coherent thing to want. `docs/projects.md` reserves the
`*-private.org` suffix so that a registry that does *not* want them tracked can
exclude the whole class with one gitignore line.

The tools never run git and never require the registry to be a repository.

### Reserved names

- `*-private.org` — registry-entry-local data, never part of the project repo.
- `directories-private.org` — the specific instance the suite reads today.

## Open question: one config file, or many?

Recorded 2026-08-20 by robla:

> I want the projmgr.py (cdproj/ptui) project-level configuration to be
> literate. I like using symlinks as pointers to directories (because that is
> what they are), but also I think the current project configuration is going
> down the right path. It seems like having maybe one (but maybe many) .org file
> in the directory pointed to in `~/.config/ortask/ortask.ini` is the right way
> to go. Not sure which way to go, though.

The two shapes on the table:

**One file at the registry root** — a single `projects.org` listing every
project, each as a heading with its directories beneath it. Maximally literate:
the whole system is one document you can read top to bottom and write prose in.

**Many files, one per entry** — the status quo. Each registry subdirectory
carries its own config next to its own symlink.

The recommendation is to **keep many, and get the literacy a different way.**
Three reasons, in descending order of how hard they are to reverse:

1. **A central file would force `projmgr.py` to become an Org editor.** Its
   layer boundary is that it edits config and registry symlinks but never Org
   content — `ortask.py` owns that (`docs/architecture.md`). With one shared
   file, `pmgr rm` can no longer be `rm -r entry`; it has to parse a document,
   excise one subtree, and rewrite the rest without disturbing the user's prose.
   That is the single largest piece of complexity on the table, and it buys
   nothing that the many-file layout does not already have.

2. **It would create a second source of truth.** Today an entry is a project
   because it points outward, and that positive marker is the whole test —
   which is exactly what replaced an earlier blocklist of directory names. A
   central list reintroduces the question "what if the file and the symlinks
   disagree?", and every answer to it is a reconciliation rule that did not need
   to exist.

3. **Small files do not conflict.** In a registry under version control, per-entry
   files let two projects change independently; one document makes every edit a
   whole-file rewrite.

What the one-file shape is genuinely better at is *prose* — saying why a project
is registered, what state it is in, what to do next. That want is real and it is
separable from configuration. The registry already has the right home for it: a
`README.md` (or `README.org`) at the registry root that no tool reads. Keeping
it tool-invisible is the feature, because it can then be freeform.

So: pointers stay symlinks, config stays per-entry, prose goes in a root
document that is never parsed. If a genuinely registry-wide *setting* ever
appears — as opposed to prose — `ortask.ini` is where it goes, not a new Org
file, because by definition it is needed before any project is found.

This remains robla's call; nothing above has been implemented as a change.

## Known gaps

- **`*-private.org` is reserved but not enforced.** `docs/projects.md` states
  that private files are never candidates for task-file resolution, but
  `manager.choose_org_file` excludes only the exact name
  `directories-private.org`. A file named `notes-private.org` in a registry
  entry is currently selected as that project's task file. Verified 2026-08-20;
  the fix is to match the suffix rather than the literal name.
- **`CLAUDE.md` and `GEMINI.md` describe the resolution ladder wrongly.** Both
  treat `tasks.org` as a legacy fallback tried last; it is in fact the *first*
  name probed. The ladder in this document is the verified one. Left uncorrected
  here because those are the shared agent-instruction files, and the sibling
  `AGENTS.md` should be brought into line in the same pass.
- **`ortask.ini` has no per-project section**, and should not grow one: a
  setting that varies per project has a home next to the project.
