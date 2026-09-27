"""API version negotiation and the frozen version-1 response contract.

Routes are served under ``/api/v<version>/...``. Unprefixed ``/api/...``
paths are treated as version 1 so existing clients keep working unchanged.

Version 1 responses are *projected* through the whitelists below: only the
fields that existed when the contract was frozen reach a v1 client. Fields
added to the views later (for v2) are stripped automatically, so a new key
can never break an old client again. Version 2 receives the full views.

A schema is a nested ``dict`` mirroring the response shape:

- a plain key whitelists that key, and its value is projected recursively;
- the ``"*"`` key whitelists every remaining key (used for maps keyed by
  crane id, where the keys are not known ahead of time);
- ``...`` (Ellipsis) keeps the whole subtree as-is;
- a one-element list applies its element schema to every list item.
"""

from __future__ import annotations

from typing import Any

VERSION_1 = 1
VERSION_2 = 2
LATEST_VERSION = VERSION_2
SUPPORTED_VERSIONS = (VERSION_1, VERSION_2)

POINT = {"x": ..., "y": ...}

AUDIT_ENTRY = {
    "id": ...,
    "at": ...,
    "crane_id": ...,
    "action": ...,
    "detail": ...,
}

COMMAND = {
    "source": ...,
    "action": ...,
    "axis": ...,
    "angle": ...,
}

ZONE = {
    "id": ...,
    "name": ...,
    "boundary": [POINT],
    "revision": ...,
    "durable": ...,
    "occupants": ...,
    "conflict": ...,
    "latch": ...,
    "coverage": {
        "inside": ...,
        "outside": ...,
        "inside_bounds": ...,
        "area": ...,
        "bounds_width": ...,
        "bounds_height": ...,
        "center": POINT,
        "expanded_area": ...,
        "expanded_overlap": ...,
    },
    "occupied": {"*": ...},
    "registrations": ...,
    "boundary_area": ...,
    "occupant_count": ...,
    "separation": {
        "distance": ...,
        "positions": {"*": POINT},
        "revision": ...,
        "fresh": ...,
    },
    "latest_registration_seq": ...,
    "latch_generation": ...,
    "pending_releases": [{"crane_id": ..., "generation": ...}],
    "occupancy_version": ...,
}

SLEW = {
    "id": ...,
    "angle": ...,
    "moving": ...,
    "stopped": ...,
    "wind_held": ...,
    "latch_held": ...,
    "faulted": ...,
    "stop_count": ...,
    "last_stop_sequence": ...,
    "stop_reason": ...,
    "stop_sequence": ...,
    "axis_stopped": {"*": ...},
    "ramp_target": ...,
    "ramp_velocity": ...,
    "ramp_on_target": ...,
}

TROLLEY = {
    "id": ...,
    "position": ...,
    "moving": ...,
    "last_speed": ...,
}

HOIST = {
    "id": ...,
    "hook_lowered": ...,
    "brake_engaged": ...,
    "moving": ...,
    "drum_layers": ...,
    "cable_height": ...,
    "feedback_speed": ...,
}

WIND = {
    "level": ...,
    "active": ...,
    "latch": ...,
    "gust_peak": ...,
    "gust_samples": ...,
    "alarm_sequence": ...,
    "level_history": ...,
    "generation": ...,
}

LIMIT = {
    "tripped": {"*": ...},
    "stopped": {"*": ...},
    "latch": ...,
    "generation": ...,
}

NEAREST_PAIR = {
    "pair": {
        "first": ...,
        "second": ...,
        "distance": ...,
        "line": {"start": POINT, "end": POINT},
    },
    "center": POINT,
    "clearance": ...,
    "length": ...,
    "safe_margin": ...,
    "below_safe": ...,
}

FLEET = {
    "positions": {"*": POINT},
    "baseline": {"*": POINT},
    "revision": ...,
    "baseline_revision": ...,
    "released": ...,
    "release_generation": ...,
    "release_counts": {"*": ...},
    "release_ledger": [{"crane_id": ..., "generation": ..., "count": ...}],
    "nearest_pair": NEAREST_PAIR,
    "fleet_center": POINT,
    "spread": ...,
    "mean_spacing": ...,
    "spacing_deviation": ...,
}

CRANE = {
    "id": ...,
    "last_command": COMMAND,
    "stops_pending": ...,
    "sway_angle": ...,
    "momentum": ...,
    "load_height": ...,
    "command_sequence": ...,
    "stop_sequence": ...,
    "blocked_moves": ...,
}

LOAD = {
    "sway_angle": ...,
    "momentum": ...,
    "height": ...,
    "mass": ...,
}

CONFIG = {
    "site_name": ...,
    "safe_separation": ...,
    "alarm_threshold": ...,
    "surveyed_cranes": ...,
}

STATE = {
    "zone": ZONE,
    "slew": SLEW,
    "trolley": TROLLEY,
    "hoist": HOIST,
    "wind": WIND,
    "limit": LIMIT,
    "cluster": FLEET,
    "crane": CRANE,
    "load": LOAD,
    "config": CONFIG,
    "audit": ...,
    "audit_capacity": ...,
    "alarms": ...,
    "latest_wind_stop": AUDIT_ENTRY,
    "store": {"keys": ..., "bytes": ...},
}

# The v1 whitelist for every route, keyed by the canonical (unprefixed) path.
V1_SCHEMAS: dict[str, Any] = {
    "/api/state": STATE,
    "/api/zone/state": ZONE,
    "/api/zone/occupy": {
        "occupants": ...,
        "conflicts": [{"zone_id": ..., "crane_ids": ...}],
        "mismatches": ...,
    },
    "/api/zone/vacate": ...,
    "/api/zone/clear": ZONE,
    "/api/cluster/survey": FLEET,
    "/api/slew/start": SLEW,
    "/api/slew/stop": SLEW,
    "/api/slew/target": SLEW,
    "/api/slew/fault": SLEW,
    "/api/wind/alarm": WIND,
    "/api/wind/clear": WIND,
    "/api/crane/command": COMMAND,
    "/api/crane/state": CRANE,
    "/api/crane/load": LOAD,
    "/api/crane/reset": COMMAND,
    "/api/trolley/state": TROLLEY,
    "/api/trolley/resume": {"trolley": TROLLEY, "hoist": HOIST},
    "/api/hoist/state": HOIST,
    "/api/hoist/payout": HOIST,
    "/api/hoist/feedback": HOIST,
    "/api/hoist/release": HOIST,
    "/api/hoist/stop": HOIST,
    "/api/limit/state": LIMIT,
    "/api/limit/check": {
        "exceeded": ...,
        "margins": ...,
        "limit": LIMIT,
        "arbitration": {"axes": ..., "stops": ..., "sequence": ...},
    },
    "/api/config": CONFIG,
    "/api/audit": [AUDIT_ENTRY],
}


def split_api_path(path: str) -> tuple[int | None, str]:
    """Split ``/api/v2/state`` into ``(2, "/api/state")``.

    Unprefixed ``/api/...`` paths resolve to version 1. A ``vN`` segment with
    an unsupported version yields ``(None, path)`` so the caller can reject
    the request with an explicit error instead of misrouting it.
    """

    parts = path.split("/", 3)
    if len(parts) >= 3:
        segment = parts[2]
        if segment.startswith("v") and segment[1:].isdigit():
            version = int(segment[1:])
            if version in SUPPORTED_VERSIONS:
                rest = "/api/" + parts[3] if len(parts) > 3 else "/api"
                return version, rest
            return None, path
    return VERSION_1, path


def project(data: Any, schema: Any) -> Any:
    """Keep only the whitelisted keys, recursing into dicts and lists."""

    if schema is ...:
        return data
    if isinstance(schema, dict) and isinstance(data, dict):
        projected: dict[str, Any] = {}
        for key, value in data.items():
            if key in schema:
                projected[key] = project(value, schema[key])
            elif "*" in schema:
                projected[key] = project(value, schema["*"])
        return projected
    if isinstance(schema, list) and isinstance(data, list) and schema:
        return [project(item, schema[0]) for item in data]
    return data


def project_v1(path: str, data: Any) -> Any:
    """Project a response body down to the frozen v1 contract for ``path``."""

    return project(data, V1_SCHEMAS.get(path, ...))
