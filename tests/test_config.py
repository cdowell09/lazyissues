import pytest

from lazyissues.config import (
    Config,
    ConfigError,
    Repo,
    SavedFilter,
    Status,
    config_dir,
    load,
    save,
)


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


def test_loads_the_done_window_which_is_two_weeks_by_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[[repos]]\nname = "a/x"\n')
    assert load(path).done_window_days == 14
    path.write_text('done_window_days = 7\n[[repos]]\nname = "a/x"\n')
    assert load(path).done_window_days == 7


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (None, "No config"),
        ("repos = [", "not valid TOML"),
        ("", "at least one"),
        ('[[repos]]\nname = "nope"\n', "owner/repo"),
        ('[[repos]]\nname = "a/x"\nstatus_source = "project"\n', "owner/number"),
        ('[[repos]]\nname = "a/x"\n[[statuses]]\nactive = true\n', "needs a `name`"),
        ('done_window_days = 0\n[[repos]]\nname = "a/x"\n', "whole number of days"),
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


def test_loads_team_and_saved_filters(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """team = ["me", "sam"]

[[repos]]
name = "a/x"

[[filters]]
name = "Ready for me"
query = "label:ready-for-human"
"""
    )
    config = load(path)
    assert config.team == ["me", "sam"]
    assert config.filters == [SavedFilter("Ready for me", "label:ready-for-human")]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('team = "me"\n[[repos]]\nname = "a/x"\n', "`team`"),
        ('[[repos]]\nname = "a/x"\n[[filters]]\nname = "x"\n', "`query`"),
    ],
)
def test_reports_bad_team_or_filters(tmp_path, text, message):
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(ConfigError, match=message):
        load(path)


CONFIG = Config(
    repos=[Repo("a/x"), Repo("a/y", "project", "a/1")],
    statuses=[Status("Todo"), Status("In Progress", active=True, key="p")],
    team=["me"],
    filters=[SavedFilter("Needs triage", "label:needs-triage")],
)


def test_save_writes_a_config_that_loads_back(tmp_path):
    path = tmp_path / "new" / "config.toml"
    save(CONFIG, path)
    assert load(path) == CONFIG


def test_save_accepts_an_empty_status_list(tmp_path):
    path = tmp_path / "config.toml"
    config = Config(repos=[Repo("a/x")], statuses=[], team=["me"])
    save(config, path)
    assert load(path) == config


def test_resaving_keeps_the_users_comments(tmp_path):
    path = tmp_path / "config.toml"
    save(CONFIG, path)
    text = path.read_text()
    text = "# my lazyissues setup\n" + text.replace(
        '[[repos]]\nname = "a/x"', '[[repos]]\n# my side project\nname = "a/x"'
    )
    path.write_text(text)

    changed = Config(
        repos=[Repo("a/x", "project", "a/2")],
        statuses=[Status("In Progress", active=True)],
        team=["me", "sam"],
        filters=[],
    )
    save(changed, path)
    save(changed, path)

    assert load(path) == changed
    text = path.read_text()
    assert text.startswith("# my lazyissues setup\n")
    assert "# my side project\n" in text


def test_a_records_comments_follow_it_when_earlier_records_go(tmp_path):
    path = tmp_path / "config.toml"
    save(CONFIG, path)
    path.write_text(path.read_text().replace('name = "a/y"', '# work board\nname = "a/y"'))

    save(Config(repos=[CONFIG.repos[1], Repo("a/new")], statuses=[]), path)

    assert '# work board\nname = "a/y"' in path.read_text()
    assert load(path).repo_names == ["a/y", "a/new"]
