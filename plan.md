# Questy: Quest Diagnostics PDF Parser & MCP Server

## Overview

A local-first system to parse Quest Diagnostics blood work PDFs, store results in SQLite, and expose them via MCP for LLM analysis. Mirrors the architecture of `bodyspecy` but adapted for the fundamentally different nature of lab data (variable tests, panels, reference ranges, clinical notes).

## Key Differences from bodyspecy (DEXA)

| Aspect | bodyspecy (DEXA) | questy (Quest) |
|--------|------------------|----------------|
| Schema | Fixed measurements every scan | Variable tests per report |
| Values | Always numeric | Numeric, inequalities (`<0.5`, `<30`), flags (`H`, `L`) |
| Units | Consistent (converted to metric) | Vary per test, stored as-is |
| Reference ranges | N/A (no ref ranges in DEXA) | Per-test, can change between reports |
| Grouping | Regions (arms, legs, trunk) | Panels (Lipid Panel, CBC, CMP, etc.) |
| Notes | None | Rich clinical notes, risk categories, comments |
| Pages to parse | 2-3 structured pages | ~14 pages of results + ~8 pages of educational content |

## PDF Structure Analysis

Based on the two available PDFs (04/17/2025 - 23 pages, 11/14/2025 - 22 pages):

### Page 1 Header
- Patient: name, DOB, age, sex, phone, patient ID
- Specimen: ID, requisition, lab reference ID, report status
- Timing: collected, received, reported dates
- Client/ordering provider info
- Fasting status (YES/NO)

### Result Pages (~pages 1-14)
Each test entry contains:
- **Panel heading** (bold section header, e.g., "LIPID PANEL, STANDARD")
- **Test name** (e.g., "CHOLESTEROL, TOTAL")
- **Unit** (mg/dL, ng/mL, %, Thousand/uL, etc.)
- **Value** — numeric (`229`), with flag (`152 H`, `115 H`), inequality (`<0.5`, `<10`, `<30`), or marked `(calc)`
- **Reference range** — diverse formats:
  - Range: `30-100`, `3.6-5.1`, `250-1100`
  - Less-than: `<200`, `<150`, `<5.7`
  - Greater-than: `> OR = 40`, `> OR = 60`
  - Less-or-equal: `< or = 18.4`, `< or = 13.5`
  - Compound/conditional: Cortisol has time-of-day ranges
- **Notes/comments** — clinical interpretation, risk categories, methodology notes

### Trailing Pages (~page 14 onward)
- Performing Sites (lab locations)
- Key (Priority Out of Range, Out of Range symbols)
- **Report Insights** — educational content about each test (can be captured as enrichment data)

### Complete Test Inventory (union of both PDFs)

**Lipid Panel, Standard:** Cholesterol Total, HDL Cholesterol, Triglycerides, LDL-Cholesterol (calc), CHOL/HDLC Ratio (calc), Non HDL Cholesterol (calc)

**Apolipoprotein B**

**Vitamin D,25-OH,Total,IA**

**Hemoglobin A1c**

**Albumin** (standalone in PDF1, part of CMP in PDF2)

**GGT** (PDF1 only)

**Magnesium** (serum, PDF1 only)

**AST, ALT** (standalone in PDF1, part of CMP in PDF2)

**Iron Panel:** Iron Total, Iron Binding Capacity (calc), % Saturation (calc), Ferritin

**Calcium** (standalone in PDF1, part of CMP in PDF2)

**Glucose** (standalone in PDF1, part of CMP in PDF2)

**Potassium, Sodium** (standalone in PDF1, part of CMP in PDF2)

**Creatine Kinase, Total** (PDF1 only)

**CBC (Includes Diff/PLT):** WBC, RBC, Hemoglobin, Hematocrit, MCV, MCH, MCHC, RDW, Platelet Count, MPV, Absolute Neutrophils/Lymphocytes/Monocytes/Eosinophils/Basophils, Neutrophils %/Lymphocytes %/Monocytes %/Eosinophils %/Basophils %

**HS CRP**

**Testosterone Panel:** Testosterone Total MS, Testosterone Free, Sex Hormone Binding Globulin

**Insulin**

**Cortisol, Total**

**Folate, Serum** (PDF1 only)

**Progesterone** (PDF1 only)

**TSH**

**Vitamin B12** (PDF1 only)

**Estradiol**

**Magnesium, RBC** (PDF1 only)

**Lipoprotein (a)** (PDF2 only)

**Comprehensive Metabolic Panel** (PDF2 only): Glucose, BUN, Creatinine, EGFR, BUN/Creatinine Ratio, Sodium, Potassium, Chloride, Carbon Dioxide, Calcium, Protein Total, Albumin, Globulin, Albumin/Globulin Ratio, Bilirubin Total, Alkaline Phosphatase, AST, ALT

**Homocysteine** (PDF2 only)

**DHEA Sulfate** (PDF2 only)

**FSH and LH** (PDF2 only): FSH, LH

## Project Structure

```
/home/jtiisto/dev/questy/
├── pyproject.toml
├── src/questy/
│   ├── __init__.py
│   ├── parser/
│   │   ├── __init__.py
│   │   ├── pdf_parser.py      # pdfplumber-based PDF text extraction + parsing
│   │   └── models.py          # ParsedReport, ParsedResult, ParsedNote dataclasses
│   ├── localdb/
│   │   ├── __init__.py
│   │   ├── __main__.py
│   │   ├── cli.py             # CLI: questy-import <pdf_files>
│   │   ├── db.py              # QuestyDB class, import logic, upsert
│   │   └── models.py          # SQLAlchemy ORM models
│   └── mcp/
│       ├── __init__.py
│       ├── __main__.py
│       ├── cli.py             # CLI: questy-mcp server
│       ├── config.py          # MCPConfig
│       └── server.py          # FastMCP server with tools
└── tests/
    ├── conftest.py
    ├── test_parser.py
    ├── test_db.py
    └── test_mcp.py
```

## Database Schema

### Table: `reports`

Report-level metadata, one row per PDF imported.

| Column | Type | Notes |
|--------|------|-------|
| `report_date` | DATE | **PK** — specimen collection date |
| `patient_name` | TEXT | |
| `dob` | DATE | |
| `age` | INTEGER | Age at time of report |
| `sex` | TEXT | M/F |
| `fasting` | BOOLEAN | |
| `specimen_id` | TEXT | e.g., "SZ814712S" |
| `requisition_id` | TEXT | |
| `lab_reference_id` | TEXT | |
| `collected_at` | DATETIME | |
| `received_at` | DATETIME | |
| `reported_at` | DATETIME | |
| `report_status` | TEXT | e.g., "FINAL / SEE REPORT" |
| `pdf_path` | TEXT | |
| `imported_at` | DATETIME | |

### Table: `results`

Individual test measurements — the core data table.

| Column | Type | Notes |
|--------|------|-------|
| `report_date` | DATE | **PK**, FK → reports |
| `panel_name` | TEXT | **PK** — e.g., "LIPID PANEL, STANDARD" |
| `test_name` | TEXT | **PK** — e.g., "CHOLESTEROL, TOTAL" |
| `value` | REAL | Numeric value (NULL if inequality like `<0.5`) |
| `value_text` | TEXT | Raw text: "229", "152 H", "<0.5", "<30" |
| `value_prefix` | TEXT | NULL, "<", ">" for inequality values |
| `unit` | TEXT | "mg/dL", "ng/mL", "%", etc. |
| `flag` | TEXT | NULL, "H" (high), "L" (low) |
| `is_calculated` | BOOLEAN | True if marked "(calc)" |
| `ref_range_low` | REAL | Parsed lower bound (NULL if open-ended) |
| `ref_range_high` | REAL | Parsed upper bound (NULL if open-ended) |
| `ref_range_text` | TEXT | Raw reference range string: "<200", "30-100", "> OR = 40" |

**Design rationale:**
- `value` stores the numeric part for easy SQL queries (`WHERE value > 200`), while `value_text` preserves the original
- `value_prefix` captures inequality markers so `<0.5` can be distinguished from `0.5`
- Reference range stored both parsed (for queries) and raw (for display fidelity)
- Same test appearing in different panels across reports (e.g., Glucose standalone vs. in CMP) is handled naturally — the `panel_name` differs but `test_name` stays consistent for trend queries

### Table: `notes`

Clinical notes, comments, risk categories, and methodology notes per test or panel.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER | **PK**, autoincrement |
| `report_date` | DATE | FK → reports |
| `panel_name` | TEXT | Panel the note belongs to |
| `test_name` | TEXT | NULL for panel-level notes |
| `note_type` | TEXT | "comment", "risk_category", "clinical", "methodology" |
| `content` | TEXT | The note text |

**Examples of captured notes:**
- Risk categories: "Optimal <90 / Moderate 90-129 / High > or = 130" (for ApoB)
- Clinical: "For someone without known diabetes, a hemoglobin A1c value between 5.7% and 6.4% is consistent with prediabetes..."
- Methodology: "LDL-C is now calculated using the Martin-Hopkins calculation..."
- Comments: "See Note 1 — For additional information..."
- Conditional ranges: "Reference Range: For 8 a.m.(7-9 a.m.) Specimen: 4.0-22.0 / For 4 p.m.(3-5 p.m.) Specimen: 3.0-17.0"

## Parser Design

### Approach: Claude vision API + text cross-check

The parser uses Claude's vision API to read PDF page images directly, extracting
structured data without regex. A text-based cross-check using `pdftotext` (poppler)
verifies completeness and correctness.

1. **Convert PDF pages to images** using `pdf2image` (poppler-backed, 150 DPI)
2. **Pass 1 — Header**: Send page 1 image to Claude, extract patient/specimen metadata
3. **Pass 2 — Results**: Send pages in batches of 4, extract test results with panel
   context carried across batches. Collection date provided so the model can
   distinguish current results from historical chart data.
4. **Pass 3 — Cross-check recovery**: Run `pdftotext` to find test names with values
   that the LLM missed. Re-query specific pages to recover them.
5. **Pass 4 — Value verification**: Compare LLM-extracted numeric values against raw
   text. Auto-correct mismatches (typically from historical chart confusion) and
   emit warnings.

**Validation gates:**
- Reject results where `value_text` contains no digits (catches hallucinations)
- Validate flags (H/L/null), prefixes (</>), and numeric types
- Deduplicate results from page-boundary overlaps
- Warnings surfaced to CLI for user review

### Parser Output Models (dataclasses)

```python
@dataclass
class ParsedResult:
    panel_name: str
    test_name: str
    value: float | None       # Numeric part, None for unparseable
    value_text: str            # Raw: "229", "<0.5", "152 H"
    value_prefix: str | None   # "<", ">", None
    unit: str
    flag: str | None           # "H", "L", None
    is_calculated: bool
    ref_range_low: float | None
    ref_range_high: float | None
    ref_range_text: str

@dataclass
class ParsedNote:
    panel_name: str
    test_name: str | None
    note_type: str
    content: str

@dataclass
class ParsedReport:
    # Header
    patient_name: str
    dob: date
    age: int
    sex: str
    fasting: bool
    specimen_id: str
    requisition_id: str | None
    lab_reference_id: str | None
    collected_at: datetime
    received_at: datetime
    reported_at: datetime
    report_status: str
    # Data
    results: list[ParsedResult]
    notes: list[ParsedNote]
```

### Page Processing

All pages are processed (no hardcoded stop markers). The LLM naturally skips
non-result content (performing sites, educational pages). This handles formats
where test results appear after "Performing Sites" (e.g., HEMOGLOBIN A1c in
simple-format PDFs).

## Database Import

- **Upsert by `report_date`**: Re-importing the same PDF replaces all results/notes for that date (idempotent)
- **No unit conversion**: Unlike bodyspecy, lab values are stored in their original units since there's no standard "metric equivalent" for blood work
- **Collection date as PK**: The specimen collection date is the natural key (same as bodyspecy using scan_date)

## MCP Server Tools

### 1. `explore_database_structure()`
Returns table schemas, row counts, date ranges, and available panels/tests.

### 2. `get_table_details(table_name: str)`
Shows columns, types, and sample rows for a specific table.

### 3. `execute_sql_query(query: str, params: list | None)`
Custom SQL queries — SELECT/WITH only, with same security validations as bodyspecy.

### 4. `get_report_summary()`
Quick overview: total reports, date range, latest report's out-of-range results, panels tested.

### 5. `get_test_trends(test_name: str)`
Time-series data for a specific test across all reports. Returns values, reference ranges, flags.
Example: `get_test_trends("LDL-CHOLESTEROL")` → `[{date: "2025-04-17", value: 152, flag: "H", ref: "<100"}, ...]`

### 6. `get_panel_results(panel_name: str | None, report_date: str | None)`
- No args: list all panels from latest report with summary
- `panel_name` only: that panel's results from latest report
- Both: specific panel from specific date
- `report_date` only: all panels from that date

### 7. `get_out_of_range_results(report_date: str | None)`
Returns all flagged (H/L) results. If no date specified, returns latest report.

### 8. `get_reference_range_changes()`
Compares reference ranges across reports, highlighting any that changed for the same test. Useful for tracking when Quest updates their ranges.

### 9. `get_available_tests()`
Lists all unique test names in the database with their panel(s), unit, and how many reports include each test.

## Implementation Plan

### Phase 1: Project Setup
- Initialize project with pyproject.toml (mirror bodyspecy's structure)
- Set up src/questy package layout
- Dependencies: pdfplumber, sqlalchemy, fastmcp

### Phase 2: Parser
- Implement PDF text extraction with pdfplumber
- Build header parser (regex for patient/specimen info)
- Build result parser (handle all value/reference range formats)
- Build note parser (capture clinical text, risk categories)
- Write parser tests against the two available PDFs

### Phase 3: Database
- Define SQLAlchemy ORM models
- Implement QuestyDB with import_report() and upsert logic
- Build CLI for importing PDFs
- Write database tests

### Phase 4: MCP Server
- Implement all 9 tools listed above
- Add data guide resource
- Security: read-only SQLite, query validation
- Write MCP tests

### Phase 5: Integration Testing
- End-to-end: PDF → parse → import → query via MCP
- Verify both PDFs parse correctly and all data is captured

## Configuration

- **Database path**: `~/.questy/questy.db` (default), configurable via `--database` or `QUESTY_DB_PATH`
- **MCP transport**: stdio (default), also http/sse
- **CLI commands**: `questy-import`, `questy-mcp`
