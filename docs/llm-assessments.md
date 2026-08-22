# LLM Assessments

Peer feedback between the models working on ortask. **This is not a log** —
`docs/llm-log.org` records what changed. This file records how the project is
doing, so an incoming model can read one page and know where things stand.

Instructions:

- Edit only your own section. Do not rewrite or rebut another model's text.
- Replace your prior assessment wholesale and update its `Updated:` field with
  a full local timestamp (`YYYY-MM-DD Ddd HH:MM TZ`); keep it to roughly 1000
  characters.
- Say what is working, what is drifting, and what the next model should watch
  out for. Cite files. Prefer a specific finding over a general impression.

## ChatGPT

**Updated: 2026-08-21 Fri 18:33 PDT**

**Overall: healthy, with the registry transition now coherent.** The t0026
sequence has one source-backed read model, a validated and resumable migration,
and a strict no-fallback cutover. `cdproj` reads `projects.org`, initializes only
the selected project's bounded section for explicit editing, and refuses absent
or mixed migration state; `doctor` covers missing, stale, duplicate, malformed,
and mixed indexes. The 184-test suite exercises those boundaries without using
the configured registry.

The next architectural pressure is `t0031`: writing directory stacks must reuse
the same migration gate and source spans rather than introducing a second index
parser or broad serialization. `manager.py` now owns substantial registry-index
policy, so keep `orglib` limited to source-backed Org structure and keep path,
migration, and project-name policy in the manager layer. Also watch the stale
`repair` behavior and unused menu selectors already identified by Claude; they
are independent cleanup, not reasons to delay `set-dirs`.

## Claude

**Updated: 2026-08-21**

**Overall: healthy.** 192 tests pass in ~6s. The `orglib` split held — a
subprocess test blocks `ortasklib` at the meta-path, so the dependency cannot
quietly reverse — and `t0026` landed the registry index with source spans
rather than bare values, which is the right shape for rewriting one section of
a shared file. The gap I flagged earlier today (the migration running ahead of
its readers) is closed: `manager.directory_candidates()` now requires the
migrated index rather than falling back to per-entry files.

`t0011` removed the one-shot selectors, taking `menu.py` from 1357 to 1124
lines. Its empty-list contract moved to an `InlineMenuSession` test rather than
being dropped with the code it covered.

Two things to watch. `repair` still advertises "find and fix ID problems" and
only reports (`ortask.py:241`). And `menu.py` still holds rendering, session
lifecycle, and dashboard printing in one module — worth splitting locally
before anything is extracted into a shared library, and cheaper now that the
dead paths are gone.

## Gemini

**Updated: 2026-08-21**

**Overall: Solid trajectory and clean architecture.** The split of Org syntax into the standalone, zero-dependency `orglib/` package establishes a healthy boundary. The test suite is robust (167 passing tests), and the visual separation between `ptui` and `cdproj` resolves earlier UI ambiguity.

Key observations and recommendations:

1. **CLI Nomenclature:** The `doctor` subcommand in `projmgr` is cute but imprecise. It should be renamed or aliased to `check` (or `audit`), which cleanly describes validating registry symlinks and task file health.
2. **Directory & Project Introspection:** Adding `pmgr info` (and `ort info`) fills a crucial gap for scripting and prompt integration, providing a zero-mutation way to resolve project roots, active task files, and directory stacks.
3. **Dirstack Persistence (`t0031`):** Implementing `cdproj -s` / `pmgr set-dirs` with `$PWD` project auto-detection, `~` normalization, and subtraction prompts makes shell navigation state durable.
4. **Preservation Over Normalization:** Keep the bespoke line-patching engine as the sole runtime writer; external parsers like `orgparse` belong strictly in optional test conformance suites.
