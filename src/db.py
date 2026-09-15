import sqlite3
from pathlib import Path

DEFAULT_PROVIDER_ORDER = ("claude", "codex", "kiro")


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
                outcome          TEXT NOT NULL DEFAULT 'unknown',
                PRIMARY KEY (repo_slug, pr_id)
            )
            """
        )
        try:
            conn.execute("ALTER TABLE reviewed_prs ADD COLUMN last_commit_hash TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE reviewed_prs ADD COLUMN outcome TEXT NOT NULL DEFAULT 'unknown'")
        except sqlite3.OperationalError:
            pass
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_provider_order(db_path: str) -> list[str]:
    """Return the persisted reviewer priority, or the safe default for new installs."""
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT value FROM app_settings WHERE key = 'provider_order'").fetchone()
        if not row:
            return list(DEFAULT_PROVIDER_ORDER)
        providers = [provider.strip() for provider in row[0].split(",") if provider.strip()]
        if set(providers) != set(DEFAULT_PROVIDER_ORDER) or len(providers) != len(DEFAULT_PROVIDER_ORDER):
            return list(DEFAULT_PROVIDER_ORDER)
        return providers
    finally:
        conn.close()


def set_provider_order(db_path: str, providers: list[str]) -> None:
    """Persist a complete, ordered list of the supported review providers."""
    if set(providers) != set(DEFAULT_PROVIDER_ORDER) or len(providers) != len(DEFAULT_PROVIDER_ORDER):
        raise ValueError("Provider order must contain Claude, Codex, and Kiro exactly once.")
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('provider_order', ?)",
            (",".join(providers),),
        )
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


def mark_reviewed(
    db_path: str, repo_slug: str, pr_id: int, reviewed_at: str, commit_hash: str, outcome: str = "unknown"
) -> None:
    """Record that a PR was reviewed. `outcome` is 'approved', 'changes_requested', or 'unknown'."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO reviewed_prs (repo_slug, pr_id, reviewed_at, last_commit_hash, outcome) "
            "VALUES (?, ?, ?, ?, ?)",
            (repo_slug, pr_id, reviewed_at, commit_hash, outcome),
        )
        conn.commit()
    finally:
        conn.close()


def list_recent_reviews(db_path: str, limit: int = 20, offset: int = 0) -> list[dict]:
    """Reviewed PRs, newest first, for display on the web dashboard.

    Supports paging arbitrarily far back via offset — bounded only by how
    much history is retained in the reviewed_prs table.
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT repo_slug, pr_id, reviewed_at, last_commit_hash, outcome FROM reviewed_prs "
            "ORDER BY reviewed_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_reviews(db_path: str) -> int:
    """Total number of reviewed PRs on record, for computing page count."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT COUNT(*) FROM reviewed_prs").fetchone()
        return row[0] if row else 0
    finally:
        conn.close()
