## Commit Messages

Commits follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/):

```text
<type>[optional scope][!]: <description>

[optional body]

[optional footer(s)]
```

### Types

- `feat`: a new feature (MINOR in Semantic Versioning).
- `fix`: a bug fix (PATCH).
- `docs`, `refactor`, `test`, `perf`, `style`, `build`, `ci`, `chore`: everything else. These carry no version meaning on their own.
- A breaking change adds `!` before the colon (`feat(config)!: ...`), or a `BREAKING CHANGE: <description>` footer, or both (MAJOR). `BREAKING CHANGE` is the one token that must be uppercase.

### Scope

A scope is a noun naming the part of the codebase, in parentheses: `ui`, `config`, `github`, `cache`, `setup`, `readme`, `release`. Leave it out when the change spans the app.

### Description

The description follows `: ` directly. It is lowercase, imperative, has no trailing period, and says what the change does for the user, not which files it touched: `fix: refetch an issue's detail every time it's opened`.

### Body

The body starts one blank line after the description and is prose wrapped at about 72 columns. It explains **why**: what was wrong or missing before, what behaves differently now, and any non-obvious consequence (a test guard added, a cache refetched once). Write it for the person reading `git log` in a year. Skip it when the description says everything.

### Footers

Footers start one blank line after the body, as `Token: value` or `Token #value`, with `-` in place of spaces in the token (`Reviewed-by: Z`, `Refs: #123`).

### House conventions

- PRs are squash-merged; GitHub appends `(#N)` to the description.
- A squashed PR with several commits keeps each one as a `* type(scope): ...` bullet with its own body.
- Version bumps are their own commit: `chore(release): bump version to 0.9.0`.

### Examples

```text
docs: correct spelling of CHANGELOG
```

```text
fix: refetch an issue's detail every time it's opened (#40)

Details were fetched once and then only read from the local cache,
which nothing invalidates, so a body edited on GitHub never reached the
detail view, even after a refresh. Opening a detail now shows the cached
copy and starts a fetch unless one is running.

Also stops tests from reaching GitHub: the client refuses to send
requests under pytest.
```

```text
feat(config)!: key status shortcuts by status name

BREAKING CHANGE: `[statuses.shortcuts]` replaces the `shortcut` field on
each status; move existing shortcuts into the new table.
```
