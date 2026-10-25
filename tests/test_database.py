"""Tests for questy.localdb — database layer."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

import pytest

from questy.localdb.db import QuestyDB
from questy.localdb.models import Base, Note, Report, Result
from questy.parser.models import ParsedNote, ParsedReport, ParsedResult


class TestQuestyDBInit:
    def test_creates_db_file(self, tmp_path):
        db_path = tmp_path / "test.db"
        db = QuestyDB(db_path)
        assert db_path.exists()

    def test_creates_parent_dirs(self, tmp_path):
        db_path = tmp_path / "subdir" / "deep" / "test.db"
        db = QuestyDB(db_path)
        assert db_path.parent.exists()

    def test_tables_created(self, tmp_db):
        session = tmp_db.get_session()
        # Verify tables exist by querying them
        assert session.query(Report).count() == 0
        assert session.query(Result).count() == 0
        assert session.query(Note).count() == 0
        session.close()

    def test_adds_pdf_sha256_to_existing_db(self, tmp_path, sample_report):
        """A database created before pdf_sha256 existed gains the column."""
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE reports (report_date DATE PRIMARY KEY, patient_name VARCHAR, "
            "dob DATE, age INTEGER, sex VARCHAR, fasting BOOLEAN, specimen_id VARCHAR, "
            "requisition_id VARCHAR, lab_reference_id VARCHAR, collected_at DATETIME, "
            "received_at DATETIME, reported_at DATETIME, report_status VARCHAR, "
            "pdf_path TEXT, imported_at DATETIME)"
        )
        conn.execute("INSERT INTO reports (report_date) VALUES ('2025-01-01')")
        conn.commit()
        conn.close()

        db = QuestyDB(db_path)
        db.import_report(sample_report, pdf_sha256="ab" * 32)

        with db.get_session() as session:
            assert session.get(Report, date(2025, 1, 1)).pdf_sha256 is None
            assert session.get(Report, date(2025, 4, 11)).pdf_sha256 == "ab" * 32

        # Opening it again does not try to add the column twice
        QuestyDB(db_path)


class TestImportReport:
    def test_import_basic_report(self, tmp_db, sample_report):
        tmp_db.import_report(sample_report, pdf_path="/tmp/test.pdf")

        with tmp_db.get_session() as session:
            reports = session.query(Report).all()
            assert len(reports) == 1
            assert reports[0].report_date == date(2025, 4, 11)
            assert reports[0].pdf_path == "/tmp/test.pdf"

    def test_import_results(self, tmp_db, sample_report):
        tmp_db.import_report(sample_report)

        with tmp_db.get_session() as session:
            results = session.query(Result).all()
            assert len(results) == 1
            r = results[0]
            assert r.test_name == "CHOLESTEROL, TOTAL"
            assert r.value == 229.0
            assert r.flag == "H"
            assert r.unit == "mg/dL"

    def test_import_notes(self, tmp_db, sample_report):
        tmp_db.import_report(sample_report)

        with tmp_db.get_session() as session:
            notes = session.query(Note).all()
            assert len(notes) == 1
            assert notes[0].note_type == "risk_category"
            assert "Desirable" in notes[0].content

    def test_import_multi_result_report(self, tmp_db, multi_result_report):
        tmp_db.import_report(multi_result_report)

        with tmp_db.get_session() as session:
            results = session.query(Result).all()
            assert len(results) == 5

            notes = session.query(Note).all()
            assert len(notes) == 2

    def test_upsert_replaces_existing(self, tmp_db, sample_report):
        tmp_db.import_report(sample_report, pdf_path="/first.pdf")
        tmp_db.import_report(sample_report, pdf_path="/second.pdf")

        with tmp_db.get_session() as session:
            reports = session.query(Report).all()
            assert len(reports) == 1
            assert reports[0].pdf_path == "/second.pdf"

    def test_upsert_replaces_results_and_notes(self, tmp_db, sample_report):
        """Upserting should delete old results/notes and insert new ones."""
        tmp_db.import_report(sample_report)

        # Create modified report with different result for same date
        modified = ParsedReport(
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
            results=[
                ParsedResult(
                    panel_name="CMP",
                    test_name="GLUCOSE",
                    value=100.0,
                    value_text="100",
                    value_prefix=None,
                    unit="mg/dL",
                    flag=None,
                    is_calculated=False,
                    ref_range_low=65.0,
                    ref_range_high=99.0,
                    ref_range_text="65-99",
                ),
            ],
            notes=[],
        )
        tmp_db.import_report(modified)

        with tmp_db.get_session() as session:
            results = session.query(Result).all()
            assert len(results) == 1
            assert results[0].test_name == "GLUCOSE"

            notes = session.query(Note).all()
            assert len(notes) == 0

    def test_import_no_report_date_raises(self, tmp_db):
        report = ParsedReport(
            patient_name="DOE, JOHN",
            dob=None,
            age=None,
            sex=None,
            fasting=False,
            specimen_id=None,
            requisition_id=None,
            lab_reference_id=None,
            collected_at=None,  # No collection date = no report_date
            received_at=None,
            reported_at=None,
            report_status=None,
        )
        with pytest.raises(ValueError, match="no collection date"):
            tmp_db.import_report(report)

    def test_import_multiple_dates(self, tmp_db, sample_report, multi_result_report):
        tmp_db.import_report(sample_report)
        tmp_db.import_report(multi_result_report)

        with tmp_db.get_session() as session:
            reports = session.query(Report).all()
            # Both have same collection date (2025-04-11), so upsert
            assert len(reports) == 1


class TestGetImportedDates:
    def test_empty_db(self, tmp_db):
        assert tmp_db.get_imported_dates() == set()

    def test_after_import(self, tmp_db, sample_report):
        tmp_db.import_report(sample_report)
        dates = tmp_db.get_imported_dates()
        assert date(2025, 4, 11) in dates

    def test_multiple_imports(self, tmp_db):
        for month in (1, 2, 3):
            report = ParsedReport(
                patient_name="DOE, JOHN",
                dob=None,
                age=None,
                sex=None,
                fasting=False,
                specimen_id=None,
                requisition_id=None,
                lab_reference_id=None,
                collected_at=datetime(2025, month, 1, 8, 0),
                received_at=None,
                reported_at=None,
                report_status=None,
            )
            tmp_db.import_report(report)

        dates = tmp_db.get_imported_dates()
        assert len(dates) == 3
        assert date(2025, 1, 1) in dates
        assert date(2025, 2, 1) in dates
        assert date(2025, 3, 1) in dates
