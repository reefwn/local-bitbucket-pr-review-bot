import sqlite3
from pathlib import Path


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reviewed_prs (
                repo_slug   TEXT NOT NULL,
                pr_id       INTEGER NOT NULL,
                reviewed_at TEXT NOT NULL,
                PRIMARY KEY (repo_slug, pr_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def is_reviewed(db_path: str, repo_slug: str, pr_id: int) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM reviewed_prs WHERE repo_slug = ? AND pr_id = ?",
            (repo_slug, pr_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_reviewed(db_path: str, repo_slug: str, pr_id: int, reviewed_at: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO reviewed_prs (repo_slug, pr_id, reviewed_at) VALUES (?, ?, ?)",
            (repo_slug, pr_id, reviewed_at),
        )
        conn.commit()
    finally:
        conn.close()
