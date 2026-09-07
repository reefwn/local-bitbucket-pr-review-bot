from src.db import init_db, is_reviewed, mark_reviewed


def test_is_reviewed_false_before_marking(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    assert is_reviewed(db_path, "my-repo", 1, "hash-a") is False


def test_mark_reviewed_then_is_reviewed_true(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T00:00:00+00:00", "hash-a")
    assert is_reviewed(db_path, "my-repo", 1, "hash-a") is True


def test_mark_reviewed_is_idempotent(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T00:00:00+00:00", "hash-a")
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T01:00:00+00:00", "hash-a")
    assert is_reviewed(db_path, "my-repo", 1, "hash-a") is True


def test_reviewed_prs_scoped_by_repo_and_id(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "repo-a", 1, "2026-09-04T00:00:00+00:00", "hash-a")
    assert is_reviewed(db_path, "repo-b", 1, "hash-a") is False
    assert is_reviewed(db_path, "repo-a", 2, "hash-a") is False


def test_init_db_creates_parent_directory(tmp_path):
    db_path = str(tmp_path / "nested" / "dir" / "reviewed.db")
    init_db(db_path)
    assert is_reviewed(db_path, "my-repo", 1, "hash-a") is False


def test_is_reviewed_false_after_commit_hash_changes(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T00:00:00+00:00", "hash-a")
    assert is_reviewed(db_path, "my-repo", 1, "hash-b") is False


def test_mark_reviewed_updates_commit_hash_on_new_push(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T00:00:00+00:00", "hash-a")
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T01:00:00+00:00", "hash-b")
    assert is_reviewed(db_path, "my-repo", 1, "hash-a") is False
    assert is_reviewed(db_path, "my-repo", 1, "hash-b") is True


def test_init_db_migrates_existing_db_missing_commit_hash_column(tmp_path):
    import sqlite3

    db_path = str(tmp_path / "reviewed.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE reviewed_prs (
            repo_slug   TEXT NOT NULL,
            pr_id       INTEGER NOT NULL,
            reviewed_at TEXT NOT NULL,
            PRIMARY KEY (repo_slug, pr_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO reviewed_prs (repo_slug, pr_id, reviewed_at) VALUES (?, ?, ?)",
        ("my-repo", 1, "2026-09-04T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    init_db(db_path)

    assert is_reviewed(db_path, "my-repo", 1, "") is True
    assert is_reviewed(db_path, "my-repo", 1, "hash-a") is False
