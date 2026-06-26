# Template Application for ortask.py

This document specifies the `ortask.py apply` feature for turning reusable Org
task templates into real tasks. The motivating case is
`~/tmpsorta/electorama-weekly/TODO-ElWeek.org`, which has active weekly work
under `* Tasks` and a reusable checklist under `* Template`.

**Status:** implemented. `ortask.py apply` (default `--template weekly`) is live
in `ortask.py`/`ortasklib.tasks`, with the placeholder, insertion, and safety
rules below. The duplicate-week guard, `--dry-run`, and `--week`/`--date`
resolution all match this spec.

## Goal

Each week, `ortask.py` should be able to copy the ElectoramaWeekly template,
replace placeholders such as `twYYWNN` and `Month Day`, and insert the result as
new TODO tasks under `* Tasks`. The command should get the file 95% ready while
preserving the plain-text links and leaving final human edits easy.

## Proposed Command

```sh
ortask.py apply \
  --file ~/tmpsorta/electorama-weekly/TODO-ElWeek.org \
  --week 2026W26 \
  --date 2026-06-25 \
  --dry-run
```

The `--template` option selects the template profile and defaults to `weekly`,
so `ortask.py apply` and `ortask.py apply --template weekly` are equivalent.
(`--template` is a named option rather than a bare positional, so the command
line reads clearly and leaves room for other options without an
order-dependent argument.) The first implementation only supports one template
per file — a single top-level `* Template` subtree — and only the `weekly`
profile; any other `--template` value is rejected with a clear "unknown
template profile" error. That template is treated as weekly unless the file
later gains an explicit convention saying otherwise.

To keep the interface clean and avoid redundant input, all date and week parameters are optional and automatically fall back:
- **Default (no options)**: Derives the current ISO week from the system date
  and uses that ISO week's Monday as the template date.
- **Only `--week`**: Sets the target week (e.g., `2026W26`) and derives the
  template date as the Monday of that ISO week.
- **Only `--date`**: Uses that date to derive both the ISO week and the template
  date's week-start Monday. For example, `--date 2026-06-25` generates
  `tw26W26` with `June 22` as the week label.
- **Both specified**: The date must fall within the specified ISO week. If not,
  fail with a clear error rather than generating mismatched IDs and labels.

All week/date math uses ISO calendar semantics (`date.isocalendar()`). The
two-digit year in `twYYWNN` is the **ISO year**, which can differ from the
calendar year for dates in late December or early January — derive it from
`isocalendar()`, not `date.year`. The `--week` value accepts the same forms as
week IDs elsewhere in ortask: two- or four-digit year, upper- or lowercase `W`,
with or without a leading `tw` (`26W26`, `2026W26`, `26w26`, `tw26W26` all name
the same week). Reuse the week-ID parsing that `ortask.py show` already relies
on rather than writing a second parser.

`--dry-run` prints the Org content that would be inserted without modifying the file. A non-dry run writes atomically using the existing line-preserving write path.

## Command Name (open question)

`apply` is the working verb, but it may not be the clearest choice and is worth
revisiting before the interface is considered stable. Candidates, with their
trade-offs:

- **`apply`** *(current; recommended for now)* — reads naturally as "apply a
  template" and echoes familiar tools (`git apply`, `kubectl apply`). The mild
  downside is that those tools apply *patches/diffs*, so `apply` could suggest
  diff semantics rather than instantiation.
- **`expand`** — "expand a template," a macro-expansion metaphor; concise and
  accurate, and unlikely to collide with other verbs.
- **`new`** — "make a new week of tasks"; short and friendly, but vague about
  templates and conceptually close to the existing `add`.
- **`generate`** / **`gen`** — common in scaffolding tools ("generate tasks from
  a template"); clear but a little heavyweight.
- **`rollover`** / **`roll`** — evokes the recurring weekly cadence ("roll over
  to next week"); apt for the weekly profile but obscure as a general verb.
- **`instantiate`** — the most precise term, but long; it was dropped earlier in
  favor of a shorter verb.

The verb is cheap to change later: it is a single subparser name, one dispatch
key, and the doc references. Recommendation: keep `apply` until a clearly
better verb emerges from real use.

## Template Format

Templates live under a top-level heading:

```org
* Template
** TODO twYYWNN Week of Month Day's tasks for ElectoramaWeekly
*** TODO twYYWNN.0 Promote Month Day ElectoramaWeekly episode
https://example.com/body-links-stay-as-body-lines
*** TODO twYYWNN.1 Prepare for Next Month Day ElectoramaWeekly episode
```

`ortask.py` should treat the subtree below `* Template` as source text, not as
active tasks. Template headings may use normal `TODO` keywords and placeholder
IDs. Body lines, blank lines, and bare URLs should be copied exactly except for
placeholder replacement.

Only one template is supported per file in the first version. If a file has
zero or multiple top-level `* Template` headings, `ortask.py apply` should fail
with a clear error. A future multi-template design can introduce `* Templates`
or named template subtrees after the single-template workflow is reliable.

## Placeholder Replacement

Required replacements for the weekly profile:

- `twYYWNN` / `twYYYYWNN` -> the canonical week task ID, for example `tw26W26` / `tw2026W26`
- `YYWNN` / `YYYYWNN` -> the week ID without prefix, for example `26W26` / `2026W26`
- `Month Day` -> the week-start date, formatted as `Month D` (e.g., `June 22`)
- `Next Month Day` -> the following week's start date, formatted as `Month D`
  (e.g., `June 29`)

A placeholder that does not occur in a given template is simply a no-op. Note
that the current ElectoramaWeekly template phrases its forward-looking subtree
as "Prepare for next week's ElectoramaWeekly episode" and does **not** yet use
`Next Month Day`, so that replacement only takes effect once the template is
updated to use it.

Replacements are applied as plain-text substitutions across every instantiated
heading and body line, in a single pass over the copied template text. Several
placeholders are substrings of others, so they must be applied **longest
literal first**, ensuring the longer placeholder is consumed before a shorter
one can match inside it:

- The shorter ID placeholders are substrings of the longer ones: `YYWNN`
  appears inside `twYYWNN`, `YYYYWNN`, and `twYYYYWNN`, and `YYYYWNN` appears
  inside `twYYYYWNN`. So apply `twYYYYWNN`, then `twYYWNN` and `YYYYWNN`, then
  `YYWNN` last.
- `Next Month Day` contains `Month Day`, so apply `Next Month Day` first.

Applying short placeholders first corrupts the longer ones — e.g. replacing
`YYWNN` before `twYYWNN` turns `twYYWNN` into `twYY...W..` garbage rather than
`tw26W26`. Replacement outputs (digits and month names) never contain a
placeholder pattern, so an earlier substitution cannot be re-matched by a later
one.

Optional future placeholders:

- `{{week_id}}` -> `tw26W26`
- `{{date}}` -> `June 25`
- `{{next_date}}` -> `July 2`
- `{{episode_url}}` and `{{episode_title}}` for later interactive prompting

The first version should support the existing literal placeholders before
introducing brace syntax, because the current Electorama template already uses
them.

## Insertion Rules

1. Parse the target file and find top-level `* Tasks` and exactly one top-level
   `* Template`.
2. Instantiate only the direct contents of `* Template`, not the `* Template`
   heading itself.
3. Insert the instantiated subtree at the end of the `* Tasks` subtree, before the next top-level heading.
4. Preserve heading levels from the template, so a `**` template parent remains a `**` active task.
5. Refuse to insert if any generated task ID already exists under `* Tasks`,
   comparing IDs canonically (via `core.canonical_id`) so that `tw26W26` and
   `tw2026W26` count as the same week. This makes a second `apply` for a week
   that is already present a safe, no-write error. A future `--replace` option
   may override this.
6. Do not mark old weekly tasks `DONE`; template application only adds new work.
7. If the file has a `* Template` but no `* Tasks` heading, create an empty
   `* Tasks` section at the end of the file first (as `ortask.py add` does),
   then insert into it.

For the week containing June 25, 2026, the parent task would become:

```org
** TODO tw26W26 Week of June 22's tasks for ElectoramaWeekly
```

## Safety

The command must be conservative:

- Default to `--dry-run` in documentation examples.
- Never edit outside the selected file.
- Never rewrite the whole Org document when line insertion is enough.
- Refuse files with zero or multiple top-level `* Template` headings.
- Refuse mismatched `--week` and `--date` values.
- Report duplicate generated IDs before writing.

## Exit Status

Follow the convention in `docs/ortask.md`: exit `0` on success, including
`--dry-run`; exit `1` for the refuse/error conditions above (missing or
multiple `* Template`, mismatched `--week`/`--date`, duplicate generated IDs, or
an unreadable/missing file). `apply` does not use exit code `2`.

## Implementation Notes

- The shared parser (`core.parse_org` / `core.find_tasks_range`) is scoped to
  the `* Tasks` subtree and stops at the next top-level heading, so it will not
  see `* Template`. Locate the template with a parallel line scan — from the
  `* Template` heading to the next top-level `*` heading — and copy those lines
  as raw source text rather than parsing them into `TodoItem`s. This is also why
  the placeholder IDs (`twYYWNN`) need no special handling: they are never
  parsed, so they never have to satisfy the strict week-ID regex.
- Reuse `core.find_tasks_range` to find the insertion point — its `end` index is
  the next top-level heading (here, `* Template`) — and `core.write_lines` for
  the atomic, line-preserving write. This keeps `apply` consistent with how
  `tasks.add_task` already inserts.
- Reuse `core.canonical_id` for the duplicate-ID check and the existing week-ID
  parsing for interpreting `--week`. (As implemented: `core.find_template_range`
  / `core.count_template_sections` locate and validate the template;
  `tasks.resolve_week_target` and `tasks.apply_template` do the date math and
  instantiation; `apply` is documented in `docs/ortask.md`.)

## Tests

Add pytest coverage with temporary Org files:

- apply template with `--dry-run` and verify it prints the output without modifying the file.
- verify date and week default behavior when no options, only `--week`, or only `--date` are specified.
- reject mismatched `--week` and `--date` values.
- instantiate `twYYWNN` into `tw26W26` and preserve dotted children.
- with a fixture that mixes `twYYWNN`, `twYYYYWNN`, and bare `YYWNN`/`YYYYWNN`,
  confirm longest-first ordering so no placeholder is corrupted by a shorter one.
- replace `Month Day` and (in a fixture that uses it) `Next Month Day` in
  headings while preserving bare URL body lines.
- insert under `* Tasks` before `* Template`.
- refuse duplicate generated IDs.
- verify missing, duplicate, and non-top-level templates produce clear errors.
