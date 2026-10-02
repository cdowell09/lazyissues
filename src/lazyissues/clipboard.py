"""Copying: the system clipboard, and the Copy key screens offer.

The app copies with OSC 52 and with the system clipboard, so a copy works whether or not
the terminal honours OSC 52 (macOS Terminal doesn't).
"""

import subprocess
import sys
from collections.abc import Callable

from textual.binding import Binding

# Copies the selected text. The app shows it, and so makes it clickable, only while text is
# selected; otherwise ctrl+c is left to whatever else binds it, such as an input copying its
# own selection. The app's footer lists it; a modal screen lists it in its own BINDINGS.
COPY = Binding("ctrl+c", "app.copy_selection", "Copy", priority=True)

# Runs a command with `data` on its stdin; raises OSError or SubprocessError on failure.
Runner = Callable[[list[str], bytes], None]


def _commands(platform: str) -> list[tuple[list[str], str]]:
    """Clipboard commands to try in order, each with the encoding it reads."""
    if platform == "darwin":
        return [(["pbcopy"], "utf-8")]
    if platform == "win32":
        return [(["clip.exe"], "utf-16")]  # with a byte order mark, so clip reads Unicode
    return [(["wl-copy"], "utf-8"), (["xclip", "-selection", "clipboard"], "utf-8")]


def _run(command: list[str], data: bytes) -> None:
    if "pytest" in sys.modules:
        raise RuntimeError("Tests must not touch the clipboard; pass a fake runner.")
    # No output reaches the terminal the app is drawing on.
    subprocess.run(
        command,
        input=data,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
        timeout=2,
    )


def copy(text: str, platform: str = sys.platform, run: Runner = _run) -> None:
    """Put `text` on the clipboard with the first of `platform`'s commands that works.

    Does nothing when none does: OSC 52 may still have copied it.
    """
    for command, encoding in _commands(platform):
        try:
            run(command, text.encode(encoding))
        except (OSError, subprocess.SubprocessError):
            continue
        return
