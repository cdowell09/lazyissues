from dataclasses import replace

import pytest

from lazyissues.config import (
    Config,
    ConfigError,
    Preferences,
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


def test_loads_pinned_milestones_in_order(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'pinned_milestones = ["a/y/v1.0", "a/x/Big / launch"]\n[[repos]]\nname = "a/x"\n'
    )
    assert load(path).pinned_milestones == ["a/y/v1.0", "a/x/Big / launch"]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('team = "me"\n[[repos]]\nname = "a/x"\n', "`team`"),
        ('[[repos]]\nname = "a/x"\n[[filters]]\nname = "x"\n', "`query`"),
        ('pinned_milestones = ["v1"]\n[[repos]]\nname = "a/x"\n', "owner/repo/title"),
        ('pinned_milestones = "a/x/v1"\n[[repos]]\nname = "a/x"\n', "owner/repo/title"),
    ],
)
def test_reports_bad_team_filters_or_pinned_milestones(tmp_path, text, message):
    path = tmp_path / "config.toml"
    path.write_text(text)
    with pytest.raises(ConfigError, match=message):
        load(path)


CONFIG = Config(
    repos=[Repo("a/x"), Repo("a/y", "project", "a/1")],
    statuses=[Status("Todo"), Status("In Progress", active=True, key="p")],
    team=["me"],
    filters=[SavedFilter("Needs triage", "label:needs-triage")],
    pinned_milestones=["a/y/v1.0"],
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


def test_pinning_milestones_in_a_saved_config_then_unpinning_them(tmp_path):
    path = tmp_path / "config.toml"
    unpinned = Config(repos=[Repo("a/x")], statuses=[Status("Todo")], team=["me"])
    save(unpinned, path)

    pinned = Config(unpinned.repos, unpinned.statuses, ["me"], pinned_milestones=["a/x/v1"])
    save(pinned, path)
    assert load(path) == pinned

    save(unpinned, path)
    assert "pinned_milestones" not in path.read_text()


def test_loads_preferences_with_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[[repos]]\nname = "a/x"\n')
    assert load(path).preferences == Preferences(
        show_done=False, start_tab="My Work", theme="textual-dark"
    )
    path.write_text(
        """[preferences]
show_done = true
start_tab = "Team"
theme = "nord"

[[repos]]
name = "a/x"
"""
    )
    config = load(path)
    assert config.preferences == Preferences(show_done=True, start_tab="Team", theme="nord")


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("preferences = 1\n", "`preferences`"),
        ("[preferences]\nshow_done = 1\n", "`show_done`"),
        ("[preferences]\nstart_tab = 2\n", "`start_tab`"),
        ('[preferences]\ntheme = "no-such-theme"\n', "`theme`"),
    ],
)
def test_reports_bad_preferences(tmp_path, text, message):
    path = tmp_path / "config.toml"
    path.write_text(text + '[[repos]]\nname = "a/x"\n')
    with pytest.raises(ConfigError, match=message):
        load(path)


def test_saves_preferences_keeping_comments(tmp_path):
    path = tmp_path / "config.toml"
    save(CONFIG, path)
    assert "preferences" not in path.read_text()  # defaults are left out

    changed = replace(CONFIG, preferences=Preferences(show_done=True, theme="nord"))
    save(changed, path)
    assert load(path) == changed
    path.write_text(path.read_text().replace("show_done", "# lists start with done\nshow_done"))

    changed = replace(changed, preferences=Preferences(show_done=True, start_tab="Team"))
    save(changed, path)
    assert load(path) == changed
    assert "# lists start with done\nshow_done = true" in path.read_text()
    assert "theme" not in path.read_text()

    save(CONFIG, path)
    assert load(path) == CONFIG
    assert "preferences" not in path.read_text()
