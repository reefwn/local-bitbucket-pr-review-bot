import json
import logging
import re
import subprocess

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


def write_kiro_mcp_config(path: str, mcp_url: str) -> None:
    """Write the MCP config file Kiro CLI needs to reach the bundled Bitbucket-PR MCP server.

    Written as a standalone mcp.json (Kiro's `--mcp-config` equivalent is
    scoped via the `pr-reviewer` agent config, but the agent's own
    `mcpServers.bitbucket-pr.url` uses `${MCP_URL}` expansion — this file is
    kept for parity with Claude's config and for tooling/tests that expect a
    materialized config file on disk).
    """
    config = {"mcpServers": {"bitbucket-pr": {"url": mcp_url, "disabled": False}}}
    with open(path, "w") as f:
        json.dump(config, f)


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


def run_review(
    repo_slug: str,
    pr_id: int,
    existing_comments: str,
    mcp_config_path: str,
    kiro_agent_name: str = KIRO_AGENT_NAME,
) -> dict:
    """Run headless Claude to review and comment on a PR, falling back to Kiro CLI
    if Claude reports a usage-limit failure. Returns the parsed JSON result
    (or, when the Kiro fallback ran, a dict wrapping its plain-text output).

    Raises subprocess.CalledProcessError if the invocation fails for a reason
    other than an exhausted Claude usage quota, or if the Kiro fallback itself
    fails.
    """
    prompt = build_prompt(repo_slug, pr_id, existing_comments)
    result = _run_claude(prompt, mcp_config_path)
    usage_limit_hit = _is_usage_limit_failure(result.returncode, result.stdout, result.stderr)

    if result.returncode == 0 and not usage_limit_hit:
        return json.loads(result.stdout)

    if not usage_limit_hit:
        # Generic failure — surface it the same way subprocess.run(check=True) would.
        raise subprocess.CalledProcessError(
            result.returncode, result.args, output=result.stdout, stderr=result.stderr
        )

    logger.warning(
        "Claude hit a usage limit reviewing %s PR #%s; falling back to Kiro CLI", repo_slug, pr_id
    )
    kiro_result = _run_kiro(prompt, kiro_agent_name)
    if kiro_result.returncode != 0:
        raise subprocess.CalledProcessError(
            kiro_result.returncode, kiro_result.args, output=kiro_result.stdout, stderr=kiro_result.stderr
        )
    return {"result": kiro_result.stdout, "agent": "kiro"}
