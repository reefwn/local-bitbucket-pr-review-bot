import json
import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_ALLOWED_TOOLS = (
    "mcp__bitbucket-pr__bitbucket_get_pr,"
    "mcp__bitbucket-pr__bitbucket_get_pr_diff,"
    "mcp__bitbucket-pr__bitbucket_list_pr_comments,"
    "mcp__bitbucket-pr__bitbucket_create_pr_comment,"
    "mcp__bitbucket-pr__bitbucket_approve_pr,"
    "mcp__bitbucket-pr__bitbucket_request_changes_pr"
)

# Substrings seen in Claude Code's headless output when the account has
# exhausted its usage/rate/session quota (case-insensitive match against the
# JSON `result` field, or stderr on a non-zero exit).
_USAGE_LIMIT_PATTERNS = (
    "usage limit",
    "session limit",
    "rate limit",
    "quota",
)

KIRO_AGENT_NAME = "pr-reviewer"
DEFAULT_MCP_URL = "http://mcp:7390/mcp"
DEFAULT_PROVIDER_ORDER = ("claude", "codex", "cursor", "kiro")
DEFAULT_CURSOR_WORKSPACE = "/app"


def build_prompt(repo_slug: str, pr_id: int, existing_comments: str) -> str:
    """Build the headless review prompt for a single PR."""
    comments_section = existing_comments.strip() or "(none)"
    return (
        f"Review pull request #{pr_id} in the '{repo_slug}' Bitbucket repository. "
        "Fetch its diff and existing comments using the available Bitbucket tools, "
        "then post a single review comment via bitbucket_create_pr_comment.\n\n"
        "After posting the comment, take one action: if the review found zero "
        "Critical and zero Important issues, call bitbucket_approve_pr; if it found "
        "any Critical or Important issue, call bitbucket_request_changes_pr instead. "
        "Never decline or close the PR.\n\n"
        "Review across these dimensions (adapted from the pr-review-toolkit:review-pr "
        "Claude Code skill, condensed into one pass since this runs headless against a "
        "diff rather than a local checkout):\n"
        "- Correctness: bugs, logic errors, race conditions, security issues, adherence "
        "to project conventions.\n"
        "- Silent failures: empty or overly broad catch blocks, swallowed errors, "
        "undocumented fallback behavior, unhelpful error messages.\n"
        "- Test coverage: missing tests for new logic, edge cases, or error paths.\n"
        "- Comment accuracy: comments that are outdated, misleading, or restate the "
        "obvious.\n"
        "- Type design (if new types/dataclasses/schemas are introduced): weak "
        "invariants, exposed mutable internals, missing validation.\n"
        "- Simplification: unnecessary complexity, duplication, or over-engineering "
        "that could be simplified without changing behavior.\n\n"
        "Only report issues you're confident about — skip nitpicks and speculative "
        "concerns. Structure the comment as:\n"
        "## Critical Issues (must fix)\n## Important Issues (should fix)\n"
        "## Suggestions (nice to have)\n## Strengths\n"
        "Use file:line references. Omit a section if it has nothing to report.\n\n"
        f"Existing comments on this PR (do not repeat points already raised):\n{comments_section}"
    )


def write_mcp_config(path: str, mcp_url: str) -> None:
    """Write the MCP config file Claude Code needs to reach the bundled Bitbucket-PR MCP server."""
    config = {"mcpServers": {"bitbucket-pr": {"type": "http", "url": mcp_url}}}
    with open(path, "w") as f:
        json.dump(config, f)


def write_cursor_mcp_config(path: str, mcp_url: str) -> None:
    """Write project `.cursor/mcp.json` for the Cursor Agent CLI."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    config = {"mcpServers": {"bitbucket-pr": {"type": "http", "url": mcp_url}}}
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


_KIRO_AGENT_TOOLS = (
    "@bitbucket-pr/bitbucket_get_pr",
    "@bitbucket-pr/bitbucket_get_pr_diff",
    "@bitbucket-pr/bitbucket_list_pr_comments",
    "@bitbucket-pr/bitbucket_create_pr_comment",
    "@bitbucket-pr/bitbucket_approve_pr",
    "@bitbucket-pr/bitbucket_request_changes_pr",
)

KIRO_AGENT_DIR = ".kiro/agents"


def write_kiro_mcp_config(path: str, mcp_url: str) -> None:
    """Write the resolved `pr-reviewer` Kiro agent config with a literal MCP URL.

    Kiro CLI's `${VAR}` expansion does not apply to a remote MCP server's
    `url` field (confirmed: it fails at runtime with "relative URL without a
    base" when left as `${MCP_URL}` in the static .kiro/agents/pr-reviewer.json
    checked into the repo). So at startup we regenerate that same agent file
    with the actual URL substituted in, overwriting the placeholder version.

    `path` is expected to be the agent config path (e.g.
    `.kiro/agents/pr-reviewer.json`), not a standalone mcp.json.
    """
    config = {
        "name": "pr-reviewer",
        "description": "Headless Bitbucket PR reviewer used as the Kiro fallback when Claude hits its usage limit.",
        "prompt": (
            "You review a single Bitbucket pull request per invocation using only the "
            "bitbucket-pr MCP tools. Fetch the diff and existing comments, post one review "
            "comment, then approve the PR or request changes based on the review. Never "
            "decline or close a PR."
        ),
        "tools": list(_KIRO_AGENT_TOOLS),
        "allowedTools": list(_KIRO_AGENT_TOOLS),
        "mcpServers": {"bitbucket-pr": {"url": mcp_url, "disabled": False}},
    }
    with open(path, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def _is_usage_limit_failure(returncode: int, stdout: str, stderr: str) -> bool:
    """Decide whether a Claude invocation failed because of an exhausted usage quota.

    Claude Code's headless mode can report a usage-limit condition either as
    a non-zero exit (auth/rate-limit failures) or as exit 0 with the limit
    message embedded in the JSON `result` field. We check both.
    """
    haystack = f"{stdout}\n{stderr}".lower()
    if any(pattern in haystack for pattern in _USAGE_LIMIT_PATTERNS):
        return True
    if returncode != 0:
        # Non-zero exits are usage-limit failures only if they also mention
        # a limit/quota phrase (already checked above); otherwise they're
        # generic failures (bad prompt, tool crash, etc.) that should
        # propagate rather than trigger a fallback.
        return False
    return False


def _run_claude(prompt: str, mcp_config_path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "claude", "-p", prompt,
            "--mcp-config", mcp_config_path,
            "--allowedTools", _ALLOWED_TOOLS,
            "--output-format", "json",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )


def _run_codex(prompt: str, mcp_url: str) -> subprocess.CompletedProcess:
    """Run Codex non-interactively with the Bitbucket MCP server for this review.

    Codex reads MCP settings from TOML. A command-line config override keeps the
    review server scoped to this invocation and, importantly, does not overwrite
    the user's persisted ``~/.codex/config.toml`` alongside their device-login
    credentials. MCP calls need the bypass flag in Codex's non-interactive mode;
    the bot is already isolated in its Docker container and the prompt limits the
    agent to the Bitbucket MCP tools.
    """
    mcp_config = f"mcp_servers.bitbucket-pr.url={json.dumps(mcp_url)}"
    return subprocess.run(
        [
            "codex", "exec",
            "--json",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "--config", mcp_config,
            prompt,
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )


def _run_kiro(prompt: str, agent_name: str = KIRO_AGENT_NAME) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "kiro-cli", "chat", prompt,
            "--agent", agent_name,
            "--no-interactive",
            "--output-format", "text",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )


def _run_cursor(
    prompt: str, workspace: str, cursor_model: str | None = None
) -> subprocess.CompletedProcess:
    """Run Cursor Agent in print mode with project MCP config and headless approvals."""
    args = ["agent"]
    if cursor_model:
        args.extend(["--model", cursor_model])
    args.extend(
        [
            "-p",
            prompt,
            "--trust",
            "--approve-mcps",
            "--force",
            "--output-format",
            "json",
            "--workspace",
            workspace,
        ]
    )
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=600,
        cwd=workspace,
    )


def _codex_result(stdout: str) -> str:
    """Extract Codex's final agent message from its JSONL exec output.

    ``codex exec --json`` emits progress events as JSON Lines and the final
    response as the final completed ``agent_message`` item. Retain raw output as
    a fallback so a compatible future CLI format remains visible to callers.
    """
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            return item.get("text", "")
    return stdout


def _redact_prompt_arg(args: list[str]) -> list[str]:
    """Replace the (potentially huge) prompt argument with a placeholder for log/error output.

    Any argument over 200 chars is almost certainly the review prompt, not a
    flag or path — long enough to distinguish reliably without tracking
    positional argument indices per CLI.
    """
    return [f"<prompt, {len(arg)} chars>" if len(arg) > 200 else arg for arg in args]


def _cursor_result(stdout: str) -> str:
    """Parse Cursor Agent `--output-format json` stdout."""
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout.strip()
    if isinstance(payload.get("result"), str):
        return payload["result"]
    return stdout.strip()


def _validated_provider_order(provider_order: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    providers = tuple(provider_order)
    if set(providers) != set(DEFAULT_PROVIDER_ORDER) or len(providers) != len(DEFAULT_PROVIDER_ORDER):
        raise ValueError(
            "Provider order must contain Claude, Codex, Kiro, and Cursor exactly once."
        )
    return providers


def _run_provider(
    provider: str,
    prompt: str,
    mcp_config_path: str,
    kiro_agent_name: str,
    mcp_url: str,
    cursor_workspace: str,
    cursor_model: str | None,
) -> subprocess.CompletedProcess:
    if provider == "claude":
        return _run_claude(prompt, mcp_config_path)
    if provider == "codex":
        return _run_codex(prompt, mcp_url)
    if provider == "kiro":
        return _run_kiro(prompt, kiro_agent_name)
    return _run_cursor(prompt, cursor_workspace, cursor_model)


def _provider_result(provider: str, stdout: str) -> dict:
    if provider == "claude":
        return json.loads(stdout)
    if provider == "codex":
        return {"result": _codex_result(stdout), "agent": "codex"}
    if provider == "kiro":
        return {"result": stdout, "agent": "kiro"}
    return {"result": _cursor_result(stdout), "agent": "cursor"}


def run_review(
    repo_slug: str,
    pr_id: int,
    existing_comments: str,
    mcp_config_path: str,
    kiro_agent_name: str = KIRO_AGENT_NAME,
    mcp_url: str = DEFAULT_MCP_URL,
    provider_order: list[str] | tuple[str, ...] = DEFAULT_PROVIDER_ORDER,
    cursor_workspace: str = DEFAULT_CURSOR_WORKSPACE,
    cursor_model: str | None = None,
) -> dict:
    """Run review providers in priority order, only falling back on quota exhaustion.

    The primary result retains Claude's JSON response. Codex, Kiro, and Cursor return a
    normalized text result labelled with the agent that produced it.

    Raises subprocess.CalledProcessError if the invocation fails for a reason
    other than an exhausted agent usage quota, or if the final configured fallback
    itself fails.
    """
    prompt = build_prompt(repo_slug, pr_id, existing_comments)
    providers = _validated_provider_order(provider_order)
    for index, provider in enumerate(providers):
        result = _run_provider(
            provider,
            prompt,
            mcp_config_path,
            kiro_agent_name,
            mcp_url,
            cursor_workspace,
            cursor_model,
        )
        usage_limit_hit = _is_usage_limit_failure(result.returncode, result.stdout, result.stderr)
        if result.returncode == 0 and not usage_limit_hit:
            return _provider_result(provider, result.stdout)

        if not usage_limit_hit:
            logger.error(
                "%s failed reviewing %s PR #%s (exit %s): %s",
                provider.title(), repo_slug, pr_id, result.returncode,
                result.stderr.strip() or result.stdout.strip(),
            )
            raise subprocess.CalledProcessError(
                result.returncode,
                _redact_prompt_arg(result.args),
                output=result.stdout,
                stderr=result.stderr,
            )

        if index < len(providers) - 1:
            logger.warning(
                "%s hit a usage limit reviewing %s PR #%s; falling back to %s",
                provider.title(), repo_slug, pr_id, providers[index + 1].title(),
            )
            continue

        logger.error(
            "%s exhausted its usage limit reviewing %s PR #%s and no fallback remains",
            provider.title(), repo_slug, pr_id,
        )
        raise subprocess.CalledProcessError(
            result.returncode or 1,
            _redact_prompt_arg(result.args),
            output=result.stdout,
            stderr=result.stderr,
        )

    raise AssertionError("Validated provider order cannot be empty")
