"""PDF parser for Quest Diagnostics lab reports.

Uses Claude vision API to extract structured data from PDF page images.
Converts PDF pages to images, sends them to Claude for extraction, and
returns ParsedReport objects compatible with the existing database layer.
"""

from __future__ import annotations

import base64
import io
import os
import re
import subprocess
import time
from datetime import date, datetime
from pathlib import Path

import anthropic
from pdf2image import convert_from_path

from .models import ParsedNote, ParsedReport, ParsedResult, ParseWarning
from .prompt import (
    HEADER_PROMPT,
    HEADER_SCHEMA,
    RESULTS_PROMPT,
    RESULTS_SCHEMA,
)

# Pages per API call for result extraction
_BATCH_SIZE = 4

# DPI for page rendering — 150 is enough for readable text
_RENDER_DPI = 150

# Model to use for extraction — haiku is fast, cheap, and accurate for this
_MODEL = "claude-haiku-4-5-20251001"


# Known font/glyph mapping errors in PDF text extraction.
# PDF fonts sometimes render glyphs differently than their Unicode mapping,
# causing OCR/text-extraction to produce wrong characters.
_UNIT_CORRECTIONS = {
    "IL": "fL",  # femtoliters — PDF glyph for "f" misread as "I"
}


def _post_process_results(results: list[ParsedResult]) -> list[ParsedResult]:
    """Apply deterministic post-processing corrections to extracted results."""
    for r in results:
        # Fix known unit glyph errors
        if r.unit in _UNIT_CORRECTIONS:
            old_unit = r.unit
            new_unit = _UNIT_CORRECTIONS[old_unit]
            r.unit = new_unit
            if old_unit in r.ref_range_text:
                r.ref_range_text = r.ref_range_text.replace(old_unit, new_unit)
    return results


def parse_pdf(
    pdf_path: Path | str,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> ParsedReport:
    """Parse a Quest Diagnostics PDF report using Claude vision.

    Args:
        pdf_path: Path to the PDF file.
        api_key: Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
        model: Model to use for extraction. Defaults to Haiku.

    Returns:
        ParsedReport with all extracted data.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError(
            "Anthropic API key required. Set ANTHROPIC_API_KEY or pass api_key=."
        )

    client = anthropic.Anthropic(api_key=key)
    use_model = model or _MODEL

    # Convert PDF pages to base64-encoded PNGs
    page_images = _pdf_to_images(pdf_path)
    if not page_images:
        raise ValueError(f"No pages extracted from {pdf_path}")

    # Pass 1: Extract header from page 1
    header = _extract_header(client, use_model, page_images[0])

    warnings: list[ParseWarning] = []

    # Cross-check header fields (DOB, age) against raw text
    text_header = _extract_header_from_text(pdf_path)
    header_warnings = _validate_header(header, text_header)
    warnings.extend(header_warnings)

    # Pass 2: Extract results from all pages in batches
    # Provide the collection date so the LLM can distinguish current results
    # from historical chart data that may appear on the same pages.
    collected_at = header.get("collected_at")
    all_results, all_notes = _extract_results(
        client, use_model, page_images, collected_at=collected_at
    )

    # Pass 3: Cross-check against raw text to catch missed results and
    # verify extracted values
    text_values = _extract_text_values(pdf_path)
    missed = _find_missed_names(text_values, all_results)
    if missed:
        warnings.append(ParseWarning(
            level="warn",
            message=(
                f"Cross-check found {len(missed)} test(s) in raw text "
                f"not in initial extraction: {', '.join(missed)}. "
                f"Running recovery pass."
            ),
        ))
        recovered, extra_notes = _recover_missed(
            client, use_model, page_images, missed
        )
        all_results.extend(recovered)
        all_notes.extend(extra_notes)

        # Report on recovery outcome
        recovered_names = {r.test_name.upper() for r in recovered}
        still_missing = set(n.upper() for n in missed) - recovered_names
        if still_missing:
            warnings.append(ParseWarning(
                level="error",
                message=(
                    f"Recovery pass failed to extract {len(still_missing)} "
                    f"test(s): {', '.join(sorted(still_missing))}. "
                    f"These may need manual review."
                ),
            ))
        else:
            warnings.append(ParseWarning(
                level="warn",
                message=f"Recovery pass successfully extracted all {len(missed)} missed test(s).",
            ))

    # Deduplicate results that may span page boundaries
    results = _deduplicate_results(all_results)

    # Pass 4: Verify extracted numeric values against raw text
    value_warnings = _verify_values(results, text_values)
    warnings.extend(value_warnings)

    # Post-processing: fix known deterministic issues (unit glyphs, etc.)
    results = _post_process_results(results)

    return ParsedReport(
        patient_name=header.get("patient_name", ""),
        dob=_parse_date(header.get("dob")),
        age=header.get("age"),
        sex=header.get("sex"),
        fasting=header.get("fasting", False),
        specimen_id=header.get("specimen_id"),
        requisition_id=header.get("requisition_id"),
        lab_reference_id=header.get("lab_reference_id"),
        collected_at=_parse_datetime(header.get("collected_at")),
        received_at=_parse_datetime(header.get("received_at")),
        reported_at=_parse_datetime(header.get("reported_at")),
        report_status=header.get("report_status"),
        results=results,
        notes=all_notes,
        warnings=warnings,
    )


_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 5  # seconds


def _call_api(client: anthropic.Anthropic, **kwargs) -> anthropic.types.Message:
    """Call the Claude API with retries on transient errors."""
    for attempt in range(_MAX_RETRIES):
        try:
            return client.messages.create(**kwargs)
        except anthropic.APIStatusError as e:
            if e.status_code in (429, 529) and attempt < _MAX_RETRIES - 1:
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                time.sleep(delay)
                continue
            raise
    raise RuntimeError("Unreachable")  # pragma: no cover


def _pdf_to_images(pdf_path: Path) -> list[str]:
    """Convert PDF pages to base64-encoded PNGs."""
    pages = convert_from_path(pdf_path, dpi=_RENDER_DPI)
    encoded = []
    for page in pages:
        buf = io.BytesIO()
        page.save(buf, format="PNG")
        encoded.append(base64.standard_b64encode(buf.getvalue()).decode("ascii"))
    return encoded


def _extract_header(
    client: anthropic.Anthropic, model: str, page_b64: str
) -> dict:
    """Extract patient/specimen metadata from page 1."""
    response = _call_api(client,
        model=model,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": page_b64,
                        },
                    },
                    {"type": "text", "text": HEADER_PROMPT},
                ],
            }
        ],
    )
    return _parse_json_response(response, HEADER_SCHEMA)


def _extract_results(
    client: anthropic.Anthropic,
    model: str,
    page_images: list[str],
    *,
    collected_at: str | None = None,
) -> tuple[list[ParsedResult], list[ParsedNote]]:
    """Extract test results from all pages in batches."""
    all_results: list[ParsedResult] = []
    all_notes: list[ParsedNote] = []

    # Track the last panel name seen across batches so the model can
    # correctly assign tests that appear at the top of a new batch
    # without a panel heading visible on that page.
    last_panel = ""

    for batch_start in range(0, len(page_images), _BATCH_SIZE):
        batch = page_images[batch_start : batch_start + _BATCH_SIZE]

        prompt = RESULTS_PROMPT
        if collected_at:
            prompt += (
                f"\n\nIMPORTANT: This report was collected on {collected_at}. "
                f"Some pages contain historical trend charts showing results "
                f"from multiple dates. Only extract the CURRENT result values "
                f"for this report date — ignore values from charts or other dates."
            )
        if last_panel:
            prompt += (
                f"\n\nContext: the most recent panel heading from prior pages "
                f'was "{last_panel}". If a page starts with test results '
                f"before any new panel heading, they belong to that panel."
            )

        content: list[dict] = []
        for img_b64 in batch:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_b64,
                    },
                }
            )
        content.append({"type": "text", "text": prompt})

        response = _call_api(client,
            model=model,
            max_tokens=8192,
            messages=[{"role": "user", "content": content}],
        )

        data = _parse_json_response(response, RESULTS_SCHEMA)

        for r in data.get("results", []):
            result = _build_result(r)
            if result:
                all_results.append(result)
                last_panel = result.panel_name

        for n in data.get("notes", []):
            note = _build_note(n)
            if note:
                all_notes.append(note)

    return all_results, all_notes


def _parse_json_response(response, schema: dict) -> dict:
    """Extract JSON from a Claude response, with basic validation."""
    import json

    text = response.content[0].text.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        lines = [l for l in lines[1:] if l.strip() != "```"]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse Claude response as JSON: {e}\n{text[:500]}")

    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object, got {type(data).__name__}")

    # Verify required keys are present
    for key in schema.get("required", []):
        if key not in data:
            raise ValueError(f"Missing required key in response: {key}")

    return data


def _build_result(raw: dict) -> ParsedResult | None:
    """Build a ParsedResult from raw LLM output, with validation."""
    panel = (raw.get("panel_name") or "").strip()
    test = (raw.get("test_name") or "").strip()
    if not panel or not test:
        return None

    value_text = (raw.get("value_text") or "").strip()
    if not value_text:
        return None

    # Every real lab value contains at least one digit (e.g., "229", "<0.5",
    # "152 H"). Reject hallucinated entries like "Not shown in extraction area".
    if not any(c.isdigit() for c in value_text):
        return None

    # Validate flag
    flag = raw.get("flag")
    if flag not in ("H", "L", None):
        flag = None

    # Validate prefix
    prefix = raw.get("value_prefix")
    if prefix not in ("<", ">", None):
        prefix = None

    # Validate numeric value
    value = raw.get("value")
    if value is not None:
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = None

    # Validate ref range bounds
    ref_low = raw.get("ref_range_low")
    if ref_low is not None:
        try:
            ref_low = float(ref_low)
        except (TypeError, ValueError):
            ref_low = None

    ref_high = raw.get("ref_range_high")
    if ref_high is not None:
        try:
            ref_high = float(ref_high)
        except (TypeError, ValueError):
            ref_high = None

    return ParsedResult(
        panel_name=panel,
        test_name=test,
        value=value,
        value_text=value_text,
        value_prefix=prefix,
        unit=raw.get("unit", ""),
        flag=flag,
        is_calculated=bool(raw.get("is_calculated", False)),
        ref_range_low=ref_low,
        ref_range_high=ref_high,
        ref_range_text=raw.get("ref_range_text", ""),
    )


def _build_note(raw: dict) -> ParsedNote | None:
    """Build a ParsedNote from raw LLM output."""
    panel = (raw.get("panel_name") or "").strip()
    content = (raw.get("content") or "").strip()
    if not panel or not content:
        return None

    note_type = raw.get("note_type", "clinical")
    valid_types = {"comment", "risk_category", "clinical", "methodology"}
    if note_type not in valid_types:
        note_type = "clinical"

    return ParsedNote(
        panel_name=panel,
        test_name=raw.get("test_name"),
        note_type=note_type,
        content=content,
    )


def _deduplicate_results(results: list[ParsedResult]) -> list[ParsedResult]:
    """Remove duplicate results from page-boundary overlaps.

    Keeps the last occurrence (from the later batch) since it may have
    more complete data (unit, ref range from the next page).
    """
    seen: dict[tuple[str, str], int] = {}
    for i, r in enumerate(results):
        key = (r.panel_name, r.test_name)
        seen[key] = i

    # Rebuild in original order, keeping only the last occurrence of each key
    indices = sorted(seen.values())
    return [results[i] for i in indices]


# --- Text-based cross-check for header fields (DOB, age) ---

# Regex patterns for Quest Diagnostics header fields in pdftotext output
_DOB_RE = re.compile(r"DOB[:\s]+(\d{2}/\d{2}/\d{4})")
_AGE_RE = re.compile(r"Age[:\s]+(\d+)", re.IGNORECASE)


def _extract_header_from_text(pdf_path: Path) -> dict[str, str | None]:
    """Extract DOB and age from raw PDF text via pdftotext.

    Returns a dict with 'dob' (MM/DD/YYYY string) and 'age' (string).
    These are deterministic text extractions, more reliable than LLM
    vision for structured header fields.
    """
    result = subprocess.run(
        ["pdftotext", "-f", "1", "-l", "1", "-layout", str(pdf_path), "-"],
        capture_output=True,
        text=True,
    )
    raw_text = result.stdout
    if not raw_text:
        return {"dob": None, "age": None}

    dob_match = _DOB_RE.search(raw_text)
    age_match = _AGE_RE.search(raw_text)

    return {
        "dob": dob_match.group(1) if dob_match else None,
        "age": age_match.group(1) if age_match else None,
    }


def _validate_header(header: dict, text_header: dict) -> list[ParseWarning]:
    """Cross-check LLM-extracted header against text extraction.

    Validates DOB and age fields. If the text extraction found values
    that differ from the LLM extraction, auto-corrects to the text
    values (which are deterministic and more reliable for structured
    fields). Also validates that age is consistent with DOB and
    collection date.
    """
    warnings: list[ParseWarning] = []

    # Cross-check DOB
    text_dob_str = text_header.get("dob")
    if text_dob_str:
        text_dob = _parse_date(text_dob_str)
        llm_dob = _parse_date(header.get("dob"))

        if text_dob and llm_dob and text_dob != llm_dob:
            warnings.append(ParseWarning(
                level="warn",
                message=(
                    f"DOB corrected: LLM extracted {header.get('dob')}, "
                    f"text extraction found {text_dob_str}. Using text value."
                ),
            ))
            header["dob"] = text_dob.strftime("%Y-%m-%d")
        elif text_dob and not llm_dob:
            header["dob"] = text_dob.strftime("%Y-%m-%d")

    # Cross-check age
    text_age_str = text_header.get("age")
    if text_age_str:
        try:
            text_age = int(text_age_str)
        except ValueError:
            text_age = None

        llm_age = header.get("age")
        if text_age is not None and llm_age is not None and text_age != llm_age:
            warnings.append(ParseWarning(
                level="warn",
                message=(
                    f"Age corrected: LLM extracted {llm_age}, "
                    f"text extraction found {text_age}. Using text value."
                ),
            ))
            header["age"] = text_age
        elif text_age is not None and llm_age is None:
            header["age"] = text_age

    # Validate age against DOB + collection date
    dob = _parse_date(header.get("dob"))
    collected_at = _parse_datetime(header.get("collected_at"))
    if dob and collected_at:
        collection_date = collected_at.date()
        computed_age = (
            collection_date.year - dob.year
            - ((collection_date.month, collection_date.day) < (dob.month, dob.day))
        )
        current_age = header.get("age")
        if current_age is not None and abs(current_age - computed_age) > 1:
            warnings.append(ParseWarning(
                level="warn",
                message=(
                    f"Age/DOB inconsistency: extracted age {current_age}, "
                    f"but DOB {header.get('dob')} with collection date "
                    f"{collection_date} gives age {computed_age}. "
                    f"Using computed age."
                ),
            ))
            header["age"] = computed_age
        elif current_age is None:
            header["age"] = computed_age

    return warnings


# --- Text-based cross-check for missed results and value verification ---

# Matches lines like "CHOLESTEROL, TOTAL 229" or "ALT 22" or "ESTRADIOL <30"
_TEXT_RESULT_RE = re.compile(
    r"^([A-Z][A-Z0-9,\s/\-().%*#&]+?)\s+([<>]?\d[\d.]*)\s*([HL])?\s*$"
)


def _extract_text_values(pdf_path: Path) -> dict[str, str]:
    """Extract test name → raw value string from PDF via pdftotext.

    Returns a dict mapping uppercase test names to their value strings
    as found in the raw text (e.g., {"ALT": "22", "ESTRADIOL": "<30"}).
    Keeps the first occurrence of each test name (the actual result,
    not chart labels that may repeat later).
    """
    result = subprocess.run(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        capture_output=True,
        text=True,
    )
    raw_text = result.stdout
    if not raw_text:
        return {}

    values: dict[str, str] = {}
    for line in raw_text.split("\n"):
        line = line.strip()
        m = _TEXT_RESULT_RE.match(line)
        if m:
            name = m.group(1).strip()
            value = m.group(2).strip()
            # Must be mostly uppercase (real test names are ALL CAPS)
            alpha = [c for c in name if c.isalpha()]
            if alpha and sum(1 for c in alpha if c.isupper()) / len(alpha) >= 0.8:
                key = name.upper()
                if key not in values:
                    values[key] = value
    return values


def _find_missed_names(
    text_values: dict[str, str],
    extracted: list[ParsedResult],
) -> list[str]:
    """Find test names in raw text that are missing from LLM extraction."""
    extracted_names = {r.test_name.upper().strip() for r in extracted}
    return sorted(name for name in text_values if name not in extracted_names)


def _verify_values(
    results: list[ParsedResult],
    text_values: dict[str, str],
) -> list[ParseWarning]:
    """Cross-check LLM-extracted numeric values against raw text.

    Compares the numeric value the LLM extracted with what pdftotext found
    for the same test name. On mismatch, auto-corrects to the text value
    (which is the first occurrence in the PDF — the actual result line,
    not historical chart data) and emits a warning.
    """
    warnings: list[ParseWarning] = []

    for r in results:
        key = r.test_name.upper().strip()
        text_val_str = text_values.get(key)
        if text_val_str is None or r.value is None:
            continue

        # Parse the text value's numeric part and prefix
        text_prefix = None
        cleaned = text_val_str
        if cleaned.startswith("<"):
            text_prefix = "<"
            cleaned = cleaned[1:]
        elif cleaned.startswith(">"):
            text_prefix = ">"
            cleaned = cleaned[1:]

        try:
            text_num = float(cleaned)
        except ValueError:
            continue

        # Compare with tolerance for floating point
        if abs(r.value - text_num) > 0.01:
            old_value = r.value
            old_text = r.value_text

            # Auto-correct: trust the text-extracted value
            r.value = text_num
            r.value_prefix = text_prefix
            r.value_text = text_val_str

            warnings.append(ParseWarning(
                level="warn",
                message=(
                    f"Value corrected for {r.test_name}: "
                    f"LLM extracted {old_text} (likely from historical chart), "
                    f"corrected to {text_val_str} from raw text."
                ),
            ))

    return warnings


def _recover_missed(
    client: anthropic.Anthropic,
    model: str,
    page_images: list[str],
    missed_names: list[str],
) -> tuple[list[ParsedResult], list[ParsedNote]]:
    """Re-query with all pages, asking specifically for missed test names."""
    names_list = ", ".join(missed_names)

    prompt = (
        f"The following test names were detected in this Quest Diagnostics report "
        f"but were not extracted in a previous pass: {names_list}\n\n"
        f"Look carefully at EVERY page, including pages dominated by charts or "
        f"graphs. These tests may appear as a single result line alongside a "
        f"large chart. Extract the full result for each of these tests.\n\n"
        + RESULTS_PROMPT
    )

    # Send all pages — we don't know which page has the missed result.
    # To stay within limits, send in batches of 4.
    all_results: list[ParsedResult] = []
    all_notes: list[ParsedNote] = []
    missed_upper = {n.upper() for n in missed_names}

    for batch_start in range(0, len(page_images), _BATCH_SIZE):
        batch = page_images[batch_start : batch_start + _BATCH_SIZE]

        content: list[dict] = []
        for img_b64 in batch:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": img_b64,
                    },
                }
            )
        content.append({"type": "text", "text": prompt})

        response = _call_api(client,
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": content}],
        )

        data = _parse_json_response(response, RESULTS_SCHEMA)

        for r in data.get("results", []):
            result = _build_result(r)
            if result and result.test_name.upper() in missed_upper:
                all_results.append(result)

        for n in data.get("notes", []):
            note = _build_note(n)
            if note:
                all_notes.append(note)

        # If we've found all missed results, stop early
        found = {r.test_name.upper() for r in all_results}
        if missed_upper <= found:
            break

    return all_results, all_notes


def _parse_date(val: str | None) -> date | None:
    """Parse a date string (YYYY-MM-DD) to a date object."""
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d").date()
    except ValueError:
        # Try MM/DD/YYYY as fallback
        try:
            return datetime.strptime(val, "%m/%d/%Y").date()
        except ValueError:
            return None


def _parse_datetime(val: str | None) -> datetime | None:
    """Parse an ISO-ish datetime string to a datetime object."""
    if not val:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None
