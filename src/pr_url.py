import re

_PR_URL_RE = re.compile(
    r"^https://bitbucket\.org/(?P<workspace>[^/]+)/(?P<repo_slug>[^/]+)/pull-requests/(?P<pr_id>\d+)/?$"
)

_PR_URL_FIND_RE = re.compile(
    r"https://bitbucket\.org/(?P<workspace>[^/]+)/(?P<repo_slug>[^/]+)/pull-requests/(?P<pr_id>\d+)/?"
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


def extract_pr_urls_from_text(text: str, expected_workspace: str) -> tuple[list[str], list[str]]:
    """Find Bitbucket PR URLs in arbitrary text.

    Returns (accepted_urls, error_messages) where accepted_urls are unique,
    normalized URLs in order of first appearance.
    """
    accepted: list[str] = []
    seen: set[tuple[str, int]] = set()
    errors: list[str] = []

    for match in _PR_URL_FIND_RE.finditer(text):
        workspace = match.group("workspace")
        repo_slug = match.group("repo_slug")
        pr_id = int(match.group("pr_id"))
        raw = match.group(0).rstrip("/")
        key = (repo_slug, pr_id)
        if key in seen:
            continue
        if workspace != expected_workspace:
            errors.append(
                f"PR URL workspace {workspace!r} does not match configured workspace {expected_workspace!r}: {raw}"
            )
            continue
        seen.add(key)
        accepted.append(
            f"https://bitbucket.org/{workspace}/{repo_slug}/pull-requests/{pr_id}"
        )

    return accepted, errors
