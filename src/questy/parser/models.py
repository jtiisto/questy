"""Data models for parsed Quest Diagnostics PDF reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class ParsedResult:
    """A single test measurement from a Quest report."""

    panel_name: str
    test_name: str
    value: float | None  # Numeric part, None for unparseable
    value_text: str  # Raw: "229", "<0.5", "152 H"
    value_prefix: str | None  # "<", ">", None
    unit: str
    flag: str | None  # "H", "L", None
    is_calculated: bool
    ref_range_low: float | None
    ref_range_high: float | None
    ref_range_text: str  # Raw reference range string


@dataclass
class ParsedNote:
    """A clinical note, comment, or risk category from a Quest report."""

    panel_name: str
    test_name: str | None  # None for panel-level notes
    note_type: str  # "comment", "risk_category", "clinical", "methodology"
    content: str


@dataclass
class ParseWarning:
    """A warning emitted during parsing that the user should review."""

    level: str  # "warn" or "error"
    message: str


@dataclass
class ParsedReport:
    """A fully parsed Quest Diagnostics PDF report."""

    # Header
    patient_name: str
    dob: date | None
    age: int | None
    sex: str | None
    fasting: bool
    specimen_id: str | None
    requisition_id: str | None
    lab_reference_id: str | None
    collected_at: datetime | None
    received_at: datetime | None
    reported_at: datetime | None
    report_status: str | None

    # Data
    results: list[ParsedResult] = field(default_factory=list)
    notes: list[ParsedNote] = field(default_factory=list)
    warnings: list[ParseWarning] = field(default_factory=list)

    @property
    def report_date(self) -> date | None:
        """The collection date, used as the primary key."""
        if self.collected_at:
            return self.collected_at.date()
        return None
