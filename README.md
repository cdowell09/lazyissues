# lazyissues

A fast terminal UI for GitHub Issues, with keyboard and mouse controls for daily triage across your repos and keeping an eye on your team's work.

> **Status:** early development. Nothing is installable yet. The design lives in [CONTEXT.md](CONTEXT.md) (the domain language) and [docs/adr/](docs/adr/) (the decisions behind it).

lazyissues is the GitHub counterpart of [lazyjira](https://github.com/cdowellmdb/lazyjira). It uses your existing `gh` login, opens instantly from a local cache, and refreshes in the background.

## Planned features

Five tabs: **My Work**, **Team**, **Milestones**, **Unassigned**, and **Filters** (saved GitHub searches).

- Track a set of repositories together, with issues shown as `repo#123`
- Statuses you define, read from labels or a GitHub Project's Status field, chosen per repo
- Move, close (completed, not planned, duplicate), reopen, comment, assign, create and edit issues without leaving the terminal
- Moves change an issue only after GitHub confirms them
- Bulk move and assign, skipping issues that can't make the change and listing why
- Sub-issues nested under their parent; milestone progress bars
- Markdown rendering, activity timeline, `$EDITOR` support
- Keyboard or mouse, including drag-to-select and copy
- macOS, Linux and Windows

## Requirements

- Python 3.12+
- The [GitHub CLI](https://cli.github.com/) logged in with the `project` scope: `gh auth refresh -s project`

## Install

lazyissues is not on PyPI yet; these commands work once the first release ships.

```bash
uv tool install lazyissues
# or
pipx install lazyissues
```

Until then, run it from a checkout with `uv run lazyissues`. Maintainers cut releases with [docs/RELEASING.md](docs/RELEASING.md).

## License

[MIT](LICENSE)
