"""Structured logging configuration for Sentri."""

import logging
import logging.handlers
from pathlib import Path


def setup_logging(log_file: Path | None = None, level: str = "INFO") -> None:
    """Configure logging with console + file output."""
    root = logging.getLogger("sentri")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate handlers on re-init
    if root.handlers:
        return

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler (if path provided and directory exists)
    if log_file and log_file.parent.exists():
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
