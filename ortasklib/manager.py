"""Project management helpers shared by ``orgmgr.py`` and ``projtui.py``.

Covers config-path resolution, the legacy ``projdir`` workspace model, project
discovery, per-project Org-file selection, and JSON-ready multi-project task
summaries. Like the rest of ``ortasklib``, it does no argument parsing and never
calls ``sys.exit``.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path

from . import core

SKIP_PROJECT_DIRS = {".git", ".hg", ".svn", "__pycache__", "docs"}
DEFAULT_PROJDIR = "~/Projects"
CONFIG_SECTION = "projtui"
CONFIG_OPTION = "projdir"


@dataclass(frozen=True)
class Project:
    name: str
    path: Path
    org_file: Path


# ---------------------------------------------------------------------------
# Config / workspace resolution
# ---------------------------------------------------------------------------

def default_config_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home).expanduser() / "ortask" / "projtui.ini"
    return Path.home() / ".config" / "ortask" / "projtui.ini"


def read_config_projdir(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    if not parser.has_option(CONFIG_SECTION, CONFIG_OPTION):
        return None
    value = parser.get(CONFIG_SECTION, CONFIG_OPTION).strip()
    return value or None


def _friendly_path(path: Path, original: str | None = None) -> str:
    if original and original.startswith("~"):
        return original
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def resolve_projdir(cli_projdir: str | None, config_path: Path) -> tuple[Path, str]:
    raw = cli_projdir or read_config_projdir(config_path) or DEFAULT_PROJDIR
    resolved = Path(raw).expanduser().resolve()
    return resolved, _friendly_path(resolved, raw)


# ---------------------------------------------------------------------------
# Project / Org-file discovery
# ---------------------------------------------------------------------------

def _org_sort_key(path: Path) -> tuple[int, str]:
    lower = path.name.lower()
    if path.name == "TODO.org":
        return (0, lower)
    if lower == "todo.org":
        return (1, lower)
    if path.name.startswith("TODO-") and path.suffix == ".org":
        return (2, lower)
    return (3, lower)


def choose_org_file(project_dir: Path) -> Path | None:
    root_files = sorted(
        (p for p in project_dir.iterdir() if p.is_file() and p.suffix == ".org"),
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
# Multi-project summaries (orgmgr list)
# ---------------------------------------------------------------------------

def summarize_projects(workspace: Path, include_all: bool = False) -> list[dict]:
    """Return JSON-ready records for each project in a workspace.

    Each record has ``project`` and ``file`` (relative to the workspace). Valid
    projects also carry ``tasks`` — top-level (``level == 2``) tasks, TODO-only
    unless ``include_all``. Projects whose Org file is unreadable, lacks a
    ``* Tasks`` section, or has duplicate IDs carry a ``warning`` and empty
    ``tasks`` instead of raising.
    """
    records: list[dict] = []

    for project in discover_projects(workspace):
        rel_file = project.org_file.relative_to(workspace)
        record: dict = {"project": project.name, "file": str(rel_file)}

        try:
            text = project.org_file.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001 — surface any read failure as a warning
            record["warning"] = f"Could not read file: {e}"
            record["tasks"] = []
            records.append(record)
            continue

        lines = text.splitlines()
        if not any(core.TASKS_HEADING_RE.match(line) for line in lines):
            record["warning"] = "no parseable * Tasks section found"
            record["tasks"] = []
            records.append(record)
            continue

        tasks = core.parse_org(text)

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

        record["tasks"] = [
            {"id": t.id, "state": t.state, "title": t.text}
            for t in tasks
            if t.level == 2 and (include_all or t.state == "TODO")
        ]
        records.append(record)

    return records
