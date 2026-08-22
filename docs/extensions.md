# Extensions

**Status: pull interface implemented, extensions deferred.** `ort log --format
json` now provides the stable read contract; no extension program ships yet.

An extension is a program that does something with ortask's data that ortask
itself should not do — push a summary into a wiki, drive a status bar, file a
weekly report. This document describes how such a program gets its data, where
it keeps its own state, and what it may assume. The zim journal integration is
the first one, and it has its own section below.

The companion document is `docs/logging.md`, which specifies the event log most
extensions will read.

## The rule

**ortask does not run extension code.** Extensions run ortask.

That is the starting position, and it is worth stating plainly because the
opposite is the default assumption for anything called a plugin system. A plugin
architecture means arbitrary code runs inside — or as a child of — a command
whose job is editing a text file safely. Every extension then becomes a way for
`ort done` to hang, crash, or write something unexpected.

Inverting it costs nothing for the cases that matter. A daily journal entry is
generated once a day; a weekly report, once a week. Both are naturally a
scheduled program that asks ortask what happened, which is a shape that needs no
mechanism at all beyond a stable way to ask.

## Two models

| | Pull | Push |
|---|---|---|
| Who starts it | the extension (cron, timer, or a person) | ortask, after a write |
| Data source | `ort log --format json` | one event on stdin |
| Latency | as often as it runs | immediate |
| Failure blast radius | the extension | the command that triggered it |
| Needed for a daily journal | yes | no |

**Pull is the default and the only one to build first.** Push is specified
below so that a later need — a status bar that must update the instant a task
closes — has a design to follow rather than an improvisation.

## The pull contract

An extension is any program. It gets its data from ortask's CLI:

```sh
ort log --format json --since today          # events, one JSON object per line
ort log --format json --since 2026-08-21 --until 2026-08-22
pmgr list --format json                      # projects and their open tasks
ort list --format json                       # tasks in one file
```

Three promises come with that:

- **The JSON is the interface; the files are not.** An extension that opens
  `<registry>/log/2026-08.jsonl` directly is relying on a layout that is free to
  change. One that calls `ort log --format json` is not.
- **Unknown fields are ignored, `schema` says when that stops being safe.**
  Fields get added without ceremony. `schema` increments only when an existing
  field changes meaning or disappears, which is the extension's cue to check.
- **Empty is not an error.** No events for a day exits 0 with no output. An
  extension should treat "nothing happened" as an ordinary Tuesday.

### State and idempotency

An extension keeps its own state under `$XDG_STATE_HOME/ortask/ext/<name>/`,
typically one cursor file recording the last event `id` or timestamp it acted
on. Every event carries a stable `id` precisely so a re-run, a retry, or an
overlapping time window does not produce the thing twice.

The rule for anything that writes to a shared destination — a wiki page, a
report, a file someone else edits — is: **never modify a line you did not
write.** Mark what you write, so a later run can find its own output and leave
everything else alone. The zim section shows one way to mark it.

### Configuration

Each extension owns its configuration and ortask never reads it.
`$XDG_CONFIG_HOME/ortask/ext/<name>.ini` is the suggested location, but nothing
enforces it, because nothing in ortask looks. This keeps the rule from
`docs/config.md` intact: extension settings stay out of the suite's global
configuration, which contains only the registry location and activity-log
opt-in.

**Per-project settings are different**, because there is already a good place
for them: the registry index, `<registry>/projects.org`, which holds one
heading per project and treats prose under it as free text. An extension may
claim a named subsection under a project heading:

```org
* elweek
  Weekly Electorama show prep.
** Directories
   - ~/tmpsorta/electorama-weekly
** Zim
   page: project-active:ElectoramaWeekly
```

`** Directories` is ortask's own section; `** Zim` belongs to the zim extension,
which parses it. ortask core parses neither the extension's section nor its
contents. Two conventions keep this from turning into a mess: name the section
after the extension, and keep it small enough to read at a glance.

### Discovery

Optional, and last. If it becomes tiresome to type `ortask-zim`, ortask can
adopt the git convention: an unrecognized `ort <name>` looks for `ort-<name>` on
`PATH` and execs it with the remaining arguments. That is roughly fifteen lines
in the dispatcher and it adds no plugin registry, no manifest, and no import of
anything.

Until then, extensions are ordinary programs that a person runs or schedules.
There is nothing to install and nothing to register.

## The push contract (specified, deferred)

When something genuinely needs to react to a write rather than poll for it:

- Hooks live in `$XDG_CONFIG_HOME/ortask/hooks/<verb>.d/` and are executable
  files, run in sorted filename order — the `run-parts` convention, so numeric
  prefixes control ordering.
- Each hook receives the event as one JSON line on stdin, the same object
  `ort log --format json` emits.
- Hooks run **after** the write has succeeded and been flushed. A hook cannot
  veto, alter, or roll back an edit. There is no pre-write hook, because a task
  edit that a third-party program can cancel is a task edit that cannot be
  reasoned about.
- A hook's nonzero exit is reported on stderr and does not change ortask's exit
  code. A hook that exceeds a short timeout is killed and reported.
- `ORTASK_HOOKS=off` disables all of them.

One security rule is not negotiable: **hooks are read only from the user's
config directory.** Never from a project directory, never from a registry entry,
never from anywhere reached by the upward walk that finds task files. ortask
discovers files by walking up from the current directory; if that walk could
also find executable code, then `cd` into a cloned repository followed by
`ort list` would run that repository's code.

## Writing an extension

1. Decide the trigger: scheduled, or run by hand.
2. Read `ort log --format json --since <cursor>`.
3. Do the thing. Mark whatever you write so you can recognize it later.
4. Advance the cursor only after the write succeeded.
5. Support `--dry-run` printing exactly what would be written. For anything
   that writes into a person's notes, make it the first thing that works.
6. Exit 0 when there was nothing to do.

## Zim

[Zim](https://zim-wiki.org/) is a desktop wiki that stores each page as a plain
text file in a notebook directory. That makes it a good integration target: the
destination is a text file with a documented format, and the same conservative
editing discipline the Org writers use applies directly.

**Verified on this machine, 2026-08-21:** zim 0.75.1,
`notebook_layout=files`, `default_file_extension=.txt`, `endofline=unix`. The
details below come from a real notebook rather than from the general zim
documentation, and an extension should still read them from the notebook rather
than assume them.

### Read the notebook, do not hardcode it

Each notebook has a `notebook.zim` at its root:

```ini
[Notebook]
notebook_layout=files
default_file_extension=.txt
endofline=unix

[JournalPlugin]
namespace=Journal
granularity=Week
```

`granularity` is the setting that decides where a given date's entry goes, and
it varies between notebooks on this machine — `Week` in two of them, `Month` in
another. An extension that assumes daily pages will write to the wrong file.
`~/.config/zim/notebooks.list` maps notebook names to paths and names the
default.

| Granularity | Page for 2026-08-21 | File |
|---|---|---|
| `Day` | `Journal:2026:08:21` | `Journal/2026/08/21.txt` |
| `Week` | `Journal:2026:Week 34` | `Journal/2026/Week_34.txt` |
| `Month` | `Journal:2026:08` | `Journal/2026/08.txt` |

### The page shape

A weekly journal page in this notebook looks like this:

```
Content-Type: text/x-zim-wiki
Wiki-Format: zim 0.6
Creation-Date: 2026-08-17T13:35:40-07:00

====== Week 34 2026 ======
26W34: 2026-08-17 (Monday) to 2026-08-23 (Sunday)
« [[Week 33]] – Week 34 – [[Week 35]] »
(see also: [[TODO]])

=== 2026-08-19 (26W34 Wednesday) ===
* [[person:Carah Ong Whaley]] -- interview planned for ElectoramaWeekly

=== 2026-08-17 (26W34 Monday) ===
* [[project-active:ElectoramaWeekly]] -- Working on maybe rescheduling today's recording
```

Four things an extension must respect, all of them observable in the existing
pages:

- **Day sections are newest first.** Wednesday appears above Monday. Inserting a
  new day means finding its position, not appending to the end of the file.
- **A day section may not exist yet.** Create it in the right position.
- **Entries are `* [[namespace:Page]] -- prose`.** The namespaces in use include
  `person:`, `org:`, `event:`, `project-active:`, `project-archived:`, and
  `electoral-src:`.
- **The header block is three lines and a blank.** `Creation-Date` is set once,
  when the page is created, and is never rewritten.

### The week ID already matches

The journal page says `26W34`. An ortask weekly task ID is `tw26W34`. They are
the same week in the same notation, which was not planned and is worth using:
`ort apply` instantiating `tw26W35` and zim opening `Journal:2026:Week 35` are
talking about the same seven days, so a weekly summary can be joined to a
weekly page without a date calculation.

### Mapping projects to pages

The registry has `bashfuncs2023`, `elusync`, `elweek`, `jobhunt2026`, `mwsync`,
`ortask`. The notebook has `project-active:ortask` and `project-active:mwsync`
matching exactly, `project-active:ElectoramaWeekly` for what the registry calls
`elweek`, and no page at all for `elusync` or `jobhunt2026`.

So three cases, and each needs a decided answer:

1. **A page with the project's name exists** — link it.
2. **A mapping is configured** — use it. The mapping belongs in the `** Zim`
   section of the project's heading in `projects.org`, next to the project's
   other private settings.
3. **Neither** — link `project-active:<name>` anyway. Zim renders a link to a
   nonexistent page as an invitation to create it, which is the correct
   suggestion. `link_missing = false` for anyone who would rather see plain
   text.

### What to write

One bullet per project touched, not one per event. A journal is prose the
person will edit; a machine-generated wall of task IDs is something to delete
rather than something to build on.

```
=== 2026-08-21 (26W34 Friday) ===
* [[project-active:ortask]] -- 3 tasks done, 1 added @ortask
```

With `--detail`, the individual tasks go underneath as nested bullets, which zim
writes as a tab indent:

```
* [[project-active:ortask]] -- 3 tasks done, 1 added @ortask
	* done t0011 Remove obsolete one-shot interactive selector code
	* done t0025 Enforce the *-private.org reservation in task-file discovery
	* added t0032 Write docs/logging.md and docs/extensions.md
```

The `@ortask` tag is the mark from the general rule above. Zim treats `@word` as
a tag, so the tag is searchable in the GUI and gives the extension a reliable
way to find its own lines. **The extension appends, and never edits or removes a
line that does not carry the tag.** A `--replace` mode may rewrite its own
tagged bullets for a given day; without it, the run appends only events newer
than the cursor.

### Two ways to write, for two situations

**Unattended** (a timer, once a day): write the page file directly. Insert into
the day section, leave every other byte alone, write to a temp file in the same
directory and rename — the same discipline as the Org writers. Then
`zim --index NOTEBOOK` if the index needs to notice.

**Attended** (a person running `ort zim push`): hand the text to zim instead.

```sh
printf '%s\n' "$lines" | zim --plugin quicknote \
    --notebook ~/Notebooks/ZimRegora2023 \
    --page "Journal:2026:Week 34" --append true --input stdin
```

Zim then owns the write, creates the page from its own template if it is
missing, and updates its index. The catch is that quicknote presents a dialog,
so this path wants a person present. That makes it the better choice for the
first version anyway: the dialog is a preview and a confirmation.

**A running zim is the risk in both cases.** Zim holds the open page in memory
and saves it back, so a direct file write to the page someone is currently
editing can be overwritten. The mitigations, in order of preference: prefer the
quicknote path when zim is running; write only to a day section other than the
one being edited; and treat `--dry-run` as the default until the flow is
trusted. This behavior should be checked against zim 0.75.1 before the
unattended path is enabled by default.

### Sketch of the flow

```sh
ort log --format json --since today \
  | ortask-zim --notebook ZimRegora2023 --dry-run
```

1. Read events, group by project, drop projects with nothing.
2. Resolve each project to a zim page (the three cases above).
3. Resolve the date to a page path (granularity from `notebook.zim`).
4. Find or create the day section, in the right position.
5. Append tagged bullets.
6. Advance the cursor.

## Other extensions this shape would fit

Listed to show the mechanism is not zim-specific, not because any of them is
planned:

- **Weekly report** — `ort log --since week --format json` into a Markdown or
  Org summary, keyed by the same `26W34` week ID.
- **Status line** — `pmgr list --format json` into a prompt or bar segment
  showing open counts for the current project.
- **castabout** — the ElectoramaWeekly workflow assistant already reads task
  files; consuming events instead would let it react to a week's tasks being
  applied. See `docs/ecosystem.md`.
- **Time accounting** — `activity`-level events carry the project switches that
  a timesheet wants.

Each of them is a program that reads JSON from a command. None of them needs
ortask to grow a plugin API.

## Open questions

- Does `ort <name>` → `ort-<name>` dispatch earn its fifteen lines, or is typing
  the full program name fine?
- Should the zim extension live in this repository or its own? It depends on
  zim's page conventions, not on ortask's internals, which argues for its own.
- Is one cursor per extension enough, or does an extension writing to several
  destinations need one per destination?
- Should `ort log` grow a `--follow` mode, which would let a long-running
  extension avoid polling without ortask learning to spawn anything?
