from lazyissues import demo


async def test_fake_understands_the_queries_the_app_sends():
    github = demo.github()
    github.closed.add("octo-dev/tidepool#15")

    async def numbers(query: str) -> list[int]:
        return [issue.number for issue in await github.search_issues(query)]

    assert await numbers("is:issue is:open assignee:@me repo:octo-dev/tidepool") == [12]
    assert await numbers("is:closed") == [15]
    assert await numbers("no:assignee") == [18]
    assert await numbers("assignee:sam-reef repo:octo-dev/lanternfish") == [4, 7]
    assert await numbers('label:bug "tide table"') == [12]


async def test_fake_returns_state_and_project_statuses_like_github():
    github = demo.github()
    github.closed.add("octo-dev/tidepool#15")
    found = {issue.key: issue for issue in await github.search_issues("is:issue")}
    assert found["octo-dev/tidepool#15"].closed
    assert not found["octo-dev/tidepool#12"].closed
    assert found["octo-dev/lanternfish#4"].project_statuses == {"octo-dev/3": "In Progress"}
