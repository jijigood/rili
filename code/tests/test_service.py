import json
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

import app
from storage import Storage


class ServiceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.db_path = Path(cls.temp.name) / "queries.sqlite3"
        app.store = Storage(cls.db_path)
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.host, cls.port = cls.server.server_address

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        app.store.close()
        cls.temp.cleanup()

    @classmethod
    def request(cls, method, path, body=None):
        connection = HTTPConnection(cls.host, cls.port, timeout=5)
        headers = {}
        encoded = None
        if body is not None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(encoded))
        connection.request(method, path, encoded, headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        content_type = response.getheader("Content-Type", "")
        parsed = json.loads(payload.decode("utf-8")) if "json" in content_type else payload.decode("utf-8")
        return response.status, parsed

    def test_health_and_unknown_paths_use_json_contract(self):
        status, payload = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["success"], True)
        self.assertEqual(payload["data"]["status"], "ok")

        status, payload = self.request("GET", "/api/does-not-exist")
        self.assertEqual(status, 404)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "NOT_FOUND")

        status, payload = self.request("POST", "/api/does-not-exist")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "NOT_FOUND")

        status, payload = self.request("GET", "/static/missing.js")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "NOT_FOUND")

    def test_single_query_returns_engine_result_and_persists(self):
        status, payload = self.request(
            "POST",
            "/api/query",
            {"submit_time": "2025-01-22 15:00", "fund_type": "EQ"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["data"]["T_date"], "2025-01-22")
        self.assertIn("reasons", payload["data"])

        history_status, history = self.request("GET", "/api/history?limit=10")
        self.assertEqual(history_status, 200)
        self.assertTrue(any(row["kind"] == "query" for row in history["data"]))

    def test_invalid_single_query_is_400_and_persisted(self):
        status, payload = self.request(
            "POST", "/api/query", {"submit_time": "bad", "fund_type": "EQ"}
        )
        self.assertEqual(status, 400)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["error"]["code"], "INVALID_INPUT")
        self.assertIsNone(payload["data"])
        rows = app.store.recent(10)
        self.assertTrue(any(row["kind"] == "query" and row["error"] for row in rows))

    def test_batch_preserves_apply_id_and_isolates_item_errors(self):
        status, payload = self.request(
            "POST",
            "/api/batch",
            {
                "items": [
                    {"apply_id": "OK-1", "submit_time": "2025-01-22 15:00", "fund_type": "EQ"},
                    {"apply_id": "BAD-1", "submit_time": "bad", "fund_type": "EQ"},
                    {"apply_id": "OK-2", "submit_time": "2025-01-22 15:01", "fund_type": "BD"},
                ]
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["success"])
        items = payload["data"]["items"]
        self.assertEqual(payload["data"]["count"], 3)
        self.assertEqual(items[0]["apply_id"], "OK-1")
        self.assertEqual(items[0]["T_date"], "2025-01-22")
        self.assertEqual(items[1]["apply_id"], "BAD-1")
        self.assertEqual(items[1]["error"]["code"], "INVALID_INPUT")
        self.assertEqual(items[2]["apply_id"], "OK-2")
        self.assertEqual(items[2]["T_date"], "2025-01-23")
        self.assertTrue(any(row["kind"] == "batch" for row in app.store.recent(10)))

    def test_batch_shape_errors_are_400(self):
        for body in ({}, {"items": []}, {"items": "not-list"}):
            with self.subTest(body=body):
                status, payload = self.request("POST", "/api/batch", body)
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"]["code"], "INVALID_INPUT")

    def test_calendar_endpoint_for_a_share_and_us(self):
        status, payload = self.request("GET", "/api/calendar?date=2025-01-25&market=A_SHARE")
        self.assertEqual(status, 200)
        self.assertFalse(payload["data"]["is_trading_day"])
        self.assertEqual(payload["data"]["next_trading_day"], "2025-01-27")

        status, payload = self.request("GET", "/api/calendar?date=2025-01-20&market=US")
        self.assertEqual(status, 200)
        self.assertFalse(payload["data"]["is_trading_day"])
        self.assertEqual(payload["data"]["next_trading_day"], "2025-01-21")

    def test_calendar_invalid_input_is_400(self):
        for path in (
            "/api/calendar?date=not-a-date&market=A_SHARE",
            "/api/calendar?date=2025-01-22&market=BAD",
            "/api/calendar?date=2023-12-31&market=A_SHARE",
        ):
            with self.subTest(path=path):
                status, payload = self.request("GET", path)
                self.assertEqual(status, 400)
                self.assertEqual(payload["error"]["code"], "INVALID_INPUT")

    def test_static_page_is_api_driven(self):
        status, html = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("/static/app.js", html)
        status, js = self.request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn("/api/query", js)
        self.assertIn("/api/history", js)

    def test_storage_survives_reopen(self):
        before = len(app.store.recent(500))
        reopened = Storage(self.db_path)
        try:
            self.assertGreaterEqual(len(reopened.recent(500)), before)
        finally:
            reopened.close()


if __name__ == "__main__":
    unittest.main()
