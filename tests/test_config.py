import pytest

from lazyissues.config import Config, ConfigError, Repo, Status, config_dir, load


def test_loads_repos_and_statuses(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """# mine
[[repos]]
name = "a/x"

[[repos]]
name = "a/y"
status_source = "project"
project = "a/1"

[[statuses]]
name = "Todo"

[[statuses]]
name = "In Progress"
active = true
key = "p"
"""
    )
    assert load(path) == Config(
        repos=[Repo("a/x"), Repo("a/y", "project", "a/1")],
        statuses=[Status("Todo"), Status("In Progress", active=True, key="p")],
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (None, "No config"),
        ("repos = [", "not valid TOML"),
        ("", "at least one"),
        ('[[repos]]\nname = "nope"\n', "owner/repo"),
        ('[[repos]]\nname = "a/x"\nstatus_source = "project"\n', "owner/number"),
        ('[[repos]]\nname = "a/x"\n[[statuses]]\nactive = true\n', "needs a `name`"),
    ],
)
def test_reports_bad_config(tmp_path, text, message):
    path = tmp_path / "config.toml"
    if text is not None:
        path.write_text(text)
    with pytest.raises(ConfigError, match=message):
        load(path)


def test_config_dir_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert config_dir() == tmp_path / "lazyissues"
