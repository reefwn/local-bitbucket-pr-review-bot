import sqlite3
from pathlib import Path


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reviewed_prs (
                repo_slug        TEXT NOT NULL,
                pr_id            INTEGER NOT NULL,
                reviewed_at      TEXT NOT NULL,
                last_commit_hash TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (repo_slug, pr_id)
            )
            """
        )
        try:
            conn.execute("ALTER TABLE reviewed_prs ADD COLUMN last_commit_hash TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()


def is_reviewed(db_path: str, repo_slug: str, pr_id: int, commit_hash: str) -> bool:
    """Whether this PR was already reviewed at its current commit hash."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM reviewed_prs WHERE repo_slug = ? AND pr_id = ? AND last_commit_hash = ?",
            (repo_slug, pr_id, commit_hash),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_reviewed(db_path: str, repo_slug: str, pr_id: int, reviewed_at: str, commit_hash: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO reviewed_prs (repo_slug, pr_id, reviewed_at, last_commit_hash) "
            "VALUES (?, ?, ?, ?)",
            (repo_slug, pr_id, reviewed_at, commit_hash),
        )
        conn.commit()
    finally:
        conn.close()


def list_recent_reviews(db_path: str, limit: int = 20) -> list[dict]:
    """Most recently reviewed PRs, newest first, for display on the web dashboard."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT repo_slug, pr_id, reviewed_at, last_commit_hash FROM reviewed_prs "
            "ORDER BY reviewed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
