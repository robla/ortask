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

*Not yet assessed.*
