"""Loading and saving `config.toml` in the platform config directory."""

import os
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

import platformdirs
import tomlkit
from tomlkit.exceptions import TOMLKitError
from tomlkit.items import AoT


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Repo:
    name: str  # "owner/name"
    status_source: Literal["labels", "project"] = "labels"
    project: str | None = None  # "owner/number" when status_source is "project"


@dataclass(frozen=True)
class Status:
    name: str
    active: bool = False  # moving here assigns the current user if unassigned
    key: str | None = None  # move shortcut


@dataclass(frozen=True)
class SavedFilter:
    name: str
    query: str  # GitHub issue search syntax


@dataclass(frozen=True)
class Config:
    repos: list[Repo]
    statuses: list[Status]
    team: list[str] = field(default_factory=list)  # GitHub logins in the team roster
    filters: list[SavedFilter] = field(default_factory=list)
    done_window_days: int = 14  # how far back shown done issues reach
    # "owner/repo/title" of the milestones the Milestones tab shows, in order; empty: all.
    pinned_milestones: list[str] = field(default_factory=list)

    @property
    def repo_names(self) -> list[str]:
        return [repo.name for repo in self.repos]


def config_dir() -> Path:
    if sys.platform == "win32":
        return Path(platformdirs.user_config_dir("lazyissues", appauthor=False, roaming=True))
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "lazyissues"


def is_repo_name(name: str) -> bool:
    """Whether `name` has the `owner/name` shape of a repo in the repo set."""
    return name.count("/") == 1 and all(name.split("/"))


def load(path: Path) -> Config:
    try:
        doc = tomlkit.parse(path.read_text(encoding="utf-8")).unwrap()
    except FileNotFoundError:
        raise ConfigError(f"No config at {path}. Run lazyissues to set one up.") from None
    except TOMLKitError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from None

    repos = doc.get("repos")
    if not isinstance(repos, list) or not repos:
        raise ConfigError(f"{path} needs at least one `[[repos]]` entry with a `name`.")
    team = doc.get("team", [])
    if not isinstance(team, list) or not all(isinstance(login, str) for login in team):
        raise ConfigError(f'{path}: `team` must be a list of GitHub logins, like `team = ["me"]`.')
    done_window_days = doc.get("done_window_days", Config.done_window_days)
    if type(done_window_days) is not int or done_window_days < 1:
        raise ConfigError(f"{path}: `done_window_days` must be a whole number of days, 1 or more.")
    pinned = doc.get("pinned_milestones", [])
    if not isinstance(pinned, list) or not all(map(_is_milestone_key, pinned)):
        raise ConfigError(
            f'{path}: `pinned_milestones` must list milestones as "owner/repo/title",'
            ' like `pinned_milestones = ["octo/app/v1.0"]`.'
        )
    return Config(
        repos=[_repo(path, entry) for entry in repos],
        statuses=[_status(path, entry) for entry in doc.get("statuses", [])],
        team=team,
        filters=[_filter(path, entry) for entry in doc.get("filters", [])],
        done_window_days=done_window_days,
        pinned_milestones=pinned,
    )


def _is_milestone_key(key: Any) -> bool:
    """`owner/repo/title`; the title may itself hold `/`."""
    parts = key.split("/", 2) if isinstance(key, str) else []
    return len(parts) == 3 and all(parts)


def _repo(path: Path, entry: Any) -> Repo:
    name = entry.get("name") if isinstance(entry, dict) else None
    if not isinstance(name, str) or not is_repo_name(name):
        raise ConfigError(f'{path}: each `[[repos]]` needs `name = "owner/repo"`.')
    source = entry.get("status_source", "labels")
    project = entry.get("project")
    if source == "labels":
        return Repo(name)
    if source == "project" and isinstance(project, str) and project.count("/") == 1:
        return Repo(name, "project", project)
    raise ConfigError(
        f'{path}: {name} needs `status_source = "labels"`, or `status_source = "project"`'
        ' with `project = "owner/number"`.'
    )


def _status(path: Path, entry: Any) -> Status:
    name = entry.get("name") if isinstance(entry, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"{path}: each `[[statuses]]` needs a `name`.")
    return Status(name, active=bool(entry.get("active", False)), key=entry.get("key"))


def _filter(path: Path, entry: Any) -> SavedFilter:
    entry = entry if isinstance(entry, dict) else {}
    name, query = entry.get("name"), entry.get("query")
    if not (isinstance(name, str) and name.strip() and isinstance(query, str) and query.strip()):
        raise ConfigError(f"{path}: each `[[filters]]` needs a `name` and a `query`.")
    return SavedFilter(name, query)


def save(config: Config, path: Path) -> None:
    """Write `config` to `path`, keeping the comments of the file it replaces."""
    try:
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        doc = tomlkit.document()
        doc.add(tomlkit.comment("lazyissues config. Comments you add here are kept."))
    except TOMLKitError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from None
    _set_list(doc, "team", config.team)
    _set_list(doc, "pinned_milestones", config.pinned_milestones)
    _set_tables(doc, "repos", config.repos)
    _set_tables(doc, "statuses", config.statuses)
    _set_tables(doc, "filters", config.filters)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".tmp")  # a failed write never truncates the config
    try:
        partial.write_text(tomlkit.dumps(doc), encoding="utf-8")
        partial.replace(path)
    except OSError:
        partial.unlink(missing_ok=True)
        raise


def _set_list(doc: tomlkit.TOMLDocument, key: str, values: list[str]) -> None:
    """Set a top-level list, left out when empty; an unchanged one keeps its formatting."""
    if not values:
        doc.pop(key, None)
    elif doc.get(key) != values:
        doc[key] = values


def _set_tables(doc: tomlkit.TOMLDocument, key: str, records: list[Any]) -> None:
    """Rewrite the `[[key]]` tables, reusing each record's old table, matched by name,
    so the comments inside it stay with it."""
    if not records:
        doc.pop(key, None)
        return
    tables = doc.get(key)
    if not isinstance(tables, AoT):
        doc.pop(key, None)
        tables = tomlkit.aot()
        doc.add(tomlkit.nl())
        doc.add(key, tables)
    old = {table.get("name"): table for table in tables}
    del tables[:]
    for record in records:
        table = old.pop(record.name, None)
        if table is None:
            table = tomlkit.table()
        # Fields at their default are left out; keys this schema doesn't know are kept.
        for f in fields(record):
            value = getattr(record, f.name)
            if value == f.default:
                table.pop(f.name, None)
            elif table.get(f.name) != value:
                table[f.name] = value
        tables.append(table)
