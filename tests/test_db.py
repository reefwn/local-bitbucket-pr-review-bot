from src.db import init_db, is_reviewed, mark_reviewed


def test_is_reviewed_false_before_marking(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    assert is_reviewed(db_path, "my-repo", 1) is False


def test_mark_reviewed_then_is_reviewed_true(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T00:00:00+00:00")
    assert is_reviewed(db_path, "my-repo", 1) is True


def test_mark_reviewed_is_idempotent(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T00:00:00+00:00")
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T01:00:00+00:00")
    assert is_reviewed(db_path, "my-repo", 1) is True


def test_reviewed_prs_scoped_by_repo_and_id(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "repo-a", 1, "2026-09-04T00:00:00+00:00")
    assert is_reviewed(db_path, "repo-b", 1) is False
    assert is_reviewed(db_path, "repo-a", 2) is False


def test_init_db_creates_parent_directory(tmp_path):
    db_path = str(tmp_path / "nested" / "dir" / "reviewed.db")
    init_db(db_path)
    assert is_reviewed(db_path, "my-repo", 1) is False
