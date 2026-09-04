import json
import subprocess

_ALLOWED_TOOLS = (
    "mcp__bitbucket-pr__bitbucket_get_pr,"
    "mcp__bitbucket-pr__bitbucket_get_pr_diff,"
    "mcp__bitbucket-pr__bitbucket_list_pr_comments,"
    "mcp__bitbucket-pr__bitbucket_create_pr_comment"
)


def build_prompt(repo_slug: str, pr_id: int, existing_comments: str) -> str:
    """Build the headless review prompt for a single PR."""
    comments_section = existing_comments.strip() or "(none)"
    return (
        f"Review pull request #{pr_id} in the '{repo_slug}' Bitbucket repository. "
        "Fetch its diff and existing comments using the available Bitbucket tools, "
        "then post a single review comment via bitbucket_create_pr_comment.\n\n"
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


def run_review(repo_slug: str, pr_id: int, existing_comments: str, mcp_config_path: str) -> dict:
    """Run headless Claude to review and comment on a PR. Returns the parsed JSON result.

    Raises subprocess.CalledProcessError if the claude invocation fails.
    """
    prompt = build_prompt(repo_slug, pr_id, existing_comments)
    result = subprocess.run(
        [
            "claude", "-p", prompt,
            "--mcp-config", mcp_config_path,
            "--allowedTools", _ALLOWED_TOOLS,
            "--output-format", "json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=600,
    )
    return json.loads(result.stdout)
