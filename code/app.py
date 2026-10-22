from __future__ import annotations

import json
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from calendar_engine import CalendarError, Engine
from storage import Storage


BASE = Path(__file__).parent
engine = Engine(BASE / "data")
store = Storage(BASE / "queries.sqlite3")


def response(ok: bool, data=None, error=None) -> dict:
    return {"success": ok, "data": data, "error": error}


def error_payload(code: str, message: str) -> dict:
    return {"code": code, "message": message}


class Handler(BaseHTTPRequestHandler):
    server_version = "FundCalendar/1.0"

    def log_message(self, format: str, *args) -> None:
        # Keep the console useful when the service is run in the exam environment.
        print(f"{self.address_string()} - {format % args}")

    def send_json(self, payload: dict, status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 2_000_000:
                raise ValueError("请求体不能为空且不能超过2MB")
            return json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("请求体必须是合法JSON") from exc

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/health":
                return self.send_json(response(True, {"status": "ok"}))
            if parsed.path == "/api/history":
                raw_limit = parse_qs(parsed.query).get("limit", ["50"])[0]
                try:
                    limit = int(raw_limit)
                except ValueError as exc:
                    raise ValueError("limit必须是整数") from exc
                return self.send_json(response(True, store.recent(limit)))
            if parsed.path == "/api/calendar":
                query = parse_qs(parsed.query)
                current = date.fromisoformat(query.get("date", [""])[0])
                market = query.get("market", ["A_SHARE"])[0]
                if market not in {"A_SHARE", "US"}:
                    raise ValueError("market必须为A_SHARE或US")
                calendar = engine.a if market == "A_SHARE" else engine.u
                if not (calendar.START <= current <= calendar.END):
                    raise ValueError("日期超出日历覆盖范围")
                is_trading = calendar.is_trading(current)
                next_day = calendar.next_trading(current, include=not is_trading)
                return self.send_json(
                    response(
                        True,
                        {
                            "date": current.isoformat(),
                            "market": market,
                            "is_trading_day": is_trading,
                            "next_trading_day": next_day.isoformat(),
                        },
                    )
                )
            if parsed.path == "/" or parsed.path.startswith("/static/"):
                relative = "index.html" if parsed.path == "/" else parsed.path.removeprefix("/static/")
                path = (BASE / "static" / relative).resolve()
                static_root = (BASE / "static").resolve()
                if static_root not in path.parents or not path.is_file():
                    raise FileNotFoundError(relative)
                content_type = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(
                    path.suffix, "application/octet-stream"
                )
                raw = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", f"{content_type}; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self.send_json(response(False, error=error_payload("NOT_FOUND", "接口不存在")), 404)
        except FileNotFoundError:
            self.send_json(response(False, error=error_payload("NOT_FOUND", "资源不存在")), 404)
        except (ValueError, CalendarError) as exc:
            self.send_json(response(False, error=error_payload("INVALID_INPUT", str(exc))), 400)
        except Exception as exc:
            self.send_json(response(False, error=error_payload("INTERNAL_ERROR", str(exc))), 500)

    def do_POST(self) -> None:
        if self.path not in {"/api/query", "/api/batch"}:
            return self.send_json(response(False, error=error_payload("NOT_FOUND", "接口不存在")), 404)
        try:
            body = self.read_json()
            if self.path == "/api/query":
                if not isinstance(body, dict):
                    raise ValueError("单笔查询请求必须是JSON对象")
                request = {"submit_time": body.get("submit_time"), "fund_type": body.get("fund_type")}
                try:
                    result = engine.calculate(request["submit_time"], request["fund_type"])
                except (ValueError, CalendarError) as exc:
                    error = error_payload("INVALID_INPUT", str(exc))
                    store.add("query", request, error=error)
                    return self.send_json(response(False, error=error), 400)
                store.add("query", request, result=result)
                return self.send_json(response(True, result))
            if self.path == "/api/batch":
                items = body.get("items") if isinstance(body, dict) else body
                if not isinstance(items, list) or not items:
                    raise ValueError("批量查询items必须是非空数组")
                if len(items) > 100_000:
                    raise ValueError("单次批量查询最多100000条")
                results = []
                for item in items:
                    if not isinstance(item, dict):
                        results.append({"error": error_payload("INVALID_INPUT", "每条记录必须是JSON对象")})
                        continue
                    try:
                        calculated = engine.calculate(item.get("submit_time"), item.get("fund_type"))
                        if "apply_id" in item:
                            calculated["apply_id"] = item["apply_id"]
                        results.append(calculated)
                    except (ValueError, CalendarError) as exc:
                        results.append({**item, "error": error_payload("INVALID_INPUT", str(exc))})
                result = {"items": results, "count": len(results)}
                store.add("batch", {"count": len(items)}, result=result)
                return self.send_json(response(True, result))
            return self.send_json(response(False, error=error_payload("NOT_FOUND", "接口不存在")), 404)
        except ValueError as exc:
            error = error_payload("INVALID_INPUT", str(exc))
            if self.path == "/api/query":
                store.add("query", {}, error=error)
            elif self.path == "/api/batch":
                store.add("batch", {}, error=error)
            self.send_json(response(False, error=error), 400)
        except Exception as exc:
            self.send_json(response(False, error=error_payload("INTERNAL_ERROR", str(exc))), 500)


if __name__ == "__main__":
    print("交易日历服务启动: http://127.0.0.1:8000")
    ThreadingHTTPServer(("127.0.0.1", 8000), Handler).serve_forever()

