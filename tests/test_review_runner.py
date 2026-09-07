import json
import subprocess
from unittest.mock import MagicMock, patch

from src.review_runner import build_prompt, run_review, write_kiro_mcp_config, write_mcp_config


def _claude_result(stdout="", stderr="", returncode=0, args=None):
    return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode, args=args or [])


def test_build_prompt_includes_repo_and_pr():
    prompt = build_prompt("my-repo", 42, "")
    assert "my-repo" in prompt
    assert "#42" in prompt


def test_build_prompt_includes_existing_comments():
    prompt = build_prompt("my-repo", 42, "[Alice] please add tests")
    assert "[Alice] please add tests" in prompt


def test_build_prompt_includes_review_dimensions_and_output_structure():
    prompt = build_prompt("my-repo", 42, "")
    for dimension in ("Silent failures", "Test coverage", "Comment accuracy", "Simplification"):
        assert dimension in prompt
    for section in ("## Critical Issues", "## Important Issues", "## Suggestions", "## Strengths"):
        assert section in prompt


def test_build_prompt_no_comments_placeholder():
    prompt = build_prompt("my-repo", 42, "")
    assert "(none)" in prompt


def test_build_prompt_instructs_approve_or_request_changes():
    prompt = build_prompt("my-repo", 42, "")
    assert "bitbucket_approve_pr" in prompt
    assert "bitbucket_request_changes_pr" in prompt
    assert "Never decline or close the PR" in prompt


def test_write_mcp_config(tmp_path):
    path = str(tmp_path / "mcp-config.json")
    write_mcp_config(path, "http://mcp:7390/mcp")
    with open(path) as f:
        config = json.load(f)
    assert config == {
        "mcpServers": {
            "bitbucket-pr": {"type": "http", "url": "http://mcp:7390/mcp"}
        }
    }


def test_write_kiro_mcp_config(tmp_path):
    path = str(tmp_path / "kiro-mcp-config.json")
    write_kiro_mcp_config(path, "http://mcp:7390/mcp")
    with open(path) as f:
        config = json.load(f)
    assert config == {
        "mcpServers": {
            "bitbucket-pr": {"url": "http://mcp:7390/mcp", "disabled": False}
        }
    }


def test_run_review_invokes_claude_with_expected_args(tmp_path):
    config_path = str(tmp_path / "mcp-config.json")
    fake_result = _claude_result(stdout=json.dumps({"result": "posted comment"}))
    with patch("subprocess.run", return_value=fake_result) as mock_run:
        result = run_review("my-repo", 42, "", config_path)
    assert result == {"result": "posted comment"}
    args = mock_run.call_args[0][0]
    assert args[0] == "claude"
    assert "-p" in args
    assert "--mcp-config" in args
    assert config_path in args
    assert "--allowedTools" in args
    allowed_tools = args[args.index("--allowedTools") + 1]
    assert "mcp__bitbucket-pr__bitbucket_create_pr_comment" in allowed_tools
    assert "mcp__bitbucket-pr__bitbucket_approve_pr" in allowed_tools
    assert "mcp__bitbucket-pr__bitbucket_request_changes_pr" in allowed_tools
    assert "--output-format" in args
    assert "json" in args


def test_run_review_passes_timeout(tmp_path):
    config_path = str(tmp_path / "mcp-config.json")
    fake_result = _claude_result(stdout=json.dumps({"result": "posted comment"}))
    with patch("subprocess.run", return_value=fake_result) as mock_run:
        run_review("my-repo", 42, "", config_path)
    assert mock_run.call_args[1]["timeout"] == 600


def test_run_review_propagates_generic_failure(tmp_path):
    """A non-zero exit with no usage-limit phrasing is a generic failure, not a fallback trigger."""
    config_path = str(tmp_path / "mcp-config.json")
    fake_result = _claude_result(stdout="", stderr="tool crashed unexpectedly", returncode=1)
    with patch("subprocess.run", return_value=fake_result):
        try:
            run_review("my-repo", 42, "", config_path)
            assert False, "expected CalledProcessError"
        except subprocess.CalledProcessError:
            pass


def test_run_review_falls_back_to_kiro_on_usage_limit_exit_code(tmp_path):
    """Non-zero exit + limit phrasing in stderr triggers the Kiro fallback."""
    config_path = str(tmp_path / "mcp-config.json")
    claude_result = _claude_result(stdout="", stderr="Error: rate limit reached for this account", returncode=1)
    kiro_result = _claude_result(stdout="Reviewed and approved PR #42", stderr="", returncode=0)
    with patch("subprocess.run", side_effect=[claude_result, kiro_result]) as mock_run:
        result = run_review("my-repo", 42, "", config_path)
    assert result == {"result": "Reviewed and approved PR #42", "agent": "kiro"}
    assert mock_run.call_count == 2
    kiro_args = mock_run.call_args_list[1][0][0]
    assert kiro_args[0] == "kiro-cli"
    assert kiro_args[1] == "chat"
    assert "--agent" in kiro_args
    assert "pr-reviewer" in kiro_args
    assert "--no-interactive" in kiro_args


def test_run_review_falls_back_to_kiro_on_usage_limit_in_successful_result(tmp_path):
    """Exit 0 but the JSON result text itself reports a hit usage limit."""
    config_path = str(tmp_path / "mcp-config.json")
    claude_result = _claude_result(
        stdout=json.dumps({"result": "You've hit your session limit. Try again later."}), returncode=0
    )
    kiro_result = _claude_result(stdout="Reviewed and requested changes on PR #42", returncode=0)
    with patch("subprocess.run", side_effect=[claude_result, kiro_result]) as mock_run:
        result = run_review("my-repo", 42, "", config_path)
    assert result == {"result": "Reviewed and requested changes on PR #42", "agent": "kiro"}
    assert mock_run.call_count == 2


def test_run_review_propagates_kiro_failure_after_claude_usage_limit(tmp_path):
    """If Claude hits its limit and the Kiro fallback also fails, the failure propagates."""
    config_path = str(tmp_path / "mcp-config.json")
    claude_result = _claude_result(stdout="", stderr="usage limit exceeded", returncode=1)
    kiro_result = _claude_result(stdout="", stderr="kiro auth expired", returncode=1)
    with patch("subprocess.run", side_effect=[claude_result, kiro_result]):
        try:
            run_review("my-repo", 42, "", config_path)
            assert False, "expected CalledProcessError"
        except subprocess.CalledProcessError:
            pass


def test_run_review_redacts_long_prompt_arg_in_generic_failure(tmp_path):
    """The raised CalledProcessError should not embed the full (potentially huge) prompt text."""
    config_path = str(tmp_path / "mcp-config.json")
    long_comment = "x" * 5000
    prompt = build_prompt("my-repo", 42, long_comment)
    fake_result = _claude_result(
        stdout="", stderr="tool crashed unexpectedly", returncode=1,
        args=["claude", "-p", prompt, "--mcp-config", config_path],
    )
    with patch("subprocess.run", return_value=fake_result):
        try:
            run_review("my-repo", 42, long_comment, config_path)
            assert False, "expected CalledProcessError"
        except subprocess.CalledProcessError as e:
            args_str = " ".join(e.cmd)
            assert long_comment not in args_str
            assert "<prompt," in args_str


def test_run_review_redacts_long_prompt_arg_in_kiro_failure(tmp_path):
    """Same redaction should apply to the Kiro fallback's own failure."""
    config_path = str(tmp_path / "mcp-config.json")
    long_comment = "y" * 5000
    prompt = build_prompt("my-repo", 42, long_comment)
    claude_result = _claude_result(
        stdout="", stderr="usage limit exceeded", returncode=1,
        args=["claude", "-p", prompt, "--mcp-config", config_path],
    )
    kiro_result = _claude_result(
        stdout="", stderr="kiro auth expired", returncode=1,
        args=["kiro-cli", "chat", prompt, "--agent", "pr-reviewer"],
    )
    with patch("subprocess.run", side_effect=[claude_result, kiro_result]):
        try:
            run_review("my-repo", 42, long_comment, config_path)
            assert False, "expected CalledProcessError"
        except subprocess.CalledProcessError as e:
            args_str = " ".join(e.cmd)
            assert long_comment not in args_str
            assert "<prompt," in args_str
