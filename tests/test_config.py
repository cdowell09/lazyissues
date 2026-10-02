import pytest

from lazyissues.config import Config, ConfigError, config_dir, load


def test_loads_repo_set(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('# mine\n[[repos]]\nname = "a/x"\n\n[[repos]]\nname = "a/y"\n')
    assert load(path) == Config(repos=["a/x", "a/y"])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (None, "No config"),
        ("repos = [", "not valid TOML"),
        ("", "at least one"),
        ('[[repos]]\nname = "nope"\n', "owner/repo"),
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
