"""Structured JSON logs (suite document 14 section 5).

With LOG_FORMAT=json, every record is one JSON line:
    {"time", "level", "service", "logger", "requestId", "studentUniqueId", "msg"}
so CloudWatch metric filters can match `{ $.level = "ERROR" }`. requestId and studentUniqueId
come from `extra={...}` when given, else from the text ("request_id=x", "student S1").
Phone numbers are masked in every line as a last line of defence; code must still not log them.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

SERVICE = os.getenv("SERVICE_NAME", "samvaad-backend")
_PHONE = re.compile(r"(?<![\w-])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?![\w-])|(?<![\w-])\+\d{11,15}(?![\w-])")
_REQUEST = re.compile(r"request[_ ]?id[=: ]\s*([^\s|,]+)", re.I)
_STUDENT = re.compile(r"\bstudent(?:_?unique_?id|_id)?[=: ]\s*([A-Za-z0-9_-]{3,})", re.I)


def mask_phones(text: str) -> str:
    def _mask(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return "*" * max(len(digits) - 2, 0) + digits[-2:]

    return _PHONE.sub(_mask, text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.exc_info:
            msg = f"{msg}\n{self.formatException(record.exc_info)}"
        msg = mask_phones(msg)
        req = getattr(record, "request_id", None) or (_REQUEST.search(msg) or [None, None])[1]
        stu = getattr(record, "student_unique_id", None) or (_STUDENT.search(msg) or [None, None])[1]
        return json.dumps(
            {
                "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "service": SERVICE,
                "logger": record.name,
                "requestId": req,
                "studentUniqueId": stu,
                "msg": msg,
            },
            ensure_ascii=False,
        )


class MaskingFormatter(logging.Formatter):
    """Plain text format, with phone numbers masked."""

    def format(self, record: logging.LogRecord) -> str:
        return mask_phones(super().format(record))


def configure_root_logging(level: str | int) -> None:
    handler = logging.StreamHandler()
    if os.getenv("LOG_FORMAT", "").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(MaskingFormatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))
    logging.basicConfig(level=level, handlers=[handler], force=True)
