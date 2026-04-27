"""
Logging configuration and search-specific log helpers.

Every search request emits:
  - A REQUEST line with the full parameters and a ready-to-run cURL command
  - A RESPONSE line with the result count and elapsed time
  - An ERROR line (on failure) with error type, message, and the cURL command

The cURL command lets you manually reproduce the exact search from a terminal.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional


# ── ANSI colours for the console handler ─────────────────────────────────────
_RESET = "\033[0m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_MAGENTA = "\033[35m"
_GREY = "\033[90m"

_LEVEL_COLORS = {
    "DEBUG": _GREY,
    "INFO": _CYAN,
    "WARNING": _YELLOW,
    "ERROR": _RED,
    "CRITICAL": _MAGENTA,
}


class ColorFormatter(logging.Formatter):
    """Console formatter with ANSI colour coding by log level."""

    FMT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    DATEFMT = "%H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        color = _LEVEL_COLORS.get(record.levelname, _RESET)
        record.levelname = f"{color}{_BOLD}{record.levelname}{_RESET}"
        record.name = f"{_GREY}{record.name}{_RESET}"
        return super().format(record)


class PlainFormatter(logging.Formatter):
    """Plain formatter for file output (no ANSI escape codes)."""

    FMT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    DATEFMT = "%Y-%m-%dT%H:%M:%S"

    def format(self, record: logging.LogRecord) -> str:
        return super().format(record)


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """
    Configure root logger with:
      - A coloured StreamHandler to stdout
      - An optional RotatingFileHandler (plain text) if log_file is set
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove any existing handlers (e.g. those added by libraries)
    root.handlers.clear()

    # Console handler
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(
        ColorFormatter(fmt=ColorFormatter.FMT, datefmt=ColorFormatter.DATEFMT)
    )
    root.addHandler(console)

    # File handler (optional)
    if log_file:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(
            PlainFormatter(fmt=PlainFormatter.FMT, datefmt=PlainFormatter.DATEFMT)
        )
        root.addHandler(file_handler)

    # Quiet noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


# ── Request/Response log helpers ─────────────────────────────────────────────

def log_request(
    logger: logging.Logger,
    request_id: str,
    target: str,
    params: Optional[dict],
    retry_curl: str,
) -> None:
    """Emit a structured log line for an outgoing request."""
    param_str = _fmt_params(params) if params else ""
    logger.info(
        "[%s] REQUEST  target=%r  %s",
        request_id,
        target,
        param_str,
    )
    # The DEBUG-level line contains the full cURL for easy copy-paste
    logger.debug(
        "[%s] RETRY CURL  %s",
        request_id,
        retry_curl,
    )
    # Always log the cURL at INFO so it is always available in the log file
    logger.info(
        "[%s] RETRY CURL  %s",
        request_id,
        retry_curl,
    )


def log_response(
    logger: logging.Logger,
    request_id: str,
    target: str,
    result_info: str,
    elapsed: float,
) -> None:
    """Emit a structured log line for a successful response."""
    logger.info(
        "[%s] OK       target=%r  %s  elapsed=%.2fs",
        request_id,
        target,
        result_info,
        elapsed,
    )


def log_error(
    logger: logging.Logger,
    request_id: str,
    target: str,
    error_type: str,
    message: str,
    elapsed: float,
    retry_curl: str = "",
) -> None:
    """Emit structured log lines for a failed request."""
    logger.error(
        "[%s] ERROR    target=%r  type=%s  elapsed=%.2fs  message=%s",
        request_id,
        target,
        error_type,
        elapsed,
        message,
    )
    if retry_curl:
        logger.error(
            "[%s] RETRY CURL  %s",
            request_id,
            retry_curl,
        )


def _fmt_params(params: dict) -> str:
    """Format params dict for compact log output, excluding 'format' key."""
    parts = [f"{k}={v!r}" for k, v in params.items() if k != "format"]
    return "  ".join(parts)
