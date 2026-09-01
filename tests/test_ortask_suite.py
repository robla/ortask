from __future__ import annotations

import argparse
import inspect
import io
import json
import os
import subprocess
import sys
import textwrap
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ortask
import projmgr
import orglib  # noqa: E402 — after the sys.path insert above
from ortasklib import core, manager, taskui, tasks, viewstate, viewui


def test_orglib_imports_without_ortasklib():
    """orglib must stand alone: ortasklib depends on it, not the reverse.

    Run in a subprocess with ortasklib blocked at import time, so an accidental
    ``from ortasklib import ...`` added to orglib later fails here rather than
    quietly reintroducing the cycle.
    """
    program = textwrap.dedent(
        """
        import sys

        class Block:
            def find_module(self, name, path=None):
                if name == "ortasklib" or name.startswith("ortasklib."):
                    raise ImportError("ortasklib is blocked")

        sys.meta_path.insert(0, Block())
        import orglib

        text = "* Tasks" + chr(10) + "** TODO t0001 x" + chr(10)
        assert [t.id for t in orglib.parse(text).tasks()] == ["t0001"]
        assert not any(m.startswith("ortasklib") for m in sys.modules)
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_viewstate_is_generic_state_only():
    """The view-state core must not learn about Org, tasks, or the terminal.

    Run in a subprocess with ``ortasklib.core``, ``orglib``, and
    ``prompt_toolkit`` blocked, so a convenience import added later fails here
    rather than quietly turning the shared model into a dependency hub. Task
    predicates belong beside task presentation, project axes beside project
    presentation; only the state machine and stable ordering live here.
    """
    program = textwrap.dedent(
        """
        import sys

        BLOCKED = ("ortasklib.core", "orglib", "prompt_toolkit", "rich")

        class Block:
            def find_module(self, name, path=None):
                if name in BLOCKED or any(
                    name.startswith(one + ".") for one in BLOCKED
                ):
                    raise ImportError(name + " is blocked")

        sys.meta_path.insert(0, Block())
        from ortasklib import viewstate

        axes = viewstate.ViewAxes(
            ("all", "open"),
            ("name", "age"),
            {"all": "", "open": "open only"},
            {"name": "Name", "age": "Age"},
            directions={"age": ("newest", "oldest")},
        )
        view = axes.initial()
        assert view.badge() == "Name"
        assert not view.reversible
        assert view.next_sort().with_reverse(True).badge() == "Age (oldest)"
        assert not any(
            module.startswith(one)
            for module in sys.modules
            for one in BLOCKED
        )
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_orglib_parse_agrees_with_core():
    """The bespoke backend is a pass-through, so both must see the same tasks."""
    text = textwrap.dedent(
        """
        * Tasks
        ** TODO t0001 First
        ** DONE [#A] t0002 Second          :tag:
        *** TODO t0002.1 Nested
        """
    ).lstrip()
    assert orglib.parse(text).tasks() == core.parse_org(text)


def test_project_heading_priority_cookie_does_not_change_the_name():
    # A cookie is ptui ordering metadata, not part of the registry entry name,
    # so a heading that gains one keeps resolving to the same project (t0036).
    def index(heading: str) -> str:
        return heading + "\n** Directories\n   - ~/src/ortask\n"

    for heading in (
        "* ortask",
        "* [#A] ortask",
        "* [#c] ortask",
        "* [#1] ortask",
        "* [#A] ortask   :work:tools:",
    ):
        lookup = orglib.parse(index(heading)).directories("ortask")
        assert lookup.project_found, heading
        assert lookup.section is not None
        assert lookup.section.entries == ("~/src/ortask",), heading

    # Two characters is not a cookie, so the brackets stay part of the name.
    weird = orglib.parse(index("* [#AB] ortask")).directories("ortask")
    assert not weird.project_found

    # Both forms name one project, so a file carrying both is ambiguous.
    with pytest.raises(orglib.OrgStructureError):
        orglib.parse(
            index("* ortask") + "\n" + index("* [#A] ortask")
        ).directories("ortask")


def test_project_section_reads_priority_tags_and_drawer():
    # ptui's metadata (t0035.1): the heading carries priority and tags, the
    # drawer carries description and the non-normative task-file mirror, and
    # every span points back at exactly what was read.
    text = (
        "#+TITLE: Projects\n\n"
        "* [#A] ortask   :work:tools:\n"
        ":PROPERTIES:\n"
        ":DESCRIPTION: Org-backed task and project tools\n"
        ":TASK_FILE: ~/src/ortask/todo.org\n"
        ":CUSTOM_ID: keep-me\n"
        ":END:\n"
        "Prose the parser never touches.\n"
        "** Directories\n"
        "   - ~/src/ortask\n\n"
        "* plain\n"
    )
    section = orglib.parse(text).project("ortask")
    assert section is not None
    assert section.name == "ortask"
    assert section.priority == "A"
    assert section.tags == ("work", "tools")
    assert section.description == "Org-backed task and project tools"
    assert section.task_file == "~/src/ortask/todo.org"
    # Unknown properties are kept in order so a writer can put them back.
    assert section.properties[2] == ("CUSTOM_ID", "keep-me")
    assert text[section.heading_span.start:section.heading_span.end] == (
        "* [#A] ortask   :work:tools:\n"
    )
    assert text[section.drawer_span.start:section.drawer_span.end].startswith(
        ":PROPERTIES:\n"
    )
    assert text[section.drawer_span.start:section.drawer_span.end].endswith(":END:\n")
    # The subtree stops before the next project, prose and children included.
    assert text[section.span.end:] == "* plain\n"
    assert "Prose the parser never touches." in (
        text[section.span.start:section.span.end]
    )

    # A section with no drawer is ordinary, not an error.
    plain = orglib.parse(text).project("plain")
    assert plain is not None
    assert plain.priority is None and plain.properties == ()
    assert plain.drawer_span is None
    assert orglib.parse(text).project("absent") is None


def test_project_sections_lists_every_top_level_heading():
    # A faithful listing, not a join: which headings name projects is the
    # registry's question, so reserved and repeated names come back as written.
    text = "* one\n* Tasks\n** TODO t0001 x\n* [#C] one\n"
    assert [
        (section.name, section.priority)
        for section in orglib.parse(text).projects()
    ] == [("one", None), ("Tasks", None), ("one", "C")]
    # Looking one up by name applies the duplicate check instead.
    with pytest.raises(orglib.OrgStructureError):
        orglib.parse(text).project("one")


def test_project_section_refuses_a_drawer_it_cannot_place():
    # Dropping metadata a person wrote is worse than refusing to read it.
    misplaced = (
        "* ortask\n"
        "Prose first.\n"
        ":PROPERTIES:\n"
        ":DESCRIPTION: never reached\n"
        ":END:\n"
    )
    with pytest.raises(orglib.OrgStructureError, match="must directly follow"):
        orglib.parse(misplaced).project("ortask")

    unterminated = "* ortask\n:PROPERTIES:\n:DESCRIPTION: no end\n** Directories\n"
    with pytest.raises(orglib.OrgStructureError, match="unterminated"):
        orglib.parse(unterminated).project("ortask")

    malformed = "* ortask\n:PROPERTIES:\nnot a property\n:END:\n"
    with pytest.raises(orglib.OrgStructureError, match="malformed"):
        orglib.parse(malformed).project("ortask")

    # A drawer under a child heading belongs to the child, not the project.
    nested = (
        "* ortask\n"
        "** Directories\n"
        ":PROPERTIES:\n"
        ":DESCRIPTION: the child's\n"
        ":END:\n"
    )
    section = orglib.parse(nested).project("ortask")
    assert section is not None and section.properties == ()


def test_orglib_render_returns_the_source_unchanged():
    """The fidelity property a future backend has to match, asserted now.

    A read-only document must reproduce its input byte for byte. Writing this
    down while there is only one backend is the point: it is the assertion that
    tells us whether a second one is a drop-in replacement.
    """
    text = (
        "#+TITLE: Kept As Written\n"
        "\n"
        "* Tasks\n"
        "** TODO t0001 Spacing   and   padding preserved      :tag:\n"
        "\n"
        "* Notes\n"
        "  | a | b |\n"
    )
    assert orglib.parse(text).render() == text


def test_orglib_tasks_is_scoped_like_core():
    """Scoping to ``* Tasks`` is backend behavior, not caller behavior."""
    text = textwrap.dedent(
        """
        * Tasks
        ** TODO t0001 Counted
        * Other
        ** TODO t0002 Not counted
        """
    ).lstrip()
    assert [t.id for t in orglib.parse(text).tasks()] == ["t0001"]


def test_orglib_directories_distinguishes_missing_and_empty():
    """Project, section, and entry presence remain three separate states."""
    text = (
        "* notes\n"
        "No private stack.\n"
        "* empty\n"
        "** Directories\n"
        "# Deliberately empty.\n"
    )

    missing_project = orglib.parse(text).directories("absent")
    assert not missing_project.project_found
    assert missing_project.project_span is None
    assert missing_project.section is None

    missing_section = orglib.parse(text).directories("notes")
    assert missing_section.project_found
    assert missing_section.section is None

    empty_section = orglib.parse(text).directories("empty")
    assert empty_section.project_found
    assert empty_section.section_found
    assert empty_section.section is not None
    assert empty_section.section.entries == ()


def test_orglib_directories_uses_only_a_direct_child():
    """A nested heading named Directories must not become project config."""
    nested_only = (
        "* ortask\n"
        "** Notes\n"
        "*** Directories\n"
        "**** file:/not/config\n"
    )
    assert orglib.parse(nested_only).directories("ortask").section is None

    with_direct_child = nested_only + "** Directories\n*** file:~/src/ortask\n"
    lookup = orglib.parse(with_direct_child).directories("ortask")
    assert lookup.section is not None
    assert lookup.section.entries == ("~/src/ortask",)


def test_orglib_directories_rejects_duplicate_projects():
    """Case and trailing tags cannot hide an ambiguous project heading."""
    text = "* OrTask :local:\n** Directories\n* ortask\n** Directories\n"

    with pytest.raises(orglib.OrgStructureError, match="lines 1, 3"):
        orglib.parse(text).directories("ORTASK")


def test_orglib_directories_rejects_duplicate_sections():
    """Two direct-child directory sections are never resolved first-wins."""
    text = (
        "* ortask\n"
        "** Directories\n"
        "*** file:/one\n"
        "** Notes\n"
        "** Directories\n"
        "*** file:/two\n"
    )

    with pytest.raises(orglib.OrgStructureError, match="lines 2, 5"):
        orglib.parse(text).directories("ortask")


def test_orglib_directories_retains_exact_source_spans():
    """Project and section ranges address exact slices of the original text."""
    text = (
        "#+TITLE: Projects\r\n"
        "* alpha\r\n"
        "Alpha prose.\r\n"
        "* OrTask :local:\r\n"
        "Project prose.\r\n"
        "** Directories\r\n"
        "   - ~/src/ortask\r\n"
        "\r\n"
        "** Notes\r\n"
        "Keep me.\r\n"
        "* tail\r\n"
    )
    lookup = orglib.parse(text).directories("ortask")

    assert lookup.project_span is not None
    assert text[lookup.project_span.start : lookup.project_span.end] == (
        "* OrTask :local:\r\n"
        "Project prose.\r\n"
        "** Directories\r\n"
        "   - ~/src/ortask\r\n"
        "\r\n"
        "** Notes\r\n"
        "Keep me.\r\n"
    )
    assert (lookup.project_span.start_line, lookup.project_span.end_line) == (3, 10)

    assert lookup.section is not None
    span = lookup.section.span
    assert text[span.start : span.end] == (
        "** Directories\r\n   - ~/src/ortask\r\n\r\n"
    )
    assert (span.start_line, span.end_line) == (5, 8)
    assert lookup.section.entries == ("~/src/ortask",)


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return path


def register(registry: Path, name: str, project_dir: Path) -> Path:
    """Create one registry entry of symlinks, the way ``projmgr add`` does.

    A registry entry is a project because it points outward; see
    ``docs/projects.md``. Tests that need a registry build one this way rather
    than dropping real files into the registry.
    """
    entry = registry / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / project_dir.name).symlink_to(project_dir)
    org_file = manager.choose_org_file(project_dir)
    if org_file is not None:
        (entry / org_file.name).symlink_to(org_file)
    return entry


def test_parse_standard_task_tree() -> None:
    # This test protects parsing of the canonical * Tasks subtree shape.
    text = """
    * Notes
    ** TODO t9999 Ignored because outside Tasks
    * Tasks
    ** TODO [#A] t0001 First task  :urgent:
    Body line
    https://example.invalid
    *** DONE t0001.1 Child task
    ** DONE t0002 Finished task
    * Later
    ** TODO t9998 Also ignored
    """

    items = ortask.parse_org(textwrap.dedent(text).lstrip())

    assert [item.id for item in items] == ["t0001", "t0001.1", "t0002"]
    assert items[0].level == 2
    assert items[0].state == "TODO"
    assert items[0].priority == "A"
    assert items[0].tags == "urgent"
    assert items[0].body_lines == ["Body line", "https://example.invalid"]
    assert items[0].line_num == 3


def test_parse_org_without_tasks_section() -> None:
    # t0003: files without * Tasks still expose valid TODO/DONE task headings.
    text = """
    * Overview
    ** TODO t0001 First task
    Body line
    *** DONE t0001.1 Child task
    * Notes
    This prose should not become task body.
    ** TODO Missing ID is ignored
    * TODO t0002 Top-level fallback task  :tag:
    """

    items = ortask.parse_org(textwrap.dedent(text).lstrip())

    assert [item.id for item in items] == ["t0001", "t0001.1", "t0002"]
    assert [item.level for item in items] == [2, 3, 1]
    assert items[0].body_lines == ["Body line"]
    assert items[1].body_lines == []
    assert items[2].tags == "tag"


def test_normalize_and_match_weekly_ids() -> None:
    # This test keeps all accepted week-ID spellings equivalent for lookup.
    items = [
        ortask.TodoItem(level=2, state="TODO", id="tw26W24", text="Weekly task"),
    ]

    aliases = ["26W24", "26w24", "tw26W24", "tw2026W24", "tw2026w24"]

    assert {ortask.canonical_id(ortask.normalize_id(alias)) for alias in aliases} == {"tw26w24"}
    for alias in aliases:
        assert ortask.find_by_id(items, ortask.normalize_id(alias)) is items[0]
    assert items[0].id == "tw26W24"


def test_filter_root_todo_tasks() -> None:
    # This test captures the top-level task view used by projmgr.py list.
    items = ortask.parse_org(
        textwrap.dedent(
            """
            * Tasks
            ** TODO t0001 Open parent
            *** TODO t0001.1 Open child
            ** DONE t0002 Done parent
            *** DONE t0002.1 Done child
            ** TODO t0003 Another parent
            """
        ).lstrip()
    )

    todo_roots = ortask.filter_items(items, state="todo", root_only=True)
    all_roots = ortask.filter_items(items, state="all", root_only=True)

    assert [item.id for item in todo_roots] == ["t0001", "t0003"]
    assert [item.id for item in all_roots] == ["t0001", "t0002", "t0003"]


def test_filter_root_tasks_without_tasks_section() -> None:
    # Root-only filtering uses the minimum parsed level when there is no * Tasks.
    items = ortask.parse_org(
        textwrap.dedent(
            """
            * TODO t0001 Root task
            ** TODO t0001.1 Child task
            * DONE t0002 Done root
            """
        ).lstrip()
    )

    assert [item.id for item in ortask.filter_items(items, state="todo", root_only=True)] == ["t0001"]
    assert [item.id for item in ortask.filter_items(items, state="all", root_only=True)] == ["t0001", "t0002"]


def test_discover_local_org_file_order(tmp_path: Path, monkeypatch) -> None:
    # This test fixes the task-file discovery order and parent walk behavior.
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "tasks.org", "* Tasks\n** TODO t0000 tasks canonical\n")
    write(tmp_path / "project.task.org", "* Tasks\n** TODO t0001 named task\n")
    write(tmp_path / "task.org", "* Tasks\n** TODO t0002 canonical task\n")
    write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 lowercase\n")
    write(tmp_path / "TODO-Project.org", "* Tasks\n** TODO t0002 project\n")
    write(tmp_path / "TODO.org", "* Tasks\n** TODO t0003 canonical\n")
    write(tmp_path / "README.org", "* Tasks\n** TODO t0006 readme\n")

    assert ortask.resolve_org_file() == Path("tasks.org")

    (tmp_path / "tasks.org").unlink()
    assert ortask.resolve_org_file() == Path("task.org")
    (tmp_path / "task.org").unlink()
    assert ortask.resolve_org_file() == Path("project.task.org")

    (tmp_path / "project.task.org").unlink()
    assert ortask.resolve_org_file() == Path("TODO.org")

    (tmp_path / "TODO.org").unlink()
    assert ortask.resolve_org_file() == Path("TODO-Project.org")
    (tmp_path / "TODO-Project.org").unlink()
    assert ortask.resolve_org_file() == Path("todo.org")
    (tmp_path / "todo.org").unlink()
    assert ortask.resolve_org_file() == Path("README.org")

    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    write(tmp_path / "tasks.org", "* Tasks\n** TODO t0007 parent task\n")
    monkeypatch.chdir(nested)
    assert ortask.resolve_org_file() == Path("../../tasks.org")


def test_discover_local_org_file_ambiguity(tmp_path: Path, monkeypatch) -> None:
    # This test ensures ambiguous task-file tiers stop instead of guessing.
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "alpha.task.org", "* Tasks\n** TODO t0001 alpha\n")
    write(tmp_path / "beta.task.org", "* Tasks\n** TODO t0002 beta\n")

    with pytest.raises(ortask.OrgFileDiscoveryError):
        ortask.resolve_org_file()

    (tmp_path / "alpha.task.org").unlink()
    (tmp_path / "beta.task.org").unlink()
    write(tmp_path / "README.org", "* Tasks\n** TODO t0003 readme\n")
    write(tmp_path / "NOTES.org", "* Tasks\n** TODO t0004 notes\n")

    with pytest.raises(ortask.OrgFileDiscoveryError):
        ortask.resolve_org_file()


def test_add_top_level_and_subtask(tmp_path: Path) -> None:
    # This test protects ID allocation and insertion points for local adds.
    org_file = write(
        tmp_path / "todo.org",
        """
        * Intro
        Keep me.
        * Tasks
        ** TODO t0001 Parent
        Parent body
        *** TODO t0001.1 Existing child
        Child body
        ** TODO t0002 Later parent
        * Notes
        Keep this too.
        """,
    )

    assert ortask.cmd_add(argparse.Namespace(file=org_file, title="New parent", parent=None)) == 0
    assert ortask.cmd_add(argparse.Namespace(file=org_file, title="New child", parent="t0001")) == 0

    lines = org_file.read_text(encoding="utf-8").splitlines()
    assert "** TODO t0003 New parent" in lines
    assert "*** TODO t0001.2 New child" in lines
    assert lines.index("*** TODO t0001.2 New child") == lines.index("Child body") + 1
    assert lines.index("** TODO t0003 New parent") < lines.index("* Notes")
    assert "Keep me." in lines
    assert "Keep this too." in lines


def test_toggle_task_state_in_place(tmp_path: Path) -> None:
    # This test ensures done/open edits only the selected heading line.
    org_file = write(
        tmp_path / "todo.org",
        """
        * Tasks
        ** TODO t0001 Target  :tag:
        Body line

        ** TODO t0002 Neighbor
        """,
    )

    original = org_file.read_text(encoding="utf-8").splitlines()
    assert ortask.cmd_done(argparse.Namespace(file=org_file, id="t0001")) == 0
    done_lines = org_file.read_text(encoding="utf-8").splitlines()
    assert done_lines[0] == original[0]
    assert done_lines[1] == "** DONE t0001 Target  :tag:"
    assert done_lines[2:] == original[2:]

    assert ortask.cmd_open(argparse.Namespace(file=org_file, id="t0001")) == 0
    assert org_file.read_text(encoding="utf-8").splitlines() == original


def test_moot_and_superseded_are_terminal_parseable_states() -> None:
    # MOOT is preferred, while SUPERSEDED remains a completed-state alias.
    text = textwrap.dedent(
        """
        * Tasks
        ** MOOT tw26W27 Old week
        ** SUPERSEDED tw26W28 Older spelling
        ** DONE t0001 Done
        ** TODO t0002 Open
        """
    ).lstrip()

    items = core.parse_org(text)

    assert [item.state for item in items] == ["MOOT", "SUPERSEDED", "DONE", "TODO"]
    assert [item.id for item in core.filter_items(items, state="todo")] == ["t0002"]
    assert [item.id for item in core.filter_items(items, state="done")] == [
        "tw26W27", "tw26W28", "t0001"
    ]


def test_ensure_terminal_keyword_and_mark_subtree_moot() -> None:
    # Shared workflow helpers should write MOOT while preserving completed children.
    text = textwrap.dedent(
        """
        Intro
        * Tasks
        ** TODO tw26W27 Old week
        Parent note
        *** DONE tw26W27.0 Keep done
        Done note
        *** TODO tw26W27.1 Drop old work
        URL stays
        ** TODO tw26W29 Current week
        """
    ).lstrip()

    with_keyword = tasks.ensure_terminal_keyword(text)
    changed, count = tasks.change_subtree_state(
        with_keyword,
        "tw26W27",
        note="Superseded by castabout.",
    )

    assert count == 2
    assert changed.startswith("Intro\n#+TODO: TODO | DONE MOOT\n* Tasks\n")
    assert "** MOOT tw26W27 Old week\nParent note\nSuperseded by castabout.\n" in changed
    assert "*** DONE tw26W27.0 Keep done\nDone note\n" in changed
    assert "*** MOOT tw26W27.1 Drop old work\nURL stays\n" in changed
    assert "** TODO tw26W29 Current week" in changed
    assert tasks.ensure_terminal_keyword(changed) == changed


def test_ensure_terminal_keyword_merges_or_rejects_declarations() -> None:
    # Declaration updates should prefer MOOT and safely coexist with the old alias.
    merged = tasks.ensure_terminal_keyword(
        "#+TODO: NEXT TODO | DONE CANCELED\n* Tasks\n"
    )
    assert merged.startswith("#+TODO: NEXT TODO | DONE CANCELED MOOT\n")

    legacy = tasks.ensure_terminal_keyword(
        "#+TODO: TODO | DONE SUPERSEDED\n* Tasks\n"
    )
    assert legacy.startswith("#+TODO: TODO | DONE SUPERSEDED MOOT\n")
    assert tasks.ensure_terminal_keyword(
        "* Tasks\n", state="SUPERSEDED"
    ).startswith("#+TODO: TODO | DONE SUPERSEDED\n")

    with pytest.raises(tasks.TodoStateError):
        tasks.ensure_terminal_keyword(
            "#+TODO: TODO | DONE\n#+TODO: NEXT | DONE\n* Tasks\n"
        )


def test_menu_counts_moot_and_superseded_as_done() -> None:
    # Dashboard totals should use the shared terminal-state vocabulary.
    rows = [
        menu.MenuRow(1, "TODO", "Open"),
        menu.MenuRow(2, "DONE", "Done"),
        menu.MenuRow(3, "MOOT", "Moot"),
        menu.MenuRow(4, "SUPERSEDED", "Compatibility alias"),
    ]

    assert menu.count_statuses(rows) == (1, 3, 4)


def test_atomic_write_follows_symlink(tmp_path: Path) -> None:
    target = write(tmp_path / "real" / "todo.org", "old\n")
    link = tmp_path / "castabout.task.org"
    link.symlink_to(target)

    core.atomic_write(link, "new\n")

    assert link.is_symlink()
    assert link.resolve() == target.resolve()
    assert target.read_text(encoding="utf-8") == "new\n"


def test_archive_done_subtrees_with_org_context(tmp_path: Path) -> None:
    # A default sweep moves each outermost DONE subtree and records Org context.
    source = textwrap.dedent(
        """
        #+TODO: TODO WAIT | DONE MOOT
        #+CATEGORY: sample-project
        #+FILETAGS: :team:
        * Tasks
        ** TODO t0001 Open parent  :parent:
        *** DONE t0001.1 Finished child
        Child body.
        ** DONE t0002 Finished parent
        Parent body.
        *** DONE t0002.1 Finished descendant
        *** TODO t0002.2 Open descendant carried with its parent
        ** MOOT t0003 Terminal but not DONE
        """
    ).lstrip()

    result = tasks.archive_tasks(
        source,
        "",
        source_file=str(tmp_path / "tasks.org"),
        archived_at=datetime(2026, 8, 17, 3, 5),
    )

    assert result.task_ids == ("t0001.1", "t0002")
    assert "** TODO t0001 Open parent" in result.source_text
    assert "t0001.1" not in result.source_text
    assert "t0002" not in result.source_text
    assert "** MOOT t0003 Terminal but not DONE" in result.source_text
    assert result.archive_text.startswith(
        "# -*- mode: org -*-\n#+TODO: TODO WAIT | DONE MOOT\n"
    )
    assert "* DONE t0001.1 Finished child" in result.archive_text
    assert "* DONE t0002 Finished parent" in result.archive_text
    assert "** DONE t0002.1 Finished descendant" in result.archive_text
    assert "** TODO t0002.2 Open descendant carried with its parent" in result.archive_text
    assert result.archive_text.count("* DONE t0002.1") == 1
    assert ":ARCHIVE_TIME: [2026-08-17 Mon 03:05]" in result.archive_text
    assert ":ARCHIVE_OLPATH: Tasks/t0001 Open parent" in result.archive_text
    assert ":ARCHIVE_OLPATH: Tasks\n" in result.archive_text
    assert ":ARCHIVE_CATEGORY: sample-project" in result.archive_text
    assert ":ARCHIVE_ITAGS: team parent" in result.archive_text


def test_archive_explicit_task_and_merge_todo_declaration(tmp_path: Path) -> None:
    # An explicit ID archives an open subtree and unions workflow declarations.
    source = textwrap.dedent(
        """
        #+TODO: TODO NEXT | DONE MOOT
        * Tasks
        ** NEXT Project heading
        *** TODO t0001 Selected open task
        :PROPERTIES:
        :OWNER: robla
        :END:
        Body.
        **** DONE t0001.1 Child
        ** DONE t0002 Unselected task
        """
    ).lstrip()
    archive = textwrap.dedent(
        """
        # -*- mode: org -*-
        #+TODO: NEXT | DONE CANCELED

        Archived entries from an older workflow

        * CANCELED old001 Old entry
        """
    ).lstrip()

    result = tasks.archive_tasks(
        source,
        archive,
        source_file=str(tmp_path / "todo.org"),
        task_id="1",
        archived_at=datetime(2026, 8, 17, 4, 0),
    )

    assert result.task_ids == ("t0001",)
    assert "t0001" not in result.source_text
    assert "** DONE t0002 Unselected task" in result.source_text
    assert "#+TODO: NEXT TODO | DONE CANCELED MOOT" in result.archive_text
    assert result.archive_text.count("#+TODO:") == 1
    assert "* CANCELED old001 Old entry" in result.archive_text
    assert "* TODO t0001 Selected open task" in result.archive_text
    assert ":OWNER: robla" in result.archive_text
    assert result.archive_text.count(":PROPERTIES:") == 1
    assert ":ARCHIVE_TODO: TODO" in result.archive_text
    assert ":ARCHIVE_OLPATH: Tasks/Project heading" in result.archive_text


def test_archive_recovers_custom_states_without_existing_declaration() -> None:
    # A stock archive without #+TODO keeps historical custom states parseable.
    source = "* Tasks\n** DONE t0001 New completed task\n"
    archive = (
        "# -*- mode: org -*-\n\n"
        "Archived entries from an older workflow\n\n"
        "* CANCELED t0099 Historical task\n"
    )

    result = tasks.archive_tasks(
        source,
        archive,
        source_file="/tmp/tasks.org",
        archived_at=datetime(2026, 8, 17, 4, 5),
    )

    assert "#+TODO: TODO | DONE CANCELED" in result.archive_text
    assert "* CANCELED t0099 Historical task" in result.archive_text


def test_archive_no_done_tasks_is_an_exact_noop() -> None:
    # A bare archive with no DONE headings does not create or rewrite anything.
    source = "* Tasks\n** TODO t0001 Still open\n** MOOT t0002 Not a DONE task\n"

    result = tasks.archive_tasks(
        source,
        "",
        source_file="/tmp/tasks.org",
        archived_at=datetime(2026, 8, 17, 4, 10),
    )

    assert result.task_ids == ()
    assert result.source_text == source
    assert result.archive_text == ""


def test_archive_rejects_unknown_explicit_id() -> None:
    # Explicit archive requests fail rather than silently falling back to a sweep.
    source = "* Tasks\n** DONE t0001 Existing task\n"

    with pytest.raises(tasks.TaskNotFound, match="t9999"):
        tasks.archive_tasks(
            source,
            "",
            source_file="/tmp/tasks.org",
            task_id="9999",
        )


def test_add_reserves_archived_subtask_ids() -> None:
    # Archived child suffixes remain unavailable beneath a live parent.
    source = "* Tasks\n** TODO t0001 Parent\n*** TODO t0001.1 Live child\n"

    lines, task_id = tasks.add_task(
        source,
        "New child",
        "t0001",
        reserved_ids={"t0001.4"},
    )

    assert task_id == "t0001.5"
    assert "*** TODO t0001.5 New child" in lines

    weekly_lines, weekly_id = tasks.add_task(
        "* Tasks\n** TODO tw26W34 Weekly parent\n",
        "New weekly child",
        "tw26W34",
        reserved_ids={"tw2026w34.3"},
    )
    assert weekly_id == "tw26W34.4"
    assert "*** TODO tw26W34.4 New weekly child" in weekly_lines


def test_atomic_write_pair_rolls_back_first_file(
    tmp_path: Path, monkeypatch
) -> None:
    # A failed destructive second write restores the archive destination.
    archive = write(tmp_path / "tasks.org_archive", "old archive\n")
    source = write(tmp_path / "tasks.org", "old source\n")
    real_atomic_write = core.atomic_write

    def fail_source(path: Path, content: str) -> None:
        if path == source:
            raise OSError("simulated source failure")
        real_atomic_write(path, content)

    monkeypatch.setattr(core, "atomic_write", fail_source)

    with pytest.raises(OSError, match="simulated source failure"):
        core.atomic_write_pair(
            archive,
            "new archive\n",
            source,
            "new source\n",
        )

    assert archive.read_text(encoding="utf-8") == "old archive\n"
    assert source.read_text(encoding="utf-8") == "old source\n"


def test_atomic_write_pair_removes_new_file_during_rollback(
    tmp_path: Path, monkeypatch
) -> None:
    # A failed source replacement also removes a newly created archive.
    archive = tmp_path / "tasks.org_archive"
    source = write(tmp_path / "tasks.org", "old source\n")
    real_atomic_write = core.atomic_write

    def fail_source(path: Path, content: str) -> None:
        if path == source:
            raise OSError("simulated source failure")
        real_atomic_write(path, content)

    monkeypatch.setattr(core, "atomic_write", fail_source)

    with pytest.raises(OSError, match="simulated source failure"):
        core.atomic_write_pair(
            archive,
            "new archive\n",
            source,
            "new source\n",
        )

    assert not archive.exists()
    assert source.read_text(encoding="utf-8") == "old source\n"


def test_archive_cli_uses_canonical_path_and_reserves_ids(tmp_path: Path) -> None:
    # CLI archiving follows a source symlink and add never reuses archived IDs.
    project = tmp_path / "project"
    source = write(
        project / "tasks.org",
        (
            "#+TODO: TODO | DONE CANCELED\n"
            "* Tasks\n"
            "** DONE t0007 Completed\n"
            "** DONE t0008 Retained\n"
        ),
    )
    registry = tmp_path / "registry"
    registry.mkdir()
    source_link = registry / "tasks.org"
    source_link.symlink_to(source)

    archived = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ortask.py"),
            "--file",
            str(source_link),
            "archive",
            "t0007",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert archived.returncode == 0
    assert "archived 1 subtree (t0007)" in archived.stdout
    assert archived.stderr == ""
    assert source_link.is_symlink()
    archive = project / "tasks.org_archive"
    assert archive.exists()
    assert not (registry / "tasks.org_archive").exists()
    assert "** DONE t0008 Retained" in source.read_text(encoding="utf-8")
    archive.write_text(
        archive.read_text(encoding="utf-8")
        + "\n* CANCELED t0009 Archived under an older keyword\n",
        encoding="utf-8",
    )

    added = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ortask.py"),
            "--file",
            str(source_link),
            "add",
            "New task",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert added.returncode == 0
    assert "added t0010" in added.stdout
    assert "** TODO t0010 New task" in source.read_text(encoding="utf-8")


def test_list_and_done_without_tasks_section(tmp_path: Path, capsys) -> None:
    # t0003: list/done operate on valid task headings even without * Tasks.
    org_file = write(
        tmp_path / "notes.org",
        """
        * TODO t0001 Root task
        Body
        ** TODO t0001.1 Child task
        * Notes
        Keep me.
        """,
    )

    list_args = argparse.Namespace(
        file=org_file,
        state="all",
        root_only=False,
        items=None,
        format="plain",
    )
    assert ortask.cmd_list(list_args) == 0
    captured = capsys.readouterr()
    assert "[TODO] t0001 Root task" in captured.out
    assert "  [TODO] t0001.1 Child task" in captured.out

    assert ortask.cmd_done(argparse.Namespace(file=org_file, id="t0001")) == 0
    lines = org_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "* DONE t0001 Root task"
    assert lines[3] == "* Notes"

    assert ortask.cmd_add(
        argparse.Namespace(file=org_file, title="New child", parent="t0001")
    ) == 0
    lines = org_file.read_text(encoding="utf-8").splitlines()
    assert "** TODO t0001.2 New child" in lines
    assert lines.index("** TODO t0001.2 New child") < lines.index("* Notes")


def test_detect_repair_problems() -> None:
    # This test locks down current repair diagnostics before auto-fix work.
    text = """
    * Tasks
    ** TODO t0001 Parent
    *** TODO t9999.1 Wrong child prefix
    ** TODO Missing ID
    ** DONE t0001 Duplicate
    """

    problems = ortask._find_repair_problems(textwrap.dedent(text).lstrip())
    descriptions = [problem[1] for problem in problems]

    assert any("doesn't match parent t0001" in desc for desc in descriptions)
    assert any("heading has task keyword but no valid task ID" in desc for desc in descriptions)
    assert any("duplicate ID t0001" in desc for desc in descriptions)


def test_manager_choose_org_file_prefers_tasks_org(tmp_path: Path) -> None:
    # Global project tools should use the same dedicated task-file priority.
    project = tmp_path / "project"
    write(project / "README.org", "* Tasks\n** TODO t0004 generic\n")
    write(project / "castabout.task.org", "* Tasks\n** TODO t0003 named\n")
    write(project / "task.org", "* Tasks\n** TODO t0002 singular\n")
    write(project / "tasks.org", "* Tasks\n** TODO t0001 plural\n")

    assert manager.choose_org_file(project) == project / "tasks.org"
    (project / "tasks.org").unlink()
    assert manager.choose_org_file(project) == project / "task.org"
    (project / "task.org").unlink()
    assert manager.choose_org_file(project) == project / "castabout.task.org"
    (project / "castabout.task.org").unlink()
    assert manager.choose_org_file(project) == project / "README.org"


def test_summarize_projects_for_projmgr(tmp_path: Path, capsys) -> None:
    # This test covers global project summaries without touching real config.
    workspace = tmp_path / "workspace"
    real_project = tmp_path / "real-alpha"
    real_alpha_org = write(
        real_project / "TODO.org",
        """
        * Tasks
        ** TODO t0001 Alpha parent
        *** TODO t0001.1 Alpha child
        ** DONE t0002 Alpha done
        """,
    )
    (workspace / "alpha").mkdir(parents=True)
    (workspace / "alpha" / "TODO.org").symlink_to(real_alpha_org)
    write(
        tmp_path / "real-beta" / "README.org",
        """
        * Notes
        No task section.
        """,
    )
    register(workspace, "beta", tmp_path / "real-beta")
    write(
        tmp_path / "real-gamma" / "README.org",
        """
        * TODO t0003 Gamma root
        ** TODO t0003.1 Gamma child
        * Notes
        No dedicated task section.
        """,
    )
    register(workspace, "gamma", tmp_path / "real-gamma")
    # Neither of these is a project: the hidden entry is skipped by name, and
    # the registry's own notes directory points nowhere outside the registry.
    write(workspace / ".hidden" / "TODO.org", "* Tasks\n** TODO t0003 Hidden\n")
    write(workspace / "docs" / "TODO.org", "* Tasks\n** TODO t0004 Docs\n")

    args = argparse.Namespace(registry=str(workspace), all=False, format="json")
    assert projmgr.cmd_list(args) == 0
    projects = json.loads(capsys.readouterr().out)

    by_name = {project["project"]: project for project in projects}
    assert set(by_name) == {"alpha", "beta", "gamma"}
    assert by_name["alpha"]["file"] == str(real_alpha_org.resolve())
    assert by_name["alpha"]["tasks"] == [
        {"id": "t0001", "state": "TODO", "title": "Alpha parent"},
    ]
    assert by_name["beta"]["warning"] == "no parseable tasks found"
    assert by_name["gamma"]["tasks"] == [
        {"id": "t0003", "state": "TODO", "title": "Gamma root"},
    ]


# --- Tests beyond the original nine in docs/testing.md -----------------------
# These cover the output-format and show-expansion behavior that moves into
# ortasklib/tasks.py during the architecture refactor.


def test_list_json_nesting_and_org_roundtrip() -> None:
    # This test protects the JSON/org formatters: nested subtasks and a
    # parse -> format_org -> parse round-trip that preserves every field.
    text = textwrap.dedent(
        """
        * Tasks
        ** TODO [#A] t0001 Parent  :work:
        *** DONE t0001.1 Child one
        *** TODO t0001.2 Child two
        ** TODO t0002 Lonely parent
        """
    ).lstrip()
    items = ortask.parse_org(text)

    payload = json.loads(ortask.format_json(items))
    assert [node["id"] for node in payload] == ["t0001", "t0002"]
    assert [child["id"] for child in payload[0]["subtasks"]] == ["t0001.1", "t0001.2"]
    assert payload[0]["subtasks"][0]["state"] == "DONE"
    assert "subtasks" not in payload[1]  # a childless root omits the key

    fields = lambda i: (i.level, i.state, i.id, i.text, i.priority, i.tags)
    reparsed = ortask.parse_org("* Tasks\n" + ortask.format_org(items) + "\n")
    assert [fields(i) for i in reparsed] == [fields(i) for i in items]


def test_show_expands_descendants_and_resolves_shorthand(tmp_path: Path, capsys) -> None:
    # This test pins show: shorthand/weekly ID lookup, body + descendant
    # printing, and exclusion of unrelated tasks' subtrees.
    org_file = write(
        tmp_path / "todo.org",
        """
        * Tasks
        ** TODO tw26W24 Weekly parent
        Promo link
        *** TODO tw26W24.1 Subtask
        **** TODO tw26W24.1.1 Deep subtask
        ** TODO t0002 Unrelated
        *** TODO t0002.1 Unrelated child
        """,
    )

    assert ortask.cmd_show(argparse.Namespace(file=org_file, id="26W24")) == 0
    out = capsys.readouterr().out
    assert "** TODO tw26W24 Weekly parent" in out
    assert "Promo link" in out
    assert "*** TODO tw26W24.1 Subtask" in out
    assert "**** TODO tw26W24.1.1 Deep subtask" in out
    assert "t0002" not in out  # a sibling task and its child are not shown

    # A missing ID is a clean exit-1 error rather than a crash.
    assert ortask.cmd_show(argparse.Namespace(file=org_file, id="t0404")) == 1


def test_interactive_uses_resolved_local_org_file(tmp_path: Path, monkeypatch) -> None:
    # This test keeps ortask.py -i scoped to the current directory's task file.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 Local task\n")
    called: list[tuple[Path, str | None]] = []

    monkeypatch.setattr(
        taskui,
        "local_file_menu",
        lambda path, *, filter_mode=None: called.append((path, filter_mode)) or 0,
    )

    # Bare -i takes the same starting visibility as `list`: open work only.
    assert ortask.cmd_interactive(argparse.Namespace(file=org_file)) == 0
    assert called == [(org_file, "todo")]

    # A state chosen on the command line still wins over that default.
    called.clear()
    assert ortask.cmd_interactive(
        argparse.Namespace(file=org_file, state="all")
    ) == 0
    assert called == [(org_file, "all")]


def test_cli_smoke_tests(tmp_path: Path) -> None:
    # This test proves top-level scripts still import and run on temp fixtures.
    org_file = write(
        tmp_path / "example.org",
        """
        * Tasks
        ** TODO t0001 Smoke parent
        Body
        *** TODO t0001.1 Smoke child
        ** DONE t0002 Finished parent
        """,
    )
    workspace = tmp_path / "workspace"
    write(tmp_path / "real-sample" / "TODO.org", org_file.read_text(encoding="utf-8"))
    register(workspace, "sample", tmp_path / "real-sample")

    list_result = subprocess.run(
        [sys.executable, str(ROOT / "ortask.py"), "--file", str(org_file), "list"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert list_result.returncode == 0
    assert "[TODO] t0001 Smoke parent" in list_result.stdout

    show_result = subprocess.run(
        [sys.executable, str(ROOT / "ortask.py"), "--file", str(org_file), "show", "t0001"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert show_result.returncode == 0
    assert "*** TODO t0001.1 Smoke child" in show_result.stdout

    interactive_result = subprocess.run(
        [sys.executable, str(ROOT / "ortask.py"), "-i", "--file", str(org_file)],
        cwd=ROOT,
        input="q\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert interactive_result.returncode == 0
    assert f"ortask — reading {org_file}" in interactive_result.stdout
    assert "Open: 2" in interactive_result.stdout
    # -i starts on open work, so the DONE task is filtered out of the list.
    assert "Done: 0" in interactive_result.stdout
    assert "Finished parent" not in interactive_result.stdout

    all_states = subprocess.run(
        [
            sys.executable, str(ROOT / "ortask.py"), "-i",
            "--file", str(org_file), "list", "--all",
        ],
        cwd=ROOT,
        input="q\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert all_states.returncode == 0
    assert "Done: 1" in all_states.stdout
    assert "Finished parent" in all_states.stdout

    projmgr_result = subprocess.run(
        [sys.executable, str(ROOT / "projmgr.py"), "--registry", str(workspace), "list"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert projmgr_result.returncode == 0
    assert projmgr_result.stdout.splitlines()[0] == f"Registry: {workspace}"
    assert "sample" in projmgr_result.stdout

    projmgr_json_result = subprocess.run(
        [sys.executable, str(ROOT / "projmgr.py"), "--registry", str(workspace), "list", "--format", "json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert projmgr_json_result.returncode == 0
    assert json.loads(projmgr_json_result.stdout)[0]["project"] == "sample"

    navigator_result = subprocess.run(
        [sys.executable, str(ROOT / "projmgr.py"), "--registry", str(workspace), "-i"],
        cwd=ROOT,
        input="q\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert navigator_result.returncode == 0
    assert "Projects in" in navigator_result.stdout
    assert "sample" in navigator_result.stdout

    projmgr_interactive_result = subprocess.run(
        [sys.executable, str(ROOT / "projmgr.py"), "--registry", str(workspace), "-i"],
        cwd=ROOT,
        input="q\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert projmgr_interactive_result.returncode == 0
    assert f"Finding project in {workspace}" in projmgr_interactive_result.stdout
    assert "Projects in" in projmgr_interactive_result.stdout
    assert "sample" in projmgr_interactive_result.stdout


def test_projmgr_no_args_and_help_show_help() -> None:
    # This test ensures projmgr.py is explicit: bare invocation shows help rather
    # than listing the configured real registry.
    for args in ([], ["help"]):
        result = subprocess.run(
            [sys.executable, str(ROOT / "projmgr.py"), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
        assert "usage:" in result.stdout
        assert "list" in result.stdout
        assert result.stderr == ""


def test_cli_subcommands_are_registered_alphabetically() -> None:
    # Argparse preserves registration order in usage and command help.
    expected = {
        "ort": [
            "add",
            "apply",
            "archive",
            "done",
            "help",
            "info",
            "init",
            "list",
            "log",
            "open",
            "repair",
            "show",
        ],
        "pmgr": [
            "add",
            "cdproj",
            "doctor",
            "help",
            "info",
            "init",
            "list",
            "log",
            "migrate",
            "projadd",
            "repair",
            "rm",
            "set-dirs",
        ],
    }
    parsers = {
        "ort": ortask.build_parser(),
        "pmgr": projmgr.build_parser(),
    }

    for command, parser in parsers.items():
        subparsers = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        registered = list(subparsers.choices)
        assert registered == expected[command]
        assert registered == sorted(registered)


def test_projmgr_interactive_uses_registry_project_menu(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # pmgr -i (ptui) is the public registry-scoped entry point for the navigator.
    calls: list[tuple[Path, str]] = []
    # Task views reached from here start where orti starts them, on open work.
    parameters = inspect.signature(projmgr.project_menu).parameters
    assert parameters["include_done"].default is False

    monkeypatch.setattr(
        manager,
        "resolve_registry",
        lambda registry: (tmp_path, "~/Projects"),
    )
    monkeypatch.setattr(
        projmgr,
        "project_menu",
        lambda workspace, display: calls.append((workspace, display)) or 0,
    )

    # --todo-only is inert: the navigator no longer passes a visibility flag.
    assert projmgr.cmd_interactive(
        argparse.Namespace(registry=None, todo_only=True)
    ) == 0

    captured = capsys.readouterr()
    assert captured.out == "Finding project in ~/Projects\n"
    assert captured.err == ""
    assert calls == [(tmp_path, "~/Projects")]


def test_bash_completion_for_ortask_and_alias() -> None:
    # This test verifies bash completion for scripts and common aliases without
    # depending on an interactive shell.
    script = ROOT / "misc" / "ortask-completion.bash"
    cases = [
        ("_ortask_complete", "COMP_WORDS=(ortask.py ad); COMP_CWORD=1", "add"),
        ("_ortask_complete", "COMP_WORDS=(ort ad); COMP_CWORD=1", "add"),
        ("_ortask_complete", "COMP_WORDS=(ort --in); COMP_CWORD=1", "--interactive"),
        ("_ortask_complete", "COMP_WORDS=(ortask.py app); COMP_CWORD=1", "apply"),
        ("_ortask_complete", "COMP_WORDS=(ort ar); COMP_CWORD=1", "archive"),
        ("_ortask_complete", "COMP_WORDS=(ort inf); COMP_CWORD=1", "info"),
        ("_ortask_complete", "COMP_WORDS=(ort ini); COMP_CWORD=1", "init"),
        ("_ortask_complete", "COMP_WORDS=(ort lo); COMP_CWORD=1", "log"),
        ("_ortask_complete", "COMP_WORDS=(ort log --si); COMP_CWORD=2", "--since"),
        ("_ortask_complete", "COMP_WORDS=(ortask.py list --fo); COMP_CWORD=2", "--format"),
        ("_ortask_complete", "COMP_WORDS=(ortask.py apply --te); COMP_CWORD=2", "--template"),
        ("_ortask_complete", "COMP_WORDS=(ortask.py apply --template w); COMP_CWORD=3", "weekly"),
        ("_projmgr_complete", "COMP_WORDS=(pmgr --in); COMP_CWORD=1", "--interactive"),
        ("_projmgr_complete", "COMP_WORDS=(pmgr li); COMP_CWORD=1", "list"),
        ("_projmgr_complete", "COMP_WORDS=(pmgr lo); COMP_CWORD=1", "log"),
        ("_projmgr_complete", "COMP_WORDS=(pmgr log --pr); COMP_CWORD=2", "--project"),
        ("_projmgr_complete", "COMP_WORDS=(pmgr log --format o); COMP_CWORD=3", "org"),
        ("_projmgr_complete", "COMP_WORDS=(projmgr.py list --fo); COMP_CWORD=2", "--format"),
        ("_projmgr_complete", "COMP_WORDS=(pmgr migrate --dr); COMP_CWORD=2", "--dry-run"),
        ("_projmgr_complete", "COMP_WORDS=(pmgr set-dirs --mi); COMP_CWORD=2", "--missing"),
        ("_projmgr_complete", "COMP_WORDS=(pmgr set-dirs --missing r); COMP_CWORD=3", "remove"),
        ("_cdproj_complete", "COMP_WORDS=(cdproj -s); COMP_CWORD=1", "-s"),
        ("_cdproj_complete", "COMP_WORDS=(cdproj --sa); COMP_CWORD=1", "--save"),
    ]

    for function, setup, expected in cases:
        result = subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-c",
                f"source {script}; {setup}; {function}; printf '%s\\n' \"${{COMPREPLY[@]}}\"",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stderr == ""
        assert result.stdout.strip() == expected


def test_cdproj_save_forwards_the_live_stack_without_changing_it(tmp_path: Path) -> None:
    # Save must pass each live stack entry as argv and leave this shell alone.
    source = ROOT / "misc" / "cdproj.func.sh"
    first = tmp_path / "first directory"
    second = tmp_path / "second directory"
    first.mkdir()
    second.mkdir()
    capture = tmp_path / "argv"
    capture_without_project = tmp_path / "argv-without-project"
    program = r'''
source "$CDPROJ_SOURCE"
fake_projmgr() { printf '%s\n' "$@" > "$CAPTURE"; }
ORTASK_PROJMGR=fake_projmgr
cd "$FIRST"
pushd "$SECOND" >/dev/null
before_pwd=$PWD
before_stack=$(dirs -l -p)

cdproj -s elweek || exit
[[ "$PWD" == "$before_pwd" ]] || exit 20
[[ "$(dirs -l -p)" == "$before_stack" ]] || exit 21

CAPTURE="$CAPTURE_WITHOUT_PROJECT"
cdproj --save || exit
[[ "$PWD" == "$before_pwd" ]] || exit 22
[[ "$(dirs -l -p)" == "$before_stack" ]] || exit 23
'''

    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", program],
        cwd=ROOT,
        env={
            **os.environ,
            "CDPROJ_SOURCE": str(source),
            "FIRST": str(first),
            "SECOND": str(second),
            "CAPTURE": str(capture),
            "CAPTURE_WITHOUT_PROJECT": str(capture_without_project),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "set-dirs",
        "--project",
        "elweek",
        str(second),
        str(first),
    ]
    assert capture_without_project.read_text(encoding="utf-8").splitlines() == [
        "set-dirs",
        str(second),
        str(first),
    ]


def test_cdproj_save_rejects_more_than_one_project(tmp_path: Path) -> None:
    # A malformed save invocation must stop before calling the Python helper.
    source = ROOT / "misc" / "cdproj.func.sh"
    capture = tmp_path / "called"
    program = r'''
source "$CDPROJ_SOURCE"
fake_projmgr() { touch "$CAPTURE"; }
ORTASK_PROJMGR=fake_projmgr
cdproj -s one two
status=$?
[[ $status == 2 ]] || exit 20
[[ ! -e "$CAPTURE" ]] || exit 21
'''

    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", program],
        cwd=ROOT,
        env={
            **os.environ,
            "CDPROJ_SOURCE": str(source),
            "CAPTURE": str(capture),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == "usage: cdproj -s [PROJECT]\n"


def test_bash_completion_lists_subcommands_alphabetically() -> None:
    # Empty-prefix completion presents each command inventory in policy order.
    script = ROOT / "misc" / "ortask-completion.bash"
    cases = [
        (
            "_ortask_complete",
            "ort",
            [
                "add",
                "apply",
                "archive",
                "done",
                "help",
                "info",
                "init",
                "list",
                "log",
                "open",
                "repair",
                "show",
            ],
        ),
        (
            "_projmgr_complete",
            "pmgr",
            [
                "add",
                "cdproj",
                "doctor",
                "help",
                "info",
                "init",
                "list",
                "log",
                "migrate",
                "projadd",
                "repair",
                "rm",
                "set-dirs",
            ],
        ),
    ]

    for function, executable, expected in cases:
        result = subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-c",
                (
                    f"source {script}; COMP_WORDS=({executable} ''); "
                    f"COMP_CWORD=1; {function}; "
                    "printf '%s\\n' \"${COMPREPLY[@]}\""
                ),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stderr == ""
        assert result.stdout.splitlines()[:len(expected)] == expected


def test_add_rejects_existing_org_file_without_tasks_section(
    tmp_path: Path, capsys
) -> None:
    # ort add must not silently append * Tasks to arbitrary existing Org prose.
    org_file = write(
        tmp_path / "README.org",
        """
        * Intro
        Keep me.
        """,
    )
    before = org_file.read_text(encoding="utf-8")

    assert ortask.cmd_add(argparse.Namespace(file=org_file, title="First task", parent=None)) == 1

    captured = capsys.readouterr()
    assert "no '* Tasks' section found" in captured.err
    assert org_file.read_text(encoding="utf-8") == before


def test_add_bootstraps_empty_dedicated_task_file(tmp_path: Path) -> None:
    # Empty dedicated task files may be initialized with * Tasks and the new item.
    org_file = write(tmp_path / "todo.org", "")

    assert ortask.cmd_add(argparse.Namespace(file=org_file, title="First task", parent=None)) == 0

    lines = org_file.read_text(encoding="utf-8").splitlines()
    assert lines == ["* Tasks", "** TODO t0001 First task"]


def test_cli_add_creates_tasks_org_when_no_task_file_exists(tmp_path: Path) -> None:
    # t0002: real CLI add bootstraps tasks.org when discovery finds nothing.
    result = subprocess.run(
        [sys.executable, str(ROOT / "ortask.py"), "add", "First task"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert "added t0001" in result.stdout
    assert (tmp_path / "tasks.org").read_text(encoding="utf-8").splitlines() == [
        "* Tasks",
        "** TODO t0001 First task",
    ]


def test_cli_init_creates_local_tasks_org_without_parent_discovery(
    tmp_path: Path,
) -> None:
    # init establishes a new local task boundary even when an ancestor has one.
    parent_file = write(tmp_path / "tasks.org", "* Tasks\n** TODO t0001 Parent\n")
    child = tmp_path / "bashfuncs2023"
    child.mkdir()
    env = {key: value for key, value in os.environ.items() if key != "ORTASK_FILE"}

    result = subprocess.run(
        [sys.executable, str(ROOT / "ortask.py"), "init"],
        cwd=child,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == "initialized tasks.org\n"
    assert result.stderr == ""
    assert (child / "tasks.org").read_text(encoding="utf-8") == "* Tasks\n"
    assert parent_file.read_text(encoding="utf-8") == (
        "* Tasks\n** TODO t0001 Parent\n"
    )


def test_cli_init_honors_explicit_and_environment_task_files(tmp_path: Path) -> None:
    # Explicit and environment overrides may select another dedicated filename.
    explicit = tmp_path / "bashfuncs.task.org"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ortask.py"),
            "--file",
            str(explicit),
            "init",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert explicit.read_text(encoding="utf-8") == "* Tasks\n"

    configured = tmp_path / "configured.task.org"
    result = subprocess.run(
        [sys.executable, str(ROOT / "ortask.py"), "init"],
        cwd=tmp_path,
        env={**os.environ, "ORTASK_FILE": str(configured)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert configured.read_text(encoding="utf-8") == "* Tasks\n"


def test_cli_init_only_replaces_empty_dedicated_files(tmp_path: Path) -> None:
    # init may fill an empty task file but must preserve nonempty or generic files.
    empty = write(tmp_path / "task.org", "  \n")
    assert ortask.cmd_init(argparse.Namespace(file=empty)) == 0
    assert empty.read_text(encoding="utf-8") == "* Tasks\n"

    existing = write(tmp_path / "tasks.org", "* Notes\nKeep me.\n")
    assert ortask.cmd_init(argparse.Namespace(file=existing)) == 1
    assert existing.read_text(encoding="utf-8") == "* Notes\nKeep me.\n"

    generic = tmp_path / "notes.org"
    assert ortask.cmd_init(argparse.Namespace(file=generic)) == 1
    assert not generic.exists()


# --- projmgr registry model: the project marker, init, add, rm, doctor -------
# These isolate config by pointing XDG_CONFIG_HOME at a temp directory, so they
# never read or write (or delete) the real ~/.config/ortask.


def _add_args(path: Path | None, **kw) -> argparse.Namespace:
    base = dict(path=None if path is None else str(path), name=None, file=None,
                registry=None, force=False, dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_init_records_registry(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # init records [projects] registry without consulting projtui.ini.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    assert projmgr.cmd_init(
        argparse.Namespace(registry=str(workspace), force=False, dry_run=False)
    ) == 0
    capsys.readouterr()

    assert manager.read_ortask_registry() == str(workspace.resolve())
    resolved, _ = manager.resolve_registry()              # now follows ortask.ini
    assert resolved == workspace.resolve()


def _migration_project(registry: Path, tmp_path: Path, name: str) -> Path:
    project = tmp_path / "src" / name
    write(project / "tasks.org", "* Tasks\n** TODO t0001 Keep working\n")
    return register(registry, name, project)


def _migrate_args(registry: Path, *, dry_run: bool = False) -> argparse.Namespace:
    return argparse.Namespace(registry=str(registry), dry_run=dry_run)


def test_projmgr_migrate_dry_run_changes_nothing(tmp_path: Path, capsys) -> None:
    # Dry-run prints the complete proposed index and removal list without writes.
    registry = tmp_path / "projects"
    entry = _migration_project(registry, tmp_path, "ortask")
    source = (
        "# Keep this comment.\n"
        "* Directories\n"
        "** file:~/src/ortask\n"
        "* Notes\n"
        "Keep this prose.\n"
    )
    legacy = entry / manager.DIRECTORIES_PRIVATE_NAME
    legacy.write_text(source, encoding="utf-8")

    assert projmgr.cmd_migrate(_migrate_args(registry, dry_run=True)) == 0

    out = capsys.readouterr().out
    assert "[dry-run] proposed" in out
    assert "* ortask\n# Keep this comment.\n** Directories\n" in out
    assert "*** file:~/src/ortask\n** Notes\nKeep this prose.\n" in out
    assert str(legacy) in out
    assert legacy.read_text(encoding="utf-8") == source
    assert not (registry / manager.PROJECTS_INDEX_NAME).exists()


def test_projmgr_migrate_preserves_content_and_removes_sources(
    tmp_path: Path, capsys
) -> None:
    # Migration wraps files in sorted project headings and only demotes headings.
    registry = tmp_path / "projects"
    zeta_entry = _migration_project(registry, tmp_path, "zeta")
    alpha_entry = _migration_project(registry, tmp_path, "Alpha")
    zeta_source = "* Directories\n** file:/zeta\n"
    alpha_source = (
        "Alpha prose.\n"
        "* Directories\n"
        "   - ~/alpha\n"
        "* Notes\n"
        "  Formatting stays."
    )
    zeta_legacy = zeta_entry / manager.DIRECTORIES_PRIVATE_NAME
    alpha_legacy = alpha_entry / manager.DIRECTORIES_PRIVATE_NAME
    zeta_legacy.write_text(zeta_source, encoding="utf-8")
    alpha_legacy.write_text(alpha_source, encoding="utf-8")

    assert projmgr.cmd_migrate(_migrate_args(registry)) == 0

    index = registry / manager.PROJECTS_INDEX_NAME
    assert index.read_text(encoding="utf-8") == (
        "#+TITLE: Projects\n"
        "\n"
        "* Alpha\n"
        "Alpha prose.\n"
        "** Directories\n"
        "   - ~/alpha\n"
        "** Notes\n"
        "  Formatting stays.\n"
        "\n"
        "* zeta\n"
        "** Directories\n"
        "*** file:/zeta\n"
        "\n"
    )
    assert not alpha_legacy.exists()
    assert not zeta_legacy.exists()
    document = orglib.parse(index.read_text(encoding="utf-8"))
    alpha_section = document.directories("alpha").section
    zeta_section = document.directories("zeta").section
    assert alpha_section is not None and alpha_section.entries == ("~/alpha",)
    assert zeta_section is not None and zeta_section.entries == ("/zeta",)
    assert "migration complete" in capsys.readouterr().out


def test_projmgr_migrate_validates_every_source_before_writing(
    tmp_path: Path, capsys
) -> None:
    # Multiple malformed legacy files are all reported and none is modified.
    registry = tmp_path / "projects"
    keyword_entry = _migration_project(registry, tmp_path, "keyword")
    duplicate_entry = _migration_project(registry, tmp_path, "duplicate")
    keyword = keyword_entry / manager.DIRECTORIES_PRIVATE_NAME
    duplicate = duplicate_entry / manager.DIRECTORIES_PRIVATE_NAME
    keyword_text = "#+TITLE: Unsafe here\n* Directories\n"
    duplicate_text = "* Directories\n* Directories\n"
    keyword.write_text(keyword_text, encoding="utf-8")
    duplicate.write_text(duplicate_text, encoding="utf-8")

    assert projmgr.cmd_migrate(_migrate_args(registry)) == 1

    err = capsys.readouterr().err
    assert str(keyword) in err and "Org keywords" in err
    assert str(duplicate) in err and "found 2" in err
    assert keyword.read_text(encoding="utf-8") == keyword_text
    assert duplicate.read_text(encoding="utf-8") == duplicate_text
    assert not (registry / manager.PROJECTS_INDEX_NAME).exists()


def test_projmgr_migrate_creates_marker_and_is_idempotent(
    tmp_path: Path, capsys
) -> None:
    # A registry with no legacy files still gets a marker; rerunning is a no-op.
    registry = tmp_path / "projects"
    _migration_project(registry, tmp_path, "bare")
    args = _migrate_args(registry)

    assert projmgr.cmd_migrate(args) == 0
    index = registry / manager.PROJECTS_INDEX_NAME
    assert index.read_text(encoding="utf-8") == manager.PROJECTS_INDEX_HEADER
    capsys.readouterr()

    assert projmgr.cmd_migrate(args) == 0
    assert "registry already migrated" in capsys.readouterr().out
    assert index.read_text(encoding="utf-8") == manager.PROJECTS_INDEX_HEADER


def test_projmgr_migrate_cli_dispatches_to_migration(tmp_path: Path) -> None:
    # The public verb creates the index rather than retaining its old init alias.
    registry = tmp_path / "projects"
    registry.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "projmgr.py"),
            "migrate",
            "--registry",
            str(registry),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (registry / manager.PROJECTS_INDEX_NAME).read_text(encoding="utf-8") == (
        manager.PROJECTS_INDEX_HEADER
    )
    assert "migration complete" in result.stdout


def test_projmgr_migrate_resumes_interrupted_cleanup(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # A cleanup failure leaves a complete index that a later run can verify.
    registry = tmp_path / "projects"
    alpha_entry = _migration_project(registry, tmp_path, "alpha")
    beta_entry = _migration_project(registry, tmp_path, "beta")
    alpha = alpha_entry / manager.DIRECTORIES_PRIVATE_NAME
    beta = beta_entry / manager.DIRECTORIES_PRIVATE_NAME
    alpha.write_text("* Directories\n** file:/alpha\n", encoding="utf-8")
    beta.write_text("* Directories\n** file:/beta\n", encoding="utf-8")

    real_unlink = Path.unlink
    failed = False

    def fail_beta_once(path: Path, *args, **kwargs) -> None:
        nonlocal failed
        if path == beta and not failed:
            failed = True
            raise OSError("simulated cleanup failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_beta_once)
    assert projmgr.cmd_migrate(_migrate_args(registry)) == 1
    assert not alpha.exists()
    assert beta.exists()
    index = registry / manager.PROJECTS_INDEX_NAME
    original_index = index.read_text(encoding="utf-8")
    assert "cleanup incomplete" in capsys.readouterr().err

    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert projmgr.cmd_migrate(_migrate_args(registry)) == 0
    assert not beta.exists()
    assert index.read_text(encoding="utf-8") == original_index
    assert "resumed migration" in capsys.readouterr().out


def test_projmgr_migrate_refuses_conflicting_existing_index(
    tmp_path: Path, capsys
) -> None:
    # A leftover is removed only when its generated project subtree matches.
    registry = tmp_path / "projects"
    entry = _migration_project(registry, tmp_path, "ortask")
    legacy = entry / manager.DIRECTORIES_PRIVATE_NAME
    legacy.write_text("* Directories\n** file:~/src/ortask\n", encoding="utf-8")
    plan = manager.plan_registry_migration(registry)
    conflicting = plan.index_text.replace("~/src/ortask", "~/src/changed")
    core.atomic_write(plan.index_path, conflicting)

    assert projmgr.cmd_migrate(_migrate_args(registry)) == 1

    assert "does not exactly match" in capsys.readouterr().err
    assert legacy.exists()
    assert plan.index_path.read_text(encoding="utf-8") == conflicting


def test_registry_migration_rechecks_preimages_before_writing(tmp_path: Path) -> None:
    # Inputs changed after planning abort before projects.org can be created.
    registry = tmp_path / "projects"
    entry = _migration_project(registry, tmp_path, "ortask")
    legacy = entry / manager.DIRECTORIES_PRIVATE_NAME
    legacy.write_text("* Directories\n** file:/one\n", encoding="utf-8")
    plan = manager.plan_registry_migration(registry)
    legacy.write_text("* Directories\n** file:/two\n", encoding="utf-8")

    with pytest.raises(manager.RegistryMigrationError, match="changed after"):
        manager.apply_registry_migration(plan)
    assert not plan.index_path.exists()
    assert legacy.read_text(encoding="utf-8") == "* Directories\n** file:/two\n"


def test_projadd_creates_symlink_subdir(tmp_path: Path, monkeypatch, capsys) -> None:
    # projadd creates a per-project symlink subdir under the registry.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "proj2026"
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))

    project = tmp_path / "src" / "elweek"
    write(project / "TODO.org", "* Tasks\n** TODO t0001 Promote episode\n")

    assert projmgr.cmd_add(_add_args(project)) == 0
    capsys.readouterr()

    subdir = registry / "elweek"
    project_link = subdir / "elweek"
    task_link = subdir / "TODO.org"
    assert subdir.is_dir()
    assert project_link.is_symlink() and project_link.resolve() == project.resolve()
    assert task_link.is_symlink()
    assert task_link.resolve() == (project / "TODO.org").resolve()

    # Discovery follows the symlinks: list shows the project and its task.
    assert projmgr.cmd_list(
        argparse.Namespace(registry=str(registry), all=False, format="json")
    ) == 0
    projects = json.loads(capsys.readouterr().out)
    assert projects[0]["project"] == "elweek"
    assert projects[0]["file"] == str((project / "TODO.org").resolve())
    assert projects[0]["tasks"] == [
        {"id": "t0001", "state": "TODO", "title": "Promote episode"},
    ]

    # An existing project subdir is a conflict without --force; --force repoints.
    assert projmgr.cmd_add(_add_args(project)) == 1
    assert "already exists" in capsys.readouterr().err
    assert projmgr.cmd_add(_add_args(project, force=True)) == 0


def test_projadd_links_project_only_when_no_task_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # A project with no discoverable .org is still added, with just a project link
    # (the same state as a hand-created registry entry).
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))

    project = tmp_path / "bare"
    project.mkdir()  # no .org inside

    assert projmgr.cmd_add(_add_args(project, name="bare")) == 0
    capsys.readouterr()

    subdir = registry / "bare"
    assert (subdir / "bare").is_symlink()                          # project link
    assert not any(p.suffix == ".org" for p in subdir.iterdir())   # no task link


def test_projadd_registers_project_when_task_discovery_is_ambiguous(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # Ambiguous Org files must not block registration or be chosen arbitrarily.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))

    project = tmp_path / "src" / "abiftool"
    write(project / "CHANGELOG.org", "* Changelog\n")
    write(project / "release-checklist.org", "* Release checklist\n")

    assert projmgr.cmd_add(_add_args(project)) == 0
    captured = capsys.readouterr()
    assert "adding project link only" in captured.err
    assert "use --file" in captured.err

    entry = registry / "abiftool"
    assert (entry / "abiftool").resolve() == project.resolve()
    assert not any(path.suffix == ".org" for path in entry.iterdir())

    registered = manager.discover_projects(registry)
    assert len(registered) == 1
    assert registered[0].org_file is None
    assert "ambiguous task files" in (registered[0].warning or "")
    assert "CHANGELOG.org" in (registered[0].warning or "")
    assert "release-checklist.org" in (registered[0].warning or "")


def test_projadd_explicit_file_resolves_ambiguous_discovery(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # --file must let callers register a chosen task file among generic Org files.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))

    project = tmp_path / "src" / "mixed"
    write(project / "CHANGELOG.org", "* Changelog\n")
    tasks_file = write(
        project / "release-checklist.org",
        "* Tasks\n** TODO t0001 Cut release\n",
    )

    assert projmgr.cmd_add(
        _add_args(project, file="release-checklist.org")
    ) == 0
    capsys.readouterr()

    task_link = registry / "mixed" / "release-checklist.org"
    assert task_link.is_symlink()
    assert task_link.resolve() == tasks_file.resolve()
    registered = manager.discover_projects(registry)
    assert registered[0].org_file == task_link
    assert registered[0].warning is None


def test_manager_project_marker_ignores_registry_notes(tmp_path: Path) -> None:
    # A registry entry is a project because it points outward. The registry's own
    # notes directory holds Org files but links nowhere, so it is not a project.
    registry = tmp_path / "registry"
    project = tmp_path / "src" / "alpha"
    write(project / "tasks.org", "* Tasks\n** TODO t0001 Alpha\n")
    register(registry, "alpha", project)

    write(registry / "docs" / "llm-log.org", "* Notes\n")
    (registry / "docs" / "guide.md").symlink_to(tmp_path / "elsewhere.md")
    write(registry / "README.md", "not a project\n")

    names = [p.name for p in manager.discover_projects(registry)]
    assert names == ["alpha"]


def test_manager_lists_a_project_with_no_task_file(tmp_path: Path) -> None:
    # A project registered before it has any tasks must still be listed; that is
    # exactly when the user is most likely looking for it.
    registry = tmp_path / "registry"
    project = tmp_path / "src" / "fresh"
    project.mkdir(parents=True)
    register(registry, "fresh", project)

    projects = manager.discover_projects(registry)
    assert [p.name for p in projects] == ["fresh"]
    assert projects[0].org_file is None
    assert manager.canonical_org_file(projects[0]) is None
    assert manager.real_project_path(projects[0]) == project.resolve()


def test_manager_registers_a_task_file_link_alone(tmp_path: Path) -> None:
    # A hand-made entry that links only the task file is still a registration.
    registry = tmp_path / "registry"
    org_file = write(tmp_path / "src" / "beta" / "tasks.org", "* Tasks\n")
    entry = registry / "beta"
    entry.mkdir(parents=True)
    (entry / "tasks.org").symlink_to(org_file)

    projects = manager.discover_projects(registry)
    assert [p.name for p in projects] == ["beta"]
    assert manager.real_project_path(projects[0]) == org_file.parent.resolve()


def test_manager_broken_and_ambiguous_entries_stay_visible(tmp_path: Path) -> None:
    # A broken project is broken, not missing: hiding it would hide the fix.
    registry = tmp_path / "registry"
    (registry / "gone").mkdir(parents=True)
    (registry / "gone" / "gone").symlink_to(tmp_path / "src" / "gone")

    (registry / "twins").mkdir()
    first = tmp_path / "src" / "one"
    second = tmp_path / "src" / "two"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    (registry / "twins" / "one").symlink_to(first)
    (registry / "twins" / "two").symlink_to(second)

    by_name = {p.name: p for p in manager.discover_projects(registry)}
    assert set(by_name) == {"gone", "twins"}
    assert by_name["gone"].warning.startswith("broken project link: gone -> ")
    assert by_name["twins"].warning == "several project links: one, two"
    assert by_name["twins"].link is None


def test_projmgr_add_walks_up_to_the_project_root(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # Run from a subdirectory, `add` registers the project, not the subdirectory.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))

    project = tmp_path / "src" / "walker"
    write(project / "tasks.org", "* Tasks\n** TODO t0001 Root task\n")
    (project / "docs").mkdir()
    monkeypatch.chdir(project / "docs")

    assert projmgr.cmd_add(_add_args(None)) == 0
    out = capsys.readouterr().out
    assert "using project root" in out
    assert (registry / "walker" / "walker").resolve() == project.resolve()

    # An explicit path is taken literally: no walking, so the subdirectory wins.
    assert projmgr.cmd_add(_add_args(project / "docs", name="docs")) == 0
    capsys.readouterr()
    assert (registry / "docs" / "docs").resolve() == (project / "docs").resolve()


def test_projmgr_list_shows_a_project_with_no_task_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))

    project = tmp_path / "src" / "fresh"
    project.mkdir(parents=True)
    register(registry, "fresh", project)

    assert projmgr.cmd_list(
        argparse.Namespace(registry=None, all=False, format="plain")
    ) == 0
    out = capsys.readouterr().out
    assert "fresh" in out
    assert "(no task file)" in out


def test_projmgr_list_format_names_applies_the_marker_rule(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # The shell completions read the registry through this, so it must honour
    # the outward-symlink marker: a registry's own README and notes directory
    # are not projects, and a bare glob of the registry would offer them.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    registry.mkdir(parents=True)
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))

    for name in ("elweek", "elusync"):
        project = tmp_path / "src" / name
        write(project / "todo.org", "* Tasks\n** TODO t0001 A task\n")
        register(registry, name, project)

    (registry / "README.md").write_text("# registry\n", encoding="utf-8")
    notes = registry / "docs"
    notes.mkdir()
    (notes / "notes.md").write_text("notes\n", encoding="utf-8")

    assert projmgr.cmd_list(
        argparse.Namespace(registry=None, all=False, format="names")
    ) == 0
    captured = capsys.readouterr()
    assert captured.out.split() == ["elusync", "elweek"]
    assert captured.err == ""


def test_bash_completion_offers_project_names(tmp_path: Path, monkeypatch) -> None:
    # cdproj, and the arguments of pmgr that name a project, complete from the
    # registry. The names come from projmgr.py itself rather than from a glob.
    script = ROOT / "misc" / "ortask-completion.bash"
    registry = tmp_path / "projects"
    registry.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))
    for name in ("elweek", "elusync"):
        project = tmp_path / "src" / name
        write(project / "todo.org", "* Tasks\n** TODO t0001 A task\n")
        register(registry, name, project)
    (registry / "README.md").write_text("# registry\n", encoding="utf-8")

    cases = [
        # cdproj is a shell function: it supplies "cdproj --out FILE" itself, so
        # the first word the user types is already the project name.
        ("_cdproj_complete", f"(cdproj --registry {registry} '')", 3,
         ["elusync", "elweek"]),
        ("_cdproj_complete", f"(cdproj --registry {registry} elw)", 3, ["elweek"]),
        ("_cdproj_complete", f"(cdproj --registry {registry} --)", 3,
         ["--registry", "--help"]),
        ("_cdproj_complete", "(cdproj -s '')", 2, ["elusync", "elweek"]),
        ("_cdproj_complete", "(cdproj --save elw)", 2, ["elweek"]),
        ("_cdproj_complete", "(cdproj -s elweek '')", 3, []),
        ("_projmgr_complete", f"(pmgr --registry {registry} cdproj '')", 4,
         ["elusync", "elweek"]),
        ("_projmgr_complete", f"(pmgr --registry {registry} rm '')", 4,
         ["elusync", "elweek"]),
    ]

    for function, words, cword, expected in cases:
        result = subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-c",
                (
                    f"source {script}; COMP_WORDS={words}; COMP_CWORD={cword}; "
                    f"{function}; printf '%s\\n' \"${{COMPREPLY[@]}}\""
                ),
            ],
            cwd=ROOT,
            env={**os.environ, "ORTASK_PROJMGR": str(ROOT / "projmgr.py")},
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.stderr == ""
        assert result.stdout.split() == expected, (function, words)


def test_bash_completion_survives_an_unreadable_registry(tmp_path: Path) -> None:
    # A TAB must never spill an error into the prompt, however broken the setup.
    script = ROOT / "misc" / "ortask-completion.bash"
    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            (
                f"source {script}; COMP_WORDS=(cdproj --registry {tmp_path}/nope ''); "
                "COMP_CWORD=3; _cdproj_complete; "
                "printf '%s\\n' \"${COMPREPLY[@]}\""
            ),
        ],
        cwd=ROOT,
        env={**os.environ, "ORTASK_PROJMGR": str(ROOT / "projmgr.py")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.stderr == ""
    assert result.stdout.strip() == ""


def test_projmgr_listings_are_visually_distinct() -> None:
    # ptui and cdproj show the same projects for different reasons. Whatever the
    # wording, the two must not be mistakable for each other on screen.
    from ortasklib import menu

    navigator, cdproj = projmgr.NAVIGATOR, projmgr.CDPROJ
    assert navigator.title != cdproj.title
    assert navigator.label != cdproj.label
    assert navigator.detail_header != cdproj.detail_header
    # The row label also carries the row's color, so both must still read as
    # project rows rather than falling through to the neutral style.
    assert {navigator.label, cdproj.label} <= menu.PROJECT_ROW_LABELS
    # Row labels are rendered in a six-column field.
    assert all(len(label) <= 6 for label in (navigator.label, cdproj.label))


def test_projmgr_navigator_counts_the_same_tasks_as_list(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The navigator's count and ``pmgr list``'s rows must not disagree."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    registry.mkdir(parents=True)

    project = tmp_path / "src" / "counted"
    write(
        project / "todo.org",
        """
        * Tasks
        ** TODO t0001 One
        *** TODO t0001.1 A subtask, not counted at the top level
        ** DONE t0002 Two
        ** TODO t0003 Three
        """,
    )
    register(registry, "counted", project)
    empty = tmp_path / "src" / "empty"
    empty.mkdir(parents=True)
    register(registry, "empty", empty)

    projects = {p.name: p for p in manager.discover_projects(registry)}
    assert projmgr._project_tasks(projects["counted"]) == "2 open"
    assert projmgr._project_tasks(projects["empty"]) == "(no task file)"

    summaries = {r["project"]: r for r in manager.summarize_projects(registry)}
    assert len(summaries["counted"]["tasks"]) == 2


def test_projmgr_cdproj_rows_count_the_stack_and_mark_custom_sources(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # Rows show the effective count; only registry-defined stacks get "*".
    registry, project = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    entry = manager.discover_projects(registry)[0]
    assert projmgr._project_stack(entry) == f" 1 dir   {project}"

    (project / "TODO.org").write_text(
        "* Tasks\n** TODO t0001 task\n* Directories\n"
        "** file:/shared/one\n** file:/shared/two\n",
        encoding="utf-8",
    )
    entry = manager.discover_projects(registry)[0]
    assert projmgr._project_stack(entry) == f" 2 dir   {project}"

    _write_private_index(
        registry,
        "** file:/private/one\n** file:/private/one\n",
    )
    entry = manager.discover_projects(registry)[0]
    assert projmgr._project_stack(entry) == f" 1 dir*  {project}"

    session = projmgr._CdprojSession("~/registry", [entry], tmp_path / "out")
    assert session.project_view().instruction.startswith("* = custom · ")


def test_projmgr_project_menu_summary_follows_the_highlight(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A project menu names where the highlighted project lives, as you move."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    registry.mkdir(parents=True)

    here = tmp_path / "src" / "here"
    write(here / "todo.org", "* Tasks\n** TODO t0001 One\n")
    register(registry, "here", here)
    nested = tmp_path / "src" / "nested"
    write(nested / "docs" / "plan.task.org", "* Tasks\n** TODO t0001 One\n")
    register(registry, "nested", nested)
    bare = tmp_path / "src" / "bare"
    bare.mkdir(parents=True)
    register(registry, "bare", bare)

    projects = manager.discover_projects(registry)
    view = projmgr._project_view(
        projects,
        "~/projects",
        lambda session, result: None,
        listing=projmgr.NAVIGATOR,
        instruction=projmgr.PROJECT_MENU_INSTRUCTION,
        select_help="Open the highlighted project",
    )
    assert view.title_right == "Registry: ~/projects"

    summaries = []
    for index in range(len(projects)):
        view.selected_index = index
        summaries.append(view.status_text())

    # Directory first, always. A task file sitting in the project directory is
    # named; one that does not is given its path.
    assert summaries[0].startswith(str(bare))
    assert summaries[0].endswith("(no task file)")
    assert summaries[1] == f"{here}  ·  todo.org"
    assert summaries[2] == f"{nested}  ·  {nested / 'docs' / 'plan.task.org'}"

    # Out-of-range never raises; an empty registry has nothing to describe.
    view.selected_index = 99
    assert view.status_text() == ""


def test_project_priority_and_alphabetical_sort_modes(tmp_path: Path) -> None:
    # Priority and alphabetical orders stay stable as the three-mode ring grows.
    projects = [
        manager.Project("zulu", tmp_path),
        manager.Project("alpha", tmp_path),
        manager.Project("custom", tmp_path),
        manager.Project("bravo", tmp_path),
    ]
    metadata = {
        "zulu": manager.ProjectMetadata(priority="C"),
        "alpha": manager.ProjectMetadata(),
        "custom": manager.ProjectMetadata(priority="1"),
        "bravo": manager.ProjectMetadata(priority="a"),
    }

    priority = manager.sort_projects(
        projects, metadata, manager.PROJECT_SORT_PRIORITY
    )
    alphabetical = manager.sort_projects(
        projects, metadata, manager.PROJECT_SORT_ALPHABETICAL
    )

    assert [project.name for project in priority] == [
        "bravo",
        "zulu",
        "custom",
        "alpha",
    ]
    assert [project.name for project in alphabetical] == [
        "alpha",
        "bravo",
        "custom",
        "zulu",
    ]
    assert manager.next_project_sort_mode(manager.PROJECT_SORT_PRIORITY) == (
        manager.PROJECT_SORT_ALPHABETICAL
    )
    assert manager.next_project_sort_mode(manager.PROJECT_SORT_ALPHABETICAL) == (
        manager.PROJECT_SORT_MODIFIED
    )
    assert manager.next_project_sort_mode(manager.PROJECT_SORT_MODIFIED) == (
        manager.PROJECT_SORT_PRIORITY
    )
    with pytest.raises(ValueError, match="unknown project sort mode"):
        manager.sort_projects(projects, metadata, "recent")


def test_project_modified_sort_uses_task_file_mtime_with_unavailable_last(
    tmp_path: Path,
) -> None:
    # Modified mode is newest-first, name-stable, and puts unusable files last.
    older = write(tmp_path / "older.org", "* Tasks\n")
    newer = write(tmp_path / "newer.org", "* Tasks\n")
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
    projects = [
        manager.Project("missing", tmp_path, tmp_path / "missing.org"),
        manager.Project("older", tmp_path, older),
        manager.Project("absent", tmp_path),
        manager.Project("directory", tmp_path, tmp_path),
        manager.Project("newer", tmp_path, newer),
    ]

    ordered = manager.sort_projects(
        projects, {}, manager.PROJECT_SORT_MODIFIED
    )

    assert [project.name for project in ordered] == [
        "newer",
        "older",
        "absent",
        "directory",
        "missing",
    ]


def test_task_file_mirror_diagnostic_is_semantic_and_non_authoritative(
    tmp_path: Path, monkeypatch
) -> None:
    # Equivalent path spellings agree; a stale mirror is shown but never resolved.
    project_dir = tmp_path / "sample"
    task_file = write(project_dir / "tasks.org", "* Tasks\n")
    project = manager.Project("sample", project_dir, task_file)
    monkeypatch.setenv("SAMPLE_ROOT", str(project_dir))

    assert manager.task_file_mirror_mismatch(project, None) is None
    assert manager.task_file_mirror_mismatch(project, "tasks.org") is None
    assert (
        manager.task_file_mirror_mismatch(
            project, "$SAMPLE_ROOT/tasks.org"
        )
        is None
    )
    mismatch = manager.task_file_mirror_mismatch(project, "stale.org")
    assert mismatch == (
        f"TASK_FILE differs: recorded stale.org; resolved {task_file.resolve()}"
    )
    assert "TASK_FILE differs" in projmgr._navigator_row(
        project, manager.ProjectMetadata(task_file="stale.org")
    )
    assert projmgr._project_summary(
        project, manager.ProjectMetadata(task_file="stale.org")
    ).endswith(mismatch)


def test_view_state_cycles_only_the_axes_a_surface_offers() -> None:
    # A one-position axis is not a choice: no key, and nothing in the badge.
    tasks_view = taskui.TASK_VIEW_AXES.initial()
    assert (tasks_view.filter, tasks_view.sort) == ("all", "file")
    assert tasks_view.axes.cycles_filter and tasks_view.axes.cycles_sort
    assert tasks_view.badge() == "all · File order"
    assert tasks_view.next_filter().badge() == "TODO · File order"
    assert tasks_view.next_filter().next_filter().badge() == "DONE+ · File order"
    assert tasks_view.next_filter().next_filter().next_filter().filter == "all"
    assert tasks_view.next_sort().badge() == "all · Priority order"
    assert tasks_view.next_sort().next_sort().badge() == "all · Title order"
    assert tasks_view.next_sort().next_sort().next_sort().sort == "file"

    projects_view = projmgr.PROJECT_VIEW_AXES.initial()
    assert projects_view.badge() == "Priority sort"
    assert projects_view.next_sort().badge() == "Alphabetical sort"
    assert projects_view.toggle_reverse().badge() == (
        "Priority sort (lowest first)"
    )
    assert projects_view.next_sort().toggle_reverse().badge() == (
        "Alphabetical sort (Z-A)"
    )
    assert projects_view.next_filter().badge() == "open only · Priority sort"

    # Transitions return new states rather than mutating the rendered one.
    assert projects_view.filter == "all" and projects_view.sort == "priority"
    with pytest.raises(ValueError):
        projects_view.with_sort("file")
    with pytest.raises(ValueError):
        viewstate.next_position(taskui.TASK_FILTERS, "open")

    # t0039.3.2: reversibility is declared per sort, not globally. File order
    # cannot be inverted, so nothing can put a task list into that state — even
    # though the task list's other two orders can be.
    assert not tasks_view.reversible
    assert tasks_view.with_reverse(True) == tasks_view
    with pytest.raises(ValueError):
        viewstate.ViewState(taskui.TASK_VIEW_AXES, "all", "file", reverse=True)
    by_title = tasks_view.next_sort().next_sort()
    assert by_title.reversible
    assert by_title.with_reverse(True).badge() == "all · Title order (Z-A)"
    # Returning to file order drops a direction it cannot hold.
    assert by_title.with_reverse(True).next_sort().reverse is False
    assert projects_view.reversible
    assert projects_view.direction_label == "highest first"


def test_order_by_inverts_only_the_primary_axis() -> None:
    # sorted(reverse=True) would flip the tie-break and hoist unavailable rows.
    rows = [("b", 2, 0), ("a", 1, 0), ("c", 1, 0), ("z", 9, 1), ("y", 9, 1)]
    forward = viewstate.order_by(
        rows,
        primary=lambda row: row[1],
        tiebreak=lambda row: row[0],
        unavailable=lambda row: row[2],
    )
    assert [row[0] for row in forward] == ["a", "c", "b", "y", "z"]

    reversed_rows = viewstate.order_by(
        rows,
        primary=lambda row: row[1],
        tiebreak=lambda row: row[0],
        unavailable=lambda row: row[2],
        reverse=True,
    )
    # Primary descending; the name tie-break stays ascending; unavailable last.
    assert [row[0] for row in reversed_rows] == ["b", "a", "c", "y", "z"]


def test_reversed_project_sorts_keep_unreadable_projects_last(
    tmp_path: Path,
) -> None:
    # Asking for the oldest project first must not promote the ones we cannot read.
    registry = tmp_path / "registry"
    for name, modified_ns in (
        ("alpha", 1_000_000_000),
        ("bravo", 2_000_000_000),
    ):
        project = tmp_path / "src" / name
        task_file = write(project / "tasks.org", "* Tasks\n** TODO t0001 One\n")
        os.utime(task_file, ns=(modified_ns, modified_ns))
        register(registry, name, project)
    broken = registry / "gone"
    broken.mkdir(parents=True)
    (broken / "gone").symlink_to(tmp_path / "missing")

    projects = manager.discover_projects(registry)
    metadata = manager.read_project_metadata(registry, projects)
    order = lambda mode, reverse: [
        project.name
        for project in manager.sort_projects(
            projects, metadata, mode, reverse=reverse
        )
    ]

    assert order(manager.PROJECT_SORT_MODIFIED, False) == ["bravo", "alpha", "gone"]
    assert order(manager.PROJECT_SORT_MODIFIED, True) == ["alpha", "bravo", "gone"]
    assert order(manager.PROJECT_SORT_ALPHABETICAL, True) == [
        "gone",
        "bravo",
        "alpha",
    ]


def test_project_rows_show_the_modification_age(tmp_path: Path) -> None:
    # Sorting by a key nobody can see is a guess, so Modified has a column.
    now_ns = 1_000_000 * 1_000_000_000
    second = 1_000_000_000
    ages = {
        "now": 30 * second,
        "9m": 9 * 60 * second,
        "5h": 5 * 60 * 60 * second,
        "3d": 3 * 24 * 60 * 60 * second,
        "5w": 40 * 24 * 60 * 60 * second,
        "2y": 800 * 24 * 60 * 60 * second,
    }
    for expected, age in ages.items():
        snapshot = manager.ProjectSnapshot(1, 2, now_ns - age)
        assert projmgr._project_modified(snapshot, now_ns=now_ns) == expected
    # A clock that ran backwards must not print a negative age.
    assert projmgr._project_modified(
        manager.ProjectSnapshot(1, 2, now_ns + second), now_ns=now_ns
    ) == "now"
    assert projmgr._project_modified(manager.ProjectSnapshot(1, 2, None)) == (
        projmgr.UNKNOWN_AGE
    )
    assert projmgr._project_modified(None) == projmgr.UNKNOWN_AGE

    registry = tmp_path / "registry"
    project = tmp_path / "src" / "alpha"
    task_file = write(project / "tasks.org", "* Tasks\n** TODO t0001 One\n")
    os.utime(task_file, ns=(now_ns - ages["3d"], now_ns - ages["3d"]))
    register(registry, "alpha", project)
    broken = registry / "gone"
    broken.mkdir(parents=True)
    (broken / "gone").symlink_to(tmp_path / "missing")

    projects = manager.discover_projects(registry)
    snapshots = manager.snapshot_projects(projects)
    by_name = {one.name: one for one in projects}

    row = projmgr._navigator_row(
        by_name["alpha"],
        manager.ProjectMetadata(priority="A", description="Steady work"),
        snapshots["alpha"],
        now_ns=now_ns,
    )
    assert row == "[A] alpha           1 open        3d    Steady work"

    # An overflowing workload column cannot run into the age beside it.
    crowded = projmgr._navigator_row(
        by_name["gone"],
        manager.ProjectMetadata(),
        snapshots["gone"],
        now_ns=now_ns,
    )
    assert "  " + projmgr.UNKNOWN_AGE in crowded
    assert ")" + projmgr.UNKNOWN_AGE not in crowded

    # The numbered table has no column to hold open, so an unknown age is left
    # out rather than printed as a dash.
    detail = projmgr._navigator_dashboard_detail(
        by_name["alpha"], manager.ProjectMetadata(), snapshots["alpha"],
        now_ns=now_ns,
    )
    assert detail.endswith("(1 open, 3d)")
    broken_detail = projmgr._navigator_dashboard_detail(
        by_name["gone"], manager.ProjectMetadata(), snapshots["gone"],
        now_ns=now_ns,
    )
    assert f", {projmgr.UNKNOWN_AGE})" not in broken_detail


def test_project_name_column_holds_a_realistic_name(tmp_path: Path) -> None:
    """A name as long as the ones people actually use must not shift the row.

    Both listings pad to the same width so a project sits in the same place in
    the navigator and the cdproj picker.
    """
    registry = tmp_path / "registry"
    names = ("a", "bashfuncs2023", "ortask")
    for name in names:
        project = tmp_path / "src" / name
        write(project / "tasks.org", "* Tasks\n** TODO t0001 One\n")
        register(registry, name, project)
    index = registry / manager.PROJECTS_INDEX_NAME
    index.write_text(
        manager.PROJECTS_INDEX_HEADER
        + "".join(
            f"* {name}\n** Directories\n*** file:{tmp_path / 'src' / name}\n"
            for name in names
        ),
        encoding="utf-8",
    )

    projects = manager.discover_projects(registry)
    snapshots = manager.snapshot_projects(projects)
    by_name = {one.name: one for one in projects}
    assert len(max(names, key=len)) < projmgr.PROJECT_NAME_WIDTH

    workload = [
        projmgr._navigator_row(
            by_name[name], manager.ProjectMetadata(), snapshots[name]
        ).index("1 open")
        for name in names
    ]
    assert len(set(workload)) == 1

    for listing in (projmgr.NAVIGATOR, projmgr.CDPROJ):
        rows = projmgr._project_rows(projects, listing, {}, snapshots)
        starts = {row.text.index(listing.detail(by_name[name])) for row, name in zip(rows, sorted(names))}
        assert len(starts) == 1

    # A name past the width still pushes its own row right; nothing is cut.
    long_name = "x" * (projmgr.PROJECT_NAME_WIDTH + 3)
    project = tmp_path / "src" / long_name
    write(project / "tasks.org", "* Tasks\n** TODO t0001 One\n")
    register(registry, long_name, project)
    overflowing = manager.discover_projects(registry)
    stretched = projmgr._navigator_row(
        next(one for one in overflowing if one.name == long_name),
        manager.ProjectMetadata(),
        manager.snapshot_projects(overflowing)[long_name],
    )
    assert long_name in stretched
    assert stretched.index("1 open") > workload[0]


def test_project_render_reads_each_task_file_once(
    tmp_path: Path, monkeypatch
) -> None:
    # t0039.3.3: filtering, ordering, and rows share one reading per project.
    registry = tmp_path / "registry"
    for name, tasks, modified_ns in (
        ("alpha", "** TODO t0001 One\n", 1_000_000_000),
        ("bravo", "** TODO t0001 One\n** DONE t0002 Two\n", 2_000_000_000),
        ("zulu", "** DONE t0001 Finished\n", 3_000_000_000),
    ):
        project = tmp_path / "src" / name
        task_file = write(project / "tasks.org", f"* Tasks\n{tasks}")
        os.utime(task_file, ns=(modified_ns, modified_ns))
        register(registry, name, project)
    (registry / manager.PROJECTS_INDEX_NAME).write_text(
        manager.PROJECTS_INDEX_HEADER + "* alpha\n* bravo\n* zulu\n",
        encoding="utf-8",
    )

    reads: list[str] = []
    real = manager.read_project_snapshot

    def counted(project):
        reads.append(project.name)
        return real(project)

    monkeypatch.setattr(manager, "read_project_snapshot", counted)

    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)
    browser.view_state = browser.view_state.next_filter().next_sort().next_sort()
    assert browser.view_state.filter == "open"
    assert browser.view_state.sort == manager.PROJECT_SORT_MODIFIED

    reads.clear()
    view = browser.view()

    # The open filter, the Modified order, and the "n open" column all want the
    # same two facts about the same file; one render reads it once.
    assert sorted(reads) == ["alpha", "bravo", "zulu"]
    assert [row.text[4:].split()[0] for row in view.rows] == ["bravo", "alpha"]
    assert "1 open" in view.rows[1].text


def test_project_filter_hides_only_provably_quiet_projects(tmp_path: Path) -> None:
    # docs/ptui.md: warnings survive filtering, and unreadable is not "nothing to do".
    registry = tmp_path / "registry"
    for name, tasks in (
        ("busy", "** TODO t0001 One\n** DONE t0002 Two\n"),
        ("quiet", "** DONE t0001 One\n** MOOT t0002 Two\n"),
        ("empty", ""),
    ):
        project = tmp_path / "src" / name
        write(project / "tasks.org", f"* Tasks\n{tasks}")
        register(registry, name, project)
    broken = registry / "gone"
    broken.mkdir(parents=True)
    (broken / "gone").symlink_to(tmp_path / "missing")

    projects = manager.discover_projects(registry)
    by_name = {project.name: project for project in projects}
    assert manager.top_level_task_counts(by_name["busy"]) == (1, 2)
    assert manager.top_level_task_counts(by_name["quiet"]) == (0, 2)
    assert manager.top_level_task_counts(by_name["empty"]) == (0, 0)
    assert manager.top_level_task_counts(by_name["gone"]) is None

    assert [p.name for p in manager.filter_projects(projects, "all")] == [
        "busy",
        "empty",
        "gone",
        "quiet",
    ]
    assert [p.name for p in manager.filter_projects(projects, "open")] == [
        "busy",
        "gone",
    ]
    with pytest.raises(ValueError):
        manager.filter_projects(projects, "todo")


def test_task_filter_names_every_state_the_done_position_matches(
    tmp_path: Path,
) -> None:
    # The third position selects MOOT too, so the label cannot say DONE alone.
    org_file = write(
        tmp_path / "tasks.org",
        "* Tasks\n"
        "** TODO t0001 Open\n"
        "** DONE t0002 Finished\n"
        "** MOOT t0003 Abandoned\n"
        "** SUPERSEDED t0004 Replaced\n",
    )
    buf = taskui.OrgBuffer(org_file)

    def ids(mode: str) -> list[str]:
        return [
            item.task.id
            for item in taskui.load_menu_items(buf, filter_mode=mode)
            if item.task is not None
        ]

    assert ids("all") == ["t0001", "t0002", "t0003", "t0004"]
    assert ids("todo") == ["t0001"]
    # Every terminal state, SUPERSEDED included — which is why the label is not
    # the name of one of them.
    assert ids("done") == ["t0002", "t0003", "t0004"]
    assert taskui._task_filter_label("done") == "DONE+"
    assert taskui._task_menu_instruction("done").startswith("DONE+ · ")
    assert taskui.TASK_TERMINAL_FILTER_DESCRIPTION == "DONE, MOOT, SUPERSEDED"
    assert taskui.task_view(include_done=False).filter == "todo"


def test_project_browser_shows_metadata_and_anchors_sort_selection(
    tmp_path: Path,
) -> None:
    # Navigator rows expose steering data and keep the same project selected.
    registry = tmp_path / "registry"
    for name, tasks, modified_ns in (
        ("alpha", "** TODO t0001 One\n", 1_000_000_000),
        ("bravo", "** TODO t0001 One\n** TODO t0002 Two\n", 2_000_000_000),
        ("zulu", "** DONE t0001 Finished\n", 3_000_000_000),
    ):
        project = tmp_path / "src" / name
        task_file = write(project / "tasks.org", f"* Tasks\n{tasks}")
        os.utime(task_file, ns=(modified_ns, modified_ns))
        register(registry, name, project)
    (registry / manager.PROJECTS_INDEX_NAME).write_text(
        manager.PROJECTS_INDEX_HEADER
        + "* alpha\n"
        + ":PROPERTIES:\n:DESCRIPTION: Unprioritized work\n:END:\n"
        + "* [#B] bravo\n"
        + ":PROPERTIES:\n:DESCRIPTION: Two open tasks\n"
        + ":TASK_FILE: first.org\n:TASK_FILE: repeated.org\n:END:\n"
        + "* [#A] zulu\n"
        + ":PROPERTIES:\n:DESCRIPTION: Highest priority\n:END:\n",
        encoding="utf-8",
    )

    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)
    view = browser.view()

    assert view.instruction.startswith("Priority sort")
    assert view.actions["s"] == projmgr.PROJECT_SORT_ACTION
    assert view.rows[0].text.startswith("[A] zulu")
    assert "0 open" in view.rows[0].text
    assert "Highest priority" in view.rows[0].text
    assert view.rows[1].text.startswith("[B] bravo")
    assert "2 open" in view.rows[1].text
    assert "metadata: recorded more than once: TASK_FILE" in view.rows[1].text
    assert view.rows[2].text.startswith("[ ] alpha")
    view.selected_index = 1
    assert view.status_text().endswith(
        "metadata: recorded more than once: TASK_FILE"
    )

    class FakeSession:
        def __init__(self, current_view) -> None:
            self.current_view = current_view
            self.message = ""

        def replace_view(self, replacement) -> None:
            self.current_view = replacement

        def set_transient_message(self, message: str) -> None:
            self.message = message

    session = FakeSession(view)
    view.on_result(session, menu.MenuResult("sort", 1))

    assert browser.sort_mode == manager.PROJECT_SORT_ALPHABETICAL
    assert session.message == "Sort: Alphabetical"
    assert [row.text[4:].split()[0] for row in session.current_view.rows] == [
        "alpha",
        "bravo",
        "zulu",
    ]
    assert session.current_view.selected_index == 1
    assert session.current_view.rows[1].text.startswith("[B] bravo")

    session.current_view.on_result(session, menu.MenuResult("sort", 1))
    assert browser.sort_mode == manager.PROJECT_SORT_MODIFIED
    assert session.message == "Sort: Modified"
    assert session.current_view.selected_index == 1

    session.current_view.on_result(session, menu.MenuResult("sort", 1))
    assert browser.sort_mode == manager.PROJECT_SORT_PRIORITY
    assert session.message == "Sort: Priority"
    assert session.current_view.selected_index == 1


def test_project_browser_filters_and_anchors_the_selection(tmp_path: Path) -> None:
    # C-t hides quiet projects, keeps the highlight on the same one, and says so.
    registry = tmp_path / "registry"
    for name, tasks in (
        ("alpha", "** TODO t0001 One\n"),
        ("bravo", "** DONE t0001 Finished\n"),
        ("zulu", "** TODO t0001 One\n"),
    ):
        project = tmp_path / "src" / name
        write(project / "tasks.org", f"* Tasks\n{tasks}")
        register(registry, name, project)
    (registry / manager.PROJECTS_INDEX_NAME).write_text(
        manager.PROJECTS_INDEX_HEADER + "* alpha\n* bravo\n* zulu\n",
        encoding="utf-8",
    )

    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)
    view = browser.view()
    assert view.instruction.startswith("Priority sort")
    assert "C-t filter" in view.instruction
    assert view.actions["c-t"] == projmgr.PROJECT_FILTER_ACTION
    assert [row.text[4:].split()[0] for row in view.rows] == [
        "alpha",
        "bravo",
        "zulu",
    ]

    class FakeSession:
        def __init__(self, current_view) -> None:
            self.current_view = current_view
            self.message = ""

        def replace_view(self, replacement) -> None:
            self.current_view = replacement

        def set_transient_message(self, message: str) -> None:
            self.message = message

    session = FakeSession(view)
    view.selected_index = 2
    view.on_result(session, menu.MenuResult("filter", 2))

    assert browser.view_state.filter == "open"
    assert session.message == "Showing: open only"
    assert session.current_view.instruction.startswith(
        "open only · Priority sort"
    )
    assert [row.text[4:].split()[0] for row in session.current_view.rows] == [
        "alpha",
        "zulu",
    ]
    # zulu was selected before bravo was hidden, so it is still selected after.
    assert session.current_view.selected_index == 1

    session.current_view.on_result(session, menu.MenuResult("filter", 1))
    assert browser.view_state.filter == "all"
    assert session.message == "Showing: all projects"
    assert len(session.current_view.rows) == 3


def test_view_options_screen_applies_live_and_leaves_save_alone(
    tmp_path: Path,
) -> None:
    # No apply key: choices take effect immediately, so C-s keeps meaning "save".
    saved: list[str] = []
    applied: list[viewstate.ViewState] = []
    screen = viewui.view_options_screen(
        projmgr.PROJECT_VIEW_AXES.initial(),
        lambda session, revised: applied.append(revised),
        title="View options: project navigator",
        save=lambda session: saved.append("projects.org"),
        save_help="Save buffered project metadata to projects.org",
    )

    assert len(screen.focus_targets) == 3
    assert screen.summary == "Showing: Priority sort"
    assert "C-s save" in screen.instruction
    assert "C-s save" in screen.editing_instruction
    assert ("Ctrl-S", "Save buffered project metadata to projects.org") in (
        screen.help_entries
    )
    # Every field needs an explicit Enter before arrows change it, like the
    # metadata workspace.
    assert screen.choice_focus_indices == frozenset({0, 1, 2})
    assert screen.edit_focus_indices == frozenset({0, 1, 2})

    class FakeSession:
        pass

    session = FakeSession()
    screen.on_choice_change(session, 1, 1)
    screen.on_choice_change(session, 2, 1)
    screen.on_choice_change(session, 0, 1)
    assert [state.badge() for state in applied] == [
        "Alphabetical sort",
        "Alphabetical sort (Z-A)",
        "open only · Alphabetical sort (Z-A)",
    ]
    assert screen.summary == "Showing: open only · Alphabetical sort (Z-A)"
    assert screen.status_text() == screen.summary
    assert not saved

    screen.on_save(session)
    assert saved == ["projects.org"]

    # t0039.3.2: the direction field's words follow the selected order, and
    # they track it live rather than showing what was true when it opened.
    def rendered(index: int) -> str:
        control = screen.focus_targets[index].content
        return "".join(part[1] for part in control.text()).strip()

    assert rendered(2) == "Direction  [Z-A]"
    screen.on_choice_change(session, 1, 1)
    assert rendered(1) == "Order      [Modified]"
    assert rendered(2) == "Direction  [oldest first]"

    # The task list offers its own three axes; its default order is the one
    # that holds the tree together, and that one cannot be inverted.
    task_screen = viewui.view_options_screen(
        taskui.TASK_VIEW_AXES.initial(),
        lambda session, revised: applied.append(revised),
        title="View options: tasks",
        save=lambda session: saved.append("tasks.org"),
        save_help="Save the entire tasks.org file",
    )
    assert len(task_screen.focus_targets) == 3
    assert task_screen.summary == "Showing: all · File order"

    def task_row(index: int) -> str:
        control = task_screen.focus_targets[index].content
        return "".join(part[1] for part in control.text()).strip()

    # File order has no opposite, so its direction field says so — and a
    # refused choice must not leave the form showing a setting not in effect.
    assert task_row(2) == "Direction  [n/a]"
    task_screen.on_choice_change(session, 2, 1)
    assert task_row(2) == "Direction  [n/a]"
    assert applied[-1].reverse is False

    task_screen.on_choice_change(session, 1, 1)
    assert task_row(1) == "Order      [Priority]"
    assert task_row(2) == "Direction  [highest first]"
    assert applied[-1].reverse is False


def test_project_browser_view_screen_changes_the_list_behind_it(
    tmp_path: Path,
) -> None:
    # v opens the screen; leaving it re-renders the list through the same resume.
    registry = tmp_path / "registry"
    for name, tasks in (
        ("alpha", "** TODO t0001 One\n"),
        ("bravo", "** DONE t0001 Finished\n"),
    ):
        project = tmp_path / "src" / name
        write(project / "tasks.org", f"* Tasks\n{tasks}")
        register(registry, name, project)
    (registry / manager.PROJECTS_INDEX_NAME).write_text(
        manager.PROJECTS_INDEX_HEADER + "* alpha\n* bravo\n", encoding="utf-8"
    )

    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)
    view = browser.view()
    assert view.actions["v"].name == "view"
    assert "v view" in view.instruction

    class FakeSession:
        def __init__(self, current_view) -> None:
            self.current_view = current_view
            self.pushed = None
            self.message = ""

        def push_view(self, replacement) -> None:
            self.pushed = replacement

        def replace_view(self, replacement) -> None:
            self.current_view = replacement

        def set_transient_message(self, message: str) -> None:
            self.message = message

    session = FakeSession(view)
    view.on_result(session, menu.MenuResult("view", 0))
    screen = session.pushed
    assert isinstance(screen, menu.WorkspaceView)
    assert screen.title == "View options: project navigator"

    screen.on_choice_change(session, 0, 1)
    assert browser.view_state.filter == "open"
    # The list is covered while the screen is open; resume rebuilds it.
    assert [row.text[4:].split()[0] for row in session.current_view.rows] == [
        "alpha",
        "bravo",
    ]
    view.on_resume(session)
    assert [row.text[4:].split()[0] for row in session.current_view.rows] == [
        "alpha"
    ]
    assert session.current_view.instruction.startswith("open only · Priority sort")


def test_project_browser_buffers_priority_undo_redo_and_save(tmp_path: Path) -> None:
    # Project priority changes stay in one buffer, follow sorting, and save on C-s.
    registry = tmp_path / "registry"
    for name in ("alpha", "bravo"):
        project = tmp_path / "src" / name
        write(project / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
        register(registry, name, project)
    index = registry / manager.PROJECTS_INDEX_NAME
    original = (
        manager.PROJECTS_INDEX_HEADER
        + "* alpha :work:\nAlpha prose.\n"
        + "* [#B] bravo\n:PROPERTIES:\n:DESCRIPTION: Other work\n:END:\n"
    )
    index.write_text(original, encoding="utf-8")
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)
    assert browser.index_buffer is not None

    class FakeSession:
        def __init__(self, current_view) -> None:
            self.current_view = current_view
            self.message = ""
            self.final_message = ""

        def replace_view(self, replacement) -> None:
            self.current_view = replacement

        def set_transient_message(self, message: str) -> None:
            self.message = message

        def set_outcome(self, message: str) -> None:
            self.message = self.final_message = message

    session = FakeSession(browser.view())
    assert session.current_view.rows[1].text.startswith("[ ] alpha")

    # None -> C keeps alpha below B; C -> B reorders alpha before bravo by name.
    session.current_view.on_result(session, menu.MenuResult("priority_up", 1))
    assert session.current_view.selected_index == 1
    session.current_view.on_result(session, menu.MenuResult("priority_up", 1))
    assert session.current_view.selected_index == 0
    assert session.current_view.rows[0].text.startswith("[B] alpha")
    assert session.current_view.title == "Project navigator *"
    assert session.current_view.status_text().startswith("FILE MODIFIED: 2 edits")
    assert session.current_view.instruction.startswith("FILE MODIFIED")
    assert index.read_text(encoding="utf-8") == original
    assert browser.index_buffer.autosave_path.exists()

    session.current_view.on_result(session, menu.MenuResult("undo", 0))
    assert session.current_view.selected_index == 1
    assert session.current_view.rows[1].text.startswith("[C] alpha")
    session.current_view.on_result(session, menu.MenuResult("redo", 1))
    assert session.current_view.selected_index == 0
    assert session.current_view.rows[0].text.startswith("[B] alpha")

    session.current_view.on_result(session, menu.MenuResult("save", 0))
    assert session.final_message == "Saved changes to projects.org"
    assert browser.index_buffer.dirty is False
    assert not browser.index_buffer.autosave_path.exists()
    assert index.read_text(encoding="utf-8") == original.replace(
        "* alpha :work:", "* [#B] alpha :work:"
    )


@pytest.mark.skipif(
    projmgr.TextArea is None,
    reason="prompt_toolkit not installed",
)
def test_project_metadata_workspace_applies_one_buffered_transaction(
    tmp_path: Path, monkeypatch
) -> None:
    # Metadata fields should compose one source-preserving OrgBuffer transaction.
    from prompt_toolkit.document import Document
    from ortasklib import menu

    registry = tmp_path / "registry"
    project_dir = tmp_path / "src" / "alpha"
    docs_dir = project_dir / "docs"
    code_dir = project_dir / "code"
    docs_dir.mkdir(parents=True)
    code_dir.mkdir()
    task_file = write(
        project_dir / "tasks.org",
        "* Tasks\n** TODO t0001 Work\n",
    )
    register(registry, "alpha", project_dir)
    index = registry / manager.PROJECTS_INDEX_NAME
    original = (
        manager.PROJECTS_INDEX_HEADER
        + "* [#B] alpha :work:\n"
        + ":PROPERTIES:\n:DESCRIPTION: Old summary\n"
        + ":TASK_FILE: stale.org\n:CUSTOM: exact\n:END:\n"
        + "Keep this prose.\n** Directories\n   - docs\n"
        + "* other\nOther source.\n"
    )
    index.write_text(original, encoding="utf-8")
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)

    class FakeSession:
        def __init__(self, current_view) -> None:
            self.views = [current_view]
            self.message = ""

        @property
        def current_view(self):
            return self.views[-1]

        def push_view(self, view) -> None:
            self.views.append(view)

        def set_message(self, message: str | None) -> None:
            self.message = message or ""

        def set_transient_message(self, message: str) -> None:
            self.message = message

        def set_outcome(self, message: str) -> None:
            self.message = message

    session = FakeSession(browser.view())
    session.current_view.on_result(session, menu.MenuResult("metadata", 0))
    workspace = session.current_view
    assert isinstance(workspace, menu.WorkspaceView)
    assert workspace.summary.startswith("Effective directories: 1 (custom)")
    assert "TASK_FILE differs" in workspace.summary
    assert workspace.focused_index == 1
    assert workspace.edit_focus_indices == frozenset({0, 1, 2})
    assert workspace.multiline_edit_focus_indices == frozenset({2})
    assert workspace.editing_index is None
    assert workspace.dirty_label == "PROJECT EDITED"

    workspace.on_choice_change(session, 0, 1)
    workspace.editing_index = 1
    workspace.focus_targets[1].buffer.set_document(
        Document("New summary", cursor_position=11)
    )
    workspace.focused_index = 2
    workspace.editing_index = 2
    directories = f"{docs_dir}\n{code_dir}"
    workspace.focus_targets[2].buffer.set_document(
        Document(directories, cursor_position=len(directories))
    )
    workspace.on_activate(session, 3)
    assert session.message == "TASK_FILE correction staged; C-s saves it"
    assert "TASK_FILE differs" not in workspace.summary
    assert workspace.is_dirty is not None and workspace.is_dirty()

    monkeypatch.setattr(
        browser,
        "_save_index_changes",
        lambda: (True, "Kept changes buffered for test"),
    )
    workspace.on_save(session)

    assert browser.index_buffer is not None
    revised = browser.index_buffer.read()
    assert browser.index_buffer.undo_count == 1
    assert index.read_text(encoding="utf-8") == original
    assert "* [#A] alpha :work:" in revised
    assert ":DESCRIPTION: New summary" in revised
    assert f":TASK_FILE: {task_file.resolve()}" in revised
    assert f"   - {docs_dir.resolve()}" in revised
    assert f"   - {code_dir.resolve()}" in revised
    assert ":CUSTOM: exact\n" in revised
    assert ":END:\nKeep this prose." in revised
    assert revised.endswith("* other\nOther source.\n")
    assert workspace.is_dirty() is False
    assert "TASK_FILE differs" not in workspace.summary


def test_project_browser_same_section_conflict_preserves_both_versions(
    tmp_path: Path,
) -> None:
    # Differing ptui/set-dirs edits to one project must remain a named conflict.
    registry = tmp_path / "registry"
    project_dir = tmp_path / "src" / "alpha"
    write(project_dir / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
    register(registry, "alpha", project_dir)
    old_dir = project_dir / "old"
    new_dir = project_dir / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    index = registry / manager.PROJECTS_INDEX_NAME
    index.write_text(
        manager.PROJECTS_INDEX_HEADER
        + f"* alpha\n** Directories\n   - {old_dir}\n",
        encoding="utf-8",
    )
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)
    assert browser.index_buffer is not None

    class FakeSession:
        def __init__(self, current_view) -> None:
            self.views = [current_view]
            self.message = ""

        @property
        def current_view(self):
            return self.views[-1]

        def replace_view(self, replacement) -> None:
            self.views[-1] = replacement

        def push_view(self, view) -> None:
            self.views.append(view)

        def set_transient_message(self, message: str) -> None:
            self.message = message

        def set_outcome(self, message: str) -> None:
            raise AssertionError(f"stale save reported success: {message}")

    session = FakeSession(browser.view())
    session.current_view.on_result(session, menu.MenuResult("priority_up", 0))
    assert "* [#C] alpha" in browser.index_buffer.read()

    project = manager.discover_projects(registry)[0]
    plan = manager.plan_registry_directories_update(project, [new_dir])
    assert manager.apply_registry_directories_update(plan, keep_missing=False)
    external = index.read_text(encoding="utf-8")
    assert str(new_dir) in external and "[#C]" not in external

    browser._poll_index(session)

    assert browser.index_conflicts[0].project == "alpha"
    assert session.message == "Merge conflict: alpha"
    assert session.current_view.status_text().startswith("MERGE CONFLICT: alpha")
    session.current_view.on_result(session, menu.MenuResult("save", 0))

    assert session.current_view.title == "Merge conflict: alpha"
    assert [row.status for row in session.current_view.rows] == [
        "RELOAD",
        "RETRY",
        "CONTINUE",
    ]
    assert index.read_text(encoding="utf-8") == external
    assert "* [#C] alpha" in browser.index_buffer.read()
    assert browser.index_buffer.dirty is True
    assert browser.index_buffer.can_undo is True
    assert browser.index_buffer.autosave_path.exists()
    assert browser.index_buffer.external_change is not None
    assert browser.index_buffer.external_change.text == external
    assert browser.index_buffer.undo_count == 1
    assert browser.index_buffer.autosave_path.read_text(encoding="utf-8") == (
        browser.index_buffer.read()
    )


def test_project_browser_rebases_disjoint_external_edit_and_logs_only_ours(
    tmp_path: Path, monkeypatch
) -> None:
    # Polling rebases one local section over another external section in memory.
    registry = tmp_path / "registry"
    for name in ("alpha", "bravo"):
        project = tmp_path / "src" / name
        write(project / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
        register(registry, name, project)
    index = registry / manager.PROJECTS_INDEX_NAME
    base = manager.PROJECTS_INDEX_HEADER + "* alpha\nAlpha.\n* bravo\nBravo.\n"
    index.write_text(base, encoding="utf-8")
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)
    assert browser.index_buffer is not None

    class FakeSession:
        def __init__(self, current_view) -> None:
            self.current_view = current_view
            self.message = ""

        def replace_view(self, replacement) -> None:
            self.current_view = replacement

        def set_transient_message(self, message: str) -> None:
            self.message = message

    session = FakeSession(browser.view())
    session.current_view.on_result(session, menu.MenuResult("priority_up", 0))
    ours = base.replace("* alpha", "* [#C] alpha")
    theirs = base.replace("* bravo", "* [#A] bravo")
    merged = theirs.replace("* alpha", "* [#C] alpha")
    index.write_text(theirs, encoding="utf-8")

    browser._poll_index(session)

    assert index.read_text(encoding="utf-8") == theirs
    assert browser.index_buffer.saved_text == theirs
    assert browser.index_buffer.read() == merged
    assert browser.index_buffer.dirty is True
    assert browser.index_buffer.undo_count == 1
    assert browser.index_buffer.can_redo is False
    assert browser.index_buffer.autosave_path.read_text(encoding="utf-8") == merged
    assert browser.index_conflicts == ()
    assert browser.index_buffer.external_change is None
    assert session.message == "Merged external changes: bravo"
    assert session.current_view.rows[0].text.startswith("[A] bravo")
    assert session.current_view.rows[1].text.startswith("[C] alpha")
    assert session.current_view.selected_index == 1

    description = browser.index_buffer.undo()
    assert description == "Reapply alpha after external changes"
    assert browser.index_buffer.read() == theirs
    assert browser.index_buffer.dirty is False
    assert not browser.index_buffer.autosave_path.exists()
    assert index.read_text(encoding="utf-8") == theirs
    assert browser.index_buffer.redo() == description

    recorded: list[tuple[str, str, Path]] = []

    def record(before: str, after: str, source_file: Path, **kwargs) -> None:
        recorded.append((before, after, source_file))

    monkeypatch.setattr(taskui.eventlog, "record_task_edits", record)
    saved, message = browser._save_index_changes()

    assert saved is True
    assert message == "Saved changes to projects.org"
    assert recorded == [(theirs, merged, index)]
    assert index.read_text(encoding="utf-8") == merged
    assert not browser.index_buffer.autosave_path.exists()


def test_project_browser_conflict_reload_discards_only_local_edit(
    tmp_path: Path,
) -> None:
    # The conflict screen can reload Theirs without ever overwriting it first.
    registry = tmp_path / "registry"
    project = tmp_path / "src" / "alpha"
    write(project / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
    register(registry, "alpha", project)
    index = registry / manager.PROJECTS_INDEX_NAME
    base = manager.PROJECTS_INDEX_HEADER + "* alpha\nAlpha.\n"
    index.write_text(base, encoding="utf-8")
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)
    assert browser.index_buffer is not None

    class FakeSession:
        def __init__(self, current_view) -> None:
            self.views = [current_view]
            self.message = ""

        @property
        def current_view(self):
            return self.views[-1]

        def replace_view(self, replacement) -> None:
            self.views[-1] = replacement

        def push_view(self, view) -> None:
            self.views.append(view)

        def pop_view(self, message: str | None = None) -> None:
            self.views.pop()
            if message:
                self.message = message

        def set_transient_message(self, message: str) -> None:
            self.message = message

        def set_outcome(self, message: str) -> None:
            raise AssertionError(f"conflicting save reported success: {message}")

    session = FakeSession(browser.view())
    session.current_view.on_result(session, menu.MenuResult("priority_up", 0))
    ours = browser.index_buffer.read()
    auto = browser.index_buffer.autosave_path.read_text(encoding="utf-8")
    theirs = base.replace("* alpha", "* [#A] alpha")
    index.write_text(theirs, encoding="utf-8")

    session.current_view.on_result(session, menu.MenuResult("save", 0))

    assert session.current_view.title == "Merge conflict: alpha"
    assert browser.index_buffer.saved_text == base
    assert browser.index_buffer.read() == ours
    assert browser.index_buffer.undo_count == 1
    assert browser.index_buffer.autosave_path.read_text(encoding="utf-8") == auto
    assert index.read_text(encoding="utf-8") == theirs

    session.current_view.on_result(session, menu.MenuResult("select", 0))

    assert len(session.views) == 1
    assert session.message == "Reloaded projects.org; local edits discarded"
    assert browser.index_buffer.saved_text == theirs
    assert browser.index_buffer.read() == theirs
    assert browser.index_buffer.dirty is False
    assert browser.index_buffer.can_undo is False
    assert browser.index_conflicts == ()
    assert not browser.index_buffer.autosave_path.exists()
    assert index.read_text(encoding="utf-8") == theirs


def test_project_browser_final_preimage_check_blocks_second_external_change(
    tmp_path: Path, monkeypatch
) -> None:
    # A second disk write after reconciliation must still defeat the final save.
    registry = tmp_path / "registry"
    for name in ("alpha", "bravo"):
        project = tmp_path / "src" / name
        write(project / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
        register(registry, name, project)
    index = registry / manager.PROJECTS_INDEX_NAME
    base = manager.PROJECTS_INDEX_HEADER + "* alpha\nAlpha.\n* bravo\nBravo.\n"
    index.write_text(base, encoding="utf-8")
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)
    assert browser.index_buffer is not None
    ours = base.replace("* alpha", "* [#C] alpha")
    theirs = base.replace("* bravo", "* [#B] bravo")
    merged = theirs.replace("* alpha", "* [#C] alpha")
    second = theirs.replace("Bravo.", "Bravo changed again.")
    browser.index_buffer.apply_text(ours, description="Set alpha priority to C")
    index.write_text(theirs, encoding="utf-8")
    real_save = browser.index_buffer.save

    def raced_save() -> None:
        index.write_text(second, encoding="utf-8")
        real_save()

    monkeypatch.setattr(browser.index_buffer, "save", raced_save)

    saved, message = browser._save_index_changes()

    assert saved is False
    assert "changed" in message
    assert index.read_text(encoding="utf-8") == second
    assert browser.index_buffer.saved_text == theirs
    assert browser.index_buffer.read() == merged
    assert browser.index_buffer.dirty is True
    assert browser.index_buffer.undo_count == 1
    assert browser.index_buffer.autosave_path.read_text(encoding="utf-8") == merged
    assert browser.index_buffer.external_change is not None
    assert browser.index_buffer.external_change.text == second


def test_project_browser_poll_adopts_clean_index_and_refreshes_rows(
    tmp_path: Path,
) -> None:
    # A clean browser adopts an external metadata edit and redraws the same project.
    registry = tmp_path / "registry"
    project = tmp_path / "src" / "alpha"
    write(project / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
    register(registry, "alpha", project)
    index = registry / manager.PROJECTS_INDEX_NAME
    original = manager.PROJECTS_INDEX_HEADER + "* alpha\n"
    index.write_text(original, encoding="utf-8")
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)

    class FakeSession:
        def __init__(self, current_view) -> None:
            self.current_view = current_view
            self.message = ""

        def replace_view(self, replacement) -> None:
            self.current_view = replacement

        def set_transient_message(self, message: str) -> None:
            self.message = message

    session = FakeSession(browser.view())
    external = original.replace("* alpha", "* [#A] alpha")
    index.write_text(external, encoding="utf-8")

    browser._poll_index(session)

    assert browser.index_buffer is not None
    assert browser.index_buffer.saved_text == external
    assert browser.index_buffer.dirty is False
    assert session.current_view.rows[0].text.startswith("[A] alpha")
    assert session.current_view.selected_index == 0
    assert session.message == "Reloaded external projects.org changes"


@pytest.mark.skipif(
    projmgr.menu.Application is None,
    reason="prompt_toolkit not installed",
)
@pytest.mark.parametrize(
    ("exit_keys", "saved"),
    [("qy", True), ("q\x03qn", False)],
)
def test_project_browser_exit_resolves_buffered_priority(
    tmp_path: Path, exit_keys: str, saved: bool
) -> None:
    # The footer exit prompt should save with Y or cancel and later discard.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    registry = tmp_path / "registry"
    project = tmp_path / "src" / "alpha"
    write(project / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
    register(registry, "alpha", project)
    index = registry / manager.PROJECTS_INDEX_NAME
    original = manager.PROJECTS_INDEX_HEADER + "* alpha\n** Directories\n"
    index.write_text(original, encoding="utf-8")
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("\x1b[1;2A" + exit_keys)
            browser.run()

    expected = original.replace("* alpha", "* [#C] alpha") if saved else original
    assert index.read_text(encoding="utf-8") == expected
    assert browser.index_buffer is not None
    assert browser.index_buffer.dirty is False
    assert not browser.index_buffer.autosave_path.exists()


@pytest.mark.skipif(
    projmgr.menu.Application is None,
    reason="prompt_toolkit not installed",
)
def test_project_browser_global_exit_saves_metadata_workspace_draft(
    tmp_path: Path,
) -> None:
    # Global Y should apply an active metadata field before saving projects.org.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    registry = tmp_path / "registry"
    project = tmp_path / "src" / "alpha"
    write(project / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
    register(registry, "alpha", project)
    index = registry / manager.PROJECTS_INDEX_NAME
    index.write_text(
        manager.PROJECTS_INDEX_HEADER + "* alpha\n** Directories\n",
        encoding="utf-8",
    )
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Open metadata, edit Description without applying, then save and exit.
            pin.send_text("m\rProject note\x18y")
            browser.run()

    assert ":DESCRIPTION: Project note" in index.read_text(encoding="utf-8")
    assert browser.index_buffer is not None
    assert browser.index_buffer.dirty is False
    assert not browser.index_buffer.autosave_path.exists()


@pytest.mark.skipif(
    projmgr.menu.Application is None,
    reason="prompt_toolkit not installed",
)
def test_project_browser_global_exit_saves_index_then_task_file(
    tmp_path: Path,
) -> None:
    # One Y should preflight and save both retained ptui buffers in fixed order.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    registry = tmp_path / "registry"
    project = tmp_path / "src" / "alpha"
    task_file = write(project / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
    register(registry, "alpha", project)
    index = registry / manager.PROJECTS_INDEX_NAME
    index.write_text(
        manager.PROJECTS_INDEX_HEADER + "* alpha\n** Directories\n",
        encoding="utf-8",
    )
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Buffer project priority, enter tasks, buffer state, then save both.
            pin.send_text("\x1b[1;2A\r\x1b[1;2C\x18y")
            browser.run()

    assert "* [#C] alpha" in index.read_text(encoding="utf-8")
    assert "** DONE t0001 Work" in task_file.read_text(encoding="utf-8")
    assert browser.session is not None
    assert browser.session.final_message == (
        "Saved changes to projects.org · Saved changes to tasks.org"
    )


@pytest.mark.skipif(
    projmgr.menu.Application is None,
    reason="prompt_toolkit not installed",
)
def test_project_browser_exit_preflights_all_files_before_writing(
    tmp_path: Path,
) -> None:
    # A task conflict must block projects.org before the first ordered write.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    registry = tmp_path / "registry"
    project = tmp_path / "src" / "alpha"
    task_file = write(project / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
    register(registry, "alpha", project)
    index = registry / manager.PROJECTS_INDEX_NAME
    index.write_text(
        manager.PROJECTS_INDEX_HEADER + "* alpha\n** Directories\n",
        encoding="utf-8",
    )
    original_index = index.read_text(encoding="utf-8")
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)
    assert browser.index_buffer is not None
    revised = manager.change_project_priority(
        browser.index_buffer.read(), "alpha", "A"
    )
    assert revised is not None
    browser.index_buffer.apply_text(revised, description="Raise alpha priority")

    task_buffer = taskui.OrgBuffer(task_file)
    task_buffer.apply(
        ["* Tasks", "** DONE t0001 Work"],
        description="Complete t0001",
    )
    external_task = "* Tasks\n** TODO t0001 Changed elsewhere\n"
    task_file.write_text(external_task, encoding="utf-8")
    project_record = manager.discover_projects(registry)[0]
    controller = taskui.InteractiveTaskController(
        project_record,
        task_buffer,
        include_done=True,
    )

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            session = menu.InlineMenuSession(
                browser.view(),
                exit_name="ptui",
                exit_concerns=browser._exit_concerns,
            )
            browser.session = session
            browser.task_controller = controller
            controller.session = session
            session.request_exit()
            assert "Save 2 modified files?" in str(session._render_footer())
            # Y fails task preflight before writes; the next request discards both.
            pin.send_text("y\x18n")
            session.run()

    assert index.read_text(encoding="utf-8") == original_index
    assert task_file.read_text(encoding="utf-8") == external_task
    assert browser.index_buffer.dirty is False
    assert task_buffer.dirty is False


@pytest.mark.skipif(
    projmgr.menu.Application is None,
    reason="prompt_toolkit not installed",
)
def test_project_browser_recovery_is_a_child_of_clean_exit(tmp_path: Path) -> None:
    # Resolving recovery should return to the list before clean root exit.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    registry = tmp_path / "registry"
    project = tmp_path / "src" / "alpha"
    write(project / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
    register(registry, "alpha", project)
    index = registry / manager.PROJECTS_INDEX_NAME
    original = manager.PROJECTS_INDEX_HEADER + "* alpha\n** Directories\n"
    index.write_text(original, encoding="utf-8")
    autosave = taskui.autosave_path_for(index)
    autosave.write_text(original.replace("* alpha", "* [#A] alpha"), encoding="utf-8")
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("jj\rq")
            browser.run()

    assert not autosave.exists()
    assert index.read_text(encoding="utf-8") == original
    assert browser.session is not None
    assert browser.session.final_message == (
        "Discarded recovery data in #projects.org#"
    )


@pytest.mark.skipif(
    projmgr.menu.Application is None,
    reason="prompt_toolkit not installed",
)
def test_view_options_screen_is_driven_by_the_real_key_bindings(
    tmp_path: Path,
) -> None:
    # v, then the workspace's own navigate/edit modes, then back to the list.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    registry = tmp_path / "registry"
    for name in ("alpha", "bravo"):
        project = tmp_path / "src" / name
        write(project / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
        register(registry, name, project)
    index = registry / manager.PROJECTS_INDEX_NAME
    index.write_text(
        manager.PROJECTS_INDEX_HEADER + "* [#A] bravo\n* alpha\n",
        encoding="utf-8",
    )
    original = index.read_text(encoding="utf-8")
    browser = projmgr._ProjectBrowser(registry, "~/registry", include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text(
                "v"          # open the view options screen
                "j"          # navigation mode: move to the Order field
                "\r"         # Enter begins changing it
                "\x1b[C"     # Right: Priority -> Alphabetical
                "\r"         # Enter finishes changing it
                "q"          # leave the screen
                "q"          # leave the navigator
            )
            browser.run()

    assert browser.view_state.sort == manager.PROJECT_SORT_ALPHABETICAL
    assert browser.view_state.filter == "all"
    # A view is not an edit: nothing was written on the way through.
    assert index.read_text(encoding="utf-8") == original
    assert browser.index_buffer is not None
    assert browser.index_buffer.dirty is False


def test_projmgr_navigator_dashboard_keeps_the_location(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The numbered table has no highlight, so its rows carry the location."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    registry.mkdir(parents=True)
    project = tmp_path / "src" / "shown"
    write(project / "todo.org", "* Tasks\n** TODO t0001 One\n")
    register(registry, "shown", project)

    entry = manager.discover_projects(registry)[0]
    # The picker delegates the location to its summary line; the table cannot.
    assert projmgr.NAVIGATOR.detail(entry) == "1 open"
    row = projmgr.NAVIGATOR.dashboard()(entry)
    assert str(project) in row
    assert "(1 open)" in row


def test_menu_right_aligned_context_yields_to_the_left(tmp_path: Path) -> None:
    """The registry is the expendable half; a location must never be pushed off."""
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from ortasklib import menu

    view = menu.MenuView(
        rows=[menu.MenuRow(1, "PROJ", "a")],
        on_result=lambda session, result: None,
        title="Project navigator",
        title_right="Registry: ~/projects",
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):  # 80 columns
            session = menu.InlineMenuSession(view)
            assert session._right_gap("Project navigator", view.title_right) == 43
            # No tail at all, and a tail that cannot fit, both render alone.
            assert session._right_gap("Project navigator", "") is None
            assert session._right_gap("x" * 70, view.title_right) is None

            header = "".join(text for _, text in session._render_header())
            title_line = header.split("\n")[0]
            assert len(title_line) == 80
            assert title_line.startswith("Project navigator")
            assert title_line.endswith("Registry: ~/projects")


def test_menu_dashboards_escape_rich_markup(capsys) -> None:
    # Cell text is data. Rich reads bracketed notes and "[#A]" as console markup
    # and drops what it cannot resolve, which must not silently eat either.
    from ortasklib import menu

    menu.print_project_dashboard(
        "Change directory",
        "~/projects",
        [menu.ProjectRow(1, "myproj", "~/src/myproj  [literal]")],
        "Directories",
    )
    assert "[literal]" in capsys.readouterr().out

    menu.print_task_dashboard(
        "Tasks", Path("x.org"), [menu.MenuRow(1, "TODO", "Fix [#A] handling")]
    )
    assert "[#A]" in capsys.readouterr().out


def test_projmgr_rm_removes_only_the_registry_entry(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))

    project = tmp_path / "src" / "doomed"
    org_file = write(project / "tasks.org", "* Tasks\n** TODO t0001 Survive\n")
    register(registry, "doomed", project)

    # A real file in the entry is data that lives nowhere else.
    private = registry / "doomed" / manager.DIRECTORIES_PRIVATE_NAME
    private.write_text("* Directories\n", encoding="utf-8")

    args = argparse.Namespace(name="doomed", registry=None, force=False, dry_run=False)
    assert projmgr.cmd_rm(args) == 1
    assert (registry / "doomed").is_dir()
    capsys.readouterr()

    args.force = True
    assert projmgr.cmd_rm(args) == 0
    capsys.readouterr()
    assert not (registry / "doomed").exists()
    assert org_file.read_text(encoding="utf-8") == "* Tasks\n** TODO t0001 Survive\n"
    assert project.is_dir()


def test_projmgr_doctor_reports_problems_and_exits_two(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))

    healthy = tmp_path / "src" / "healthy"
    write(healthy / "tasks.org", "* Tasks\n** TODO t0001 Fine\n")
    register(registry, "healthy", healthy)
    (registry / manager.PROJECTS_INDEX_NAME).write_text(
        manager.PROJECTS_INDEX_HEADER, encoding="utf-8"
    )

    args = argparse.Namespace(registry=None, dry_run=True, force=False)
    assert projmgr.cmd_repair(args) == 0
    assert "no problems found" in capsys.readouterr().out

    (registry / "gone").mkdir()
    (registry / "gone" / "gone").symlink_to(tmp_path / "src" / "gone")
    write(registry / "docs" / "notes.org", "* Notes\n")

    assert projmgr.cmd_repair(args) == 2
    out = capsys.readouterr().out
    assert "problem: gone: broken project link" in out
    assert "note: docs: not a project entry, ignored" in out

    # A stale task-file link would otherwise read as "no task file".
    (registry / "healthy" / "tasks.org").unlink()
    (registry / "healthy" / "tasks.org").symlink_to(tmp_path / "src" / "vanished.org")

    assert projmgr.cmd_repair(args) == 2
    out = capsys.readouterr().out
    assert "problem: healthy: dangling link tasks.org -> " in out


def test_projmgr_repair_reports_registry_index_states(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # Repair diagnoses missing, mixed, unreadable, stale, and ambiguous indexes.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    args = argparse.Namespace(registry=str(registry), dry_run=True, force=False)
    index = registry / manager.PROJECTS_INDEX_NAME

    index.unlink()
    assert projmgr.cmd_repair(args) == 2
    assert "problem: registry not migrated; run pmgr migrate" in capsys.readouterr().out

    index.write_text(
        manager.PROJECTS_INDEX_HEADER
        + "* myproj\n** Directories\n** Directories\n"
        + "* vanished\n** Directories\n",
        encoding="utf-8",
    )
    assert projmgr.cmd_repair(args) == 2
    out = capsys.readouterr().out
    assert "duplicate Directories heading for project 'myproj'" in out
    assert "stale project section 'vanished'" in out

    index.write_text("* myproj\n* MYPROJ\n", encoding="utf-8")
    assert projmgr.cmd_repair(args) == 2
    assert "duplicate project heading for 'myproj'" in capsys.readouterr().out

    index.unlink()
    index.mkdir()
    assert projmgr.cmd_repair(args) == 2
    assert "cannot read" in capsys.readouterr().out
    index.rmdir()

    index.write_text(manager.PROJECTS_INDEX_HEADER, encoding="utf-8")
    legacy = registry / "myproj" / manager.DIRECTORIES_PRIVATE_NAME
    legacy.write_text("* Directories\n", encoding="utf-8")
    assert projmgr.cmd_repair(args) == 2
    assert "registry migration incomplete" in capsys.readouterr().out


def _cdproj_registry(tmp_path: Path, monkeypatch, capsys, org_text: str) -> tuple:
    """Register one project and return ``(registry, project_dir)``."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))

    project = tmp_path / "myproj"
    write(project / "TODO.org", org_text)
    assert projmgr.cmd_add(_add_args(project, name="myproj")) == 0
    capsys.readouterr()
    (registry / manager.PROJECTS_INDEX_NAME).write_text(
        manager.PROJECTS_INDEX_HEADER, encoding="utf-8"
    )
    return registry, project


def _write_private_index(
    registry: Path, entries: str, project: str = "myproj"
) -> Path:
    """Write one migrated private directory section for a cdproj fixture."""
    index = registry / manager.PROJECTS_INDEX_NAME
    index.write_text(
        manager.PROJECTS_INDEX_HEADER
        + f"* {project}\n"
        + "** Directories\n"
        + entries,
        encoding="utf-8",
    )
    return index


def _set_dirs_args(registry: Path, directories: list[str], **kw) -> argparse.Namespace:
    base = dict(
        registry=str(registry),
        directories=directories,
        project="myproj",
        stdin=False,
        missing=None,
        dry_run=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_set_dirs_replaces_only_selected_directory_section(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # The canonical rewrite may normalize its section but no surrounding byte.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    home = tmp_path / "home"
    new = home / "work"
    old = home / "old"
    shared = tmp_path / "shared"
    for path in (new, old, shared):
        path.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    index = registry / manager.PROJECTS_INDEX_NAME
    original = (
        "#+TITLE: Projects\n\n"
        "* other\n"
        "** Directories\n"
        "   - /other/unchanged\n\n"
        "* myproj\n"
        "Project prose stays.\n"
        "** Directories\n"
        f"*** file:{old}\n"
        f"*** file:{shared}\n"
        "# section comment may be normalized\n"
        "** Notes\n"
        "Keep this note byte-for-byte.\n"
        "* Tasks\n"
        "** TODO t0001 Registry task\n"
    )
    index.write_text(original, encoding="utf-8")
    old_lookup = orglib.parse(original).directories("myproj")
    assert old_lookup.section is not None

    args = _set_dirs_args(
        registry,
        [str(new), str(shared), str(new)],
        missing="remove",
    )
    assert projmgr.cmd_set_dirs(args) == 0

    revised = index.read_text(encoding="utf-8")
    new_lookup = orglib.parse(revised).directories("myproj")
    assert new_lookup.section is not None
    old_span = old_lookup.section.span
    new_span = new_lookup.section.span
    assert revised[:new_span.start] == original[:old_span.start]
    assert revised[new_span.end:] == original[old_span.end:]
    assert revised[new_span.start:new_span.end] == (
        "** Directories\n"
        "   - ~/work\n"
        f"   - {shared}\n"
    )
    assert "directories updated" in capsys.readouterr().out


def test_cdproj_resolves_the_same_stack_with_a_priority_cookie(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # ptui will write "* [#A] myproj"; that must not detach the private stack,
    # which is now the index's alone to supply (t0026.3, t0036).
    from ortasklib import menu

    registry, project = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    (project / "docs").mkdir()
    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(menu, "prompt_text", lambda prompt: "1")
    index = registry / manager.PROJECTS_INDEX_NAME

    stacks = []
    for heading in ("* myproj", "* [#A] myproj"):
        index.write_text(
            manager.PROJECTS_INDEX_HEADER
            + f"{heading}\n** Directories\n"
            + f"   - {project}\n   - {project / 'docs'}\n",
            encoding="utf-8",
        )
        out_file = tmp_path / "out.txt"
        assert projmgr.cmd_cdproj(
            argparse.Namespace(registry=str(registry), out=str(out_file))
        ) == 0
        stacks.append(out_file.read_text(encoding="utf-8").splitlines())
    capsys.readouterr()

    assert stacks[0] == [
        str(project.resolve()),
        str((project / "docs").resolve()),
    ]
    assert stacks[1] == stacks[0]


def test_set_dirs_preserves_priority_cookie_and_property_drawer(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # ptui metadata shares this section (t0035); rewriting the stack may not
    # disturb the heading's cookie or the drawer above the section.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    home = tmp_path / "home"
    new = home / "work"
    new.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    index = registry / manager.PROJECTS_INDEX_NAME
    preamble = (
        "#+TITLE: Projects\n\n"
        "* [#A] myproj\n"
        ":PROPERTIES:\n"
        ":DESCRIPTION: Org-backed task and project tools\n"
        ":END:\n"
    )
    index.write_text(preamble + "** Directories\n   - /old/one\n", encoding="utf-8")

    assert projmgr.cmd_set_dirs(
        _set_dirs_args(registry, [str(new)], missing="remove")
    ) == 0
    capsys.readouterr()

    assert index.read_text(encoding="utf-8") == (
        preamble + "** Directories\n   - ~/work\n"
    )


def test_migrate_leaves_a_cookied_index_untouched(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # A migrated registry whose headings carry cookies is already migrated;
    # re-running migrate must not rewrite a byte of it.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    index = registry / manager.PROJECTS_INDEX_NAME
    original = (
        manager.PROJECTS_INDEX_HEADER
        + "* [#B] myproj\n** Directories\n   - ~/src/myproj\n"
    )
    index.write_text(original, encoding="utf-8")

    assert projmgr.cmd_migrate(
        argparse.Namespace(registry=str(registry), dry_run=False)
    ) == 0
    capsys.readouterr()
    assert index.read_text(encoding="utf-8") == original


def test_doctor_accepts_a_cookied_project_section(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # doctor normalizes headings its own way; it must use orglib's rule, or a
    # priority cookie reads as part of the name and the section looks stale.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    index = registry / manager.PROJECTS_INDEX_NAME
    index.write_text(
        manager.PROJECTS_INDEX_HEADER + "* [#A] myproj\n** Directories\n",
        encoding="utf-8",
    )
    assert projmgr.cmd_repair(argparse.Namespace(registry=str(registry), dry_run=True, force=False)) == 0
    assert "stale project section" not in capsys.readouterr().out

    # A section that really matches nothing is still reported, by its name.
    index.write_text(
        manager.PROJECTS_INDEX_HEADER
        + "* [#A] myproj\n** Directories\n* [#B] ghost\n",
        encoding="utf-8",
    )
    assert projmgr.cmd_repair(argparse.Namespace(registry=str(registry), dry_run=True, force=False)) == 2
    assert "stale project section 'ghost'" in capsys.readouterr().out


def test_read_project_metadata_joins_the_index_onto_registered_projects(
    tmp_path: Path,
) -> None:
    # The registry decides membership; the index only decorates it (t0035.1).
    registry = tmp_path / "registry"
    for name in ("described", "bare", "broken"):
        project = tmp_path / name
        write(project / "todo.org", "* Tasks\n** TODO t0001 task\n")
        register(registry, name, project)
    (registry / manager.PROJECTS_INDEX_NAME).write_text(
        manager.PROJECTS_INDEX_HEADER
        + "* [#A] described\n"
        ":PROPERTIES:\n"
        ":DESCRIPTION: A described project\n"
        ":TASK_FILE: ~/src/described/todo.org\n"
        ":END:\n"
        "* bare\n"
        "* broken\n"
        "* broken\n"
        "* ghost\n",
        encoding="utf-8",
    )
    projects = manager.discover_projects(registry)
    metadata = manager.read_project_metadata(registry, projects)

    # Every registered project has an entry; an index-only section has none.
    assert set(metadata) == {"described", "bare", "broken"}
    assert metadata["described"] == manager.ProjectMetadata(
        priority="A",
        description="A described project",
        task_file="~/src/described/todo.org",
    )
    assert metadata["bare"] == manager.ProjectMetadata()
    # One unreadable section warns on its own project and spares the others.
    assert "duplicate project heading" in metadata["broken"].warning
    assert metadata["described"].warning is None


def test_read_project_metadata_tolerates_a_registry_with_no_index(
    tmp_path: Path,
) -> None:
    # An unmigrated registry is doctor's business, not a reason to refuse rows.
    registry = tmp_path / "registry"
    project = tmp_path / "solo"
    write(project / "todo.org", "* Tasks\n** TODO t0001 task\n")
    register(registry, "solo", project)
    projects = manager.discover_projects(registry)

    assert manager.read_project_metadata(registry, projects) == {
        "solo": manager.ProjectMetadata()
    }


def test_read_project_metadata_reports_a_repeated_property(tmp_path: Path) -> None:
    # First-wins is a silent choice; say that the file disagrees with itself.
    registry = tmp_path / "registry"
    project = tmp_path / "twice"
    write(project / "todo.org", "* Tasks\n** TODO t0001 task\n")
    register(registry, "twice", project)
    (registry / manager.PROJECTS_INDEX_NAME).write_text(
        manager.PROJECTS_INDEX_HEADER
        + "* twice\n:PROPERTIES:\n:DESCRIPTION: first\n:DESCRIPTION: second\n:END:\n",
        encoding="utf-8",
    )
    entry = manager.read_project_metadata(
        registry, manager.discover_projects(registry)
    )["twice"]

    assert entry.description == "first"
    assert entry.warning == "recorded more than once: DESCRIPTION"


def test_change_project_priority_preserves_the_project_section_source() -> None:
    # Priority editing changes only the cookie, preserving CRLF, tags, and content.
    original = (
        "#+TITLE: Projects\r\n\r\n"
        "* [#C] sample  :work:tools:\r\n"
        ":PROPERTIES:\r\n:DESCRIPTION: Keep me\r\n:CUSTOM: exact\r\n:END:\r\n"
        "Project prose.\r\n** Directories\r\n   - ~/src/sample\r\n"
        "* other\r\nOther prose.\r\n"
    )

    raised = manager.change_project_priority(original, "sample", "B")
    assert raised == original.replace("* [#C] sample", "* [#B] sample", 1)
    assert manager.change_project_priority(raised, "sample", None) == original.replace(
        "* [#C] sample", "* sample", 1
    )


def test_change_project_priority_adds_only_a_missing_project_section() -> None:
    # A migrated index may lack a project section; setting priority adds the minimum.
    original = "#+TITLE: Projects\n\n* other\nOther prose.\n"
    revised = manager.change_project_priority(original, "sample", "C")

    assert revised == original + "* [#C] sample\n"
    assert manager.change_project_priority(revised, "sample", "C") is None
    assert manager.change_project_priority(original, "sample", None) is None


def test_change_project_priority_rejects_malformed_cookie() -> None:
    # Cookie-like syntax must not cause a second project section to be appended.
    text = "#+TITLE: Projects\n\n* [#AB] sample :work:\nKeep me.\n"

    with pytest.raises(ValueError, match="malformed priority cookie.*line 3"):
        manager.change_project_priority(text, "sample", "C")

    with pytest.raises(ValueError, match="malformed priority cookie.*line 3"):
        manager.change_project_priority(text.replace("[#AB]", "[#A"), "sample", "C")


def test_change_project_property_preserves_unrelated_project_source() -> None:
    # Property edits retain CRLF, unknown fields, prose, tags, and sibling sections.
    original = (
        "#+TITLE: Projects\r\n\r\n"
        "* [#B] sample :work:\r\n"
        ":PROPERTIES:\r\n:DESCRIPTION: Old\r\n:CUSTOM: exact\r\n:END:\r\n"
        "Project prose.\r\n** Notes\r\nKeep.\r\n"
        "* other\r\nOther prose.\r\n"
    )

    described = manager.change_project_property(
        original, "sample", "DESCRIPTION", "New"
    )
    assert described is not None
    mirrored = manager.change_project_property(
        described, "sample", "TASK_FILE", "~/src/sample/tasks.org"
    )
    assert mirrored is not None
    cleared = manager.change_project_property(
        mirrored, "sample", "DESCRIPTION", None
    )
    assert cleared is not None

    assert ":DESCRIPTION:" not in cleared
    assert ":CUSTOM: exact\r\n:TASK_FILE: ~/src/sample/tasks.org\r\n" in cleared
    assert "Project prose.\r\n** Notes\r\nKeep.\r\n" in cleared
    assert cleared.endswith("* other\r\nOther prose.\r\n")


def test_change_project_property_rejects_ambiguous_or_multiline_values() -> None:
    # A metadata writer must refuse repeated fields and non-Org single-line values.
    repeated = (
        "* sample\n:PROPERTIES:\n:DESCRIPTION: one\n"
        ":DESCRIPTION: two\n:END:\n"
    )

    with pytest.raises(ValueError, match="repeated DESCRIPTION"):
        manager.change_project_property(repeated, "sample", "DESCRIPTION", "new")
    with pytest.raises(ValueError, match="single line"):
        manager.change_project_property("* sample\n", "sample", "DESCRIPTION", "a\nb")
    with pytest.raises(ValueError, match="malformed priority cookie"):
        manager.change_project_property(
            "* [#AB] sample\n", "sample", "DESCRIPTION", "new"
        )


def test_change_project_directories_is_a_bounded_in_memory_writer(
    tmp_path: Path,
) -> None:
    # Directory editing replaces one child section and leaves adjacent source exact.
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    original = (
        "#+TITLE: Projects\r\n\r\n"
        "* [#A] sample :work:\r\n:PROPERTIES:\r\n:CUSTOM: yes\r\n:END:\r\n"
        "Prose.\r\n** Directories\r\n   - old\r\n"
        "* other\r\nKeep byte-for-byte.\r\n"
    )

    revised = manager.change_project_directories(
        original, "sample", [first, second, first]
    )
    assert revised is not None

    lookup = orglib.parse(revised).directories("sample")
    assert lookup.section is not None
    assert lookup.section.entries == (str(first), str(second))
    assert ":PROPERTIES:\r\n:CUSTOM: yes\r\n:END:\r\nProse.\r\n" in revised
    assert revised.endswith("* other\r\nKeep byte-for-byte.\r\n")


def test_shift_project_priority_clamps_at_both_ends() -> None:
    # Shift-Up follows unset/C/B/A while Shift-Down follows the reverse path.
    assert manager.shift_project_priority(None, 1) == "C"
    assert manager.shift_project_priority("C", 1) == "B"
    assert manager.shift_project_priority("B", 1) == "A"
    assert manager.shift_project_priority("A", 1) == "A"
    assert manager.shift_project_priority("A", -1) == "B"
    assert manager.shift_project_priority("C", -1) is None
    assert manager.shift_project_priority(None, -1) is None
    with pytest.raises(ValueError, match="unsupported project priority"):
        manager.shift_project_priority("1", 1)


def test_project_index_merge_replays_local_edit_on_external_canvas() -> None:
    # Disjoint local/external edits preserve external preamble, order, add, and delete.
    header = "#+TITLE: Projects\n#+NOTE: base\n\n"
    external_header = "#+TITLE: Projects\n#+NOTE: externally revised\n\n"
    alpha = (
        "* alpha :work:\n:PROPERTIES:\n:DESCRIPTION: Alpha\n:END:\n"
        "Alpha prose.\n** Directories\n   - ~/src/alpha\n"
    )
    alpha_ours = alpha.replace("* alpha", "* [#A] alpha", 1)
    beta = "* beta\nBeta prose.\n"
    beta_theirs = "* beta\nBeta prose changed externally.\n"
    delta = "* delta\nDeleted externally.\n"
    gamma = "* gamma\nAdded externally.\n"
    base = header + alpha + beta + delta
    ours = header + alpha_ours + beta + delta
    theirs = external_header + beta_theirs + alpha + gamma
    expected = external_header + beta_theirs + alpha_ours + gamma

    plan = manager.plan_project_index_merge(base, ours, theirs)

    assert plan.can_merge is True
    assert plan.merged_text == expected
    assert plan.replayed_projects == ("alpha",)
    assert set(plan.absorbed_projects) == {"alpha", "beta", "delta", "gamma"}
    assert plan.conflicts == ()


def test_project_index_merge_accepts_identical_local_and_external_edit() -> None:
    # The same edit in Ours and Theirs is already merged and is not a conflict.
    base = "#+TITLE: Projects\n\n* alpha\nAlpha prose.\n"
    changed = base.replace("* alpha", "* [#B] alpha")

    plan = manager.plan_project_index_merge(base, changed, changed)

    assert plan.can_merge is True
    assert plan.merged_text == changed
    assert plan.replayed_projects == ("alpha",)
    assert plan.absorbed_projects == ("alpha",)


def test_project_index_merge_applies_multiple_replacements_back_to_front() -> None:
    # Two length-changing local edits survive an external reorder and third edit.
    header = "#+TITLE: Projects\n\n"
    alpha = "* alpha\nAlpha.\n"
    beta = "* beta\nBeta.\n"
    gamma = "* gamma\nGamma.\n"
    alpha_ours = alpha.replace("* alpha", "* [#A] alpha")
    gamma_ours = gamma.replace("* gamma", "* [#C] gamma")
    beta_theirs = beta.replace("Beta.", "Beta changed externally.")
    base = header + alpha + beta + gamma
    ours = header + alpha_ours + beta + gamma_ours
    theirs = header + gamma + beta_theirs + alpha

    plan = manager.plan_project_index_merge(base, ours, theirs)

    assert plan.merged_text == header + gamma_ours + beta_theirs + alpha_ours
    assert plan.replayed_projects == ("alpha", "gamma")


def test_project_index_merge_reports_same_section_and_delete_conflicts() -> None:
    # Differing edits to one section and deleting a locally edited section both refuse.
    base = "#+TITLE: Projects\n\n* alpha\nAlpha.\n* beta\nBeta.\n"
    ours = base.replace("* alpha", "* [#A] alpha")
    changed_theirs = base.replace("* alpha", "* [#B] alpha")

    changed = manager.plan_project_index_merge(base, ours, changed_theirs)
    deleted = manager.plan_project_index_merge(
        base,
        ours,
        "#+TITLE: Projects\n\n* beta\nBeta.\n",
    )

    assert changed.merged_text is None
    assert changed.conflicts[0].kind == "project_changed_both"
    assert changed.conflicts[0].project == "alpha"
    assert deleted.merged_text is None
    assert deleted.conflicts[0].kind == "external_project_deleted"
    assert deleted.conflicts[0].project == "alpha"


@pytest.mark.parametrize(
    ("base", "ours", "kind"),
    [
        (
            "#+TITLE: Projects\n\n* alpha\nAlpha.\n",
            "#+TITLE: Locally changed\n\n* alpha\nAlpha.\n",
            "local_outside_project",
        ),
        (
            "#+TITLE: Projects\n\n* alpha\nAlpha.\n",
            "#+TITLE: Projects\n\n* alpha\nAlpha.\n* beta\nBeta.\n",
            "local_project_added",
        ),
        (
            "#+TITLE: Projects\n\n* alpha\nAlpha.\n* beta\nBeta.\n",
            "#+TITLE: Projects\n\n* alpha\nAlpha.\n",
            "local_project_deleted",
        ),
        (
            "#+TITLE: Projects\n\n* alpha\nAlpha.\n* beta\nBeta.\n",
            "#+TITLE: Projects\n\n* beta\nBeta.\n* alpha\nAlpha.\n",
            "local_project_reordered",
        ),
        (
            "#+TITLE: Projects\n\n* alpha\nAlpha.\n* Tasks\n** TODO t0001 One\n",
            "#+TITLE: Projects\n\n* alpha\nAlpha.\n* Tasks\n** DONE t0001 One\n",
            "local_outside_project",
        ),
    ],
)
def test_project_index_merge_rejects_local_nonproject_changes(
    base: str, ours: str, kind: str
) -> None:
    # Only edits within an existing, uniquely named project section are replayable.
    plan = manager.plan_project_index_merge(base, ours, base)

    assert plan.merged_text is None
    assert kind in {conflict.kind for conflict in plan.conflicts}
    assert all(conflict.source == "ours" for conflict in plan.conflicts)


@pytest.mark.parametrize(
    ("source", "base", "ours", "theirs"),
    [
        (
            "base",
            "* Alpha\n* [#A] alpha :tag:\n",
            "* alpha\n",
            "* alpha\n",
        ),
        (
            "ours",
            "* alpha\n",
            "* alpha\n:PROPERTIES:\nnot a property\n:END:\n",
            "* alpha\n",
        ),
        (
            "theirs",
            "* alpha\n",
            "* alpha\n",
            "* alpha\n:PROPERTIES:\n:DESCRIPTION: unterminated\n",
        ),
        (
            "theirs",
            "* alpha\n",
            "* alpha\n",
            "* alpha\n** Directories\n   - /one\n** Directories\n   - /two\n",
        ),
        (
            "theirs",
            "* alpha\n",
            "* alpha\n",
            "* [#AB] alpha\n",
        ),
    ],
)
def test_project_index_merge_returns_structured_invalid_input(
    source: str, base: str, ours: str, theirs: str
) -> None:
    # Malformed or ambiguous input returns a source-labeled conflict, not an exception.
    plan = manager.plan_project_index_merge(base, ours, theirs)

    assert plan.can_merge is False
    assert plan.merged_text is None
    assert any(
        conflict.kind == "invalid_input" and conflict.source == source
        for conflict in plan.conflicts
    )


def test_project_index_merge_performs_no_file_io(monkeypatch) -> None:
    # Planning is pure: even disabled path and atomic-write APIs cannot affect it.
    def reject_io(*args, **kwargs):
        raise AssertionError("merge planner attempted file I/O")

    monkeypatch.setattr(Path, "read_text", reject_io)
    monkeypatch.setattr(Path, "write_text", reject_io)
    monkeypatch.setattr(core, "atomic_write", reject_io)
    base = "#+TITLE: Projects\n\n* alpha\nAlpha.\n"
    ours = base.replace("* alpha", "* [#C] alpha")

    plan = manager.plan_project_index_merge(base, ours, base)

    assert plan.can_merge is True
    assert plan.merged_text == ours


def test_set_dirs_noninteractive_default_keeps_missing_entries(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # Without a prompt, the recoverable default appends omitted existing paths.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    index = _write_private_index(registry, f"   - {old}\n")

    assert projmgr.cmd_set_dirs(_set_dirs_args(registry, [str(new)])) == 0

    lookup = orglib.parse(index.read_text(encoding="utf-8")).directories("myproj")
    assert lookup.section is not None
    assert lookup.section.entries == (str(new), str(old))
    assert "keeping 1 existing directory absent from input" in capsys.readouterr().err


def test_set_dirs_dry_run_reads_stdin_without_writing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # Stdin input is deduplicated and rendered, while dry-run preserves the index.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    directory = tmp_path / "stack entry"
    directory.mkdir()
    index = registry / manager.PROJECTS_INDEX_NAME
    original = index.read_text(encoding="utf-8")
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(f"{directory}\n{directory}\n")
    )

    args = _set_dirs_args(
        registry,
        [],
        stdin=True,
        missing="remove",
        dry_run=True,
    )
    assert projmgr.cmd_set_dirs(args) == 0

    assert index.read_text(encoding="utf-8") == original
    out = capsys.readouterr().out
    assert "[dry-run] myproj" in out
    assert f"   - {directory}" in out


def test_projmgr_set_dirs_cli_dispatches_to_index_writer(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # The public subcommand reaches the bounded writer with explicit arguments.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    directory = tmp_path / "stack"
    directory.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "projmgr.py"),
            "set-dirs",
            "--registry",
            str(registry),
            "--project",
            "myproj",
            "--missing",
            "remove",
            str(directory),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    index = registry / manager.PROJECTS_INDEX_NAME
    lookup = orglib.parse(index.read_text(encoding="utf-8")).directories("myproj")
    assert lookup.section is not None
    assert lookup.section.entries == (str(directory),)


def test_set_dirs_infers_project_and_rejects_ambiguous_match(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # PWD selects one resolved project root, never an arbitrary duplicate alias.
    registry, project = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    nested = project / "docs"
    nested.mkdir()
    monkeypatch.setenv("PWD", str(nested))
    args = _set_dirs_args(
        registry,
        [str(project)],
        project=None,
        missing="remove",
    )

    assert projmgr.cmd_set_dirs(args) == 0
    capsys.readouterr()
    index = registry / manager.PROJECTS_INDEX_NAME
    before = index.read_text(encoding="utf-8")

    outsider = tmp_path / "outsider"
    (outsider / ".git").mkdir(parents=True)
    monkeypatch.setenv("PWD", str(outsider))
    assert projmgr.cmd_set_dirs(args) == 1
    assert "no registered project matches" in capsys.readouterr().err
    assert index.read_text(encoding="utf-8") == before

    monkeypatch.setenv("PWD", str(nested))
    register(registry, "alias", project)
    assert projmgr.cmd_set_dirs(args) == 1
    assert "multiple registered projects match" in capsys.readouterr().err
    assert index.read_text(encoding="utf-8") == before


def test_set_dirs_requires_input_and_complete_migration(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # Empty/conflicting input and missing/mixed registry layouts never write.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    index = registry / manager.PROJECTS_INDEX_NAME
    original = index.read_text(encoding="utf-8")

    assert projmgr.cmd_set_dirs(_set_dirs_args(registry, [])) == 1
    assert "at least one directory is required" in capsys.readouterr().err
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert projmgr.cmd_set_dirs(
        _set_dirs_args(registry, [], stdin=True)
    ) == 1
    assert "at least one directory is required" in capsys.readouterr().err
    assert projmgr.cmd_set_dirs(
        _set_dirs_args(registry, [str(tmp_path)], stdin=True)
    ) == 1
    assert "--stdin cannot be combined" in capsys.readouterr().err
    assert index.read_text(encoding="utf-8") == original

    index.unlink()
    assert projmgr.cmd_set_dirs(
        _set_dirs_args(registry, [str(tmp_path)], missing="remove")
    ) == 1
    assert "registry not migrated; run pmgr migrate" in capsys.readouterr().err

    index.write_text(original, encoding="utf-8")
    legacy = registry / "myproj" / manager.DIRECTORIES_PRIVATE_NAME
    legacy.write_text("* Directories\n", encoding="utf-8")
    assert projmgr.cmd_set_dirs(
        _set_dirs_args(registry, [str(tmp_path)], missing="remove")
    ) == 1
    assert "registry migration incomplete" in capsys.readouterr().err
    assert index.read_text(encoding="utf-8") == original


def test_set_dirs_prompt_supports_remove_keep_and_cancel(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # Interactive subtraction offers each specified outcome, including no write.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    old = tmp_path / "old"
    new = tmp_path / "new"
    _write_private_index(registry, f"   - {old}\n")
    project = manager.discover_projects(registry)[0]
    plan = manager.plan_registry_directories_update(project, [new])
    args = _set_dirs_args(registry, [str(new)])

    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", Tty())
    monkeypatch.setattr(sys, "stdout", Tty())
    monkeypatch.setattr(menu, "prompt_text", lambda label: "remove")
    assert projmgr._set_dirs_keep_missing(args, plan) is False
    monkeypatch.setattr(menu, "prompt_text", lambda label: "keep")
    assert projmgr._set_dirs_keep_missing(args, plan) is True
    monkeypatch.setattr(menu, "prompt_text", lambda label: "cancel")
    assert projmgr._set_dirs_keep_missing(args, plan) is None


def test_registry_directories_update_rechecks_index_preimage(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # A concurrent index edit invalidates the plan before the atomic replacement.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    project = manager.discover_projects(registry)[0]
    plan = manager.plan_registry_directories_update(project, [tmp_path])
    index = registry / manager.PROJECTS_INDEX_NAME
    changed = plan.previous_index_text + "# concurrent edit\n"
    index.write_text(changed, encoding="utf-8")

    with pytest.raises(manager.RegistryIndexError, match="changed after"):
        manager.apply_registry_directories_update(plan, keep_missing=False)
    assert index.read_text(encoding="utf-8") == changed


def test_set_dirs_adds_missing_heading_after_project_prose(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # A missing section is inserted after project prose without rewriting it.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    directory = tmp_path / "stack"
    directory.mkdir()
    index = registry / manager.PROJECTS_INDEX_NAME
    original = (
        manager.PROJECTS_INDEX_HEADER
        + "* myproj\nKeep this prose.\n"
        + "* other\nOther bytes stay.\n"
    )
    index.write_text(original, encoding="utf-8")

    assert projmgr.cmd_set_dirs(
        _set_dirs_args(registry, [str(directory)], missing="remove")
    ) == 0

    assert index.read_text(encoding="utf-8") == (
        manager.PROJECTS_INDEX_HEADER
        + "* myproj\nKeep this prose.\n"
        + "** Directories\n"
        + f"   - {directory}\n"
        + "* other\nOther bytes stay.\n"
    )


def test_core_parse_directories() -> None:
    # No section at all is distinct from a section that lists nothing.
    assert core.parse_directories("* Tasks\n** TODO t0001 x\n") is None
    assert core.parse_directories("* Directories\n") == []

    text = (
        "* Directories\n"
        "# a comment\n"
        "\n"
        "** .\n"
        "** file:docs\n"
        "** [[/absolute/path]]\n"
        "** [[file:$SOME_VAR]]\n"
        "- ~/bullet\n"
        "plain/relative\n"
        "* Tasks\n"
        "** TODO t0001 not a directory\n"
    )
    assert core.parse_directories(text) == [
        ".",
        "docs",
        "/absolute/path",
        "$SOME_VAR",
        "~/bullet",
        "plain/relative",
    ]


def test_manager_resolve_directories(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CDPROJ_TEST_VAR", "from_env")
    root = tmp_path / "root"
    root.mkdir()
    resolved = manager.resolve_directories(
        [".", "docs", "/absolute/path", "$CDPROJ_TEST_VAR"], root
    )
    assert resolved == [
        root.resolve(),
        (root / "docs").resolve(),
        Path("/absolute/path"),
        (root / "from_env").resolve(),
    ]


def test_projmgr_cdproj_writes_only_directories(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from ortasklib import menu

    registry, project = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(menu, "prompt_text", lambda prompt: "1")

    # With no ``* Directories`` anywhere, the project root is the whole stack.
    out_file = tmp_path / "out1.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file))
    ) == 0
    assert out_file.read_text(encoding="utf-8").splitlines() == [
        str(project.resolve())
    ]

    monkeypatch.setenv("CDPROJ_TEST_VAR", "subdir_env")
    (project / "TODO.org").write_text(
        "* Tasks\n** TODO t0001 task\n\n"
        "* Directories\n# Comment line\n\n"
        "** .\n** file:docs\n** [[/absolute/path]]\n** [[file:$CDPROJ_TEST_VAR]]\n",
        encoding="utf-8",
    )
    out_file2 = tmp_path / "out2.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file2))
    ) == 0
    assert out_file2.read_text(encoding="utf-8").splitlines() == [
        str(project.resolve()),
        str((project / "docs").resolve()),
        "/absolute/path",
        str((project / "subdir_env").resolve()),
    ]


def test_projmgr_cdproj_requires_complete_migration(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # cdproj neither falls back before migration nor reads an incomplete layout.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    index = registry / manager.PROJECTS_INDEX_NAME
    index.unlink()
    out_file = tmp_path / "out.txt"
    args = argparse.Namespace(
        registry=str(registry), out=str(out_file), project="myproj"
    )

    assert projmgr.cmd_cdproj(args) == 1
    assert capsys.readouterr().err.strip() == (
        "cdproj: registry not migrated; run pmgr migrate"
    )
    assert not out_file.exists()

    index.write_text(manager.PROJECTS_INDEX_HEADER, encoding="utf-8")
    legacy = registry / "myproj" / manager.DIRECTORIES_PRIVATE_NAME
    legacy.write_text("* Directories\n** file:/legacy\n", encoding="utf-8")
    assert projmgr.cmd_cdproj(args) == 1
    assert "cdproj: registry migration incomplete" in capsys.readouterr().err
    assert not out_file.exists()


def test_cdproj_private_editor_initializes_index_section(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # Explicit private editing adds one bounded section and opens its heading.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    project = manager.discover_projects(registry)[0]
    candidate = manager.directory_candidates(project)[0]
    captured: dict[str, object] = {}

    class FakeSession:
        def set_transient_message(self, message: str) -> None:
            captured["error"] = message

        def suspend(self, callback, on_done) -> None:
            callback()
            on_done()

        def pop_view(self, message: str) -> None:
            captured["message"] = message

    def editor_argv(path: Path, line_num: int | None = None) -> list[str]:
        captured["path"] = path
        captured["line"] = line_num
        return ["editor"]

    monkeypatch.setattr(core, "editor_argv", editor_argv)
    monkeypatch.setattr(
        projmgr.subprocess, "run", lambda argv, check: captured.setdefault("argv", argv)
    )
    session = projmgr._CdprojSession("registry", [project], tmp_path / "out")
    session.edit_source(FakeSession(), project, candidate)

    index = registry / manager.PROJECTS_INDEX_NAME
    assert index.read_text(encoding="utf-8") == (
        manager.PROJECTS_INDEX_HEADER + "* myproj\n** Directories\n"
    )
    assert captured["path"] == index
    assert captured["line"] == 3
    assert "error" not in captured


def test_cdproj_private_editor_refuses_a_stale_index(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # The external-editor entrance must not overwrite a newer projects.org revision.
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    project = manager.discover_projects(registry)[0]
    candidate = manager.directory_candidates(project)[0]
    session = projmgr._CdprojSession("registry", [project], tmp_path / "out")
    index = registry / manager.PROJECTS_INDEX_NAME
    external = index.read_text(encoding="utf-8") + "* external\n"
    index.write_text(external, encoding="utf-8")
    captured: dict[str, object] = {}

    class FakeSession:
        def set_transient_message(self, message: str) -> None:
            captured["error"] = message

        def suspend(self, callback, on_done) -> None:
            raise AssertionError("stale index opened in editor")

    monkeypatch.setattr(core, "editor_argv", lambda path, line=None: ["editor"])
    session.edit_source(FakeSession(), project, candidate)

    assert "changed since the editing buffer was loaded" in str(captured["error"])
    assert index.read_text(encoding="utf-8") == external


def test_projmgr_cdproj_never_edits_the_task_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # cdproj resolves and reports; Org content belongs to ortask.py.
    from ortasklib import menu

    original = "* Tasks\n** TODO t0001 task\n"
    registry, project = _cdproj_registry(tmp_path, monkeypatch, capsys, original)
    prompts: list[str] = []
    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(
        menu,
        "prompt_text",
        lambda prompt: prompts.append(prompt) or "1",
    )

    out_file = tmp_path / "out.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file))
    ) == 0
    assert (project / "TODO.org").read_text(encoding="utf-8") == original
    assert prompts == ["* = custom · number, Esc/q=cancel"]


def test_projmgr_cdproj_private_list_wins_outright(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The private list is the stack; the project's list is only checked."""
    from ortasklib import menu

    registry, project = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    _write_private_index(registry, "*** file:/private/one\n")

    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(menu, "prompt_text", lambda prompt: "1")

    # Only the private file defines a stack: nothing to compare, nothing to say.
    out_file = tmp_path / "out1.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file))
    ) == 0
    assert out_file.read_text(encoding="utf-8").splitlines() == ["/private/one"]
    assert capsys.readouterr().err == ""

    # The private file must not be mistaken for the project's task file.
    projects = manager.discover_projects(registry)
    assert manager.canonical_org_file(projects[0]).name == "TODO.org"

    # Two sources: private is listed first and both are reachable.
    (project / "TODO.org").write_text(
        "* Tasks\n** TODO t0001 task\n* Directories\n** file:/shared/one\n",
        encoding="utf-8",
    )
    sources = manager.directory_sources(projects[0])
    assert [s.label for s in sources] == ["private", "project"]

    # The shared entry is reported, not merged in. Exit status is unaffected.
    out_file2 = tmp_path / "out2.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file2))
    ) == 0
    assert out_file2.read_text(encoding="utf-8").splitlines() == ["/private/one"]
    assert capsys.readouterr().err.strip() == (
        "myproj: in TODO.org but not projects.org: /shared/one"
    )


def test_projmgr_cdproj_private_list_sets_the_order(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Order comes from the private list, not from the project's."""
    from ortasklib import menu

    registry, _ = _cdproj_registry(
        tmp_path,
        monkeypatch,
        capsys,
        "* Tasks\n** TODO t0001 task\n"
        "* Directories\n** file:/a\n** file:/b\n** file:/c\n",
    )
    _write_private_index(
        registry, "*** file:/c\n*** file:/b\n*** file:/a\n"
    )
    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(menu, "prompt_text", lambda prompt: "1")

    out_file = tmp_path / "out.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file))
    ) == 0
    assert out_file.read_text(encoding="utf-8").splitlines() == ["/c", "/b", "/a"]
    # Same set, so nothing is missing and nothing is said.
    assert capsys.readouterr().err == ""


def test_projmgr_cdproj_compares_resolved_paths(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """"docs", "./docs", and the absolute form are one entry, not three."""
    from ortasklib import menu

    registry, project = _cdproj_registry(
        tmp_path,
        monkeypatch,
        capsys,
        "* Tasks\n** TODO t0001 task\n"
        "* Directories\n** ./docs\n** docs\n",
    )
    _write_private_index(
        registry,
        f"*** {project}/docs\n*** file:/private/only\n",
    )
    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(menu, "prompt_text", lambda prompt: "1")

    out_file = tmp_path / "out.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file))
    ) == 0
    assert out_file.read_text(encoding="utf-8").splitlines() == [
        str((project / "docs").resolve()),
        "/private/only",
    ]
    # The spellings match after resolution, and an entry only the private list
    # has is never reported -- that is what the private list is for.
    assert capsys.readouterr().err == ""


def test_projmgr_cdproj_dedupes_the_winning_list(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from ortasklib import menu

    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    _write_private_index(
        registry, "*** file:/a\n*** file:/b\n*** file:/a\n"
    )
    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(menu, "prompt_text", lambda prompt: "1")

    out_file = tmp_path / "out.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file))
    ) == 0
    assert out_file.read_text(encoding="utf-8").splitlines() == ["/a", "/b"]


def test_projmgr_cdproj_empty_private_section_wins_and_reports(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """Emptying the private list is a choice; the shared list must not undo it."""
    from ortasklib import menu

    registry, project = _cdproj_registry(
        tmp_path,
        monkeypatch,
        capsys,
        "* Tasks\n** TODO t0001 task\n* Directories\n** file:/shared/one\n",
    )
    _write_private_index(registry, "")
    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(menu, "prompt_text", lambda prompt: "1")

    out_file = tmp_path / "out.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file))
    ) == 0
    # An empty stack would read as "do nothing" in the shell function.
    assert out_file.read_text(encoding="utf-8").splitlines() == [
        str(project.resolve())
    ]
    assert "not projects.org: /shared/one" in capsys.readouterr().err


def test_projmgr_cdproj_routes_resolve_alike(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """The picker, cdproj PROJECT, and the numbered fallback must not drift."""
    from ortasklib import menu

    registry, _ = _cdproj_registry(
        tmp_path,
        monkeypatch,
        capsys,
        "* Tasks\n** TODO t0001 task\n* Directories\n** file:/shared/one\n",
    )
    _write_private_index(registry, "*** file:/private/one\n")
    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(menu, "prompt_text", lambda prompt: "1")

    results = []
    for index, project_arg in enumerate((None, "myproj")):
        out_file = tmp_path / f"out{index}.txt"
        assert projmgr.cmd_cdproj(
            argparse.Namespace(
                registry=str(registry), out=str(out_file), project=project_arg
            )
        ) == 0
        results.append(
            (
                out_file.read_text(encoding="utf-8"),
                capsys.readouterr().err.strip(),
            )
        )

    assert results[0] == results[1]
    assert results[0][0].splitlines() == ["/private/one"]
    assert results[0][1] == (
        "myproj: in TODO.org but not projects.org: /shared/one"
    )


def test_projmgr_cdproj_empty_section_falls_back_to_the_project_root(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """An empty stack would read as "do nothing" in the shell function."""
    from ortasklib import menu

    registry, project = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n* Directories\n"
    )
    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(menu, "prompt_text", lambda prompt: "1")

    out_file = tmp_path / "out.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file))
    ) == 0
    assert out_file.read_text(encoding="utf-8").splitlines() == [
        str(project.resolve())
    ]


def test_projmgr_cdproj_cancel_writes_nothing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from ortasklib import menu

    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(menu, "prompt_text", lambda prompt: "q")

    out_file = tmp_path / "out.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file))
    ) != 0
    assert not out_file.exists()


def test_projmgr_cdproj_direct_resolution(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    registry, project = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n* Directories\n** docs\n"
    )
    out_file = tmp_path / "out.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file), project="myproj")
    ) == 0
    assert out_file.read_text(encoding="utf-8").splitlines() == [
        str((project / "docs").resolve())
    ]

    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file), project="nonexistent")
    ) == 1


def test_project_verbs_unrelated_to_private_stacks_work_before_migration(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # list, add, rm, and init remain available without a projects.org marker.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "registry"
    existing = tmp_path / "existing"
    write(existing / "tasks.org", "* Tasks\n** TODO t0001 Existing\n")
    register(registry, "existing", existing)

    assert projmgr.cmd_list(
        argparse.Namespace(registry=str(registry), format="names", all=False)
    ) == 0
    assert capsys.readouterr().out.strip() == "existing"

    added = tmp_path / "added"
    write(added / "tasks.org", "* Tasks\n** TODO t0001 Added\n")
    assert projmgr.cmd_add(
        _add_args(added, name="added", registry=str(registry))
    ) == 0
    capsys.readouterr()
    assert projmgr.cmd_rm(
        argparse.Namespace(
            name="added", registry=str(registry), force=False, dry_run=False
        )
    ) == 0
    capsys.readouterr()
    assert projmgr.cmd_init(
        argparse.Namespace(
            registry=str(registry), force=False, dry_run=False
        )
    ) == 0
    assert not (registry / manager.PROJECTS_INDEX_NAME).exists()


def test_core_editor_argv(monkeypatch) -> None:
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    assert core.editor_argv(Path("/tmp/x.org")) is None

    # An editor carrying arguments must survive as separate argv entries.
    monkeypatch.setenv("EDITOR", "emacs -nw")
    assert core.editor_argv(Path("/tmp/x.org")) == ["emacs", "-nw", "/tmp/x.org"]
    assert core.editor_argv(Path("/tmp/x.org"), 12) == [
        "emacs",
        "-nw",
        "+12",
        "/tmp/x.org",
    ]

    monkeypatch.setenv("VISUAL", "code")
    assert core.editor_argv(Path("/tmp/x.org"), 12) == [
        "code",
        "--goto",
        "/tmp/x.org:12",
    ]


def test_projmgr_task_menu_displays_canonical_symlink_target(tmp_path: Path) -> None:
    # This test reproduces a registry symlink: project selection should show the
    # real task-file target path, not the path inside the registry.
    target = tmp_path / "electorama-weekly"
    task_file = write(
        target / "TODO-ElWeek.org",
        """
        * Tasks
        ** TODO tw26W24 Week of June 8's tasks for ElectoramaWeekly
        """,
    )
    workspace = tmp_path / "proj2026"
    project_dir = workspace / "elweek"
    project_dir.mkdir(parents=True)
    (project_dir / "TODO-ElWeek.org").symlink_to(task_file)

    result = subprocess.run(
        [sys.executable, str(ROOT / "projmgr.py"), "--registry", str(workspace), "-i"],
        cwd=ROOT,
        input="1\nb\nq\n",
        text=True,
        capture_output=True,
        check=False,
    )

    symlink_path = project_dir / "TODO-ElWeek.org"
    assert result.returncode == 0
    assert f"ortask — reading {task_file.resolve()}" in result.stdout
    assert "Open: 1" in result.stdout
    assert f"ortask — reading {symlink_path}" not in result.stdout


def test_projmgr_project_task_quit_returns_to_project_menu(tmp_path: Path) -> None:
    # In project mode, q from a project's task list returns to the parent menu.
    workspace = tmp_path / "workspace"
    write(
        tmp_path / "real-sample" / "tasks.org",
        """
        * Tasks
        ** TODO t0001 Sample task
        """,
    )
    register(workspace, "sample", tmp_path / "real-sample")

    result = subprocess.run(
        [sys.executable, str(ROOT / "projmgr.py"), "--registry", str(workspace), "-i"],
        cwd=ROOT,
        input="1\nq\nq\n",
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.count(f"Projects in {workspace}") == 2
    assert "sample" in result.stdout
    assert "Sample task" in result.stdout


def test_projmgr_task_menu_opens_org_file_from_task_list(tmp_path: Path, monkeypatch) -> None:
    # This test ensures the file-scoped task-list menu can open the whole Org
    # file in an editor before a specific task has been selected.
    org_file = write(
        tmp_path / "project" / "TODO.org",
        """
        * Tasks
        ** TODO t0001 Open from task menu
        """,
    )
    project = manager.Project("sample", tmp_path / "project", org_file)
    opened: list[tuple[Path, int | None]] = []
    choices = iter(["e", "b"])

    monkeypatch.setattr(taskui, "_open_editor", lambda buf, line: opened.append((buf.path, line)))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(choices))

    assert taskui.task_menu(project, include_done=False) is None
    assert opened == [(org_file.resolve(), None)]


def test_projmgr_escape_cancels_done_confirmation(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # This test verifies Esc cancels the current focus action without writing.
    org_file = write(
        tmp_path / "project" / "TODO.org",
        """
        * Tasks
        ** TODO t0001 Keep open
        """,
    )
    buf = taskui.OrgBuffer(org_file)
    item = taskui.load_menu_items(buf)[0]
    choices = iter(["d", "\x1b", "b"])

    monkeypatch.setattr("builtins.input", lambda prompt="": next(choices))

    assert taskui.focus_menu(buf, item) is None
    capsys.readouterr()
    assert "** TODO t0001 Keep open" in org_file.read_text(encoding="utf-8")
    assert buf.dirty is False  # Esc cancelled the toggle; nothing buffered


def test_focus_menu_q_returns_to_immediate_parent(tmp_path: Path, monkeypatch) -> None:
    # q from a subtask should reveal its parent focus view, not unwind every menu.
    org_file = write(
        tmp_path / "tasks.org",
        """
        * Tasks
        ** TODO t0001 Parent
        *** TODO t0001.1 Child
        """,
    )
    buf = taskui.OrgBuffer(org_file)
    parent = taskui.load_menu_items(buf)[0]
    shown: list[str] = []
    choices = iter(["1", "q", "q"])

    monkeypatch.setattr(
        taskui,
        "_show_context",
        lambda _buf, selected: shown.append(selected.task.id),
    )
    monkeypatch.setattr("builtins.input", lambda prompt="": next(choices))

    assert taskui.focus_menu(buf, parent) is None
    assert shown == ["t0001", "t0001.1", "t0001"]


# --- apply: template instantiation (docs/templates.md) ------------------------


def _apply_ns(org: Path, **kw) -> argparse.Namespace:
    base = dict(file=org, template="weekly", week=None, date="2026-06-25",
                dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_resolve_week_target_modes() -> None:
    # Default/only-week/only-date/both, plus mismatch and parse errors.
    assert tasks.resolve_week_target(None, None, today=date(2026, 6, 25)) == (
        2026, 26, date(2026, 6, 22))
    assert tasks.resolve_week_target("26W26", None) == (2026, 26, date(2026, 6, 22))
    assert tasks.resolve_week_target(None, "2026-06-25") == (2026, 26, date(2026, 6, 22))
    assert tasks.resolve_week_target("2026W26", "2026-06-25")[2] == date(2026, 6, 22)

    with pytest.raises(tasks.TemplateError):       # date not in the given week
        tasks.resolve_week_target("2026W25", "2026-06-25")
    with pytest.raises(tasks.TemplateError):       # unparseable week
        tasks.resolve_week_target("nope", None)
    with pytest.raises(tasks.TemplateError):       # unparseable date
        tasks.resolve_week_target(None, "2026-13-99")


def test_apply_template_longest_first_ordering(tmp_path: Path) -> None:
    # A fixture that mixes every ID placeholder confirms longest-literal-first
    # so no shorter placeholder corrupts a longer one.
    org = write(
        tmp_path / "todo.org",
        """
        * Tasks
        ** TODO t0001 Keep

        * Template
        ** TODO twYYWNN Week twYYWNN / twYYYYWNN / YYWNN / YYYYWNN
        forms: YYWNN YYYYWNN twYYWNN twYYYYWNN
        """,
    )
    _, block, week_id = tasks.apply_template(
        org.read_text(encoding="utf-8"), 2026, 26, date(2026, 6, 22))

    assert week_id == "tw26W26"
    assert "** TODO tw26W26 Week tw26W26 / tw2026W26 / 26W26 / 2026W26" in block
    assert "forms: 26W26 2026W26 tw26W26 tw2026W26" in block


def test_apply_template_dry_run_then_insert(tmp_path: Path, capsys) -> None:
    # End-to-end: dry-run prints without writing; a real run inserts under
    # * Tasks before * Template, substitutes dates/IDs, preserves body URLs and
    # the template itself; a second run refuses the duplicate week.
    org = write(
        tmp_path / "TODO-ElWeek.org",
        """
        * Tasks
        ** TODO t0001 Keep me

        * Template
        ** TODO twYYWNN Week of Month Day's tasks for ElectoramaWeekly
        *** TODO twYYWNN.0 Promote Month Day ElectoramaWeekly episode
        https://example.com/promo
        *** TODO twYYWNN.1 Prepare for Next Month Day ElectoramaWeekly episode
        """,
    )

    before = org.read_text(encoding="utf-8")
    assert ortask.cmd_apply(_apply_ns(org, dry_run=True)) == 0
    out = capsys.readouterr().out
    assert "** TODO tw26W26 Week of June 22's tasks for ElectoramaWeekly" in out
    assert "*** TODO tw26W26.0 Promote June 22 ElectoramaWeekly episode" in out
    assert "*** TODO tw26W26.1 Prepare for June 29 ElectoramaWeekly episode" in out
    assert org.read_text(encoding="utf-8") == before          # dry-run wrote nothing

    assert ortask.cmd_apply(_apply_ns(org)) == 0
    capsys.readouterr()
    lines = org.read_text(encoding="utf-8").splitlines()
    parent = "** TODO tw26W26 Week of June 22's tasks for ElectoramaWeekly"
    assert (lines.index("** TODO t0001 Keep me")
            < lines.index(parent) < lines.index("* Template"))
    assert "twYYWNN" in "\n".join(lines)                       # template preserved
    assert lines.count("https://example.com/promo") == 2      # url copied, original kept

    snapshot = org.read_text(encoding="utf-8")
    assert ortask.cmd_apply(_apply_ns(org)) == 1               # duplicate week refused
    assert "already exist" in capsys.readouterr().err
    assert org.read_text(encoding="utf-8") == snapshot         # and nothing changed


def test_apply_template_section_errors(tmp_path: Path) -> None:
    # Missing, duplicate, empty, and unknown-profile cases all raise cleanly.
    base = "* Tasks\n** TODO t0001 keep\n"
    one = base + "\n* Template\n** TODO twYYWNN A\n"

    with pytest.raises(tasks.TemplateError):                   # no * Template
        tasks.apply_template(base, 2026, 26, date(2026, 6, 22))
    with pytest.raises(tasks.TemplateError):                   # two * Template
        tasks.apply_template(one + "\n* Template\n** TODO twYYWNN B\n",
                             2026, 26, date(2026, 6, 22))
    with pytest.raises(tasks.TemplateError):                   # empty * Template
        tasks.apply_template(base + "\n* Template\n", 2026, 26, date(2026, 6, 22))
    with pytest.raises(tasks.TemplateError):                   # unknown profile
        tasks.apply_template(one, 2026, 26, date(2026, 6, 22), profile="monthly")


def test_apply_template_creates_tasks_section_if_missing(tmp_path: Path) -> None:
    # With a * Template but no * Tasks, apply creates the section then inserts.
    text = "* Template\n** TODO twYYWNN Week of Month Day's tasks\n"
    new_lines, block, _ = tasks.apply_template(text, 2026, 26, date(2026, 6, 22))
    assert "* Tasks" in new_lines
    assert "** TODO tw26W26 Week of June 22's tasks" in new_lines
    assert new_lines.index("* Tasks") < new_lines.index(block[0])


# ---------------------------------------------------------------------------
# Interactive task selector (ortasklib.menu / taskui)
# ---------------------------------------------------------------------------

from ortasklib import menu  # noqa: E402 — grouped with the interactive tests


def test_next_state_ring() -> None:
    # The Emacs-style toggle ring flips between the two keywords ortask uses.
    assert tasks.next_state("TODO") == "DONE"
    assert tasks.next_state("DONE") == "TODO"
    # Anything unrecognized cycles back to the first ring entry.
    assert tasks.next_state("WAITING") == "TODO"


def test_change_text_preserves_task_heading_structure_and_body() -> None:
    # Heading-text edits should preserve syntax, spacing, tags, and nearby lines.
    original = (
        "* Tasks\n"
        "**  TODO   [#A]  t0001   Old title  :work:home:\n"
        "Body line\n"
        "** TODO t0002 Neighbor\n"
    )

    changed = tasks.change_text(original, "t0001", "New title with  spacing")
    assert changed is not None
    assert changed[1] == (
        "**  TODO   [#A]  t0001   New title with  spacing  :work:home:"
    )
    assert changed[2:] == original.splitlines()[2:]

    reparsed = core.find_by_id(core.parse_org("\n".join(changed)), "t0001")
    assert reparsed is not None
    assert reparsed.text == "New title with  spacing"
    assert reparsed.priority == "A"
    assert reparsed.tags == "work:home"
    assert (
        tasks.change_text("\n".join(changed), "t0001", " New title with  spacing ")
        is None
    )


def test_change_text_rejects_invalid_or_ambiguous_titles() -> None:
    # Invalid input must not create empty, multiline, or accidentally tagged tasks.
    original = "* Tasks\n** TODO t0001 Original\n"

    with pytest.raises(ValueError, match="must not be empty"):
        tasks.change_text(original, "t0001", "   ")
    with pytest.raises(ValueError, match="single line"):
        tasks.change_text(original, "t0001", "First\nSecond")
    with pytest.raises(ValueError, match="Org heading syntax"):
        tasks.change_text(original, "t0001", "Accidental :newtag:")
    with pytest.raises(tasks.TaskNotFound):
        tasks.change_text(original, "t9999", "Missing")


def test_change_body_replaces_only_the_task_own_body() -> None:
    # Body edits preserve the heading, descendants, siblings, and later sections.
    original = (
        "* Tasks\n"
        "** TODO [#A] t0001 Parent  :work:\n"
        "SCHEDULED: <2026-08-17 Mon>\n"
        ":PROPERTIES:\n"
        ":OWNER: robla\n"
        ":END:\n"
        "\n"
        "Old description\n"
        "*** TODO t0001.1 Child\n"
        "Child body\n"
        "** TODO t0002 Sibling\n"
        "Sibling body\n"
        "* Notes\n"
        "Unrelated prose\n"
    )
    replacement = (
        "DEADLINE: <2026-08-18 Tue>\n"
        ":PROPERTIES:\n"
        ":OWNER: nobody\n"
        ":END:\n"
        "\n"
        "New description"
    )

    changed = tasks.change_body(original, "t0001", replacement)

    assert changed is not None
    assert core.lines_to_text(changed) == (
        "* Tasks\n"
        "** TODO [#A] t0001 Parent  :work:\n"
        f"{replacement}\n"
        "*** TODO t0001.1 Child\n"
        "Child body\n"
        "** TODO t0002 Sibling\n"
        "Sibling body\n"
        "* Notes\n"
        "Unrelated prose\n"
    )
    assert tasks.change_body(core.lines_to_text(changed), "t0001", replacement) is None


def test_change_body_handles_empty_bodies_and_rejects_structure_changes() -> None:
    # Body insertion/removal stays before children and cannot create Org headings.
    original = (
        "* Tasks\n"
        "** TODO t0001 Parent\n"
        "*** TODO t0001.1 Child\n"
        "** TODO t0002 Sibling\n"
    )

    inserted = tasks.change_body(original, "t0001", "First line\n\nLast line")
    assert inserted is not None
    assert inserted[2:6] == [
        "First line",
        "",
        "Last line",
        "*** TODO t0001.1 Child",
    ]
    removed = tasks.change_body(core.lines_to_text(inserted), "t0001", "")
    assert removed == original.splitlines()

    with pytest.raises(ValueError, match="carriage returns"):
        tasks.change_body(original, "t0001", "First\rSecond")
    with pytest.raises(ValueError, match="cannot create Org headings"):
        tasks.change_body(original, "t0001", "Prose\n*** TODO t0001.2 New child")
    with pytest.raises(tasks.TaskNotFound):
        tasks.change_body(original, "t9999", "Missing")


def test_change_priority_preserves_task_heading_and_body() -> None:
    # Priority edits should touch only the cookie and round-trip back to the source.
    original = (
        "* Tasks\n"
        "** TODO   t0001 Target  :tag:\n"
        "Body line\n"
        "** TODO t0002 Neighbor\n"
    )

    added = tasks.change_priority(original, "t0001", "A")
    assert added is not None
    assert added[1] == "** TODO   [#A] t0001 Target  :tag:"
    assert added[2:] == original.splitlines()[2:]

    changed = tasks.change_priority("\n".join(added) + "\n", "t0001", "B")
    assert changed is not None
    assert changed[1] == "** TODO   [#B] t0001 Target  :tag:"

    cleared = tasks.change_priority("\n".join(changed) + "\n", "t0001", None)
    assert cleared == original.splitlines()


def test_priority_scale_clamps_and_rejects_invalid_values() -> None:
    # Directional priority changes use none/C/B/A and stop at both boundaries.
    assert tasks.shift_priority(None, 1) == "C"
    assert tasks.shift_priority("C", 1) == "B"
    assert tasks.shift_priority("B", 1) == "A"
    assert tasks.shift_priority("A", 1) == "A"
    assert tasks.shift_priority("A", -1) == "B"
    assert tasks.shift_priority("C", -1) is None
    assert tasks.shift_priority(None, -1) is None
    with pytest.raises(ValueError):
        tasks.shift_priority("Z", 1)
    with pytest.raises(ValueError):
        tasks.change_priority("* Tasks\n** TODO t0001 Task\n", "t0001", "Z")


def test_toggle_state_buffers_change_until_save(tmp_path: Path) -> None:
    # t0006: taskui._toggle_state edits the in-memory buffer (and the auto-save
    # file), NOT the real file. The real file changes only on buf.save(), and
    # then only the selected heading line, leaving the rest byte-for-byte intact.
    org_file = write(
        tmp_path / "todo.org",
        """
        * Tasks
        ** TODO t0001 Target  :tag:
        Body line

        ** TODO t0002 Neighbor
        """,
    )
    original = org_file.read_text(encoding="utf-8")
    buf = taskui.OrgBuffer(org_file)
    target = next(i for i in taskui.load_menu_items(buf, include_done=True)
                  if i.task and i.task.id == "t0001")

    taskui._toggle_state(buf, target)
    # Real file untouched; change lives in the buffer and the auto-save sibling.
    assert org_file.read_text(encoding="utf-8") == original
    assert buf.dirty is True
    assert "** DONE t0001 Target  :tag:" in buf.read()
    assert buf.autosave_path.exists()
    assert buf.autosave_path.read_text(encoding="utf-8") == buf.read()

    buf.save()
    toggled = org_file.read_text(encoding="utf-8").splitlines()
    orig_lines = original.splitlines()
    assert toggled[1] == "** DONE t0001 Target  :tag:"
    assert toggled[:1] + toggled[2:] == orig_lines[:1] + orig_lines[2:]
    assert not buf.autosave_path.exists()  # save clears the auto-save
    assert buf.dirty is False


def test_priority_change_stays_buffered_until_save(tmp_path: Path) -> None:
    # Interactive priority shortcuts should update auto-save but not the real file.
    org_file = write(
        tmp_path / "todo.org",
        "* Tasks\n** TODO t0001 Target  :tag:\nBody line\n",
    )
    original = org_file.read_text(encoding="utf-8")
    buf = taskui.OrgBuffer(org_file)
    item = taskui.load_menu_items(buf)[0]

    taskui._shift_priority(buf, item, 1)

    assert "** TODO [#C] t0001 Target  :tag:" in buf.read()
    assert org_file.read_text(encoding="utf-8") == original
    assert buf.autosave_path.read_text(encoding="utf-8") == buf.read()


def test_interactive_select_unavailable_without_tty() -> None:
    # Under pytest there is no TTY, so the selector must report unavailable and
    # callers fall back to the numbered menu (no interactive code runs in CI).
    assert menu.interactive_select_available() is False


@pytest.mark.skipif(menu.FormattedText is None, reason="prompt_toolkit not installed")
def test_selector_help_uses_action_metadata_once() -> None:
    # Help should discover custom commands without listing aliases as duplicates.
    help_view = menu._selector_help(
        taskui.TASK_MENU_ACTIONS,
        select_help="Open task details",
        back_help="Return to tasks",
    )
    text = "".join(fragment[1] for fragment in help_view)

    assert "C-g" in text and "Show or close this help" in text
    assert "Open the highlighted task in the editor" in text
    # t0039.3.4: help spells out every terminal state the position selects.
    assert "DONE+ (DONE, MOOT, SUPERSEDED)" in text
    assert text.count("Cycle the highlighted task's state") == 1
    assert "Raise the highlighted task's priority" in text
    assert "Choose the highlighted task's priority" in text
    assert "Save all buffered edits to the Org file" in text
    assert "Undo the most recent buffered task edit" in text
    assert "Redo the most recently undone task edit" in text
    assert "Esc/b/q" in text and "C-g/Esc/b/q/Enter closes it" in text


def _run_local_task_pty(
    directory: Path,
    *,
    initial_rows: int = 24,
    resized_rows: int = 12,
) -> tuple[int, bytes, bool]:
    """Run ``ortask.py -i`` in a real PTY and capture its terminal stream."""
    import fcntl
    import os
    import pty
    import select
    import signal
    import struct
    import termios
    import time

    master, slave = pty.openpty()
    fcntl.ioctl(
        slave,
        termios.TIOCSWINSZ,
        struct.pack("HHHH", initial_rows, 80, 0, 0),
    )
    attributes_before = termios.tcgetattr(slave)
    os.write(slave, b"SENTINEL-ABOVE\r\n")
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["TERM"] = "xterm-256color"
    process = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "ortask.py"),
            "-i",
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=directory,
        env=environment,
        close_fds=True,
    )
    captured = bytearray()
    cursor_requests_answered = 0

    def read_chunk() -> bool:
        nonlocal cursor_requests_answered
        try:
            captured.extend(os.read(master, 65536))
        except OSError:
            return False
        requests = bytes(captured).count(b"\x1b[6n")
        while cursor_requests_answered < requests:
            os.write(master, b"\x1b[2;1R")
            cursor_requests_answered += 1
        return True

    def read_until(marker: bytes, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while marker not in captured and time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.1)
            if not readable:
                continue
            if not read_chunk():
                break
        assert marker in captured, f"{marker!r} not rendered in {captured!r}"

    def read_for(duration: float) -> None:
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.05)
            if readable and not read_chunk():
                return

    try:
        read_until(b"Open: 55")
        os.write(master, b"j" * 50)
        read_until(b"t0051")
        read_for(0.1)
        fcntl.ioctl(
            slave,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", resized_rows, 80, 0, 0),
        )
        before_resize = len(captured)
        process.send_signal(signal.SIGWINCH)
        read_for(0.2)
        assert len(captured) > before_resize, "terminal resize did not repaint"
        os.write(master, b"\x07")
        read_until(b"Interactive help")
        os.write(master, b"qq")
        returncode = process.wait(timeout=5)
        read_for(0.1)
        attributes_after = termios.tcgetattr(slave)
        os.write(slave, b"NEXT-PROMPT> ")
        read_until(b"NEXT-PROMPT>")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        os.close(master)
        os.close(slave)

    return returncode, bytes(captured), attributes_before == attributes_after


@pytest.mark.skipif(
    menu.Application is None or sys.platform == "win32",
    reason="PTY integration requires prompt_toolkit on POSIX",
)
def test_local_task_normal_exit_contract_in_real_pty(tmp_path: Path) -> None:
    # A long, resized session should retain one outcome and restore the next prompt.
    import re

    tasks_text = "* Tasks\n" + "".join(
        f"** TODO t{index:04} Task {index}\n"
        for index in range(1, 56)
    )
    write(tmp_path / "tasks.org", tasks_text)

    returncode, output, restored = _run_local_task_pty(tmp_path)

    assert returncode == 0
    assert restored is True
    assert b"SENTINEL-ABOVE" in output
    assert b"t0051" in output
    assert b"Interactive help" in output
    assert b"No changes to tasks.org" in output
    assert b"\x1b[?1049h" not in output
    assert b"\x1b[?1047h" not in output
    assert b"\x1b[?47h" not in output
    before_prompt, marker, after_prompt = output.rpartition(b"NEXT-PROMPT>")
    assert marker == b"NEXT-PROMPT>"
    assert re.search(
        rb"\r+\n(?:\x1b\[[0-?]*[ -/]*[@-~])*$",
        before_prompt,
    )
    assert after_prompt == b" "


TREE_FIXTURE = (
    "* Tasks\n"
    "** TODO [#C] t0001 Zebra parent\n"
    "*** TODO [#B] t0001.1 Middle child\n"
    "*** TODO [#A] t0001.2 Alpha child\n"
    "*** TODO t0001.3 Unranked child\n"
    "** TODO [#A] t0002 Alpha parent\n"
    "*** TODO t0002.1 Only child\n"
)


def _ordered_ids(buf, view) -> list[str]:
    """Task ids in the order the list would render them under one view."""
    items = taskui.load_menu_items(buf, filter_mode="all")
    parent_ids, child_ids, _ = taskui._task_tree(items, buf.read())
    ordered = taskui.sibling_ordered_items(items, parent_ids, child_ids, view)
    return [item.task.id for item in ordered]


def test_task_sorting_moves_siblings_and_never_crosses_parents(
    tmp_path: Path,
) -> None:
    # t0039.5: a flat sort would put children above other families' parents.
    buf = taskui.OrgBuffer(write(tmp_path / "tasks.org", TREE_FIXTURE))
    view = taskui.TASK_VIEW_AXES.initial()

    assert _ordered_ids(buf, view) == [
        "t0001",
        "t0001.1",
        "t0001.2",
        "t0001.3",
        "t0002",
        "t0002.1",
    ]
    # Priority reorders roots among roots and children among their own parent.
    # An unset cookie ranks last; source order breaks ties.
    assert _ordered_ids(buf, view.with_sort(taskui.TASK_SORT_PRIORITY)) == [
        "t0002",
        "t0002.1",
        "t0001",
        "t0001.2",
        "t0001.1",
        "t0001.3",
    ]
    assert _ordered_ids(buf, view.with_sort(taskui.TASK_SORT_TITLE)) == [
        "t0002",
        "t0002.1",
        "t0001",
        "t0001.2",
        "t0001.1",
        "t0001.3",
    ]
    reversed_titles = view.with_sort(taskui.TASK_SORT_TITLE).with_reverse(True)
    assert _ordered_ids(buf, reversed_titles) == [
        "t0001",
        "t0001.3",
        "t0001.1",
        "t0001.2",
        "t0002",
        "t0002.1",
    ]

    # The structural invariant every reader of this list depends on: a parent is
    # immediately followed by its own subtree, under every order.
    for sort in taskui.TASK_SORTS:
        for reverse in (False, True):
            ids = _ordered_ids(buf, view.with_sort(sort).with_reverse(reverse))
            assert sorted(ids) == sorted(_ordered_ids(buf, view))
            for parent, child_count in (("t0001", 3), ("t0002", 1)):
                found = [
                    index
                    for index, task_id in enumerate(ids)
                    if task_id.startswith(parent + ".")
                ]
                first = ids.index(parent) + 1
                assert found == list(range(first, first + child_count)), (
                    f"{sort} reverse={reverse} split {parent}'s subtree: {ids}"
                )


def test_task_sorting_keeps_fold_navigation_and_anchoring(tmp_path: Path) -> None:
    # t0039.5: a non-default order must not break the tree controls.
    buf = taskui.OrgBuffer(write(tmp_path / "tasks.org", TREE_FIXTURE))
    project = manager.Project("demo", tmp_path, org_file := buf.path)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    class FakeSession:
        def __init__(self) -> None:
            self.current_view = None
            self.message = ""

        def replace_view(self, replacement) -> None:
            self.current_view = replacement

        def set_transient_message(self, message: str) -> None:
            self.message = message

        def set_message(self, message) -> None:
            self.message = message

    session = FakeSession()
    view = controller._task_view()
    session.current_view = view
    assert view.actions["s"].name == "sort"
    assert "s sort" in view.instruction
    assert view.instruction.startswith("all · File order · ")

    def ids() -> list[str]:
        return [
            next(
                token
                for token in row.text.split()
                if orglib.syntax.NUMERIC_ID_RE.match(token)
            )
            for row in session.current_view.rows
        ]
    # The overview shows roots only, in file order.
    assert ids() == ["t0001", "t0002"]

    # s cycles to Priority: the roots swap, the tree is still a tree.
    view.on_result(session, menu.MenuResult("sort", 0))
    assert controller.view_state.sort == taskui.TASK_SORT_PRIORITY
    assert session.message == "Order: Priority"
    assert session.current_view.instruction.startswith("all · Priority order · ")
    assert ids() == ["t0002", "t0001"]

    # Fold still expands the highlighted task, and its children arrive sorted
    # under it rather than anywhere else in the list.
    session.current_view.selected_index = 1
    session.current_view.on_result(session, menu.MenuResult("fold", 1))
    assert ids() == ["t0002", "t0001", "t0001.2", "t0001.1", "t0001.3"]

    # Selection stays anchored to the same task across a state toggle, and the
    # toggle does not move it: only the sort key it was ordered by can.
    session.current_view.selected_index = 3
    assert ids()[3] == "t0001.1"
    session.current_view.on_result(session, menu.MenuResult("toggle", 3))
    assert ids() == ["t0002", "t0001", "t0001.2", "t0001.1", "t0001.3"]
    assert session.current_view.selected_index == 3
    assert session.current_view.rows[3].status == "DONE"

    # Raising a priority in a priority order deliberately moves the row, and
    # the selection follows the task rather than the position.
    session.current_view.on_result(session, menu.MenuResult("priority_up", 3))
    assert ids() == ["t0002", "t0001", "t0001.1", "t0001.2", "t0001.3"]
    assert session.current_view.selected_index == 2
    assert ids()[2] == "t0001.1"


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_task_contexts_share_one_bounded_application(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Task workspace, Help, and priority views should repaint one 20-row app.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(
        tmp_path / "tasks.org",
        "* Tasks\n** TODO t0001 Parent\n*** TODO t0001.1 Child\n",
    )
    original = org_file.read_text(encoding="utf-8")
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    real_application = menu.Application
    applications = []

    def tracked_application(*args, **kwargs):
        app = real_application(*args, **kwargs)
        applications.append(app)
        return app

    monkeypatch.setattr(menu, "Application", tracked_application)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Visit workspace Help, cancel a picker, then discard a priority edit.
            pin.send_text("\r\x07q\x1bpq\x1b[1;2Aqn")
            controller.run()

    assert len(applications) == 1
    assert controller.session is not None
    assert controller.session.requested_height == 20
    assert controller.session.effective_height == 20
    assert applications[0].full_screen is False
    assert applications[0].erase_when_done is False
    assert buf.read() == original
    assert org_file.read_text(encoding="utf-8") == original


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_transient_message_restores_view_hint(monkeypatch) -> None:
    # A transient warning should expire back to the current view's instruction.
    import asyncio

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.output import DummyOutput

    captured = []
    view = menu.MenuView(
        [menu.MenuRow(1, "TODO", "t0001 task")],
        lambda _session, _result: None,
        instruction="C-g help · Esc back",
    )
    with create_app_session(output=DummyOutput()):
        session = menu.InlineMenuSession(view)
        monkeypatch.setattr(
            session.application,
            "create_background_task",
            lambda coroutine: captured.append(coroutine),
        )
        monkeypatch.setattr(session.application, "invalidate", lambda: None)

        session.set_transient_message("No visible subtasks", timeout=0)
        assert "No visible subtasks" in str(session._render_footer())
        asyncio.run(captured.pop())

    assert session.message is None
    assert "C-g help · Esc back" in str(session._render_footer())


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_stale_transient_timer_cannot_clear_newer_message(monkeypatch) -> None:
    # Replaced timers must not erase a newer warning or persistent save outcome.
    import asyncio

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.output import DummyOutput

    captured = []
    view = menu.MenuView([], lambda _session, _result: None)
    with create_app_session(output=DummyOutput()):
        session = menu.InlineMenuSession(view)
        monkeypatch.setattr(
            session.application,
            "create_background_task",
            lambda coroutine: captured.append(coroutine),
        )
        monkeypatch.setattr(session.application, "invalidate", lambda: None)

        session.set_transient_message("first", timeout=0)
        first = captured.pop()
        session.set_transient_message("second", timeout=0)
        second = captured.pop()
        asyncio.run(first)
        assert session.message == "second"

        session.set_message("Saved changes")
        asyncio.run(second)
        assert session.message == "Saved changes"

        session.set_transient_message("third", timeout=0)
        third = captured.pop()
        session.replace_view(menu.MenuView([], lambda _session, _result: None))
        asyncio.run(third)

    assert session.message is None


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_menu_session_clamps_height_and_scrolls() -> None:
    # A bounded session should reserve one terminal row and scroll its body.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    class TinyOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=12, columns=80)

        def get_rows_below_cursor_position(self) -> int:
            return 12

    rows = [
        menu.MenuRow(index + 1, "TODO", f"t{index + 1:04} row")
        for index in range(30)
    ]

    def handle(session: menu.InlineMenuSession, result: menu.MenuResult) -> None:
        if result.action == "select":
            session.pop_view()

    view = menu.MenuView(rows, handle, selected_index=20)
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=TinyOutput()):
            pin.send_text("\r")
            session = menu.InlineMenuSession(view)
            session.run()

    assert session.effective_height == 11
    assert session.body_window.render_info.window_height == 6
    assert session.body_window.vertical_scroll <= 20
    assert 20 < session.body_window.vertical_scroll + 6


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_menu_session_rejects_too_small_terminal_cleanly() -> None:
    # A terminal that cannot fit the minimum plus one outside row exits once.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    class TooSmallOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=6, columns=80)

        def get_rows_below_cursor_position(self) -> int:
            return 6

    view = menu.MenuView(
        [menu.MenuRow(1, "TODO", "t0001 task")],
        lambda _session, _result: None,
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=TooSmallOutput()):
            session = menu.InlineMenuSession(view)
            assert session.run() == menu.MenuResult("back", None)

    assert session.error == "terminal has 6 rows; at least 7 are required"
    assert session.application.erase_when_done is True


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_menu_session_never_enters_alternate_screen() -> None:
    # The persistent mini-app must emit no common alternate-screen entry code.
    import io

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output.vt100 import Vt100_Output

    stream = io.StringIO()
    output = Vt100_Output(
        stream,
        get_size=lambda: Size(rows=30, columns=80),
        term="xterm-256color",
        enable_cpr=False,
    )

    def handle(_session: menu.InlineMenuSession, _result: menu.MenuResult) -> None:
        return

    view = menu.MenuView([menu.MenuRow(1, "TODO", "t0001 task")], handle)
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=output):
            pin.send_text("q")
            menu.InlineMenuSession(view).run()

    rendered = stream.getvalue()
    assert "\x1b[?1049h" not in rendered
    assert "\x1b[?1047h" not in rendered
    assert "\x1b[?47h" not in rendered


def test_escape_flush_window_falls_back_on_an_unusable_override(monkeypatch) -> None:
    """A slow link can widen the window; nothing can shrink it to unusable."""
    monkeypatch.delenv(menu.ESCAPE_FLUSH_ENV, raising=False)
    assert menu.escape_flush_seconds() == menu.ESCAPE_FLUSH_SECONDS

    monkeypatch.setenv(menu.ESCAPE_FLUSH_ENV, "0.5")
    assert menu.escape_flush_seconds() == 0.5

    for unusable in ("", "soon", "0", "-1"):
        monkeypatch.setenv(menu.ESCAPE_FLUSH_ENV, unusable)
        assert menu.escape_flush_seconds() == menu.ESCAPE_FLUSH_SECONDS


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_escape_leaves_a_menu_without_the_stock_flush_wait() -> None:
    """Esc exits about as promptly as the q beside it, not half a second later.

    prompt_toolkit's stock ttimeoutlen is 0.5s, so a session that never sets it
    cannot pass this; the ceiling is generous enough that a loaded machine can
    still make it.
    """
    import time

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    def handle(_session: menu.InlineMenuSession, _result: menu.MenuResult) -> None:
        return

    def elapsed(keys: str) -> float:
        view = menu.MenuView([menu.MenuRow(1, "TODO", "t0001 task")], handle)
        with create_pipe_input() as pin:
            with create_app_session(input=pin, output=DummyOutput()):
                pin.send_text(keys)
                session = menu.InlineMenuSession(view)
                start = time.perf_counter()
                session.run()
                return time.perf_counter() - start

    assert min(elapsed("\x1b") for _ in range(3)) < 0.25
    assert min(elapsed("q") for _ in range(3)) < 0.25


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_split_arrow_sequence_still_moves_within_the_flush_window() -> None:
    """Shortening the wait must not turn a straggling arrow key into an Esc."""
    import threading
    import time

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    def handle(_session: menu.InlineMenuSession, _result: menu.MenuResult) -> None:
        return

    def selection_after(gap: float) -> int:
        view = menu.MenuView(
            [menu.MenuRow(n, "TODO", f"t000{n} task") for n in (1, 2)],
            handle,
        )
        with create_pipe_input() as pin:
            with create_app_session(input=pin, output=DummyOutput()):
                session = menu.InlineMenuSession(view)

                def drive() -> None:
                    # Down arrow, torn in half the way a slow link tears it.
                    time.sleep(0.05)
                    pin.send_text("\x1b")
                    time.sleep(gap)
                    pin.send_text("[B")
                    time.sleep(0.05 + gap)
                    pin.send_text("q")

                driver = threading.Thread(target=drive)
                driver.start()
                session.run()
                driver.join()
        return view.selected_index

    # Arriving inside the window, the two halves are still one arrow key.
    # The 10ms tear is absolute on purpose: shrinking the window until a
    # plausible tear no longer fits is the way this fix could go wrong.
    assert selection_after(0.0) == 1
    assert selection_after(0.01) == 1
    # Past any sane window the lone byte is Esc, which leaves without moving.
    assert selection_after(0.4) == 0


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_text_input_preserves_shortcut_letters_and_help() -> None:
    # Text focus should type menu shortcut letters and survive a Help round trip.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    accepted: list[str] = []
    resumed: list[int] = []

    def accept(session: menu.InlineMenuSession, text: str) -> None:
        accepted.append(text)
        session.pop_view()

    def handle(session: menu.InlineMenuSession, result: menu.MenuResult) -> None:
        if result.action == "select":
            session.push_view(menu.TextInputView("", accept))

    def resume(session: menu.InlineMenuSession) -> None:
        assert isinstance(session.current_view, menu.MenuView)
        resumed.append(session.current_view.selected_index)

    parent = menu.MenuView(
        [menu.MenuRow(1, "TEXT", "Edit text")],
        handle,
        on_resume=resume,
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("\rbqjk\x07\x07ped\rq")
            session = menu.InlineMenuSession(parent, action_keys=("e", "p"))
            session.run()

    assert accepted == ["bqjkped"]
    assert resumed == [0]
    assert session.application.erase_when_done is False


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_text_input_escape_restores_parent_without_accepting() -> None:
    # Standalone Esc should discard field edits and restore the exact parent row.
    import threading
    import time

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    accepted: list[str] = []
    resumed: list[int] = []

    def handle(session: menu.InlineMenuSession, result: menu.MenuResult) -> None:
        if result.action == "select":
            session.push_view(
                menu.TextInputView(
                    "Original",
                    lambda _session, text: accepted.append(text),
                )
            )

    def resume(session: menu.InlineMenuSession) -> None:
        assert isinstance(session.current_view, menu.MenuView)
        resumed.append(session.current_view.selected_index)

    parent = menu.MenuView(
        [
            menu.MenuRow(1, "ONE", "First"),
            menu.MenuRow(2, "TWO", "Second"),
        ],
        handle,
        selected_index=1,
        on_resume=resume,
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            session = menu.InlineMenuSession(parent)

            def drive() -> None:
                pin.send_text("\r changed\x1b")
                time.sleep(0.05)
                pin.send_text("q")

            driver = threading.Thread(target=drive)
            driver.start()
            session.run()
            driver.join()

    assert accepted == []
    assert resumed == [1]
    assert session.current_view is parent
    assert parent.selected_index == 1


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_multiline_input_uses_enter_for_lines_and_ctrl_s_to_apply() -> None:
    # Multiline focus must preserve Enter for body text and use Ctrl-S to apply.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    accepted: list[str] = []

    def accept(session: menu.InlineMenuSession, text: str) -> None:
        accepted.append(text)
        session.pop_view()

    def handle(session: menu.InlineMenuSession, result: menu.MenuResult) -> None:
        if result.action == "select":
            session.push_view(menu.MultilineInputView("Original", accept))

    parent = menu.MenuView(
        [menu.MenuRow(1, "BODY", "Edit body")],
        handle,
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("\r\x15First line\rSecond line\x13q")
            menu.InlineMenuSession(parent).run()

    assert accepted == ["First line\nSecond line"]


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_workspace_keeps_multiple_fields_visible_and_focusable() -> None:
    # Workspace Tab, Help, multiline entry, and save should share one application.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.layout import HSplit
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.widgets import TextArea

    title = TextArea("Old title", multiline=False, height=1)
    body = TextArea("Old body", multiline=True)
    title.buffer.cursor_position = len(title.text)
    body.buffer.cursor_position = len(body.text)
    saved: list[tuple[str, str]] = []

    def save(_session: menu.InlineMenuSession) -> None:
        saved.append((title.text, body.text))

    view = menu.WorkspaceView(
        HSplit([title, body]),
        [title, body],
        save,
        edit_focus_indices=frozenset({0, 1}),
        multiline_edit_focus_indices=frozenset({1}),
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text(
                "\r\x15New title\r"
                "\x1b[B\r\x15First body line\rSecond body line"
                "\x07\x07\x13\t\x1b"
            )
            menu.InlineMenuSession(view).run()

    assert saved == [("New title", "First body line\nSecond body line")]
    assert view.focused_index == 0
    assert view.editing_index is None


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_workspace_arrows_navigate_until_enter_begins_editing() -> None:
    # Browse-mode arrows move fields; only Enter enables choice or text edits.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.layout import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.widgets import TextArea

    choice = Window(FormattedTextControl("Priority", focusable=True))
    text_area = TextArea("Old", multiline=False, height=1)
    text_area.buffer.cursor_position = len(text_area.text)
    changes: list[int] = []

    view = menu.WorkspaceView(
        HSplit([choice, text_area]),
        [choice, text_area],
        lambda _session: None,
        edit_focus_indices=frozenset({0, 1}),
        choice_focus_indices=frozenset({0}),
        on_choice_change=lambda _session, _index, delta: changes.append(delta),
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text(
                "\x1b[BX\x1b[A\x1b[C\x1b[D"
                "\r\x1b[C\r"
                "\x1b[B\rX\r\x1b"
            )
            menu.InlineMenuSession(view).run()

    assert changes == [1]
    assert text_area.text == "OldX"
    assert view.focused_index == 1
    assert view.editing_index is None


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_workspace_activates_focused_control() -> None:
    # Workspace Enter should dispatch application-owned button controls.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.layout import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.output import DummyOutput

    controls = [
        Window(FormattedTextControl("Field", focusable=True)),
        Window(FormattedTextControl("[ Action ]", focusable=True)),
    ]
    activated: list[int] = []

    def activate(session: menu.InlineMenuSession, focus_index: int) -> None:
        activated.append(focus_index)
        session.pop_view()

    view = menu.WorkspaceView(
        HSplit(controls),
        controls,
        lambda _session: None,
        activate_focus_indices=frozenset({1}),
        on_activate=activate,
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("\t\r")
            menu.InlineMenuSession(view).run()

    assert activated == [1]


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_workspace_moves_focused_list_region() -> None:
    # Workspace list regions should receive line and page movement commands.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.layout import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.output import DummyOutput

    control = Window(FormattedTextControl("List", focusable=True))
    moved: list[int] = []

    def move(
        session: menu.InlineMenuSession,
        _focus_index: int,
        direction: int,
    ) -> None:
        moved.append(direction)
        if direction == -5:
            session.pop_view()

    view = menu.WorkspaceView(
        HSplit([control]),
        [control],
        lambda _session: None,
        edit_focus_indices=frozenset({0}),
        list_focus_indices=frozenset({0}),
        on_list_move=move,
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("\r\x1b[B\x1b[6~\x1b[A\x1b[5~")
            menu.InlineMenuSession(view).run()

    assert moved == [1, 5, -1, -5]


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_workspace_resumes_after_child_view() -> None:
    # Popping a child should restore the same workspace and run its refresh hook.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.layout import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.output import DummyOutput

    control = Window(FormattedTextControl("Parent", focusable=True))
    resumed: list[str] = []

    def activate(session: menu.InlineMenuSession, _focus_index: int) -> None:
        def close_child(
            child_session: menu.InlineMenuSession,
            _result: menu.MenuResult,
        ) -> None:
            child_session.pop_view()

        session.push_view(
            menu.MenuView([menu.MenuRow(1, "CHILD", "Child")], close_child)
        )

    view = menu.WorkspaceView(
        HSplit([control]),
        [control],
        lambda _session: None,
        on_resume=lambda _session: resumed.append("parent"),
        activate_focus_indices=frozenset({0}),
        on_activate=activate,
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("\r\r\x1b")
            menu.InlineMenuSession(view).run()

    assert resumed == ["parent"]
    assert view.focused_index == 0


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_menu_session_suspends_external_command(monkeypatch) -> None:
    # External commands should run through terminal handoff and resume one app.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    calls = []

    async def fake_run_in_terminal(func, *, in_executor):
        assert in_executor is False
        return func()

    monkeypatch.setattr(menu, "run_in_terminal", fake_run_in_terminal)
    actions = {"e": menu.MenuAction("edit", "e", "Open external editor")}

    def handle(session: menu.InlineMenuSession, result: menu.MenuResult) -> None:
        if result.action != "edit":
            return

        def resumed() -> None:
            calls.append("resumed")
            session.pop_view()

        session.suspend(
            lambda: calls.append("external"),
            on_done=resumed,
        )

    view = menu.MenuView(
        [menu.MenuRow(1, "EDIT", "Open editor")],
        handle,
        actions=actions,
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("e")
            menu.InlineMenuSession(view, action_keys=("e",)).run()

    assert calls == ["external", "resumed"]


def test_task_workspace_exposes_title_body_and_compact_metadata(tmp_path: Path) -> None:
    # The task workspace should show title and body controls on the same screen.
    org_file = write(
        tmp_path / "tasks.org",
        """
        * Tasks
        ** TODO [#B] t0001 Parent
        Body line
        *** TODO t0001.1 Child
        """,
    )
    buf = taskui.OrgBuffer(org_file)
    parent = taskui.load_menu_items(buf)[0]
    project = manager.Project("demo", tmp_path, org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    view = controller._focus_view(parent)

    assert isinstance(view, menu.WorkspaceView)
    assert view.title_right == f"File: {org_file.resolve()}"
    assert [control.text for control in view.focus_targets[2:4]] == [
        "Parent",
        "Body line",
    ]
    assert len(view.focus_targets) == 6
    assert "Subtasks: 1" in view.summary
    assert view.focused_index == 2
    assert view.choice_focus_indices == frozenset({0, 1})
    assert view.edit_focus_indices == frozenset({0, 1, 2, 3, 4})
    assert view.multiline_edit_focus_indices == frozenset({3})
    assert view.list_focus_indices == frozenset({4})
    assert view.activate_focus_indices == frozenset({4, 5})
    assert view.editing_index is None
    assert view.is_dirty is not None and view.is_dirty() is False
    assert view.status_text is not None and view.status_text() == ""
    view.editing_index = 2
    view.focus_targets[2].buffer.insert_text(" changed")
    assert view.is_dirty() is True
    taskui._shift_priority(buf, parent, 1)
    assert view.status_text() == "FILE MODIFIED: 1 edit"


def test_task_workspace_subtasks_follow_org_tree_without_truncation(
    tmp_path: Path,
) -> None:
    # The scroll model should retain every descendant in Org hierarchy order.
    org_file = write(
        tmp_path / "tasks.org",
        """
        * Tasks
        ** TODO t0001 Parent
        *** TODO t9001 First child
        **** DONE t9002 Grandchild
        *** TODO t9003 Second child
        *** TODO t9004 Third child
        *** TODO t9005 Fourth child
        *** TODO t9006 Fifth child
        ** Notes
        *** TODO t9998 Outside parent subtree
        ** TODO t0002 Sibling
        """,
    )
    buf = taskui.OrgBuffer(org_file)
    parent = taskui.load_menu_items(buf)[0]

    descendants = taskui._descendant_subtasks(buf, parent)
    fragments = taskui._workspace_subtask_fragments(
        parent.task,
        descendants,
        5,
        active=True,
    )
    rendered = "".join(text for _style, text in fragments)

    assert [child.task.id for child in descendants] == [
        "t9001",
        "t9002",
        "t9003",
        "t9004",
        "t9005",
        "t9006",
    ]
    assert "t9001 First child" in rendered
    assert "  t9002 Grandchild" in rendered
    assert "t9006 Fifth child" in rendered
    assert "more subtask" not in rendered
    assert "t9998" not in rendered
    assert ("[SetCursorPosition]", "") in fragments
    assert any(
        style == "class:selected.todo" and "t9006 Fifth child" in text
        for style, text in fragments
    )


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_task_workspace_subtask_viewport_scrolls_to_selection(tmp_path: Path) -> None:
    # Selecting an off-screen descendant should scroll the five-row viewport.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(
        tmp_path / "tasks.org",
        "* Tasks\n"
        "** TODO t0001 Parent\n"
        "*** TODO t0001.1 Child one\n"
        "*** TODO t0001.2 Child two\n"
        "*** TODO t0001.3 Child three\n"
        "*** TODO t0001.4 Child four\n"
        "*** TODO t0001.5 Child five\n"
        "*** TODO t0001.6 Child six\n",
    )
    buf = taskui.OrgBuffer(org_file)
    project = manager.Project("demo", tmp_path, org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)
    view = controller._focus_view(taskui.load_menu_items(buf)[0])
    assert isinstance(view, menu.WorkspaceView)
    assert view.on_list_move is not None

    class QuietSession:
        def set_message(self, _message) -> None:
            pass

    view.on_list_move(QuietSession(), 4, 5)
    view.focused_index = 4
    viewport = view.focus_targets[4]
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("\x1b")
            menu.InlineMenuSession(view).run()

    assert viewport.render_info.window_height == taskui.WORKSPACE_SUBTASK_HEIGHT
    assert viewport.vertical_scroll <= 5
    assert 5 < viewport.vertical_scroll + taskui.WORKSPACE_SUBTASK_HEIGHT


def test_task_workspace_opens_selected_subtask_workspace(tmp_path: Path) -> None:
    # Enter on the subtask region should push the selected child's workspace.
    org_file = write(
        tmp_path / "tasks.org",
        "* Tasks\n"
        "** TODO t0001 Parent\n"
        "*** TODO t0001.1 First child\n"
        "*** TODO t0001.2 Second child\n",
    )
    buf = taskui.OrgBuffer(org_file)
    project = manager.Project("demo", tmp_path, org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)
    parent_view = controller._focus_view(taskui.load_menu_items(buf)[0])
    pushed: list[menu.InlineView] = []

    class ImmediateSession:
        def set_message(self, _message) -> None:
            pass

        def push_view(self, view: menu.InlineView) -> None:
            pushed.append(view)

    session = ImmediateSession()
    assert isinstance(parent_view, menu.WorkspaceView)
    assert parent_view.on_list_move is not None
    assert parent_view.on_activate is not None
    assert parent_view.is_dirty is not None
    parent_view.editing_index = 2
    parent_view.focus_targets[2].buffer.insert_text(" draft")
    parent_view.focused_index = 4
    parent_view.editing_index = 4

    parent_view.on_list_move(session, 4, 1)
    parent_view.on_activate(session, 4)

    assert len(pushed) == 1
    assert isinstance(pushed[0], menu.WorkspaceView)
    assert pushed[0].title == "Edit t0001.2"
    assert parent_view.focused_index == 4
    assert parent_view.focus_targets[2].text == "Parent draft"
    assert parent_view.is_dirty() is True


def test_task_workspace_button_opens_editor_at_task_line(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # The workspace editor button should suspend at the selected task heading.
    org_file = write(
        tmp_path / "tasks.org",
        "* Tasks\n** TODO t0001 Open me\nBody\n",
    )
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)
    opened: list[tuple[Path, int | None]] = []
    monkeypatch.setattr(
        taskui,
        "_open_editor",
        lambda target, line: opened.append((target.path, line)),
    )

    class ImmediateSession:
        def suspend(self, func, *, on_done) -> None:
            func()
            on_done()

        def replace_view(self, replacement) -> None:
            self.replacement = replacement

    view = controller._focus_view(taskui.load_menu_items(buf)[0])
    session = ImmediateSession()
    assert isinstance(view, menu.WorkspaceView)
    assert view.on_activate is not None

    view.on_activate(session, 4)

    assert opened == [(org_file.resolve(), 1)]
    assert isinstance(session.replacement, menu.WorkspaceView)


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_task_workspace_dirty_editor_button_defaults_to_continue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Unsaved task fields should not be discarded or opened past implicitly.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    original = "* Tasks\n** TODO t0001 Original\n"
    org_file = write(tmp_path / "tasks.org", original)
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)
    opened: list[int | None] = []
    monkeypatch.setattr(
        taskui,
        "_open_editor",
        lambda _target, line: opened.append(line),
    )

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Continue from editor warning, then explicitly discard on exit.
            pin.send_text("\r\r changed\r\t\t\r\r\x1bj\rq")
            controller.run()

    assert opened == []
    assert org_file.read_text(encoding="utf-8") == original


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_bounded_task_text_edit_validates_buffers_and_saves(tmp_path: Path) -> None:
    # Workspace apply should reject an empty title, then buffer and save a rename.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(
        tmp_path / "tasks.org",
        "* Tasks\n** TODO [#B] t0001 Original  :work:\nBody line\n",
    )
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Open the workspace, reject empty TITLE, apply a rename, and save.
            pin.send_text("\r\r\x15\x13bqjkped renamed\x13\x1b\x1bq")
            controller.run()

    assert org_file.read_text(encoding="utf-8") == (
        "* Tasks\n"
        "** TODO [#B] t0001 bqjkped renamed  :work:\n"
        "Body line\n"
    )
    assert buf.dirty is False
    assert not buf.autosave_path.exists()
    assert controller.session is not None
    assert controller.session.final_message == "Saved changes to tasks.org"


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_bounded_task_editor_updates_title_and_multiline_body(tmp_path: Path) -> None:
    # One bounded session should edit and save both issue title and own-body text.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(
        tmp_path / "tasks.org",
        (
            "* Tasks\n"
            "** TODO [#B] t0001 Original title  :work:\n"
            "Old body\n"
            "*** TODO t0001.1 Child\n"
            "Child body\n"
            "** TODO t0002 Neighbor\n"
        ),
    )
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Edit TITLE and BODY together, apply once, then save the task buffer.
            pin.send_text(
                "\r\r\x15Renamed title\r"
                "\x1b[B\r\x15First body line\rSecond body line\x13"
                "\x1b\x1bq"
            )
            controller.run()

    assert org_file.read_text(encoding="utf-8") == (
        "* Tasks\n"
        "** TODO [#B] t0001 Renamed title  :work:\n"
        "First body line\n"
        "Second body line\n"
        "*** TODO t0001.1 Child\n"
        "Child body\n"
        "** TODO t0002 Neighbor\n"
    )
    assert buf.dirty is False
    assert not buf.autosave_path.exists()
    assert controller.session is not None
    assert controller.session.final_message == "Saved changes to tasks.org"


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_task_workspace_ctrl_s_saves_whole_file_and_resets_undo(tmp_path: Path) -> None:
    # Ctrl-S should flush prior buffered edits and make pre-save undo unavailable.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(
        tmp_path / "tasks.org",
        "* Tasks\n** TODO t0001 Original\nBody\n",
    )
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Raise priority in the list, edit title, save, try undo, then leave.
            pin.send_text("\x1b[1;2A\r\r saved\x13\x1f\x1b\x1bq")
            controller.run()

    assert org_file.read_text(encoding="utf-8") == (
        "* Tasks\n** TODO [#C] t0001 Original saved\nBody\n"
    )
    assert buf.dirty is False
    assert buf.can_undo is False
    assert buf.can_redo is False
    assert not buf.autosave_path.exists()


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_task_workspace_compact_controls_save_state_and_priority(tmp_path: Path) -> None:
    # Staged compact choices should save with the rest of the task workspace.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(
        tmp_path / "tasks.org",
        "* Tasks\n** TODO t0001 Original\nBody\n",
    )
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Enter each compact field before changing Priority and State.
            pin.send_text(
                "\r\x1b[Z\r\x1b[C\r"
                "\x1b[Z\r\x1b[C\x1b[C\r\x13\x1bq"
            )
            controller.run()

    assert org_file.read_text(encoding="utf-8") == (
        "* Tasks\n** MOOT [#C] t0001 Original\nBody\n"
    )
    assert buf.dirty is False
    assert buf.can_undo is False
    assert not buf.autosave_path.exists()


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_task_workspace_compact_controls_discard_with_other_fields(tmp_path: Path) -> None:
    # Dirty-exit Discard should restore staged metadata as well as title and body.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    original = "* Tasks\n** TODO t0001 Original\n"
    org_file = write(tmp_path / "tasks.org", original)
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Change Priority, Escape, choose Discard below Continue, then quit.
            pin.send_text("\r\x1b[Z\r\x1b[C\r\x1bj\rq")
            controller.run()

    assert org_file.read_text(encoding="utf-8") == original
    assert buf.read() == original
    assert buf.dirty is False


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_task_list_undo_redo_and_save_shortcuts(tmp_path: Path) -> None:
    # C-/, C-r, and C-s should undo, redo, and checkpoint logical list edits.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(
        tmp_path / "tasks.org",
        "* Tasks\n** TODO t0001 Original\n",
    )
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Change state and priority, undo/redo priority, save, then leave cleanly.
            pin.send_text("\x1b[1;2C\x1b[1;2A\x1f\x12\x13q")
            controller.run()

    assert org_file.read_text(encoding="utf-8") == (
        "* Tasks\n** DONE [#C] t0001 Original\n"
    )
    assert buf.dirty is False
    assert buf.can_undo is False
    assert buf.can_redo is False
    assert not buf.autosave_path.exists()
    assert controller.session is not None
    assert controller.session.final_message == "Saved changes to tasks.org"


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_dirty_workspace_escape_continues_by_default(tmp_path: Path) -> None:
    # The dirty-exit warning should default to continuing without losing controls.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(
        tmp_path / "tasks.org",
        "* Tasks\n** TODO t0001 Original\n",
    )
    project = manager.Project("demo", tmp_path, org_file)
    controller = taskui.InteractiveTaskController(
        project,
        taskui.OrgBuffer(org_file),
        include_done=True,
    )

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Escape, accept default Continue, then save the still-present edit.
            pin.send_text("\r\r continued\r\x1b\r\x13\x1bq")
            controller.run()

    assert "** TODO t0001 Original continued" in org_file.read_text(
        encoding="utf-8"
    )


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_dirty_workspace_escape_can_save_and_return(tmp_path: Path) -> None:
    # Choosing Save in the dirty-exit warning should write the file and leave.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(
        tmp_path / "tasks.org",
        "* Tasks\n** TODO t0001 Original\n",
    )
    project = manager.Project("demo", tmp_path, org_file)
    controller = taskui.InteractiveTaskController(
        project,
        taskui.OrgBuffer(org_file),
        include_done=True,
    )

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Escape opens warning; Up selects Save and returns to the task list.
            pin.send_text("\r\r saved-on-exit\r\x1bk\rq")
            controller.run()

    assert "** TODO t0001 Original saved-on-exit" in org_file.read_text(
        encoding="utf-8"
    )


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_dirty_workspace_escape_can_discard_unapplied_edits(tmp_path: Path) -> None:
    # Choosing Discard should leave the saved Org file and OrgBuffer unchanged.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    original = "* Tasks\n** TODO t0001 Original\n"
    org_file = write(tmp_path / "tasks.org", original)
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Escape opens warning; Down selects explicit Discard, then quit.
            pin.send_text("\r\r discarded\r\x1bj\rq")
            controller.run()

    assert org_file.read_text(encoding="utf-8") == original
    assert buf.read() == original
    assert buf.dirty is False


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_menu_session_empty_rows_allows_exit_and_actions() -> None:
    # An empty list still honors stack-pop keys and action hotkeys, reporting
    # index None rather than a row that is not there.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    seen: list[menu.MenuResult] = []

    def run(keys: str) -> menu.MenuResult:
        seen.clear()
        view = menu.MenuView(
            [],
            lambda _session, result: seen.append(result),
            actions={"e": menu.MenuAction("edit", "e", "Open in editor")},
        )
        with create_pipe_input() as pin:
            with create_app_session(input=pin, output=DummyOutput()):
                pin.send_text(keys)
                return menu.InlineMenuSession(view, action_keys=("e",)).run()

    assert run("q") == menu.MenuResult("back", None)
    assert run("b") == menu.MenuResult("back", None)
    assert run("eq") == menu.MenuResult("back", None)
    assert seen == [menu.MenuResult("edit", None)]


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
@pytest.mark.parametrize("exit_keys", ["q", "\x18"])
def test_inline_menu_clean_exit_is_immediate(exit_keys: str) -> None:
    # Root Back and C-x should immediately leave an opted-in clean session.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    view = menu.MenuView(
        [menu.MenuRow(1, "TODO", "t0001 task")],
        lambda _session, _result: None,
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text(exit_keys)
            session = menu.InlineMenuSession(view, exit_name="test app")
            result = session.run()

    assert result == menu.MenuResult("exit", None)
    assert session.current_view is view
    assert len(session.views) == 1
    assert session.application.erase_when_done is False


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_menu_global_exit_preserves_active_text_context() -> None:
    # A footer-prompt cancel should preserve the active field and exact draft.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    saved: list[str] = []
    text_view = menu.TextInputView(
        "",
        lambda _session, _text: None,
        title="Draft title",
    )

    def handle(session: menu.InlineMenuSession, result: menu.MenuResult) -> None:
        if result.action == "select":
            session.push_view(text_view)

    parent = menu.MenuView(
        [menu.MenuRow(1, "TEXT", "Edit title")],
        handle,
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Open the field, cancel the exit question, keep typing, then save.
            pin.send_text("\rdraft\x18\x03!\x18y")
            session = menu.InlineMenuSession(
                parent,
                exit_name="test app",
                exit_concerns=lambda: (
                    menu.ExitConcern(
                        "draft",
                        dirty=True,
                        save=lambda: (
                            saved.append(text_view.text)
                            or menu.ExitActionResult(True)
                        ),
                        discard=lambda: menu.ExitActionResult(True),
                    ),
                ),
            )
            result = session.run()

    assert result == menu.MenuResult("exit", None)
    assert text_view.text == "draft!"
    assert saved == ["draft!"]
    assert session.current_view is text_view
    assert len(session.views) == 2


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_menu_dirty_exit_prompt_replaces_only_footer(monkeypatch) -> None:
    # Requesting dirty exit must leave the current body and view stack intact.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.output import DummyOutput

    view = menu.MenuView(
        [menu.MenuRow(1, "TODO", "t0001 task")],
        lambda _session, _result: None,
        instruction="C-g help · Esc back",
    )
    concern = menu.ExitConcern(
        "tasks.org",
        dirty=True,
        save=lambda: menu.ExitActionResult(True),
        discard=lambda: menu.ExitActionResult(True),
    )
    with create_app_session(output=DummyOutput()):
        session = menu.InlineMenuSession(
            view,
            exit_name="test app",
            exit_concerns=lambda: (concern,),
        )
        monkeypatch.setattr(session.application, "invalidate", lambda: None)
        body_before = str(session._render_body())

        session.request_exit()

    assert session.current_view is view
    assert len(session.views) == 1
    assert str(session._render_body()) == body_before
    assert "Save modified file? Y Yes | N No | ^C Cancel" in str(
        session._render_footer()
    )


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_project_menu_uses_one_application_for_nested_task_views(
    tmp_path: Path, monkeypatch
) -> None:
    # Project, task, detail, Help, and picker views should share one application.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    project_dir = tmp_path / "sample"
    write(
        project_dir / "todo.org",
        """
        * Tasks
        ** TODO t0001 Pick me
        """,
    )
    register(tmp_path / "registry", "sample", project_dir)
    real_application = menu.Application
    applications = []

    def tracked_application(*args, **kwargs):
        app = real_application(*args, **kwargs)
        applications.append(app)
        return app

    def obsolete_path(*_args, **_kwargs):
        raise AssertionError("project mode must not start a one-shot selector")

    monkeypatch.setattr(menu, "interactive_select_available", lambda: True)
    monkeypatch.setattr(menu, "Application", tracked_application)
    monkeypatch.setattr(projmgr.taskui, "task_menu", obsolete_path)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Open project/workspace, visit Help, then use task-list priority/back.
            pin.send_text("\r\r\x07q\x1bpqqq")
            assert projmgr.project_menu(
                tmp_path / "registry",
                str(tmp_path / "registry"),
                include_done=True,
            ) == 0

    assert len(applications) == 1
    assert applications[0].full_screen is False


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_project_task_save_returns_to_same_project(tmp_path: Path) -> None:
    # Saving a child task context should restore its project selection, not exit.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    registry = tmp_path / "registry"
    first = write(
        tmp_path / "src" / "alpha" / "tasks.org",
        "* Tasks\n** TODO t0001 First\n",
    )
    register(registry, "alpha", first.parent)
    second = write(
        tmp_path / "src" / "beta" / "tasks.org",
        "* Tasks\n** TODO t0001 Second\n",
    )
    register(registry, "beta", second.parent)
    controller = projmgr._ProjectBrowser(registry, str(registry), include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Select beta, edit state, save, then leave the restored project view.
            pin.send_text("j\r\x1b[1;2Cq\rq")
            controller.run()

    assert controller.session is not None
    assert len(controller.session.views) == 1
    assert controller.session.current_view.title == projmgr.NAVIGATOR.title
    assert controller.session.current_view.selected_index == 1
    assert controller.session.final_message == "Saved changes to tasks.org"
    assert controller.session.message == "Saved changes to tasks.org"
    assert "** TODO t0001 First" in first.read_text(encoding="utf-8")
    assert "** DONE t0001 Second" in second.read_text(encoding="utf-8")


def test_task_menu_order_preserves_org_file_hierarchy(tmp_path: Path) -> None:
    # Menus should display tasks in file order so parent/child hierarchy stays intact.
    org_file = write(
        tmp_path / "todo.org",
        """
        * Tasks
        ** TODO t0001 parent
        *** TODO t0001.1 child
        ** TODO [#A] t0002 priority sibling
        *** DONE t0002.1 done child
        ** TODO t0003 last
        """,
    )
    buf = taskui.OrgBuffer(org_file)

    ids = [item.task.id for item in taskui.load_menu_items(buf, include_done=True)]
    sorted_ids = [
        task.id
        for task in sorted(core.parse_org(buf.read()), key=taskui._stable_sort_key)
    ]

    assert ids == ["t0001", "t0001.1", "t0002", "t0002.1", "t0003"]
    assert sorted_ids == ids


def test_interactive_task_tree_starts_collapsed_with_disclosure_cues(
    tmp_path: Path,
) -> None:
    # The task dashboard should begin at overview depth and visibly mark parents.
    org_file = write(
        tmp_path / "tasks.org",
        """
        * Tasks
        ** TODO t0001 Parent
        *** TODO t0001.1 Child
        **** TODO t0001.1.1 Grandchild
        ** DONE t0002 Leaf
        """,
    )
    project = manager.Project("demo", tmp_path, org_file)
    controller = taskui.InteractiveTaskController(
        project,
        taskui.OrgBuffer(org_file),
        include_done=True,
    )

    view = controller.initial_view()

    assert [row.text for row in view.rows] == ["t0001 Parent", "t0002 Leaf"]
    assert [row.disclosure for row in view.rows] == ["▸", " "]
    assert view.title_right == f"File: {org_file.resolve()}"
    assert view.summary == "Open: 3  Done: 1  Total: 4"
    rendered = "".join(part[1] for part in menu._render_menu_rows(view.rows, 0))
    assert "▶" in rendered
    assert "▸ t0001 Parent" in rendered


def test_task_tree_respects_non_task_heading_boundaries(tmp_path: Path) -> None:
    # Ordinary Org headings must break or preserve task ancestry by structure.
    org_file = write(
        tmp_path / "notes.org",
        """
        * TODO t0001 First root
        * Notes
        ** TODO t0002 Root beneath non-task heading
        * TODO t0003 Second root
        ** Discussion
        *** TODO t0003.1 Child through non-task heading
        """,
    )
    buf = taskui.OrgBuffer(org_file)
    items = taskui.load_menu_items(buf)

    parents, children, depths = taskui._task_tree(items, buf.read())

    assert parents == {"t0003.1": "t0003"}
    assert children == {"t0003": ["t0003.1"]}
    assert depths == {"t0001": 0, "t0002": 0, "t0003": 0, "t0003.1": 1}


def test_interactive_task_tree_actions_preserve_hierarchy_and_selection(
    tmp_path: Path,
) -> None:
    # Local and global folds should reveal levels and anchor hidden descendants.
    org_file = write(
        tmp_path / "tasks.org",
        """
        * Tasks
        ** TODO t0001 Parent
        *** TODO t0001.1 Child
        **** TODO t0001.1.1 Grandchild
        ** TODO t0002 Sibling
        """,
    )
    project = manager.Project("demo", tmp_path, org_file)
    controller = taskui.InteractiveTaskController(
        project,
        taskui.OrgBuffer(org_file),
        include_done=True,
    )

    class ReplacingSession:
        def __init__(self, view: menu.MenuView) -> None:
            self.current_view = view
            self.message: str | None = None

        def replace_view(self, view: menu.MenuView) -> None:
            self.current_view = view

        def set_message(self, message: str) -> None:
            self.message = message

    session = ReplacingSession(controller.initial_view())

    session.current_view.on_result(session, menu.MenuResult("fold", 0))
    assert [row.text for row in session.current_view.rows] == [
        "t0001 Parent",
        "t0001.1 Child",
        "t0002 Sibling",
    ]
    assert [row.disclosure for row in session.current_view.rows] == ["▾", "▸", " "]
    assert [row.tree_depth for row in session.current_view.rows] == [0, 1, 0]

    session.current_view.on_result(session, menu.MenuResult("tree_right", 1))
    session.current_view.on_result(session, menu.MenuResult("tree_right", 1))
    assert session.current_view.selected_index == 2
    assert session.current_view.rows[2].text == "t0001.1.1 Grandchild"

    session.current_view.on_result(session, menu.MenuResult("tree_left", 2))
    assert session.current_view.selected_index == 1
    session.current_view.on_result(session, menu.MenuResult("fold_all", 1))
    assert len(session.current_view.rows) == 2
    assert session.current_view.selected_index == 0
    assert session.current_view.rows[0].text == "t0001 Parent"


def test_filtered_task_tree_retains_ancestors_as_context(tmp_path: Path) -> None:
    # A matching child should remain reachable beneath a nonmatching parent.
    org_file = write(
        tmp_path / "tasks.org",
        """
        * Tasks
        ** DONE t0001 Completed parent
        *** TODO t0001.1 Open child
        ** DONE t0002 Completed leaf
        """,
    )
    project = manager.Project("demo", tmp_path, org_file)
    controller = taskui.InteractiveTaskController(
        project,
        taskui.OrgBuffer(org_file),
        include_done=False,
    )

    view = controller.initial_view()

    assert [row.text for row in view.rows] == ["t0001 Completed parent"]
    assert view.rows[0].disclosure == "▸"
    assert view.summary == "Open: 1  Done: 0  Total: 1"


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_interactive_task_tree_keybindings(tmp_path: Path) -> None:
    # Terminal key sequences should drive local folds and directional tree motion.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(
        tmp_path / "tasks.org",
        "* Tasks\n** TODO t0001 Parent\n*** TODO t0001.1 Child\n"
        "** TODO t0002 Sibling\n",
    )
    project = manager.Project("demo", tmp_path, org_file)
    controller = taskui.InteractiveTaskController(
        project,
        taskui.OrgBuffer(org_file),
        include_done=True,
    )

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Right expands and enters; Left returns; S-Tab folds all; Tab reopens.
            pin.send_text("\x1b[C\x1b[C\x1b[D\x1b[Z\tq")
            controller.run()

    assert controller.session is not None
    view = controller.session.current_view
    assert isinstance(view, menu.MenuView)
    assert [row.text for row in view.rows] == [
        "t0001 Parent",
        "t0001.1 Child",
        "t0002 Sibling",
    ]
    assert view.selected_index == 0


def test_load_menu_items_defaults_to_all_task_states(tmp_path: Path) -> None:
    # The shared task view starts with TODO and DONE rows visible, then filters explicitly.
    org_file = write(
        tmp_path / "todo.org",
        """
        * Tasks
        ** TODO t0001 open
        ** DONE t0002 done
        """,
    )
    buf = taskui.OrgBuffer(org_file)

    assert [item.task.id for item in taskui.load_menu_items(buf)] == [
        "t0001",
        "t0002",
    ]
    assert [item.task.id for item in taskui.load_menu_items(buf, include_done=False)] == [
        "t0001",
    ]
    assert [item.task.id for item in taskui.load_menu_items(buf, filter_mode="done")] == [
        "t0002",
    ]


def test_leaf_tree_navigation_uses_transient_warning(tmp_path: Path) -> None:
    # Right on a leaf task should report a temporary notice, not a sticky footer.
    org_file = write(tmp_path / "tasks.org", "* Tasks\n** TODO t0001 Leaf\n")
    project = manager.Project("demo", tmp_path, org_file)
    controller = taskui.InteractiveTaskController(
        project,
        taskui.OrgBuffer(org_file),
        include_done=True,
    )
    view = controller.initial_view()
    notices: list[str] = []

    class NoticeSession:
        def set_transient_message(self, message: str) -> None:
            notices.append(message)

    view.on_result(NoticeSession(), menu.MenuResult("tree_right", 0))

    assert notices == ["t0001 has no visible subtasks"]


def test_anchor_index_follows_task_and_clamps() -> None:
    # Build MenuItems directly so the helper is tested in isolation.
    org = "* Tasks\n** TODO t0001 a\n** TODO t0002 b\n** TODO t0003 c\n"
    items = [
        taskui.MenuItem(label=t.text, detail="ortask task", task=t, line_num=t.line_num)
        for t in core.parse_org(org)
    ]
    assert taskui._anchor_index(items, "t0002", 0) == 1     # follows the id
    assert taskui._anchor_index(items, "t0999", 2) == 2     # missing -> fallback
    assert taskui._anchor_index(items, "t0999", 99) == 2    # fallback clamped
    assert taskui._anchor_index([], "t0001", 5) == 0        # empty list


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_interactive_toggle_keeps_highlight_on_same_task(tmp_path: Path, monkeypatch) -> None:
    # t0007 end-to-end: toggling the highlighted task twice must cancel out,
    # which only holds if the highlight stays on that same task after a reload.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(
        tmp_path / "todo.org",
        """
        * Tasks
        ** TODO t0001 alpha
        ** TODO t0002 beta
        ** TODO t0003 gamma
        """,
    )
    original = org_file.read_text(encoding="utf-8")
    project = manager.Project(name="demo", path=tmp_path, org_file=org_file)
    buf = taskui.OrgBuffer(org_file)
    monkeypatch.setattr(menu, "interactive_select_available", lambda: True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Shift-Right twice toggles highlighted, toggles it back, then quits.
            pin.send_text("\x1b[1;2C\x1b[1;2Cq")
            taskui._interactive_task_menu(project, buf, include_done=True)

    # Two toggles of the same task cancel out in the buffer (so the highlight
    # stayed put), and nothing was written to the real file (still buffered).
    assert buf.read() == original
    assert org_file.read_text(encoding="utf-8") == original


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_interactive_priority_shortcuts_keep_selected_task(
    tmp_path: Path, monkeypatch
) -> None:
    # Repeated Shift-Up edits should stay anchored through the bounded save view.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(
        tmp_path / "todo.org",
        "* Tasks\n** TODO t0001 alpha\n** TODO t0002 beta\n",
    )
    original = org_file.read_text(encoding="utf-8")
    project = manager.Project(name="demo", path=tmp_path, org_file=org_file)
    buf = taskui.OrgBuffer(org_file)
    monkeypatch.setattr(menu, "interactive_select_available", lambda: True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("\x1b[1;2A\x1b[1;2Aqy")
            taskui._interactive_task_menu(project, buf, include_done=True)

    assert "** TODO [#B] t0001 alpha" in buf.read()
    assert "** TODO t0002 beta" in buf.read()
    assert org_file.read_text(encoding="utf-8") == buf.read()
    assert org_file.read_text(encoding="utf-8") != original


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_interactive_exit_gateway_saves_explicitly(tmp_path: Path) -> None:
    # A dirty task-list exit should save only after selecting Save and Exit.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(tmp_path / "tasks.org", "* Tasks\n** TODO t0001 alpha\n")
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("\x1b[1;2Cqy")
            controller.run()

    assert org_file.read_text(encoding="utf-8") == "* Tasks\n** DONE t0001 alpha\n"
    assert buf.dirty is False
    assert not buf.autosave_path.exists()
    assert controller.session is not None
    assert controller.session.message == "Saved changes to tasks.org"
    assert controller.session.final_message == "Saved changes to tasks.org"
    assert controller.session.application.erase_when_done is False


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_interactive_ctrl_x_saves_active_workspace_draft(tmp_path: Path) -> None:
    # C-x should apply an active field draft before saving the complete Org file.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(tmp_path / "tasks.org", "* Tasks\n** TODO t0001 Original\n")
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Open TITLE, type without applying it, then choose Save and Exit.
            pin.send_text("\r\r revised\x18y")
            controller.run()

    assert org_file.read_text(encoding="utf-8") == (
        "* Tasks\n** TODO t0001 Original revised\n"
    )
    assert buf.dirty is False
    assert not buf.autosave_path.exists()
    assert controller.session is not None
    assert controller.session.final_message == "Saved changes to tasks.org"


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_interactive_exit_prompt_blocks_active_field_edits(tmp_path: Path) -> None:
    # Keys other than Y/N/C-c must not leak into a field behind the footer prompt.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(tmp_path / "tasks.org", "* Tasks\n** TODO t0001 Original\n")
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Backspace, Left, and X are ignored until C-c restores field editing.
            pin.send_text("\r\r revised\x18\x7f\x1b[DX\x03\x13\x18")
            controller.run()

    assert org_file.read_text(encoding="utf-8") == (
        "* Tasks\n** TODO t0001 Original revised\n"
    )


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_interactive_ctrl_x_discards_active_workspace_draft(tmp_path: Path) -> None:
    # Discard and Exit should drop an unapplied field draft without touching disk.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    original = "* Tasks\n** TODO t0001 Original\n"
    org_file = write(tmp_path / "tasks.org", original)
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Open TITLE, type without applying it, then choose Discard and Exit.
            pin.send_text("\r\r discarded\x18n")
            controller.run()

    assert org_file.read_text(encoding="utf-8") == original
    assert buf.read() == original
    assert buf.dirty is False
    assert not buf.autosave_path.exists()
    assert controller.session is not None
    assert controller.session.final_message == "Discarded changes to tasks.org"


def test_task_exit_preflight_preserves_draft_on_external_change(
    tmp_path: Path,
) -> None:
    # A failed exact-source check must retain both the draft and buffered edit.
    original = "* Tasks\n** TODO t0001 Original\n"
    external = "* Tasks\n** TODO t0001 Changed elsewhere\n"
    org_file = write(tmp_path / "tasks.org", original)
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)
    workspace = controller._focus_view(taskui.load_menu_items(buf)[0])
    assert isinstance(workspace, menu.WorkspaceView)
    workspace.editing_index = 2
    workspace.focus_targets[2].buffer.insert_text(" revised")

    class WorkspaceSession:
        views = [controller.initial_view(), workspace]

    controller.session = WorkspaceSession()
    org_file.write_text(external, encoding="utf-8")

    concern = controller.exit_concern()
    assert concern.dirty is True
    assert concern.prepare is not None
    result = concern.prepare()

    assert result.success is False
    assert "changed externally" in result.message
    assert "Original revised" in buf.read()
    assert workspace.is_dirty is not None and workspace.is_dirty() is True
    assert org_file.read_text(encoding="utf-8") == external
    assert buf.autosave_path.exists()


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_interactive_exit_gateway_cancel_then_discard(tmp_path: Path) -> None:
    # Back should cancel the first exit, while a later Discard leaves disk unchanged.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(tmp_path / "tasks.org", "* Tasks\n** TODO t0001 alpha\n")
    original = org_file.read_text(encoding="utf-8")
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Open, cancel, reopen, move to Discard, and confirm.
            pin.send_text("\x1b[1;2Cq\x03qn")
            controller.run()

    assert org_file.read_text(encoding="utf-8") == original
    assert buf.read() == original
    assert buf.dirty is False
    assert not buf.autosave_path.exists()
    assert controller.session is not None
    assert controller.session.message == "Discarded changes to tasks.org"
    assert controller.session.final_message == "Discarded changes to tasks.org"
    assert controller.session.application.erase_when_done is False


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
@pytest.mark.parametrize("keep_keys", ["\rq", "qq"])
def test_interactive_recovery_view_keeps_data_by_default(
    tmp_path: Path,
    keep_keys: str,
    monkeypatch,
) -> None:
    # Enter and Back should both preserve recovery data and open the saved tasks.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    original = "* Tasks\n** TODO t0001 alpha\n"
    recovered = "* Tasks\n** DONE t0001 alpha\n"
    org_file = write(tmp_path / "tasks.org", original)
    autosave = taskui.autosave_path_for(org_file)
    autosave.write_text(recovered, encoding="utf-8")
    project = manager.Project("demo", tmp_path, org_file)

    def unexpected_prompt(_label):
        raise AssertionError("interactive recovery must not start a separate prompt")

    monkeypatch.setattr(menu, "interactive_select_available", lambda: True)
    monkeypatch.setattr(menu, "prompt_text", unexpected_prompt)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text(keep_keys)
            taskui.task_menu(project, include_done=True)

    assert org_file.read_text(encoding="utf-8") == original
    assert autosave.read_text(encoding="utf-8") == recovered


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_interactive_recovery_view_recovers_then_saves(tmp_path: Path) -> None:
    # Recover should load the auto-save as dirty data for the bounded save flow.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    original = "* Tasks\n** TODO t0001 alpha\n"
    recovered = "* Tasks\n** DONE t0001 alpha\n"
    org_file = write(tmp_path / "tasks.org", original)
    autosave = taskui.autosave_path_for(org_file)
    autosave.write_text(recovered, encoding="utf-8")
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            # Select Recover, leave the dirty task view, then accept Save.
            pin.send_text("j\rqy")
            controller.run()

    assert buf.read() == recovered
    assert org_file.read_text(encoding="utf-8") == recovered
    assert buf.dirty is False
    assert not autosave.exists()


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_interactive_recovery_view_discards_explicitly(tmp_path: Path) -> None:
    # Only selecting Discard should delete recovery data without changing disk.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    original = "* Tasks\n** TODO t0001 alpha\n"
    recovered = "* Tasks\n** DONE t0001 alpha\n"
    org_file = write(tmp_path / "tasks.org", original)
    autosave = taskui.autosave_path_for(org_file)
    autosave.write_text(recovered, encoding="utf-8")
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("jj\rq")
            controller.run()

    assert buf.read() == original
    assert org_file.read_text(encoding="utf-8") == original
    assert not autosave.exists()


# ---------------------------------------------------------------------------
# OrgBuffer auto-save / save-on-exit (t0006)
# ---------------------------------------------------------------------------

def test_org_buffer_autosave_path_naming(tmp_path: Path) -> None:
    assert taskui.autosave_path_for(tmp_path / "todo.org") == tmp_path / "#todo.org#"
    assert taskui.autosave_path_for(tmp_path / "a.task.org") == tmp_path / "#a.task.org#"


def test_org_buffer_apply_save_and_discard(tmp_path: Path) -> None:
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    original = org_file.read_text(encoding="utf-8")
    buf = taskui.OrgBuffer(org_file)
    assert buf.dirty is False and not buf.autosave_path.exists()

    buf.apply(["* Tasks", "** DONE t0001 one"])
    assert buf.dirty is True
    assert org_file.read_text(encoding="utf-8") == original          # real file untouched
    assert buf.autosave_path.read_text(encoding="utf-8") == buf.read()

    # discard reverts the buffer and removes the auto-save; real file unchanged.
    buf.discard()
    assert buf.read() == original
    assert buf.dirty is False
    assert not buf.autosave_path.exists()
    assert org_file.read_text(encoding="utf-8") == original
    assert buf.can_undo is False and buf.can_redo is False

    # apply then save commits to the real file and clears the auto-save.
    buf.apply(["* Tasks", "** DONE t0001 one"])
    buf.save()
    assert org_file.read_text(encoding="utf-8") == "* Tasks\n** DONE t0001 one\n"
    assert not buf.autosave_path.exists()
    assert buf.dirty is False
    assert buf.can_undo is False and buf.can_redo is False


def test_org_buffer_stale_save_preserves_disk_buffer_and_history(tmp_path: Path) -> None:
    # A changed preimage refuses save without losing either writer's version.
    original = "* Tasks\n** TODO t0001 one\n"
    external = "* Tasks\n** TODO t0001 externally edited\n"
    org_file = write(tmp_path / "todo.org", original)
    buf = taskui.OrgBuffer(org_file)
    buf.apply_text(
        "* Tasks\n** DONE t0001 one\n",
        description="Finish t0001",
    )
    org_file.write_text(external, encoding="utf-8")

    with pytest.raises(taskui.BufferChangedError, match="changed since"):
        buf.save()

    assert org_file.read_text(encoding="utf-8") == external
    assert buf.read() == "* Tasks\n** DONE t0001 one\n"
    assert buf.dirty is True
    assert buf.undo_count == 1
    assert buf.autosave_path.read_text(encoding="utf-8") == buf.read()


def test_org_buffer_poll_records_dirty_external_text(tmp_path: Path) -> None:
    # Dirty polling captures Theirs without changing Base, Ours, history, or auto-save.
    original = "* Tasks\n** TODO t0001 one\n"
    ours = "* Tasks\n** DONE t0001 one\n"
    theirs = "* Tasks\n** TODO t0001 externally changed\n"
    org_file = write(tmp_path / "todo.org", original)
    buf = taskui.OrgBuffer(org_file)
    buf.apply_text(ours, description="Finish t0001")
    org_file.write_text(theirs, encoding="utf-8")

    change = buf.check_external_change()

    assert change is not None and change.kind == "changed"
    assert change.text == theirs
    assert buf.external_change == change
    assert buf.saved_text == original
    assert buf.read() == ours
    assert buf.undo_count == 1
    assert buf.autosave_path.read_text(encoding="utf-8") == ours


def test_org_buffer_rebase_external_change_is_one_undoable_transaction(
    tmp_path: Path,
) -> None:
    # Rebase adopts Theirs, autosaves merged Ours, and never writes the real file.
    original = "* alpha\nAlpha.\n* bravo\nBravo.\n"
    ours = original.replace("* alpha", "* [#C] alpha")
    theirs = original.replace("* bravo", "* [#A] bravo")
    merged = theirs.replace("* alpha", "* [#C] alpha")
    org_file = write(tmp_path / "projects.org", original)
    buf = taskui.OrgBuffer(org_file)
    buf.apply_text(ours, description="Set alpha priority to C")
    buf.apply_text(
        ours.replace("[#C]", "[#B]"),
        description="Set alpha priority to B",
    )
    assert buf.undo() == "Set alpha priority to B"
    assert buf.can_redo is True
    org_file.write_text(theirs, encoding="utf-8")
    change = buf.check_external_change()
    assert change is not None

    changed = buf.rebase_external_change(
        change,
        merged,
        description="Reapply alpha after external changes",
    )

    assert changed is True
    assert org_file.read_text(encoding="utf-8") == theirs
    assert buf.saved_text == theirs
    assert buf.read() == merged
    assert buf.dirty is True
    assert buf.undo_count == 1
    assert buf.can_redo is False
    assert buf.external_change is None
    assert buf.autosave_path.read_text(encoding="utf-8") == merged
    assert buf.undo() == "Reapply alpha after external changes"
    assert buf.read() == theirs
    assert buf.dirty is False
    assert not buf.autosave_path.exists()
    assert buf.redo() == "Reapply alpha after external changes"
    buf.discard()
    assert buf.read() == theirs
    assert org_file.read_text(encoding="utf-8") == theirs
    assert not buf.autosave_path.exists()


def test_org_buffer_poll_ignores_a_touched_identical_file(tmp_path: Path) -> None:
    # A metadata hint with byte-identical text refreshes the hint without an alert.
    original = "* Tasks\n** TODO t0001 one\n"
    org_file = write(tmp_path / "todo.org", original)
    buf = taskui.OrgBuffer(org_file)
    stat = org_file.stat()
    os.utime(
        org_file,
        ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000),
    )

    assert buf.check_external_change() is None
    assert buf.saved_text == original
    assert buf.read() == original
    assert buf.external_change is None


def test_org_buffer_poll_adopts_a_replaced_clean_file(tmp_path: Path) -> None:
    # Replacing the inode with changed text reloads a clean buffer and clears redo.
    original = "* Tasks\n** TODO t0001 one\n"
    external = "* Tasks\n** TODO t0001 replaced\n"
    org_file = write(tmp_path / "todo.org", original)
    buf = taskui.OrgBuffer(org_file)
    buf.apply_text(external, description="Temporary local edit")
    assert buf.undo() == "Temporary local edit"
    replacement = write(tmp_path / "replacement.org", external)
    replacement.replace(org_file)

    assert buf.check_external_change() is None
    assert buf.saved_text == external
    assert buf.read() == external
    assert buf.dirty is False
    assert buf.can_undo is False and buf.can_redo is False
    assert buf.external_change is None


def test_org_buffer_poll_reports_missing_and_unreadable_files(
    tmp_path: Path, monkeypatch
) -> None:
    # Missing and unreadable disk states stay structured and block exact saves.
    missing_file = write(tmp_path / "missing.org", "* Tasks\n")
    missing = taskui.OrgBuffer(missing_file)
    missing_file.unlink()

    missing_change = missing.check_external_change()
    assert missing_change is not None and missing_change.kind == "missing"
    with pytest.raises(taskui.BufferChangedError, match="file is missing"):
        missing.save()

    unreadable_file = write(tmp_path / "unreadable.org", "* Tasks\n")
    unreadable = taskui.OrgBuffer(unreadable_file)

    def deny_read() -> str:
        raise PermissionError("permission denied")

    monkeypatch.setattr(unreadable, "_read_disk_text", deny_read)
    unreadable_change = unreadable.check_external_change(force=True)
    assert unreadable_change is not None
    assert unreadable_change.kind == "unreadable"
    assert "permission denied" in (unreadable_change.detail or "")
    with pytest.raises(taskui.BufferChangedError, match="file is unreadable"):
        unreadable.save()


def test_org_buffer_save_detects_changed_text_with_the_same_signature(
    tmp_path: Path,
) -> None:
    # Save compares exact text even when the polling signature is unchanged.
    original = "* Tasks\n** TODO t0001 one\n"
    ours = "* Tasks\n** DONE t0001 one\n"
    theirs = "* Tasks\n** TODO t0001 two\n"
    assert len(original) == len(theirs)
    org_file = write(tmp_path / "todo.org", original)
    buf = taskui.OrgBuffer(org_file)
    buf.apply_text(ours, description="Finish t0001")
    baseline = org_file.stat()
    org_file.write_text(theirs, encoding="utf-8")
    os.utime(org_file, ns=(baseline.st_atime_ns, baseline.st_mtime_ns))
    current = org_file.stat()
    assert (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    ) == (
        baseline.st_dev,
        baseline.st_ino,
        baseline.st_size,
        baseline.st_mtime_ns,
    )

    assert buf.check_external_change() is None
    with pytest.raises(taskui.BufferChangedError, match="changed externally"):
        buf.save()
    assert buf.external_change is not None
    assert buf.external_change.text == theirs


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_inline_menu_session_polls_from_the_render_cycle() -> None:
    # A configured session invokes its lightweight poll hook before rendering.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.output import DummyOutput

    polls = []
    view = menu.MenuView([], lambda session, result: None)
    with create_app_session(output=DummyOutput()):
        session = menu.InlineMenuSession(view, on_poll=lambda active: polls.append(active))
        session._before_render(session.application)

    assert polls == [session]
    assert session.application.refresh_interval == 0.5


def test_org_buffer_undo_redo_tracks_logical_edits_and_autosave(tmp_path: Path) -> None:
    # History should reverse whole task actions and mirror every state to autosave.
    original = "* Tasks\n** TODO t0001 one\n"
    org_file = write(tmp_path / "todo.org", original)
    buf = taskui.OrgBuffer(org_file)

    buf.apply(
        ["* Tasks", "** DONE t0001 one"],
        description="Set t0001 state to DONE",
    )
    buf.apply(
        ["* Tasks", "** DONE [#A] t0001 one"],
        description="Set t0001 priority to A",
    )

    assert buf.undo_count == 2
    assert buf.undo() == "Set t0001 priority to A"
    assert buf.read() == "* Tasks\n** DONE t0001 one\n"
    assert buf.autosave_path.read_text(encoding="utf-8") == buf.read()
    assert buf.undo() == "Set t0001 state to DONE"
    assert buf.read() == original
    assert buf.dirty is False
    assert not buf.autosave_path.exists()
    assert buf.can_redo is True

    assert buf.redo() == "Set t0001 state to DONE"
    assert buf.redo() == "Set t0001 priority to A"
    assert buf.read() == "* Tasks\n** DONE [#A] t0001 one\n"
    assert buf.dirty is True

    buf.save()
    assert buf.can_undo is False and buf.can_redo is False
    assert buf.undo() is None and buf.redo() is None


def test_task_controller_reports_file_history_status(tmp_path: Path) -> None:
    # Header status should distinguish modified data from clean redo history.
    org_file = write(tmp_path / "tasks.org", "* Tasks\n** TODO t0001 one\n")
    buf = taskui.OrgBuffer(org_file)
    project = manager.Project("demo", tmp_path, org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)
    item = taskui.load_menu_items(buf)[0]
    view = controller.initial_view()

    assert view.status_text is not None and view.status_text() == ""
    taskui._toggle_state(buf, item)
    assert view.status_text() == "FILE MODIFIED: 1 edit"
    assert buf.undo() == "Set t0001 state to DONE"
    assert view.status_text() == "FILE CLEAN · Redo available"
    assert buf.redo() == "Set t0001 state to DONE"
    assert view.status_text() == "FILE MODIFIED: 1 edit"


def test_org_buffer_apply_back_to_original_clears_autosave(tmp_path: Path) -> None:
    # Editing back to the saved content marks the buffer clean and drops the file.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    buf = taskui.OrgBuffer(org_file)
    buf.apply(["* Tasks", "** DONE t0001 one"])
    assert buf.autosave_path.exists()
    buf.apply(["* Tasks", "** TODO t0001 one"])  # back to original
    assert buf.dirty is False
    assert not buf.autosave_path.exists()


def test_org_buffer_recover_adopts_autosave(tmp_path: Path) -> None:
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    buf = taskui.OrgBuffer(org_file)
    recovered = "* Tasks\n** DONE t0001 one\n"
    buf.recover(recovered)
    assert buf.read() == recovered
    assert buf.dirty is True
    assert buf.autosave_path.read_text(encoding="utf-8") == recovered


def test_resolve_buffer_save_prompt_yes(tmp_path: Path, monkeypatch) -> None:
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    buf = taskui.OrgBuffer(org_file)
    buf.apply(["* Tasks", "** DONE t0001 one"])
    monkeypatch.setattr(menu, "prompt_text", lambda _label: "y")
    assert taskui._resolve_buffer(buf) is True  # exit proceeds
    assert org_file.read_text(encoding="utf-8") == "* Tasks\n** DONE t0001 one\n"
    assert not buf.autosave_path.exists()


def test_resolve_buffer_save_prompt_default_enter_saves(tmp_path: Path, monkeypatch) -> None:
    # The prompt defaults to yes, so a bare Enter ("") preserves the work.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    buf = taskui.OrgBuffer(org_file)
    buf.apply(["* Tasks", "** DONE t0001 one"])
    monkeypatch.setattr(menu, "prompt_text", lambda _label: "")
    taskui._resolve_buffer(buf)
    assert org_file.read_text(encoding="utf-8") == "* Tasks\n** DONE t0001 one\n"


def test_resolve_buffer_save_prompt_no_discards(tmp_path: Path, monkeypatch) -> None:
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    original = org_file.read_text(encoding="utf-8")
    buf = taskui.OrgBuffer(org_file)
    buf.apply(["* Tasks", "** DONE t0001 one"])
    monkeypatch.setattr(menu, "prompt_text", lambda _label: "n")
    assert taskui._resolve_buffer(buf) is True  # discard still exits
    assert org_file.read_text(encoding="utf-8") == original
    assert not buf.autosave_path.exists()


def test_resolve_buffer_escape_stays_in_context(tmp_path: Path, monkeypatch) -> None:
    # Esc on the save prompt vetoes the exit: returns False, keeps the buffer
    # dirty and the auto-save in place, and never writes the real file.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    original = org_file.read_text(encoding="utf-8")
    buf = taskui.OrgBuffer(org_file)
    buf.apply(["* Tasks", "** DONE t0001 one"])

    def _esc(_label):
        raise menu.ContextCancelled()

    monkeypatch.setattr(menu, "prompt_text", _esc)
    assert taskui._resolve_buffer(buf) is False  # stay in the running context
    assert buf.dirty is True
    assert buf.autosave_path.exists()
    assert org_file.read_text(encoding="utf-8") == original


def test_resolve_buffer_clean_buffer_exits_without_prompt(tmp_path: Path, monkeypatch) -> None:
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    buf = taskui.OrgBuffer(org_file)  # not dirty

    def _boom(_label):
        raise AssertionError("should not prompt when nothing is pending")

    monkeypatch.setattr(menu, "prompt_text", _boom)
    assert taskui._resolve_buffer(buf) is True


def test_maybe_recover_yes_loads_autosave(tmp_path: Path, monkeypatch) -> None:
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    autosave = taskui.autosave_path_for(org_file)
    autosave.write_text("* Tasks\n** DONE t0001 one\n", encoding="utf-8")
    buf = taskui.OrgBuffer(org_file)
    monkeypatch.setattr(menu, "prompt_text", lambda _label: "y")
    taskui._maybe_recover(buf)
    assert buf.read() == "* Tasks\n** DONE t0001 one\n"
    assert buf.dirty is True


def test_maybe_recover_no_discards_autosave(tmp_path: Path, monkeypatch) -> None:
    # Explicit "n"/"no" is the only thing that throws away recovery data.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    original = org_file.read_text(encoding="utf-8")
    autosave = taskui.autosave_path_for(org_file)
    autosave.write_text("* Tasks\n** DONE t0001 one\n", encoding="utf-8")
    buf = taskui.OrgBuffer(org_file)
    monkeypatch.setattr(menu, "prompt_text", lambda _label: "n")
    taskui._maybe_recover(buf)
    assert buf.read() == original
    assert buf.dirty is False
    assert not autosave.exists()


def test_maybe_recover_escape_keeps_autosave_for_later(tmp_path: Path, monkeypatch) -> None:
    # Esc leaves #todo.org# untouched and proceeds from the on-disk file.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    original = org_file.read_text(encoding="utf-8")
    autosave = taskui.autosave_path_for(org_file)
    recovery_data = "* Tasks\n** DONE t0001 one\n"
    autosave.write_text(recovery_data, encoding="utf-8")
    buf = taskui.OrgBuffer(org_file)

    def _esc(_label):
        raise menu.ContextCancelled()

    monkeypatch.setattr(menu, "prompt_text", _esc)
    taskui._maybe_recover(buf)
    assert buf.read() == original          # not recovered; buffer is the disk file
    assert buf.dirty is False
    assert autosave.exists()               # recovery data left in place...
    assert autosave.read_text(encoding="utf-8") == recovery_data  # ...untouched


def test_maybe_recover_default_enter_keeps_autosave(tmp_path: Path, monkeypatch) -> None:
    # The default (bare Enter) also keeps the recovery data for later.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    autosave = taskui.autosave_path_for(org_file)
    autosave.write_text("* Tasks\n** DONE t0001 one\n", encoding="utf-8")
    buf = taskui.OrgBuffer(org_file)
    monkeypatch.setattr(menu, "prompt_text", lambda _label: "")
    taskui._maybe_recover(buf)
    assert buf.dirty is False
    assert autosave.exists()


def test_repair_exit_code_matrix_and_safety(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # 0 clean, 2 problems remain, 1 unreadable subject; non-TTY requires --dry-run or --force (t0032.2, t0032.3).
    # 1. Clean ort task file
    clean_org = write(tmp_path / "tasks.org", "* Tasks\n** TODO t0001 Task one\n")
    assert ortask.cmd_repair(argparse.Namespace(file=clean_org, dry_run=False, force=False)) == 0
    assert ortask.cmd_repair(argparse.Namespace(file=clean_org, dry_run=True, force=False)) == 0
    assert ortask.cmd_repair(argparse.Namespace(file=clean_org, dry_run=False, force=True)) == 0

    # 2. Task file with problems (duplicate IDs)
    broken_org = write(
        tmp_path / "broken.org",
        "* Tasks\n** TODO t0001 Task one\n** TODO t0001 Task duplicate\n",
    )
    # Dry-run reports and exits 2
    assert ortask.cmd_repair(argparse.Namespace(file=broken_org, dry_run=True, force=False)) == 2
    assert "duplicate ID t0001" in capsys.readouterr().err

    # Bare form without TTY and without --force refuses with exit 1
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert ortask.cmd_repair(argparse.Namespace(file=broken_org, dry_run=False, force=False)) == 1
    assert "interactive confirmation requires a TTY" in capsys.readouterr().err

    # Force skips safety but exits 2 because problems remain unfixed
    assert ortask.cmd_repair(argparse.Namespace(file=broken_org, dry_run=False, force=True)) == 2

    # 3. Clean projmgr registry
    registry, _ = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    reg_str = str(registry)
    assert projmgr.cmd_repair(argparse.Namespace(registry=reg_str, dry_run=False, force=False)) == 0
    assert projmgr.cmd_repair(argparse.Namespace(registry=reg_str, dry_run=True, force=False)) == 0
    assert projmgr.cmd_repair(argparse.Namespace(registry=reg_str, dry_run=False, force=True)) == 0
    # doctor alias sets dry_run=True by default
    parser = projmgr.build_parser()
    doc_args = parser.parse_args(["--registry", reg_str, "doctor"])
    assert doc_args.dry_run is True
    assert projmgr.cmd_repair(doc_args) == 0

    # 4. Registry with problems
    (registry / "dangling").mkdir()
    (registry / "dangling" / "dangling").symlink_to(tmp_path / "src" / "nonexistent")
    # Dry-run reports and exits 2
    assert projmgr.cmd_repair(argparse.Namespace(registry=reg_str, dry_run=True, force=False)) == 2
    # doctor alias reports and exits 2 without TTY refusal
    doc_broken_args = parser.parse_args(["--registry", reg_str, "doctor"])
    assert projmgr.cmd_repair(doc_broken_args) == 2
    capsys.readouterr()

    # Bare form without TTY and without --force refuses with exit 1
    assert projmgr.cmd_repair(argparse.Namespace(registry=reg_str, dry_run=False, force=False)) == 1
    assert "interactive confirmation requires a TTY" in capsys.readouterr().err

    # Force skips safety but exits 2 because problems remain
    assert projmgr.cmd_repair(argparse.Namespace(registry=reg_str, dry_run=False, force=True)) == 2

    # 5. Unreadable subject exits 1
    assert projmgr.cmd_repair(argparse.Namespace(registry=str(tmp_path / "nosuchreg"), dry_run=False, force=False)) == 1
    assert ortask.cmd_repair(argparse.Namespace(file=tmp_path / "nosuchfile.org", dry_run=False, force=False)) == 1


def test_ort_info_reports_metadata_and_scripting_flags(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # ort info reports file metadata and supports --file and --format json (t0032.4).
    proj_dir = tmp_path / "project"
    proj_dir.mkdir()
    org_file = write(
        proj_dir / "tasks.org",
        """#+TODO: TODO | DONE MOOT
* Intro
Some project notes.
* Tasks
** TODO [#A] t0001 First task :tag1:
** DONE t0002.1 Subtask
** MOOT tw26W24 Weekly task
""",
    )
    # 1. Plain text format
    args = argparse.Namespace(file=org_file, provenance="probed in .", format="plain", single_file=False)
    assert ortask.cmd_info(args) == 0
    out = capsys.readouterr().out
    assert f"Task file:   {org_file.resolve()} (probed in .)" in out
    assert "Tasks:       depth 1, lines 4–7" in out
    assert "Counts:      TODO: 1, DONE: 1, MOOT: 1 (total: 3)" in out
    assert "IDs:         numeric (highest: t0002), weekly (highest: tw26W24)" in out
    assert "Keywords:    TODO | DONE MOOT" in out
    assert "archive (does not exist)" in out

    # 2. JSON format
    args_json = argparse.Namespace(file=org_file, provenance="probed in .", format="json", single_file=False)
    assert ortask.cmd_info(args_json) == 0
    raw_json = capsys.readouterr().out
    data = json.loads(raw_json)
    assert data["task_file"] == str(org_file.resolve())
    assert data["provenance"] == "probed in ."
    assert data["tasks_subtree"]["depth"] == 1
    assert data["tasks_subtree"]["start_line"] == 4
    assert data["tasks_subtree"]["end_line"] == 7
    assert data["counts"]["todo"] == 1
    assert data["counts"]["done"] == 1
    assert data["counts"]["moot"] == 1
    assert data["counts"]["total"] == 3
    assert data["ids"]["numeric"] == "t0002"
    assert data["ids"]["weekly"] == "tw26W24"
    assert data["keywords"] == "TODO | DONE MOOT"
    assert data["archive"]["exists"] is False

    # 3. CLI --file flag (prints canonical path and exits 0)
    monkeypatch.chdir(proj_dir)
    monkeypatch.setattr(sys, "argv", ["ortask.py", "info", "--file"])
    assert ortask.main() == 0
    assert capsys.readouterr().out.strip() == str(org_file.resolve())

    # 4. CLI --file flag when no task file exists (silent stderr, exit 1)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.chdir(empty_dir)
    monkeypatch.setattr(sys, "argv", ["ortask.py", "info", "--file"])
    assert ortask.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_pmgr_info_reports_metadata_and_scripting_flags(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # pmgr info inspects project registry context and scripting flags (t0032.5).
    registry, project_dir = _cdproj_registry(
        tmp_path,
        monkeypatch,
        capsys,
        "* Tasks\n** TODO t0001 task 1\n** DONE t0002 task 2\n",
    )
    reg_str = str(registry)
    index = registry / manager.PROJECTS_INDEX_NAME
    index.write_text(
        manager.PROJECTS_INDEX_HEADER + "* [#A] myproj\n** Directories\n   - ~/src/myproj\n",
        encoding="utf-8",
    )

    # 1. Plain text format inferred from PWD inside project
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("PWD", str(project_dir))
    args = argparse.Namespace(registry=reg_str, target=None, name=False, path=False, file=False, format="plain")
    assert projmgr.cmd_info(args) == 0
    out = capsys.readouterr().out
    assert "Project:     myproj" in out
    assert "Task file:" in out
    assert "Registry:    " in out
    assert "* [#A] myproj" in out
    assert "Directories: private (1 entry)" in out
    assert "Tasks:       open: 1, completed: 1, total: 2" in out

    # 2. JSON format by project name target
    args_json = argparse.Namespace(registry=reg_str, target="myproj", name=False, path=False, file=False, format="json")
    assert projmgr.cmd_info(args_json) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["project"] == "myproj"
    assert data["directory"] == str(project_dir.resolve())
    assert data["heading"] == "* [#A] myproj"
    assert data["directories"]["source"] == "private"
    assert data["directories"]["count"] == 1
    assert data["tasks"]["open"] == 1
    assert data["tasks"]["completed"] == 1

    # 3. Single-field flag --name
    args_name = argparse.Namespace(registry=reg_str, target=None, name=True, path=False, file=False, format="plain")
    assert projmgr.cmd_info(args_name) == 0
    assert capsys.readouterr().out.strip() == "myproj"

    # 4. Single-field flag --path
    args_path = argparse.Namespace(registry=reg_str, target="myproj", name=False, path=True, file=False, format="plain")
    assert projmgr.cmd_info(args_path) == 0
    assert capsys.readouterr().out.strip() == str(project_dir.resolve())

    # 5. Single-field flag --file
    args_file = argparse.Namespace(registry=reg_str, target="myproj", name=False, path=False, file=True, format="plain")
    assert projmgr.cmd_info(args_file) == 0
    assert capsys.readouterr().out.strip() == str((project_dir / "TODO.org").resolve())

    # 6. Single-field flag in unregistered directory: exits 1 with silent stderr
    unreg_dir = tmp_path / "unregistered"
    unreg_dir.mkdir()
    monkeypatch.chdir(unreg_dir)
    monkeypatch.setenv("PWD", str(unreg_dir))
    args_unreg = argparse.Namespace(registry=reg_str, target=None, name=True, path=False, file=False, format="plain")
    assert projmgr.cmd_info(args_unreg) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    # 7. Human report in unregistered directory: exits 1 with error on stderr
    args_human_unreg = argparse.Namespace(registry=reg_str, target=None, name=False, path=False, file=False, format="plain")
    assert projmgr.cmd_info(args_human_unreg) == 1
    err_out = capsys.readouterr().err
    assert "no registered project matches" in err_out
