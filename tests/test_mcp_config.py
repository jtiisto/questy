"""Tests for questy.mcp.config — MCP server configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from questy.mcp.config import MCPConfig


class TestMCPConfig:
    def test_defaults(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = MCPConfig(db_path=db_path)
        assert config.max_rows == 1000
        assert config.max_rows_absolute == 5000
        assert config.strict_validation is True
        assert config.transport == "stdio"
        assert config.host == "127.0.0.1"
        assert config.port == 8000

    def test_from_db_path(self, tmp_path):
        db_path = tmp_path / "test.db"
        config = MCPConfig.from_db_path(db_path, max_rows=500)
        assert config.db_path == db_path
        assert config.max_rows == 500

    def test_validate_missing_db(self, tmp_path):
        config = MCPConfig(db_path=tmp_path / "nonexistent.db")
        with pytest.raises(FileNotFoundError, match="Database file not found"):
            config.validate()

    def test_validate_max_rows_exceeds_absolute(self, tmp_path):
        db_path = tmp_path / "test.db"
        db_path.touch()
        config = MCPConfig(db_path=db_path, max_rows=10000, max_rows_absolute=5000)
        with pytest.raises(ValueError, match="max_rows cannot exceed"):
            config.validate()

    def test_validate_max_rows_zero(self, tmp_path):
        db_path = tmp_path / "test.db"
        db_path.touch()
        config = MCPConfig(db_path=db_path, max_rows=0)
        with pytest.raises(ValueError, match="max_rows must be positive"):
            config.validate()

    def test_validate_negative_max_rows(self, tmp_path):
        db_path = tmp_path / "test.db"
        db_path.touch()
        config = MCPConfig(db_path=db_path, max_rows=-1)
        with pytest.raises(ValueError, match="max_rows must be positive"):
            config.validate()

    def test_validate_success(self, tmp_path):
        db_path = tmp_path / "test.db"
        db_path.touch()
        config = MCPConfig(db_path=db_path)
        config.validate()  # Should not raise
