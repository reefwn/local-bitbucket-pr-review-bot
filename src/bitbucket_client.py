import httpx

from src.config import Config


class BitbucketApiError(Exception):
    """Raised when the Bitbucket API returns an error. Message includes status, URL, and response body."""

    def __init__(self, response: httpx.Response):
        body = response.text.strip()
        self.status_code = response.status_code
        self.method = response.request.method
        self.url = str(response.request.url)
        self.body = body
        super().__init__(
            f"Bitbucket API error {response.status_code} {self.method} {self.url}\n{body or '(empty response body)'}"
        )


class BitbucketClient:
    """Bitbucket Cloud API client (PR-related endpoints only)."""

    def __init__(self, config: Config):
        self.config = config
        self._http = httpx.AsyncClient(
            auth=(config.bitbucket_email, config.bitbucket_api_token),
            headers={"Accept": "application/json"},
            timeout=30.0,
            follow_redirects=True,
        )

    @staticmethod
    def _check_response(response: httpx.Response) -> None:
        if response.is_error:
            raise BitbucketApiError(response)

    async def get(self, path: str, params: dict | None = None) -> dict:
        r = await self._http.get(f"{self.config.bitbucket_base_url}{path}", params=params)
        self._check_response(r)
        return r.json()

    async def get_text(self, path: str, params: dict | None = None) -> str:
        r = await self._http.get(f"{self.config.bitbucket_base_url}{path}", params=params)
        self._check_response(r)
        return r.text

    async def post(self, path: str, json: dict) -> dict:
        r = await self._http.post(f"{self.config.bitbucket_base_url}{path}", json=json)
        self._check_response(r)
        return r.json()

    async def close(self) -> None:
        await self._http.aclose()

    async def get_own_account_uuid(self) -> str:
        """UUID of the authenticated Bitbucket account (the review bot's own identity)."""
        data = await self.get("/user")
        return data["uuid"]

    async def get_review_outcome(self, repo_slug: str, pr_id: int) -> str:
        """Determine whether the bot's own review on a PR was an approval or a change request.

        Reads the PR's `participants` list and looks for the entry matching the
        authenticated account's UUID. Returns 'approved', 'changes_requested', or
        'unknown' if no matching participant entry is found (e.g. the review
        comment posted but the approve/request-changes call failed or hasn't
        landed yet).
        """
        own_uuid = await self.get_own_account_uuid()
        pr_data = await self.get(f"/repositories/{self.config.bitbucket_workspace}/{repo_slug}/pullrequests/{pr_id}")
        for participant in pr_data.get("participants", []):
            if participant.get("user", {}).get("uuid") == own_uuid:
                state = participant.get("state")
                if state in ("approved", "changes_requested"):
                    return state
        return "unknown"
