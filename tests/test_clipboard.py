"""The system clipboard fallback, per platform, with a fake command runner."""

import subprocess

import pytest

from lazyissues import clipboard


class Runner:
    """Records commands; the ones in `missing` aren't installed, those in `failing` exit 1."""

    def __init__(self, missing: tuple[str, ...] = (), failing: tuple[str, ...] = ()) -> None:
        self.missing = missing
        self.failing = failing
        self.ran: list[tuple[list[str], bytes]] = []

    def __call__(self, command: list[str], data: bytes) -> None:
        if command[0] in self.missing:
            raise FileNotFoundError(command[0])
        if command[0] in self.failing:
            raise subprocess.CalledProcessError(1, command)
        self.ran.append((command, data))


def test_macos_copies_with_pbcopy():
    run = Runner()
    clipboard.copy("lanternfish#4 ✓", "darwin", run)
    assert run.ran == [(["pbcopy"], "lanternfish#4 ✓".encode())]


def test_windows_copies_with_clip_as_utf16():
    run = Runner()
    clipboard.copy("lanternfish#4 ✓", "win32", run)
    assert run.ran == [(["clip.exe"], "lanternfish#4 ✓".encode("utf-16"))]


def test_linux_prefers_wl_copy():
    run = Runner()
    clipboard.copy("text", "linux", run)
    assert run.ran == [(["wl-copy"], b"text")]


@pytest.mark.parametrize(
    "runner",
    [Runner(missing=("wl-copy",)), Runner(failing=("wl-copy",))],
    ids=["wl-copy missing", "wl-copy failing, as outside Wayland"],
)
def test_linux_falls_back_to_xclip(runner: Runner):
    clipboard.copy("text", "linux", runner)
    assert runner.ran == [(["xclip", "-selection", "clipboard"], b"text")]


def test_no_clipboard_command_is_not_an_error():
    run = Runner(missing=("wl-copy", "xclip"))
    clipboard.copy("text", "linux", run)
    assert run.ran == []


def test_tests_cannot_touch_the_real_clipboard():
    with pytest.raises(RuntimeError):
        clipboard.copy("text", "darwin")
