# Template Instantiation for ortask.py

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
ortask.py template instantiate weekly \
  --file ~/tmpsorta/electorama-weekly/TODO-ElWeek.org \
  --week 2026W26 \
  --date 2026-06-25 \
  --next-date 2026-07-02 \
  --dry-run
```

`weekly` names the template profile. The first implementation can support only
the default `* Template` subtree and only the Electorama-style weekly fields.
`--dry-run` should print the Org that would be inserted without modifying the
file. A non-dry run should write atomically using the existing line-preserving
write path.

## Template Format

Templates live under a top-level heading:

```org
* Template
** TODO twYYWNN Week of Month Day's tasks for ElectoramaWeekly
*** TODO twYYWNN.0 Promote Month Day ElectoramaWeekly episode
https://example.com/body-links-stay-as-body-lines
*** TODO twYYWNN.1 Prepare for next week's ElectoramaWeekly episode
```

`ortask.py` should treat the subtree below `* Template` as source text, not as
active tasks. Template headings may use normal `TODO` keywords and placeholder
IDs. Body lines, blank lines, and bare URLs should be copied exactly except for
placeholder replacement.

## Placeholder Replacement

Required replacements for the weekly profile:

- `twYYWNN` -> the canonical week task ID, for example `tw26W26`
- `YYWNN` / `YYYYWNN` if they appear in body text -> `26W26` / `2026W26`
- `Month Day` -> the human date for `--date`, for example `June 25`
- `next week` text may stay literal unless a more specific placeholder is used

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
2. Instantiate only the direct contents of `* Template`, not the `* Template`
   heading itself.
3. Insert the instantiated subtree at the end of the `* Tasks` subtree, before
   the next top-level heading.
4. Preserve heading levels from the template, so a `**` template parent remains
   a `**` active task.
5. Refuse to insert if any generated task ID already exists, unless a future
   `--replace` option is explicitly implemented.
6. Do not mark old weekly tasks `DONE`; template instantiation only adds new
   work.

For the week of June 25, 2026, the parent task would become:

```org
** TODO tw26W26 Week of June 25's tasks for ElectoramaWeekly
```

## Safety

The command must be conservative:

- Default to `--dry-run` in documentation examples.
- Never edit outside the selected file.
- Never rewrite the whole Org document when line insertion is enough.
- Refuse ambiguous files with multiple `* Template` headings.
- Report duplicate generated IDs before writing.

## Tests

Add pytest coverage with temporary Org files:

- instantiate `twYYWNN` into `tw26W26` and preserve dotted children
- replace `Month Day` in headings while preserving bare URL body lines
- insert under `* Tasks` before `* Template`
- refuse duplicate generated IDs
- verify `--dry-run` does not modify the file
- verify missing `* Template` and missing `* Tasks` produce clear errors
