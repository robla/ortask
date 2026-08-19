"""Project management helpers shared by ``orgmgr.py`` and ``projtui.py``.

Covers registry resolution (``ortask.ini`` records where the project registry
directory lives), project discovery over that registry's per-project
subdirectories, per-project Org-file selection, and JSON-ready multi-project
task summaries. Like the rest of ``ortasklib``, it does no argument parsing and
never calls ``sys.exit``.
"""

from __future__ import annotations

import configparser
import io
import os
from dataclasses import dataclass
from pathlib import Path

from . import core

SKIP_PROJECT_DIRS = {".git", ".hg", ".svn", "__pycache__", "docs"}
DEFAULT_REGISTRY = "~/Projects"

# A project's private directory stack lives in the registry rather than in the
# project itself, so it is never part of the project's own repository.
DIRECTORIES_PRIVATE_NAME = "directories-private.org"

# Canonical suite config: ortask.ini records where the registry directory lives.
ORTASK_INI_NAME = "ortask.ini"
PROJECTS_SECTION = "projects"
REGISTRY_OPTION = "registry"


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    org_file: Path


def canonical_org_file(project: Project) -> Path:
    """Return the real task-file path, resolving any registry symlinks."""
    return project.org_file.resolve()


def real_project_path(project: Project) -> Path:
    """Return the real project directory path, resolving any registry symlinks."""
    try:
        for item in project.path.iterdir():
            if item.is_symlink() and item.resolve().is_dir():
                return item.resolve()
    except OSError:
        pass
    return project.org_file.resolve().parent


# ---------------------------------------------------------------------------
# Config: where the registry directory lives
# ---------------------------------------------------------------------------

def _config_dir() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base / "ortask"


def ortask_config_path() -> Path:
    """Canonical suite config (``ortask.ini``), honoring ``XDG_CONFIG_HOME``."""
    return _config_dir() / ORTASK_INI_NAME


def _read_ini_option(path: Path, section: str, option: str) -> str | None:
    if not path.exists():
        return None
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    if not parser.has_option(section, option):
        return None
    value = parser.get(section, option).strip()
    return value or None


def read_ortask_registry(path: Path | None = None) -> str | None:
    """Return ``ortask.ini``'s ``[projects] registry`` value."""
    return _read_ini_option(
        path or ortask_config_path(),
        PROJECTS_SECTION,
        REGISTRY_OPTION,
    )


def write_ortask_registry(path: Path, registry_value: str) -> None:
    """Atomically write ``[projects] registry = <value>`` to ``ortask.ini``."""
    parser = configparser.ConfigParser()
    parser[PROJECTS_SECTION] = {REGISTRY_OPTION: registry_value}
    buffer = io.StringIO()
    parser.write(buffer)
    path.parent.mkdir(parents=True, exist_ok=True)
    core.atomic_write(path, buffer.getvalue())


def _friendly_path(path: Path, original: str | None = None) -> str:
    if original and original.startswith("~"):
        return original
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def friendly_path(path: Path, original: str | None = None) -> str:
    """Render a path for storage/display: keep a user-supplied ``~`` prefix,
    otherwise collapse ``$HOME`` to ``~`` when possible."""
    return _friendly_path(path, original)


def resolve_registry(cli_registry: str | None = None) -> tuple[Path, str]:
    """Resolve the registry directory and a display string.

    Precedence: CLI override > ``ortask.ini`` ``[projects] registry`` > the
    ``~/Projects`` default.
    """
    raw = (
        cli_registry
        or read_ortask_registry()
        or DEFAULT_REGISTRY
    )
    resolved = Path(raw).expanduser().resolve()
    return resolved, _friendly_path(resolved, raw)


def has_task_section(text: str) -> bool:
    """True if ``text`` contains a top-level ``* Tasks`` heading."""
    return any(core.TASKS_HEADING_RE.match(line) for line in text.splitlines())


# ---------------------------------------------------------------------------
# Project / Org-file discovery
# ---------------------------------------------------------------------------

def _org_sort_key(path: Path) -> tuple[int, str]:
    lower = path.name.lower()
    if lower == "tasks.org":
        return (0, lower)
    if path.name == "task.org":
        return (1, lower)
    if path.name.endswith(".task.org"):
        return (2, lower)
    if path.name == "TODO.org":
        return (3, lower)
    if path.name.startswith("TODO-") and path.suffix == ".org":
        return (4, lower)
    if lower == "todo.org":
        return (5, lower)
    return (6, lower)


def choose_org_file(project_dir: Path) -> Path | None:
    root_files = sorted(
        (
            p
            for p in project_dir.iterdir()
            if p.is_file()
            and p.suffix == ".org"
            and p.name != DIRECTORIES_PRIVATE_NAME
        ),
        key=_org_sort_key,
    )
    if root_files:
        return root_files[0]

    nested: list[Path] = []
    for child in sorted(project_dir.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        nested.extend(
            sorted(
                (p for p in child.iterdir() if p.is_file() and p.suffix == ".org"),
                key=_org_sort_key,
            )
        )
    return nested[0] if nested else None


def discover_projects(workspace: Path) -> list[Project]:
    projects: list[Project] = []
    for child in sorted(workspace.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name in SKIP_PROJECT_DIRS or child.name.startswith("."):
            continue
        org_file = choose_org_file(child)
        if org_file:
            projects.append(Project(child.name, child, org_file))
    return projects


# ---------------------------------------------------------------------------
# Project directory stacks (orgmgr pcd)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DirectorySource:
    """One file that can define a project's ``* Directories`` section.

    ``label`` is ``"private"`` for the registry-local file, which is not part of
    the project's own repository, or ``"project"`` for the project's task file,
    which usually is. ``entries`` is ``None`` when the file has no
    ``* Directories`` section, which is how a candidate location is
    distinguished from a real source.
    """

    label: str
    path: Path
    entries: list[str] | None

    @property
    def defines_stack(self) -> bool:
        return self.entries is not None


def directory_candidates(project: Project) -> list[DirectorySource]:
    """Both places a project's directory stack may live, private first.

    Candidates are returned whether or not they exist, so callers can offer to
    create the private file. Use :func:`directory_sources` for the subset that
    actually defines a stack.
    """
    candidates: list[tuple[str, Path]] = [
        ("private", project.path / DIRECTORIES_PRIVATE_NAME),
        ("project", canonical_org_file(project)),
    ]
    sources: list[DirectorySource] = []
    for label, path in candidates:
        try:
            entries = core.parse_directories(path.read_text(encoding="utf-8"))
        except OSError:
            entries = None
        sources.append(DirectorySource(label, path, entries))
    return sources


def directory_sources(project: Project) -> list[DirectorySource]:
    """The candidates that actually define a ``* Directories`` section."""
    return [c for c in directory_candidates(project) if c.defines_stack]


def resolve_directories(entries: list[str], root: Path) -> list[Path]:
    """Expand ``~`` and ``$VAR`` and resolve relative entries against ``root``."""
    resolved: list[Path] = []
    for entry in entries:
        path = Path(os.path.expandvars(os.path.expanduser(entry)))
        if not path.is_absolute():
            path = root / path
        resolved.append(path.resolve())
    return resolved


# ---------------------------------------------------------------------------
# Multi-project summaries (orgmgr list)
# ---------------------------------------------------------------------------

def summarize_projects(workspace: Path, include_all: bool = False) -> list[dict]:
    """Return JSON-ready records for each project in a workspace.

    Each record has ``project`` and ``file`` (the resolved Org file path with
    ``$HOME`` collapsed to ``~`` where possible). Valid projects also carry
    ``tasks`` — top-level tasks, TODO-only unless ``include_all``. Projects
    whose Org file is unreadable, has no parseable task headings, or has
    duplicate IDs carry a ``warning`` and empty ``tasks`` instead of raising.
    """
    records: list[dict] = []

    for project in discover_projects(workspace):
        real_org_file = canonical_org_file(project)
        record: dict = {"project": project.name, "file": friendly_path(real_org_file)}

        try:
            text = real_org_file.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — surface any read failure as a warning
            record["warning"] = f"Could not read file: {e}"
            record["tasks"] = []
            records.append(record)
            continue

        tasks = core.parse_org(text)
        if not tasks:
            record["warning"] = "no parseable tasks found"
            record["tasks"] = []
            records.append(record)
            continue

        seen: set[str] = set()
        duplicates: set[str] = set()
        for t in tasks:
            if t.id:
                key = core.canonical_id(t.id)
                if key in seen:
                    duplicates.add(t.id)
                seen.add(key)
        if duplicates:
            record["warning"] = f"duplicate task IDs: {', '.join(sorted(duplicates))}"
            record["tasks"] = []
            records.append(record)
            continue

        root_level = min((t.level for t in tasks), default=2)
        record["tasks"] = [
            {"id": t.id, "state": t.state, "title": t.text}
            for t in tasks
            if t.level == root_level and (include_all or t.state == "TODO")
        ]
        records.append(record)

    return records
