from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orgmgr
import ortask
import projtui
from ortasklib import manager


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return path


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
    # This test captures the top-level task view used by orgmgr.py list.
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


def test_discover_local_org_file_order(tmp_path: Path, monkeypatch) -> None:
    # This test fixes the single-directory Org task-file discovery order.
    monkeypatch.chdir(tmp_path)
    write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 lowercase\n")
    write(tmp_path / "TODO-Project.org", "* Tasks\n** TODO t0002 project\n")
    write(tmp_path / "TODO.org", "* Tasks\n** TODO t0003 canonical\n")
    write(tmp_path / "nested" / "TODO.org", "* Tasks\n** TODO t0004 nested\n")

    assert ortask.resolve_org_file() == Path("TODO.org")

    (tmp_path / "TODO.org").unlink()
    assert ortask.resolve_org_file() == Path("TODO-Project.org")

    (tmp_path / "TODO-Project.org").unlink()
    assert ortask.resolve_org_file() == Path("todo.org")

    (tmp_path / "todo.org").unlink()
    write(tmp_path / "tasks.org", "* Tasks\n** TODO t0005 tasks\n")
    assert ortask.resolve_org_file() == Path("tasks.org")

    (tmp_path / "tasks.org").unlink()
    write(tmp_path / "README.org", "* Tasks\n** TODO t0006 readme\n")
    assert ortask.resolve_org_file() == Path("README.org")


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
    assert any("heading has TODO/DONE keyword but no valid task ID" in desc for desc in descriptions)
    assert any("duplicate ID t0001" in desc for desc in descriptions)


def test_summarize_projects_for_orgmgr(tmp_path: Path, capsys) -> None:
    # This test covers global project summaries without touching real config.
    workspace = tmp_path / "workspace"
    write(
        workspace / "alpha" / "TODO.org",
        """
        * Tasks
        ** TODO t0001 Alpha parent
        *** TODO t0001.1 Alpha child
        ** DONE t0002 Alpha done
        """,
    )
    write(workspace / "beta" / "README.org", "* Notes\nNo task section.\n")
    write(workspace / ".hidden" / "TODO.org", "* Tasks\n** TODO t0003 Hidden\n")
    write(workspace / "docs" / "TODO.org", "* Tasks\n** TODO t0004 Docs\n")

    args = argparse.Namespace(projdir=str(workspace), all=False, format="json")
    assert orgmgr.cmd_list(args) == 0
    projects = json.loads(capsys.readouterr().out)

    by_name = {project["project"]: project for project in projects}
    assert set(by_name) == {"alpha", "beta"}
    assert by_name["alpha"]["file"] == "alpha/TODO.org"
    assert by_name["alpha"]["tasks"] == [
        {"id": "t0001", "state": "TODO", "title": "Alpha parent"},
    ]
    assert by_name["beta"]["warning"] == "no parseable * Tasks section found"


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


def test_cli_smoke_tests(tmp_path: Path) -> None:
    # This test proves top-level scripts still import and run on temp fixtures.
    org_file = write(
        tmp_path / "example.org",
        """
        * Tasks
        ** TODO t0001 Smoke parent
        Body
        *** TODO t0001.1 Smoke child
        """,
    )
    workspace = tmp_path / "workspace"
    write(workspace / "sample" / "TODO.org", org_file.read_text(encoding="utf-8"))

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

    orgmgr_result = subprocess.run(
        [sys.executable, str(ROOT / "orgmgr.py"), "--projdir", str(workspace), "list", "--format", "json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert orgmgr_result.returncode == 0
    assert json.loads(orgmgr_result.stdout)[0]["project"] == "sample"

    projtui_result = subprocess.run(
        [sys.executable, str(ROOT / "projtui.py"), "--projdir", str(workspace)],
        cwd=ROOT,
        input="q\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert projtui_result.returncode == 0
    assert "Projects in" in projtui_result.stdout
    assert "sample" in projtui_result.stdout


def test_add_task_creates_tasks_section_if_missing(tmp_path: Path) -> None:
    # This test verifies that cmd_add creates a "* Tasks" section at the end
    # of the file if one does not already exist.
    org_file = write(
        tmp_path / "todo.org",
        """
        * Intro
        Keep me.
        """,
    )

    assert ortask.cmd_add(argparse.Namespace(file=org_file, title="First task", parent=None)) == 0

    lines = org_file.read_text(encoding="utf-8").splitlines()
    # It should have added the "* Tasks" section and the new task
    assert "* Tasks" in lines
    assert "** TODO t0001 First task" in lines
    # Ensure they are appended at the end
    tasks_index = lines.index("* Tasks")
    task_index = lines.index("** TODO t0001 First task")
    assert tasks_index > lines.index("Keep me.")
    assert task_index == tasks_index + 1


# --- orgmgr registry: migrate + projadd ---------------------------------------
# These isolate config by pointing XDG_CONFIG_HOME at a temp directory, so they
# never read or write the real ~/.config/ortask.


def test_projadd_gated_until_migrate(tmp_path: Path, monkeypatch, capsys) -> None:
    # projadd refuses to run until migrate has created the registry; migrate
    # then imports the projdir workspace (skipping projects with no * Tasks).
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    workspace = tmp_path / "ws"
    write(workspace / "alpha" / "TODO.org", "* Tasks\n** TODO t0001 Alpha\n")
    write(workspace / "beta" / "README.org", "* Notes\nno task section\n")

    standalone = tmp_path / "standalone"
    write(standalone / "todo.org", "* Tasks\n** TODO t0001 Standalone\n")

    registry_path = manager.registry_config_path()
    add_args = argparse.Namespace(
        path=str(standalone), name=None, file=None, force=False, dry_run=False
    )

    # Gated before migrate: exit 1, friendly error, no config written.
    assert orgmgr.cmd_projadd(add_args) == 1
    assert "registry not set up" in capsys.readouterr().err
    assert not registry_path.exists()

    # migrate imports alpha (beta has no * Tasks and is skipped).
    assert orgmgr.cmd_migrate(
        argparse.Namespace(projdir=str(workspace), force=False, dry_run=False)
    ) == 0
    capsys.readouterr()
    assert set(manager.read_registry(registry_path)) == {"alpha"}

    # projadd now succeeds and adds the standalone project.
    assert orgmgr.cmd_projadd(add_args) == 0
    capsys.readouterr()
    assert set(manager.read_registry(registry_path)) == {"alpha", "standalone"}

    # A duplicate name without --force is a clean error.
    assert orgmgr.cmd_projadd(add_args) == 1
    assert "already registered" in capsys.readouterr().err

    # migrate again without --force is an idempotent no-op.
    assert orgmgr.cmd_migrate(
        argparse.Namespace(projdir=str(workspace), force=False, dry_run=False)
    ) == 0
    assert "already migrated" in capsys.readouterr().out


def test_migrate_without_projdir_initializes_empty_registry(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # A fresh machine with no projtui.ini still gets a usable (empty) registry,
    # so projadd works without any legacy config.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry_path = manager.registry_config_path()

    assert not manager.registry_exists(registry_path)
    assert orgmgr.cmd_migrate(
        argparse.Namespace(projdir=None, force=False, dry_run=False)
    ) == 0
    capsys.readouterr()

    # Registry now exists and is empty (distinct from "not initialized").
    assert manager.registry_exists(registry_path)
    assert manager.read_registry(registry_path) == {}

    project = tmp_path / "proj"
    write(project / "TODO.org", "* Tasks\n** TODO t0001 Solo\n")
    assert orgmgr.cmd_projadd(
        argparse.Namespace(path=str(project), name="solo", file=None,
                           force=False, dry_run=False)
    ) == 0
    assert manager.read_registry(registry_path) == {"solo": str(project.resolve())}

