# lazyissues

A fast terminal UI for GitHub Issues, with keyboard and mouse controls for daily triage across your repos and keeping an eye on your team's work.

> **Status:** early development. Nothing is installable yet. The design lives in [CONTEXT.md](https://github.com/cdowell09/lazyissues/blob/main/CONTEXT.md) (the domain language) and [docs/adr/](https://github.com/cdowell09/lazyissues/tree/main/docs/adr) (the decisions behind it).

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

## Statuses

My Work groups your open issues by status. You list the statuses in `config.toml`, in the order you want the groups shown, and each repo says where its statuses live: status labels, or one GitHub Project's Status field.

```toml
[[repos]]
name = "octo-dev/tidepool"          # labels such as `todo` or `in-progress`

[[repos]]
name = "octo-dev/lanternfish"
status_source = "project"
project = "octo-dev/3"              # the project's owner/number

[[statuses]]
name = "Todo"

[[statuses]]
name = "In Progress"
active = true
```

Names match ignoring case and treating `-`, `_` and spaces alike, so an `in-progress` label and a project's "In Progress" option share one group. Issues with no status come first under "No status", and statuses not in your list come after the ones that are. An issue with two status labels shows under the later one, marked ⚠.

## Keys

| Key | Action |
| --- | --- |
| `r` | Refresh from GitHub |
| `q` | Quit |

## Cache

The last loaded issues are saved in your platform's cache directory (`~/Library/Caches/lazyissues` on macOS, `~/.cache/lazyissues` on Linux, `%LOCALAPPDATA%\lazyissues\Cache` on Windows), one snapshot per repo set. lazyissues shows it at startup while it refreshes. Deleting it is always safe.

## Install

lazyissues is not on PyPI yet; these commands work once the first release ships.

```bash
uv tool install lazyissues
# or
pipx install lazyissues
```

Until then, run it from a checkout with `uv run lazyissues`. Maintainers cut releases with [docs/RELEASING.md](https://github.com/cdowell09/lazyissues/blob/main/docs/RELEASING.md).

## License

[MIT](https://github.com/cdowell09/lazyissues/blob/main/LICENSE)
