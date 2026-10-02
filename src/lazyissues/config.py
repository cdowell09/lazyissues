"""Loading `config.toml` from the platform config directory."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import platformdirs
import tomlkit
from tomlkit.exceptions import TOMLKitError


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
class Config:
    repos: list[Repo]
    statuses: list[Status]

    @property
    def repo_names(self) -> list[str]:
        return [repo.name for repo in self.repos]


def config_dir() -> Path:
    if sys.platform == "win32":
        return Path(platformdirs.user_config_dir("lazyissues", appauthor=False, roaming=True))
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "lazyissues"


def load(path: Path) -> Config:
    try:
        doc = tomlkit.parse(path.read_text(encoding="utf-8")).unwrap()
    except FileNotFoundError:
        raise ConfigError(f"No config at {path}. Create it with a `[[repos]]` entry.") from None
    except TOMLKitError as e:
        raise ConfigError(f"{path} is not valid TOML: {e}") from None

    repos = doc.get("repos")
    if not isinstance(repos, list) or not repos:
        raise ConfigError(f"{path} needs at least one `[[repos]]` entry with a `name`.")
    return Config(
        repos=[_repo(path, entry) for entry in repos],
        statuses=[_status(path, entry) for entry in doc.get("statuses", [])],
    )


def _repo(path: Path, entry: Any) -> Repo:
    name = entry.get("name") if isinstance(entry, dict) else None
    if not isinstance(name, str) or name.count("/") != 1:
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
