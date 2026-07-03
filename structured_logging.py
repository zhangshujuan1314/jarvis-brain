"""
Structured logging for Jarvis Brain.
Outputs JSON-formatted logs for production (easy to parse by ELK, Datadog, etc.)
Falls back to human-readable format for development.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time


class JSONFormatter(logging.Formatter):
    """JSON log formatter for production."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "turn_id"):
            log_entry["turn_id"] = record.turn_id
        if hasattr(record, "device_id"):
            log_entry["device_id"] = record.device_id
        return json.dumps(log_entry, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Human-readable formatter for development."""

    FORMAT = "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"

    def __init__(self):
        super().__init__(fmt=self.FORMAT, datefmt="%H:%M:%S")


def setup_logging():
    """Configure logging based on environment."""
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    fmt = os.environ.get("LOG_FORMAT", "auto")  # auto, json, human

    if fmt == "json" or (fmt == "auto" and os.environ.get("DYNO")):
        # Production: JSON format (Heroku sets DYNO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
    else:
        # Development: human-readable
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(HumanFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))

    # Suppress noisy libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)
