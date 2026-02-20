"""Shared test fixtures for questy tests."""

from __future__ import annotations

import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest

from questy.localdb.db import QuestyDB
from questy.parser.models import ParsedNote, ParsedReport, ParsedResult, ParseWarning


@pytest.fixture
def tmp_db(tmp_path):
    """Create a QuestyDB backed by a temporary file."""
    db_path = tmp_path / "test.db"
    return QuestyDB(db_path)


@pytest.fixture
def sample_result():
    """A typical parsed lab result."""
    return ParsedResult(
        panel_name="LIPID PANEL, STANDARD",
        test_name="CHOLESTEROL, TOTAL",
        value=229.0,
        value_text="229",
        value_prefix=None,
        unit="mg/dL",
        flag="H",
        is_calculated=False,
        ref_range_low=None,
        ref_range_high=200.0,
        ref_range_text="<200",
    )


@pytest.fixture
def sample_note():
    """A typical parsed clinical note."""
    return ParsedNote(
        panel_name="LIPID PANEL, STANDARD",
        test_name="LDL-CHOLESTEROL",
        note_type="risk_category",
        content="Desirable: <100 mg/dL",
    )


@pytest.fixture
def sample_report(sample_result, sample_note):
    """A minimal parsed report with one result and one note."""
    return ParsedReport(
        patient_name="DOE, JOHN",
        dob=date(1990, 1, 15),
        age=35,
        sex="M",
        fasting=True,
        specimen_id="SPC-123",
        requisition_id="REQ-456",
        lab_reference_id="LAB-789",
        collected_at=datetime(2025, 4, 11, 7, 30),
        received_at=datetime(2025, 4, 11, 10, 0),
        reported_at=datetime(2025, 4, 12, 14, 0),
        report_status="FINAL",
        results=[sample_result],
        notes=[sample_note],
    )


@pytest.fixture
def multi_result_report():
    """A report with multiple results across panels."""
    results = [
        ParsedResult(
            panel_name="LIPID PANEL, STANDARD",
            test_name="CHOLESTEROL, TOTAL",
            value=229.0,
            value_text="229",
            value_prefix=None,
            unit="mg/dL",
            flag="H",
            is_calculated=False,
            ref_range_low=None,
            ref_range_high=200.0,
            ref_range_text="<200",
        ),
        ParsedResult(
            panel_name="LIPID PANEL, STANDARD",
            test_name="HDL CHOLESTEROL",
            value=55.0,
            value_text="55",
            value_prefix=None,
            unit="mg/dL",
            flag=None,
            is_calculated=False,
            ref_range_low=40.0,
            ref_range_high=None,
            ref_range_text="> OR = 40",
        ),
        ParsedResult(
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
        ),
        ParsedResult(
            panel_name="CBC (INCLUDES DIFF/PLT)",
            test_name="WHITE BLOOD CELL COUNT",
            value=6.5,
            value_text="6.5",
            value_prefix=None,
            unit="Thousand/uL",
            flag=None,
            is_calculated=False,
            ref_range_low=3.8,
            ref_range_high=10.8,
            ref_range_text="3.8-10.8",
        ),
        ParsedResult(
            panel_name="COMPREHENSIVE METABOLIC PANEL",
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
        ),
    ]
    notes = [
        ParsedNote(
            panel_name="LIPID PANEL, STANDARD",
            test_name="LDL-CHOLESTEROL",
            note_type="risk_category",
            content="Desirable: <100 mg/dL",
        ),
        ParsedNote(
            panel_name="CBC (INCLUDES DIFF/PLT)",
            test_name=None,
            note_type="methodology",
            content="Testing performed by Quest Diagnostics.",
        ),
    ]
    return ParsedReport(
        patient_name="DOE, JANE",
        dob=date(1985, 6, 20),
        age=40,
        sex="F",
        fasting=True,
        specimen_id="SPC-999",
        requisition_id="REQ-888",
        lab_reference_id="LAB-777",
        collected_at=datetime(2025, 4, 11, 7, 30),
        received_at=datetime(2025, 4, 11, 10, 0),
        reported_at=datetime(2025, 4, 12, 14, 0),
        report_status="FINAL",
        results=results,
        notes=notes,
    )
