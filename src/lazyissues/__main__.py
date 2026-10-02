"""Command-line entry point."""

import argparse
import sys

from lazyissues import config as config_module
from lazyissues import demo
from lazyissues.app import LazyIssuesApp
from lazyissues.config import ConfigError
from lazyissues.github import GitHubError, GraphQLGateway, gh_token


def main() -> None:
    parser = argparse.ArgumentParser(description="A fast terminal UI for GitHub Issues.")
    parser.add_argument("--demo", action="store_true", help="run on made-up data, no GitHub")
    args = parser.parse_args()

    if args.demo:
        app = LazyIssuesApp(demo.config(), demo.github())
    else:
        # Fail before the TUI takes over the terminal, so errors print normally.
        try:
            config = config_module.load(config_module.config_dir() / "config.toml")
            app = LazyIssuesApp(config, GraphQLGateway(gh_token()))
        except (ConfigError, GitHubError) as e:
            sys.exit(f"lazyissues: {e}")
    app.run()


if __name__ == "__main__":
    main()
