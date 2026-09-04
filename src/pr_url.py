import re

_PR_URL_RE = re.compile(
    r"^https://bitbucket\.org/(?P<workspace>[^/]+)/(?P<repo_slug>[^/]+)/pull-requests/(?P<pr_id>\d+)/?$"
)


def parse_pr_url(url: str, expected_workspace: str) -> tuple[str, int]:
    """Parse a Bitbucket PR URL into (repo_slug, pr_id).

    Raises ValueError if the URL doesn't match the expected Bitbucket PR URL
    shape, or if its workspace doesn't match expected_workspace.
    """
    match = _PR_URL_RE.match(url.strip())
    if not match:
        raise ValueError(
            f"Invalid Bitbucket PR URL: {url!r}. Expected "
            "'https://bitbucket.org/{workspace}/{repo_slug}/pull-requests/{pr_id}'."
        )
    workspace = match.group("workspace")
    if workspace != expected_workspace:
        raise ValueError(
            f"PR URL workspace {workspace!r} does not match configured workspace {expected_workspace!r}."
        )
    return match.group("repo_slug"), int(match.group("pr_id"))
