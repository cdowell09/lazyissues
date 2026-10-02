# lazyissues

A fast, cache-first Python TUI for GitHub Issues, built with Textual. A port of [lazyjira](https://github.com/cdowellmdb/lazyjira) (Rust, Jira) to GitHub: same workflows, GitHub's own concepts.

## Build & Run

```bash
uv run lazyissues          # runs this checkout
uv run lazyissues --demo   # fake data, no GitHub calls
uv run pytest
```

`.githooks/pre-commit` runs the format, lint and type checks on commits that touch Python. Enable it per clone with `git config core.hooksPath .githooks`, and fix what it reports rather than committing with `--no-verify` (unless asked).

Supported platforms are macOS, Linux and Windows. Reach for `pathlib`, `platformdirs`, `webbrowser` and Textual's clipboard, so every path, browser link and copy works on all three.

## Architecture

- **`__main__`**: CLI; loads config and the `gh` token before the TUI takes the terminal, so errors print normally; runs setup first when there is no config
- **`app`**: the Textual app shell; `tabs()` lists one view per tab; `save_config(config)` is the one place the running app changes and writes its config (to the path it was started with), and both `save_filters` and preferences go through it; `configure(config)` applies saved preferences to the mover and each list view's `configure`, so a view that reads config and isn't an `IssueList` needs a `configure(config)` called from there; `action_keys` names the binding sections `?` lists, so add a section for each new screen with keys (and `MOUSE` lists what the mouse does)
- **`views/`**: one module per tab. List tabs subclass `views.issue_list.IssueList` and supply only their search (`QUERY`, or `queries()`) and `grouping()`, and may add to a group's `header()` (Milestones adds its progress bar); `IssueList` owns the tab's store, refresh, view state (search, status focus, done, folds, repo filter), Enter-to-detail, clicks (`on_click`: select, open, fold) and drawing, hosts `m` and status shortcut keys for moves and the writing keys (`writer.IssueActions`), and `configure(config)` re-applies a changed config live
- **`views.issue_list.IssueTable`**: the list's `DataTable`, with drag-to-select text (Textual's table has none); it hands clicks to its `IssueList`
- **`views.filters`**: the Filters tab: `Filters`, a sidebar of saved filters (`n`/`e`/`x` through `FilterForm` and `ConfirmDelete`) beside the results of the one that ran; each query's results are a `FilterResults` list of their own, keyed by `results_id(query)` for its store and snapshot
- **`preferences`**: `PreferencesScreen` (`S`), which edits a copy of the config and dismisses with it on Save or None on Cancel; it previews themes live and restores the config's theme on Cancel
- **`keys`**: `KeysScreen` (`?`), which lists the bindings it is given, so help is generated from the code's `BINDINGS`
- **`status_list`**: `StatusList`, the status-list editor (order, active, move key) that setup and preferences share
- **`view_model`**: pure, no Textual: `visible_groups(issues, grouping, rules, state)` turns issues and a tab's `ViewState` into the groups and rows it shows, each `Row` with its sub-issue nesting and parent fold; `by_status`, `by_assignee` and `by_milestone` are the groupings; `pinned` picks config's pinned milestones and `progress_bar` draws a milestone's progress. Put list filtering, grouping and nesting here, tested without the UI
- **`detail`**: `IssueDetailScreen`, the issue detail and activity any view opens over its list; it takes the list's issues, the selected index and a `select` callback, so it never depends on which tab opened it; `m` moves its issue and the writing keys work there too; `[×]` closes it
- **`move_planner`**: pure: `Planner(rules, viewer, project_options)` gives an issue's `targets()` and `plan(issue, target)`, the steps (gateway calls) of a move or a `Skip` reason; each step's `apply` gives the issue once GitHub confirms, and `send` runs a plan. A bulk move plans each issue on its own
- **`move_tracker`**: pure: `MoveTracker` holds pending, confirmed and rejected moves; `settle(issue, requested_at)` is the stale-read rule every read of a status goes through (ADR 0003)
- **`move_picker`**: `MovePicker`, the modal listing a move's targets, and the duplicate prompt
- **`menu`**: `Menu`, the option list for every picker and sidebar: a click chooses an option, a click on the chosen one takes it. Use it, not `OptionList`, for lists of choices
- **`clipboard`**: `copy(text, platform, run)`, the system clipboard fallback (`pbcopy`, `wl-copy`/`xclip`, `clip.exe`) the app's `copy_to_clipboard` adds to OSC 52; refuses the real clipboard under pytest, so app tests pass `system_clipboard=`. `COPY` is the footer's Copy binding: add it to a modal screen's `BINDINGS` to offer Copy there
- **`mover`**: `Mover`, the app's one move flow (picker, shortcuts, send, confirm; updates the detail cache and publishes `changed`, on which each list applies confirmed moves to its store) and `RejectedMoveBanner`
- **`writer`**: `Writer`, the app's one write flow, like `Mover` for moves: `comment`, `assign`, `create` and `edit` open their form; once GitHub confirms it settles the result through the move tracker, updates the detail cache and publishes `changed` (a list holding the issue shows the new copy, one that doesn't reloads); a new issue's status is set with `Mover.move`. `IssueActions` binds `C`/`a`/`c`/`e` for a list or the detail
- **`forms/`**: the forms `Writer` opens, one module each (`comment`, `assign`, `create`, `edit`), on the shared `forms.form`: `Form` (load what the fields offer, save, keep GitHub's error on screen), `TextField` (Enter submits, Shift+Enter/Ctrl+J newline, `Ctrl+E` editor), `Picker` (filterable multi-select) and `IssueFields`
- **`editor`**: `edit(text)` round-trips text through `$VISUAL`, `$EDITOR`, or notepad/vi; the form suspends the app around it
- **`github`**: the gateway protocol and its GraphQL implementation; `gh_token`
- **`fake`**: `FakeGitHub`, the in-memory gateway for tests and `--demo`
- **`demo`**: made-up config and data for `--demo`
- **`models`**: domain records (`Issue` with its milestone and parent key, `Milestone`, `Project`, and `IssueDetail` with its `Event` activity and `ProjectField` values)
- **`statuses`**: `StatusRules`, the only code that interprets statuses: resolves an issue's status from its repo's status source, groups issues in display order (a closed issue's status is Done, last), answers done and active, lists the statuses a move can reach (`reachable`); `is_done_option` for project options that mean done
- **`store`**: `IssueStore`, the loaded issues and their snapshot in the platform cache dir, one per view and repo set; `refresh` stamps each read with when it was requested, and `replace` ignores a read older than the one applied and settles each issue through the app's shared `MoveTracker`; `now` is the clock for both
- **`search`**: building a view's searches: `scoped(query, repos)` adds the repo set unless the query has `repo:`/`org:`/`user:`; `with_states(query, closed_since)` adds `is:open` (and the done window) unless the query names its own state
- **`config`**: `config.toml` location, loading, and `save()` (rewrites in place with `tomlkit`, keeping the user's comments); `Preferences` (`[preferences]`: show done, start tab, theme)
- **`discovery`**: first-run discovery: `discover()` builds a `Proposal` (repos, status sources, statuses, roster, starter filters) from the gateway; owns every setup suggestion rule
- **`setup`**: `SetupApp`, the first-run screens; they only edit a `Proposal`, then save it and exit with the `Config`

## Domain and decisions

Name domain concepts with [CONTEXT.md](CONTEXT.md): Issue, Status, Status source, Move, Close reason, Milestone, Sub-issue. Before changing GitHub transport, status interpretation, or move consistency, read the matching decision in [docs/adr/](docs/adr/).

## Key Design Decisions

### Statuses
A status is a name the user defines in config; the app ships none. Each repo's status source (labels, or one project's Status field) supplies them, matched by name ignoring case and treating `-`, `_` and spaces alike ([ADR 0001](docs/adr/0001-status-from-labels-or-project.md)). One status-rules object built from config (`statuses.StatusRules`) owns display order, done-ness and active-ness; every view asks it, so sorting statuses or testing for done happens in that one place. Done is always "closed", in every source.

### Moves wait for GitHub
A move changes an issue only after GitHub confirms it; a failure keeps the old status and shows the error until dismissed. Every read carries the time it was requested, and a read requested before an issue's latest confirmed move must not overwrite its status ([ADR 0003](docs/adr/0003-confirm-moves-and-reject-stale-status.md)). Route status writes from reads through the cache-update helpers that check this.

### GitHub gateway
Everything above the gateway depends on the `Gateway` protocol, never on GraphQL. It has two implementations that must stay in step: `GraphQLGateway` (GraphQL over `httpx`, authenticated with `gh auth token`, [ADR 0002](docs/adr/0002-graphql-over-httpx-with-gh-token.md)) and `FakeGitHub`, an in-memory GitHub. Every new gateway operation lands in both, with a `MockTransport` test for the GraphQL shape and app tests against the fake. `GraphQLGateway` refuses to build a real transport under pytest, so no test reaches GitHub. `FakeGitHub` interprets only the search syntax the app sends; teach it each new qualifier the app starts using.

### Demo recording
`--demo` runs the app on `FakeGitHub` seeded with made-up data; record demos only in that mode, never against a real account. Extend the demo data whenever a feature needs something to show.

## Configuration

`config.toml` lives in the platform config directory (`~/.config/lazyissues/`, `%APPDATA%\lazyissues\` on Windows). The app reads it at startup and rewrites it when preferences or saved filters change, so hand edits belong outside a running session.

## Notes

`README.md` is the onboarding doc for install, setup and keybindings; update it with any user-facing change.

## Before a PR lands

Run all three reviews against the PR's diff, rank every finding P0–P3, and fix every P0–P2 before merging:

- `/ponytail:ponytail-review`: over-engineering
- `/pragmatic-programmer`: craftsmanship (DRY, orthogonality, contracts)
- `/thermo-nuclear-code-quality-review`: structure and maintainability

P0 breaks behavior or loses data, P1 is a bug or structural regression, P2 is a maintainability problem worth fixing now, P3 is a nit. List the P3s you left in the PR description.

## Commit Messages
Follow @COMMIT_STYLING.md
