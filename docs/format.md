# Org Format Conventions for ortask.py

This is a minimal stub for the Org format that `ortask.py` should expect and
encourage. The goal is the "wiki way": plain text that is natural for humans
to read, with only enough convention for tools to help.

## Core Convention

Prefer a dedicated task file named `TODO.org` or `TODO-ProjectName.org`.
Inside it, put actionable work under a top-level `* Tasks` heading:

```org
* Tasks
** TODO tw26W24.0 Promote June 10 ElectoramaWeekly episode
*** TODO tw26W24.0.1 Identify the latest episode title and URL
*** TODO tw26W24.0.2 Draft short promo copy
*** TODO tw26W24.0.3 Post to /r/electorama
*** TODO tw26W24.0.4 Post to X/Twitter
*** TODO tw26W24.0.5 Post to Facebook
*** TODO tw26W24.0.6 Record posted links or notes
** TODO tw26W24.1 Prepare for June 17 ElectoramaWeekly episode

* Template
** reddit /r/electorama
*** https://reddit.com/r/electorama
*** https://www.reddit.com/r/electorama/submit
** Twitter/X
*** https://x.com/electorama
** Facebook
*** https://facebook.com/electorama
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

## Discovery Direction

Project tooling should search in this order:

1. `TODO*.org` files in the project directory, with `TODO.org` first.
2. `todo.org` as a compatibility fallback.
3. Larger Org files, especially `README.org`, that contain a clear task
   section such as `* Tasks` or `* TODO`.

Dedicated `TODO*.org` files are preferred because they make intent obvious.
Searching inside larger files is still useful for projects that keep chores
beside prose, but the tool should avoid guessing when multiple plausible task
sections exist.
