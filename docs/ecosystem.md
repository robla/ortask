# Ortask Ecosystem

The ortask ecosystem is a set of small tools that share Org task files and,
where practical, share `ortasklib`. Emacs and Org mode are foundational as the
culture and file format, but the tools should not require Emacs. The durable
contract is plain `.org` text with recognizable task headings, stable IDs, and
surgical edits.

Castabout is a separate tool that is not yet published. Handrail is a separate,
unpublished set of compact-TUI UX guidelines that may become a library later.
They are design context, not dependencies or files supplied by this repository.

## Shared Center

`ortasklib` should be the common substrate:

- task-file discovery (`tasks.org`, `task.org`, `*.task.org`, compatibility fallbacks)
- `* Tasks` subtree parsing when present, with whole-file task-heading parsing
  as the compatibility path

Since 2026-08-21 the parsing half of that lives in `orglib`, a separate
top-level package that imports nothing outside the standard library (see
`docs/orglib.md`). For a tool like castabout that wants to read ortask task
headings but has its own opinions about files and workflow, `orglib` alone may
be the better dependency: it carries no discovery rules, no atomic-write
policy, and nothing else to inherit. Depend on `ortasklib` when the discovery
and write behavior is wanted too.
- stable task IDs and lookup normalization
- task filtering and summary views
- byte-preserving line edits for state changes, inserted notes, and new tasks
- atomic writes and clear ambiguity errors

The command-line tools can differ in interface and domain logic, but should not
fork basic Org parsing or writeback behavior.

## Current Projects

There are two commands and one library. Everything else is an alias.

`ortask.py` (`ort`) is the local task CLI, scoped to one Org file. It should
stay scriptable and conservative: list, show, add, done/open, apply templates,
and open the local interactive workspace. Its default mode should remain useful
in shell scripts and simple enough to test with temporary fixtures. `orti` is
`ortask.py -i`, the issue-editing workspace over the resolved file.

`projmgr.py` (`pmgr`) is the project layer. It owns the registry, the project
list, project registration (`add`), removal (`rm`), health (`repair`), config
(`init`), and the `cdproj` directory-stack helper. It must not become a local task
editor: it reads Org task content and never writes it. `ptui` is
`projmgr.py -i`, the project navigator. `docs/projects.md` is the model it
implements.

`ortasklib/taskui.py` is the shared task UI — the buffered task list and issue
workspace behind both `orti` and the project navigator. It was `projtui.py`, a
2103-line script imported by both commands; the name suggested a third tool and
the content was library code, so it moved into the package.

`castabout.py` is an unpublished workflow assistant for recurring
ElectoramaWeekly promotion
chores. It reads a task file, shows a status dashboard, drafts promotional
copy, opens posting destinations, and writes completion state back into Org.
It currently carries bespoke Org parsing and writeback code; the ecosystem goal
is to migrate that shared substrate to `ortasklib` while keeping castabout's
domain-specific workflow in castabout.

## Interface Pattern

The ecosystem should support three interface layers:

- **Scriptable CLI**: stable commands with plain/json/org output where useful.
- **Inline TUI**: status-first displays, numbered choices, editable prompts,
  clear proposed changes, and `Esc` as back/cancel.
- **Editor workflow**: easy opening at task lines, with Org files remaining
  readable and editable directly in Emacs or any text editor.

Castabout is the best current prototype for the richer inline TUI style:
`prompt_toolkit` for editable prefilled fields and fast `Esc` cancellation,
`rich` for tables/panels, visible URLs before confirmation, clipboard/browser
fallbacks, and explicit write confirmation for meaningful changes. Ortask
should borrow those techniques without forcing every core command to depend on
optional UI libraries.

## Data Boundaries

The `.org` file is the source of truth. Tools may infer temporary context, such
as episode metadata in castabout, but persistent task state should be expressed
as normal Org text. Avoid hidden databases, credentials, or session state unless
a later tool has a specific reason for them.

Shared conventions should remain small:

- `tasks.org` is the preferred canonical filename.
- `task.org` remains recognized for compatibility.
- `NAME.task.org` names workflow-specific task files.
- `* Tasks` is the preferred home for actionable items, but valid task headings
  outside that section remain usable when the section is absent.
- `* Template` contains reusable task templates.
- Task body lines may contain URLs and notes used by workflow tools.

## Convergence Plan

Near-term castabout integration should happen in layers:

1. Replace castabout task-file discovery with `ortasklib.core` behavior or a
   thin wrapper that prefers `castabout.task.org`.
2. Parse active-week and venue tasks using `orglib` task records where the
   current heading shape allows it — this step needs the parser only, so it can
   land without castabout taking on `ortasklib`.
3. Move reusable URL extraction and task-body insertion helpers into
   `ortasklib`.
4. Keep ElectoramaWeekly episode lookup, draft templates, and posting guidance
   in castabout.
5. Backfill tests in both repos so a shared helper change proves it did not
   break local task management or the castabout posting flow.

The goal is not a monolith. It is a family of focused tools that can cooperate
because they agree on file discovery, task identity, parsing, and safe writes.
