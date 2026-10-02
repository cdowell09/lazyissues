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

| Key | Where | Action |
| --- | --- | --- |
| `↑` `↓` | lists | select an issue |
| `Enter` | lists | open the issue detail |
| `←` `→` | detail | previous or next issue in the list underneath |
| `z` | detail | toggle full screen |
| `o` | detail | open the issue in your browser |
| `h` | detail | toggle the activity timeline |
| `Esc` | detail | close |
| `r` | lists | refresh from GitHub |
| `q` | anywhere | quit |

The detail shows the issue's fields, its Markdown body, comments oldest first, project fields such as Theme, and its parent and sub-issues. It shows the copy from earlier in the session at once and fetches the latest from GitHub every time it opens.

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

## Setup

The first time you run `lazyissues` there is no config yet, so it starts setup. Setup needs `gh` installed and logged in (`gh auth login`); without it, lazyissues prints what to run and exits.

1. **Repos.** Setup suggests the repos where you have open issues. Uncheck any you don't want, and type `owner/name` and Enter to add others, including org repos. A repo GitHub won't read (an org that enforces SAML, say) is left out with the reason; add it again to retry. If GitHub can't be reached at all, press `r` to try again.
2. **Status sources.** A repo linked to exactly one project uses that project's Status field; any other repo uses labels. Change either with the repo's dropdown. For a label-backed repo, check the labels that are statuses; none are checked for you, so `bug` never becomes a status by accident.
3. **Statuses.** The statuses from every repo, merged by name, with project options that mean done (Done, Closed, Complete) left out because closing an issue is how it gets there. Reorder them with `shift+↑`/`shift+↓` or the buttons, and check the active ones ("In Progress" starts checked). An empty list is fine; you can add statuses later.
4. **Save** (`ctrl+s`) writes `config.toml` and opens My Work. `esc` goes back a step, and `ctrl+q` quits without saving.

If your `gh` token lacks the `project` scope, setup says so and offers labels only. Run `gh auth refresh -s project`, then start lazyissues again to use project boards.

Setup also puts you on the team roster and adds two saved filters, "Ready for me" (`label:ready-for-human`) and "Needs triage" (`label:needs-triage`). To run setup again, delete or rename `config.toml`.

## Configuration

`config.toml` lives in `~/.config/lazyissues/` (or `$XDG_CONFIG_HOME/lazyissues/`) on macOS and Linux, and `%APPDATA%\lazyissues\` on Windows. lazyissues rewrites it in place when it saves, keeping your comments, so edit it while the app isn't running.

```toml
team = ["octo-dev", "sam-reef"]     # GitHub logins in the Team tab

[[repos]]                           # at least one
name = "octo-dev/tidepool"
status_source = "labels"            # the default

[[repos]]
name = "octo-dev/lanternfish"
status_source = "project"
project = "octo-dev/3"              # owner/number of the project

[[statuses]]                        # display order; may be empty
name = "In Progress"
active = true                       # moving here assigns you if nobody is
key = "p"                           # move shortcut

[[filters]]                         # saved GitHub issue searches
name = "Ready for me"
query = "label:ready-for-human"
```

An invalid file stops lazyissues before the TUI starts, with a message naming the problem.

## License

[MIT](https://github.com/cdowell09/lazyissues/blob/main/LICENSE)
