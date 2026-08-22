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

**Updated: 2026-08-15**

**Overall: healthy.** The bounded-inline migration ChatGPT drove is real work,
not plan-shaped prose. 84 tests pass in ~2s, including PTY coverage for
long-list scrolling, live resize, termios restoration, and alternate-screen
avoidance. Roadmap status claims match the code, and the buffered-edit safety
contract (`OrgBuffer`, explicit-save-only) survived the rewrite intact.

Two things to fix before they calcify:

1. **Dead code.** `_run_selector`, `select_menu`, and `select_project_menu`
   (~230 of `ortasklib/menu.py`'s 877 lines) now have zero production callers;
   only tests reference them. Delete them and their tests together.
2. **Stale agent instructions.** `CLAUDE.md` and `AGENTS.md` both still say no
   test suite exists, while `CLAUDE.md`'s own Key files section calls
   `tests/test_ortask_suite.py` the refactor gate. These files are what every
   model reads first; wrong ones misdirect all of us.

Watch: `menu.py` holds rendering, session lifecycle, and dashboard printing in
one module where inedit splits the equivalent surface three ways. Split it
locally before extracting anything into a shared library.

## Gemini

**Updated: 2026-08-21**

**Overall: Solid trajectory and clean architecture.** The split of Org syntax into the standalone, zero-dependency `orglib/` package establishes a healthy boundary. The test suite is robust (167 passing tests), and the visual separation between `ptui` and `cdproj` resolves earlier UI ambiguity.

Key observations and recommendations:

1. **CLI Nomenclature:** The `doctor` subcommand in `projmgr` is cute but imprecise. It should be renamed or aliased to `check` (or `audit`), which cleanly describes validating registry symlinks and task file health.
2. **Directory & Project Introspection:** Adding `pmgr info` (and `ort info`) fills a crucial gap for scripting and prompt integration, providing a zero-mutation way to resolve project roots, active task files, and directory stacks.
3. **Dirstack Persistence (`t0031`):** Implementing `cdproj -s` / `pmgr set-dirs` with `$PWD` project auto-detection, `~` normalization, and subtraction prompts makes shell navigation state durable.
4. **Preservation Over Normalization:** Keep the bespoke line-patching engine as the sole runtime writer; external parsers like `orgparse` belong strictly in optional test conformance suites.
