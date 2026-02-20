"""Tests for questy.parser.models — dataclasses and properties."""

from __future__ import annotations

from datetime import date, datetime

from questy.parser.models import ParsedNote, ParsedReport, ParsedResult, ParseWarning


class TestParsedResult:
    def test_basic_creation(self, sample_result):
        assert sample_result.panel_name == "LIPID PANEL, STANDARD"
        assert sample_result.test_name == "CHOLESTEROL, TOTAL"
        assert sample_result.value == 229.0
        assert sample_result.flag == "H"
        assert sample_result.is_calculated is False

    def test_inequality_result(self):
        r = ParsedResult(
            panel_name="TESTOSTERONE, FREE",
            test_name="ESTRADIOL",
            value=None,
            value_text="<30",
            value_prefix="<",
            unit="pg/mL",
            flag=None,
            is_calculated=False,
            ref_range_low=None,
            ref_range_high=39.0,
            ref_range_text="< OR = 39",
        )
        assert r.value is None
        assert r.value_prefix == "<"
        assert r.value_text == "<30"

    def test_calculated_result(self):
        r = ParsedResult(
            panel_name="LIPID PANEL, STANDARD",
            test_name="LDL-CHOLESTEROL",
            value=152.0,
            value_text="152 H",
            value_prefix=None,
            unit="mg/dL",
            flag="H",
            is_calculated=True,
            ref_range_low=None,
            ref_range_high=130.0,
            ref_range_text="<130 (calc)",
        )
        assert r.is_calculated is True
        assert r.flag == "H"

    def test_normal_result_no_flag(self):
        r = ParsedResult(
            panel_name="CMP",
            test_name="GLUCOSE",
            value=95.0,
            value_text="95",
            value_prefix=None,
            unit="mg/dL",
            flag=None,
            is_calculated=False,
            ref_range_low=65.0,
            ref_range_high=99.0,
            ref_range_text="65-99",
        )
        assert r.flag is None
        assert r.ref_range_low == 65.0
        assert r.ref_range_high == 99.0


class TestParsedNote:
    def test_test_level_note(self, sample_note):
        assert sample_note.test_name == "LDL-CHOLESTEROL"
        assert sample_note.note_type == "risk_category"

    def test_panel_level_note(self):
        n = ParsedNote(
            panel_name="CBC (INCLUDES DIFF/PLT)",
            test_name=None,
            note_type="methodology",
            content="Testing performed by Quest Diagnostics.",
        )
        assert n.test_name is None
        assert n.note_type == "methodology"


class TestParseWarning:
    def test_warn_level(self):
        w = ParseWarning(level="warn", message="Value corrected")
        assert w.level == "warn"

    def test_error_level(self):
        w = ParseWarning(level="error", message="Recovery failed")
        assert w.level == "error"


class TestParsedReport:
    def test_report_date_from_collected_at(self, sample_report):
        assert sample_report.report_date == date(2025, 4, 11)

    def test_report_date_none_when_no_collected_at(self):
        r = ParsedReport(
            patient_name="DOE, JOHN",
            dob=None,
            age=None,
            sex=None,
            fasting=False,
            specimen_id=None,
            requisition_id=None,
            lab_reference_id=None,
            collected_at=None,
            received_at=None,
            reported_at=None,
            report_status=None,
        )
        assert r.report_date is None

    def test_default_empty_lists(self):
        r = ParsedReport(
            patient_name="DOE, JOHN",
            dob=None,
            age=None,
            sex=None,
            fasting=False,
            specimen_id=None,
            requisition_id=None,
            lab_reference_id=None,
            collected_at=None,
            received_at=None,
            reported_at=None,
            report_status=None,
        )
        assert r.results == []
        assert r.notes == []
        assert r.warnings == []

    def test_report_with_results_and_notes(self, sample_report):
        assert len(sample_report.results) == 1
        assert len(sample_report.notes) == 1
        assert sample_report.patient_name == "DOE, JOHN"
        assert sample_report.sex == "M"
        assert sample_report.fasting is True
