"""Extraction prompts and response schemas for Claude vision-based PDF parsing."""

HEADER_PROMPT = """\
Extract patient and specimen metadata from this Quest Diagnostics report page.

Return a JSON object with exactly these fields:
- "patient_name": Full name as printed (string)
- "dob": Date of birth as "YYYY-MM-DD" or null
- "age": Age as integer or null
- "sex": "M" or "F" or null
- "fasting": true or false
- "specimen_id": Specimen ID string or null
- "requisition_id": Requisition ID string or null
- "lab_reference_id": Lab Reference ID string or null
- "collected_at": Collection datetime as "YYYY-MM-DDTHH:MM" or null
- "received_at": Received datetime as "YYYY-MM-DDTHH:MM" or null
- "reported_at": Reported datetime as "YYYY-MM-DDTHH:MM" or null
- "report_status": Report status string (e.g., "FINAL") or null

Return ONLY the JSON object, no other text."""

RESULTS_PROMPT = """\
Extract all lab test results from these Quest Diagnostics report pages.

Return a JSON object with these fields:

IMPORTANT: Quest reports group tests under panel headings. A panel heading \
is a bold or prominent section title (e.g., "LIPID PANEL, STANDARD", \
"CBC (INCLUDES DIFF/PLT)", "COMPREHENSIVE METABOLIC PANEL"). Multiple \
individual tests appear under each panel. Every test MUST be assigned to \
its parent panel — do NOT use the test name as the panel name. For example, \
NEUTROPHILS, LYMPHOCYTES, PLATELET COUNT, RDW, MCH, MCHC, MPV etc. all \
belong under "CBC (INCLUDES DIFF/PLT)", not under their own names. Similarly \
GLUCOSE, SODIUM, POTASSIUM, CALCIUM, ALT, AST etc. belong under \
"COMPREHENSIVE METABOLIC PANEL".

"results": array of objects, each with:
- "panel_name": The parent section/panel heading exactly as printed, ALL CAPS \
(e.g., "LIPID PANEL, STANDARD", "CBC (INCLUDES DIFF/PLT)"). This is the \
group heading, NOT the individual test name.
- "test_name": The individual test name exactly as printed \
(e.g., "CHOLESTEROL, TOTAL", "LDL-CHOLESTEROL")
- "value": The numeric value as a number, or null if not purely numeric
- "value_text": The raw value string exactly as printed \
(e.g., "229", "<0.5", "152 H")
- "value_prefix": "<" or ">" if the value has an inequality prefix, otherwise null
- "unit": Unit of measurement (e.g., "mg/dL", "ng/mL", "%"), empty string if none
- "flag": "H" if flagged high, "L" if flagged low, null if normal/in-range
- "is_calculated": true if marked "(calc)", false otherwise
- "ref_range_low": Lower bound of reference range as number, or null
- "ref_range_high": Upper bound of reference range as number, or null
- "ref_range_text": The reference range exactly as printed \
(e.g., "30-100", "< or = 18.4", "> OR = 60"), empty string if none shown

"notes": array of objects for clinical notes, risk categories, and comments:
- "panel_name": Panel this note belongs to
- "test_name": Specific test name, or null for panel-level notes
- "note_type": One of "comment", "risk_category", "clinical", "methodology"
- "content": The note text

"has_more_results": true if these pages end mid-results and more result pages \
likely follow, false if you see "Performing Sites", "Report Insights", or no \
more test data.

Rules:
- Extract EVERY test result shown. Do not skip any.
- Preserve exact panel names and test names as printed.
- For values like "<0.5", set value to 0.5, value_prefix to "<", \
value_text to "<0.5".
- For values like "152 H", set value to 152, flag to "H", \
value_text to "152 H".
- Skip charts, graphs, trend visualizations, performing site addresses, \
and "Report Insights" educational content.
- For reference ranges like "< or = 18.4", set ref_range_high to 18.4.
- For reference ranges like "> OR = 60", set ref_range_low to 60.
- For reference ranges like "30-100", set ref_range_low to 30, ref_range_high to 100.
- Classify notes: "risk_category" if about risk levels/optimal ranges, \
"methodology" if about test methodology/validation, \
"comment" if about additional info/see-note references, \
"clinical" for other clinical interpretation.

Return ONLY the JSON object, no other text."""

HEADER_SCHEMA = {
    "type": "object",
    "properties": {
        "patient_name": {"type": "string"},
        "dob": {"type": ["string", "null"]},
        "age": {"type": ["integer", "null"]},
        "sex": {"type": ["string", "null"]},
        "fasting": {"type": "boolean"},
        "specimen_id": {"type": ["string", "null"]},
        "requisition_id": {"type": ["string", "null"]},
        "lab_reference_id": {"type": ["string", "null"]},
        "collected_at": {"type": ["string", "null"]},
        "received_at": {"type": ["string", "null"]},
        "reported_at": {"type": ["string", "null"]},
        "report_status": {"type": ["string", "null"]},
    },
    "required": [
        "patient_name", "dob", "age", "sex", "fasting",
        "specimen_id", "requisition_id", "lab_reference_id",
        "collected_at", "received_at", "reported_at", "report_status",
    ],
    "additionalProperties": False,
}

RESULTS_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "panel_name": {"type": "string"},
                    "test_name": {"type": "string"},
                    "value": {"type": ["number", "null"]},
                    "value_text": {"type": "string"},
                    "value_prefix": {"type": ["string", "null"]},
                    "unit": {"type": "string"},
                    "flag": {"type": ["string", "null"]},
                    "is_calculated": {"type": "boolean"},
                    "ref_range_low": {"type": ["number", "null"]},
                    "ref_range_high": {"type": ["number", "null"]},
                    "ref_range_text": {"type": "string"},
                },
                "required": [
                    "panel_name", "test_name", "value", "value_text",
                    "value_prefix", "unit", "flag", "is_calculated",
                    "ref_range_low", "ref_range_high", "ref_range_text",
                ],
                "additionalProperties": False,
            },
        },
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "panel_name": {"type": "string"},
                    "test_name": {"type": ["string", "null"]},
                    "note_type": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["panel_name", "test_name", "note_type", "content"],
                "additionalProperties": False,
            },
        },
        "has_more_results": {"type": "boolean"},
    },
    "required": ["results", "notes", "has_more_results"],
    "additionalProperties": False,
}
