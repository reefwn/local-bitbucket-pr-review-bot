from datetime import datetime, timezone

from src.bot.poller import filter_open, filter_recent, filter_unreviewed
from src.db import init_db, mark_reviewed


def test_filter_open_keeps_only_open():
    prs = [{"id": 1, "state": "OPEN"}, {"id": 2, "state": "MERGED"}, {"id": 3, "state": "DECLINED"}]
    result = filter_open(prs)
    assert [pr["id"] for pr in result] == [1]


def test_filter_recent_keeps_prs_inside_window():
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    prs = [
        {"id": 1, "created_on": "2026-09-04T11:55:00.000000Z"},  # 5 min ago - inside
        {"id": 2, "created_on": "2026-09-04T11:30:00.000000Z"},  # 30 min ago - outside
    ]
    result = filter_recent(prs, now, window_minutes=10)
    assert [pr["id"] for pr in result] == [1]


def test_filter_recent_boundary_is_inclusive():
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    prs = [{"id": 1, "created_on": "2026-09-04T11:50:00.000000Z"}]  # exactly 10 min ago
    result = filter_recent(prs, now, window_minutes=10)
    assert [pr["id"] for pr in result] == [1]


def test_filter_unreviewed_skips_already_reviewed(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T00:00:00+00:00")
    prs = [{"id": 1}, {"id": 2}]
    result = filter_unreviewed(prs, db_path, "my-repo")
    assert [pr["id"] for pr in result] == [2]
