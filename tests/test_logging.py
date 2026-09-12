from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ortask  # noqa: E402
import projmgr  # noqa: E402
from ortasklib import log as eventlog  # noqa: E402
from ortasklib import manager, taskui  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_logging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    eventlog.set_sink(None)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("ORTASK_LOG", raising=False)
    monkeypatch.delenv("ORTASK_LOG_DIR", raising=False)
    yield
    eventlog.set_sink(None)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _register(registry: Path, name: str, project: Path, task_file: Path) -> None:
    entry = registry / name
    entry.mkdir(parents=True, exist_ok=True)
    (entry / project.name).symlink_to(project.resolve())
    (entry / task_file.name).symlink_to(task_file.resolve())


def _log_args(**overrides) -> argparse.Namespace:
    values = {
        "since": None,
        "until": None,
        "limit": None,
        "day_start": "00:00",
        "format": "json",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_event_writer_is_opt_in_and_config_updates_preserve_log_setting(
    tmp_path: Path,
) -> None:
    # Logging stays off by default, and pmgr init-style writes retain [log].
    registry = tmp_path / "registry"
    registry.mkdir()
    config = manager.ortask_config_path()
    manager.write_ortask_registry(config, str(registry))
    event = eventlog.make_event(
        "ort", "init", now=datetime(2026, 8, 22, 1, tzinfo=timezone.utc)
    )

    eventlog.record(event, registry=registry)
    assert not (registry / "log").exists()

    config.write_text(
        f"[projects]\nregistry = {registry}\n\n[log]\nenabled = true\n",
        encoding="utf-8",
    )
    manager.write_ortask_registry(config, str(tmp_path / "other-registry"))
    assert manager.read_ortask_option("log", "enabled") == "true"

    eventlog.record(event, registry=registry)
    line = (registry / "log" / "2026-08.jsonl").read_text(encoding="utf-8")
    assert json.loads(line)["verb"] == "init"


def test_disabled_writer_does_not_resolve_or_scan_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The default-off path must stay cheaper than project discovery.
    monkeypatch.setattr(
        manager,
        "resolve_registry",
        lambda *_args: (_ for _ in ()).throw(AssertionError("registry resolved")),
    )

    eventlog.record(eventlog.make_event("ort", "done"))


def test_event_writer_caps_utf8_lines_and_marks_truncated_titles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # One append remains valid JSON and no larger than the documented cap.
    log_dir = tmp_path / "events"
    monkeypatch.setenv("ORTASK_LOG", "on")
    monkeypatch.setenv("ORTASK_LOG_DIR", str(log_dir))
    event = eventlog.make_event(
        "ort",
        "add",
        task={"id": "t0001", "title": "é" * 6000},
        now=datetime(2026, 8, 22, 1, tzinfo=timezone.utc),
    )

    eventlog.record(event)

    data = (log_dir / "2026-08.jsonl").read_bytes()
    assert len(data) <= eventlog.MAX_EVENT_BYTES
    parsed = json.loads(data)
    assert parsed["task"]["title"].endswith(eventlog.TRUNCATION_MARKER)


def test_event_writer_never_creates_a_missing_registry_or_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A disposable logging failure cannot alter a successful command's result.
    missing = tmp_path / "missing-registry"
    monkeypatch.setenv("ORTASK_LOG", "on")
    eventlog.record(eventlog.make_event("pmgr", "migrate"), registry=missing)
    assert not missing.exists()

    eventlog.set_sink(lambda _event: (_ for _ in ()).throw(OSError("full")))
    task_file = _write(tmp_path / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
    assert ortask.cmd_done(argparse.Namespace(file=task_file, id="t0001")) == 0
    assert "** DONE t0001 Work" in task_file.read_text(encoding="utf-8")


def test_two_processes_append_complete_json_lines(tmp_path: Path) -> None:
    # Concurrent writers each contribute one intact line to the monthly file.
    log_dir = tmp_path / "events"
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "ORTASK_LOG": "on",
        "ORTASK_LOG_DIR": str(log_dir),
    }
    program = (
        "import sys; from ortasklib import log; "
        "log.record(log.make_event('ort','add',detail={'worker':sys.argv[1]}))"
    )
    processes = [
        subprocess.Popen([sys.executable, "-c", program, worker], env=env)
        for worker in ("one", "two")
    ]
    assert [process.wait() for process in processes] == [0, 0]

    lines = (log_dir / f"{datetime.now().astimezone():%Y-%m}.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert {json.loads(line)["detail"]["worker"] for line in lines} == {
        "one",
        "two",
    }


def test_when_parser_defines_shifted_days_calendar_weeks_and_relative_ranges() -> None:
    # Every documented WHEN spelling has deterministic local-time semantics.
    zone = timezone(timedelta(hours=-7))
    now = datetime(2026, 8, 22, 2, 30, tzinfo=zone)
    start = time(4)
    assert eventlog.parse_when("today", now=now, day_start=start) == datetime(
        2026, 8, 21, 4, tzinfo=zone
    )
    assert eventlog.parse_when("yesterday", now=now, day_start=start) == datetime(
        2026, 8, 20, 4, tzinfo=zone
    )
    assert eventlog.parse_when("week", now=now, day_start=start) == datetime(
        2026, 8, 17, 4, tzinfo=zone
    )
    assert eventlog.parse_when("2d", now=now, day_start=start) == now - timedelta(days=2)
    assert eventlog.parse_when("3h", now=now, day_start=start) == now - timedelta(hours=3)
    assert eventlog.parse_when("2026-08-20", now=now, day_start=start) == datetime(
        2026, 8, 20, 4, tzinfo=zone
    )
    assert eventlog.parse_when("2026-08-20T12:00:00-07:00", now=now) == datetime(
        2026, 8, 20, 12, tzinfo=zone
    )


def test_reader_filters_across_months_limits_newest_and_preserves_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Readers span monthly files while JSON output retains each matching line.
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    monkeypatch.setenv("ORTASK_LOG_DIR", str(log_dir))
    raw_lines: list[str] = []
    for month, day, project in ((7, 31, "one"), (8, 1, "two"), (8, 2, "one")):
        event = eventlog.make_event(
            "ort",
            "done",
            project=project,
            task={"id": f"t00{day:02d}", "title": f"day {day}"},
            now=datetime(2026, month, day, 12, tzinfo=timezone.utc),
        )
        raw = json.dumps(event, separators=(", ", ": ")) + "\n"
        raw_lines.append(raw)
        with (log_dir / f"2026-{month:02d}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(raw)
    with (log_dir / "2026-08.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("not json\n")

    events = eventlog.read_events(
        since=datetime(2026, 7, 31, tzinfo=timezone.utc),
        until=datetime(2026, 8, 3, tzinfo=timezone.utc),
        project="one",
        limit=1,
    )
    assert len(events) == 1
    assert events[0].data["task"]["title"] == "day 2"
    assert events[0].raw == raw_lines[2]


def test_ort_write_commands_emit_post_commit_task_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Local write verbs log after success and multi-task verbs share a session.
    registry = tmp_path / "registry"
    registry.mkdir()
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))
    project = tmp_path / "project"
    task_file = _write(
        project / "tasks.org",
        "* Tasks\n** TODO t0001 Existing\n"
        "* Template\n** TODO twYYWNN Week YYWNN\n"
        "*** TODO twYYWNN.1 Child\n",
    )
    _register(registry, "sample", project, task_file)
    events: list[dict] = []
    eventlog.set_sink(events.append)

    assert ortask.cmd_add(
        argparse.Namespace(file=task_file, title="Added", parent=None)
    ) == 0
    assert ortask.cmd_done(argparse.Namespace(file=task_file, id="t0001")) == 0
    assert ortask.cmd_open(argparse.Namespace(file=task_file, id="t0001")) == 0
    assert ortask.cmd_apply(
        argparse.Namespace(
            file=task_file,
            template="weekly",
            week=None,
            date="2026-06-25",
            dry_run=False,
        )
    ) == 0
    capsys.readouterr()

    assert [event["verb"] for event in events[:3]] == ["add", "done", "open"]
    assert all(event["project"] == "sample" for event in events)
    apply_events = [event for event in events if event["verb"] == "apply"]
    assert len(apply_events) == 2
    assert len({event["session"] for event in apply_events}) == 1


def test_archive_logs_one_event_per_moved_subtree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Archiving several DONE roots records each title under one command session.
    task_file = _write(
        tmp_path / "tasks.org",
        "* Tasks\n** DONE t0001 First\n** DONE t0002 Second\n",
    )
    events: list[dict] = []
    eventlog.set_sink(events.append)

    assert ortask.cmd_archive(argparse.Namespace(file=task_file, id=None)) == 0
    capsys.readouterr()

    assert [event["task"]["title"] for event in events] == ["First", "Second"]
    assert len({event["session"] for event in events}) == 1


def test_project_write_and_switch_commands_emit_expected_events(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # pmgr logs writes and deliberate cdproj selection, but not dry-run browsing.
    registry = tmp_path / "registry"
    registry.mkdir()
    project = tmp_path / "sample"
    task_file = _write(project / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
    events: list[dict] = []
    eventlog.set_sink(events.append)

    add_args = argparse.Namespace(
        path=project,
        name=None,
        file=None,
        registry=str(registry),
        force=False,
        dry_run=False,
    )
    assert projmgr.cmd_add(add_args) == 0
    assert projmgr.cmd_migrate(
        argparse.Namespace(registry=str(registry), dry_run=False)
    ) == 0
    assert projmgr.cmd_set_dirs(
        argparse.Namespace(
            registry=str(registry),
            project="sample",
            directories=[str(project)],
            stdin=False,
            missing="remove",
            dry_run=False,
        )
    ) == 0
    assert projmgr.cmd_cdproj(
        argparse.Namespace(
            registry=str(registry),
            project="sample",
            out=str(tmp_path / "stack"),
        )
    ) == 0
    assert projmgr.cmd_rm(
        argparse.Namespace(
            registry=str(registry), name="sample", force=False, dry_run=False
        )
    ) == 0
    capsys.readouterr()

    assert [event["verb"] for event in events] == [
        "add",
        "migrate",
        "set-dirs",
        "cdproj",
        "rm",
    ]
    assert events[3]["detail"]["directories"] == [str(project)]


def test_tui_logs_only_saved_task_field_changes(tmp_path: Path) -> None:
    # Buffered edits emit on save, while an otherwise identical discard is silent.
    task_file = _write(tmp_path / "tasks.org", "* Tasks\n** TODO t0001 Work\n")
    events: list[dict] = []
    eventlog.set_sink(events.append)
    buf = taskui.OrgBuffer(task_file, project="sample", registry=tmp_path)
    item = taskui.load_menu_items(buf)[0]
    taskui._toggle_state(buf, item)
    buf.save()

    assert len(events) == 1
    assert events[0]["verb"] == "edit"
    assert events[0]["detail"]["fields"] == ["state"]
    assert events[0]["task"]["from"] == "TODO"
    assert events[0]["task"]["to"] == "DONE"

    second = taskui.OrgBuffer(task_file, project="sample", registry=tmp_path)
    taskui._toggle_state(second, taskui.load_menu_items(second)[0])
    second.discard()
    assert len(events) == 1


def test_log_commands_scope_projects_and_pass_json_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # ort defaults to its task-file project; pmgr can select another project.
    registry = tmp_path / "registry"
    registry.mkdir()
    manager.write_ortask_registry(manager.ortask_config_path(), str(registry))
    first_dir = tmp_path / "first"
    first_file = _write(first_dir / "tasks.org", "* Tasks\n** TODO t0001 One\n")
    _register(registry, "first", first_dir, first_file)
    log_dir = registry / "log"
    log_dir.mkdir()
    monkeypatch.setenv("ORTASK_LOG_DIR", str(log_dir))

    raws = {}
    for project in ("first", "second"):
        event = eventlog.make_event(
            "ort",
            "done",
            project=project,
            file=manager.friendly_path(first_file.resolve()),
            task={"id": "t0001", "title": project},
            now=datetime(2026, 8, 22, 12, tzinfo=timezone.utc),
        )
        raw = json.dumps(event, separators=(", ", ": ")) + "\n"
        raws[project] = raw
        with (log_dir / "2026-08.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(raw)

    assert ortask.cmd_log(_log_args(file=first_file, all=False)) == 0
    assert capsys.readouterr().out == raws["first"]

    assert projmgr.cmd_log(
        _log_args(registry=str(registry), project="second")
    ) == 0
    assert capsys.readouterr().out == raws["second"]


def test_ort_log_all_needs_no_local_task_file(tmp_path: Path) -> None:
    # Registry-wide reads work from an empty directory without task discovery.
    log_dir = tmp_path / "events"
    log_dir.mkdir()
    event = eventlog.make_event(
        "pmgr",
        "cdproj",
        project="sample",
        now=datetime(2026, 8, 22, 12, tzinfo=timezone.utc),
    )
    raw = json.dumps(event) + "\n"
    (log_dir / "2026-08.jsonl").write_text(raw, encoding="utf-8")
    cwd = tmp_path / "empty"
    cwd.mkdir()

    result = subprocess.run(
        [sys.executable, str(ROOT / "ortask.py"), "log", "--all", "--format", "json"],
        cwd=cwd,
        env={
            **os.environ,
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "ORTASK_LOG_DIR": str(log_dir),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == raw
    assert result.stderr == ""


def test_repair_ignores_reserved_log_directory_and_readers_emit_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Reserved derived storage is not a stray project and read-only verbs do not log.
    registry = tmp_path / "registry"
    (registry / "log").mkdir(parents=True)
    (registry / manager.PROJECTS_INDEX_NAME).write_text(
        manager.PROJECTS_INDEX_HEADER, encoding="utf-8"
    )
    events: list[dict] = []
    eventlog.set_sink(events.append)

    assert projmgr.cmd_repair(argparse.Namespace(registry=str(registry))) == 0

    output = capsys.readouterr().out
    assert "log: not a project entry" not in output
    assert events == []
