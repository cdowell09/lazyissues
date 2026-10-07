# lazyissues

A fast terminal UI for GitHub Issues, with keyboard and mouse controls for daily triage across your repos and keeping an eye on your team's work.

> **Status:** early development. The design lives in [CONTEXT.md](https://github.com/cdowell09/lazyissues/blob/main/CONTEXT.md) (the domain language) and [docs/adr/](https://github.com/cdowell09/lazyissues/tree/main/docs/adr) (the decisions behind it).

lazyissues uses your existing `gh` login, opens instantly from a local cache, and refreshes in the background.

## Planned features

Five tabs: **My Work**, **Team**, **Milestones**, **Unassigned**, and **Filters** (saved GitHub searches).

- Track a set of repositories together, with issues shown as `repo#123`
- Statuses you define, read from labels or a GitHub Project's Status field, chosen per repo
- Move, close (completed, not planned, duplicate), reopen, comment, assign, create and edit issues without leaving the terminal
- Moves change an issue only after GitHub confirms them
- Bulk move and assign, skipping issues that can't make the change and listing why
- Sub-issues nested under their parent; milestone progress bars
- Markdown rendering, activity timeline, `$EDITOR` support for comments and bodies
- Keyboard or mouse, including drag-to-select and copy
- macOS, Linux and Windows

## Requirements

- Python 3.12+
- The [GitHub CLI](https://cli.github.com/) logged in with the `project` scope: `gh auth refresh -s project`

## Tabs

- **My Work**: your open issues, by status
- **Team**: open issues by assignee, for everyone in `team` plus you, the busiest (most issues in an active status) first; within a person, active issues come first and done ones last
- **Milestones**: each repo's open milestones as `repo / title`, with a bar of done (closed) issues out of all of them; under each, its open issues by status. Same-named milestones in different repos stay separate. Set `pinned_milestones` to show only those, in that order
- **Unassigned**: open issues with no assignee, by status
- **Filters**: your saved filters in a sidebar; `Enter` runs one and lists its issues by status, with the same search, focus, done, folding and detail keys as every list

## Saved filters

A saved filter is a name and a query in [GitHub's issue search syntax](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/filtering-and-searching-issues-and-pull-requests), such as `label:bug assignee:@me`. lazyissues limits it to your repos unless the query names its own `repo:`, `org:` or `user:`, and lists open issues unless it names its own `is:open`, `is:closed` or `state:` (closed results show with `d`). If GitHub rejects a query, lazyissues shows GitHub's message and carries on.

In the sidebar, `n` adds a filter, `e` edits the highlighted one and `x` deletes it after you confirm; each change is saved to `config.toml` at once, keeping your comments. Each filter's results keep their own snapshot, search and folds.

In every tab, issues are indented under their group's header, most recently updated first, and the Updated column says how long ago each one changed (`5m`, `3h`, `2d`). A sub-issue whose parent is in the same group hangs under it on a tree line (`├ lanternfish#7`, `└ lanternfish#11`); otherwise its row leads with the parent, dimmed (`lanternfish#9 → lanternfish#7`). A parent's `▾`/`▸` sits in a column of its own, so every issue number lines up. `z` on a parent or one of its sub-issues folds the parent's sub-issues; the group's count still includes them.

Lists fit the terminal and refit when you resize it: Title takes the room the other columns leave, and a title cut short ends in `…`, so Status, Assignees, Labels and Updated stay on screen even at 80 columns. On a terminal narrower than 100 columns, the detail opens full screen.

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

Names match ignoring case and treating `-`, `_` and spaces alike, so an `in-progress` label and a project's "In Progress" option share one group. Issues with no status come first under "No status", and statuses not in your list come after the ones that are. An issue with two status labels shows under the later one, marked ⚠. Closed issues, shown with `d`, form a Done group at the end.

## Keys

| Key | Where | Action |
| --- | --- | --- |
| `↑` `↓` | lists | select an issue |
| `Enter` | lists | open the issue detail |
| `/` | lists | search by number, title, assignee or label (and team member in Team); the list filters after a short pause in typing; `Enter` keeps it, `Esc` clears it |
| `f` `F` | lists | focus the next or previous status, then back to all |
| `d` | lists | show or hide done issues, loading those closed within `done_window_days` |
| `z` `Z` | lists | fold the sub-issues of the parent under the cursor (or of the sub-issue's parent), else the group; or fold and unfold every group |
| `R` | lists | show one repo at a time, then all |
| `m` | lists, detail | move the issue: pick a status, a close reason, or reopen |
| a status's `key` | lists, detail | open the move picker on that status; the uppercase key moves at once |
| `C` | lists, detail | comment on the issue |
| `a` | lists, detail | assign: pick from the team roster and the repo's assignable users |
| `c` | lists, detail | create an issue |
| `e` | lists, detail | edit the issue's title, body, labels and milestone |
| `o` | lists, detail | open the issue in your browser |
| `y` | lists, detail | copy the issue's URL, through your terminal and the system clipboard (see [Mouse](#mouse)) |
| `Space` | lists | select the issue for a bulk action, or on a group header the whole group; again to unselect |
| `A` `u` | lists | select every issue shown; clear the selection |
| `B` | lists | bulk move or assign the selected issues |
| `←` `→` | detail | previous or next issue in the list underneath |
| `w` | detail | toggle full screen |
| `h` | detail | toggle the activity timeline |
| `Esc` | detail | close |
| `r` | lists | refresh the tab from GitHub |
| `Enter` | Filters sidebar | run the highlighted filter |
| `Tab` `Shift+Tab` | Filters | move between the sidebar and the results |
| `n` `e` `x` | Filters sidebar | new filter, edit or delete the highlighted one |
| `1`–`5` | anywhere | go to a tab and its list, Filters included |
| `[` `]` | anywhere | go to the previous or next tab |
| `S` | anywhere | preferences |
| `?` | anywhere | list every key |
| `Ctrl+C` | anywhere | copy the selected text |
| `q` | anywhere | quit |

The footer shows the keys you reach for most, so it fits an 80-column terminal; `?` lists every key.

The detail shows the issue's fields, its Markdown body, comments oldest first, project fields such as Theme, and its parent and sub-issues. It shows the copy from earlier in the session at once and fetches the latest from GitHub every time it opens.

Each tab remembers its selection, search, status focus, done toggle, folds and repo filter while the app runs.

## Mouse

Click a tab or row to select it, and click the selected row again to open it. Click a group header, or a parent's `▸`/`▾`, to fold or unfold it. Click a row's `☐` to select it for a bulk action, or a group header's to select the group. Menus work the same way: in the move picker and the Filters sidebar, click an option to choose it and click it again to take it. Checkboxes, fields and buttons in forms, preferences and setup are clickable. Click **[×]** at the top of the issue detail to close it. The wheel scrolls lists, the detail, help and editors.

Drag across text in a list, the detail or a form field to select it. Press `Ctrl+C`, or click **Copy** in the footer while text is selected, to copy it. lazyissues copies through your terminal (OSC 52) and also through the system clipboard, so copying works in terminals that ignore OSC 52, such as macOS Terminal: `pbcopy` on macOS, `wl-copy` or `xclip` on Linux (install one), and `clip.exe` on Windows.

## Moves

`m` lists the moves the issue can make: every status in your list for a label-backed repo, or the project's Status options for a project-backed one (leaving out options that mean done), then Close as completed, Close as not planned and Close as duplicate of… (which asks for the original as `12`, `repo#12`, `owner/repo#12` or a URL), or Reopen for a closed issue. A status with a `key` in `config.toml` has a shortcut: the key opens the picker on that status, and the uppercase key moves at once. Built-in keys keep their meaning, so give statuses keys the lists, the detail and the tab keys don't already use.

- In a label-backed repo, a move adds the status's label (creating it in the repo if it has none) and removes every other status label.
- In a project-backed repo, a move adds the issue to the project if it isn't on it, then sets its Status.
- Moving an unassigned issue to an active status assigns you.
- Moving a closed issue to a status reopens it.

The issue keeps its status until GitHub confirms the move; the list and detail show `⋯` and the move meanwhile, and an issue has one move at a time. If GitHub rejects the move, its error stays on screen until you press `Enter` or `Esc`, and `o` opens the issue in your browser. A refresh that was already running when a move was confirmed doesn't undo it.

## Bulk actions

`Space` selects the issue under the cursor (`☑`, and its row is tinted), or on a group header every issue in the group, folded or not; `A` selects every issue the tab shows and `u` clears the selection. Each tab keeps its own selection, through refreshes, and the line under the list counts it. Issues a search or filter hides stay selected.

`B` acts on the selected issues:

- **Move** offers every move at least one of them can make, each with how many can. Each issue is planned as if moved on its own, so a move works across label-backed and project-backed repos at once.
- **Assign** offers your team roster first, then everyone the issues' repos can assign, each with how many can. It adds that person and leaves other assignees as they are.

Before anything is sent, a confirmation lists the issues that will change and those skipped with the reason (already in that status or assigned to that person, a status their repo doesn't have, someone their repo can't assign, a move still in flight). `Enter` or `y` sends, `Esc` or `n` cancels. Moves show `⋯` until GitHub confirms each one, as single moves do. The summary then lists what changed, what GitHub refused with its error, and what was skipped.

## Preferences

`S` opens preferences:

- **Team roster**: the GitHub logins in the Team tab, one per line
- **Pinned milestones**: `owner/repo/title`, one per line, in the order the Milestones tab shows them
- **Show done issues when lists open**, and the tab lazyissues **starts on**
- **Statuses**: their order (`shift+↑`/`shift+↓`), which are active (`space`), and the highlighted status's move shortcut (the key field)
- **Theme**: any of Textual's built-in themes, previewed as you move through the list

`ctrl+s` saves to `config.toml`, keeping your comments, and applies everything at once: lists regroup, the Team tab reloads its roster. `esc` cancels and puts the saved theme back.


## Writing

`C` comments, `a` assigns, `c` creates and `e` edits, from any list or the detail. In a form, `Enter` submits (or `Ctrl+S` anywhere), `Esc` cancels, and `Shift+Enter` or `Ctrl+J` starts a new line in a text box; pasting keeps its lines. `Ctrl+E` (or the Editor button) opens the text box in `$VISUAL`, else `$EDITOR`, else notepad on Windows and vi elsewhere, and brings the text back for you to review before submitting.

- **Assign** lists your team roster first, then everyone the repo can assign; type to filter, `Space` to pick. Only the people you add or remove change.
- **Create** starts in the repo you last created in, else the selected issue's. Its status is set as a move once the issue exists: the status label in a label-backed repo, or the project's Status in a project-backed one. If GitHub rejects that, the issue is still created and the rejected move stays on screen.
- **Edit** starts from the issue's cached detail when there is one (else GitHub's copy) and sends only the fields you changed, so edits made elsewhere meanwhile are kept.

If you typed text or changed a pick (labels, assignees, milestone), `Esc` asks before discarding it; choosing only create's repo or status doesn't ask: `y` discards, `n` or `Esc` keeps editing. A form whose save is already on its way closes once GitHub answers. If GitHub rejects a write, its error shows in the form and nothing you typed is lost.

## Cache

The last loaded issues are saved in your platform's cache directory (`~/Library/Caches/lazyissues` on macOS, `~/.cache/lazyissues` on Linux, `%LOCALAPPDATA%\lazyissues\Cache` on Windows), one snapshot per tab and repo set. lazyissues shows it at startup while it refreshes. At startup only the tab you start on refreshes; each other tab refreshes the first time you open it. Deleting it is always safe.

The line under each list says how old its issues are (`updated 4m ago`), from startup on. If a refresh fails, the list keeps its cached issues and the line says so and when they were loaded (`refresh failed · cached 09:12`) until a refresh succeeds.

## Rate limits

If GitHub's rate limit is reached, lazyissues says so and when to try again. A read that is told to wait 10 seconds or less (a refresh, a detail) waits and tries once more; a longer wait shows the message at once. A write is never retried, since GitHub may have applied it.

## Install

```bash
uv tool install lazyissues
# or
pipx install lazyissues
```

Maintainers cut releases with [docs/RELEASING.md](https://github.com/cdowell09/lazyissues/blob/main/docs/RELEASING.md).

## Setup

The first time you run `lazyissues` there is no config yet, so it starts setup. Setup needs `gh` installed and logged in (`gh auth login`); without it, lazyissues prints what to run and exits.

1. **Repos.** Setup suggests the repos where you have open issues. Uncheck any you don't want, and type `owner/name` and Enter to add others, including org repos. A repo GitHub won't read (an org that enforces SAML, say) is left out with the reason; add it again to retry. If GitHub can't be reached at all, press `r` to try again.
2. **Status sources.** A repo linked to exactly one project uses that project's Status field; any other repo uses labels. Change either with the repo's dropdown. For a label-backed repo, check the labels that are statuses; none are checked for you, so `bug` never becomes a status by accident.
3. **Statuses.** The statuses from every repo, merged by name, with project options that mean done (Done, Closed, Complete) left out because closing an issue is how it gets there. Reorder them with `shift+↑`/`shift+↓` or the buttons, and check the active ones ("In Progress" starts checked). An empty list is fine; you can add statuses later.
4. **Save** (`ctrl+s`) writes `config.toml` and opens My Work. `esc` goes back a step, and `ctrl+q` quits without saving.

If your `gh` token lacks the `project` scope, setup says so and offers labels only. Run `gh auth refresh -s project`, then start lazyissues again to use project boards.

Setup also puts you on the team roster and adds two saved filters, "Ready for me" (`label:ready-for-human`) and "Needs triage" (`label:needs-triage`).

To change your repos, status sources or statuses later, run `lazyissues --setup`. It starts from your current config: its repos are checked (other repos where you have open issues are offered unchecked), each keeps its status source, the labels that are statuses are checked, and the statuses keep their order, active marks and move keys. A status a source now offers that the config doesn't list joins the end. A repo setup can't read now, or a project-backed one when your token lacks the `project` scope, is kept unchanged with all your statuses, and the Repos screen lists it under "Kept unchanged"; edit `config.toml` to drop one. Saving keeps your team roster, saved filters, preferences, other settings and the comments in `config.toml`; quitting with `ctrl+q` leaves it unchanged. Without a config, `--setup` is the same as a first run.

## Configuration

`config.toml` lives in `~/.config/lazyissues/` (or `$XDG_CONFIG_HOME/lazyissues/`) on macOS and Linux, and `%APPDATA%\lazyissues\` on Windows. lazyissues rewrites it in place when it saves, keeping your comments, so edit it while the app isn't running.

```toml
team = ["octo-dev", "sam-reef"]     # GitHub logins in the Team tab
done_window_days = 14               # how far back `d` reaches for closed issues; the default
pinned_milestones = ["octo-dev/lanternfish/v1.0"]  # owner/repo/title; Milestones shows only these

[preferences]                       # every key is optional; these are the defaults
show_done = false                   # lists start with done issues shown
start_tab = "My Work"               # a tab's title; an unknown one starts on the first tab
theme = "textual-dark"              # one of Textual's built-in themes

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
