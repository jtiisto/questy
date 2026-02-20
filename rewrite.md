# Parser Rewrite: Regex → Claude Vision

Replace the brittle regex + pdfplumber parser (`pdf_parser.py`, ~700 lines) with
Claude API vision-based extraction. Everything downstream (models, DB, MCP server)
stays the same — only the parser internals change.

## Why

The current parser has ~20 regex patterns, a hardcoded list of known panels, chart-
skipping heuristics, multi-line heading detection, and a fragile state machine. Any
change to Quest's PDF formatting breaks it. An LLM reading the visual layout handles
all of this naturally.

## What changes

```
src/questy/parser/
  pdf_parser.py    ← rewrite (regex → Claude API calls)
  models.py        ← no change (ParsedReport, ParsedResult, ParsedNote stay)
  prompt.py        ← new (extraction prompt + response schema)

src/questy/localdb/
  *                ← no change

src/questy/mcp/
  *                ← no change

pyproject.toml     ← add anthropic, pdf2image dependencies
```

## Approach

### Step 1: PDF → page images

Use **pdf2image** (poppler-backed) to convert each PDF page to a PNG. pdfplumber
can stay as a fallback or be dropped entirely — we're sending images, not text.

```python
from pdf2image import convert_from_path

pages = convert_from_path(pdf_path, dpi=150)  # ~150 DPI is enough for text
```

Why images instead of extracted text:
- Claude sees the actual layout: bold panel headings, tabular columns, flags
- No text-extraction artifacts (merged columns, broken lines)
- Reference ranges with complex formatting ("For 8 a.m.: 4.0-22.0") are visually clear
- Charts and graphs are visually obvious to skip

### Step 2: Two-pass extraction

**Pass 1 — Header (page 1 only):**
Send page 1 image with a prompt requesting patient metadata. Returns a small JSON
object. This is cheap (one image, small response).

**Pass 2 — Results (all result pages):**
Send result pages (typically pages 1–14) in batches with a prompt requesting
structured test results. Stop at "Performing Sites" / "Report Insights" pages
(Claude can identify these visually).

Batching strategy: send 3–5 pages per API call to balance context usage vs. number
of calls. A 22-page PDF with ~14 result pages = 3–5 API calls total.

### Step 3: Structured output via response schema

Define JSON schemas matching the existing dataclass structure. Claude returns
structured JSON that maps directly to `ParsedResult` and `ParsedNote`.

```python
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "panel_name": {"type": "string"},
                    "test_name": {"type": "string"},
                    "value": {"type": ["number", "null"]},
                    "value_text": {"type": "string"},
                    "value_prefix": {"type": ["string", "null"], "enum": ["<", ">", null]},
                    "unit": {"type": "string"},
                    "flag": {"type": ["string", "null"], "enum": ["H", "L", null]},
                    "is_calculated": {"type": "boolean"},
                    "ref_range_low": {"type": ["number", "null"]},
                    "ref_range_high": {"type": ["number", "null"]},
                    "ref_range_text": {"type": "string"}
                },
                "required": ["panel_name", "test_name", "value", "value_text",
                             "value_prefix", "unit", "flag", "is_calculated",
                             "ref_range_low", "ref_range_high", "ref_range_text"]
            }
        },
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "panel_name": {"type": "string"},
                    "test_name": {"type": ["string", "null"]},
                    "note_type": {"type": "string", "enum": ["comment", "risk_category", "clinical", "methodology"]},
                    "content": {"type": "string"}
                },
                "required": ["panel_name", "test_name", "note_type", "content"]
            }
        },
        "is_last_result_page": {"type": "boolean"}
    }
}
```

### Step 4: Assemble ParsedReport

Merge header + all result batches into a single `ParsedReport`. Deduplicate any
results that span page boundaries (keyed on panel_name + test_name).

## Prompt design (prompt.py)

Two prompts, kept in a dedicated module for easy iteration:

**Header prompt:**
```
Extract patient and specimen metadata from this Quest Diagnostics report page.
Return JSON with these exact fields:
- patient_name, dob (YYYY-MM-DD), age, sex (M/F), fasting (true/false)
- specimen_id, requisition_id, lab_reference_id
- collected_at, received_at, reported_at (ISO 8601)
- report_status
```

**Results prompt:**
```
Extract all lab test results from these Quest Diagnostics report pages.

For each test result, extract:
- panel_name: The section heading (e.g., "LIPID PANEL, STANDARD")
- test_name: The test name exactly as printed (e.g., "CHOLESTEROL, TOTAL")
- value: Numeric value as a number, or null if not numeric
- value_text: Raw value as printed (e.g., "229", "<0.5", "152 H")
- value_prefix: "<" or ">" if present, otherwise null
- unit: The unit of measurement (e.g., "mg/dL")
- flag: "H" for high, "L" for low, null if normal
- is_calculated: true if marked "(calc)"
- ref_range_low / ref_range_high: Numeric bounds, null if not applicable
- ref_range_text: Reference range as printed (e.g., "30-100", "< or = 18.4")

Also extract clinical notes with:
- panel_name, test_name (null for panel-level), note_type, content

Skip: charts/graphs, performing sites, report insights, educational content.
Set is_last_result_page to true if these pages contain "Performing Sites" or
"Report Insights" or no more test results.
```

## Validation layer

After LLM extraction, run lightweight validation (not regex — just sanity checks):

1. **Required fields**: Every result must have panel_name, test_name, value_text
2. **Value consistency**: If value_text is numeric, value should be a number
3. **Flag values**: Must be "H", "L", or null
4. **Dedup**: Remove duplicate (panel_name, test_name) entries from page overlaps
5. **Ordering**: Results stay in document order (matches how the DB stores them)

This catches LLM mistakes without reimplementing the regex parser.

## Dependencies

```toml
# pyproject.toml changes
dependencies = [
    "anthropic>=0.40.0",   # Claude API client
    "pdf2image>=1.16.0",   # PDF page → PNG conversion
    "sqlalchemy>=2.0.0",
]
# pdfplumber removed — no longer needed
```

System dependency: **poppler-utils** (for pdf2image). Already common on Linux;
on macOS: `brew install poppler`.

## Cost estimate

Per import (one 22-page PDF):
- ~14 result pages at 150 DPI ≈ 14 images
- Batched into ~4 API calls (3-4 pages each)
- Using claude-haiku-4-5 for extraction (fast, cheap, accurate enough for structured data)
- Estimated cost: ~$0.02–0.05 per PDF import
- At 2-4 reports/year: <$0.20/year

## API key configuration

The parser needs an Anthropic API key at import time. Options (in priority order):

1. `ANTHROPIC_API_KEY` environment variable (standard)
2. `--api-key` flag on `questy-import` CLI
3. `~/.questy/config.toml` for persistent config

The MCP server and database queries remain fully offline — the API key is only
needed during `questy-import`.

## Implementation order

1. **Add `prompt.py`** — extraction prompts and JSON schemas
2. **Rewrite `pdf_parser.py`** — replace regex with:
   - `convert_from_path()` for page images
   - `anthropic.Anthropic().messages.create()` for extraction
   - Response parsing into existing `ParsedResult` / `ParsedNote` dataclasses
   - Validation layer
3. **Update `pyproject.toml`** — swap pdfplumber for anthropic + pdf2image
4. **Update CLI** — add `--api-key` flag, env var handling
5. **Test against both sample PDFs** — compare output to current regex parser

## Rollback

The regex parser can be kept as `pdf_parser_regex.py` during development for
comparison. Once the vision parser is validated against both sample PDFs, delete it.

## What does NOT change

- `parser/models.py` — ParsedReport, ParsedResult, ParsedNote (the contract)
- `localdb/models.py` — SQLAlchemy ORM models
- `localdb/db.py` — QuestyDB.import_report() (takes a ParsedReport, unchanged)
- `localdb/cli.py` — import CLI (calls parse_pdf, unchanged interface)
- `mcp/` — entire MCP server (reads from SQLite, unaffected)
