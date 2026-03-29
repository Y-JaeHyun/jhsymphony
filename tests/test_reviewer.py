from unittest.mock import AsyncMock, MagicMock

import pytest

from jhsymphony.providers.base import AgentEvent, EventType, RunContext
from jhsymphony.review.reviewer import Reviewer


@pytest.fixture
def mock_provider():
    provider = AsyncMock()

    async def fake_run(session, prompt):
        yield AgentEvent(type=EventType.MESSAGE_DELTA, data={"text": "LGTM. No issues found."})
        yield AgentEvent(type=EventType.COMPLETED, data={"reason": "done"})

    provider.run_turn = fake_run
    provider.start_session.return_value = {"session_id": "review-1"}
    return provider


@pytest.fixture
def mock_tracker():
    tracker = AsyncMock()
    tracker.create_pr.return_value = {"number": 10, "html_url": "https://github.com/o/r/pull/10"}
    return tracker


@pytest.fixture
def reviewer(mock_provider, mock_tracker):
    return Reviewer(provider=mock_provider, tracker=mock_tracker, auto_approve=False)


async def test_review_creates_pr(reviewer, mock_tracker):
    result = await reviewer.review(
        issue_number=1, issue_title="Fix bug",
        branch="jhsymphony/issue-1", base="main", repo="o/r", workspace_path="/tmp",
    )
    assert result.pr_number == 10
    mock_tracker.create_pr.assert_called_once()


async def test_review_posts_review_comment(reviewer, mock_tracker):
    await reviewer.review(
        issue_number=1, issue_title="Fix bug",
        branch="jhsymphony/issue-1", base="main", repo="o/r", workspace_path="/tmp",
    )
    mock_tracker.post_comment.assert_called()
    comment_body = mock_tracker.post_comment.call_args[0][1]
    assert "LGTM" in comment_body or "Review" in comment_body
