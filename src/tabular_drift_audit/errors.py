"""Typed error outcomes and availability constants for Tabular Drift Audit.

Copyright (c) 2026 Lofrano Tecnologia (https://lofranotecnologia.com.br)
GitHub owner: martinlofranodeoliveira
SPDX-License-Identifier: MIT
"""

from __future__ import annotations

STATUS_COMPLETE = "COMPLETE"
STATUS_INVALID_INPUT = "INVALID_INPUT"
STATUS_UNSUPPORTED_INPUT = "UNSUPPORTED_INPUT"
STATUS_UNKNOWN_BUDGET = "UNKNOWN_BUDGET"
STATUS_NUMERICALLY_INDETERMINATE = "NUMERICALLY_INDETERMINATE"

VALID_ERROR_STATUSES = frozenset(
    {
        STATUS_INVALID_INPUT,
        STATUS_UNSUPPORTED_INPUT,
        STATUS_UNKNOWN_BUDGET,
        STATUS_NUMERICALLY_INDETERMINATE,
    }
)

AVAILABILITY_AVAILABLE = "AVAILABLE"
AVAILABILITY_INSUFFICIENT_NONMISSING = "INSUFFICIENT_NONMISSING"
AVAILABILITY_UNAVAILABLE_SCHEMA = "UNAVAILABLE_SCHEMA"

VALID_AVAILABILITY_STATES = frozenset(
    {
        AVAILABILITY_AVAILABLE,
        AVAILABILITY_INSUFFICIENT_NONMISSING,
        AVAILABILITY_UNAVAILABLE_SCHEMA,
    }
)


class DriftError(Exception):
    """Typed algorithmic error exposing a stable status and reason code."""

    __slots__ = ("_status", "_reason_code", "_message")

    def __init__(self, status: str, reason_code: str, message: str = "") -> None:
        if not isinstance(status, str) or status not in VALID_ERROR_STATUSES:
            raise ValueError(f"Invalid DriftError status: {status!r}")
        if not isinstance(reason_code, str) or not reason_code:
            raise ValueError("DriftError reason_code must be a non-empty string")
        if not isinstance(message, str):
            raise ValueError("DriftError message must be a string")
        self._status = status
        self._reason_code = reason_code
        self._message = message or f"{status}: {reason_code}"
        super().__init__(f"{self._status} [{self._reason_code}]: {self._message}")

    @property
    def status(self) -> str:
        return self._status

    @property
    def reason_code(self) -> str:
        return self._reason_code

    @property
    def reason(self) -> str:
        return self._reason_code

    @property
    def message(self) -> str:
        return self._message

    def to_dict(self) -> dict[str, str]:
        return {
            "status": self._status,
            "reason_code": self._reason_code,
            "message": self._message,
        }
