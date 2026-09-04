import pytest

from src.pr_url import parse_pr_url


def test_parse_pr_url_basic():
    repo_slug, pr_id = parse_pr_url(
        "https://bitbucket.org/my-ws/my-repo/pull-requests/42", "my-ws"
    )
    assert repo_slug == "my-repo"
    assert pr_id == 42


def test_parse_pr_url_trailing_slash():
    repo_slug, pr_id = parse_pr_url(
        "https://bitbucket.org/my-ws/my-repo/pull-requests/42/", "my-ws"
    )
    assert repo_slug == "my-repo"
    assert pr_id == 42


def test_parse_pr_url_strips_whitespace():
    repo_slug, pr_id = parse_pr_url(
        "  https://bitbucket.org/my-ws/my-repo/pull-requests/42  ", "my-ws"
    )
    assert repo_slug == "my-repo"
    assert pr_id == 42


def test_parse_pr_url_wrong_workspace():
    with pytest.raises(ValueError, match="does not match configured workspace"):
        parse_pr_url("https://bitbucket.org/other-ws/my-repo/pull-requests/42", "my-ws")


def test_parse_pr_url_malformed():
    with pytest.raises(ValueError, match="Invalid Bitbucket PR URL"):
        parse_pr_url("https://example.com/not-a-pr-url", "my-ws")
