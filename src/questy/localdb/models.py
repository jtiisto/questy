"""SQLAlchemy ORM models for Quest Diagnostics lab data."""

from datetime import datetime

from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Report(Base):
    """Report-level metadata — one row per imported PDF."""

    __tablename__ = "reports"

    report_date = Column(Date, primary_key=True)
    patient_name = Column(String)
    dob = Column(Date)
    age = Column(Integer)
    sex = Column(String)
    fasting = Column(Boolean)
    specimen_id = Column(String)
    requisition_id = Column(String)
    lab_reference_id = Column(String)
    collected_at = Column(DateTime)
    received_at = Column(DateTime)
    reported_at = Column(DateTime)
    report_status = Column(String)
    pdf_path = Column(Text)
    imported_at = Column(DateTime, default=datetime.utcnow)


class Result(Base):
    """Individual test measurement — the core data table."""

    __tablename__ = "results"

    report_date = Column(Date, primary_key=True)
    panel_name = Column(String, primary_key=True)
    test_name = Column(String, primary_key=True)
    value = Column(Float)  # Numeric part, NULL for unparseable/inequality
    value_text = Column(String)  # Raw: "229", "<0.5", "152 H"
    value_prefix = Column(String)  # "<", ">", NULL
    unit = Column(String)
    flag = Column(String)  # "H", "L", NULL
    is_calculated = Column(Boolean, default=False)
    ref_range_low = Column(Float)
    ref_range_high = Column(Float)
    ref_range_text = Column(String)  # Raw reference range string


class Note(Base):
    """Clinical notes, comments, risk categories per test or panel."""

    __tablename__ = "notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_date = Column(Date)
    panel_name = Column(String)
    test_name = Column(String)  # NULL for panel-level notes
    note_type = Column(String)  # "comment", "risk_category", "clinical", "methodology"
    content = Column(Text)
