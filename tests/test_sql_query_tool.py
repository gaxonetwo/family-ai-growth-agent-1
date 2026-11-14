from pathlib import Path
import pytest

from src.tools.sql_query_tool import SQLQueryError, SQLQueryTool

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "processed" / "family_ai.db"


@pytest.fixture
def tool():
    if not DB.exists():
        pytest.skip("Generate the Phase 1 database before running tests.")
    return SQLQueryTool(DB)


def test_select_count_users(tool):
    result = tool.run("SELECT COUNT(*) AS n FROM users")
    assert result["ok"] is True
    assert result["rows"][0]["n"] == 1000


def test_group_by_event_name(tool):
    result = tool.run(
        "SELECT event_name, COUNT(*) AS n FROM events GROUP BY event_name"
    )
    assert result["row_count"] > 0
    assert "event_name" in result["columns"]


def test_reject_delete(tool):
    with pytest.raises(SQLQueryError):
        tool.run("DELETE FROM users")


def test_reject_multiple_statements(tool):
    with pytest.raises(SQLQueryError):
        tool.run("SELECT * FROM users; DELETE FROM users")


def test_reject_reading_ground_truth_or_system_tables(tool):
    with pytest.raises(SQLQueryError):
        tool.run("SELECT name FROM sqlite_master")


def test_row_limit(tool):
    result = tool.run("SELECT * FROM events")
    assert result["row_count"] <= 200
    assert result["truncated"] is True
