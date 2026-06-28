# Org Format Conventions for ortask.py

This is a minimal stub for the Org format that `ortask.py` should expect and
encourage. The goal is the "wiki way": plain text that is natural for humans
to read, with only enough convention for tools to help.

## Core Convention

Prefer a dedicated task file named `tasks.org`. `task.org` remains recognized
for compatibility. For project-specific names, use `NAME.task.org`, such as
`castabout.task.org` or `elweek.task.org`.
Inside it, put actionable work under a top-level `* Tasks` heading:

```org
* Tasks
** TODO tw26W24 Week of June 8's tasks for ElectoramaWeekly
*** TODO tw26W24.0 Promote June 10 ElectoramaWeekly episode
**** TODO tw26W24.0.1 Identify the latest episode title and URL
**** TODO tw26W24.0.2 Draft short promo copy
**** TODO tw26W24.0.3 Post to reddit (/r/electorama)
https://www.reddit.com/r/electorama/submit
**** TODO tw26W24.0.4 Post to X/Twitter
https://x.com/electorama
**** TODO tw26W24.0.5 Post to Facebook
https://facebook.com/electorama
**** TODO tw26W24.0.6 Record posted links or notes
*** TODO tw26W24.1 Prepare for June 17 ElectoramaWeekly episode

* Template
** TODO twYYWNN Week of Month Day's tasks for ElectoramaWeekly
*** TODO twYYWNN.0 Promote Month Day ElectoramaWeekly episode
**** TODO twYYWNN.0.3 Post to reddit (/r/electorama)
https://reddit.com/r/electorama
https://www.reddit.com/r/electorama/submit
**** TODO twYYWNN.0.4 Post to X/Twitter
https://x.com/electorama
**** TODO twYYWNN.0.5 Post to Facebook
https://facebook.com/electorama
```

This keeps weekly recurring chores and one-time work in the same file without
requiring custom Org extensions. Each week, copy or reopen the checklist,
adjust the title/body for the latest episode, then mark steps `DONE` as they
are completed.

## Task Headings

For now, ortask-compatible tasks should use:

```org
** TODO t0001 Task title
** TODO tw26W24 Weekly task title
** TODO [#A] t0002 Priority task
** DONE t0003 Completed task
```

IDs are stable references. Use lowercase `t` plus four digits for ordinary
top-level tasks, or `tw` plus an ISO-like week for weekly parent tasks.
Accepted weekly forms are `tw26W24`, `tw26w24`, `tw2026W24`, and
`tw2026w24`; the `w` in `tw` is reserved for week-based IDs. Command input may
also omit the `tw` prefix, so `ortask.py show 26W24` finds `tw26W24`. Two- and
four-digit year forms compare as the same week for lookup, so `2026w24` also
finds `tw26W24`. Use dotted children such as `t0001.1` or `tw26W24.1` for
subtasks. Body text under a task is free-form Org text and should remain
readable even if `ortask.py` is never used.

Put task-specific URLs as plain body lines under the task they support, not as
extra `*` headings. That keeps the links visible in `ortask.py show` and in
interactive task details.

## Discovery Direction

Project tooling should prefer explicit task-file names over generic Org files.
The resolution order is:

1. An explicit CLI file option, such as `--file`.
2. `ORTASK_FILE`, for `ortask.py`.
3. Walk from the current directory upward, nearest directory first, looking for:
   `tasks.org`, then `task.org`, then exactly one `*.task.org`, then
   compatibility names.
4. Compatibility names, in order: `TODO.org`, exactly one other `TODO*.org`,
   then `todo.org`.
5. As a compatibility fallback, use exactly one generic `*.org` in the original
   current directory only.

Ambiguity should stop resolution rather than silently choosing alphabetically.
For example, two `*.task.org` files in the same directory require `--file` or a
rename to `tasks.org`. A named file such as `castabout.task.org` is still an
ortask-compatible task file and is preferred over generic files such as
`castabout.org`. This follows the useful part of `castabout.py`'s resolver:
canonical task filenames are automatic, but generic Org files are never guessed
when several plausible files exist.

This convention should be acceptable for Emacs/Org users because it does not
invent a custom file format; it only reserves a clearer filename. Upward search
also matches the common project-root pattern used by editor tooling.
