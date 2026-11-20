import base64
import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING, Literal

from fastapi import HTTPException, Request, Response

if TYPE_CHECKING:
    from src.config import Config

UiRole = Literal["user", "admin"]

SESSION_COOKIE_NAME = "review_ui_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
_ROLE_RANK: dict[str, int] = {"user": 1, "admin": 2}


def ui_auth_enabled(config: "Config") -> bool:
    return bool(config.review_ui_admin_password and config.review_ui_user_password)


def session_signing_key(config: "Config") -> str:
    if config.review_ui_session_key:
        return config.review_ui_session_key
    if config.review_hook_secret:
        return config.review_hook_secret
    material = f"{config.review_ui_admin_password}:{config.review_ui_user_password}".encode()
    return hmac.new(material, b"review-ui-session", hashlib.sha256).hexdigest()


def _password_matches(provided: str, expected: str) -> bool:
    if not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def authenticate_password(config: "Config", password: str) -> UiRole | None:
    if _password_matches(password, config.review_ui_admin_password):
        return "admin"
    if _password_matches(password, config.review_ui_user_password):
        return "user"
    return None


def create_session_token(config: "Config", role: UiRole) -> str:
    payload = {"role": role, "exp": int(time.time()) + SESSION_TTL_SECONDS}
    data = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(
        session_signing_key(config).encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{data}.{sig}"


def _parse_session_token(config: "Config", token: str) -> UiRole | None:
    try:
        data, sig = token.rsplit(".", 1)
    except ValueError:
        return None
    expected = hmac.new(
        session_signing_key(config).encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(data.encode()))
    except (json.JSONDecodeError, ValueError):
        return None
    if payload.get("exp", 0) < int(time.time()):
        return None
    role = payload.get("role")
    if role not in _ROLE_RANK:
        return None
    return role


def session_role_from_request(request: Request, config: "Config") -> UiRole | None:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    return _parse_session_token(config, token)


def set_session_cookie(response: Response, config: "Config", role: UiRole) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_token(config, role),
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")


def require_ui_role(request: Request, config: "Config", minimum: UiRole) -> UiRole:
    if not ui_auth_enabled(config):
        return "admin"
    role = session_role_from_request(request, config)
    if not role:
        raise HTTPException(status_code=401, detail="Sign in required.")
    if _ROLE_RANK[role] < _ROLE_RANK[minimum]:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return role