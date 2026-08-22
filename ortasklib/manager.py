"""Project management helpers shared by ``projmgr.py`` and the project browser.

Covers registry resolution (``ortask.ini`` records where the project registry
directory lives), project discovery over that registry's per-project
subdirectories, per-project Org-file selection, and JSON-ready multi-project
task summaries. ``docs/projects.md`` is the model this implements. Like the rest
of ``ortasklib``, it does no argument parsing and never calls ``sys.exit``.
"""

from __future__ import annotations

import configparser
import io
import os
import re
from dataclasses import dataclass
from pathlib import Path

import orglib

from . import core

DEFAULT_REGISTRY = "~/Projects"

# Directory names that mark a project root during an upward walk, alongside the
# canonical task-file names in ``core``.
VCS_DIR_NAMES = (".git", ".hg", ".svn")

# A project's private directory stack lives in the registry rather than in the
# project itself, so it is never part of the project's own repository.
DIRECTORIES_PRIVATE_NAME = "directories-private.org"
PROJECTS_INDEX_NAME = "projects.org"
PROJECTS_INDEX_HEADER = "#+TITLE: Projects\n\n"
LOG_DIRECTORY_NAME = "log"
_RESERVED_INDEX_HEADINGS = frozenset({"tasks", "template"})
_ORG_KEYWORD_RE = re.compile(r"^[ \t]*#\+[A-Za-z][A-Za-z0-9_-]*(?:\[[^]]*\])?:")

# Canonical suite config: ortask.ini records where the registry directory lives.
ORTASK_INI_NAME = "ortask.ini"
PROJECTS_SECTION = "projects"
REGISTRY_OPTION = "registry"


@dataclass(frozen=True)
class Project:
    """One registry entry.

    ``path`` is the registry subdirectory; ``link`` is the symlink inside it
    that marks the entry as a project. ``org_file`` is ``None`` for a project
    that has no task file yet, which is a normal state rather than an error.
    ``warning`` describes a broken or ambiguous entry that should still be
    listed so it can be fixed.
    """

    name: str
    path: Path
    org_file: Path | None = None
    link: Path | None = None
    warning: str | None = None


class RegistryMigrationError(Exception):
    """One or more conditions make registry migration unsafe."""

    def __init__(self, messages: list[str] | tuple[str, ...] | str) -> None:
        if isinstance(messages, str):
            messages = [messages]
        self.messages = tuple(messages)
        super().__init__("\n".join(self.messages))


class RegistryIndexError(Exception):
    """The registry index cannot safely serve private project settings."""

    def __init__(self, messages: list[str] | tuple[str, ...] | str) -> None:
        if isinstance(messages, str):
            messages = [messages]
        self.messages = tuple(messages)
        super().__init__("\n".join(self.messages))


@dataclass(frozen=True)
class LegacyDirectoryFile:
    """One validated legacy file and its deterministic index representation."""

    project: str
    path: Path
    source_text: str
    project_text: str


@dataclass(frozen=True)
class RegistryMigrationPlan:
    """A fully validated registry-index write and cleanup plan."""

    index_path: Path
    index_text: str
    previous_index_text: str | None
    legacy_files: tuple[LegacyDirectoryFile, ...]

    @property
    def writes_index(self) -> bool:
        return self.previous_index_text is None


def canonical_org_file(project: Project) -> Path | None:
    """Return the real task-file path, resolving any registry symlinks."""
    return project.org_file.resolve() if project.org_file is not None else None


def real_project_path(project: Project) -> Path:
    """Return the real project directory path, resolving any registry symlinks."""
    if project.link is not None:
        try:
            return project.link.resolve()
        except OSError:
            pass
    try:
        for item in project.path.iterdir():
            if item.is_symlink() and item.resolve().is_dir():
                return item.resolve()
    except OSError:
        pass
    if project.org_file is not None:
        return project.org_file.resolve().parent
    return project.path


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


def read_ortask_option(
    section: str, option: str, path: Path | None = None
) -> str | None:
    """Return one optional value from the canonical suite configuration."""
    return _read_ini_option(path or ortask_config_path(), section, option)


def read_ortask_registry(path: Path | None = None) -> str | None:
    """Return ``ortask.ini``'s ``[projects] registry`` value."""
    return _read_ini_option(
        path or ortask_config_path(),
        PROJECTS_SECTION,
        REGISTRY_OPTION,
    )


def write_ortask_registry(path: Path, registry_value: str) -> None:
    """Set the registry atomically while preserving unrelated config sections."""
    parser = configparser.ConfigParser()
    if path.exists():
        parser.read(path, encoding="utf-8")
    if not parser.has_section(PROJECTS_SECTION):
        parser.add_section(PROJECTS_SECTION)
    parser.set(PROJECTS_SECTION, REGISTRY_OPTION, registry_value)
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


def read_project_entry(entry: Path) -> Project | None:
    """Read one registry subdirectory as a project, or ``None`` if it is not one.

    A registry entry is a project when it points outward: a symlink to a
    directory, or failing that a symlink to an Org file. That positive marker is
    the whole test, and it is what replaced a blocklist of directory names — a
    registry may also hold a README, its own notes, or its own VCS directory,
    and none of those become projects because none of them point anywhere. See
    ``docs/projects.md``.

    A dangling symlink marks a *broken* project rather than a missing one, and
    several directory symlinks are ambiguous. Both are returned with a
    ``warning`` so they stay visible and fixable.
    """
    try:
        children = sorted(entry.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return None

    links = [p for p in children if p.is_symlink()]
    dir_links = [p for p in links if p.is_dir()]
    org_links = [p for p in links if p.is_file() and p.suffix == ".org"]
    # A dangling link is evidence of a broken project only if it aimed at what a
    # project link aims at. A registry's notes directory with one stale .md link
    # is not a broken project.
    broken = [
        p for p in links
        if not p.exists() and Path(os.readlink(p)).suffix in ("", ".org")
    ]

    link: Path | None = None
    warning: str | None = None
    if len(dir_links) == 1:
        link = dir_links[0]
    elif len(dir_links) > 1:
        warning = "several project links: " + ", ".join(p.name for p in dir_links)
    elif org_links:
        # A task-file link alone is still a deliberate registration; the project
        # directory is wherever that file really lives.
        link = None
    elif broken:
        target = os.readlink(broken[0])
        warning = f"broken project link: {broken[0].name} -> {target}"
    else:
        return None

    return Project(entry.name, entry, choose_org_file(entry), link, warning)


def discover_projects(workspace: Path) -> list[Project]:
    """Every project in a registry directory, in case-insensitive name order."""
    projects: list[Project] = []
    for child in sorted(workspace.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        project = read_project_entry(child)
        if project is not None:
            projects.append(project)
    return projects


# ---------------------------------------------------------------------------
# Registry index migration
# ---------------------------------------------------------------------------

def _path_exists(path: Path) -> bool:
    """Include dangling symlinks when deciding whether a path occupies a name."""
    return path.exists() or path.is_symlink()


def _demote_org_headings(text: str) -> str:
    """Add one star to every Org heading while preserving all other bytes."""
    nested: list[str] = []
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        nested.append(
            "*" + raw_line
            if orglib.syntax.ANY_HEADING_RE.match(line)
            else raw_line
        )
    return "".join(nested)


def _legacy_project_text(project: str, source_text: str) -> str:
    nested = _demote_org_headings(source_text)
    result = f"* {project}\n{nested}"
    if not result.endswith(("\n", "\r")):
        result += "\n"
    return result + "\n"


def _legacy_validation_errors(path: Path, text: str) -> list[str]:
    errors: list[str] = []
    directory_lines = [
        line_num
        for line_num, line in enumerate(text.splitlines(), start=1)
        if orglib.syntax.DIRECTORIES_HEADING_RE.match(line)
    ]
    if len(directory_lines) != 1:
        where = (
            f" at lines {', '.join(map(str, directory_lines))}"
            if directory_lines
            else ""
        )
        errors.append(
            f"{path}: expected exactly one top-level '* Directories' heading"
            f"{where}; found {len(directory_lines)}"
        )

    keyword_lines = [
        line_num
        for line_num, line in enumerate(text.splitlines(), start=1)
        if _ORG_KEYWORD_RE.match(line)
    ]
    if keyword_lines:
        errors.append(
            f"{path}: Org keywords cannot be nested safely (lines "
            f"{', '.join(map(str, keyword_lines))})"
        )
    return errors


def _legacy_candidates(registry: Path) -> tuple[list[tuple[str, Path]], list[str]]:
    candidates: list[tuple[str, Path]] = []
    errors: list[str] = []
    try:
        children = sorted(registry.iterdir(), key=lambda path: path.name.casefold())
    except OSError as exc:
        raise RegistryMigrationError(f"cannot read registry {registry}: {exc}") from exc

    for child in children:
        if child.name.startswith(".") or not child.is_dir() or child.is_symlink():
            continue
        legacy_path = child / DIRECTORIES_PRIVATE_NAME
        if not _path_exists(legacy_path):
            continue
        if read_project_entry(child) is None:
            errors.append(
                f"{legacy_path}: parent directory is not a registered project"
            )
            continue
        candidates.append((child.name, legacy_path))
    return candidates, errors


def _duplicate_project_errors(names: list[str]) -> list[str]:
    grouped: dict[str, list[str]] = {}
    for name in names:
        grouped.setdefault(name.casefold(), []).append(name)
    return [
        "registry project names collide case-insensitively: " + ", ".join(values)
        for values in grouped.values()
        if len(values) > 1
    ]


def _validate_index(text: str, projects: list[Project], path: Path) -> list[str]:
    errors = _duplicate_project_errors([project.name for project in projects])
    document = orglib.parse(text)
    for project in projects:
        if project.name.casefold() in _RESERVED_INDEX_HEADINGS:
            errors.append(
                f"{path}: project name {project.name!r} is reserved by the index"
            )
            continue
        try:
            document.directories(project.name)
        except (orglib.OrgStructureError, ValueError) as exc:
            errors.append(f"{path}: {exc}")
    return errors


def plan_registry_migration(registry: Path) -> RegistryMigrationPlan:
    """Validate and plan migration from per-entry files to ``projects.org``.

    No file is changed. All legacy inputs are read before any error is raised so
    one invocation reports the complete validation set.
    """
    if not registry.is_dir():
        raise RegistryMigrationError(f"project directory not found: {registry}")

    candidates, errors = _legacy_candidates(registry)

    legacy_files: list[LegacyDirectoryFile] = []
    for project, path in candidates:
        if project.casefold() in _RESERVED_INDEX_HEADINGS:
            errors.append(
                f"{path}: project name {project!r} is reserved by the index"
            )
        try:
            source_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"cannot read {path}: {exc}")
            continue
        source_errors = _legacy_validation_errors(path, source_text)
        errors.extend(source_errors)
        if source_errors:
            continue

        project_text = _legacy_project_text(project, source_text)
        lookup = orglib.parse(project_text).directories(project)
        if not lookup.project_found or lookup.section is None:
            errors.append(
                f"{path}: project name {project!r} cannot form a safe Org heading"
            )
            continue
        legacy_files.append(
            LegacyDirectoryFile(project, path, source_text, project_text)
        )

    try:
        projects = discover_projects(registry)
    except OSError as exc:
        errors.append(f"cannot enumerate projects in {registry}: {exc}")
        projects = []

    index_path = registry / PROJECTS_INDEX_NAME
    previous_index_text: str | None = None
    if _path_exists(index_path):
        try:
            previous_index_text = index_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"cannot read {index_path}: {exc}")

    validation_text = previous_index_text or PROJECTS_INDEX_HEADER
    errors.extend(_validate_index(validation_text, projects, index_path))
    if previous_index_text is not None:
        document = orglib.parse(previous_index_text)
        for legacy in legacy_files:
            try:
                lookup = document.directories(legacy.project)
            except (orglib.OrgStructureError, ValueError):
                continue  # _validate_index already reports the structural error.
            actual = (
                previous_index_text[lookup.project_span.start : lookup.project_span.end]
                if lookup.project_span is not None
                else None
            )
            if actual != legacy.project_text:
                errors.append(
                    f"{legacy.path}: does not exactly match project "
                    f"{legacy.project!r} in {index_path}; resolve manually"
                )

    if errors:
        raise RegistryMigrationError(errors)

    ordered = tuple(
        sorted(legacy_files, key=lambda legacy: legacy.project.casefold())
    )
    index_text = (
        previous_index_text
        if previous_index_text is not None
        else PROJECTS_INDEX_HEADER + "".join(item.project_text for item in ordered)
    )
    return RegistryMigrationPlan(
        index_path,
        index_text,
        previous_index_text,
        ordered,
    )


def _assert_migration_preimages(plan: RegistryMigrationPlan) -> None:
    errors: list[str] = []
    index_exists = _path_exists(plan.index_path)
    if plan.previous_index_text is None:
        if index_exists:
            errors.append(f"{plan.index_path}: appeared after migration was planned")
    else:
        try:
            current_index = plan.index_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"cannot re-read {plan.index_path}: {exc}")
        else:
            if current_index != plan.previous_index_text:
                errors.append(f"{plan.index_path}: changed after migration was planned")

    for legacy in plan.legacy_files:
        try:
            current = legacy.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            errors.append(f"cannot re-read {legacy.path}: {exc}")
        else:
            if current != legacy.source_text:
                errors.append(f"{legacy.path}: changed after migration was planned")
    if errors:
        raise RegistryMigrationError(errors)


def apply_registry_migration(plan: RegistryMigrationPlan) -> None:
    """Apply a validated plan; interrupted cleanup is safe to resume."""
    _assert_migration_preimages(plan)
    if plan.writes_index:
        try:
            plan.index_path.parent.mkdir(parents=True, exist_ok=True)
            core.atomic_write(plan.index_path, plan.index_text)
        except OSError as exc:
            raise RegistryMigrationError(
                f"cannot write {plan.index_path}: {exc}; no legacy files removed"
            ) from exc

    for legacy in plan.legacy_files:
        try:
            current = legacy.path.read_text(encoding="utf-8")
            if current != legacy.source_text:
                raise RegistryMigrationError(
                    f"{legacy.path}: changed before cleanup; index retained, source not removed"
                )
            legacy.path.unlink()
        except RegistryMigrationError:
            raise
        except (OSError, UnicodeError) as exc:
            raise RegistryMigrationError(
                f"cleanup incomplete at {legacy.path}: {exc}; rerun migration"
            ) from exc


def project_root_for(start: Path) -> Path:
    """Walk upward from ``start`` for the directory that looks like a project root.

    The nearest ancestor holding a task file or a VCS directory wins, so
    registering from a subdirectory registers the project rather than the
    subdirectory. ``start`` itself is returned when nothing above it qualifies,
    and the walk never rises above the user's home directory.
    """
    start = start.resolve()
    home = Path.home().resolve()
    for directory in [start, *start.parents]:
        if any((directory / name).exists() for name in VCS_DIR_NAMES):
            return directory
        try:
            if core.preferred_task_file_in(directory) is not None:
                return directory
        except core.OrgFileDiscoveryError:
            # Ambiguity here only means "not obviously a root"; keep walking.
            pass
        if directory == home:
            break
    return start


# ---------------------------------------------------------------------------
# Project directory stacks (projmgr cdproj)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DirectorySource:
    """One location that can define a project's ``Directories`` section.

    ``label`` is ``"private"`` for the project section in the registry index,
    or ``"project"`` for the project's task file. ``entries`` is ``None`` when
    the location has no ``Directories`` section. ``line_num`` identifies the
    project heading to open when a shared index is edited.
    """

    label: str
    path: Path
    entries: list[str] | None
    line_num: int | None = None

    @property
    def defines_stack(self) -> bool:
        return self.entries is not None


@dataclass(frozen=True)
class RegistryDirectoriesPlan:
    """One source-revision-bound update to a project's private stack."""

    project: Project
    index_path: Path
    previous_index_text: str
    existing_paths: tuple[Path, ...]
    live_paths: tuple[Path, ...]
    additions: tuple[Path, ...]
    missing: tuple[Path, ...]


def legacy_private_files(registry: Path) -> list[Path]:
    """Return migration leftovers without reading their contents."""
    try:
        children = sorted(registry.iterdir(), key=lambda path: path.name.casefold())
    except OSError as exc:
        raise RegistryIndexError(f"cannot read registry {registry}: {exc}") from exc
    return [
        child / DIRECTORIES_PRIVATE_NAME
        for child in children
        if not child.name.startswith(".")
        and child.is_dir()
        and _path_exists(child / DIRECTORIES_PRIVATE_NAME)
    ]


def _read_registry_index(registry: Path) -> tuple[Path, str]:
    """Read a complete migrated index or raise the consumer-facing gate."""
    index_path = registry / PROJECTS_INDEX_NAME
    if not _path_exists(index_path):
        raise RegistryIndexError("registry not migrated; run pmgr migrate")

    leftovers = legacy_private_files(registry)
    if leftovers:
        names = ", ".join(friendly_path(path) for path in leftovers)
        raise RegistryIndexError(
            f"registry migration incomplete ({names}); run pmgr migrate"
        )

    try:
        return index_path, index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RegistryIndexError(f"cannot read {index_path}: {exc}") from exc


def require_registry_index(
    registry: Path, projects: list[Project] | tuple[Project, ...] = ()
) -> tuple[Path, str]:
    """Return a complete index after validating registered project sections."""
    index_path, text = _read_registry_index(registry)
    errors = _validate_index(text, list(projects), index_path)
    if errors:
        raise RegistryIndexError(errors)
    return index_path, text


def _index_project_headings(text: str) -> list[tuple[str, int]]:
    """Return normalized top-level heading names and one-based line numbers."""
    headings: list[tuple[str, int]] = []
    for line_num, line in enumerate(text.splitlines(), start=1):
        match = orglib.syntax.ANY_HEADING_RE.match(line)
        if match and len(match.group("stars")) == 1:
            title = orglib.syntax.project_heading_name(match.group("title"))
            headings.append((title, line_num))
    return headings


@dataclass(frozen=True)
class ProjectMetadata:
    """Presentation metadata for one registered project, read from the index.

    Empty everywhere is the normal state for a project the index says nothing
    about. ``warning`` describes a section that could not be read, so a caller
    can show the project and the problem instead of neither.

    None of this is authoritative. The registry decides membership, normal
    resolution finds the task file, and ``task_file`` is a mirror recorded for
    a person to read — see ``docs/ptui.md``.
    """

    priority: str | None = None
    description: str | None = None
    task_file: str | None = None
    warning: str | None = None


PROJECT_SORT_PRIORITY = "priority"
PROJECT_SORT_ALPHABETICAL = "alphabetical"
PROJECT_SORT_MODES = (PROJECT_SORT_PRIORITY, PROJECT_SORT_ALPHABETICAL)
_PROJECT_PRIORITY_RANK = {"A": 0, "B": 1, "C": 2}
PROJECT_PRIORITY_SCALE = (None, "C", "B", "A")


def project_name_sort_key(project: Project) -> tuple[str, str]:
    """Case-insensitive project ordering with deterministic case tie-breaking."""
    return project.name.casefold(), project.name


def project_priority_sort_key(
    project: Project, metadata: ProjectMetadata
) -> tuple[int, str, str]:
    """Org priority first, then the normal project-name ordering.

    ``ptui`` edits A through C. Other one-character Org priorities remain
    visible and sort after C but before an unset priority rather than being
    silently treated as absent.
    """
    priority = metadata.priority.upper() if metadata.priority else None
    if priority in _PROJECT_PRIORITY_RANK:
        rank = _PROJECT_PRIORITY_RANK[priority]
    elif priority is not None:
        rank = len(_PROJECT_PRIORITY_RANK)
    else:
        rank = len(_PROJECT_PRIORITY_RANK) + 1
    return rank, *project_name_sort_key(project)


def sort_projects(
    projects: list[Project] | tuple[Project, ...],
    metadata: dict[str, ProjectMetadata],
    mode: str,
) -> list[Project]:
    """Return projects in one of the navigator's non-mutating display orders."""
    if mode == PROJECT_SORT_PRIORITY:
        return sorted(
            projects,
            key=lambda project: project_priority_sort_key(
                project, metadata.get(project.name, ProjectMetadata())
            ),
        )
    if mode == PROJECT_SORT_ALPHABETICAL:
        return sorted(projects, key=project_name_sort_key)
    raise ValueError(f"unknown project sort mode: {mode}")


def next_project_sort_mode(mode: str) -> str:
    """Cycle through the project sort modes currently implemented by ptui."""
    try:
        index = PROJECT_SORT_MODES.index(mode)
    except ValueError as exc:
        raise ValueError(f"unknown project sort mode: {mode}") from exc
    return PROJECT_SORT_MODES[(index + 1) % len(PROJECT_SORT_MODES)]


def shift_project_priority(priority: str | None, direction: int) -> str | None:
    """Raise or lower an editable project priority, clamping at A and unset."""
    normalized = priority.upper() if priority is not None else None
    if normalized not in PROJECT_PRIORITY_SCALE:
        raise ValueError(f"unsupported project priority: {priority}")
    if direction not in {-1, 1}:
        raise ValueError(f"priority direction must be -1 or 1: {direction}")
    index = PROJECT_PRIORITY_SCALE.index(normalized)
    shifted = min(max(index + direction, 0), len(PROJECT_PRIORITY_SCALE) - 1)
    return PROJECT_PRIORITY_SCALE[shifted]


def _project_heading_with_priority(
    heading: str, current: str | None, target: str | None
) -> str:
    """Rewrite only one heading's leading priority cookie and preserve its EOL."""
    if heading.endswith("\r\n"):
        line, ending = heading[:-2], "\r\n"
    elif heading.endswith("\n") or heading.endswith("\r"):
        line, ending = heading[:-1], heading[-1]
    else:
        line, ending = heading, ""
    match = re.match(r"^(?P<prefix>\*+[ \t]+)(?P<title>.*)$", line)
    if match is None:  # pragma: no cover - ProjectSection already parsed it
        raise ValueError("cannot locate project heading priority")
    prefix = match.group("prefix")
    title = match.group("title")
    cookie = orglib.syntax.LEADING_PRIORITY_RE.match(title)
    if current is not None:
        if cookie is None:  # pragma: no cover - parser and source must agree
            raise ValueError("cannot locate existing project priority cookie")
        if target is None:
            title = title[cookie.end():]
        else:
            start, end = cookie.span("priority")
            title = title[:start] + target + title[end:]
    elif target is not None:
        title = f"[#{target}] {title}"
    return prefix + title + ending


def _malformed_priority_heading_line(text: str, project: str) -> int | None:
    """Line of a cookie-like heading for ``project`` that Org cannot parse."""
    wanted = project.casefold()
    for line_num, line in enumerate(text.splitlines(), start=1):
        match = orglib.syntax.ANY_HEADING_RE.match(line)
        if match is None or len(match.group("stars")) != 1:
            continue
        title = orglib.syntax.TRAILING_TAGS_RE.sub("", match.group("title")).strip()
        if not title.startswith("[#") or orglib.syntax.LEADING_PRIORITY_RE.match(title):
            continue
        closing = title.find("]")
        if closing >= 0 and title[closing + 1:].strip().casefold() == wanted:
            return line_num
        if closing < 0:
            parts = title.split(None, 1)
            if len(parts) == 2 and parts[1].strip().casefold() == wanted:
                return line_num
    return None


def change_project_priority(
    text: str, project: str, target: str | None
) -> str | None:
    """Return a source-preserving project-priority edit, or ``None`` if unchanged.

    Existing sections change only their heading line. Setting a priority for a
    registered project with no index section appends the smallest valid section;
    clearing an absent priority is a no-op.
    """
    if target is not None:
        target = target.upper()
        if target not in PROJECT_PRIORITY_SCALE:
            raise ValueError(f"unsupported project priority: {target}")
    malformed_line = _malformed_priority_heading_line(text, project)
    if malformed_line is not None:
        raise ValueError(
            f"malformed priority cookie for project {project!r} "
            f"at line {malformed_line}"
        )

    section = orglib.parse(text).project(project)
    if section is None:
        if target is None:
            return None
        prefix = text
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += "\n"
        revised = prefix + f"* [#{target}] {project}\n"
    else:
        current = section.priority.upper() if section.priority else None
        if current == target:
            return None
        heading = text[section.heading_span.start:section.heading_span.end]
        replacement = _project_heading_with_priority(heading, current, target)
        revised = (
            text[:section.heading_span.start]
            + replacement
            + text[section.heading_span.end:]
        )

    check = orglib.parse(revised).project(project)
    if check is None or (check.priority.upper() if check.priority else None) != target:
        raise ValueError(f"cannot safely set priority for project {project!r}")
    return revised


def _index_value(value: str | None) -> str | None:
    stripped = value.strip() if value else ""
    return stripped or None


def _project_metadata(section: orglib.ProjectSection) -> ProjectMetadata:
    """One index section as presentation metadata, reporting what repeats."""
    repeated = [
        name
        for name in ("DESCRIPTION", "TASK_FILE")
        if len(section.property_values(name)) > 1
    ]
    warning = (
        "recorded more than once: " + ", ".join(repeated) if repeated else None
    )
    return ProjectMetadata(
        priority=section.priority,
        description=_index_value(section.description),
        task_file=_index_value(section.task_file),
        warning=warning,
    )


def project_metadata_from_text(
    text: str, projects: list[Project] | tuple[Project, ...]
) -> dict[str, ProjectMetadata]:
    """Join one in-memory index revision onto registered projects by name."""
    metadata = {project.name: ProjectMetadata() for project in projects}
    document = orglib.parse(text)
    for project in projects:
        try:
            section = document.project(project.name)
        except (orglib.OrgStructureError, ValueError) as exc:
            metadata[project.name] = ProjectMetadata(warning=str(exc))
            continue
        if section is not None:
            metadata[project.name] = _project_metadata(section)
    return metadata


def read_project_metadata(
    registry: Path, projects: list[Project] | tuple[Project, ...]
) -> dict[str, ProjectMetadata]:
    """Read and join index metadata onto registry entries by name.

    Every project gets an entry, so a caller can build a row without checking
    first. A registry with no index yields metadata for none of them and no
    warnings: an unmigrated registry is ``doctor``'s business, not a reason to
    refuse to list projects. A section that cannot be read warns on that
    project alone, leaving the rest of the list intact.

    Index sections matching no registry entry are ignored here; reporting them
    is ``registry_index_problems``.
    """
    metadata = {project.name: ProjectMetadata() for project in projects}
    index_path = registry / PROJECTS_INDEX_NAME
    if not _path_exists(index_path):
        return metadata
    try:
        text = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        unreadable = ProjectMetadata(
            warning=f"cannot read {friendly_path(index_path)}: {exc}"
        )
        return dict.fromkeys(metadata, unreadable)

    return project_metadata_from_text(text, projects)


def registry_index_problems(registry: Path, projects: list[Project]) -> list[str]:
    """Diagnose migration and index structure without changing either layout."""
    problems: list[str] = []
    index_path = registry / PROJECTS_INDEX_NAME
    try:
        leftovers = legacy_private_files(registry)
    except RegistryIndexError as exc:
        return list(exc.messages)

    if not _path_exists(index_path):
        problems.append("registry not migrated; run pmgr migrate")
        return problems
    if leftovers:
        names = ", ".join(friendly_path(path) for path in leftovers)
        problems.append(
            f"registry migration incomplete ({names}); run pmgr migrate"
        )

    try:
        text = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        problems.append(f"cannot read {index_path}: {exc}")
        return problems

    headings = _index_project_headings(text)
    registered: dict[str, list[str]] = {}
    for project in projects:
        registered.setdefault(project.name.casefold(), []).append(project.name)
    for values in registered.values():
        if len(values) > 1:
            problems.append(
                "registry project names collide case-insensitively: "
                + ", ".join(values)
            )
        if values[0].casefold() in _RESERVED_INDEX_HEADINGS:
            problems.append(
                f"{index_path}: project name {values[0]!r} is reserved by the index"
            )

    indexed: dict[str, list[tuple[str, int]]] = {}
    for name, line_num in headings:
        if not name:
            problems.append(f"{index_path}: empty top-level heading at line {line_num}")
            continue
        indexed.setdefault(name.casefold(), []).append((name, line_num))

    document = orglib.parse(text)
    for folded, occurrences in indexed.items():
        if folded in _RESERVED_INDEX_HEADINGS:
            continue
        if len(occurrences) > 1:
            lines = ", ".join(str(line) for _, line in occurrences)
            problems.append(
                f"{index_path}: duplicate project heading "
                f"for {occurrences[0][0]!r} at lines {lines}"
            )
            continue
        name = occurrences[0][0]
        if folded not in registered:
            problems.append(f"{index_path}: stale project section {name!r}")
        try:
            document.directories(name)
        except (orglib.OrgStructureError, ValueError) as exc:
            problems.append(f"{index_path}: {exc}")

    return problems


def ensure_registry_project_directories(project: Project) -> tuple[Path, int]:
    """Ensure one project has an index section and return its heading line."""
    index_path, text = require_registry_index(project.path.parent, [project])
    lookup = orglib.parse(text).directories(project.name)
    if lookup.section is not None and lookup.project_span is not None:
        return index_path, lookup.project_span.start_line + 1

    if lookup.project_span is None:
        prefix = text
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += "\n"
        addition = f"* {project.name}\n** Directories\n"
        revised = prefix + addition
    else:
        insertion = lookup.project_span.end
        prefix = text[:insertion]
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += "\n"
        revised = prefix + "** Directories\n" + text[insertion:]

    check = orglib.parse(revised).directories(project.name)
    if check.project_span is None or check.section is None:
        raise RegistryIndexError(
            f"{index_path}: cannot create a safe section for {project.name!r}"
        )
    try:
        core.atomic_write(index_path, revised)
    except OSError as exc:
        raise RegistryIndexError(f"cannot write {index_path}: {exc}") from exc
    return index_path, check.project_span.start_line + 1


def unique_resolved_directories(entries: list[str], root: Path) -> list[Path]:
    """Resolve and deduplicate directory spellings, keeping their first order."""
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in resolve_directories(entries, root):
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def format_directory_path(path: Path) -> str:
    """Use ``~`` for paths under home and absolute spelling everywhere else."""
    resolved = path.resolve()
    home = Path.home().resolve()
    try:
        relative = resolved.relative_to(home)
    except ValueError:
        return str(resolved)
    return "~" if relative == Path(".") else f"~/{relative.as_posix()}"


def plan_registry_directories_update(
    project: Project, live_paths: list[Path]
) -> RegistryDirectoriesPlan:
    """Read one index preimage and compare its private stack with the live one."""
    index_path, text = require_registry_index(project.path.parent, [project])
    lookup = orglib.parse(text).directories(project.name)
    root = real_project_path(project)
    existing = (
        unique_resolved_directories(list(lookup.section.entries), root)
        if lookup.section is not None
        else []
    )
    live: list[Path] = []
    seen_live: set[Path] = set()
    for path in live_paths:
        resolved = path.resolve()
        if resolved not in seen_live:
            seen_live.add(resolved)
            live.append(resolved)
    existing_set = set(existing)
    live_set = set(live)
    return RegistryDirectoriesPlan(
        project=project,
        index_path=index_path,
        previous_index_text=text,
        existing_paths=tuple(existing),
        live_paths=tuple(live),
        additions=tuple(path for path in live if path not in existing_set),
        missing=tuple(path for path in existing if path not in live_set),
    )


def proposed_registry_directories(
    plan: RegistryDirectoriesPlan, *, keep_missing: bool
) -> tuple[Path, ...]:
    """Return the final stack after applying the selected subtraction policy."""
    if not keep_missing:
        return plan.live_paths
    return plan.live_paths + plan.missing


def registry_directories_section(paths: tuple[Path, ...]) -> str:
    """Render the canonical direct-child directory section."""
    return "** Directories\n" + "".join(
        f"   - {format_directory_path(path)}\n" for path in paths
    )


def render_registry_directories_update(
    plan: RegistryDirectoriesPlan, *, keep_missing: bool
) -> tuple[str, str]:
    """Return ``(complete index, replacement section)`` without writing."""
    text = plan.previous_index_text
    document = orglib.parse(text)
    lookup = document.directories(plan.project.name)
    final_paths = proposed_registry_directories(plan, keep_missing=keep_missing)
    section_text = registry_directories_section(final_paths)

    if lookup.section is not None:
        span = lookup.section.span
        revised = text[:span.start] + section_text + text[span.end:]
    elif lookup.project_span is not None:
        insertion = lookup.project_span.end
        prefix = text[:insertion]
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += "\n"
        revised = prefix + section_text + text[insertion:]
    else:
        prefix = text
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += "\n"
        revised = prefix + f"* {plan.project.name}\n" + section_text

    check = orglib.parse(revised).directories(plan.project.name)
    if check.section is None:
        raise RegistryIndexError(
            f"{plan.index_path}: cannot create a safe section "
            f"for {plan.project.name!r}"
        )
    return revised, section_text


def apply_registry_directories_update(
    plan: RegistryDirectoriesPlan, *, keep_missing: bool
) -> bool:
    """Apply one planned update if its index preimage is still current."""
    _, current = require_registry_index(plan.project.path.parent, [plan.project])
    if current != plan.previous_index_text:
        raise RegistryIndexError(
            f"{plan.index_path}: changed after directory update was planned"
        )
    revised, _ = render_registry_directories_update(
        plan, keep_missing=keep_missing
    )
    if revised == current:
        return False
    try:
        core.atomic_write(plan.index_path, revised)
    except OSError as exc:
        raise RegistryIndexError(f"cannot write {plan.index_path}: {exc}") from exc
    return True


def directory_candidates(project: Project) -> list[DirectorySource]:
    """Both places a project's directory stack may live, private first.

    The private candidate is always the migrated registry index. Missing or
    incomplete migration is an error rather than a fallback to a legacy file.
    """
    index_path, index_text = require_registry_index(project.path.parent, [project])
    lookup = orglib.parse(index_text).directories(project.name)
    private_entries = (
        list(lookup.section.entries) if lookup.section is not None else None
    )
    private_line = (
        lookup.project_span.start_line + 1
        if lookup.project_span is not None
        else None
    )
    sources = [
        DirectorySource("private", index_path, private_entries, private_line)
    ]
    org_file = canonical_org_file(project)
    if org_file is not None:
        try:
            entries = core.parse_directories(org_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            entries = None
        sources.append(DirectorySource("project", org_file, entries))
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
# Multi-project summaries (projmgr list)
# ---------------------------------------------------------------------------

def summarize_projects(workspace: Path, include_all: bool = False) -> list[dict]:
    """Return JSON-ready records for each project in a workspace.

    Each record has ``project``, ``path`` (the real project directory), ``file``
    (the resolved Org file path, or ``None`` when the project has no task file),
    and ``tasks`` — top-level tasks, TODO-only unless ``include_all``. Projects
    that are broken or ambiguous, or whose Org file is unreadable, has no
    parseable task headings, or has duplicate IDs, carry a ``warning`` and empty
    ``tasks`` instead of raising.
    """
    records: list[dict] = []

    for project in discover_projects(workspace):
        real_org_file = canonical_org_file(project)
        record: dict = {
            "project": project.name,
            "path": friendly_path(real_project_path(project)),
            "file": friendly_path(real_org_file) if real_org_file else None,
            "tasks": [],
        }

        if project.warning:
            record["warning"] = project.warning
            records.append(record)
            continue

        if real_org_file is None:
            # Normal state, not a warning: a project can be registered before it
            # has any tasks, and directory-stack navigation never needs one.
            records.append(record)
            continue

        try:
            text = real_org_file.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — surface any read failure as a warning
            record["warning"] = f"Could not read file: {e}"
            record["tasks"] = []
            records.append(record)
            continue

        tasks = orglib.parse(text).tasks()
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
