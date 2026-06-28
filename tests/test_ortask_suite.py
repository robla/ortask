from __future__ import annotations

import argparse
import json
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orgmgr
import ortask
import projtui
from ortasklib import core, manager, tasks


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
    assert any("heading has TODO/DONE keyword but no valid task ID" in desc for desc in descriptions)
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


def test_summarize_projects_for_orgmgr(tmp_path: Path, capsys) -> None:
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
        workspace / "beta" / "README.org",
        """
        * Notes
        No task section.
        """,
    )
    write(
        workspace / "gamma" / "README.org",
        """
        * TODO t0003 Gamma root
        ** TODO t0003.1 Gamma child
        * Notes
        No dedicated task section.
        """,
    )
    write(workspace / ".hidden" / "TODO.org", "* Tasks\n** TODO t0003 Hidden\n")
    write(workspace / "docs" / "TODO.org", "* Tasks\n** TODO t0004 Docs\n")

    args = argparse.Namespace(registry=str(workspace), all=False, format="json")
    assert orgmgr.cmd_list(args) == 0
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
        projtui,
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

    orgmgr_result = subprocess.run(
        [sys.executable, str(ROOT / "orgmgr.py"), "--registry", str(workspace), "list"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert orgmgr_result.returncode == 0
    assert orgmgr_result.stdout.splitlines()[0] == f"Registry: {workspace}"
    assert "sample" in orgmgr_result.stdout

    orgmgr_json_result = subprocess.run(
        [sys.executable, str(ROOT / "orgmgr.py"), "--registry", str(workspace), "list", "--format", "json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert orgmgr_json_result.returncode == 0
    assert json.loads(orgmgr_json_result.stdout)[0]["project"] == "sample"

    projtui_result = subprocess.run(
        [sys.executable, str(ROOT / "projtui.py"), "--registry", str(workspace)],
        cwd=ROOT,
        input="q\n",
        text=True,
        capture_output=True,
        check=False,
    )
    assert projtui_result.returncode == 0
    assert "Projects in" in projtui_result.stdout
    assert "sample" in projtui_result.stdout


def test_orgmgr_no_args_and_help_show_help() -> None:
    # This test ensures orgmgr.py is explicit: bare invocation shows help rather
    # than listing the configured real registry.
    for args in ([], ["help"]):
        result = subprocess.run(
            [sys.executable, str(ROOT / "orgmgr.py"), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
        assert "usage:" in result.stdout
        assert "list" in result.stdout
        assert result.stderr == ""


def test_bash_completion_for_ortask_and_alias() -> None:
    # This test verifies bash completion for the script name and the common
    # "ort" alias without depending on an interactive shell.
    script = ROOT / "misc" / "ortask-completion.bash"
    cases = [
        ("COMP_WORDS=(ortask.py ad); COMP_CWORD=1", "add"),
        ("COMP_WORDS=(ort ad); COMP_CWORD=1", "add"),
        ("COMP_WORDS=(ort --in); COMP_CWORD=1", "--interactive"),
        ("COMP_WORDS=(ortask.py app); COMP_CWORD=1", "apply"),
        ("COMP_WORDS=(ortask.py list --fo); COMP_CWORD=2", "--format"),
        ("COMP_WORDS=(ortask.py apply --te); COMP_CWORD=2", "--template"),
        ("COMP_WORDS=(ortask.py apply --template w); COMP_CWORD=3", "weekly"),
    ]

    for setup, expected in cases:
        result = subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-c",
                f"source {script}; {setup}; _ortask_complete; printf '%s\\n' \"${{COMPREPLY[@]}}\"",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0
        assert result.stderr == ""
        assert result.stdout.strip() == expected


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


# --- orgmgr registry model: migrate + projadd ---------------------------------
# These isolate config by pointing XDG_CONFIG_HOME at a temp directory, so they
# never read or write (or delete) the real ~/.config/ortask.


def _projadd_args(path: Path, **kw) -> argparse.Namespace:
    base = dict(path=str(path), name=None, file=None, registry=None,
                force=False, dry_run=False)
    base.update(kw)
    return argparse.Namespace(**base)


def test_migrate_records_registry(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    # migrate records [projects] registry without consulting projtui.ini.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    workspace = tmp_path / "ws"
    workspace.mkdir()

    assert orgmgr.cmd_migrate(
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

    assert orgmgr.cmd_projadd(_projadd_args(project)) == 0
    capsys.readouterr()

    subdir = registry / "elweek"
    project_link = subdir / "elweek"
    task_link = subdir / "TODO.org"
    assert subdir.is_dir()
    assert project_link.is_symlink() and project_link.resolve() == project.resolve()
    assert task_link.is_symlink()
    assert task_link.resolve() == (project / "TODO.org").resolve()

    # Discovery follows the symlinks: list shows the project and its task.
    assert orgmgr.cmd_list(
        argparse.Namespace(registry=str(registry), all=False, format="json")
    ) == 0
    projects = json.loads(capsys.readouterr().out)
    assert projects[0]["project"] == "elweek"
    assert projects[0]["file"] == str((project / "TODO.org").resolve())
    assert projects[0]["tasks"] == [
        {"id": "t0001", "state": "TODO", "title": "Promote episode"},
    ]

    # An existing project subdir is a conflict without --force; --force repoints.
    assert orgmgr.cmd_projadd(_projadd_args(project)) == 1
    assert "already exists" in capsys.readouterr().err
    assert orgmgr.cmd_projadd(_projadd_args(project, force=True)) == 0


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

    assert orgmgr.cmd_projadd(_projadd_args(project, name="bare")) == 0
    capsys.readouterr()

    subdir = registry / "bare"
    assert (subdir / "bare").is_symlink()                          # project link
    assert not any(p.suffix == ".org" for p in subdir.iterdir())   # no task link


def test_projtui_task_menu_displays_canonical_symlink_target(tmp_path: Path) -> None:
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
        [sys.executable, str(ROOT / "projtui.py"), "--registry", str(workspace)],
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


def test_projtui_task_menu_opens_org_file_from_task_list(tmp_path: Path, monkeypatch) -> None:
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

    monkeypatch.setattr(projtui, "_open_editor", lambda buf, line: opened.append((buf.path, line)))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(choices))

    assert projtui.task_menu(project, include_done=False) is True
    assert opened == [(org_file.resolve(), None)]


def test_projtui_escape_cancels_done_confirmation(
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
    buf = projtui.OrgBuffer(org_file)
    item = projtui.load_menu_items(buf)[0]
    choices = iter(["d", "\x1b", "b"])

    monkeypatch.setattr("builtins.input", lambda prompt="": next(choices))

    assert projtui.focus_menu(buf, item) is True
    capsys.readouterr()
    assert "** TODO t0001 Keep open" in org_file.read_text(encoding="utf-8")
    assert buf.dirty is False  # Esc cancelled the toggle; nothing buffered


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
# Interactive task selector (ortasklib.menu / projtui)
# ---------------------------------------------------------------------------

from ortasklib import menu  # noqa: E402 — grouped with the interactive tests


def test_next_state_ring() -> None:
    # The Emacs-style toggle ring flips between the two keywords ortask uses.
    assert tasks.next_state("TODO") == "DONE"
    assert tasks.next_state("DONE") == "TODO"
    # Anything unrecognized cycles back to the first ring entry.
    assert tasks.next_state("WAITING") == "TODO"


def test_toggle_state_buffers_change_until_save(tmp_path: Path) -> None:
    # t0006: projtui._toggle_state edits the in-memory buffer (and the auto-save
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
    buf = projtui.OrgBuffer(org_file)
    target = next(i for i in projtui.load_menu_items(buf, include_done=True)
                  if i.task and i.task.id == "t0001")

    projtui._toggle_state(buf, target)
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


def test_interactive_select_unavailable_without_tty() -> None:
    # Under pytest there is no TTY, so the selector must report unavailable and
    # callers fall back to the numbered menu (no interactive code runs in CI).
    assert menu.interactive_select_available() is False


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_select_menu_keybindings_headless() -> None:
    # Drive select_menu through prompt_toolkit's pipe-input harness to lock down
    # navigation, in-list action hotkeys, and select/quit/back resolution.
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    rows = [
        menu.MenuRow(1, "TODO", "t0001 first"),
        menu.MenuRow(2, "TODO", "t0002 second"),
        menu.MenuRow(3, "DONE", "t0003 third"),
    ]
    actions = {"e": "edit", "t": "toggle", "s-right": "toggle"}

    def run(keys: str) -> menu.MenuResult:
        with create_pipe_input() as pin:
            with create_app_session(input=pin, output=DummyOutput()):
                pin.send_text(keys)
                return menu.select_menu(rows, actions=actions)

    assert run("\x1b[B\r") == menu.MenuResult("select", 1)   # Down, Enter
    assert run("jj\r") == menu.MenuResult("select", 2)       # j, j, Enter
    assert run("k\r") == menu.MenuResult("select", 2)        # Up wraps to last
    assert run("t") == menu.MenuResult("toggle", 0)          # hotkey on row 0
    assert run("jt") == menu.MenuResult("toggle", 1)         # move then toggle
    assert run("\x1b[1;2C") == menu.MenuResult("toggle", 0)  # Shift-Right
    assert run("q") == menu.MenuResult("quit", None)
    assert run("b") == menu.MenuResult("back", None)


@pytest.mark.skipif(menu.Application is None, reason="prompt_toolkit not installed")
def test_select_menu_empty_rows_allows_exit() -> None:
    # An empty list still honors quit/back and edit (edit yields index None).
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    def run(keys: str) -> menu.MenuResult:
        with create_pipe_input() as pin:
            with create_app_session(input=pin, output=DummyOutput()):
                pin.send_text(keys)
                return menu.select_menu([], actions={"e": "edit"})

    assert run("q") == menu.MenuResult("quit", None)
    assert run("b") == menu.MenuResult("back", None)
    assert run("e") == menu.MenuResult("edit", None)


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
    buf = projtui.OrgBuffer(org_file)

    ids = [item.task.id for item in projtui.load_menu_items(buf, include_done=True)]
    sorted_ids = [
        task.id
        for task in sorted(core.parse_org(buf.read()), key=projtui._stable_sort_key)
    ]

    assert ids == ["t0001", "t0001.1", "t0002", "t0002.1", "t0003"]
    assert sorted_ids == ids


def test_anchor_index_follows_task_and_clamps() -> None:
    # Build MenuItems directly so the helper is tested in isolation.
    org = "* Tasks\n** TODO t0001 a\n** TODO t0002 b\n** TODO t0003 c\n"
    items = [
        projtui.MenuItem(label=t.text, detail="ortask task", task=t, line_num=t.line_num)
        for t in core.parse_org(org)
    ]
    assert projtui._anchor_index(items, "t0002", 0) == 1     # follows the id
    assert projtui._anchor_index(items, "t0999", 2) == 2     # missing -> fallback
    assert projtui._anchor_index(items, "t0999", 99) == 2    # fallback clamped
    assert projtui._anchor_index([], "t0001", 5) == 0        # empty list


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
    buf = projtui.OrgBuffer(org_file)
    monkeypatch.setattr(menu, "interactive_select_available", lambda: True)

    with create_pipe_input() as pin:
        with create_app_session(input=pin, output=DummyOutput()):
            pin.send_text("ttq")  # toggle highlighted, toggle it back, quit
            projtui._interactive_task_menu(project, buf, include_done=True)

    # Two toggles of the same task cancel out in the buffer (so the highlight
    # stayed put), and nothing was written to the real file (still buffered).
    assert buf.read() == original
    assert org_file.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# OrgBuffer auto-save / save-on-exit (t0006)
# ---------------------------------------------------------------------------

def test_org_buffer_autosave_path_naming(tmp_path: Path) -> None:
    assert projtui.autosave_path_for(tmp_path / "todo.org") == tmp_path / "#todo.org#"
    assert projtui.autosave_path_for(tmp_path / "a.task.org") == tmp_path / "#a.task.org#"


def test_org_buffer_apply_save_and_discard(tmp_path: Path) -> None:
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    original = org_file.read_text(encoding="utf-8")
    buf = projtui.OrgBuffer(org_file)
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

    # apply then save commits to the real file and clears the auto-save.
    buf.apply(["* Tasks", "** DONE t0001 one"])
    buf.save()
    assert org_file.read_text(encoding="utf-8") == "* Tasks\n** DONE t0001 one\n"
    assert not buf.autosave_path.exists()
    assert buf.dirty is False


def test_org_buffer_apply_back_to_original_clears_autosave(tmp_path: Path) -> None:
    # Editing back to the saved content marks the buffer clean and drops the file.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    buf = projtui.OrgBuffer(org_file)
    buf.apply(["* Tasks", "** DONE t0001 one"])
    assert buf.autosave_path.exists()
    buf.apply(["* Tasks", "** TODO t0001 one"])  # back to original
    assert buf.dirty is False
    assert not buf.autosave_path.exists()


def test_org_buffer_recover_adopts_autosave(tmp_path: Path) -> None:
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    buf = projtui.OrgBuffer(org_file)
    recovered = "* Tasks\n** DONE t0001 one\n"
    buf.recover(recovered)
    assert buf.read() == recovered
    assert buf.dirty is True
    assert buf.autosave_path.read_text(encoding="utf-8") == recovered


def test_resolve_buffer_save_prompt_yes(tmp_path: Path, monkeypatch) -> None:
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    buf = projtui.OrgBuffer(org_file)
    buf.apply(["* Tasks", "** DONE t0001 one"])
    monkeypatch.setattr(menu, "prompt_text", lambda _label: "y")
    assert projtui._resolve_buffer(buf) is True  # exit proceeds
    assert org_file.read_text(encoding="utf-8") == "* Tasks\n** DONE t0001 one\n"
    assert not buf.autosave_path.exists()


def test_resolve_buffer_save_prompt_default_enter_saves(tmp_path: Path, monkeypatch) -> None:
    # The prompt defaults to yes, so a bare Enter ("") preserves the work.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    buf = projtui.OrgBuffer(org_file)
    buf.apply(["* Tasks", "** DONE t0001 one"])
    monkeypatch.setattr(menu, "prompt_text", lambda _label: "")
    projtui._resolve_buffer(buf)
    assert org_file.read_text(encoding="utf-8") == "* Tasks\n** DONE t0001 one\n"


def test_resolve_buffer_save_prompt_no_discards(tmp_path: Path, monkeypatch) -> None:
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    original = org_file.read_text(encoding="utf-8")
    buf = projtui.OrgBuffer(org_file)
    buf.apply(["* Tasks", "** DONE t0001 one"])
    monkeypatch.setattr(menu, "prompt_text", lambda _label: "n")
    assert projtui._resolve_buffer(buf) is True  # discard still exits
    assert org_file.read_text(encoding="utf-8") == original
    assert not buf.autosave_path.exists()


def test_resolve_buffer_escape_stays_in_context(tmp_path: Path, monkeypatch) -> None:
    # Esc on the save prompt vetoes the exit: returns False, keeps the buffer
    # dirty and the auto-save in place, and never writes the real file.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    original = org_file.read_text(encoding="utf-8")
    buf = projtui.OrgBuffer(org_file)
    buf.apply(["* Tasks", "** DONE t0001 one"])

    def _esc(_label):
        raise menu.ContextCancelled()

    monkeypatch.setattr(menu, "prompt_text", _esc)
    assert projtui._resolve_buffer(buf) is False  # stay in the running context
    assert buf.dirty is True
    assert buf.autosave_path.exists()
    assert org_file.read_text(encoding="utf-8") == original


def test_resolve_buffer_clean_buffer_exits_without_prompt(tmp_path: Path, monkeypatch) -> None:
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    buf = projtui.OrgBuffer(org_file)  # not dirty

    def _boom(_label):
        raise AssertionError("should not prompt when nothing is pending")

    monkeypatch.setattr(menu, "prompt_text", _boom)
    assert projtui._resolve_buffer(buf) is True


def test_maybe_recover_yes_loads_autosave(tmp_path: Path, monkeypatch) -> None:
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    autosave = projtui.autosave_path_for(org_file)
    autosave.write_text("* Tasks\n** DONE t0001 one\n", encoding="utf-8")
    buf = projtui.OrgBuffer(org_file)
    monkeypatch.setattr(menu, "prompt_text", lambda _label: "y")
    projtui._maybe_recover(buf)
    assert buf.read() == "* Tasks\n** DONE t0001 one\n"
    assert buf.dirty is True


def test_maybe_recover_no_discards_autosave(tmp_path: Path, monkeypatch) -> None:
    # Explicit "n"/"no" is the only thing that throws away recovery data.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    original = org_file.read_text(encoding="utf-8")
    autosave = projtui.autosave_path_for(org_file)
    autosave.write_text("* Tasks\n** DONE t0001 one\n", encoding="utf-8")
    buf = projtui.OrgBuffer(org_file)
    monkeypatch.setattr(menu, "prompt_text", lambda _label: "n")
    projtui._maybe_recover(buf)
    assert buf.read() == original
    assert buf.dirty is False
    assert not autosave.exists()


def test_maybe_recover_escape_keeps_autosave_for_later(tmp_path: Path, monkeypatch) -> None:
    # Esc leaves #todo.org# untouched and proceeds from the on-disk file.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    original = org_file.read_text(encoding="utf-8")
    autosave = projtui.autosave_path_for(org_file)
    recovery_data = "* Tasks\n** DONE t0001 one\n"
    autosave.write_text(recovery_data, encoding="utf-8")
    buf = projtui.OrgBuffer(org_file)

    def _esc(_label):
        raise menu.ContextCancelled()

    monkeypatch.setattr(menu, "prompt_text", _esc)
    projtui._maybe_recover(buf)
    assert buf.read() == original          # not recovered; buffer is the disk file
    assert buf.dirty is False
    assert autosave.exists()               # recovery data left in place...
    assert autosave.read_text(encoding="utf-8") == recovery_data  # ...untouched


def test_maybe_recover_default_enter_keeps_autosave(tmp_path: Path, monkeypatch) -> None:
    # The default (bare Enter) also keeps the recovery data for later.
    org_file = write(tmp_path / "todo.org", "* Tasks\n** TODO t0001 one\n")
    autosave = projtui.autosave_path_for(org_file)
    autosave.write_text("* Tasks\n** DONE t0001 one\n", encoding="utf-8")
    buf = projtui.OrgBuffer(org_file)
    monkeypatch.setattr(menu, "prompt_text", lambda _label: "")
    projtui._maybe_recover(buf)
    assert buf.dirty is False
    assert autosave.exists()
