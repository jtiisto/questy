"""SQLAlchemy database for Quest Diagnostics lab data."""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, Note, Report, Result


class QuestyDB:
    """SQLAlchemy database for Quest Diagnostics lab data."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}")
        self.SessionLocal = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)

    def get_session(self) -> Session:
        return self.SessionLocal()

    def import_report(self, parsed_report, pdf_path: str | None = None) -> None:
        """Import a ParsedReport into the database. Upserts by report_date."""
        report_date = parsed_report.report_date
        if report_date is None:
            raise ValueError("ParsedReport has no collection date (report_date)")

        with self.get_session() as session:
            # Delete existing data for this date (upsert)
            session.execute(delete(Note).where(Note.report_date == report_date))
            session.execute(delete(Result).where(Result.report_date == report_date))

            # Upsert report record
            report = Report(
                report_date=report_date,
                patient_name=parsed_report.patient_name,
                dob=parsed_report.dob,
                age=parsed_report.age,
                sex=parsed_report.sex,
                fasting=parsed_report.fasting,
                specimen_id=parsed_report.specimen_id,
                requisition_id=parsed_report.requisition_id,
                lab_reference_id=parsed_report.lab_reference_id,
                collected_at=parsed_report.collected_at,
                received_at=parsed_report.received_at,
                reported_at=parsed_report.reported_at,
                report_status=parsed_report.report_status,
                pdf_path=pdf_path,
                imported_at=datetime.now(timezone.utc),
            )
            session.merge(report)

            # Insert results
            for r in parsed_report.results:
                result = Result(
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
                )
                session.add(result)

            # Insert notes
            for n in parsed_report.notes:
                note = Note(
                    report_date=report_date,
                    panel_name=n.panel_name,
                    test_name=n.test_name,
                    note_type=n.note_type,
                    content=n.content,
                )
                session.add(note)

            session.commit()

    def get_imported_dates(self) -> set:
        """Get set of report dates already in the database."""
        with self.get_session() as session:
            rows = session.query(Report.report_date).all()
            return {row[0] for row in rows}
