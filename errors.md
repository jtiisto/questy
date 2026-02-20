# Questy MCP - Data Validation Errors

Comparison of MCP database contents against source PDFs:
- `diagnostics/quest_diagnostics_04-11-2025.pdf` (collected 2025-04-18)
- `diagnostics/quest-diagnostics_11-14-2025.pdf` (collected 2025-11-14)

All numeric values, flags (H/L), and reference ranges are correct. Issues are limited to spurious rows, panel misassignments, and a unit parsing error.

---

## 1. CRITICAL: Spurious HbA1c results (November 2025)

The parser extracted rows from the educational A1c-to-eAG conversion table in the notes section, treating them as actual test results.

**Affected rows (report_date = 2025-11-14):**

| panel_name | test_name | value | value_text | Correct? |
|---|---|---|---|---|
| HEMOGLOBIN A1c | HEMOGLOBIN A1c | 5.5 | 5.5 | Yes - actual result |
| HEMOGLOBIN A1C | HbA1c | NULL | 6% | **No** - from conversion table |
| HEMOGLOBIN A1C | eAG | 126 | 126 mg/dL | **No** - from conversion table |

The "6%" and "126 mg/dL" come from the standard reference table printed in the PDF notes:
> 6% → 126 mg/dL, 6.5% → 140 mg/dL, 7% → 154 mg/dL ...

The patient's actual A1c is 5.5% (corresponding eAG would be ~111 mg/dL, not 126). These two rows should not exist as results.

Note the case difference: the real result is under panel `HEMOGLOBIN A1c` (lowercase c), while the spurious entries are under `HEMOGLOBIN A1C` (uppercase C).

## 2. Panel assignment error: FERRITIN under CORTISOL (April 2025)

**Affected row (report_date = 2025-04-18):**

| panel_name (current) | test_name | value | panel_name (expected) |
|---|---|---|---|
| CORTISOL, TOTAL | FERRITIN | 102 | FERRITIN |

In the April PDF, FERRITIN is its own standalone section appearing after the CORTISOL section. The parser incorrectly grouped it under the preceding CORTISOL panel. The value (102 ng/mL) and reference range (38-380 ng/mL) are correct.

The November report does not have this issue - FERRITIN is correctly placed under `IRON, TIBC AND FERRITIN PANEL`.

## 3. Panel assignment error: AST/ALT under CMP (April 2025)

**Affected rows (report_date = 2025-04-18):**

| panel_name (current) | test_name | value | panel_name (expected) |
|---|---|---|---|
| COMPREHENSIVE METABOLIC PANEL | AST | 24 | AST |
| COMPREHENSIVE METABOLIC PANEL | ALT | 22 | ALT |

The April report does not include a Comprehensive Metabolic Panel. AST and ALT are standalone sections in the PDF. The parser incorrectly assigned them to a CMP panel that doesn't exist in this report.

The November report correctly has AST and ALT under CMP, since that panel was actually ordered.

## 4. Unit parsing error: MCV unit "IL" should be "fL" (both reports)

**Affected rows (both report dates):**

| report_date | test_name | unit (current) | unit (expected) | ref_range_text (current) |
|---|---|---|---|---|
| 2025-04-18 | MCV | IL | fL | 80.0-100.0 IL |
| 2025-11-14 | MCV | IL | fL | 80.0-100.0 IL |

The PDF shows "fL" (femtoliters) but the parser read the lowercase "f" as uppercase "I", producing "IL". This is likely a font/glyph mapping issue in the PDF text extraction.
