"""Command-line entry point."""

import argparse
import sys

from lazyissues import config as config_module
from lazyissues import demo
from lazyissues.app import LazyIssuesApp
from lazyissues.config import ConfigError
from lazyissues.github import GitHubError, GraphQLGateway, gh_token
from lazyissues.setup import SetupApp
from lazyissues.store import cache_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="A fast terminal UI for GitHub Issues.")
    parser.add_argument("--demo", action="store_true", help="run on made-up data, no GitHub")
    parser.add_argument(
        "--setup", action="store_true", help="rerun setup, starting from the current config"
    )
    args = parser.parse_args()

    if args.demo:
        LazyIssuesApp(demo.config(), demo.github()).run()
        return

    path = config_module.config_dir() / "config.toml"
    # Fail before the TUI takes over the terminal, so errors print normally.
    try:
        config = config_module.load(path) if path.exists() else None
        token = gh_token()
    except (ConfigError, GitHubError) as e:
        sys.exit(f"lazyissues: {e}")
    if config is None or args.setup:
        # Each app runs its own event loop, so each gets its own gateway (and HTTP client).
        current = config
        config = SetupApp(GraphQLGateway(token), path, current).run()
        if config is None:
            sys.exit(
                "lazyissues: setup quit before saving. "
                + (
                    "Run lazyissues again to finish it."
                    if current is None
                    else f"{path} is unchanged."
                )
            )
    LazyIssuesApp(config, GraphQLGateway(token), cache_dir(), config_path=path).run()


if __name__ == "__main__":
    main()
