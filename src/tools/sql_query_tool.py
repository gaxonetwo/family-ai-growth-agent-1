"""Read-only SQL query tool for the Family AI demo database.

This is a small, guarded foundation for later Agent tool-calling. It is not
intended to expose arbitrary SQL execution or production customer data.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "data" / "processed" / "family_ai.db"
ALLOWED_TABLES = {"users", "devices", "events", "feedback", "payments"}


class SQLQueryError(ValueError):
    """Raised when a query is invalid or not permitted."""


class SQLQueryTool:
    """Execute SELECT-only queries against approved business tables."""

    def __init__(
        self,
        db_path: str | Path = DEFAULT_DB_PATH,
        max_rows: int = 200,
        timeout_seconds: int = 5,
    ) -> None:
        self.db_path = Path(db_path).resolve()
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds

        if not self.db_path.exists():
            raise FileNotFoundError(
                f"SQLite database not found: {self.db_path}. "
                "Run the Phase 1 data pipeline first."
            )

    @staticmethod
    def _authorizer(action, arg1, arg2, db_name, trigger_name):
        """SQLite-level protection: permit reads only from approved tables."""
        read_action = getattr(sqlite3, "SQLITE_READ", -1)
        select_action = getattr(sqlite3, "SQLITE_SELECT", -2)
        function_action = getattr(sqlite3, "SQLITE_FUNCTION", -3)
        recursive_action = getattr(sqlite3, "SQLITE_RECURSIVE", -4)

        if action in {select_action, function_action, recursive_action}:
            return sqlite3.SQLITE_OK
        if action == read_action:
            return sqlite3.SQLITE_OK if arg1 in ALLOWED_TABLES else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY

    @staticmethod
    def _clean_query(sql: str) -> str:
        if not isinstance(sql, str) or not sql.strip():
            raise SQLQueryError("SQL query cannot be empty.")

        query = sql.strip()
        # Permit one optional trailing semicolon, but not multiple statements.
        if query.endswith(";"):
            query = query[:-1].rstrip()
        if ";" in query:
            raise SQLQueryError("Only one SQL statement is allowed per call.")

        # SQLite authorizer below is the real security boundary. This early
        # check gives learners a clearer message for common write statements.
        first_token = query.lstrip().split(None, 1)[0].lower()
        if first_token not in {"select", "with"}:
            raise SQLQueryError("Only SELECT queries (including WITH ... SELECT) are allowed.")
        return query

    def run(self, sql: str) -> dict[str, Any]:
        """Run a read-only query and return rows plus execution metadata."""
        query = self._clean_query(sql)
        # Path.as_uri() produces a valid file URI on both Windows and POSIX
        # and safely escapes spaces or other special path characters.
        uri = f"{self.db_path.as_uri()}?mode=ro"

        conn = sqlite3.connect(uri, uri=True, timeout=self.timeout_seconds)
        conn.row_factory = sqlite3.Row
        try:
            conn.set_authorizer(self._authorizer)
            # Stop unusually expensive queries after a bounded amount of work.
            vm_steps = 0
            max_vm_steps = 2_000_000

            def progress_handler():
                nonlocal vm_steps
                vm_steps += 1_000
                return 1 if vm_steps > max_vm_steps else 0

            conn.set_progress_handler(progress_handler, 1_000)

            # Wrapping the query enforces a row cap without changing aggregates.
            wrapped = f"SELECT * FROM ({query}) AS agent_query LIMIT {int(self.max_rows) + 1}"
            cursor = conn.execute(wrapped)
            fetched = cursor.fetchall()
            truncated = len(fetched) > self.max_rows
            fetched = fetched[: self.max_rows]
            columns = [item[0] for item in cursor.description] if cursor.description else []
            rows = [dict(row) for row in fetched]

            return {
                "ok": True,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
            }
        except sqlite3.DatabaseError as exc:
            message = str(exc)
            if "not authorized" in message.lower():
                message = "Query blocked: only read access to approved business tables is allowed."
            raise SQLQueryError(message) from exc
        finally:
            conn.close()


def query_business_data(sql: str) -> dict[str, Any]:
    """Convenience function for later Agent tool registration."""
    return SQLQueryTool().run(sql)
