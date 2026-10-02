import subprocess
import sys
from pathlib import Path

import pytest

from lazyissues import editor


def fake_editor(tmp_path: Path, code: str) -> str:
    """A command that runs `code` on the file it's given, as `$EDITOR` would."""
    script = tmp_path / "fake_editor.py"
    script.write_text(f"import sys, pathlib\npath = pathlib.Path(sys.argv[1])\n{code}\n")
    return subprocess.list2cmdline([sys.executable, str(script)])


@pytest.mark.parametrize(
    ("env", "platform", "expected"),
    [
        ({"VISUAL": "code --wait", "EDITOR": "nano"}, "linux", "code --wait"),
        ({"EDITOR": "nano"}, "darwin", "nano"),
        ({}, "win32", "notepad"),
        ({}, "linux", "vi"),
    ],
)
def test_command_prefers_visual_then_editor_then_the_platform_default(
    monkeypatch, env, platform, expected
):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "platform", platform)
    assert editor.command() == expected


def test_edit_round_trips_the_text_through_the_editor(tmp_path):
    command = fake_editor(
        tmp_path, "path.write_text(path.read_text().upper() + '\\nmore\\n', encoding='utf-8')"
    )
    # The trailing newline editors add is dropped; the user's own lines are kept.
    assert editor.edit("first line\nsecond", command) == "FIRST LINE\nSECOND\nmore"


def test_edit_keeps_unicode(tmp_path):
    command = fake_editor(tmp_path, "pass")
    assert editor.edit("naïve — ✓", command) == "naïve — ✓"


def test_a_failing_editor_raises(tmp_path):
    command = fake_editor(tmp_path, "sys.exit(3)")
    with pytest.raises(editor.EditorError, match="exited with 3"):
        editor.edit("text", command)
