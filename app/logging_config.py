"""
Structured logging (spec #44).

Emits JSON logs with contextual identifiers (run_id, source_id,
candidate_id, job_id, status, duration, error). Never log secrets or
patient data -- callers must not pass raw request bodies or env vars into
log calls; only pass the specific identifiers/values needed.
"""
import logging
import sys

import structlog

from app.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(**initial_context):
    return structlog.get_logger().bind(**initial_context)
