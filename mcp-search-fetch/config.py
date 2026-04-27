"""
Configuration for the MCP SearXNG server.
All values can be overridden via environment variables or a .env file.
"""

from typing import Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[".env", "../.env"],
        env_file_encoding="utf-8",
        env_prefix="MCP_SEARCH_FETCH_",
        case_sensitive=False,
        extra="ignore",
    )

    # ── SearXNG ──────────────────────────────────────────────────────────────
    searxng_base_url: str = "http://localhost:8081"
    """Base URL of the SearXNG Docker instance (no trailing slash)."""

    request_timeout: float = 30.0
    """HTTP timeout in seconds for requests to SearXNG."""

    # ── MCP / SSE server ─────────────────────────────────────────────────────
    mcp_host: str = "0.0.0.0"
    """Host for the SSE MCP server to bind on."""

    mcp_port: int = 8000
    """Port for the SSE MCP server."""

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    """Logging level: DEBUG, INFO, WARNING, ERROR."""

    log_file: Optional[str] = "mcp_search_fetch.log"
    """Path to log file. Set to empty string or omit to log to stdout only."""

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v_upper = v.upper()
        if v_upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return v_upper

    @field_validator("searxng_base_url")
    @classmethod
    def strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")


settings = Settings()
