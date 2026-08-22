"""Disposable append-only activity logging for the ortask suite."""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import orglib

from . import core, manager


SCHEMA_VERSION = 1
MAX_EVENT_BYTES = 4096
TRUNCATION_MARKER = "...[truncated]"
LOG_SECTION = "log"
LOG_ENABLED_OPTION = "enabled"

_sink: Callable[[dict[str, Any]], None] | None = None


@dataclass(frozen=True)
class LoggedEvent:
    """One parsed event together with its unchanged JSON Lines source line."""

    data: dict[str, Any]
    raw: str
    timestamp: datetime


def set_sink(sink: Callable[[dict[str, Any]], None] | None) -> None:
    """Set a process-local event sink for tests; ``None`` restores file output."""
    global _sink
    _sink = sink


def new_session() -> str:
    """Return an opaque ID shared by events from one command or save."""
    return uuid.uuid4().hex


def make_event(
    tool: str,
    verb: str,
    *,
    session: str | None = None,
    project: str | None = None,
    file: str | None = None,
    task: dict[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the stable schema fields; :func:`record` supplies file context."""
    timestamp = (now or datetime.now().astimezone()).astimezone()
    event: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "id": uuid.uuid4().hex[:16],
        "ts": timestamp.isoformat(timespec="seconds"),
        "session": session or new_session(),
        "tool": tool,
        "verb": verb,
        "project": project,
        "file": file,
    }
    if task is not None:
        event["task"] = task
    if detail is not None:
        event["detail"] = detail
    return event


def project_for_file(path: Path, registry: Path | None = None) -> str | None:
    """Return the registry name whose canonical task file is ``path``."""
    target = path.expanduser().resolve()
    workspace = registry or manager.resolve_registry()[0]
    if not workspace.is_dir():
        return None
    try:
        projects = manager.discover_projects(workspace)
    except OSError:
        return None
    for project in projects:
        candidate = manager.canonical_org_file(project)
        if candidate is not None and candidate == target:
            return project.name
    return None


def _logging_enabled() -> bool:
    override = os.environ.get("ORTASK_LOG")
    if override is not None:
        return override.strip().casefold() in {"1", "on", "true", "yes"}
    value = manager.read_ortask_option(LOG_SECTION, LOG_ENABLED_OPTION)
    return value is not None and value.casefold() in {"1", "on", "true", "yes"}


def log_directory(registry: Path | None = None) -> Path:
    """Return the configured/derived directory without creating it."""
    override = os.environ.get("ORTASK_LOG_DIR")
    if override:
        return Path(override).expanduser()
    workspace = registry or manager.resolve_registry()[0]
    return workspace / manager.LOG_DIRECTORY_NAME


def _serialize(event: dict[str, Any]) -> bytes:
    text = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    return text.encode("utf-8")


def _fit_event(event: dict[str, Any]) -> bytes:
    """Serialize under the append cap, marking any shortened title explicitly."""
    candidate = copy.deepcopy(event)
    encoded = _serialize(candidate)
    if len(encoded) <= MAX_EVENT_BYTES:
        return encoded

    task = candidate.get("task")
    title = task.get("title") if isinstance(task, dict) else None
    if isinstance(title, str):
        low = 0
        high = len(title)
        while low < high:
            keep = (low + high + 1) // 2
            task["title"] = title[:keep] + TRUNCATION_MARKER
            if len(_serialize(candidate)) <= MAX_EVENT_BYTES:
                low = keep
            else:
                high = keep - 1
        task["title"] = title[:low] + TRUNCATION_MARKER
        encoded = _serialize(candidate)
        if len(encoded) <= MAX_EVENT_BYTES:
            return encoded

    # Project-level details can also be large (for example, a long stack).
    if "detail" in candidate:
        candidate["detail"] = {"truncated": True}
        encoded = _serialize(candidate)
        if len(encoded) <= MAX_EVENT_BYTES:
            return encoded

    # Pathological path/project strings are still shortened rather than making
    # a successful task edit fail or writing a partial JSON object.
    for key in ("file", "project"):
        value = candidate.get(key)
        if not isinstance(value, str):
            continue
        while len(_serialize(candidate)) > MAX_EVENT_BYTES and value:
            value = value[: max(0, len(value) // 2)]
            candidate[key] = value + TRUNCATION_MARKER
    encoded = _serialize(candidate)
    if len(encoded) > MAX_EVENT_BYTES:
        candidate.pop("task", None)
        candidate.pop("detail", None)
        candidate["truncated"] = True
        encoded = _serialize(candidate)
    return encoded


def record(
    event: dict[str, Any],
    *,
    registry: Path | None = None,
    source_file: Path | None = None,
) -> None:
    """Append one event, swallowing every logging failure by design."""
    record_many([event], registry=registry, source_file=source_file)


def record_many(
    events: Iterable[dict[str, Any]],
    *,
    registry: Path | None = None,
    source_file: Path | None = None,
) -> None:
    """Record one command's events with one config/project-context lookup."""
    try:
        prepared_events = [copy.deepcopy(event) for event in events]
        if not prepared_events:
            return
        if _sink is None and not _logging_enabled():
            return

        workspace = registry or manager.resolve_registry()[0]
        if source_file is not None:
            canonical = source_file.expanduser().resolve()
            friendly = manager.friendly_path(canonical)
            needs_project = any(
                prepared.get("project") is None for prepared in prepared_events
            )
            inferred_project = (
                project_for_file(canonical, workspace) if needs_project else None
            )
            for prepared in prepared_events:
                prepared["file"] = friendly
                if prepared.get("project") is None:
                    prepared["project"] = inferred_project

        if _sink is not None:
            for prepared in prepared_events:
                _sink(prepared)
            return

        override = os.environ.get("ORTASK_LOG_DIR")
        if not override and not workspace.is_dir():
            return
        directory = log_directory(workspace)
        directory.mkdir(parents=True, exist_ok=True)
        for prepared in prepared_events:
            timestamp = datetime.fromisoformat(str(prepared["ts"]))
            path = directory / f"{timestamp:%Y-%m}.jsonl"
            data = _fit_event(prepared)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                written = os.write(fd, data)
                if written != len(data):
                    raise OSError(
                        f"short event-log write: {written}/{len(data)} bytes"
                    )
            finally:
                os.close(fd)
    except Exception:  # noqa: BLE001 - logging must never fail the real command
        return


def record_task_edits(
    before: str,
    after: str,
    source_file: Path,
    *,
    project: str | None = None,
    registry: Path | None = None,
) -> None:
    """Log one buffer save without allowing log analysis to fail the save."""
    try:
        _record_task_edits(
            before,
            after,
            source_file,
            project=project,
            registry=registry,
        )
    except Exception:  # noqa: BLE001 - the activity log is always best effort
        return


def _record_task_edits(
    before: str,
    after: str,
    source_file: Path,
    *,
    project: str | None = None,
    registry: Path | None = None,
) -> None:
    """Log changed task fields after one interactive buffer save."""
    if before == after:
        return
    previous = {
        core.canonical_id(item.id): item for item in orglib.parse(before).tasks()
    }
    current = {
        core.canonical_id(item.id): item for item in orglib.parse(after).tasks()
    }
    session = new_session()
    events: list[dict[str, Any]] = []
    for task_id in current:
        if task_id not in previous:
            continue
        old = previous[task_id]
        new = current[task_id]
        fields = [
            name
            for name, left, right in (
                ("title", old.text, new.text),
                ("body", old.body_lines, new.body_lines),
                ("state", old.state, new.state),
                ("priority", old.priority, new.priority),
                ("tags", old.tags, new.tags),
            )
            if left != right
        ]
        if not fields:
            continue
        task: dict[str, Any] = {"id": new.id, "title": new.text}
        if old.state != new.state:
            task.update({"from": old.state, "to": new.state})
        if new.priority is not None:
            task["priority"] = new.priority
        if new.tags:
            task["tags"] = [tag for tag in new.tags.split(":") if tag]
        events.append(
            make_event(
                "ort",
                "edit",
                session=session,
                project=project,
                task=task,
                detail={"fields": fields},
            )
        )
    record_many(events, registry=registry, source_file=source_file)


_RELATIVE_RE = re.compile(r"^(?P<count>\d+)(?P<unit>[dh])$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_day_start(value: str) -> time:
    """Parse ``HH:MM`` or raise a concise user-facing error."""
    try:
        hour, minute = (int(part) for part in value.split(":"))
        return time(hour, minute)
    except (TypeError, ValueError):
        raise ValueError(f"invalid day start: {value!r}; expected HH:MM") from None


def _current_day_boundary(now: datetime, start: time) -> datetime:
    boundary = datetime.combine(now.date(), start, tzinfo=now.tzinfo)
    return boundary if now >= boundary else boundary - timedelta(days=1)


def parse_when(
    value: str,
    *,
    now: datetime | None = None,
    day_start: time = time(),
) -> datetime:
    """Parse one CLI time expression into an aware datetime."""
    local_now = (now or datetime.now().astimezone()).astimezone()
    boundary = _current_day_boundary(local_now, day_start)
    lowered = value.casefold()
    if lowered == "today":
        return boundary
    if lowered == "yesterday":
        return boundary - timedelta(days=1)
    if lowered == "week":
        return boundary - timedelta(days=boundary.weekday())
    match = _RELATIVE_RE.match(lowered)
    if match:
        count = int(match.group("count"))
        unit = match.group("unit")
        delta = timedelta(days=count) if unit == "d" else timedelta(hours=count)
        return local_now - delta
    if _DATE_RE.match(value):
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError:
            pass
        else:
            return datetime.combine(parsed_date, day_start, tzinfo=local_now.tzinfo)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"invalid time: {value!r}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_now.tzinfo)
    return parsed


def time_window(
    since_value: str | None,
    until_value: str | None,
    day_start_value: str,
    *,
    now: datetime | None = None,
) -> tuple[time, datetime | None, datetime | None]:
    """Resolve shared CLI range arguments and validate their ordering."""
    day_start = parse_day_start(day_start_value)
    current = now or datetime.now().astimezone()
    since = (
        parse_when(since_value, now=current, day_start=day_start)
        if since_value
        else None
    )
    until = (
        parse_when(until_value, now=current, day_start=day_start)
        if until_value
        else None
    )
    if since is not None and until is not None and since >= until:
        raise ValueError("--since must be earlier than --until")
    return day_start, since, until


def _event_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(directory.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9].jsonl"))


def read_events(
    *,
    registry: Path | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    project: str | None = None,
    file: str | None = None,
    limit: int | None = None,
) -> list[LoggedEvent]:
    """Read matching events in chronological order; ``until`` is exclusive."""
    if limit is not None and limit < 0:
        raise ValueError("limit must not be negative")
    matches: list[LoggedEvent] = []
    for path in _event_files(log_directory(registry)):
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeError):
            continue
        for raw in lines:
            try:
                data = json.loads(raw)
                timestamp = datetime.fromisoformat(str(data["ts"]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if timestamp.tzinfo is None:
                continue
            if since is not None and timestamp < since:
                continue
            if until is not None and timestamp >= until:
                continue
            if project is not None and data.get("project") != project:
                continue
            if file is not None and data.get("file") != file:
                continue
            matches.append(LoggedEvent(data, raw, timestamp))
    matches.sort(key=lambda item: (item.timestamp, str(item.data.get("id", ""))))
    if limit is not None:
        matches = matches[-limit:] if limit else []
    return matches


def _event_summary(data: dict[str, Any]) -> str:
    task = data.get("task")
    if isinstance(task, dict):
        task_id = task.get("id", "")
        title = task.get("title", "")
        parts = (str(data.get("verb", "")), str(task_id), str(title))
        return " ".join(part for part in parts if part)
    detail = data.get("detail")
    if isinstance(detail, dict) and "directories" in detail:
        return f"{data.get('verb', '')} {len(detail['directories'])} directories"
    return str(data.get("verb", ""))


def format_plain(events: Iterable[LoggedEvent]) -> str:
    """Render one compact terminal line per event."""
    lines = []
    for event in events:
        project = event.data.get("project") or "-"
        lines.append(
            f"{event.timestamp:%Y-%m-%d %H:%M}  {project}  "
            f"{_event_summary(event.data)}"
        )
    return "\n".join(lines)


def _workday(event: LoggedEvent, start: time) -> date:
    shifted = event.timestamp - timedelta(hours=start.hour, minutes=start.minute)
    return shifted.date()


def format_org(events: Iterable[LoggedEvent], *, day_start: time = time()) -> str:
    """Render date headings and one event bullet under each."""
    lines: list[str] = []
    current: date | None = None
    for event in events:
        event_day = _workday(event, day_start)
        if event_day != current:
            if lines:
                lines.append("")
            lines.append(f"* {event_day.isoformat()}")
            current = event_day
        project = event.data.get("project") or "unregistered"
        lines.append(
            f"- {event.timestamp:%H:%M} [{project}] {_event_summary(event.data)}"
        )
    return "\n".join(lines)
