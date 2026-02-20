"""Tests for CLI argument parsing — questy-import and questy-mcp."""

from __future__ import annotations

from pathlib import Path

import pytest

from questy.localdb.cli import create_parser as create_import_parser
from questy.mcp.cli import create_parser as create_mcp_parser


class TestImportCLI:
    def test_single_file(self):
        parser = create_import_parser()
        args = parser.parse_args(["report.pdf"])
        assert len(args.pdf_files) == 1
        assert args.pdf_files[0] == Path("report.pdf")

    def test_multiple_files(self):
        parser = create_import_parser()
        args = parser.parse_args(["a.pdf", "b.pdf", "c.pdf"])
        assert len(args.pdf_files) == 3

    def test_custom_database(self):
        parser = create_import_parser()
        args = parser.parse_args(["--database", "/tmp/custom.db", "report.pdf"])
        assert args.database == Path("/tmp/custom.db")

    def test_api_key_option(self):
        parser = create_import_parser()
        args = parser.parse_args(["--api-key", "sk-test-123", "report.pdf"])
        assert args.api_key == "sk-test-123"

    def test_model_option(self):
        parser = create_import_parser()
        args = parser.parse_args(["--model", "claude-sonnet-4-6", "report.pdf"])
        assert args.model == "claude-sonnet-4-6"

    def test_default_database_path(self):
        parser = create_import_parser()
        args = parser.parse_args(["report.pdf"])
        assert args.database == Path.home() / ".questy" / "questy.db"

    def test_no_files_raises(self):
        parser = create_import_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])


class TestMCPCLI:
    def test_server_command(self):
        parser = create_mcp_parser()
        args = parser.parse_args(["server"])
        assert args.command == "server"

    def test_custom_database(self):
        parser = create_mcp_parser()
        args = parser.parse_args(["server", "--database", "/tmp/custom.db"])
        assert args.database == "/tmp/custom.db"

    def test_max_rows(self):
        parser = create_mcp_parser()
        args = parser.parse_args(["server", "--max-rows", "500"])
        assert args.max_rows == 500

    def test_transport_options(self):
        for transport in ("stdio", "http", "sse"):
            parser = create_mcp_parser()
            args = parser.parse_args(["server", "--transport", transport])
            assert args.transport == transport

    def test_invalid_transport_rejected(self):
        parser = create_mcp_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["server", "--transport", "grpc"])

    def test_no_command_raises(self):
        parser = create_mcp_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_default_max_rows(self):
        parser = create_mcp_parser()
        args = parser.parse_args(["server"])
        assert args.max_rows == 1000

    def test_default_transport(self):
        parser = create_mcp_parser()
        args = parser.parse_args(["server"])
        assert args.transport == "stdio"
