import hmac
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

if TYPE_CHECKING:
    from src.config import Config


def extract_review_hook_secret(request: Request) -> str | None:
    """Read the shared secret from X-Review-Hook-Secret or Authorization: Bearer."""
    header = request.headers.get("X-Review-Hook-Secret")
    if header:
        return header.strip()
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def require_review_hook_secret(
    request: Request,
    config: "Config",
    *,
    require_configured: bool,
) -> None:
    """Reject when the hook secret is missing, unset, or does not match."""
    if not config.review_hook_secret:
        if require_configured:
            raise HTTPException(
                status_code=503,
                detail="REVIEW_HOOK_SECRET is not configured.",
            )
        return

    provided = extract_review_hook_secret(request)
    if not provided or not hmac.compare_digest(provided, config.review_hook_secret):
        raise HTTPException(status_code=401, detail="Invalid or missing review hook secret.")