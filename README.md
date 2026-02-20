# Questy

Local-first system for importing Quest Diagnostics blood work PDFs, storing results in SQLite, and querying them via MCP for LLM analysis.

## Architecture

```
PDF report ──> questy-import ──> SQLite ──> questy-mcp ──> LLM
               (Claude vision)   (~/.questy/   (read-only
                + pdftotext       questy.db)    MCP server)
                cross-check)
```

Three modules, cleanly separated:

- **Parser** (`questy.parser`) — Converts Quest PDFs to structured data using Claude vision API, with pdftotext cross-checks
- **Database** (`questy.localdb`) — SQLAlchemy ORM, idempotent import (upsert by collection date)
- **MCP Server** (`questy.mcp`) — Read-only FastMCP server with 9 tools for LLM querying

## Setup

### Prerequisites

- Python 3.10+
- [poppler-utils](https://poppler.freedesktop.org/) (provides `pdftotext` and `pdftoppm` for PDF processing)
  - Ubuntu/Debian: `sudo apt install poppler-utils`
  - macOS: `brew install poppler`
- An [Anthropic API key](https://console.anthropic.com/) (only needed for `questy-import`)

### Install

```bash
pip install -e .            # Core (parser + database)
pip install -e '.[mcp]'     # MCP server
pip install -e '.[dev]'     # Tests + linting
```

## Usage

### Import a PDF report

```bash
export ANTHROPIC_API_KEY=sk-ant-...
questy-import ~/diagnostics/quest_diagnostics_04-11-2025.pdf
```

Multiple files at once:

```bash
questy-import ~/diagnostics/quest*.pdf
```

Options:

```
--database PATH    SQLite path (default: ~/.questy/questy.db)
--api-key KEY      Anthropic API key (default: ANTHROPIC_API_KEY env var)
--model MODEL      Claude model (default: claude-haiku-4-5-20251001)
```

### Start the MCP server

```bash
questy-mcp server
```

Options:

```
--database PATH       SQLite path (default: ~/.questy/questy.db)
--max-rows N          Max rows per query (default: 1000)
--transport TYPE      stdio | http | sse (default: stdio)
```

### Claude Desktop / Claude Code integration

Add to your MCP config:

```json
{
  "mcpServers": {
    "questy": {
      "command": "questy-mcp",
      "args": ["server"]
    }
  }
}
```

## Import Validation Pipeline

The import process uses a multi-pass strategy that pairs LLM vision extraction with deterministic text cross-checks. Every extracted value goes through at least two independent verification methods before being stored.

### Pass 1 — Header extraction (vision)

Sends page 1 as a 150-DPI image to Claude, which returns structured JSON with patient metadata (name, DOB, age, sex, fasting status, specimen IDs, dates).

### Header cross-check (pdftotext)

Runs `pdftotext` on page 1 and extracts DOB and age via regex. These deterministic text extractions are compared against the LLM output:

- **DOB mismatch** — The pdftotext value replaces the LLM value (text extraction is more reliable for structured header fields)
- **Age mismatch** — Same correction, preferring the text value
- **Age/DOB consistency** — Age is recomputed from DOB + collection date. If the extracted age differs by more than 1 year from the computed age, the computed value is used. This catches cases where both LLM and pdftotext agree on a wrong age but the DOB is correct (or vice versa)

All corrections emit warnings that are surfaced to the user via the CLI.

### Pass 2 — Result extraction (vision)

Sends all pages in batches of 4 to Claude for structured extraction of test results and clinical notes. The collection date is provided in the prompt so the model can distinguish current results from historical trend charts that appear on the same pages. Panel context is carried across batches so tests at page boundaries are assigned to the correct panel. The prompt explicitly instructs the model to skip educational conversion tables (e.g., A1c-to-eAG reference charts) and to recognize standalone test sections where the section heading is the panel name (e.g., FERRITIN, AST, ALT as their own panels rather than grouped under a preceding panel).

### Per-result validation

Each result from the LLM goes through validation before being accepted:

- **Required fields** — `panel_name`, `test_name`, and `value_text` must all be non-empty
- **Digit check** — `value_text` must contain at least one digit, rejecting hallucinated entries like "Not shown in extraction area"
- **Flag validation** — Must be `"H"`, `"L"`, or `null`; invalid values are reset to `null`
- **Prefix validation** — Must be `"<"`, `">"`, or `null`
- **Numeric validation** — `value`, `ref_range_low`, and `ref_range_high` must parse as floats or are set to `null`
- **Note type validation** — Must be one of `comment`, `risk_category`, `clinical`, `methodology`; invalid types default to `clinical`

### Deduplication

Results from overlapping page batches are deduplicated by `(panel_name, test_name)`. The last occurrence is kept, as later batches may have more complete data (units, reference ranges from the following page).

### Pass 3 — Recovery (pdftotext + vision)

Runs `pdftotext` across the full PDF and matches test-name/value patterns via regex. Any test names found in the raw text but missing from the LLM extraction trigger a targeted recovery pass: all pages are re-sent to Claude with a prompt specifically requesting those missed tests.

Recovery outcomes are tracked:

- All missed tests recovered — Warning with count
- Some tests still missing — Error listing the unrecovered test names for manual review

### Pass 4 — Value verification (pdftotext)

Every extracted numeric value is compared against the pdftotext output for the same test name. On mismatch (beyond 0.01 tolerance), the value is auto-corrected to the text-extracted value. This catches the most common LLM error: reading a number from a historical trend chart instead of the current result line.

### Post-processing

After all extraction passes, deterministic corrections are applied to fix known issues that neither the LLM nor pdftotext can reliably handle:

- **Unit glyph corrections** — Some PDF fonts render characters differently than their Unicode mapping. For example, "fL" (femtoliters) is commonly misread as "IL" by both vision and text extraction due to font glyph mapping. A corrections map (`_UNIT_CORRECTIONS`) fixes these, updating both the `unit` field and `ref_range_text`.

### Database upsert

Imports are idempotent. Re-importing the same PDF replaces all results, notes, and metadata for that collection date. The collection date is the natural primary key.

### Warning system

All corrections and anomalies produce `ParseWarning` objects (level `warn` or `error`) that are:
- Returned on the `ParsedReport` object
- Printed to the terminal during `questy-import`

This gives the user full visibility into what the parser corrected automatically and what may need manual review.

## MCP Server Tools

| Tool | Description |
|------|-------------|
| `explore_database_structure` | Table schemas, row counts, date ranges |
| `get_table_details` | Columns, types, sample rows for a table |
| `execute_sql_query` | Custom SELECT/WITH queries (read-only) |
| `get_report_summary` | Report count, date range, latest out-of-range |
| `get_test_trends` | Time-series for a specific test across all reports |
| `get_panel_results` | Results grouped by panel, filterable by date |
| `get_out_of_range_results` | All flagged (H/L) results for a report |
| `get_reference_range_changes` | Track when Quest updates reference ranges |
| `get_available_tests` | All unique test names with panels and report counts |

The MCP server enforces read-only access: SQLite connections use `?mode=ro`, and a `QueryValidator` rejects any non-SELECT/WITH queries and blocks forbidden keywords (INSERT, UPDATE, DELETE, DROP, PRAGMA, etc.).

## Database Schema

### reports

One row per imported PDF. Primary key: `report_date` (specimen collection date).

Patient metadata, specimen IDs, timing (collected/received/reported), fasting status, and path to the source PDF.

### results

One row per test measurement. Composite primary key: `(report_date, panel_name, test_name)`.

- `value` (REAL) — Numeric value, NULL for inequalities
- `value_text` (TEXT) — Raw value as printed: `"229"`, `"<0.5"`, `"152 H"`
- `value_prefix` (TEXT) — `"<"`, `">"`, or NULL
- `flag` (TEXT) — `"H"`, `"L"`, or NULL
- `is_calculated` (BOOLEAN) — True for tests marked `(calc)`
- `ref_range_low` / `ref_range_high` (REAL) — Parsed bounds
- `ref_range_text` (TEXT) — Raw reference range: `"<200"`, `"30-100"`, `"> OR = 40"`

Values are stored in original lab units (no conversion).

### notes

Clinical notes, comments, risk categories, and methodology notes. Linked to reports by `report_date`, scoped to a panel and optionally a specific test.

## Testing

```bash
pip install -e '.[dev]'
pytest
```

148 tests covering parser models, helper functions (build/validate/deduplicate/cross-check/post-process), database operations (import, upsert, date tracking), MCP config validation, query security (SQL injection prevention, read-only enforcement), MCP server integration, and CLI argument parsing.

## Project Structure

```
src/questy/
  parser/
    models.py        # ParsedReport, ParsedResult, ParsedNote, ParseWarning
    pdf_parser.py    # Claude vision extraction + pdftotext cross-checks
    prompt.py        # Extraction prompts and JSON schemas
  localdb/
    models.py        # SQLAlchemy ORM (Report, Result, Note)
    db.py            # QuestyDB — session management, import/upsert
    cli.py           # questy-import command
  mcp/
    server.py        # FastMCP server with 9 tools + data guide resource
    config.py        # MCPConfig with validation
    cli.py           # questy-mcp command
tests/
  conftest.py                 # Shared fixtures
  test_parser_models.py       # Dataclass tests
  test_parser_helpers.py      # Parser internals (no API calls needed)
  test_database.py            # DB operations with temp SQLite
  test_mcp_config.py          # Config validation
  test_mcp_query_validator.py # SQL security
  test_mcp_server.py          # Server integration
  test_cli.py                 # CLI argument parsing
```
