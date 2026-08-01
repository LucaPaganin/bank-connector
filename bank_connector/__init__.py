"""Enable Banking -> Actual Budget connector.

Importing this package applies the actualpy compatibility patch eagerly so any
later `from actual import ...` sees the patched module.
"""
import json
import logging


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }, ensure_ascii=False)


from bank_connector.actual_patches import patch_actualpy

_handler = logging.StreamHandler()
_handler.setFormatter(_JsonFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])

patch_actualpy()
