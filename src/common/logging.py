"""Structured JSON logging for Lambdas and scripts.

Why JSON-lines: CloudWatch Logs captures stdout as-is. CloudWatch Insights
can then parse each line and let us filter/aggregate on structured fields
like "source=remoteok" or "level=error". This beats free-text logging
once you have a dozen scrapers writing to the same log stream.

Usage:
    from common.logging import log
    log.info("scrape_started", source="remoteok")
    log.warn("rate_limited", source="remoteok", retry_in=30)
    log.error("parse_failed", source="remoteok", native_id="123", error=str(e))
"""
import json
import sys
import time
from typing import Any


class _Log:
    # Python param is `name` (not `event`) so callers can still pass an
    # `event=...` structured field without clobbering the positional arg.
    # The JSON record still uses the key "event" for the call site's name.
    def _emit(self, level: str, name: str, **fields: Any) -> None:
        record = {
            "t": time.time,
            "level": level,
            "event": name,
        }
        record.update(fields)
        # default=str so Decimal/datetime/etc. serialize without blowing up.
        sys.stdout.write(json.dumps(record, default=str) + "\n")
        # Flush eagerly so Lambda logs appear promptly in CloudWatch.
        sys.stdout.flush

    def info(self, name: str, **fields: Any) -> None:
        self._emit("info", name, **fields)

    def warn(self, name: str, **fields: Any) -> None:
        self._emit("warn", name, **fields)

    def error(self, name: str, **fields: Any) -> None:
        self._emit("error", name, **fields)


# Importers use this singleton. No configuration knobs — keep it boring.
log = _Log
