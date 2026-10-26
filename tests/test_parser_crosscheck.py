"""Tests for the raw-text cross-checks: test-name matching, the recovery pass,
the lab reference ID and the "(calc)" unit marker.

The layout text below is synthetic (made-up values and IDs) but shaped like
real `pdftotext -layout` output from a Quest report: a multi-column header
line on page 1, and lipid results whose unit and "(calc)" marker sit on
their own lines.
"""

from __future__ import annotations

import pytest

from questy.parser import pdf_parser
from questy.parser.models import ParsedResult
from questy.parser.names import names_match
from questy.parser.pdf_parser import (
    _align_names_to_text,
    _deduplicate_results,
    _find_missed_names,
    _parse_header_text,
    _parse_text_values,
    _post_process_results,
    _validate_header,
    _verify_values,
)

HEADER_TEXT = """\
 Patient Information             Specimen Information          Client Information
 DOE, JANE                       Specimen: AB123456C           Client #: 00000000
 DOB: 11/22/1984  AGE: 40        Requisition: 0000001          ACME-TEST
 Gender: F                       Collected: 06/02/2025 / 08:15 PDT
 Phone: (555) 555-0100           Lab Reference ID: 1234567     Reported: 06/05/2025 11:02
"""

LIPID_TEXT = """\
LIPID PANEL, STANDARD
CHOLESTEROL, TOTAL                                       201
Reference Range: <200 mg/dL                              |
HDL CHOLESTEROL                                                 62
Reference Range: > OR = 50 mg/dL                         |
TRIGLYCERIDES                                       88
Reference Range: <150 mg/dL                              |
LDL-CHOLESTEROL                                    121
mg/dL (calc)
CHOL/HDLC RATIO                                       3.2
Reference Range: <5.0 (calc)                             |
NON HDL CHOLESTEROL                                     139
Reference Range: <130 mg/dL (calc)                       |
"""

LIPID = "LIPID PANEL, STANDARD"


def _result(test, value, unit="mg/dL", panel=LIPID, ref="", calc=False):
    return ParsedResult(
        panel, test, value, str(value), None, unit, None, calc, None, None, ref,
    )


def _vision_lipids(ratio_name="CHOL/HDL RATIO"):
    """What the vision pass returned for the lipid panel, with its quirks:
    a paraphrased ratio name and "(calc)" in the unit."""
    return [
        _result("CHOLESTEROL, TOTAL", 201.0, ref="<200"),
        _result("HDL CHOLESTEROL", 62.0, ref="> OR = 50"),
        _result("TRIGLYCERIDES", 88.0, ref="<150"),
        _result("LDL-CHOLESTEROL", 121.0, unit="mg/dL (calc)"),
        _result(ratio_name, 3.2, unit="(calc)", ref="<5.0"),
        _result("NON HDL CHOLESTEROL", 139.0, unit="mg/dL (calc)", ref="<130"),
    ]


# --- _names_match ---


class TestNamesMatch:
    @pytest.mark.parametrize("a, b", [
        ("CHOL/HDL RATIO", "CHOL/HDLC RATIO"),
        ("CHOL/HDL-C RATIO", "CHOL/HDLC RATIO"),
        ("LDL CHOLESTEROL", "LDL-CHOLESTEROL"),
        ("Cholesterol, Total", "CHOLESTEROL,  TOTAL"),
        ("NON-HDL CHOLESTEROL", "NON HDL CHOLESTEROL"),
    ])
    def test_same_test(self, a, b):
        assert names_match(a, b)
        assert names_match(b, a)

    @pytest.mark.parametrize("a, b", [
        ("T3, FREE", "T4, FREE"),  # substitution
        ("APOLIPOPROTEIN A1", "APOLIPOPROTEIN B"),
        ("VITAMIN D", "VITAMIN D2"),  # digit insertion
        ("MCH", "MCHC"),  # too short for a fuzzy match
        ("LDL-CHOLESTEROL", "HDL CHOLESTEROL"),
        ("IRON, TOTAL", "IRON BINDING CAPACITY"),
    ])
    def test_different_tests(self, a, b):
        assert not names_match(a, b)
        assert not names_match(b, a)


# --- text parsing ---


class TestParseText:
    def test_lipid_values(self):
        values = _parse_text_values(LIPID_TEXT)
        assert values == {
            "CHOLESTEROL, TOTAL": "201",
            "HDL CHOLESTEROL": "62",
            "TRIGLYCERIDES": "88",
            "LDL-CHOLESTEROL": "121",
            "CHOL/HDLC RATIO": "3.2",
            "NON HDL CHOLESTEROL": "139",
        }

    def test_header_lab_reference_id(self):
        header = _parse_header_text(HEADER_TEXT)
        assert header["lab_reference_id"] == "1234567"
        assert header["dob"] == "11/22/1984"
        assert header["age"] == "40"

    def test_header_empty_text(self):
        assert _parse_header_text("") == {
            "dob": None, "age": None, "lab_reference_id": None,
        }


# --- cross-check with a paraphrased name ---


class TestParaphrasedNames:
    def test_not_reported_missing(self):
        text_values = _parse_text_values(LIPID_TEXT)
        assert _find_missed_names(text_values, _vision_lipids()) == []

    def test_aligned_to_text_spelling(self):
        results = _vision_lipids()
        _align_names_to_text(results, _parse_text_values(LIPID_TEXT))
        assert results[4].test_name == "CHOL/HDLC RATIO"
        # Names already spelled as in the text are untouched
        assert results[3].test_name == "LDL-CHOLESTEROL"

    def test_unmatched_name_left_alone(self):
        r = _result("APOLIPOPROTEIN B", 80.0)
        _align_names_to_text([r], _parse_text_values(LIPID_TEXT))
        assert r.test_name == "APOLIPOPROTEIN B"

    def test_value_verified_under_paraphrase(self):
        r = _result("CHOL/HDL RATIO", 4.1)
        warnings = _verify_values([r], _parse_text_values(LIPID_TEXT))
        assert r.value == 3.2
        assert len(warnings) == 1

    def test_exact_text_match_preferred(self):
        r = _result("CHOL/HDL RATIO", 3.2)
        _align_names_to_text([r], {"CHOL/HDLC RATIO": "3.2", "CHOL/HDL RATIO": "3.2"})
        assert r.test_name == "CHOL/HDL RATIO"


class TestDeduplicateParaphrase:
    def test_recovery_hit_replaces_existing_row(self):
        existing = _result("CHOL/HDL RATIO", 3.2, unit="(calc)")
        recovered = _result("CHOL/HDLC RATIO", 3.2, unit="")
        deduped = _deduplicate_results([_result("TRIGLYCERIDES", 88.0), existing, recovered])
        assert [r.test_name for r in deduped] == ["TRIGLYCERIDES", "CHOL/HDLC RATIO"]
        assert deduped[1] is recovered

    def test_same_name_different_panels_kept(self):
        a = _result("GLUCOSE", 95.0, panel="COMPREHENSIVE METABOLIC PANEL")
        b = _result("GLUCOSE", 96.0, panel="GLUCOSE, FASTING")
        assert len(_deduplicate_results([a, b])) == 2


# --- lab reference ID cross-check ---


class TestLabReferenceId:
    def test_mangled_value_corrected(self):
        header = {"lab_reference_id": "ID: 123567", "collected_at": None}
        warnings = _validate_header(header, _parse_header_text(HEADER_TEXT))
        assert header["lab_reference_id"] == "1234567"
        assert any("Lab reference ID corrected" in w.message for w in warnings)

    def test_matching_value_no_warning(self):
        header = {"lab_reference_id": "1234567", "collected_at": None}
        assert _validate_header(header, {"lab_reference_id": "1234567"}) == []

    def test_text_fills_missing(self):
        header = {"lab_reference_id": None, "collected_at": None}
        assert _validate_header(header, {"lab_reference_id": "1234567"}) == []
        assert header["lab_reference_id"] == "1234567"

    def test_no_text_value_keeps_llm_value(self):
        header = {"lab_reference_id": "1234567", "collected_at": None}
        _validate_header(header, {"lab_reference_id": None})
        assert header["lab_reference_id"] == "1234567"


# --- "(calc)" marker ---


class TestCalcMarker:
    def test_calc_only_unit_emptied(self):
        r = _result("CHOL/HDLC RATIO", 3.2, unit="(calc)")
        _post_process_results([r])
        assert r.unit == ""
        assert r.is_calculated is True

    def test_calc_stripped_from_unit(self):
        r = _result("LDL-CHOLESTEROL", 121.0, unit="mg/dL (calc)")
        _post_process_results([r])
        assert r.unit == "mg/dL"
        assert r.is_calculated is True

    def test_calc_in_ref_range_marks_calculated(self):
        r = _result("CHOL/HDLC RATIO", 3.2, unit="", ref="<5.0 (calc)")
        _post_process_results([r])
        assert r.ref_range_text == "<5.0 (calc)"
        assert r.is_calculated is True

    def test_plain_unit_untouched(self):
        r = _result("TRIGLYCERIDES", 88.0)
        _post_process_results([r])
        assert r.unit == "mg/dL"
        assert r.is_calculated is False


# --- parse_pdf end to end, API and pdftotext stubbed ---


class TestParsePdfLipidPanel:
    @pytest.fixture
    def stubbed(self, monkeypatch, tmp_path):
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"%PDF-1.4 stub")
        recover_calls = []

        def fake_recover(client, model, pages, missed):
            recover_calls.append(missed)
            return [], []

        monkeypatch.setattr(pdf_parser, "_pdf_to_images", lambda p: ["page1"])
        monkeypatch.setattr(pdf_parser, "_extract_header", lambda c, m, p: {
            "patient_name": "DOE, JANE",
            "dob": "1984-11-22",
            "age": 40,
            "lab_reference_id": "ID: 123567",
            "collected_at": "2025-06-02T08:15",
        })
        monkeypatch.setattr(pdf_parser, "_extract_results",
                            lambda c, m, p, collected_at=None: (_vision_lipids(), []))
        monkeypatch.setattr(pdf_parser, "_recover_missed", fake_recover)
        monkeypatch.setattr(pdf_parser, "_extract_header_from_text",
                            lambda p: _parse_header_text(HEADER_TEXT))
        monkeypatch.setattr(pdf_parser, "_extract_text_values",
                            lambda p: _parse_text_values(LIPID_TEXT))
        return pdf, recover_calls

    def test_one_row_per_printed_test(self, stubbed):
        pdf, recover_calls = stubbed
        report = pdf_parser.parse_pdf(pdf, api_key="test")

        assert recover_calls == []
        assert [r.test_name for r in report.results] == [
            "CHOLESTEROL, TOTAL", "HDL CHOLESTEROL", "TRIGLYCERIDES",
            "LDL-CHOLESTEROL", "CHOL/HDLC RATIO", "NON HDL CHOLESTEROL",
        ]
        units = {r.test_name: r.unit for r in report.results}
        assert units["CHOL/HDLC RATIO"] == ""
        assert units["LDL-CHOLESTEROL"] == "mg/dL"
        assert units["NON HDL CHOLESTEROL"] == "mg/dL"

    def test_lab_reference_id_from_text(self, stubbed):
        pdf, _ = stubbed
        report = pdf_parser.parse_pdf(pdf, api_key="test")
        assert report.lab_reference_id == "1234567"

    def test_recovered_paraphrase_not_duplicated(self, stubbed, monkeypatch):
        """A test the vision pass missed comes back from recovery under a
        paraphrased name: it is stored once, under the text spelling."""
        pdf, recover_calls = stubbed
        vision = [r for r in _vision_lipids() if r.test_name != "CHOL/HDL RATIO"]
        monkeypatch.setattr(pdf_parser, "_extract_results",
                            lambda c, m, p, collected_at=None: (vision, []))

        def fake_recover(client, model, pages, missed):
            recover_calls.append(missed)
            return [_result("CHOL/HDL RATIO", 3.2, unit="(calc)")], []

        monkeypatch.setattr(pdf_parser, "_recover_missed", fake_recover)
        report = pdf_parser.parse_pdf(pdf, api_key="test")

        assert recover_calls == [["CHOL/HDLC RATIO"]]
        names = [r.test_name for r in report.results]
        assert names.count("CHOL/HDLC RATIO") == 1
        assert len(names) == 6
        assert any("successfully extracted" in w.message for w in report.warnings)
