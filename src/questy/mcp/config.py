"""Configuration for MCP server."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class MCPConfig:
    """MCP server configuration."""

    db_path: Path
    max_rows: int = 1000
    max_rows_absolute: int = 5000
    enable_query_logging: bool = False
    strict_validation: bool = True
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_db_path(cls, db_path: Path, **kwargs) -> "MCPConfig":
        return cls(db_path=db_path, **kwargs)

    def validate(self) -> None:
        if not self.db_path.exists():
            raise FileNotFoundError(f"Database file not found: {self.db_path}")
        if self.max_rows > self.max_rows_absolute:
            raise ValueError(f"max_rows cannot exceed {self.max_rows_absolute}")
        if self.max_rows <= 0:
            raise ValueError("max_rows must be positive")
