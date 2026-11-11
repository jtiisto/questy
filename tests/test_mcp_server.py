"""Tests for questy.mcp.server — MCP tools with a real in-memory database."""

from __future__ import annotations

import pytest

from questy.localdb.db import QuestyDB
from questy.mcp.config import MCPConfig
from questy.mcp.server import DatabaseManager, SQLiteConnection, create_mcp_server
from tests.mcp_tools import extract_tools


@pytest.fixture
def populated_db(tmp_path, multi_result_report):
    """Create a database with test data."""
    db_path = tmp_path / "test.db"
    db = QuestyDB(db_path)
    db.import_report(multi_result_report, pdf_path="/tmp/test.pdf")
    return db_path


@pytest.fixture
def config(populated_db):
    return MCPConfig(db_path=populated_db)


@pytest.fixture
def db_manager(config):
    return DatabaseManager(config)


class TestSQLiteConnection:
    def test_readonly_connection(self, populated_db):
        with SQLiteConnection(populated_db) as conn:
            cursor = conn.cursor()
            rows = cursor.execute("SELECT COUNT(*) FROM reports").fetchone()
            assert rows[0] == 1

    def test_write_blocked(self, populated_db):
        """Read-only connection should block writes."""
        import sqlite3

        with SQLiteConnection(populated_db) as conn:
            cursor = conn.cursor()
            with pytest.raises(sqlite3.OperationalError):
                cursor.execute("INSERT INTO reports (report_date) VALUES ('2025-01-01')")


class TestDatabaseManager:
    def test_execute_safe_query(self, db_manager):
        rows = db_manager.execute_safe_query("SELECT COUNT(*) as cnt FROM reports")
        assert rows[0]["cnt"] == 1

    def test_execute_with_params(self, db_manager):
        rows = db_manager.execute_safe_query(
            "SELECT test_name FROM results WHERE flag = ?", ["H"]
        )
        test_names = [r["test_name"] for r in rows]
        assert "CHOLESTEROL, TOTAL" in test_names

    def test_forbidden_query_rejected(self, db_manager):
        with pytest.raises(ValueError):
            db_manager.execute_safe_query("DROP TABLE reports")

    def test_row_limit_applied(self, db_manager):
        rows = db_manager.execute_safe_query("SELECT * FROM results")
        # Should have LIMIT added but our data is small so all rows returned
        assert len(rows) <= 1000


class TestMCPServerTools:
    """Test the queries the MCP tools run, through DatabaseManager."""

    def test_explore_database_structure(self, config):
        db_mgr = DatabaseManager(config)
        tables = db_mgr.execute_safe_query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = [r["name"] for r in tables]
        assert "reports" in table_names
        assert "results" in table_names
        assert "notes" in table_names

    def test_report_summary_queries(self, db_manager):
        overview = db_manager.execute_safe_query("""
            SELECT COUNT(*) as total_reports,
                   MIN(report_date) as first_report,
                   MAX(report_date) as latest_report
            FROM reports
        """)
        assert overview[0]["total_reports"] == 1

    def test_test_trends_query(self, db_manager):
        rows = db_manager.execute_safe_query("""
            SELECT report_date, value, value_text, flag, unit
            FROM results
            WHERE UPPER(test_name) = UPPER(?)
            ORDER BY report_date ASC
        """, ["CHOLESTEROL, TOTAL"])
        assert len(rows) == 1
        assert rows[0]["value"] == 229.0

    def test_panel_results_query(self, db_manager):
        rows = db_manager.execute_safe_query("""
            SELECT panel_name, test_name, value, flag
            FROM results
            WHERE report_date = (SELECT MAX(report_date) FROM reports)
            ORDER BY panel_name, test_name
        """)
        panels = {r["panel_name"] for r in rows}
        assert "LIPID PANEL, STANDARD" in panels
        assert "CBC (INCLUDES DIFF/PLT)" in panels

    def test_out_of_range_query(self, db_manager):
        rows = db_manager.execute_safe_query("""
            SELECT test_name, value_text, flag
            FROM results
            WHERE report_date = (SELECT MAX(report_date) FROM reports)
              AND flag IS NOT NULL
            ORDER BY test_name
        """)
        flagged = [r["test_name"] for r in rows]
        assert "CHOLESTEROL, TOTAL" in flagged
        assert "LDL-CHOLESTEROL" in flagged

    def test_available_tests_query(self, db_manager):
        rows = db_manager.execute_safe_query("""
            SELECT test_name, unit, COUNT(*) as cnt
            FROM results
            GROUP BY test_name, unit
            ORDER BY test_name
        """)
        assert len(rows) == 5

    def test_notes_query(self, db_manager):
        rows = db_manager.execute_safe_query("SELECT * FROM notes ORDER BY id")
        assert len(rows) == 2
        types = [r["note_type"] for r in rows]
        assert "risk_category" in types
        assert "methodology" in types

    def test_custom_query_with_join(self, db_manager):
        rows = db_manager.execute_safe_query("""
            SELECT rep.patient_name, res.test_name, res.value
            FROM reports rep
            JOIN results res ON rep.report_date = res.report_date
            WHERE res.flag = 'H'
            ORDER BY res.test_name
        """)
        assert len(rows) >= 1
        assert all(r["patient_name"] == "DOE, JANE" for r in rows)


class TestMCPToolCalls:
    """Call every registered tool function, reached through the server's
    public tool listing, against the populated database."""

    @pytest.fixture
    def tools(self, config):
        return extract_tools(create_mcp_server(config))

    def test_lists_nine_tools(self, tools):
        assert len(tools) == 9

    def test_explore_database_structure(self, tools):
        result = tools["explore_database_structure"]()
        tables = result["available_tables"]
        assert set(tables) >= {"reports", "results", "notes"}
        assert tables["results"]["row_count"] == 5
        assert tables["results"]["date_range"] == {"earliest": "2025-04-11", "latest": "2025-04-11"}
        assert "usage_tip" in result

    def test_get_table_details(self, tools):
        result = tools["get_table_details"]("results")
        assert result["table_name"] == "results"
        assert {c["name"] for c in result["columns"]} >= {"test_name", "value", "unit", "flag"}
        assert len(result["sample_data"]) == 3

    def test_get_table_details_rejects_bad_names(self, tools):
        with pytest.raises(ValueError, match="Invalid table name"):
            tools["get_table_details"]("results; DROP TABLE reports")
        with pytest.raises(ValueError, match="does not exist"):
            tools["get_table_details"]("nope")

    def test_execute_sql_query(self, tools):
        rows = tools["execute_sql_query"](
            "SELECT test_name FROM results WHERE flag = ? ORDER BY test_name", ["H"]
        )
        assert [r["test_name"] for r in rows] == ["CHOLESTEROL, TOTAL", "LDL-CHOLESTEROL"]
        with pytest.raises(ValueError, match="cannot be empty"):
            tools["execute_sql_query"]("  ")

    def test_get_report_summary(self, tools):
        result = tools["get_report_summary"]()
        assert result["total_reports"] == 1
        assert result["date_range"] == {"first": "2025-04-11", "latest": "2025-04-11"}
        assert result["latest_report"]["patient_name"] == "DOE, JANE"
        assert [p["panel_name"] for p in result["latest_panels"]] == [
            "CBC (INCLUDES DIFF/PLT)", "COMPREHENSIVE METABOLIC PANEL", "LIPID PANEL, STANDARD",
        ]
        assert [r["test_name"] for r in result["latest_out_of_range"]] == [
            "CHOLESTEROL, TOTAL", "LDL-CHOLESTEROL",
        ]
        assert result["total_unique_tests"] == 5

    def test_get_test_trends(self, tools):
        result = tools["get_test_trends"]("ldl-cholesterol")
        assert result["data_points"] == 1
        assert result["unit"] == "mg/dL"
        assert result["trends"][0]["value"] == 152.0
        assert result["trends"][0]["flag"] == "H"

    def test_get_test_trends_unknown_name_suggests(self, tools):
        result = tools["get_test_trends"]("CHOLESTEROL")
        assert result["data_points"] == 0
        assert result["suggestions"] == ["CHOLESTEROL, TOTAL", "HDL CHOLESTEROL", "LDL-CHOLESTEROL"]
        with pytest.raises(ValueError, match="required"):
            tools["get_test_trends"]("")

    def test_get_panel_results(self, tools):
        result = tools["get_panel_results"]()
        assert result["report_date"] == "2025-04-11"
        assert result["panel_count"] == 3
        one = tools["get_panel_results"](panel_name="lipid panel, standard")
        assert list(one["panels"]) == ["LIPID PANEL, STANDARD"]
        assert len(one["panels"]["LIPID PANEL, STANDARD"]) == 3
        none = tools["get_panel_results"](report_date="2020-01-01")
        assert none["panel_count"] == 0

    def test_get_out_of_range_results(self, tools):
        result = tools["get_out_of_range_results"]()
        assert result["flagged_count"] == 2
        assert all(r["flag"] == "H" for r in result["results"])
        assert tools["get_out_of_range_results"]("2020-01-01")["flagged_count"] == 0

    def test_get_reference_range_changes(self, tools):
        result = tools["get_reference_range_changes"]()
        assert result == {"tests_with_changes": 0, "changes": []}

    def test_get_available_tests(self, tools):
        result = tools["get_available_tests"]()
        assert result["total_unique_tests"] == 5
        ldl = next(t for t in result["tests"] if t["test_name"] == "LDL-CHOLESTEROL")
        assert ldl["panels"] == "LIPID PANEL, STANDARD"
        assert ldl["report_count"] == 1
        assert ldl["first_seen"] == ldl["last_seen"] == "2025-04-11"


class TestMCPServerCreation:
    def test_create_server(self, config):
        server = create_mcp_server(config)
        assert server is not None
        assert server.name == "Quest Diagnostics Lab Data Explorer"

    def test_create_server_missing_db(self, tmp_path):
        config = MCPConfig(db_path=tmp_path / "nonexistent.db")
        with pytest.raises(FileNotFoundError):
            create_mcp_server(config)
