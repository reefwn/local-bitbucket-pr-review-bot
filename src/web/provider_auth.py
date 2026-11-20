import json
import logging
import subprocess
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

PROVIDER_AUTH_TIMEOUT_SECONDS = 20


@dataclass
class ProviderLoginStatus:
    provider: str
    logged_in: bool
    summary: str

    def to_dict(self) -> dict:
        return asdict(self)


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=PROVIDER_AUTH_TIMEOUT_SECONDS,
    )


def check_claude_login() -> ProviderLoginStatus:
    try:
        result = _run(["claude", "auth", "status", "--json"])
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return ProviderLoginStatus("claude", False, str(exc))

    stdout = result.stdout.strip()
    if result.returncode != 0:
        detail = (result.stderr or stdout or "auth check failed").strip()
        return ProviderLoginStatus("claude", False, detail)

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return ProviderLoginStatus("claude", False, "Unexpected auth status output")

    if payload.get("loggedIn"):
        method = payload.get("authMethod") or payload.get("apiProvider") or "logged in"
        return ProviderLoginStatus("claude", True, str(method))
    return ProviderLoginStatus("claude", False, "Not logged in")


def check_codex_login() -> ProviderLoginStatus:
    try:
        result = _run(["codex", "login", "status"])
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return ProviderLoginStatus("codex", False, str(exc))

    text = f"{result.stdout}\n{result.stderr}".strip()
    lowered = text.lower()
    if result.returncode == 0 and ("logged in" in lowered or "✓" in text):
        line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "Logged in")
        return ProviderLoginStatus("codex", True, line.removeprefix("✓ ").strip())
    if "not logged" in lowered or "login required" in lowered:
        return ProviderLoginStatus("codex", False, "Not logged in")
    detail = text or "auth check failed"
    return ProviderLoginStatus("codex", False, detail)


def check_cursor_login() -> ProviderLoginStatus:
    try:
        result = _run(["agent", "status"])
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return ProviderLoginStatus("cursor", False, str(exc))

    text = f"{result.stdout}\n{result.stderr}".strip()
    lowered = text.lower()
    if result.returncode == 0 and ("logged in" in lowered or "✓" in text):
        line = next((ln.strip() for ln in text.splitlines() if "logged in" in ln.lower()), text)
        return ProviderLoginStatus("cursor", True, line.removeprefix("✓ ").strip())
    if "not logged" in lowered:
        return ProviderLoginStatus("cursor", False, "Not logged in")
    detail = text or "auth check failed"
    return ProviderLoginStatus("cursor", False, detail)


def check_kiro_login() -> ProviderLoginStatus:
    try:
        result = _run(["kiro-cli", "whoami"])
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return ProviderLoginStatus("kiro", False, str(exc))

    text = f"{result.stdout}\n{result.stderr}".strip()
    lowered = text.lower()
    if result.returncode == 0 and text and "not logged" not in lowered and "login" not in lowered[:40]:
        line = next((ln.strip() for ln in text.splitlines() if ln.strip()), text)
        return ProviderLoginStatus("kiro", True, line)
    if "not logged" in lowered or "login required" in lowered or "not authenticated" in lowered:
        return ProviderLoginStatus("kiro", False, "Not logged in")
    detail = text or "auth check failed"
    return ProviderLoginStatus("kiro", False, detail)


def collect_provider_login_status() -> list[dict]:
    checks = (check_claude_login, check_codex_login, check_cursor_login, check_kiro_login)
    results: list[dict] = []
    for check in checks:
        try:
            results.append(check().to_dict())
        except Exception:
            logger.exception("Provider login check failed: %s", check.__name__)
            name = check.__name__.removeprefix("check_").removesuffix("_login")
            results.append(ProviderLoginStatus(name, False, "Check failed").to_dict())
    return results