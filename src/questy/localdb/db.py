"""SQLAlchemy database for Quest Diagnostics lab data."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, delete, inspect, or_, select, text
from sqlalchemy.orm import Session, sessionmaker

from ..parser.names import names_match
from .models import Base, Note, Report, Result

# Columns copied from a pre-multi-report database (reports keyed by report_date).
# pdf_sha256 is copied only when the old table has it.
_LEGACY_REPORT_COLUMNS = (
    "report_date, patient_name, dob, age, sex, fasting, specimen_id, "
    "requisition_id, lab_reference_id, collected_at, received_at, reported_at, "
    "report_status, pdf_path, imported_at"
)
_LEGACY_RESULT_COLUMNS = (
    "panel_name, test_name, value, value_text, value_prefix, unit, flag, "
    "is_calculated, ref_range_low, ref_range_high, ref_range_text"
)
_LEGACY_NOTE_COLUMNS = "panel_name, test_name, note_type, content"


@dataclass
class ImportOutcome:
    """What an import did, for the CLI to report."""

    report_id: int
    replaced: bool  # True if it replaced an earlier import of the same report
    same_date_reports: int  # Other reports already stored for this date
    conflicts: list[str] = field(default_factory=list)


class QuestyDB:
    """SQLAlchemy database for Quest Diagnostics lab data."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}")
        self.SessionLocal = sessionmaker(bind=self.engine)
        self._migrate_legacy_schema()
        Base.metadata.create_all(self.engine)

    def _migrate_legacy_schema(self) -> None:
        """Rebuild a database whose reports are keyed by report_date.

        That schema allowed one report per date, so a second PDF from the
        same day replaced the first. The rebuild gives every report an id and
        links results and notes to it; the data is copied unchanged. One
        transaction: on failure the database is left as it was.
        """
        insp = inspect(self.engine)
        if "reports" not in insp.get_table_names():
            return
        report_cols = {c["name"] for c in insp.get_columns("reports")}
        if "id" in report_cols:
            return

        report_copy = _LEGACY_REPORT_COLUMNS
        if "pdf_sha256" in report_cols:
            report_copy += ", pdf_sha256"

        legacy = [t for t in ("reports", "results", "notes") if t in insp.get_table_names()]
        with self.engine.begin() as conn:
            for table in legacy:
                conn.execute(text(f"ALTER TABLE {table} RENAME TO {table}_legacy"))
            Base.metadata.create_all(conn)
            conn.execute(text(
                f"INSERT INTO reports ({report_copy}) "
                f"SELECT {report_copy} FROM reports_legacy ORDER BY report_date"
            ))
            for table, columns in (
                ("results", _LEGACY_RESULT_COLUMNS),
                ("notes", _LEGACY_NOTE_COLUMNS),
            ):
                if table not in legacy:
                    continue
                prefixed = ", ".join("o." + c.strip() for c in columns.split(","))
                conn.execute(text(
                    f"INSERT INTO {table} (report_id, report_date, {columns}) "
                    f"SELECT r.id, o.report_date, {prefixed} FROM {table}_legacy o "
                    f"LEFT JOIN reports r ON r.report_date = o.report_date"
                ))
            for table in legacy:
                conn.execute(text(f"DROP TABLE {table}_legacy"))

    def get_session(self) -> Session:
        return self.SessionLocal()

    def import_report(
        self,
        parsed_report,
        pdf_path: str | None = None,
        pdf_sha256: str | None = None,
    ) -> ImportOutcome:
        """Import a ParsedReport into the database.

        Additive: other reports from the same date are kept. Re-importing the
        same report replaces only that report's results and notes (see _find_same_report). A test that also
        appears in another same-date report with a different value is listed
        in the outcome's conflicts; both rows are stored.
        """
        report_date = parsed_report.report_date
        if report_date is None:
            raise ValueError("ParsedReport has no collection date (report_date)")

        with self.get_session() as session:
            existing = self._find_same_report(session, parsed_report, pdf_sha256)
            if existing is not None:
                session.execute(delete(Note).where(Note.report_id == existing.id))
                session.execute(delete(Result).where(Result.report_id == existing.id))
                report = existing
            else:
                report = Report(report_date=report_date)
                session.add(report)

            report.patient_name = parsed_report.patient_name
            report.dob = parsed_report.dob
            report.age = parsed_report.age
            report.sex = parsed_report.sex
            report.fasting = parsed_report.fasting
            report.specimen_id = parsed_report.specimen_id
            report.requisition_id = parsed_report.requisition_id
            report.lab_reference_id = parsed_report.lab_reference_id
            report.collected_at = parsed_report.collected_at
            report.received_at = parsed_report.received_at
            report.reported_at = parsed_report.reported_at
            report.report_status = parsed_report.report_status
            report.pdf_path = pdf_path
            report.pdf_sha256 = pdf_sha256
            report.imported_at = datetime.now(timezone.utc)
            session.flush()

            others = session.scalars(
                select(Report).where(
                    Report.report_date == report_date, Report.id != report.id
                )
            ).all()
            other_results = session.scalars(
                select(Result).where(Result.report_id.in_([o.id for o in others]))
            ).all()
            labels = {o.id: _report_label(o) for o in others}

            conflicts = []
            for r in parsed_report.results:
                for o in other_results:
                    if names_match(r.test_name, o.test_name) and _values_differ(r, o):
                        conflicts.append(
                            f"{r.test_name}: {_fmt(r.value_text, r.unit)} in this report, "
                            f"{_fmt(o.value_text, o.unit)} in {labels[o.report_id]}"
                        )

            for r in parsed_report.results:
                session.add(Result(
                    report_id=report.id,
                    report_date=report_date,
                    panel_name=r.panel_name,
                    test_name=r.test_name,
                    value=r.value,
                    value_text=r.value_text,
                    value_prefix=r.value_prefix,
                    unit=r.unit,
                    flag=r.flag,
                    is_calculated=r.is_calculated,
                    ref_range_low=r.ref_range_low,
                    ref_range_high=r.ref_range_high,
                    ref_range_text=r.ref_range_text,
                ))

            for n in parsed_report.notes:
                session.add(Note(
                    report_id=report.id,
                    report_date=report_date,
                    panel_name=n.panel_name,
                    test_name=n.test_name,
                    note_type=n.note_type,
                    content=n.content,
                ))

            session.commit()
            return ImportOutcome(
                report_id=report.id,
                replaced=existing is not None,
                same_date_reports=len(others),
                conflicts=conflicts,
            )

    @staticmethod
    def _find_same_report(session: Session, parsed_report, pdf_sha256: str | None) -> Report | None:
        """The stored report this import is a re-import of, if any.

        The same PDF (by hash), or the same date, specimen and requisition:
        a re-downloaded or amended copy of one report. Two orders drawn from
        one specimen carry different requisitions, so they stay separate.
        """
        if pdf_sha256:
            found = session.scalars(
                select(Report).where(Report.pdf_sha256 == pdf_sha256)
            ).first()
            if found is not None:
                return found
        if not parsed_report.specimen_id:
            return None
        query = select(Report).where(
            Report.report_date == parsed_report.report_date,
            Report.specimen_id == parsed_report.specimen_id,
        )
        if parsed_report.requisition_id:
            query = query.where(or_(
                Report.requisition_id.is_(None),
                Report.requisition_id == parsed_report.requisition_id,
            ))
        return session.scalars(query).first()

    def get_imported_dates(self) -> set:
        """Get set of report dates already in the database."""
        with self.get_session() as session:
            rows = session.query(Report.report_date).all()
            return {row[0] for row in rows}


def _values_differ(new, stored: Result) -> bool:
    """True if a parsed result and a stored one report different values."""
    if new.value is not None and stored.value is not None:
        return abs(new.value - stored.value) > 0.01 or (new.unit or "") != (stored.unit or "")
    return (new.value_text or "").strip() != (stored.value_text or "").strip()


def _fmt(value_text: str | None, unit: str | None) -> str:
    return f"{value_text or ''} {unit or ''}".strip()


def _report_label(report: Report) -> str:
    if report.specimen_id:
        return f"report {report.id} (specimen {report.specimen_id})"
    return f"report {report.id}"
