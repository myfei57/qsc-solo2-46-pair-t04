"""Tests for public API versioning and the version 2 classified error envelope."""

from __future__ import annotations

import http.client
import json
import threading
import unittest
from pathlib import Path

from cranefleet import api
from cranefleet.console import Request
from cranefleet.server import serve
from cranefleet.wiring import build_app

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


def call(console, method, path, body=None, version=1, body_valid=True, body_error=None):  # noqa: ANN201, ANN001
    request = Request(
        method=method,
        path=path,
        query={},
        body=body or {},
        body_valid=body_valid,
        api_version=version,
        body_error=body_error,
    )
    return console.dispatch(request)


class VersionEnvelopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = build_app("memory", web_root=str(WEB_ROOT))
        self.console = self.app.console

    def tearDown(self) -> None:
        self.app.shutdown()

    # ------------------------------------------------------------- freeze v1

    def test_unversioned_defaults_to_v1_shape(self) -> None:
        status, payload = call(self.console, "GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(set(payload.keys()), {"ok", "data"})
        self.assertNotIn("api_version", payload)
        # The frozen v1 views must not leak v2-only fields.
        self.assertNotIn("ramp_remaining", payload["data"]["slew"])
        self.assertNotIn("crane_count", payload["data"]["cluster"])

    def test_v1_error_envelope_is_unchanged(self) -> None:
        status, payload = call(
            self.console, "POST", "/api/zone/occupy", version=1, body_valid=False,
            body_error=api.BODY_MALFORMED,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload, {"ok": False, "error": "bad request"})

        status, payload = call(self.console, "POST", "/api/zone/occupy", {}, version=1)
        self.assertEqual(status, 400)
        self.assertEqual(payload, {"ok": False, "error": "crane_id required"})

    def test_v1_coerces_bad_numbers_to_zero(self) -> None:
        # The historical lenient behaviour must survive for frozen clients.
        status, payload = call(
            self.console, "POST", "/api/slew/target", {"angle": "fast"}, version=1
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["ramp_target"], 0.0)

    def test_v1_unknown_route_is_plain_404(self) -> None:
        status, payload = call(self.console, "GET", "/api/unknown", version=1)
        self.assertEqual((status, payload), (404, {"ok": False, "error": "not found"}))

    # ------------------------------------------------------------------- v2

    def test_v2_state_is_additive(self) -> None:
        status, payload = call(self.console, "GET", "/api/state", version=2)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["api_version"], 2)
        # v1 fields remain present and meaningful...
        self.assertIn("ramp_target", payload["data"]["slew"])
        self.assertIn("positions", payload["data"]["cluster"])
        # ...and v2 fields are added.
        self.assertIn("ramp_remaining", payload["data"]["slew"])
        self.assertEqual(payload["data"]["cluster"]["crane_count"], 2)
        self.assertEqual(
            payload["data"]["cluster"]["surveyed_cranes"], ["crane-1", "crane-2"]
        )

    def test_v2_malformed_body_is_classified(self) -> None:
        status, payload = call(
            self.console,
            "POST",
            "/api/zone/occupy",
            version=2,
            body_valid=False,
            body_error=api.BODY_MALFORMED,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["api_version"], 2)
        error = payload["error"]
        self.assertEqual(error["code"], api.ErrorCode.INVALID_BODY)
        self.assertEqual(error["details"]["reason"], api.BODY_MALFORMED)

    def test_v2_non_object_body_is_a_distinct_reason(self) -> None:
        status, payload = call(
            self.console,
            "POST",
            "/api/zone/occupy",
            version=2,
            body_valid=False,
            body_error=api.BODY_NOT_OBJECT,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], api.ErrorCode.INVALID_BODY)
        self.assertEqual(payload["error"]["details"]["reason"], api.BODY_NOT_OBJECT)

    def test_v2_missing_field_names_the_field(self) -> None:
        status, payload = call(
            self.console, "POST", "/api/zone/occupy", {}, version=2
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], api.ErrorCode.MISSING_FIELD)
        self.assertEqual(payload["error"]["details"]["field"], "crane_id")

    def test_v2_validation_error_names_the_bad_value(self) -> None:
        status, payload = call(
            self.console, "POST", "/api/slew/target", {"angle": "fast"}, version=2
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], api.ErrorCode.VALIDATION_ERROR)
        self.assertEqual(payload["error"]["details"]["field"], "angle")
        self.assertEqual(payload["error"]["details"]["received"], "fast")

    def test_v2_bad_points_payload_is_validation_error(self) -> None:
        status, payload = call(
            self.console,
            "POST",
            "/api/cluster/survey",
            {"cranes": {"crane-1": {"x": 1}}},
            version=2,
        )
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], api.ErrorCode.VALIDATION_ERROR)

    def test_v2_resource_not_found(self) -> None:
        status, payload = call(self.console, "GET", "/api/unknown", version=2)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], api.ErrorCode.RESOURCE_NOT_FOUND)

        status, payload = call(
            self.console,
            "POST",
            "/api/crane/command",
            {"source": "cab", "action": "lower", "crane_id": "crane-9"},
            version=2,
        )
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], api.ErrorCode.RESOURCE_NOT_FOUND)
        self.assertEqual(payload["error"]["details"]["resource"], "crane")

    def test_v2_wrong_method_is_405(self) -> None:
        status, payload = call(self.console, "DELETE", "/api/state", version=2)
        self.assertEqual(status, 405)
        self.assertEqual(payload["error"]["code"], api.ErrorCode.METHOD_NOT_ALLOWED)
        self.assertEqual(payload["error"]["details"]["allowed"], ["GET"])
        # v1 keeps 404 for the same request.
        status, _ = call(self.console, "DELETE", "/api/state", version=1)
        self.assertEqual(status, 404)

    def test_v2_state_conflict_has_stable_code(self) -> None:
        # Block the slew with the zone latch, then try to start it.
        call(
            self.console,
            "POST",
            "/api/cluster/survey",
            {"cranes": {"crane-2": {"x": 5, "y": 0}}},
            version=2,
        )
        call(self.console, "POST", "/api/zone/occupy", {"crane_id": "crane-1"}, version=2)
        call(self.console, "POST", "/api/zone/occupy", {"crane_id": "crane-2"}, version=2)
        status, payload = call(self.console, "POST", "/api/slew/start", version=2)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], api.ErrorCode.ZONE_LATCHED)

    def test_v2_feedback_conflict_code(self) -> None:
        call(
            self.console,
            "POST",
            "/api/crane/command",
            {"source": "cab", "action": "lower"},
            version=2,
        )
        status, payload = call(self.console, "POST", "/api/hoist/release", version=2)
        self.assertEqual(status, 409)
        self.assertEqual(
            payload["error"]["code"], api.ErrorCode.FEEDBACK_NOT_CONFIRMED
        )
        # The v1 human message still comes through on v2 as well.
        self.assertEqual(payload["error"]["message"], "feedback not confirmed")


class NegotiationTests(unittest.TestCase):
    def test_path_prefix_selects_version_and_strips_prefix(self) -> None:
        version, canonical = api.negotiate("/api/v2/zone/state")
        self.assertEqual((version, canonical), (2, "/api/zone/state"))
        version, canonical = api.negotiate("/api/v1/state")
        self.assertEqual((version, canonical), (1, "/api/state"))

    def test_unversioned_path_defaults_to_v1(self) -> None:
        version, canonical = api.negotiate("/api/state")
        self.assertEqual((version, canonical), (1, "/api/state"))

    def test_header_selects_version(self) -> None:
        self.assertEqual(api.negotiate("/api/state", "2")[0], 2)
        self.assertEqual(api.negotiate("/api/state", "v2")[0], 2)
        self.assertEqual(api.negotiate("/api/state", "1")[0], 1)

    def test_path_wins_over_header(self) -> None:
        version, _ = api.negotiate("/api/v1/state", "2")
        self.assertEqual(version, 1)

    def test_unknown_path_version_is_404_with_supported_list(self) -> None:
        with self.assertRaises(api.ApiError) as caught:
            api.negotiate("/api/v9/state")
        self.assertEqual(caught.exception.status, 404)
        self.assertEqual(caught.exception.code, api.ErrorCode.VERSION_NOT_SUPPORTED)
        self.assertEqual(caught.exception.details["supported"], [1, 2])

    def test_bad_header_version_is_400(self) -> None:
        with self.assertRaises(api.ApiError) as caught:
            api.negotiate("/api/state", "banana")
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(caught.exception.code, api.ErrorCode.VERSION_NOT_SUPPORTED)

    def test_header_value_regex(self) -> None:
        # Guard against accidental regex loosening.
        self.assertIsNone(api._HEADER_VALUE.match("2 extra"))


class VersionedHttpTests(unittest.TestCase):
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

    def _request(self, method, path, body=None, headers=None):  # noqa: ANN201, ANN001
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        raw = json.dumps(body).encode("utf-8") if body is not None else None
        sent_headers = {"Content-Type": "application/json"} if body is not None else {}
        if headers:
            sent_headers.update(headers)
        connection.request(method, path, body=raw, headers=sent_headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, dict(response.getheaders()), data

    def test_v2_path_over_http(self) -> None:
        status, headers, raw = self._request("GET", "/api/v2/state")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Api-Version"), "2")
        payload = json.loads(raw)
        self.assertEqual(payload["api_version"], 2)
        self.assertIn("crane_count", payload["data"]["cluster"])

    def test_v1_path_matches_legacy_default(self) -> None:
        status, headers, raw = self._request("GET", "/api/v1/state")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Api-Version"), "1")
        self.assertEqual(set(json.loads(raw).keys()), {"ok", "data"})

    def test_header_version_over_http(self) -> None:
        status, _, raw = self._request("GET", "/api/state", headers={"Api-Version": "2"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["api_version"], 2)

    def test_unknown_version_over_http(self) -> None:
        status, headers, raw = self._request("GET", "/api/v9/state")
        self.assertEqual(status, 404)
        payload = json.loads(raw)
        self.assertEqual(payload["error"]["code"], api.ErrorCode.VERSION_NOT_SUPPORTED)
        self.assertEqual(headers.get("Api-Version"), str(api.LATEST_VERSION))

    def test_malformed_json_body_over_http(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "POST",
            "/api/v2/zone/occupy",
            body=b"{not json",
            headers={"Content-Type": "application/json", "Content-Length": "9"},
        )
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        self.assertEqual(response.status, 400)
        payload = json.loads(raw)
        self.assertEqual(payload["error"]["code"], api.ErrorCode.INVALID_BODY)
        self.assertEqual(payload["error"]["details"]["reason"], api.BODY_MALFORMED)

    def test_json_array_body_over_http(self) -> None:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "POST",
            "/api/v2/zone/occupy",
            body=b"[1, 2]",
            headers={"Content-Type": "application/json", "Content-Length": "6"},
        )
        response = connection.getresponse()
        payload = json.loads(response.read())
        connection.close()
        self.assertEqual(response.status, 400)
        self.assertEqual(payload["error"]["details"]["reason"], api.BODY_NOT_OBJECT)


if __name__ == "__main__":
    unittest.main()
