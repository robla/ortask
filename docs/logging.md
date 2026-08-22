# Logging

**Status: design, not implemented.** Nothing in the suite writes a log today.

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

Writes, by default. One event per task affected, so a command that touches five
tasks emits five events sharing one `session`.

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

Read-only commands — `list`, `show`, `doctor`, `repair` without a fix — write
nothing.

### Levels

`cdproj` is the interesting boundary. It changes no data, so by the rule above
it does not belong in a log of writes. But "I switched to elweek at 09:14 and to
ortask at 11:40" is exactly what a journal wants, and no other record has it.

Two levels resolve this without argument:

- **`write`** (default) — the table above. What changed.
- **`activity`** — adds `cdproj` selections and project-navigator opens. What
  you did.
- **`off`** — nothing.

The level is one setting, and moving from `write` to `activity` changes the
character of the file rather than its shape: same schema, more verbs.

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

```
$XDG_STATE_HOME/ortask/log/2026-08.jsonl
```

defaulting to `~/.local/state/ortask/log/` when `$XDG_STATE_HOME` is unset.

State, not config and not data: the XDG basedir spec puts logs and other
"persists between restarts but is not important enough for data" files in the
state directory, which is exactly the disposability rule above.

One file per month, named for the month. Rotation is then a consequence of the
naming rather than a procedure — nothing renames anything, "what happened
today" reads one bounded file, and pruning history is `rm 2025-*.jsonl`.

### Why not `ortask.ini`

`docs/config.md` states one organizing rule: there is exactly one machine-global
setting, and it is the location of the registry. Adding a `[log]` section would
break it for something that has a perfectly good default.

So the location is derived, and two environment variables cover the rest:

| Variable | Effect |
|---|---|
| `ORTASK_LOG` | `off`, `write` (default), or `activity`. |
| `ORTASK_LOG_DIR` | Overrides the directory. Exists mainly so tests never touch a real log. |

If a future setting genuinely needs to be per-project, the registry index
(`projects.org`) already has a place for it.

## Writing mechanics

- Open with `O_APPEND` and write **one** `write()` per event. POSIX guarantees
  that an append-mode write shorter than `PIPE_BUF` (4096 bytes) does not
  interleave with another process's, so two `ort` invocations racing cannot
  produce a corrupt line.
- Therefore **cap the line at 4096 bytes**, truncating the longest field (the
  title, in practice) with an explicit marker rather than dropping the event.
- No atomic-replace. The temp-file-then-rename pattern the Org writers use is
  wrong here: it would silently discard a concurrent append.
- No read-modify-write. Nothing in the writer ever reads the log.
- **Failure never fails the command.** Any exception from the log path is
  swallowed; the task edit already succeeded and the log is disposable. A
  failure may print one line to stderr, once per invocation, and must not
  change the exit code.

## Where the call goes

A new `ortasklib/log.py` with a module-level `record(event)` and a settable
sink for tests.

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

Task titles land in a plaintext file under `~/.local/state`. That is the same
exposure as the Org files themselves, in a place a person is less likely to
think about. `ORTASK_LOG=off` disables the whole mechanism, and the monthly
files can be deleted at any time with no effect on the tools.

## Open questions

- Should `repair` and `doctor` log their findings? They change nothing, but "the
  registry was broken on Tuesday" is the kind of thing worth being able to look
  up.
- Should a project be able to opt out of logging, and if so, where does that
  setting live? The registry index is the obvious home, at the cost of making
  the log path read a second file.
- Is `activity` one level or two? Directory-stack changes and project-navigator
  opens are both navigation, but only the first is a deliberate act.
- Should `ort log` be able to write a `* Log` section back into a task file, for
  people who want the record in Org rather than in a state directory? That
  reverses the disposability rule and needs its own argument.
