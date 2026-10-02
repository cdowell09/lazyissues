"""Round-tripping text through the user's own editor."""

import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path


class EditorError(Exception):
    pass


def command() -> str:
    """`$VISUAL`, then `$EDITOR`, then the platform's default editor."""
    default = "notepad" if sys.platform == "win32" else "vi"
    return os.environ.get("VISUAL") or os.environ.get("EDITOR") or default


def edit(text: str, editor: str | None = None) -> str:
    """Open `text` in `editor` (default: `command()`) and return what was saved.

    The command runs in the shell, like git runs `$EDITOR`, so `code --wait` works.
    """
    editor = editor or command()
    # An editor still holding the file (Windows) mustn't fail the edit on cleanup.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        path = Path(directory) / "lazyissues.md"
        path.write_text(text, encoding="utf-8")
        if sys.platform == "win32":
            quoted = subprocess.list2cmdline([str(path)])
        else:
            quoted = shlex.quote(str(path))
        result = subprocess.run(f"{editor} {quoted}", shell=True)
        if result.returncode != 0:
            raise EditorError(f"{editor} exited with {result.returncode}.")
        # Notepad may add a BOM; text in another encoding comes back with replacements
        # rather than failing.
        edited = path.read_text(encoding="utf-8-sig", errors="replace")
    # Most editors end the file with a newline the text didn't have.
    return edited.removesuffix("\n") if not text.endswith("\n") else edited
