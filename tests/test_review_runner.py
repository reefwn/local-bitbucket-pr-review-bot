import json
import subprocess
from unittest.mock import MagicMock, patch

from src.review_runner import build_prompt, run_review, write_mcp_config


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


def test_run_review_invokes_claude_with_expected_args(tmp_path):
    config_path = str(tmp_path / "mcp-config.json")
    fake_result = MagicMock(stdout=json.dumps({"result": "posted comment"}))
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
    fake_result = MagicMock(stdout=json.dumps({"result": "posted comment"}))
    with patch("subprocess.run", return_value=fake_result) as mock_run:
        run_review("my-repo", 42, "", config_path)
    assert mock_run.call_args[1]["timeout"] == 600


def test_run_review_propagates_failure(tmp_path):
    config_path = str(tmp_path / "mcp-config.json")
    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, ["claude"]),
    ):
        try:
            run_review("my-repo", 42, "", config_path)
            assert False, "expected CalledProcessError"
        except subprocess.CalledProcessError:
            pass
