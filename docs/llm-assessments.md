# LLM Assessments

Peer feedback between the models working on ortask. **This is not a log** —
`docs/llm-log.org` records what changed. This file records how the project is
doing, so an incoming model can read one page and know where things stand.

Instructions:

- Edit only your own section. Do not rewrite or rebut another model's text.
- Replace your prior assessment wholesale and update its date; keep it to
  roughly 1000 characters.
- Say what is working, what is drifting, and what the next model should watch
  out for. Cite files. Prefer a specific finding over a general impression.

## ChatGPT

*Not yet assessed.*

## Claude

**Updated: 2026-08-21**

**Overall: healthy, with one live gap.** 180 tests pass in ~6s. The `orglib`
split held: a subprocess test blocks `ortasklib` at the meta-path, so the
dependency cannot quietly reverse, and `t0026.1` gave the index parser source
spans rather than bare values — the right shape for rewriting one section of a
shared file.

The gap is that the registry migration has run ahead of its readers. The live
registry has `projects.org` and no `directories-private.org` files, but
`manager.directory_candidates()` still reads only the per-entry path, so every
project's private stack now resolves to nothing. Verified: `cdproj ortask`
writes one line, the project root, where the index lists three directories.
`t0026.3` is what closes it, and it should land before anything else does.

Two smaller items, both stale claims rather than bugs: `repair` advertises
"find and fix ID problems" and only reports (`ortask.py:241`), and
`menu._run_selector` / `select_menu` / `select_project_menu` still have no
production callers, as in my 2026-08-15 note.

## Gemini

**Updated: 2026-08-21**

**Overall: Solid trajectory and clean architecture.** The split of Org syntax into the standalone, zero-dependency `orglib/` package establishes a healthy boundary. The test suite is robust (167 passing tests), and the visual separation between `ptui` and `cdproj` resolves earlier UI ambiguity.

Key observations and recommendations:

1. **CLI Nomenclature:** The `doctor` subcommand in `projmgr` is cute but imprecise. It should be renamed or aliased to `check` (or `audit`), which cleanly describes validating registry symlinks and task file health.
2. **Directory & Project Introspection:** Adding `pmgr info` (and `ort info`) fills a crucial gap for scripting and prompt integration, providing a zero-mutation way to resolve project roots, active task files, and directory stacks.
3. **Dirstack Persistence (`t0031`):** Implementing `cdproj -s` / `pmgr set-dirs` with `$PWD` project auto-detection, `~` normalization, and subtraction prompts makes shell navigation state durable.
4. **Preservation Over Normalization:** Keep the bespoke line-patching engine as the sole runtime writer; external parsers like `orgparse` belong strictly in optional test conformance suites.
