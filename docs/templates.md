# Template Application for ortask.py

This document specifies a future `ortask.py` feature for turning reusable Org
task templates into real tasks. The motivating case is
`~/tmpsorta/electorama-weekly/TODO-ElWeek.org`, which has active weekly work
under `* Tasks` and a reusable checklist under `* Template`.

## Goal

Each week, `ortask.py` should be able to copy the ElectoramaWeekly template,
replace placeholders such as `twYYWNN` and `Month Day`, and insert the result as
new TODO tasks under `* Tasks`. The command should get the file 95% ready while
preserving the plain-text links and leaving final human edits easy.

## Proposed Command

```sh
ortask.py apply weekly \
  --file ~/tmpsorta/electorama-weekly/TODO-ElWeek.org \
  --week 2026W26 \
  --date 2026-06-25 \
  --dry-run
```

`weekly` names the template profile.

To keep the interface clean and avoid redundant input, all date and week parameters are optional and automatically fall back:
- **Default (no options)**: Derives the current ISO week (e.g., `26W26`) from the current system date, and uses the current date for the template date.
- **Only `--week`**: Sets the target week (e.g., `26W26`) and derives the template date as the Monday of that ISO week.
- **Only `--date`**: Sets the template date (e.g., `2026-06-25`) and derives the week from that date (ISO week 26).
- **Both specified**: Explicitly sets both week and date.

`--dry-run` prints the Org content that would be inserted without modifying the file. A non-dry run writes atomically using the existing line-preserving write path.

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

### Multiple Templates (Future Extension)

By default, `ortask.py apply weekly` looks for a template defined either under a top-level `* Template` heading, or as a sub-heading named `** weekly` under a top-level `* Templates` (plural) heading. This allows the file to support multiple named templates in the future while keeping the single-template `* Template` format simple.

## Placeholder Replacement

Required replacements for the weekly profile:

- `twYYWNN` / `twYYYYWNN` -> the canonical week task ID, for example `tw26W26` / `tw2026W26`
- `YYWNN` / `YYYYWNN` -> the week ID without prefix, for example `26W26` / `2026W26`
- `Month Day` -> the human date for `--date`, formatted as `Month D` (e.g., `June 25`)
- `Next Month Day` -> the human date for `--date` plus 7 days, formatted as `Month D` (e.g., `July 2`)

These replacements are applied across all instantiated headings and body lines.

Optional future placeholders:

- `{{week_id}}` -> `tw26W26`
- `{{date}}` -> `June 25`
- `{{next_date}}` -> `July 2`
- `{{episode_url}}` and `{{episode_title}}` for later interactive prompting

The first version should support the existing literal placeholders before
introducing brace syntax, because the current Electorama template already uses
them.

## Insertion Rules

1. Parse the target file and find top-level `* Tasks` and `* Template`.
2. Instantiate only the direct contents of `* Template` (or the named template subheading under `* Templates`), not the container heading itself.
3. Insert the instantiated subtree at the end of the `* Tasks` subtree, before the next top-level heading.
4. Preserve heading levels from the template, so a `**` template parent remains a `**` active task.
5. Refuse to insert if any generated task ID already exists, unless a future `--replace` option is explicitly implemented.
6. Do not mark old weekly tasks `DONE`; template application only adds new work.

For the week of June 25, 2026, the parent task would become:

```org
** TODO tw26W26 Week of June 25's tasks for ElectoramaWeekly
```

## Safety

The command must be conservative:

- Default to `--dry-run` in documentation examples.
- Never edit outside the selected file.
- Never rewrite the whole Org document when line insertion is enough.
- Refuse ambiguous files with multiple matching template headings.
- Report duplicate generated IDs before writing.

## Tests

Add pytest coverage with temporary Org files:

- apply template with `--dry-run` and verify it prints the output without modifying the file.
- verify date and week default behavior when no options, only `--week`, or only `--date` are specified.
- instantiate `twYYWNN` into `tw26W26` and preserve dotted children.
- replace `Month Day` and `Next Month Day` in headings while preserving bare URL body lines.
- insert under `* Tasks` before `* Template`.
- refuse duplicate generated IDs.
- verify missing templates and missing `* Tasks` produce clear errors.
