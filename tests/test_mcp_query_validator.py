"""Tests for questy.mcp.server.QueryValidator — SQL query validation."""

from __future__ import annotations

import pytest

from questy.mcp.server import QueryValidator


class TestValidateQuery:
    def test_valid_select(self):
        QueryValidator.validate_query("SELECT * FROM reports")

    def test_valid_select_lowercase(self):
        QueryValidator.validate_query("select * from reports")

    def test_valid_with_cte(self):
        QueryValidator.validate_query(
            "WITH latest AS (SELECT MAX(report_date) as d FROM reports) "
            "SELECT * FROM results WHERE report_date = (SELECT d FROM latest)"
        )

    def test_empty_query_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            QueryValidator.validate_query("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            QueryValidator.validate_query("   ")

    def test_insert_rejected(self):
        with pytest.raises(ValueError, match="Only"):
            QueryValidator.validate_query("INSERT INTO reports VALUES (1)")

    def test_update_rejected(self):
        with pytest.raises(ValueError, match="Only"):
            QueryValidator.validate_query("UPDATE reports SET age = 30")

    def test_delete_rejected(self):
        with pytest.raises(ValueError, match="Only"):
            QueryValidator.validate_query("DELETE FROM reports")

    def test_drop_rejected(self):
        with pytest.raises(ValueError, match="Only"):
            QueryValidator.validate_query("DROP TABLE reports")

    def test_forbidden_keyword_in_select(self):
        with pytest.raises(ValueError, match="Forbidden keywords"):
            QueryValidator.validate_query("SELECT * FROM reports; DELETE FROM reports")

    def test_pragma_statement_forbidden(self):
        with pytest.raises(ValueError, match="Only"):
            QueryValidator.validate_query("PRAGMA table_info('reports')")

    def test_pragma_function_in_select_allowed(self):
        """pragma_table_info() as a function inside SELECT is read-only and safe."""
        QueryValidator.validate_query("SELECT * FROM pragma_table_info('reports')")

    def test_attach_forbidden(self):
        with pytest.raises(ValueError, match="Only"):
            QueryValidator.validate_query("ATTACH DATABASE '/tmp/evil.db' AS evil")

    def test_semicolon_in_string_ok(self):
        """Semicolons inside quoted strings should be fine."""
        QueryValidator.validate_query("SELECT * FROM results WHERE test_name = 'A;B'")

    def test_multiple_statements_rejected(self):
        with pytest.raises(ValueError):
            QueryValidator.validate_query("SELECT 1; SELECT 2")


class TestContainsMultipleStatements:
    def test_single_statement(self):
        assert QueryValidator._contains_multiple_statements("SELECT * FROM reports") is False

    def test_multiple_statements(self):
        assert QueryValidator._contains_multiple_statements("SELECT 1; SELECT 2") is True

    def test_semicolon_in_single_quotes(self):
        assert QueryValidator._contains_multiple_statements("SELECT * WHERE name = 'a;b'") is False

    def test_semicolon_in_double_quotes(self):
        assert QueryValidator._contains_multiple_statements('SELECT * WHERE name = "a;b"') is False


class TestAddRowLimit:
    def test_adds_limit(self):
        result = QueryValidator.add_row_limit("SELECT * FROM reports", 500)
        assert result.endswith("LIMIT 500")

    def test_preserves_existing_limit(self):
        query = "SELECT * FROM reports LIMIT 10"
        result = QueryValidator.add_row_limit(query, 500)
        assert result == query  # Unchanged

    def test_strips_trailing_semicolon(self):
        result = QueryValidator.add_row_limit("SELECT * FROM reports;", 100)
        assert result.endswith("LIMIT 100")
        assert ";" not in result
