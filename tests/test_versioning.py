"""Tests for API versioning, the frozen v1 contract and classified errors."""

from __future__ import annotations

import http.client
import json
import threading
import unittest
from pathlib import Path

from cranefleet.console import Request
from cranefleet.errors import (
    ERR_CONFLICT,
    ERR_INTERNAL,
    ERR_INVALID_FIELD,
    ERR_INVALID_JSON,
    ERR_MISSING_FIELD,
    ERR_NOT_FOUND,
    ERR_UNKNOWN_VERSION,
)
from cranefleet.server import serve
from cranefleet.versions import STATE, project
from cranefleet.wiring import build_app

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


def call(console, method, path, body=None, version=None, body_valid=True):
    request = Request(
        method=method,
        path=path,
        query={},
        body=body or {},
        body_valid=body_valid,
        version=version or 1,
    )
    return console.dispatch(request)


GET_ROUTES = (
    ("/api/state", "data"),
    ("/api/zone/state", "data"),
    ("/api/cluster/survey", "data"),
    ("/api/crane/state", "data"),
    ("/api/crane/load", "data"),
    ("/api/trolley/state", "data"),
    ("/api/hoist/state", "data"),
    ("/api/limit/state", "data"),
    ("/api/config", "data"),
    ("/api/audit", "data"),
)

# The frozen top-level keys a v1 client must keep receiving forever.
FROZEN_KEYS = {
    "/api/state": {
        "zone", "slew", "trolley", "hoist", "wind", "limit", "cluster",
        "crane", "load", "config", "audit", "audit_capacity", "alarms",
        "latest_wind_stop", "store",
    },
    "/api/zone/state": {
        "id", "name", "boundary", "revision", "durable", "occupants",
        "conflict", "latch", "coverage", "occupied", "registrations",
        "boundary_area", "occupant_count", "separation",
        "latest_registration_seq", "latch_generation", "pending_releases",
        "occupancy_version",
    },
    "/api/cluster/survey": {
        "positions", "baseline", "revision", "baseline_revision", "released",
        "release_generation", "release_counts", "release_ledger",
        "nearest_pair", "fleet_center", "spread", "mean_spacing",
        "spacing_deviation",
    },
}


class VersionRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = build_app("memory", web_root=str(WEB_ROOT))
        self.console = self.app.console

    def tearDown(self) -> None:
        self.app.shutdown()

    def test_bare_path_is_version_1(self) -> None:
        status, payload = call(self.console, "GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertNotIn("api_version", payload)
        self.assertNotIn("meta", payload["data"])

    def test_explicit_v1_matches_bare_path(self) -> None:
        for path, key in GET_ROUTES:
            with self.subTest(path=path):
                bare = call(self.console, "GET", path)[1]
                explicit = call(self.console, "GET", path, version=1)[1]
                self.assertEqual(bare, explicit)

    def test_v2_envelope_and_new_fields(self) -> None:
        status, payload = call(self.console, "GET", "/api/state", version=2)
        self.assertEqual(status, 200)
        self.assertEqual(payload["api_version"], 2)
        self.assertTrue(payload["ok"])
        self.assertIn("meta", payload["data"])
        self.assertEqual(payload["data"]["meta"]["api_version"], 2)
        self.assertIn("generated_at", payload["data"]["meta"])

    def test_new_field_does_not_reach_v1(self) -> None:
        _, v1 = call(self.console, "GET", "/api/state")
        _, v2 = call(self.console, "GET", "/api/state", version=2)
        self.assertNotIn("meta", v1["data"])
        self.assertIn("meta", v2["data"])
        # Every v2 key apart from the v2-only addition is present in v1.
        self.assertEqual(set(v1["data"]) | {"meta"}, set(v2["data"]))

    def test_v1_contract_key_sets_are_frozen(self) -> None:
        for path, expected in FROZEN_KEYS.items():
            with self.subTest(path=path):
                data = call(self.console, "GET", path)[1]["data"]
                self.assertEqual(set(data), expected)

    def test_unknown_route_is_classified(self) -> None:
        status, payload = call(self.console, "GET", "/api/unknown", version=2)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], ERR_NOT_FOUND)
        self.assertEqual(payload["error"]["message"], "not found")

    def test_v1_unknown_route_stays_a_string(self) -> None:
        status, payload = call(self.console, "GET", "/api/unknown")
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"ok": False, "error": "not found"})


class ErrorClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = build_app("memory", web_root=str(WEB_ROOT))
        self.console = self.app.console

    def tearDown(self) -> None:
        self.app.shutdown()

    def test_invalid_json(self) -> None:
        request = Request(
            "POST", "/api/zone/occupy", {}, {}, body_valid=False, version=2
        )
        status, payload = self.console.dispatch(request)
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ERR_INVALID_JSON)

    def test_missing_field_is_classified(self) -> None:
        status, payload = call(
            self.console, "POST", "/api/zone/occupy", {}, version=2
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ERR_MISSING_FIELD)
        self.assertEqual(payload["error"]["details"]["field"], "crane_id")

    def test_invalid_field_is_classified(self) -> None:
        status, payload = call(
            self.console,
            "POST",
            "/api/slew/target",
            {"angle": "abc"},
            version=2,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ERR_INVALID_FIELD)
        self.assertEqual(payload["error"]["details"]["field"], "angle")

    def test_missing_numeric_field_is_missing_field(self) -> None:
        status, payload = call(
            self.console, "POST", "/api/wind/alarm", {}, version=2
        )
        self.assertEqual(payload["error"]["code"], ERR_MISSING_FIELD)

    def test_v1_remains_lenient_with_bad_numbers(self) -> None:
        status, payload = call(
            self.console,
            "POST",
            "/api/slew/target",
            {"angle": "abc"},
            version=1,
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["ramp_target"], 0.0)

    def test_survey_payload_categories(self) -> None:
        status, payload = call(
            self.console, "POST", "/api/cluster/survey", {}, version=2
        )
        self.assertEqual(payload["error"]["code"], ERR_MISSING_FIELD)
        status, payload = call(
            self.console,
            "POST",
            "/api/cluster/survey",
            {"cranes": {"crane-1": {"x": 1}}},
            version=2,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ERR_INVALID_FIELD)
        status, payload = call(
            self.console,
            "POST",
            "/api/cluster/survey",
            {"cranes": []},
            version=2,
        )
        self.assertEqual(payload["error"]["code"], ERR_INVALID_FIELD)

    def test_command_payload_categories(self) -> None:
        status, payload = call(
            self.console, "POST", "/api/crane/command", {}, version=2
        )
        self.assertEqual(payload["error"]["code"], ERR_MISSING_FIELD)
        status, payload = call(
            self.console,
            "POST",
            "/api/crane/command",
            {"action": "explode"},
            version=2,
        )
        self.assertEqual(payload["error"]["code"], ERR_INVALID_FIELD)
        status, payload = call(
            self.console,
            "POST",
            "/api/crane/command",
            {"action": "swing", "angle": "fast"},
            version=2,
        )
        self.assertEqual(payload["error"]["code"], ERR_INVALID_FIELD)
        status, payload = call(
            self.console,
            "POST",
            "/api/crane/command",
            {"action": "swing", "source": "pigeon"},
            version=2,
        )
        self.assertEqual(payload["error"]["code"], ERR_INVALID_FIELD)
        status, payload = call(
            self.console,
            "POST",
            "/api/crane/command",
            {"crane_id": "crane-9", "action": "stop"},
            version=2,
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], ERR_NOT_FOUND)

    def test_conflict_is_classified_but_v1_message_unchanged(self) -> None:
        call(
            self.console,
            "POST",
            "/api/cluster/survey",
            {"cranes": {"crane-2": {"x": 5, "y": 0}}},
            version=2,
        )
        call(self.console, "POST", "/api/zone/occupy", {"crane_id": "crane-1"}, version=2)
        call(self.console, "POST", "/api/zone/occupy", {"crane_id": "crane-2"}, version=2)
        status, v2 = call(self.console, "POST", "/api/slew/start", version=2)
        self.assertEqual(status, 409)
        self.assertEqual(v2["error"]["code"], ERR_CONFLICT)
        self.assertEqual(v2["error"]["message"], "zone latch held")
        status, v1 = call(self.console, "POST", "/api/slew/start", version=1)
        self.assertEqual(status, 409)
        self.assertEqual(v1, {"ok": False, "error": "zone latch held"})

    def test_feedback_interlock_category_and_v1_message(self) -> None:
        call(
            self.console,
            "POST",
            "/api/crane/command",
            {"source": "cab", "action": "lower"},
            version=2,
        )
        status, payload = call(self.console, "POST", "/api/hoist/release", version=2)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], ERR_CONFLICT)
        self.assertEqual(payload["error"]["message"], "feedback not confirmed")

    def test_unexpected_error_is_internal_not_a_dropped_connection(self) -> None:
        def boom(request):
            raise RuntimeError("simulated fault")

        self.console._routes[("GET", "/api/state")] = boom
        status, payload = call(self.console, "GET", "/api/state", version=2)
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"]["code"], ERR_INTERNAL)
        status, payload = call(self.console, "GET", "/api/state", version=1)
        self.assertEqual(status, 500)
        self.assertEqual(payload["error"], "internal error")


class ProjectionTests(unittest.TestCase):
    def test_wildcard_covers_dynamic_keys(self) -> None:
        schema = {"positions": {"*": {"x": ...}}}
        data = {"positions": {"crane-1": {"x": 1, "y": 2}, "crane-2": {"x": 3, "y": 4}}}
        projected = project(data, schema)
        self.assertEqual(projected, {"positions": {"crane-1": {"x": 1}, "crane-2": {"x": 3}}})

    def test_added_fields_are_stripped_everywhere(self) -> None:
        # Simulate a future developer adding keys at every nesting level.
        app = build_app("memory", web_root=str(WEB_ROOT))
        try:
            data = call(app.console, "GET", "/api/state")[1]["data"]
        finally:
            app.shutdown()
        data["future"] = True
        data["slew"]["future"] = True
        data["cluster"]["positions"]["crane-1"]["future"] = True
        projected = project(data, STATE)
        self.assertNotIn("future", projected)
        self.assertNotIn("future", projected["slew"])
        self.assertNotIn("future", projected["cluster"]["positions"]["crane-1"])


class HttpVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = build_app("memory", web_root=str(WEB_ROOT))
        self.server = serve(self.app.console, "127.0.0.1", 0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.app.shutdown()

    def _request(self, method, path, body=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        payload = None
        if raw is not None:
            payload = raw.encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, json.loads(data)

    def test_v2_over_http(self) -> None:
        status, payload = self._request("GET", "/api/v2/state")
        self.assertEqual(status, 200)
        self.assertEqual(payload["api_version"], 2)
        self.assertIn("meta", payload["data"])

    def test_bare_v1_over_http(self) -> None:
        status, payload = self._request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertNotIn("api_version", payload)
        self.assertNotIn("meta", payload["data"])

    def test_v1_prefix_works(self) -> None:
        status, payload = self._request(
            "POST", "/api/v1/zone/occupy", {"crane_id": "crane-1"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["occupants"], ["crane-1"])

    def test_invalid_json_over_http(self) -> None:
        status, payload = self._request(
            "POST", "/api/v2/zone/occupy", raw="{not json"
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], ERR_INVALID_JSON)

    def test_unknown_version_over_http(self) -> None:
        status, payload = self._request("GET", "/api/v9/state")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], ERR_UNKNOWN_VERSION)
        self.assertEqual(payload["error"]["details"]["supported_versions"], [1, 2])


if __name__ == "__main__":
    unittest.main()
