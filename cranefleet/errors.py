"""Domain failures raised by the control service.

Every failure carries a stable type so the HTTP layer can map it to a status
code without inspecting message text. Request rejections raised by the API
layer itself use :class:`ApiRejection`, which pairs an HTTP status with one
of the stable ``ERR_*`` codes below so clients can branch on the category
instead of parsing message text.
"""

from __future__ import annotations

# Stable error codes served in the ``error.code`` field of the API envelope.
ERR_INVALID_JSON = "invalid_json"
ERR_MISSING_FIELD = "missing_field"
ERR_INVALID_FIELD = "invalid_field"
ERR_NOT_FOUND = "not_found"
ERR_CONFLICT = "conflict"
ERR_INTERNAL = "internal"
ERR_UNKNOWN_VERSION = "unknown_version"


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    """Build the structured ``error`` object served to version-2 clients."""

    body: dict = {"code": code, "message": message}
    if details:
        body["details"] = details
    return body


class ApiRejection(Exception):
    """A request rejected by the API layer with a stable category code."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def body(self) -> dict:
        return error_body(self.code, self.message, self.details)


class CraneFleetError(Exception):
    """Base class for every failure raised by the control service."""


class StoreError(CraneFleetError):
    """Raised when the persistence layer cannot satisfy a request."""


class StoreNotFound(StoreError):
    """Raised when a store key has never been written."""


class ZoneError(CraneFleetError):
    """Raised when a collision-zone operation is rejected."""


class ZoneNotDurable(ZoneError):
    """Raised when a swing is attempted before the zone state is persisted."""


class ClusterError(CraneFleetError):
    """Raised when a fleet-level operation is rejected."""


class NoCranesSurveyed(ClusterError):
    """Raised when a baseline refresh runs before any crane is surveyed."""


class SlewError(CraneFleetError):
    """Base class for slewing failures."""


class SlewBlocked(SlewError):
    """Raised when an interlock (stop, wind or latch) holds the slew."""


class SlewBusy(SlewError):
    """Raised when a swing starts while the slew is already moving."""


class SlewIdle(SlewError):
    """Raised when a swing is applied while the slew is not moving."""


class TrolleyError(CraneFleetError):
    """Base class for trolley failures."""


class HookNotLowered(TrolleyError):
    """Raised when the trolley moves before the hook is lowered."""


class HoistError(CraneFleetError):
    """Base class for hoist failures."""


class HoistBusy(HoistError):
    """Raised when the drum is already turning."""


class FeedbackNotConfirmed(HoistError):
    """Raised when the brake is released before the speed feedback arrives."""


class WindError(CraneFleetError):
    """Base class for wind failures."""


class SlewStopNotAcknowledged(WindError):
    """Raised when the slew stop is not acknowledged during a wind alarm."""


class LimitError(CraneFleetError):
    """Base class for limit failures."""


class StaleRelease(LimitError):
    """Raised when a stale generation tries to release the limit latch."""


class ConfigError(CraneFleetError):
    """Raised when a site configuration payload is invalid."""
