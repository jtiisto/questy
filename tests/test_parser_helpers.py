"""Tests for questy.parser.pdf_parser helper functions.

These test the internal functions that don't require API calls or PDF files:
_build_result, _build_note, _deduplicate_results, _parse_date,
_parse_datetime, _parse_json_response, _find_missed_names, _verify_values.
"""

from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from questy.parser.models import ParsedResult, ParseWarning
from questy.parser.pdf_parser import (
    _build_note,
    _build_result,
    _deduplicate_results,
    _find_missed_names,
    _parse_date,
    _parse_datetime,
    _parse_json_response,
    _verify_values,
)


# --- _build_result ---


class TestBuildResult:
    def test_valid_result(self):
        raw = {
            "panel_name": "LIPID PANEL, STANDARD",
            "test_name": "CHOLESTEROL, TOTAL",
            "value": 229,
            "value_text": "229",
            "value_prefix": None,
            "unit": "mg/dL",
            "flag": "H",
            "is_calculated": False,
            "ref_range_low": None,
            "ref_range_high": 200,
            "ref_range_text": "<200",
        }
        result = _build_result(raw)
        assert result is not None
        assert result.panel_name == "LIPID PANEL, STANDARD"
        assert result.test_name == "CHOLESTEROL, TOTAL"
        assert result.value == 229.0
        assert result.flag == "H"

    def test_missing_panel_name(self):
        raw = {
            "panel_name": "",
            "test_name": "CHOLESTEROL, TOTAL",
            "value": 229,
            "value_text": "229",
            "value_prefix": None,
            "unit": "mg/dL",
            "flag": None,
            "is_calculated": False,
            "ref_range_low": None,
            "ref_range_high": None,
            "ref_range_text": "",
        }
        assert _build_result(raw) is None

    def test_missing_test_name(self):
        raw = {
            "panel_name": "LIPID PANEL",
            "test_name": "",
            "value": 229,
            "value_text": "229",
            "value_prefix": None,
            "unit": "mg/dL",
            "flag": None,
            "is_calculated": False,
            "ref_range_low": None,
            "ref_range_high": None,
            "ref_range_text": "",
        }
        assert _build_result(raw) is None

    def test_missing_value_text(self):
        raw = {
            "panel_name": "LIPID PANEL",
            "test_name": "CHOLESTEROL",
            "value": 229,
            "value_text": "",
            "value_prefix": None,
            "unit": "mg/dL",
            "flag": None,
            "is_calculated": False,
            "ref_range_low": None,
            "ref_range_high": None,
            "ref_range_text": "",
        }
        assert _build_result(raw) is None

    def test_value_text_no_digits_rejected(self):
        """Hallucinated entries like 'Not shown' are rejected."""
        raw = {
            "panel_name": "LIPID PANEL",
            "test_name": "CHOLESTEROL",
            "value": None,
            "value_text": "Not shown in extraction area",
            "value_prefix": None,
            "unit": "mg/dL",
            "flag": None,
            "is_calculated": False,
            "ref_range_low": None,
            "ref_range_high": None,
            "ref_range_text": "",
        }
        assert _build_result(raw) is None

    def test_invalid_flag_reset_to_none(self):
        raw = {
            "panel_name": "CMP",
            "test_name": "GLUCOSE",
            "value": 95,
            "value_text": "95",
            "value_prefix": None,
            "unit": "mg/dL",
            "flag": "INVALID",
            "is_calculated": False,
            "ref_range_low": None,
            "ref_range_high": None,
            "ref_range_text": "",
        }
        result = _build_result(raw)
        assert result is not None
        assert result.flag is None

    def test_invalid_prefix_reset_to_none(self):
        raw = {
            "panel_name": "CMP",
            "test_name": "GLUCOSE",
            "value": 95,
            "value_text": "95",
            "value_prefix": ">=",
            "unit": "mg/dL",
            "flag": None,
            "is_calculated": False,
            "ref_range_low": None,
            "ref_range_high": None,
            "ref_range_text": "",
        }
        result = _build_result(raw)
        assert result is not None
        assert result.value_prefix is None

    def test_invalid_numeric_value_set_to_none(self):
        raw = {
            "panel_name": "CMP",
            "test_name": "GLUCOSE",
            "value": "not-a-number",
            "value_text": "95",
            "value_prefix": None,
            "unit": "mg/dL",
            "flag": None,
            "is_calculated": False,
            "ref_range_low": None,
            "ref_range_high": None,
            "ref_range_text": "",
        }
        result = _build_result(raw)
        assert result is not None
        assert result.value is None

    def test_invalid_ref_range_bounds_set_to_none(self):
        raw = {
            "panel_name": "CMP",
            "test_name": "GLUCOSE",
            "value": 95,
            "value_text": "95",
            "value_prefix": None,
            "unit": "mg/dL",
            "flag": None,
            "is_calculated": False,
            "ref_range_low": "bad",
            "ref_range_high": "bad",
            "ref_range_text": "",
        }
        result = _build_result(raw)
        assert result is not None
        assert result.ref_range_low is None
        assert result.ref_range_high is None

    def test_inequality_value(self):
        raw = {
            "panel_name": "TESTOSTERONE",
            "test_name": "ESTRADIOL",
            "value": 0.5,
            "value_text": "<0.5",
            "value_prefix": "<",
            "unit": "pg/mL",
            "flag": None,
            "is_calculated": False,
            "ref_range_low": None,
            "ref_range_high": 39.0,
            "ref_range_text": "< OR = 39",
        }
        result = _build_result(raw)
        assert result is not None
        assert result.value_prefix == "<"
        assert result.value == 0.5

    def test_flag_low(self):
        raw = {
            "panel_name": "IRON PANEL",
            "test_name": "IRON",
            "value": 25,
            "value_text": "25 L",
            "value_prefix": None,
            "unit": "mcg/dL",
            "flag": "L",
            "is_calculated": False,
            "ref_range_low": 50,
            "ref_range_high": 195,
            "ref_range_text": "50-195",
        }
        result = _build_result(raw)
        assert result is not None
        assert result.flag == "L"


# --- _build_note ---


class TestBuildNote:
    def test_valid_note(self):
        raw = {
            "panel_name": "LIPID PANEL",
            "test_name": "LDL-CHOLESTEROL",
            "note_type": "risk_category",
            "content": "Desirable: <100 mg/dL",
        }
        note = _build_note(raw)
        assert note is not None
        assert note.note_type == "risk_category"
        assert note.test_name == "LDL-CHOLESTEROL"

    def test_panel_level_note(self):
        raw = {
            "panel_name": "CBC",
            "test_name": None,
            "note_type": "methodology",
            "content": "Testing performed by Quest.",
        }
        note = _build_note(raw)
        assert note is not None
        assert note.test_name is None

    def test_missing_panel_returns_none(self):
        raw = {
            "panel_name": "",
            "test_name": None,
            "note_type": "clinical",
            "content": "Some content",
        }
        assert _build_note(raw) is None

    def test_missing_content_returns_none(self):
        raw = {
            "panel_name": "CBC",
            "test_name": None,
            "note_type": "clinical",
            "content": "",
        }
        assert _build_note(raw) is None

    def test_invalid_note_type_defaults_to_clinical(self):
        raw = {
            "panel_name": "CBC",
            "test_name": None,
            "note_type": "unknown_type",
            "content": "Some note",
        }
        note = _build_note(raw)
        assert note is not None
        assert note.note_type == "clinical"


# --- _deduplicate_results ---


class TestDeduplicateResults:
    def test_no_duplicates(self):
        results = [
            ParsedResult("LIPID", "CHOL", 229.0, "229", None, "mg/dL", None, False, None, 200.0, "<200"),
            ParsedResult("LIPID", "HDL", 55.0, "55", None, "mg/dL", None, False, 40.0, None, "> OR = 40"),
        ]
        deduped = _deduplicate_results(results)
        assert len(deduped) == 2

    def test_duplicates_keeps_last(self):
        r1 = ParsedResult("LIPID", "CHOL", 229.0, "229", None, "mg/dL", "H", False, None, 200.0, "<200")
        r2 = ParsedResult("LIPID", "CHOL", 229.0, "229", None, "mg/dL", "H", False, None, 200.0, "<200")
        deduped = _deduplicate_results([r1, r2])
        assert len(deduped) == 1
        # Should be the second one (last occurrence)
        assert deduped[0] is r2

    def test_preserves_order(self):
        r1 = ParsedResult("LIPID", "CHOL", 229.0, "229", None, "mg/dL", None, False, None, None, "")
        r2 = ParsedResult("CBC", "WBC", 6.5, "6.5", None, "Thousand/uL", None, False, 3.8, 10.8, "3.8-10.8")
        r3 = ParsedResult("CMP", "GLUCOSE", 95.0, "95", None, "mg/dL", None, False, 65.0, 99.0, "65-99")
        deduped = _deduplicate_results([r1, r2, r3])
        assert [r.test_name for r in deduped] == ["CHOL", "WBC", "GLUCOSE"]

    def test_empty_list(self):
        assert _deduplicate_results([]) == []


# --- _parse_date ---


class TestParseDate:
    def test_iso_format(self):
        assert _parse_date("2025-04-11") == date(2025, 4, 11)

    def test_us_format(self):
        assert _parse_date("04/11/2025") == date(2025, 4, 11)

    def test_none_input(self):
        assert _parse_date(None) is None

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_invalid_format(self):
        assert _parse_date("April 11, 2025") is None


# --- _parse_datetime ---


class TestParseDatetime:
    def test_iso_with_t(self):
        result = _parse_datetime("2025-04-11T07:30")
        assert result == datetime(2025, 4, 11, 7, 30)

    def test_iso_with_space(self):
        result = _parse_datetime("2025-04-11 07:30")
        assert result == datetime(2025, 4, 11, 7, 30)

    def test_us_format(self):
        result = _parse_datetime("04/11/2025 07:30")
        assert result == datetime(2025, 4, 11, 7, 30)

    def test_none_input(self):
        assert _parse_datetime(None) is None

    def test_empty_string(self):
        assert _parse_datetime("") is None

    def test_invalid_format(self):
        assert _parse_datetime("April 11, 2025 at 7:30 AM") is None


# --- _parse_json_response ---


class TestParseJsonResponse:
    def _make_response(self, text: str):
        """Create a mock Claude API response."""
        content_block = SimpleNamespace(text=text)
        return SimpleNamespace(content=[content_block])

    def test_valid_json(self):
        resp = self._make_response('{"patient_name": "DOE, JOHN", "dob": "1990-01-15"}')
        schema = {"required": ["patient_name"]}
        result = _parse_json_response(resp, schema)
        assert result["patient_name"] == "DOE, JOHN"

    def test_json_with_code_fences(self):
        resp = self._make_response('```json\n{"patient_name": "DOE, JOHN"}\n```')
        schema = {"required": ["patient_name"]}
        result = _parse_json_response(resp, schema)
        assert result["patient_name"] == "DOE, JOHN"

    def test_code_fences_no_language(self):
        resp = self._make_response('```\n{"patient_name": "DOE"}\n```')
        schema = {"required": ["patient_name"]}
        result = _parse_json_response(resp, schema)
        assert result["patient_name"] == "DOE"

    def test_invalid_json_raises(self):
        resp = self._make_response("not json at all")
        with pytest.raises(ValueError, match="Failed to parse"):
            _parse_json_response(resp, {})

    def test_non_object_raises(self):
        resp = self._make_response("[1, 2, 3]")
        with pytest.raises(ValueError, match="Expected JSON object"):
            _parse_json_response(resp, {})

    def test_missing_required_key_raises(self):
        resp = self._make_response('{"name": "DOE"}')
        schema = {"required": ["patient_name"]}
        with pytest.raises(ValueError, match="Missing required key"):
            _parse_json_response(resp, schema)


# --- _find_missed_names ---


class TestFindMissedNames:
    def test_all_found(self):
        text_values = {"CHOLESTEROL, TOTAL": "229", "HDL CHOLESTEROL": "55"}
        extracted = [
            ParsedResult("LP", "CHOLESTEROL, TOTAL", 229.0, "229", None, "mg/dL", None, False, None, None, ""),
            ParsedResult("LP", "HDL CHOLESTEROL", 55.0, "55", None, "mg/dL", None, False, None, None, ""),
        ]
        assert _find_missed_names(text_values, extracted) == []

    def test_some_missed(self):
        text_values = {"CHOLESTEROL, TOTAL": "229", "HDL CHOLESTEROL": "55", "TRIGLYCERIDES": "110"}
        extracted = [
            ParsedResult("LP", "CHOLESTEROL, TOTAL", 229.0, "229", None, "mg/dL", None, False, None, None, ""),
        ]
        missed = _find_missed_names(text_values, extracted)
        assert "HDL CHOLESTEROL" in missed
        assert "TRIGLYCERIDES" in missed

    def test_case_insensitive_matching(self):
        text_values = {"CHOLESTEROL, TOTAL": "229"}
        extracted = [
            ParsedResult("LP", "Cholesterol, Total", 229.0, "229", None, "mg/dL", None, False, None, None, ""),
        ]
        assert _find_missed_names(text_values, extracted) == []

    def test_empty_text_values(self):
        assert _find_missed_names({}, []) == []


# --- _verify_values ---


class TestVerifyValues:
    def test_matching_values_no_warnings(self):
        results = [
            ParsedResult("LP", "CHOL", 229.0, "229", None, "mg/dL", None, False, None, None, ""),
        ]
        text_values = {"CHOL": "229"}
        warnings = _verify_values(results, text_values)
        assert len(warnings) == 0

    def test_mismatched_value_corrected(self):
        r = ParsedResult("LP", "CHOL", 300.0, "300", None, "mg/dL", None, False, None, None, "")
        text_values = {"CHOL": "229"}
        warnings = _verify_values([r], text_values)
        assert len(warnings) == 1
        assert r.value == 229.0
        assert r.value_text == "229"
        assert "corrected" in warnings[0].message.lower()

    def test_inequality_value_corrected(self):
        r = ParsedResult("T", "ESTRADIOL", 50.0, "50", None, "pg/mL", None, False, None, None, "")
        text_values = {"ESTRADIOL": "<30"}
        warnings = _verify_values([r], text_values)
        assert len(warnings) == 1
        assert r.value == 30.0
        assert r.value_prefix == "<"

    def test_no_text_value_skipped(self):
        r = ParsedResult("LP", "CHOL", 229.0, "229", None, "mg/dL", None, False, None, None, "")
        warnings = _verify_values([r], {})
        assert len(warnings) == 0

    def test_none_value_skipped(self):
        r = ParsedResult("T", "ESTRADIOL", None, "<30", "<", "pg/mL", None, False, None, None, "")
        text_values = {"ESTRADIOL": "<30"}
        warnings = _verify_values([r], text_values)
        assert len(warnings) == 0

    def test_tolerance_small_diff(self):
        """Differences smaller than 0.01 should not trigger correction."""
        r = ParsedResult("LP", "CHOL", 229.005, "229", None, "mg/dL", None, False, None, None, "")
        text_values = {"CHOL": "229"}
        warnings = _verify_values([r], text_values)
        assert len(warnings) == 0
