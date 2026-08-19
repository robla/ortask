from __future__ import annotations

import argparse
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
from ortasklib import core, manager, taskui, tasks


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
    called: list[tuple[Path, bool]] = []

    monkeypatch.setattr(
        taskui,
        "local_file_menu",
        lambda path, include_done=False: called.append((path, include_done)) or 0,
    )

    assert ortask.cmd_interactive(argparse.Namespace(file=org_file)) == 0
    assert called == [(org_file, True)]


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
    assert "Done: 1" in interactive_result.stdout
    assert "Finished parent" in interactive_result.stdout

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
            "list",
            "open",
            "repair",
            "show",
        ],
        "pmgr": [
            "add",
            "cdproj",
            "doctor",
            "help",
            "init",
            "list",
            "migrate",
            "projadd",
            "rm",
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
    calls: list[tuple[Path, str, bool]] = []

    monkeypatch.setattr(
        manager,
        "resolve_registry",
        lambda registry: (tmp_path, "~/Projects"),
    )
    monkeypatch.setattr(
        projmgr,
        "project_menu",
        lambda workspace, display, include_done: (
            calls.append((workspace, display, include_done)) or 0
        ),
    )

    assert projmgr.cmd_interactive(
        argparse.Namespace(registry=None, todo_only=True)
    ) == 0

    captured = capsys.readouterr()
    assert captured.out == "Finding project in ~/Projects\n"
    assert captured.err == ""
    assert calls == [(tmp_path, "~/Projects", False)]


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
        ("_ortask_complete", "COMP_WORDS=(ortask.py list --fo); COMP_CWORD=2", "--format"),
        ("_ortask_complete", "COMP_WORDS=(ortask.py apply --te); COMP_CWORD=2", "--template"),
        ("_ortask_complete", "COMP_WORDS=(ortask.py apply --template w); COMP_CWORD=3", "weekly"),
        ("_projmgr_complete", "COMP_WORDS=(pmgr --in); COMP_CWORD=1", "--interactive"),
        ("_projmgr_complete", "COMP_WORDS=(pmgr li); COMP_CWORD=1", "list"),
        ("_projmgr_complete", "COMP_WORDS=(projmgr.py list --fo); COMP_CWORD=2", "--format"),
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
                "list",
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
                "init",
                "list",
                "migrate",
                "projadd",
                "rm",
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


# --- projmgr registry model: the project marker, init, add, rm, doctor -------
# These isolate config by pointing XDG_CONFIG_HOME at a temp directory, so they
# never read or write (or delete) the real ~/.config/ortask.


def _add_args(path: Path | None, **kw) -> argparse.Namespace:
    base = dict(path=None if path is None else str(path), name=None, file=None,
                registry=None, force=False, dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_migrate_records_registry(
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


def test_bash_completion_offers_project_names(tmp_path: Path) -> None:
    # cdproj, and the arguments of pmgr that name a project, complete from the
    # registry. The names come from projmgr.py itself rather than from a glob.
    script = ROOT / "misc" / "ortask-completion.bash"
    registry = tmp_path / "projects"
    registry.mkdir(parents=True)
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

    args = argparse.Namespace(registry=None)
    assert projmgr.cmd_doctor(args) == 0
    assert "no problems found" in capsys.readouterr().out

    (registry / "gone").mkdir()
    (registry / "gone" / "gone").symlink_to(tmp_path / "src" / "gone")
    write(registry / "docs" / "notes.org", "* Notes\n")

    assert projmgr.cmd_doctor(args) == 2
    out = capsys.readouterr().out
    assert "problem: gone: broken project link" in out
    assert "note: docs: not a project entry, ignored" in out

    # A stale task-file link would otherwise read as "no task file".
    (registry / "healthy" / "tasks.org").unlink()
    (registry / "healthy" / "tasks.org").symlink_to(tmp_path / "src" / "vanished.org")

    assert projmgr.cmd_doctor(args) == 2
    out = capsys.readouterr().out
    assert "problem: healthy: dangling link tasks.org -> " in out


def _cdproj_registry(tmp_path: Path, monkeypatch, capsys, org_text: str) -> tuple:
    """Register one project and return ``(registry, project_dir)``."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = tmp_path / "projects"
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))

    project = tmp_path / "myproj"
    write(project / "TODO.org", org_text)
    assert projmgr.cmd_add(_add_args(project, name="myproj")) == 0
    capsys.readouterr()
    return registry, project


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


def test_projmgr_cdproj_never_edits_the_task_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """``cdproj`` resolves and reports; Org content belongs to ``ortask.py``."""
    from ortasklib import menu

    original = "* Tasks\n** TODO t0001 task\n"
    registry, project = _cdproj_registry(tmp_path, monkeypatch, capsys, original)
    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(menu, "prompt_text", lambda prompt: "1")

    out_file = tmp_path / "out.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file))
    ) == 0
    assert (project / "TODO.org").read_text(encoding="utf-8") == original


def test_projmgr_cdproj_prefers_the_private_list(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    """A private list is offered first, and chosen without a prompt when alone."""
    from ortasklib import menu

    registry, project = _cdproj_registry(
        tmp_path, monkeypatch, capsys, "* Tasks\n** TODO t0001 task\n"
    )
    private = registry / "myproj" / manager.DIRECTORIES_PRIVATE_NAME
    private.write_text("* Directories\n** file:/private/one\n", encoding="utf-8")

    monkeypatch.setattr(menu, "interactive_select_available", lambda: False)
    monkeypatch.setattr(menu, "prompt_text", lambda prompt: "1")

    # Only the private file defines a stack, so there is nothing to ask about.
    out_file = tmp_path / "out1.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file))
    ) == 0
    assert out_file.read_text(encoding="utf-8").splitlines() == ["/private/one"]

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

    out_file2 = tmp_path / "out2.txt"
    assert projmgr.cmd_cdproj(
        argparse.Namespace(registry=str(registry), out=str(out_file2))
    ) == 0
    assert out_file2.read_text(encoding="utf-8").splitlines() == ["/shared/one", "/private/one"]


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


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_select_menu_keybindings_headless() -> None:
    # Drive select_menu through prompt_toolkit's pipe-input harness to lock down
    # navigation, in-list action hotkeys, contextual help, and stack popping.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    rows = [
        menu.MenuRow(1, "TODO", "t0001 first"),
        menu.MenuRow(2, "TODO", "t0002 second"),
        menu.MenuRow(3, "DONE", "t0003 third"),
    ]
    toggle = menu.MenuAction("toggle", "Shift+←/→", "Cycle task state")
    raise_priority = menu.MenuAction("priority_up", "Shift+↑", "Raise priority")
    lower_priority = menu.MenuAction("priority_down", "Shift+↓", "Lower priority")
    actions = {
        "e": menu.MenuAction("edit", "e", "Open in editor"),
        "c-t": menu.MenuAction("filter", "C-t", "Cycle task filter"),
        "s-left": toggle,
        "s-right": toggle,
        "s-up": raise_priority,
        "s-down": lower_priority,
    }

    def run(keys: str) -> menu.MenuResult:
        with create_pipe_input() as pin:
            with create_app_session(input=pin, output=DummyOutput()):
                pin.send_text(keys)
                return menu.select_menu(rows, actions=actions)

    assert run("\x1b[B\r") == menu.MenuResult("select", 1)   # Down, Enter
    assert run("jj\r") == menu.MenuResult("select", 2)       # j, j, Enter
    assert run("k\r") == menu.MenuResult("select", 2)        # Up wraps to last
    assert run("\x14") == menu.MenuResult("filter", 0)       # Ctrl-T on row 0
    assert run("j\x14") == menu.MenuResult("filter", 1)      # move then filter
    assert run("\x1b[1;2C") == menu.MenuResult("toggle", 0)  # Shift-Right
    assert run("\x1b[1;2D") == menu.MenuResult("toggle", 0)  # Shift-Left
    assert run("\x1b[1;2A") == menu.MenuResult("priority_up", 0)  # Shift-Up
    assert run("\x1b[1;2B") == menu.MenuResult("priority_down", 0)  # Shift-Down
    assert run("\x07\x07j\r") == menu.MenuResult("select", 1)  # C-g toggles help
    assert run("\x07e\x07e") == menu.MenuResult("edit", 0)  # actions pause in help
    assert run("\x07qj\r") == menu.MenuResult("select", 1)  # q only closes help
    assert run("\x07bj\r") == menu.MenuResult("select", 1)  # b only closes help
    assert run("q") == menu.MenuResult("back", None)
    assert run("b") == menu.MenuResult("back", None)


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
    assert "Cycle visibility through all, TODO, and DONE" in text
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
            pin.send_text("\r\x07q\x1bpq\x1b[1;2Aqj\r")
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
            session.application.ttimeoutlen = 0.01

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
        enter_moves_focus=frozenset({0}),
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text(
                "\x15New title\r"
                "\x15First body line\rSecond body line"
                "\x07\x07\x13\x1b"
            )
            menu.InlineMenuSession(view).run()

    assert saved == [("New title", "First body line\nSecond body line")]
    assert view.focused_index == 1


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
        list_focus_indices=frozenset({0}),
        on_list_move=move,
    )
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("\x1b[B\x1b[6~\x1b[A\x1b[5~")
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
    assert [control.text for control in view.focus_targets[2:4]] == [
        "Parent",
        "Body line",
    ]
    assert len(view.focus_targets) == 6
    assert "Subtasks: 1" in view.summary
    assert view.focused_index == 2
    assert view.choice_focus_indices == frozenset({0, 1})
    assert view.list_focus_indices == frozenset({4})
    assert view.activate_focus_indices == frozenset({4, 5})
    assert view.enter_moves_focus == frozenset({2})
    assert view.is_dirty is not None and view.is_dirty() is False
    assert view.status_text is not None and view.status_text() == ""
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
    parent_view.focus_targets[2].buffer.insert_text(" draft")
    parent_view.focused_index = 4

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
            pin.send_text("\r changed\t\t\r\r\x1bj\rq")
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
            pin.send_text("\r\x15\x13bqjkped renamed\x13\x1bq\r")
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
                "\r\x15Renamed title\r"
                "\x15First body line\rSecond body line\x13"
                "\x1bq\r"
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
            pin.send_text("\x1b[1;2A\r saved\x13\x1f\x1bq")
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
            # Use Right for Priority and Right/Enter for the State choice, then save.
            pin.send_text("\r\x1b[Z\x1b[C\x1b[Z\x1b[C\r\x13\x1bq")
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
            pin.send_text("\r\x1b[Z\x1b[C\x1bj\rq")
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
            pin.send_text("\r continued\x1b\r\x13\x1bq")
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
            pin.send_text("\r saved-on-exit\x1bk\rq")
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
            pin.send_text("\r discarded\x1bj\rq")
            controller.run()

    assert org_file.read_text(encoding="utf-8") == original
    assert buf.read() == original
    assert buf.dirty is False


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_select_menu_scrolls_to_selected_row(monkeypatch) -> None:
    # Keep a selection below the first screen visible while header and hint stay fixed.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    class TinyOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=10, columns=80)

        def get_rows_below_cursor_position(self) -> int:
            return 10

    windows = []
    real_window = menu.Window

    def tracked_window(*args, **kwargs):
        window = real_window(*args, **kwargs)
        windows.append(window)
        return window

    monkeypatch.setattr(menu, "Window", tracked_window)
    rows = [
        menu.MenuRow(i + 1, "TODO", f"t{i + 1:04} row")
        for i in range(20)
    ]
    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=TinyOutput()):
            pin.send_text("\r")
            result = menu.select_menu(
                rows,
                title="Tasks",
                summary="Open: 20  Done: 0  Total: 20",
                instruction="Enter select",
                start_index=12,
            )

    body = next(window for window in windows if window.content.is_focusable())
    assert result == menu.MenuResult("select", 12)
    assert body.render_info.window_height == 5
    assert body.vertical_scroll <= 12 < body.vertical_scroll + 5


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_select_project_menu_keybindings_headless() -> None:
    # Project lists use the same highlight-bar navigation model as task lists.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    rows = [
        menu.ProjectRow(1, "ortask", "~/src/ortask/todo.org"),
        menu.ProjectRow(2, "elweek", "~/tmpsorta/electorama-weekly/TODO.org"),
    ]

    def run(keys: str) -> menu.MenuResult:
        with create_pipe_input() as pin:
            with create_app_session(input=pin, output=DummyOutput()):
                pin.send_text(keys)
                return menu.select_project_menu(rows)

    assert run("\x1b[B\r") == menu.MenuResult("select", 1)  # Down, Enter
    assert run("k\r") == menu.MenuResult("select", 1)       # Up wraps to last
    assert run("q") == menu.MenuResult("back", None)
    assert run("b") == menu.MenuResult("back", None)


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_select_menu_empty_rows_allows_exit() -> None:
    # An empty list still honors stack-pop keys and edit (edit yields index None).
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    def run(keys: str) -> menu.MenuResult:
        with create_pipe_input() as pin:
            with create_app_session(input=pin, output=DummyOutput()):
                pin.send_text(keys)
                return menu.select_menu([], actions={"e": "edit"})

    assert run("q") == menu.MenuResult("back", None)
    assert run("b") == menu.MenuResult("back", None)
    assert run("e") == menu.MenuResult("edit", None)


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
    monkeypatch.setattr(menu, "select_project_menu", obsolete_path)
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
    assert controller.session.current_view.title == "Projects"
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
            pin.send_text("\x1b[1;2A\x1b[1;2Aq\r")
            taskui._interactive_task_menu(project, buf, include_done=True)

    assert "** TODO [#B] t0001 alpha" in buf.read()
    assert "** TODO t0002 beta" in buf.read()
    assert org_file.read_text(encoding="utf-8") == buf.read()
    assert org_file.read_text(encoding="utf-8") != original


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_interactive_save_view_saves_by_default(tmp_path: Path) -> None:
    # Leaving a dirty task view should save on the confirmation's default row.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    org_file = write(tmp_path / "tasks.org", "* Tasks\n** TODO t0001 alpha\n")
    project = manager.Project("demo", tmp_path, org_file)
    buf = taskui.OrgBuffer(org_file)
    controller = taskui.InteractiveTaskController(project, buf, include_done=True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("\x1b[1;2Cq\r")
            controller.run()

    assert org_file.read_text(encoding="utf-8") == "* Tasks\n** DONE t0001 alpha\n"
    assert buf.dirty is False
    assert not buf.autosave_path.exists()
    assert controller.session is not None
    assert controller.session.message == "Saved changes to tasks.org"
    assert controller.session.final_message == "Saved changes to tasks.org"
    assert controller.session.application.erase_when_done is False


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_interactive_save_view_cancel_then_discard(tmp_path: Path) -> None:
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
            pin.send_text("\x1b[1;2Cqqqj\r")
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
            pin.send_text("j\rq\r")
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
