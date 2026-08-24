# Logging

**Status: implemented (`t0033`).** Logging is opt-in and remains disabled when
`[log] enabled` is absent from `ortask.ini`.

An event log records what the tools did: a task finished at 14:32, three tasks
added, a directory stack saved, a project registered. It answers "what did I do
today?" across every project at once, which is the question a daily journal
entry starts from. `docs/extensions.md` covers the consumers, including the zim
journal.

## What already records history, and what it misses

Three records exist before this one, and each is good at something different:

| Record | Scope | Written by | Says when? |
|---|---|---|---|
| The Org file itself | one project | you and the tools | no — `DONE` has no timestamp unless Org logging is on |
| Org `CLOSED:` / `:LOGBOOK:` | one project | Emacs, when configured | yes, for state changes |
| Git history of the task file | one project, if it is a repo | you, at commit time | at commit granularity, not edit granularity |
| `docs/llm-log.org` | this repository | the models working here | yes, by hand |

None of them answers a cross-project, time-ordered question. Even with Org
logging enabled everywhere, "what did I touch on Tuesday" means opening six
files and merging them by hand. That merge is the log's whole job.

`docs/llm-log.org` is a different thing and should not be confused with this
one: it lives in this repository, describes changes to this repository, and is
written by hand. The event log is per-user, machine-written, and describes
task activity across all projects.

## The rule that keeps this safe

**The log is derived, disposable, and never authoritative.** Delete it and
nothing that matters is lost — the Org files are unchanged and every tool still
works. Nothing may ever read the log to decide what a task's state is.

That rule is what lets the log live outside the Org files and outside the
careful-write discipline the rest of the suite follows. A record that could be
wrong without consequence can be written cheaply. A record the tools depended on
would need the same atomic-write and validation treatment as task data, and
would be a second source of truth about task state, which `docs/format.md`
exists to prevent.

## What gets logged

Writes, and deliberate project switches. One event per task affected, so a
command that touches five tasks emits five events sharing one `session`.

| Tool | Verb | Event carries |
|---|---|---|
| `ort` | `add` | new ID, title, parent |
| `ort` | `done` | ID, title, `from`/`to` state |
| `ort` | `open` | ID, title, `from`/`to` state |
| `ort` | `archive` | ID, title, archive path |
| `ort` | `apply` | each created ID, template profile, week |
| `ort` | `init` | the file created |
| `ort` (TUI) | `edit` | ID, and which fields changed |
| `pmgr` | `add` | project name, project path, task file |
| `pmgr` | `rm` | project name |
| `pmgr` | `set-dirs` | project name, the directories written |
| `pmgr` | `migrate` | how many entries moved |
| `pmgr` | `cdproj` | project name, the resolved stack — see below |

Read-only commands — `list`, `show`, `info`, and `repair --dry-run` — write nothing.

### Navigation

`cdproj` is the interesting boundary. It changes no task data, so by the rule
above it does not belong in a log of writes. But "I switched to elweek at 09:14
and to ortask at 11:40" is exactly what a journal wants, and no other record
has it.

Log it. Choosing a project and loading its directory stack is a deliberate act
that says where attention went, which is the same kind of fact as finishing a
task.

Do not log merely opening `ptui` and scrolling through the list. Browsing is
looking, not doing, and a log that records it fills up with events that mean
nothing on a journal page.

That line — deliberate act versus looking around — is the test for anything
added later.

## Format

JSON Lines: one JSON object per line, UTF-8, newline-terminated. No array
wrapper, no pretty-printing, no trailing commas — appending a line must never
require rewriting what came before.

```json
{"schema":1,"id":"9f2c1a7b4e8d0d31","ts":"2026-08-21T14:32:07-07:00","session":"4b1e...","tool":"ort","verb":"done","project":"ortask","file":"~/src/ortask/todo.org","task":{"id":"t0011","title":"Remove obsolete one-shot interactive selector code","from":"TODO","to":"DONE"}}
{"schema":1,"id":"a01d3c55e9b74210","ts":"2026-08-21T14:33:12-07:00","session":"7c92...","tool":"pmgr","verb":"set-dirs","project":"elweek","detail":{"directories":["~/tmpsorta/electorama-weekly","~/src/elusync"]}}
```

JSON rather than an Org or plain-text log, despite the wiki way, for one
reason: this file is written by machines and read by machines, and the wiki way
applies to the data a person edits. A person who wants to read it can, with
`grep`, `jq`, or `ort log`, and `ort log --format plain` exists so they do not
have to.

### Fields

| Field | Type | Notes |
|---|---|---|
| `schema` | int | `1`. See the compatibility rule below. |
| `id` | string | 16 hex characters, stable once written; lets a consumer dedupe across replays. |
| `ts` | string | ISO 8601, **local time with offset**, seconds precision. |
| `session` | string | One per command invocation. Groups the events of a multi-task command. |
| `tool` | string | `ort` or `pmgr`. |
| `verb` | string | The subcommand, or `edit` for a TUI save. |
| `project` | string or null | Registry name when the file belongs to a registered project; null otherwise. |
| `file` | string or null | The Org file acted on, written with `~` when under `$HOME`. |
| `task` | object | `{id, title, from?, to?, priority?, tags?}`. Absent for project-level events. |
| `detail` | object | Verb-specific. Absent when there is nothing to add. |

Three rules that matter more than the list:

- **Local time with an offset, not UTC.** A journal entry is a local-day
  artifact. Storing UTC would push a timezone conversion into every consumer and
  would put an 8pm Pacific event on the wrong day for anyone who got it wrong.
  The offset is in the string, so a consumer that wants UTC can still get it.
- **The title is denormalized into the event.** Titles get edited and tasks get
  archived. The log says what was true when it happened, so a journal generated
  a week later still reads correctly.
- **Consumers ignore unknown fields.** New fields may be added without touching
  `schema`; `schema` increments only when an existing field changes meaning or
  goes away.

## Where it lives

In the registry, beside `projects.org`:

```
<registry>/log/2026-08.jsonl
```

So `~/Projects/log/2026-08.jsonl` by default, and
`~/tmpsorta/proj2026/log/2026-08.jsonl` on the machine this was written on.

The registry is where the suite already keeps what it knows about projects
across all of them, which is exactly the scope of the log. Three things follow
from putting it there rather than in a hidden state directory:

- **The location needs no new setting.** It is derived from
  `manager.resolve_registry()`, which already answers `--registry`, then
  `[projects] registry`, then `~/Projects`. The opt-in is a boolean rather than
  another path to keep synchronized, and the log moves with the registry.
- **It is visible.** A file under `~/.local/state` is one nobody looks at. A
  `log/` next to `projects.org` is somewhere a person will actually notice it,
  read it, and remember it exists — which matters for a file whose whole
  purpose is being read later.
- **A registry that is backed up backs up the log too.** For a journal source
  that is a feature, and it costs nothing, because the log stays disposable:
  see the privacy note below for what it also means.

One file per month, named for the month. Rotation is then a consequence of the
naming rather than a procedure — nothing renames anything, "what happened
today" reads one bounded file, and pruning history is `rm 2025-*.jsonl`.

Putting the log somewhere else and symlinking `<registry>/log` to it works
without any support in the code, since the writer only ever opens paths
underneath it.

### `log/` is a reserved registry name

The registry's subdirectories are projects, so a new one needs a rule.
`log/` joins `projects.org` in the reserved list in `docs/config.md`.

`manager.discover_projects()` already ignores it: an entry is a project only
when it points outward with a symlink, and `log/` holds ordinary files. But
`pmgr repair` reports unrecognized subdirectories as notes — it currently says
`docs: not a project entry, ignored` for the registry this was written against
— so `repair` should recognize `log/` and say nothing about it.

If the registry directory does not exist, nothing is logged. Logging never
creates a registry, and a missing registry is not an error for a command that
was not otherwise using one.

A different `--registry` is a different log. That is the intended reading: the
log describes what happened to the projects in one registry.

## Turning it on and off

`ortask.ini` grows one section:

```ini
[projects]
registry = ~/Projects

[log]
enabled = true
```

Absent means off. A tool that starts writing a new file into someone's registry
— possibly a version-controlled one — because they upgraded is a tool that
surprised them. Opting in once is a small price, and the default can be
revisited after the feature has proved itself.

This is a deliberate exception to `docs/config.md`'s one-setting rule, and that
document should record it: the *location* of the log is still derived, so the
exception is one boolean rather than a second path to keep in sync.

Two environment variables override the file, mostly so tests and one-off runs
never touch a real log:

| Variable | Effect |
|---|---|
| `ORTASK_LOG` | `off` or `on`. Beats `ortask.ini`. |
| `ORTASK_LOG_DIR` | Writes the log here instead of `<registry>/log/`. |

### Per project, later

The eventual shape is per-project control, because "log my work projects, not
my personal ones" is the realistic want. The registry index is where it goes,
in the property drawer `docs/ptui.md` specifies for each project section:

```org
* [#A] ortask
:PROPERTIES:
:DESCRIPTION: Org-backed task and project tools
:LOG: off
:END:
```

Absent means "follow the global setting"; `off` and `on` override it. Reading
one more property from a file `cdproj` already reads costs nothing.

`ptui`'s metadata workspace (`m`, per `docs/ptui.md`) is the right place to
toggle it — it is already a bounded editor for exactly these per-project
fields, with buffered edits and one atomic bounded rewrite. A setting the user
can see and flip next to the project's description is a setting they will
actually use, unlike one that lives in a file they have to remember the name
of.

The global switch is where this starts, not where it ends.

## Writing mechanics

- Open with `O_APPEND` and issue **one** `os.write()` per event. Append mode
  makes selection of the end offset and the write one operation on supported
  local POSIX filesystems; concurrent-process regression coverage guards the
  Linux behavior this project relies on. `PIPE_BUF` applies to pipes, not
  regular files, and is not the justification.
- **Cap the line at 4096 bytes** as a conservative bound, truncating the title
  with an explicit `...[truncated]` marker rather than dropping the event.
- No atomic-replace. The temp-file-then-rename pattern the Org writers use is
  wrong here: it would silently discard a concurrent append.
- No read-modify-write. Nothing in the writer ever reads the log.
- **Failure never fails the command.** Any exception from the log path is
  swallowed; the task edit already succeeded and the log is disposable. A
  failure may print one line to stderr, once per invocation, and must not
  change the exit code.

## Where the call goes

`ortasklib/log.py` provides the module-level `record(event)`, event/time helpers,
readers and renderers, and a settable sink for tests.

It is called from the points that commit a change — the `cmd_*` functions in
`ortask.py` and `projmgr.py`, and the TUI's save paths in `taskui.py` — and not
from the edit helpers in `ortasklib/tasks.py`. Those helpers take text and
return lines; they do no I/O and know nothing about files, and keeping them that
way is what makes them easy to test. A logging side effect buried in
`change_state()` would also fire for a TUI buffer edit the user later discards.

The TUI logs on save, not on keystroke, for the same reason: a buffered edit
that is never saved did not happen.

## Reading it back

```sh
ort log [--since WHEN] [--until WHEN] [--all] [--format plain|json|org] [--limit N]
pmgr log [--since WHEN] [--until WHEN] [--project NAME] [--format ...]
```

The split follows the split the two tools already have: `ort log` defaults to
the resolved task file's project, `pmgr log` defaults to every project in the
registry. `--all` on `ort log` widens it to everything.

`WHEN` accepts `today`, `yesterday`, `week`, `NNd`/`NNh`, `YYYY-MM-DD`, or a
full ISO timestamp. Days start at local midnight, and `--day-start HH:MM` moves
that boundary for work that runs past it.

Formats:

- `plain` — one line per event, for a person at a terminal.
- `json` — the matching lines, unchanged. **This is the extension contract**:
  an extension calls `ort log --format json --since today` and never opens the
  log file itself, so the on-disk layout stays free to change.
- `org` — a date heading with one bullet per event, for pasting into an Org
  journal.

No `zim` format. Rendering for a particular destination belongs in that
destination's extension, per `docs/extensions.md`; adding output formats to core
for each consumer is how a small tool stops being one.

Empty output is exit 0. A missing log directory is empty output, not an error.

Ranges are half-open: `--since` is inclusive and `--until` is exclusive, so
`--since 2026-08-21 --until 2026-08-22` means one workday. `week` starts at the
most recent Monday workday boundary; `--day-start 04:00` moves every named date
boundary and Org output grouping to 04:00. `--limit N` selects the newest N
matches but prints them chronologically. Malformed lines are skipped because
the log is derived; they never prevent access to intact events.

`ort log` scopes by the resolved registry project. For an unregistered task
file it scopes by canonical friendly file path instead, so unrelated events
whose `project` is null are not combined. `ort log --all` requires no local task
file. `pmgr log` reads the whole selected registry unless `--project` narrows
it. JSON output writes each matching source line unchanged.

## The Emacs gap

An event log records what the *tools* did. Tasks marked `DONE` in Emacs, or by
hand in any editor, are invisible to it. For a journal built on this log, that
means a day spent working in Emacs reads as a quiet day.

Three ways to close it, in increasing order of effort:

1. **Accept it**, and treat the log as "what the CLI did". Honest, and enough to
   start.
2. **Merge Org's own timestamps at read time.** When a file has `CLOSED:` or
   `:LOGBOOK:` entries, `ort log` can synthesize events from them and merge by
   timestamp. This needs no new writing anywhere and makes Emacs work visible.
   It pairs with `t0023`, since a file that declares its keywords is also a file
   whose Org logging is set up deliberately.
3. **Reconcile against a snapshot.** Keep a hash or a task-state digest per
   file, and emit synthetic events for differences found on the next run. This
   catches everything and knows the time of *detection* rather than the time of
   the change, which is worse data for a journal.

Option 2 is the one worth building. Option 3 should stay on the shelf unless
something needs it.

## Privacy

Task titles land in a plaintext file in the registry. That is roughly the same
exposure as the Org files themselves, with one new consequence worth stating
plainly: **a registry that is a git repository will offer to commit the log.**
The registry this was written against is one.

So a registry under version control should decide deliberately. Either add one
line:

```gitignore
log/
```

or track it on purpose, the same choice `docs/config.md` describes for
`projects.org`. Tracking it is defensible — a record of what you did, synced
between machines, is a coherent thing to want — as long as it is a decision
rather than an accident of `git add -A`.

`[log] enabled = false`, or `ORTASK_LOG=off`, disables the mechanism entirely,
and the monthly files can be deleted at any time with no effect on the tools.

## Someday: entries a person writes

`ort log "spent the morning on the export path"` would put a note into the same
stream, as an event with no task and a `note` verb. It costs almost nothing —
the writer already exists, and the schema already allows an event without a
`task` — and it closes the gap where the interesting part of a day was thinking
rather than closing tasks.

It is a someday-maybe rather than part of the first iteration. Worth keeping
the schema compatible with it in the meantime, which the current shape already
is.

## Open questions

- Should `repair` and `doctor` findings be logged? Both are read-only reports
  today, so by the rule above neither writes an event. But "the registry was
  broken on Tuesday" is worth being able to look up. This is entangled with a
  naming question that deserves settling first: `ort repair` and `pmgr doctor`
  are the same kind of command — check a thing, report what is wrong — on two
  different subjects, with two unrelated names, and `repair` does not currently
  repair anything. Whether both verbs should exist, and what they should be
  called, is its own task.
- How much of a multi-task command belongs in one event? One event per task
  with a shared `session` is specified above, which makes `ort archive` of
  twenty tasks twenty lines. A journal summarizes them back into one bullet
  anyway.
