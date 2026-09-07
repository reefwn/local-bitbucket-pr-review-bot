from src.db import count_reviews, init_db, is_reviewed, list_recent_reviews, mark_reviewed


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


def test_list_recent_reviews_empty_before_any_review(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    assert list_recent_reviews(db_path) == []


def test_list_recent_reviews_newest_first(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "repo-a", 1, "2026-09-04T00:00:00+00:00", "hash-a")
    mark_reviewed(db_path, "repo-b", 2, "2026-09-04T01:00:00+00:00", "hash-b")
    reviews = list_recent_reviews(db_path)
    assert [r["repo_slug"] for r in reviews] == ["repo-b", "repo-a"]
    assert reviews[0]["pr_id"] == 2
    assert reviews[0]["reviewed_at"] == "2026-09-04T01:00:00+00:00"


def test_list_recent_reviews_respects_limit(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    for i in range(5):
        mark_reviewed(db_path, "repo-a", i, f"2026-09-04T0{i}:00:00+00:00", f"hash-{i}")
    assert len(list_recent_reviews(db_path, limit=3)) == 3


def test_list_recent_reviews_supports_offset_for_paging(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    for i in range(15):
        mark_reviewed(db_path, "repo-a", i, f"2026-09-04T{i:02d}:00:00+00:00", f"hash-{i}")
    page_1 = list_recent_reviews(db_path, limit=10, offset=0)
    page_2 = list_recent_reviews(db_path, limit=10, offset=10)
    assert [r["pr_id"] for r in page_1] == list(range(14, 4, -1))
    assert [r["pr_id"] for r in page_2] == list(range(4, -1, -1))


def test_count_reviews(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    assert count_reviews(db_path) == 0
    for i in range(4):
        mark_reviewed(db_path, "repo-a", i, f"2026-09-04T0{i}:00:00+00:00", f"hash-{i}")
    assert count_reviews(db_path) == 4
