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
from . import viewstate

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
    entry_org_files = [
        p
        for p in children
        if p.is_file()
        and p.suffix == ".org"
        and p.name != DIRECTORIES_PRIVATE_NAME
    ]
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

    org_file: Path | None = None
    if len(entry_org_files) == 1:
        org_file = entry_org_files[0]
    elif len(entry_org_files) > 1:
        detail = "several task-file links: " + ", ".join(
            path.name for path in entry_org_files
        )
        warning = f"{warning}; {detail}" if warning else detail
    elif link is not None:
        try:
            candidate = core.discover_org_file(link.resolve())
            if candidate is not None:
                try:
                    text = candidate.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    detail = f"cannot read task file {friendly_path(candidate)}: {exc}"
                    warning = f"{warning}; {detail}" if warning else detail
                else:
                    if has_task_section(text):
                        org_file = candidate
        except core.OrgFileDiscoveryError as exc:
            detail = str(exc)
            warning = f"{warning}; {detail}" if warning else detail

    return Project(entry.name, entry, org_file, link, warning)


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
PROJECT_SORT_MODIFIED = "modified"
PROJECT_SORT_MODES = (
    PROJECT_SORT_PRIORITY,
    PROJECT_SORT_ALPHABETICAL,
    PROJECT_SORT_MODIFIED,
)
PROJECT_FILTER_ALL = "all"
PROJECT_FILTER_OPEN = "open"
PROJECT_FILTERS = (PROJECT_FILTER_ALL, PROJECT_FILTER_OPEN)
_PROJECT_PRIORITY_RANK = {"A": 0, "B": 1, "C": 2}
PROJECT_PRIORITY_SCALE = (None, "C", "B", "A")


def project_name_sort_key(project: Project) -> tuple[str, str]:
    """Case-insensitive project ordering with deterministic case tie-breaking."""
    return project.name.casefold(), project.name


def project_priority_rank(metadata: ProjectMetadata) -> int:
    """Where one project's Org priority sorts, as a single comparable axis.

    ``ptui`` edits A through C. Other one-character Org priorities remain
    visible and sort after C but before an unset priority rather than being
    silently treated as absent.
    """
    priority = metadata.priority.upper() if metadata.priority else None
    if priority in _PROJECT_PRIORITY_RANK:
        return _PROJECT_PRIORITY_RANK[priority]
    if priority is not None:
        return len(_PROJECT_PRIORITY_RANK)
    return len(_PROJECT_PRIORITY_RANK) + 1


def project_priority_sort_key(
    project: Project, metadata: ProjectMetadata
) -> tuple[int, str, str]:
    """Org priority first, then the normal project-name ordering."""
    return project_priority_rank(metadata), *project_name_sort_key(project)


@dataclass(frozen=True)
class ProjectSnapshot:
    """One project's task file as a render saw it: counts and mtime, read once.

    Filtering, ordering, row text, and diagnostics all want the same two facts
    about the same file. Reading it per question means parsing and stating the
    same Org file several times per keystroke, and worse, lets one render
    disagree with itself if the file changes midway.
    """

    open_tasks: int | None = None
    total_tasks: int | None = None
    modified_ns: int | None = None

    @property
    def readable(self) -> bool:
        return self.open_tasks is not None

    @property
    def unavailable(self) -> int:
        """1 when the task file cannot be read, so it can be kept last."""
        return 0 if self.modified_ns is not None else 1


def read_project_snapshot(project: Project) -> ProjectSnapshot:
    """Open one project's task file once and answer everything a render asks."""
    if project.warning:
        return ProjectSnapshot()
    try:
        org_file = canonical_org_file(project)
    except (OSError, RuntimeError):
        return ProjectSnapshot()
    if org_file is None:
        return ProjectSnapshot()
    try:
        with org_file.open("rb") as stream:
            modified_ns = os.fstat(stream.fileno()).st_mtime_ns
            text = stream.read().decode("utf-8")
    except (OSError, RuntimeError, UnicodeError):
        return ProjectSnapshot()
    try:
        parsed = orglib.parse(text).tasks()
    except ValueError:
        return ProjectSnapshot(modified_ns=modified_ns)
    if not parsed:
        return ProjectSnapshot(0, 0, modified_ns)
    root_level = min(task.level for task in parsed)
    top_level = [task for task in parsed if task.level == root_level]
    return ProjectSnapshot(
        sum(1 for task in top_level if task.state == "TODO"),
        len(top_level),
        modified_ns,
    )


def snapshot_projects(
    projects: list[Project] | tuple[Project, ...],
) -> dict[str, ProjectSnapshot]:
    """One snapshot per project, for one render."""
    return {project.name: read_project_snapshot(project) for project in projects}


def _snapshot_for(
    project: Project, snapshots: dict[str, ProjectSnapshot] | None
) -> ProjectSnapshot:
    if snapshots is None:
        return read_project_snapshot(project)
    return snapshots.get(project.name) or ProjectSnapshot()


def project_modified_ns(project: Project) -> int | None:
    """The canonical task file's modification time, or ``None`` if unreadable."""
    return read_project_snapshot(project).modified_ns


def project_unavailable(project: Project) -> int:
    """1 for a project whose task file cannot be read, so it can be kept last.

    Kept out of the recency axis on purpose: reversing the order must not
    promote the projects a reader can learn the least from.
    """
    return read_project_snapshot(project).unavailable


def project_modified_sort_key(project: Project) -> tuple[int, int, str, str]:
    """Newest canonical task file first; unavailable files sort last."""
    snapshot = read_project_snapshot(project)
    newest_first = (
        -snapshot.modified_ns if snapshot.modified_ns is not None else 0
    )
    return snapshot.unavailable, newest_first, *project_name_sort_key(project)


def task_file_mirror_mismatch(
    project: Project, recorded: str | None
) -> str | None:
    """Describe a non-normative ``TASK_FILE`` disagreement, if one exists."""
    value = recorded.strip() if recorded else ""
    if not value:
        return None
    resolved_file = canonical_org_file(project)
    resolved_display = (
        friendly_path(resolved_file) if resolved_file is not None else "(no task file)"
    )
    if resolved_file is not None:
        try:
            expanded = Path(os.path.expandvars(value)).expanduser()
            if not expanded.is_absolute():
                expanded = real_project_path(project) / expanded
            if expanded.resolve() == resolved_file:
                return None
        except (OSError, RuntimeError, ValueError):
            pass
    return f"TASK_FILE differs: recorded {value}; resolved {resolved_display}"


def sort_projects(
    projects: list[Project] | tuple[Project, ...],
    metadata: dict[str, ProjectMetadata],
    mode: str,
    *,
    reverse: bool = False,
    snapshots: dict[str, ProjectSnapshot] | None = None,
) -> list[Project]:
    """Return projects in one of the navigator's non-mutating display orders.

    ``reverse`` inverts the mode's own axis and nothing else, so an unreadable
    task file stays at the bottom and the project-name tie-break stays
    ascending. ``viewstate.order_by`` is what enforces that.
    """
    if mode == PROJECT_SORT_PRIORITY:
        return viewstate.order_by(
            projects,
            primary=lambda project: project_priority_rank(
                metadata.get(project.name, ProjectMetadata())
            ),
            tiebreak=project_name_sort_key,
            reverse=reverse,
        )
    if mode == PROJECT_SORT_ALPHABETICAL:
        return viewstate.order_by(
            projects,
            primary=lambda project: project.name.casefold(),
            tiebreak=lambda project: project.name,
            reverse=reverse,
        )
    if mode == PROJECT_SORT_MODIFIED:
        return viewstate.order_by(
            projects,
            primary=lambda project: -(
                _snapshot_for(project, snapshots).modified_ns or 0
            ),
            tiebreak=project_name_sort_key,
            unavailable=lambda project: _snapshot_for(
                project, snapshots
            ).unavailable,
            reverse=reverse,
        )
    raise ValueError(f"unknown project sort mode: {mode}")


def next_project_sort_mode(mode: str) -> str:
    """Cycle through the project sort modes currently implemented by ptui."""
    try:
        return viewstate.next_position(PROJECT_SORT_MODES, mode)
    except ValueError as exc:
        raise ValueError(f"unknown project sort mode: {mode}") from exc


def top_level_task_counts(project: Project) -> tuple[int, int] | None:
    """``(open, total)`` top-level tasks, or ``None`` when they cannot be read.

    Counts the same top-level tasks ``projmgr.py list`` shows, so the navigator
    row, the project filter, and that listing never disagree.
    """
    snapshot = read_project_snapshot(project)
    if not snapshot.readable:
        return None
    return snapshot.open_tasks, snapshot.total_tasks


def filter_projects(
    projects: list[Project] | tuple[Project, ...],
    mode: str,
    *,
    snapshots: dict[str, ProjectSnapshot] | None = None,
) -> list[Project]:
    """Hide only what the navigator can prove is quiet.

    A project whose task file is missing, broken, or unreadable stays visible:
    ``docs/ptui.md`` requires warnings to survive filtering, and a project that
    cannot be read is not a project with nothing left to do.
    """
    if mode == PROJECT_FILTER_ALL:
        return list(projects)
    if mode != PROJECT_FILTER_OPEN:
        raise ValueError(f"unknown project filter: {mode}")
    kept = []
    for project in projects:
        snapshot = _snapshot_for(project, snapshots)
        if not snapshot.readable or snapshot.open_tasks > 0:
            kept.append(project)
    return kept


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


def _source_eol(text: str) -> str:
    """Use the source's first line ending, defaulting to LF for new text."""
    match = re.search(r"\r\n|\n|\r", text)
    return match.group(0) if match is not None else "\n"


def _line_body_and_eol(line: str) -> tuple[str, str]:
    """Split one source line without treating a bare CR as content."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith(("\n", "\r")):
        return line[:-1], line[-1]
    return line, ""


def change_project_property(
    text: str,
    project: str,
    property_name: str,
    target: str | None,
) -> str | None:
    """Return one source-preserving project property edit.

    The matched property line is the only existing line rewritten. Missing
    drawers are inserted directly below the project heading; unrelated drawer
    entries and project content remain byte-identical.
    """
    name = property_name.strip().upper()
    if not name or re.search(r"[\s:]", name):
        raise ValueError(f"invalid project property name: {property_name!r}")
    if target is not None and any(character in target for character in "\r\n"):
        raise ValueError(f"{name} must be a single line")
    target = _index_value(target)
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
        eol = _source_eol(text)
        prefix = text
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += eol
        revised = (
            prefix
            + f"* {project}{eol}"
            + f":PROPERTIES:{eol}:{name}: {target}{eol}:END:{eol}"
        )
    else:
        values = section.property_values(name)
        if len(values) > 1:
            raise ValueError(
                f"cannot edit repeated {name} property for project {project!r}"
            )
        current = _index_value(values[0]) if values else None
        if current == target:
            return None

        if section.drawer_span is None:
            if target is None:  # pragma: no cover - no value implies no drawer
                return None
            heading = text[
                section.heading_span.start : section.heading_span.end
            ]
            eol = _source_eol(heading or text)
            separator = "" if heading.endswith(("\n", "\r")) else eol
            drawer = (
                separator
                + f":PROPERTIES:{eol}:{name}: {target}{eol}:END:{eol}"
            )
            insertion = section.heading_span.end
            revised = text[:insertion] + drawer + text[insertion:]
        else:
            span = section.drawer_span
            drawer = text[span.start : span.end]
            lines = drawer.splitlines(keepends=True)
            matches: list[int] = []
            for index, line in enumerate(lines[1:-1], start=1):
                body, _ending = _line_body_and_eol(line)
                match = orglib.syntax.PROPERTY_RE.match(body)
                if (
                    match is not None
                    and match.group("name").casefold() == name.casefold()
                ):
                    matches.append(index)
            if len(matches) > 1:  # parser values and source should agree
                raise ValueError(
                    f"cannot edit repeated {name} property for project {project!r}"
                )
            if matches:
                index = matches[0]
                if target is None:
                    del lines[index]
                else:
                    body, ending = _line_body_and_eol(lines[index])
                    indent = body[: len(body) - len(body.lstrip())]
                    lines[index] = f"{indent}:{name}: {target}{ending}"
            else:
                assert target is not None
                closing = len(lines) - 1
                eol = _source_eol(drawer or text)
                lines.insert(closing, f":{name}: {target}{eol}")
            replacement = "".join(lines)
            revised = text[:span.start] + replacement + text[span.end:]

    check = orglib.parse(revised).project(project)
    if check is None:
        raise ValueError(f"cannot safely set {name} for project {project!r}")
    actual = tuple(_index_value(value) for value in check.property_values(name))
    expected = () if target is None else (target,)
    if actual != expected:
        raise ValueError(f"cannot safely set {name} for project {project!r}")
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
    warnings: an unmigrated registry is ``repair``'s business, not a reason to
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


# Extraction seam (t0038.2): keyed source-region merge mechanics belong in
# orglib after its Region contract exists. Keep this planner pure; registry
# names, reserved sections, and allowed edits remain project-index policy.
@dataclass(frozen=True)
class ProjectIndexMergeConflict:
    """One reason a Base/Ours/Theirs project-index merge is unsafe."""

    kind: str
    detail: str
    project: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class ProjectIndexMergePlan:
    """Pure result of planning a project-section three-way merge."""

    merged_text: str | None
    replayed_projects: tuple[str, ...] = ()
    absorbed_projects: tuple[str, ...] = ()
    conflicts: tuple[ProjectIndexMergeConflict, ...] = ()

    @property
    def can_merge(self) -> bool:
        return self.merged_text is not None and not self.conflicts


@dataclass(frozen=True)
class _ProjectMergeSection:
    name: str
    key: str
    text: str
    start: int
    end: int
    line: int


@dataclass(frozen=True)
class _ProjectMergeRevision:
    text: str
    preamble: str
    sections: tuple[_ProjectMergeSection, ...]

    @property
    def by_key(self) -> dict[str, _ProjectMergeSection]:
        return {section.key: section for section in self.sections}

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(section.key for section in self.sections)


def _project_merge_revision(
    text: str, source: str
) -> tuple[_ProjectMergeRevision | None, tuple[ProjectIndexMergeConflict, ...]]:
    """Parse one merge input and reject ambiguous normalized section names."""
    try:
        document = orglib.parse(text)
        parsed = document.projects()
    except (orglib.OrgStructureError, ValueError) as exc:
        return None, (
            ProjectIndexMergeConflict("invalid_input", str(exc), source=source),
        )

    sections: list[_ProjectMergeSection] = []
    seen: dict[str, _ProjectMergeSection] = {}
    conflicts: list[ProjectIndexMergeConflict] = []
    for section in parsed:
        name = section.name.strip()
        key = name.casefold()
        heading = text[section.heading_span.start:section.heading_span.end]
        heading_match = orglib.syntax.ANY_HEADING_RE.match(heading.rstrip("\r\n"))
        heading_title = (
            orglib.syntax.TRAILING_TAGS_RE.sub("", heading_match.group("title"))
            .strip()
            if heading_match is not None
            else ""
        )
        current = _ProjectMergeSection(
            name=name,
            key=key,
            text=text[section.span.start:section.span.end],
            start=section.span.start,
            end=section.span.end,
            line=section.span.start_line + 1,
        )
        if heading_title.startswith("[#") and not orglib.syntax.LEADING_PRIORITY_RE.match(
            heading_title
        ):
            conflicts.append(
                ProjectIndexMergeConflict(
                    "invalid_input",
                    f"malformed priority cookie at line {current.line}",
                    project=name,
                    source=source,
                )
            )
        elif not key:
            conflicts.append(
                ProjectIndexMergeConflict(
                    "invalid_input",
                    f"blank top-level heading at line {section.span.start_line + 1}",
                    source=source,
                )
            )
        elif key in seen:
            first = seen[key]
            conflicts.append(
                ProjectIndexMergeConflict(
                    "invalid_input",
                    f"duplicate normalized project heading {name!r} at lines "
                    f"{first.line} and {current.line}",
                    project=name,
                    source=source,
                )
            )
        else:
            seen[key] = current
        sections.append(current)

    if conflicts:
        return None, tuple(conflicts)
    for section in sections:
        if section.key in _RESERVED_INDEX_HEADINGS:
            continue
        try:
            document.directories(section.name)
        except (orglib.OrgStructureError, ValueError) as exc:
            conflicts.append(
                ProjectIndexMergeConflict(
                    "invalid_input",
                    str(exc),
                    project=section.name,
                    source=source,
                )
            )
    if conflicts:
        return None, tuple(conflicts)
    preamble_end = sections[0].start if sections else len(text)
    return _ProjectMergeRevision(text, text[:preamble_end], tuple(sections)), ()


def _local_project_structure_conflicts(
    base: _ProjectMergeRevision,
    ours: _ProjectMergeRevision,
) -> tuple[ProjectIndexMergeConflict, ...]:
    """Reject local changes that are not edits to an existing project subtree."""
    conflicts: list[ProjectIndexMergeConflict] = []
    if ours.preamble != base.preamble:
        conflicts.append(
            ProjectIndexMergeConflict(
                "local_outside_project",
                "local changes before the first project heading are not mergeable",
                source="ours",
            )
        )

    base_by_key = base.by_key
    ours_by_key = ours.by_key
    for key in ours_by_key.keys() - base_by_key.keys():
        project = ours_by_key[key].name
        conflicts.append(
            ProjectIndexMergeConflict(
                "local_project_added",
                f"locally added project section {project!r} is not addressable in Base",
                project=project,
                source="ours",
            )
        )
    for key in base_by_key.keys() - ours_by_key.keys():
        project = base_by_key[key].name
        conflicts.append(
            ProjectIndexMergeConflict(
                "local_project_deleted",
                f"locally deleted project section {project!r} cannot be replayed",
                project=project,
                source="ours",
            )
        )
    if set(base.order) == set(ours.order) and base.order != ours.order:
        conflicts.append(
            ProjectIndexMergeConflict(
                "local_project_reordered",
                "local project-section reordering cannot be replayed safely",
                source="ours",
            )
        )

    for key in set(base_by_key) & set(ours_by_key) & _RESERVED_INDEX_HEADINGS:
        if base_by_key[key].text != ours_by_key[key].text:
            project = base_by_key[key].name
            conflicts.append(
                ProjectIndexMergeConflict(
                    "local_outside_project",
                    f"local changes to reserved section {project!r} are not mergeable",
                    project=project,
                    source="ours",
                )
            )
    return tuple(conflicts)


def _externally_changed_projects(
    base: _ProjectMergeRevision,
    theirs: _ProjectMergeRevision,
) -> tuple[str, ...]:
    """Project names whose external section contents, presence, or order changed."""
    base_by_key = base.by_key
    theirs_by_key = theirs.by_key
    changed: set[str] = set()
    for key in set(base_by_key) | set(theirs_by_key):
        if key in _RESERVED_INDEX_HEADINGS:
            continue
        before = base_by_key.get(key)
        after = theirs_by_key.get(key)
        if before is None or after is None or before.text != after.text:
            changed.add(key)

    base_common = [
        key
        for key in base.order
        if key in theirs_by_key and key not in _RESERVED_INDEX_HEADINGS
    ]
    theirs_common = [
        key
        for key in theirs.order
        if key in base_by_key and key not in _RESERVED_INDEX_HEADINGS
    ]
    if base_common != theirs_common:
        changed.update(key for key in base_common if key not in _RESERVED_INDEX_HEADINGS)

    ordered_keys = [
        section.key for section in theirs.sections if section.key in changed
    ]
    ordered_keys.extend(
        section.key
        for section in base.sections
        if section.key in changed and section.key not in theirs_by_key
    )
    return tuple(
        (theirs_by_key.get(key) or base_by_key[key]).name for key in ordered_keys
    )


def plan_project_index_merge(
    base_text: str,
    ours_text: str,
    theirs_text: str,
) -> ProjectIndexMergePlan:
    """Plan a no-I/O, section-level three-way merge over ``projects.org``.

    Theirs is the output canvas. Each locally changed existing project section
    is replayed only when the matching external section remains byte-identical
    to Base; an identical local/external edit is accepted without replacement.
    """
    revisions: dict[str, _ProjectMergeRevision] = {}
    conflicts: list[ProjectIndexMergeConflict] = []
    for source, text in (
        ("base", base_text),
        ("ours", ours_text),
        ("theirs", theirs_text),
    ):
        revision, parse_conflicts = _project_merge_revision(text, source)
        conflicts.extend(parse_conflicts)
        if revision is not None:
            revisions[source] = revision
    if conflicts:
        return ProjectIndexMergePlan(None, conflicts=tuple(conflicts))

    base = revisions["base"]
    ours = revisions["ours"]
    theirs = revisions["theirs"]
    structure_conflicts = _local_project_structure_conflicts(base, ours)
    if structure_conflicts:
        return ProjectIndexMergePlan(None, conflicts=structure_conflicts)

    base_by_key = base.by_key
    ours_by_key = ours.by_key
    theirs_by_key = theirs.by_key
    local_keys = [
        section.key
        for section in base.sections
        if section.key not in _RESERVED_INDEX_HEADINGS
        and ours_by_key[section.key].text != section.text
    ]
    replacements: list[tuple[int, int, str]] = []
    merge_conflicts: list[ProjectIndexMergeConflict] = []
    for key in local_keys:
        base_section = base_by_key[key]
        ours_section = ours_by_key[key]
        theirs_section = theirs_by_key.get(key)
        if theirs_section is None:
            merge_conflicts.append(
                ProjectIndexMergeConflict(
                    "external_project_deleted",
                    f"externally deleted locally changed project {base_section.name!r}",
                    project=base_section.name,
                    source="theirs",
                )
            )
        elif theirs_section.text == ours_section.text:
            continue
        elif theirs_section.text == base_section.text:
            replacements.append(
                (theirs_section.start, theirs_section.end, ours_section.text)
            )
        else:
            merge_conflicts.append(
                ProjectIndexMergeConflict(
                    "project_changed_both",
                    f"project section {base_section.name!r} changed differently "
                    "in Ours and Theirs",
                    project=base_section.name,
                )
            )
    if merge_conflicts:
        return ProjectIndexMergePlan(None, conflicts=tuple(merge_conflicts))

    merged = theirs.text
    for start, end, replacement in sorted(replacements, reverse=True):
        merged = merged[:start] + replacement + merged[end:]

    merged_revision, validation_conflicts = _project_merge_revision(
        merged, "merged"
    )
    if merged_revision is None:
        return ProjectIndexMergePlan(None, conflicts=validation_conflicts)
    return ProjectIndexMergePlan(
        merged,
        replayed_projects=tuple(base_by_key[key].name for key in local_keys),
        absorbed_projects=_externally_changed_projects(base, theirs),
    )


def missing_index_sections(
    registry: Path, projects: list[Project]
) -> list[Project]:
    """Registered projects the index has no section for, in listing order.

    Empty when the index is absent or unreadable, which are migration problems
    reported on their own terms. A project whose symlink is broken is left out:
    its stack cannot be resolved, and the broken link is the thing to fix.
    """
    index_path = registry / PROJECTS_INDEX_NAME
    if not _path_exists(index_path):
        return []
    try:
        text = index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return []
    indexed = {
        name.casefold() for name, _ in _index_project_headings(text) if name
    }
    return [
        project
        for project in projects
        if not project.warning and project.name.casefold() not in indexed
    ]


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

    for project in missing_index_sections(registry, projects):
        problems.append(f"{index_path}: no section for {project.name!r}")

    return problems


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


def registry_directories_section(
    paths: tuple[Path, ...], *, eol: str = "\n"
) -> str:
    """Render the canonical direct-child directory section."""
    return f"** Directories{eol}" + "".join(
        f"   - {format_directory_path(path)}{eol}" for path in paths
    )


def initial_directory_stack(root: Path, org_file: Path | None) -> list[Path]:
    """Where a newly indexed project's directory stack should start.

    Its own ``* Directories`` section when the task file has one, so writing
    the index does not change the stack ``cdproj`` already produces; the
    project root alone otherwise.
    """
    entries: list[str] = []
    if org_file is not None:
        try:
            entries = core.parse_directories(org_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            entries = []
    resolved = unique_resolved_directories(entries, root) if entries else []
    return resolved or [root.resolve()]


def project_section_text(
    project: str,
    directories: list[Path] | tuple[Path, ...],
    *,
    task_file: Path | None = None,
    eol: str = "\n",
) -> str:
    """Render one project's index section: heading, properties, directories.

    ``DESCRIPTION`` is written empty, as the slot a person fills in.
    ``TASK_FILE`` mirrors the file the registry entry links, and is left out
    when there is none to name; ``docs/config.md`` keeps it non-normative, so
    nothing resolves a task file through it.
    """
    lines = [f"* {project}", ":PROPERTIES:", ":DESCRIPTION:"]
    if task_file is not None:
        lines.append(f":TASK_FILE: {friendly_path(task_file)}")
    lines.append(":END:")
    heading = eol.join(lines) + eol
    return heading + registry_directories_section(tuple(directories), eol=eol)


def add_project_section(
    text: str,
    project: str,
    directories: list[Path] | tuple[Path, ...],
    *,
    task_file: Path | None = None,
) -> str | None:
    """Return the index with a section for ``project``, or None if it has one."""
    malformed_line = _malformed_priority_heading_line(text, project)
    if malformed_line is not None:
        raise ValueError(
            f"malformed priority cookie for project {project!r} "
            f"at line {malformed_line}"
        )
    if orglib.parse(text).directories(project).project_span is not None:
        return None

    eol = _source_eol(text)
    prefix = text
    if prefix and not prefix.endswith(("\n", "\r")):
        prefix += eol
    revised = prefix + project_section_text(
        project, directories, task_file=task_file, eol=eol
    )

    check = orglib.parse(revised).directories(project)
    expected = tuple(format_directory_path(path) for path in directories)
    if check.section is None or check.section.entries != expected:
        raise RegistryIndexError(
            f"cannot create a safe index section for {project!r}"
        )
    return revised


def change_project_directories(
    text: str,
    project: str,
    paths: list[Path] | tuple[Path, ...],
) -> str | None:
    """Return one bounded, in-memory rewrite of a project's directory stack."""
    malformed_line = _malformed_priority_heading_line(text, project)
    if malformed_line is not None:
        raise ValueError(
            f"malformed priority cookie for project {project!r} "
            f"at line {malformed_line}"
        )
    final_paths = tuple(dict.fromkeys(path.resolve() for path in paths))
    document = orglib.parse(text)
    lookup = document.directories(project)
    eol = _source_eol(text)
    section_text = registry_directories_section(final_paths, eol=eol)

    if lookup.section is not None:
        span = lookup.section.span
        revised = text[:span.start] + section_text + text[span.end:]
    elif lookup.project_span is not None:
        insertion = lookup.project_span.end
        prefix = text[:insertion]
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += eol
        revised = prefix + section_text + text[insertion:]
    else:
        prefix = text
        if prefix and not prefix.endswith(("\n", "\r")):
            prefix += eol
        revised = prefix + f"* {project}{eol}" + section_text

    check = orglib.parse(revised).directories(project)
    expected = tuple(format_directory_path(path) for path in final_paths)
    if check.section is None or check.section.entries != expected:
        raise RegistryIndexError(
            f"cannot create a safe Directories section for {project!r}"
        )
    return None if revised == text else revised


def render_registry_directories_update(
    plan: RegistryDirectoriesPlan, *, keep_missing: bool
) -> tuple[str, str]:
    """Return ``(complete index, replacement section)`` without writing."""
    text = plan.previous_index_text
    final_paths = proposed_registry_directories(plan, keep_missing=keep_missing)
    section_text = registry_directories_section(
        final_paths, eol=_source_eol(text)
    )
    changed = change_project_directories(text, plan.project.name, final_paths)
    revised = changed if changed is not None else text
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


def directory_candidates_from_index(
    project: Project, index_path: Path, index_text: str
) -> list[DirectorySource]:
    """Both directory sources using the caller's in-memory index revision."""
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


def directory_candidates(project: Project) -> list[DirectorySource]:
    """Both places a project's directory stack may live, private first.

    The private candidate is always the migrated registry index. Missing or
    incomplete migration is an error rather than a fallback to a legacy file.
    """
    index_path, index_text = require_registry_index(project.path.parent, [project])
    return directory_candidates_from_index(project, index_path, index_text)


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
