# Releasing

A release is a version tag pushed from `main`. The tag triggers [`release.yml`](../.github/workflows/release.yml), which publishes to PyPI and creates the GitHub Release.

## Cut a release

From an up-to-date `main` with CI green, for version `X.Y.Z`:

```bash
# 1. Set version = "X.Y.Z" in pyproject.toml, then refresh the lockfile
uv lock

# 2. Commit the bump on its own
git commit -am "chore(release): bump version to X.Y.Z"

# 3. Tag it; the tag must be "v" plus project.version
git tag -a vX.Y.Z -m "vX.Y.Z"

# 4. Push main first, then the tag
git push origin main
git push origin vX.Y.Z
```

## What the workflow does

1. **build**: checks that the tag is `v` plus `project.version` and fails otherwise, then runs `uv build` and keeps the sdist and wheel from `dist/` as an artifact.
2. **publish**: uploads `dist/` to PyPI through trusted publishing, in the `pypi` environment. No API token is stored anywhere.
3. **github-release**: creates the GitHub Release for the tag with generated notes and attaches `dist/*`.

Each job waits for the one before it, so a failed publish leaves no GitHub Release behind.

## If a release fails

- **Tag does not match the version**: delete the tag (`git push --delete origin vX.Y.Z` and `git tag -d vX.Y.Z`), fix the version, and tag again.
- **publish failed**: fix the cause (usually the trusted publisher below) and re-run the failed jobs from the workflow run page.
- PyPI never accepts the same version twice. Once `X.Y.Z` is on PyPI, a fix ships as a new version.

## One-time setup

PyPI must trust this workflow before the first release ([#16](https://github.com/cdowell09/lazyissues/issues/16)). On pypi.org, under *Your account → Publishing → Add a new pending publisher → GitHub*, enter:

| Field | Value |
| --- | --- |
| PyPI Project Name | `lazyissues` |
| Owner | `cdowell09` |
| Repository name | `lazyissues` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

The first successful publish creates the project and turns the pending publisher into a normal one. GitHub creates the `pypi` environment the first time a job uses it; to require approval before each publish, add yourself as a required reviewer under *Settings → Environments → pypi*.
