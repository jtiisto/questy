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


# Schema as created before reports had an id (one report per report_date).
_LEGACY_DDL = [
    (
        "CREATE TABLE reports (report_date DATE NOT NULL, patient_name VARCHAR, dob DATE, "
        "age INTEGER, sex VARCHAR, fasting BOOLEAN, specimen_id VARCHAR, requisition_id VARCHAR, "
        "lab_reference_id VARCHAR, collected_at DATETIME, received_at DATETIME, "
        "reported_at DATETIME, report_status VARCHAR, pdf_path TEXT, {sha}imported_at DATETIME, "
        "PRIMARY KEY (report_date))"
    ),
    (
        "CREATE TABLE results (report_date DATE NOT NULL, panel_name VARCHAR NOT NULL, "
        "test_name VARCHAR NOT NULL, value FLOAT, value_text VARCHAR, value_prefix VARCHAR, "
        "unit VARCHAR, flag VARCHAR, is_calculated BOOLEAN, ref_range_low FLOAT, "
        "ref_range_high FLOAT, ref_range_text VARCHAR, "
        "PRIMARY KEY (report_date, panel_name, test_name))"
    ),
    (
        "CREATE TABLE notes (id INTEGER NOT NULL, report_date DATE, panel_name VARCHAR, "
        "test_name VARCHAR, note_type VARCHAR, content TEXT, PRIMARY KEY (id))"
    ),
]


class TestLegacyMigration:
    @pytest.fixture(params=[False, True], ids=["before-sha256", "with-sha256"])
    def legacy_db(self, request, tmp_path):
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(db_path)
        sha = "pdf_sha256 VARCHAR, " if request.param else ""
        for ddl in _LEGACY_DDL:
            conn.execute(ddl.format(sha=sha))
        conn.execute(
            "INSERT INTO reports (report_date, specimen_id, pdf_path) VALUES "
            "('2025-01-10', 'SPC-A', '/a.pdf'), ('2025-04-11', 'SPC-123', '/b.pdf')"
        )
        conn.execute(
            "INSERT INTO results (report_date, panel_name, test_name, value, value_text, unit) "
            "VALUES ('2025-01-10', 'CMP', 'GLUCOSE', 91, '91', 'mg/dL'), "
            "('2025-04-11', 'CMP', 'GLUCOSE', 95, '95', 'mg/dL'), "
            "('2025-04-11', 'CMP', 'SODIUM', 140, '140', 'mmol/L')"
        )
        conn.execute(
            "INSERT INTO notes (report_date, panel_name, note_type, content) "
            "VALUES ('2025-04-11', 'CMP', 'comment', 'Fasting')"
        )
        conn.commit()
        conn.close()
        return db_path

    def test_data_copied_and_linked(self, legacy_db):
        db = QuestyDB(legacy_db)
        with db.get_session() as session:
            reports = {r.report_date: r for r in session.query(Report).all()}
            assert set(reports) == {date(2025, 1, 10), date(2025, 4, 11)}
            assert reports[date(2025, 1, 10)].pdf_path == "/a.pdf"

            results = session.query(Result).order_by(Result.report_date, Result.test_name).all()
            assert [(r.report_date, r.test_name, r.value) for r in results] == [
                (date(2025, 1, 10), "GLUCOSE", 91.0),
                (date(2025, 4, 11), "GLUCOSE", 95.0),
                (date(2025, 4, 11), "SODIUM", 140.0),
            ]
            for r in results:
                assert r.report_id == reports[r.report_date].id

            note = session.query(Note).one()
            assert note.report_id == reports[date(2025, 4, 11)].id

        # Reopening does not migrate again
        QuestyDB(legacy_db)

    def test_migrated_db_is_additive(self, legacy_db, multi_result_report):
        db = QuestyDB(legacy_db)
        outcome = db.import_report(multi_result_report, pdf_sha256="cd" * 32)
        assert outcome.replaced is False
        assert outcome.same_date_reports == 1
        with db.get_session() as session:
            assert session.query(Report).filter_by(report_date=date(2025, 4, 11)).count() == 2
            assert session.query(Result).filter_by(report_date=date(2025, 4, 11)).count() == 7

    def test_legacy_report_reimported_in_place(self, legacy_db, sample_report):
        """A legacy row has no hash; the same specimen on the same date matches it."""
        db = QuestyDB(legacy_db)
        outcome = db.import_report(sample_report, pdf_sha256="ab" * 32)
        assert outcome.replaced is True
        with db.get_session() as session:
            report = session.query(Report).filter_by(report_date=date(2025, 4, 11)).one()
            assert report.pdf_sha256 == "ab" * 32
            names = {r.test_name for r in session.query(Result).filter_by(report_id=report.id)}
            assert names == {"CHOLESTEROL, TOTAL"}


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
            # Same collection date (2025-04-11), different specimens: both kept
            assert session.query(Report).count() == 2
            assert session.query(Result).count() == 6


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


def _report(specimen, results, requisition="REQ-1", day=date(2025, 4, 11)):
    return ParsedReport(
        patient_name="DOE, JOHN", dob=None, age=None, sex=None, fasting=False,
        specimen_id=specimen, requisition_id=requisition, lab_reference_id=None,
        collected_at=datetime.combine(day, datetime.min.time()),
        received_at=None, reported_at=None, report_status="FINAL",
        results=results, notes=[],
    )


def _res(test, value, unit="mg/dL", panel="CMP"):
    return ParsedResult(
        panel, test, value, str(value), None, unit, None, False, None, None, "",
    )


class TestSameDateReports:
    def test_second_report_is_additive(self, tmp_db):
        first = tmp_db.import_report(_report("SPC-1", [_res("GLUCOSE", 95)]), pdf_sha256="a" * 64)
        second = tmp_db.import_report(
            _report("SPC-2", [_res("TSH", 1.8, unit="mIU/L", panel="TSH")], requisition="REQ-2"),
            pdf_sha256="b" * 64,
        )
        assert first.same_date_reports == 0
        assert second.same_date_reports == 1
        assert second.replaced is False
        assert second.report_id != first.report_id

        with tmp_db.get_session() as session:
            assert session.query(Report).count() == 2
            assert {r.test_name for r in session.query(Result)} == {"GLUCOSE", "TSH"}

    def test_same_value_in_both_is_not_a_conflict(self, tmp_db):
        tmp_db.import_report(_report("SPC-1", [_res("GLUCOSE", 95)]))
        outcome = tmp_db.import_report(
            _report("SPC-2", [_res("GLUCOSE", 95, panel="GLUCOSE")], requisition="REQ-2")
        )
        assert outcome.conflicts == []
        with tmp_db.get_session() as session:
            assert session.query(Result).count() == 2

    def test_different_value_is_a_conflict(self, tmp_db):
        tmp_db.import_report(_report("SPC-1", [_res("GLUCOSE", 95), _res("SODIUM", 140, "mmol/L")]))
        outcome = tmp_db.import_report(
            _report("SPC-2", [_res("Glucose", 102, panel="GLUCOSE"), _res("SODIUM", 140, "mmol/L")],
                    requisition="REQ-2")
        )
        assert len(outcome.conflicts) == 1
        assert "Glucose" in outcome.conflicts[0]
        assert "102 mg/dL" in outcome.conflicts[0]
        assert "95 mg/dL" in outcome.conflicts[0]
        assert "SPC-1" in outcome.conflicts[0]
        # Both values are stored
        with tmp_db.get_session() as session:
            values = sorted(r.value for r in session.query(Result).filter(Result.test_name.ilike("glucose")))
            assert values == [95.0, 102.0]

    def test_different_unit_is_a_conflict(self, tmp_db):
        tmp_db.import_report(_report("SPC-1", [_res("GLUCOSE", 5.3, unit="mmol/L")]))
        outcome = tmp_db.import_report(
            _report("SPC-2", [_res("GLUCOSE", 5.3)], requisition="REQ-2")
        )
        assert len(outcome.conflicts) == 1

    def test_other_dates_ignored(self, tmp_db):
        tmp_db.import_report(_report("SPC-1", [_res("GLUCOSE", 95)], day=date(2025, 1, 1)))
        outcome = tmp_db.import_report(_report("SPC-2", [_res("GLUCOSE", 120)]))
        assert outcome.conflicts == []
        assert outcome.same_date_reports == 0

    def test_reimport_same_pdf_replaces_only_that_report(self, tmp_db):
        tmp_db.import_report(_report("SPC-1", [_res("GLUCOSE", 95)]), pdf_sha256="a" * 64)
        tmp_db.import_report(_report("SPC-2", [_res("TSH", 1.8)], requisition="REQ-2"), pdf_sha256="b" * 64)
        # Same file, specimen ID read differently this time
        outcome = tmp_db.import_report(_report("SPC-l", [_res("GLUCOSE", 96)]), pdf_sha256="a" * 64)
        assert outcome.replaced is True
        assert outcome.conflicts == []
        with tmp_db.get_session() as session:
            assert session.query(Report).count() == 2
            assert sorted((r.test_name, r.value) for r in session.query(Result)) == [
                ("GLUCOSE", 96.0), ("TSH", 1.8),
            ]

    def test_same_specimen_and_requisition_replaces(self, tmp_db):
        """A re-downloaded or amended PDF of one report: different bytes, same IDs."""
        tmp_db.import_report(_report("SPC-1", [_res("GLUCOSE", 95)]), pdf_sha256="a" * 64)
        outcome = tmp_db.import_report(_report("SPC-1", [_res("GLUCOSE", 95)]), pdf_sha256="c" * 64)
        assert outcome.replaced is True
        with tmp_db.get_session() as session:
            assert session.query(Report).count() == 1

    def test_same_specimen_other_requisition_is_separate(self, tmp_db):
        """Two orders drawn from one specimen are two reports."""
        tmp_db.import_report(_report("SPC-1", [_res("GLUCOSE", 95)]))
        outcome = tmp_db.import_report(
            _report("SPC-1", [_res("TSH", 1.8)], requisition="REQ-2")
        )
        assert outcome.replaced is False
        with tmp_db.get_session() as session:
            assert session.query(Report).count() == 2
