"""Public API version negotiation and the response envelopes.

The external control interface is versioned explicitly so a server upgrade
never breaks an older integration client:

* ``/api/...`` (no version) and ``/api/v1/...`` are version 1, the frozen
  legacy surface. Fields that v1 returned today are the fields it returns
  forever; newer data only ships under a newer version.
* ``/api/v2/...`` selects version 2. Version 2 is additive: old fields keep
  their meaning, new fields may appear, and a v2 client must ignore fields it
  does not know.

A client may also send an ``Api-Version`` header (``2`` or ``v2``) on an
unversioned path. The path prefix wins when both are present.

Version 2 failures are classified with a stable machine-readable
``ErrorCode`` so callers can tell a malformed body from a missing field, an
unknown resource or a rejected state transition without parsing message text.
Version 1 keeps its historical ``{"ok": false, "error": "..."}`` envelope.
"""

from __future__ import annotations

import re
from typing import Any

API_PREFIX = "/api"
API_VERSION_HEADER = "Api-Version"

V1 = 1
V2 = 2
DEFAULT_VERSION = V1
LATEST_VERSION = V2
SUPPORTED_VERSIONS: tuple[int, ...] = (V1, V2)

# ``details.reason`` values when an INVALID_BODY is reported on v2.
BODY_MALFORMED = "malformed_json"      # bytes were not valid JSON
BODY_NOT_OBJECT = "not_json_object"    # parsed JSON, but not an object

_VERSION_PREFIX = re.compile(r"^/api/v(\d+)(?=/|$)")
_HEADER_VALUE = re.compile(r"^v?(\d+)$", re.IGNORECASE)


class ErrorCode:
    """Stable failure categories returned in version 2 error envelopes.

    Codes are part of the public contract: never reuse one for a different
    meaning, retire a code rather than changing it.
    """

    # The request itself could not be accepted.
    INVALID_BODY = "invalid_body"            # body was not a JSON object
    VALIDATION_ERROR = "validation_error"    # well-formed body, bad value
    MISSING_FIELD = "missing_field"          # required field absent/blank
    RESOURCE_NOT_FOUND = "not_found"         # unknown route or resource id
    VERSION_NOT_SUPPORTED = "version_not_supported"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    # The request was well formed but the current machine state rejects it.
    STATE_CONFLICT = "state_conflict"
    ZONE_LATCHED = "zone_latched"
    ZONE_NOT_DURABLE = "zone_not_durable"
    SLEW_BLOCKED = "slew_blocked"
    SLEW_BUSY = "slew_busy"
    SLEW_IDLE = "slew_idle"
    HOOK_NOT_LOWERED = "hook_not_lowered"
    HOIST_BUSY = "hoist_busy"
    FEEDBACK_NOT_CONFIRMED = "feedback_not_confirmed"
    SLEW_STOP_NOT_ACKNOWLEDGED = "slew_stop_not_acknowledged"
    # Server-side failure, not the caller's fault.
    STORE_UNAVAILABLE = "store_unavailable"


class ApiError(Exception):
    """A failure rendered through the versioned error envelope."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def is_api_path(path: str) -> bool:
    """Return whether ``path`` (already split from its query) targets the API."""

    return path == API_PREFIX or path.startswith("/api/") or bool(_VERSION_PREFIX.match(path))


def negotiate(path: str, header: str | None = None) -> tuple[int, str]:
    """Resolve ``(api_version, canonical_path)`` for an API request.

    ``canonical_path`` has any version prefix stripped, e.g.
    ``/api/v2/zone/state`` becomes ``/api/zone/state``. Raises
    :class:`ApiError` when the client names a version this server does not
    speak.
    """

    match = _VERSION_PREFIX.match(path)
    if match:
        version = _parse_version_number(int(match.group(1)), path)
        remainder = path[match.end():]
        if remainder:
            canonical = API_PREFIX + remainder
        else:  # "/api/v2" with no resource
            canonical = API_PREFIX
        return version, canonical

    version = DEFAULT_VERSION
    if header is not None and header.strip():
        match = _HEADER_VALUE.match(header.strip())
        if match is None:
            raise ApiError(
                400,
                ErrorCode.VERSION_NOT_SUPPORTED,
                f"unsupported {API_VERSION_HEADER} header: {header!r}",
                {"supported": list(SUPPORTED_VERSIONS)},
            )
        version = _parse_version_number(int(match.group(1)), header)
    return version, path


def _parse_version_number(number: int, requested: object) -> int:
    if number not in SUPPORTED_VERSIONS:
        raise ApiError(
            404,
            ErrorCode.VERSION_NOT_SUPPORTED,
            f"api version {number} is not supported",
            {"version": number, "supported": list(SUPPORTED_VERSIONS)},
        )
    return number


def render_ok(version: int, data: Any) -> dict[str, Any]:
    """Build a success envelope for the negotiated version."""

    envelope: dict[str, Any] = {"ok": True}
    if version >= V2:
        envelope["api_version"] = V2
    envelope["data"] = data
    return envelope


def render_error(version: int, error: ApiError) -> dict[str, Any]:
    """Build an error envelope for the negotiated version.

    Version 1 keeps the legacy shape ``{"ok": False, "error": "<message>"}``;
    version 2 nests the stable code and optional structured details.
    """

    if version < V2:
        return {"ok": False, "error": error.message}
    body: dict[str, Any] = {"code": error.code, "message": error.message}
    if error.details:
        body["details"] = error.details
    return {"ok": False, "api_version": V2, "error": body}
