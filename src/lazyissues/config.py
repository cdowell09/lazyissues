"""Loading `config.toml` from the platform config directory."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import platformdirs
import tomlkit
from tomlkit.exceptions import TOMLKitError


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    repos: list[str]


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
    names = []
    for entry in repos:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str) or name.count("/") != 1:
            raise ConfigError(f'{path}: each `[[repos]]` needs `name = "owner/repo"`.')
        names.append(name)
    return Config(repos=names)
