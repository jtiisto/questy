"""Quest Diagnostics MCP Server implementation.

Provides secure, read-only access to Quest Diagnostics lab data through
the Model Context Protocol with optimized tools for LLM understanding.
"""

import logging
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from fastmcp import FastMCP
except ImportError:
    raise ImportError(
        "FastMCP is required for MCP server functionality. "
        "Install with: pip install questy[mcp] or pip install fastmcp"
    )

from .config import MCPConfig


class SQLiteConnection:
    """Secure SQLite connection context manager for read-only access."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn = None

    def __enter__(self):
        self.conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.close()


class QueryValidator:
    """SQL query validation and sanitization for read-only access."""

    ALLOWED_STATEMENTS = ("select", "with")
    FORBIDDEN_KEYWORDS = {
        "insert", "update", "delete", "drop", "create", "alter",
        "pragma", "attach", "detach", "vacuum", "analyze",
    }

    @classmethod
    def validate_query(cls, query: str) -> None:
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")

        query_lower = query.lower().strip()

        if not any(query_lower.startswith(prefix) for prefix in cls.ALLOWED_STATEMENTS):
            allowed = ", ".join(cls.ALLOWED_STATEMENTS).upper()
            raise ValueError(f"Only {allowed} queries are allowed for security")

        query_words = set(re.findall(r"\b\w+\b", query_lower))
        forbidden_found = query_words.intersection(cls.FORBIDDEN_KEYWORDS)
        if forbidden_found:
            raise ValueError(f"Forbidden keywords found: {', '.join(forbidden_found)}")

        if cls._contains_multiple_statements(query):
            raise ValueError("Multiple statements not allowed")

    @staticmethod
    def _contains_multiple_statements(sql: str) -> bool:
        in_single_quote = False
        in_double_quote = False
        for char in sql:
            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
            elif char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
            elif char == ";" and not in_single_quote and not in_double_quote:
                return True
        return False

    @staticmethod
    def add_row_limit(query: str, limit: int = 1000) -> str:
        if "limit" not in query.lower():
            return f"{query.rstrip(';')} LIMIT {limit}"
        return query


class DatabaseManager:
    """Manages database connections and basic operations."""

    def __init__(self, config: MCPConfig):
        self.config = config
        self.validator = QueryValidator()
        self.logger = logging.getLogger("questy.mcp.database")

        if config.enable_query_logging and not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def get_connection(self):
        return SQLiteConnection(self.config.db_path)

    def execute_safe_query(self, query: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        if self.config.strict_validation:
            self.validator.validate_query(query)

        query = self.validator.add_row_limit(query, self.config.max_rows)

        if self.config.enable_query_logging:
            self.logger.info(f"Executing query: {query}")

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params or [])
                return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as e:
            raise ValueError(f"Database error: {str(e)}")


def create_mcp_server(config: MCPConfig) -> FastMCP:
    """Create and configure the Quest Diagnostics MCP server."""
    config.validate()
    db_manager = DatabaseManager(config)
    mcp = FastMCP("Quest Diagnostics Lab Data Explorer")

    @mcp.tool()
    def explore_database_structure() -> Dict[str, Any]:
        """WHEN TO USE: When you need to understand what lab data is available.

        Starting point for exploring Quest Diagnostics data. Shows tables,
        row counts, date ranges, and available panels/tests.

        Returns:
            Database structure with tables, row counts, and date ranges
        """
        tables_query = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        tables = db_manager.execute_safe_query(tables_query)
        table_names = [row["name"] for row in tables]

        table_info = {}
        for table_name in table_names:
            count_result = db_manager.execute_safe_query(f"SELECT COUNT(*) as count FROM {table_name}")
            info = {
                "row_count": count_result[0]["count"],
                "description": _get_table_description(table_name),
            }

            if table_name in ("reports", "results", "notes"):
                date_col = "report_date"
                date_result = db_manager.execute_safe_query(
                    f"SELECT MIN({date_col}) as earliest, MAX({date_col}) as latest FROM {table_name}"
                )
                if date_result and date_result[0]["earliest"]:
                    info["date_range"] = {
                        "earliest": date_result[0]["earliest"],
                        "latest": date_result[0]["latest"],
                    }

            table_info[table_name] = info

        return {
            "available_tables": table_info,
            "usage_tip": "Use 'get_report_summary' for a quick overview, 'get_table_details' to see column structure, or 'execute_sql_query' for custom queries",
        }

    @mcp.tool()
    def get_table_details(table_name: str) -> Dict[str, Any]:
        """WHEN TO USE: When you need to see the structure and sample data of a specific table.

        Args:
            table_name: Name of the table (reports, results, or notes)

        Returns:
            Table structure with columns, data types, and sample records
        """
        if not table_name or not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table_name):
            raise ValueError("Invalid table name format")

        check_result = db_manager.execute_safe_query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", [table_name]
        )
        if not check_result:
            available = db_manager.execute_safe_query(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            table_list = [row["name"] for row in available]
            raise ValueError(f"Table '{table_name}' does not exist. Available: {', '.join(table_list)}")

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()

        column_info = [
            {"name": col[1], "type": col[2], "required": bool(col[3]), "is_primary_key": bool(col[5])}
            for col in columns
        ]

        sample_data = db_manager.execute_safe_query(
            f"SELECT * FROM {table_name} ORDER BY rowid DESC LIMIT 3"
        )

        return {
            "table_name": table_name,
            "columns": column_info,
            "sample_data": sample_data,
            "description": _get_table_description(table_name),
        }

    @mcp.tool()
    def execute_sql_query(query: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        """WHEN TO USE: When you need to run custom SQL queries against the lab data.

        Only SELECT and WITH queries are allowed. Data is stored in original lab units.

        Args:
            query: SQL SELECT query to execute
            params: Optional list of parameters for ? placeholders

        Example queries:
        - All results for latest report: "SELECT test_name, value, unit, flag, ref_range_text FROM results WHERE report_date = (SELECT MAX(report_date) FROM reports) ORDER BY panel_name, test_name"
        - Trend for a test: "SELECT report_date, value, flag, ref_range_text FROM results WHERE test_name = 'LDL-CHOLESTEROL' ORDER BY report_date"
        - Out of range: "SELECT test_name, value, unit, flag, ref_range_text FROM results WHERE flag IS NOT NULL AND report_date = (SELECT MAX(report_date) FROM reports)"

        Returns:
            List of matching records as dictionaries
        """
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")
        return db_manager.execute_safe_query(query, params)

    @mcp.tool()
    def get_report_summary() -> Dict[str, Any]:
        """WHEN TO USE: When you want a quick overview of all lab reports.

        Provides total report count, date range, latest report panels and
        out-of-range results.

        Returns:
            Summary with report count, date range, latest report details
        """
        overview = db_manager.execute_safe_query("""
            SELECT COUNT(*) as total_reports,
                   MIN(report_date) as first_report,
                   MAX(report_date) as latest_report
            FROM reports
        """)
        if not overview or overview[0]["total_reports"] == 0:
            return {"total_reports": 0, "message": "No reports in database"}

        info = overview[0]

        # Latest report metadata
        latest_meta = db_manager.execute_safe_query("""
            SELECT report_date, patient_name, age, sex, fasting
            FROM reports ORDER BY report_date DESC LIMIT 1
        """)[0]

        # Panels in latest report
        panels = db_manager.execute_safe_query("""
            SELECT panel_name, COUNT(*) as test_count
            FROM results
            WHERE report_date = (SELECT MAX(report_date) FROM reports)
            GROUP BY panel_name
            ORDER BY panel_name
        """)

        # Out-of-range in latest report
        out_of_range = db_manager.execute_safe_query("""
            SELECT test_name, value_text, unit, flag, ref_range_text, panel_name
            FROM results
            WHERE report_date = (SELECT MAX(report_date) FROM reports)
              AND flag IS NOT NULL
            ORDER BY panel_name, test_name
        """)

        # Total unique tests across all reports
        unique_tests = db_manager.execute_safe_query(
            "SELECT COUNT(DISTINCT test_name) as count FROM results"
        )[0]["count"]

        return {
            "total_reports": info["total_reports"],
            "date_range": {"first": info["first_report"], "latest": info["latest_report"]},
            "latest_report": latest_meta,
            "latest_panels": panels,
            "latest_out_of_range": out_of_range,
            "total_unique_tests": unique_tests,
        }

    @mcp.tool()
    def get_test_trends(test_name: str) -> Dict[str, Any]:
        """WHEN TO USE: When you need to see how a specific test has changed over time.

        Returns time-series data for a test across all reports including values,
        reference ranges, and flags.

        Args:
            test_name: The test name (e.g., "LDL-CHOLESTEROL", "HEMOGLOBIN A1C", "TSH").
                       Case-insensitive; use get_available_tests() to see valid names.

        Returns:
            Time-series data with values, reference ranges, and flags per report date
        """
        if not test_name or not test_name.strip():
            raise ValueError("test_name is required")

        rows = db_manager.execute_safe_query("""
            SELECT r.report_date, r.value, r.value_text, r.value_prefix,
                   r.unit, r.flag, r.ref_range_text, r.ref_range_low,
                   r.ref_range_high, r.panel_name
            FROM results r
            WHERE UPPER(r.test_name) = UPPER(?)
            ORDER BY r.report_date ASC
        """, [test_name.strip()])

        if not rows:
            # Suggest similar tests
            similar = db_manager.execute_safe_query("""
                SELECT DISTINCT test_name FROM results
                WHERE UPPER(test_name) LIKE UPPER(?)
                ORDER BY test_name LIMIT 10
            """, [f"%{test_name.strip()}%"])
            suggestions = [r["test_name"] for r in similar]
            return {
                "test_name": test_name,
                "data_points": 0,
                "message": f"No results found for '{test_name}'",
                "suggestions": suggestions,
            }

        return {
            "test_name": rows[0]["test_name"] if "test_name" in rows[0] else test_name,
            "unit": rows[0]["unit"],
            "data_points": len(rows),
            "trends": rows,
        }

    @mcp.tool()
    def get_panel_results(
        panel_name: Optional[str] = None,
        report_date: Optional[str] = None,
    ) -> Dict[str, Any]:
        """WHEN TO USE: When you need lab results grouped by panel.

        Modes:
        - No args: all panels from latest report with results
        - panel_name only: that panel from latest report
        - report_date only: all panels from that date
        - Both: specific panel from specific date

        Args:
            panel_name: Panel name (e.g., "LIPID PANEL, STANDARD", "CBC (INCLUDES DIFF/PLT)")
            report_date: Report date as string (YYYY-MM-DD)

        Returns:
            Panel results with test values, units, flags, and reference ranges
        """
        if report_date:
            date_filter = report_date
        else:
            latest = db_manager.execute_safe_query("SELECT MAX(report_date) as d FROM reports")
            if not latest or not latest[0]["d"]:
                return {"message": "No reports in database"}
            date_filter = latest[0]["d"]

        params: list[Any] = [date_filter]
        where_clause = "WHERE r.report_date = ?"

        if panel_name:
            where_clause += " AND UPPER(r.panel_name) = UPPER(?)"
            params.append(panel_name.strip())

        rows = db_manager.execute_safe_query(f"""
            SELECT r.panel_name, r.test_name, r.value, r.value_text,
                   r.unit, r.flag, r.is_calculated, r.ref_range_text
            FROM results r
            {where_clause}
            ORDER BY r.panel_name, r.test_name
        """, params)

        # Group by panel
        panels: dict[str, list] = {}
        for row in rows:
            pname = row["panel_name"]
            if pname not in panels:
                panels[pname] = []
            panels[pname].append(row)

        return {
            "report_date": date_filter,
            "panel_count": len(panels),
            "panels": panels,
        }

    @mcp.tool()
    def get_out_of_range_results(report_date: Optional[str] = None) -> Dict[str, Any]:
        """WHEN TO USE: When you need to see which results are flagged as abnormal.

        Returns all results flagged H (high) or L (low).

        Args:
            report_date: Optional date (YYYY-MM-DD). Defaults to latest report.

        Returns:
            Flagged results with values, reference ranges, and panel context
        """
        if report_date:
            date_filter = report_date
        else:
            latest = db_manager.execute_safe_query("SELECT MAX(report_date) as d FROM reports")
            if not latest or not latest[0]["d"]:
                return {"message": "No reports in database"}
            date_filter = latest[0]["d"]

        rows = db_manager.execute_safe_query("""
            SELECT panel_name, test_name, value, value_text, unit, flag,
                   ref_range_text, ref_range_low, ref_range_high
            FROM results
            WHERE report_date = ? AND flag IS NOT NULL
            ORDER BY panel_name, test_name
        """, [date_filter])

        return {
            "report_date": date_filter,
            "flagged_count": len(rows),
            "results": rows,
        }

    @mcp.tool()
    def get_reference_range_changes() -> Dict[str, Any]:
        """WHEN TO USE: When you need to check if reference ranges have changed between reports.

        Compares reference ranges for the same test across reports.
        Useful for tracking when Quest updates their reference intervals.

        Returns:
            Tests where reference ranges differ between reports
        """
        rows = db_manager.execute_safe_query("""
            SELECT test_name, report_date, ref_range_text, unit
            FROM results
            WHERE ref_range_text IS NOT NULL AND ref_range_text != ''
            ORDER BY test_name, report_date
        """)

        changes: list[dict] = []
        by_test: dict[str, list] = {}
        for row in rows:
            name = row["test_name"]
            if name not in by_test:
                by_test[name] = []
            by_test[name].append(row)

        for test_name, entries in by_test.items():
            ranges_seen = set()
            for entry in entries:
                ranges_seen.add(entry["ref_range_text"])

            if len(ranges_seen) > 1:
                changes.append({
                    "test_name": test_name,
                    "unit": entries[0]["unit"],
                    "ranges_by_date": [
                        {"report_date": e["report_date"], "ref_range_text": e["ref_range_text"]}
                        for e in entries
                    ],
                })

        return {
            "tests_with_changes": len(changes),
            "changes": changes,
        }

    @mcp.tool()
    def get_available_tests() -> Dict[str, Any]:
        """WHEN TO USE: When you need to see what tests are available in the database.

        Lists all unique test names with their panel(s), unit, and report count.

        Returns:
            List of all tests with metadata
        """
        rows = db_manager.execute_safe_query("""
            SELECT test_name, unit,
                   GROUP_CONCAT(DISTINCT panel_name) as panels,
                   COUNT(DISTINCT report_date) as report_count,
                   MIN(report_date) as first_seen,
                   MAX(report_date) as last_seen
            FROM results
            GROUP BY test_name, unit
            ORDER BY test_name
        """)

        return {
            "total_unique_tests": len(rows),
            "tests": rows,
        }

    @mcp.resource("file://questy_data_guide")
    def questy_data_guide() -> str:
        """Complete guide to understanding and querying Quest Diagnostics lab data."""
        return _get_data_guide()

    return mcp


def _get_table_description(table_name: str) -> str:
    descriptions = {
        "reports": "Report-level metadata — one row per imported PDF with patient info, specimen details, and dates",
        "results": "Individual test measurements — the core data table with values, units, flags, and reference ranges",
        "notes": "Clinical notes, comments, risk categories, and methodology notes per test or panel",
    }
    return descriptions.get(table_name, "Data table")


def _get_data_guide() -> str:
    return """
# Quest Diagnostics Lab Data Guide

## Quick Start
1. Use `get_report_summary` for a quick overview of all reports
2. Use `get_panel_results` to see results grouped by panel
3. Use `get_test_trends` to track a specific test over time
4. Use `get_out_of_range_results` to see flagged abnormal results
5. Use `get_reference_range_changes` to see if ranges changed between reports
6. Use `get_available_tests` to see all available tests
7. Use `execute_sql_query` for custom analysis

## Database Tables

### reports (one row per imported PDF)
- `report_date` (DATE PK): Specimen collection date
- `patient_name` (TEXT): Patient name
- `dob` (DATE): Date of birth
- `age` (INTEGER): Age at time of report
- `sex` (TEXT): M/F
- `fasting` (BOOLEAN): Fasting status
- `specimen_id` (TEXT): Specimen identifier
- `requisition_id` (TEXT): Requisition number
- `lab_reference_id` (TEXT): Lab reference ID
- `collected_at`, `received_at`, `reported_at` (DATETIME): Timing
- `report_status` (TEXT): e.g., "FINAL / SEE REPORT"

### results (core data — one row per test measurement)
- `report_date` (DATE PK/FK): References reports
- `panel_name` (TEXT PK): Panel grouping, e.g., "LIPID PANEL, STANDARD"
- `test_name` (TEXT PK): Test name, e.g., "CHOLESTEROL, TOTAL"
- `value` (REAL): Numeric value (NULL for inequalities like "<0.5")
- `value_text` (TEXT): Raw value text: "229", "152 H", "<0.5"
- `value_prefix` (TEXT): "<" or ">" for inequality values, NULL otherwise
- `unit` (TEXT): "mg/dL", "ng/mL", "%", "Thousand/uL", etc.
- `flag` (TEXT): "H" (high), "L" (low), or NULL (normal)
- `is_calculated` (BOOLEAN): True if marked "(calc)"
- `ref_range_low` (REAL): Parsed lower bound of reference range
- `ref_range_high` (REAL): Parsed upper bound of reference range
- `ref_range_text` (TEXT): Raw reference range: "<200", "30-100", "> OR = 40"

### notes (clinical text per test/panel)
- `id` (INTEGER PK): Auto-increment
- `report_date` (DATE FK): References reports
- `panel_name` (TEXT): Panel the note belongs to
- `test_name` (TEXT): NULL for panel-level notes
- `note_type` (TEXT): "comment", "risk_category", "clinical", "methodology"
- `content` (TEXT): The note text

## Data Conventions
- **Units are original lab units** — no conversion applied
- **Flags**: "H" = above reference range, "L" = below reference range
- **Inequality values**: `value` is NULL, `value_prefix` is "<" or ">", numeric part in `value_text`
- **Calculated values**: `is_calculated` = True for tests marked "(calc)" like LDL-Cholesterol

## Common Panels
- LIPID PANEL, STANDARD (cholesterol, HDL, LDL, triglycerides)
- CBC (INCLUDES DIFF/PLT) (blood cell counts, differentials)
- COMPREHENSIVE METABOLIC PANEL (glucose, electrolytes, kidney/liver)
- TESTOSTERONE, FREE (DIALYSIS), TOTAL (MS) AND SEX HORMONE BINDING GLOBULIN
- IRON AND TOTAL IRON BINDING CAPACITY / IRON, TIBC AND FERRITIN PANEL

## Common Query Patterns

### All flagged results from latest report
```sql
SELECT panel_name, test_name, value_text, unit, flag, ref_range_text
FROM results
WHERE report_date = (SELECT MAX(report_date) FROM reports)
  AND flag IS NOT NULL
ORDER BY panel_name, test_name
```

### Track cholesterol over time
```sql
SELECT report_date, test_name, value, unit, flag
FROM results
WHERE test_name IN ('CHOLESTEROL, TOTAL', 'HDL CHOLESTEROL', 'LDL-CHOLESTEROL', 'TRIGLYCERIDES')
ORDER BY report_date, test_name
```

### Compare all tests between two reports
```sql
SELECT r1.test_name, r1.value as first_value, r2.value as second_value,
       r1.unit, r2.flag,
       ROUND(r2.value - r1.value, 2) as change
FROM results r1
JOIN results r2 ON r1.test_name = r2.test_name
WHERE r1.report_date = (SELECT MIN(report_date) FROM reports)
  AND r2.report_date = (SELECT MAX(report_date) FROM reports)
  AND r1.value IS NOT NULL AND r2.value IS NOT NULL
ORDER BY r1.test_name
```
""".strip()
