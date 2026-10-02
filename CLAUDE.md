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

- **`__main__`**: CLI; loads config and the `gh` token before the TUI takes the terminal, so errors print normally
- **`app`**: the Textual app shell; `tabs()` lists one view per tab
- **`views/`**: one module per tab; each view owns its loading and rendering
- **`detail`**: `IssueDetailScreen`, the issue detail and activity any view opens over its list; it takes the list's issues, the selected index and a `select` callback, so it never depends on which tab opened it
- **`github`**: the gateway protocol and its GraphQL implementation; `gh_token`
- **`fake`**: `FakeGitHub`, the in-memory gateway for tests and `--demo`
- **`demo`**: made-up config and data for `--demo`
- **`models`**: domain records (`Issue`, and `IssueDetail` with its `Event` activity and `ProjectField` values)
- **`statuses`**: `StatusRules`, the only code that interprets statuses: resolves an issue's status from its repo's status source, groups issues in display order, answers done and active
- **`store`**: `IssueStore`, the loaded issues and their snapshot in the platform cache dir, one per repo set; `refresh` stamps each read with when it was requested, and `replace` ignores a read older than the one applied
- **`search`**: scoping search queries to the repo set
- **`config`**: `config.toml` location and loading

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
