"""CLI for importing Quest Diagnostics PDF reports."""

import argparse
import sys
from pathlib import Path

from ..parser.pdf_parser import parse_pdf
from .db import QuestyDB

DEFAULT_DB_PATH = Path.home() / ".questy" / "questy.db"


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="questy-import",
        description="Import Quest Diagnostics PDF reports into local database",
        epilog="""
Examples:
  %(prog)s ~/diagnostics/quest_diagnostics_04-11-2025.pdf
  %(prog)s ~/diagnostics/quest*.pdf
  %(prog)s --database ~/custom/path.db report.pdf
  ANTHROPIC_API_KEY=sk-... %(prog)s report.pdf
        """,
    )
    parser.add_argument(
        "pdf_files",
        nargs="+",
        type=Path,
        help="PDF report file(s) to import",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DB_PATH,
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Anthropic API key (default: ANTHROPIC_API_KEY env var)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Claude model for extraction (default: claude-haiku-4-5-20251001)",
    )
    return parser


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()

    db = QuestyDB(args.database)

    success = 0
    failed = 0

    for pdf_path in args.pdf_files:
        if not pdf_path.exists():
            print(f"File not found: {pdf_path}")
            failed += 1
            continue

        try:
            parsed = parse_pdf(pdf_path, api_key=args.api_key, model=args.model)
            db.import_report(parsed, pdf_path=str(pdf_path.resolve()))
            n_results = len(parsed.results)
            n_notes = len(parsed.notes)
            print(
                f"Imported: {pdf_path.name} "
                f"(date: {parsed.report_date}, "
                f"{n_results} results, {n_notes} notes)"
            )
            for w in parsed.warnings:
                prefix = "WARNING" if w.level == "warn" else "ERROR"
                print(f"  {prefix}: {w.message}")
            success += 1
        except Exception as e:
            print(f"Failed: {pdf_path.name}: {e}")
            failed += 1

    print(f"\nDone: {success} imported, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
