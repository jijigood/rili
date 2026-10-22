from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class Storage:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS queries(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                request_json TEXT NOT NULL,
                result_json TEXT,
                error_json TEXT
            )
            """
        )
        self.db.commit()

    def add(self, kind: str, request: Any, result: Any = None, error: Any = None) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO queries(created_at,kind,request_json,result_json,error_json) VALUES(?,?,?,?,?)",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    kind,
                    json.dumps(request, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    json.dumps(error, ensure_ascii=False) if error is not None else None,
                ),
            )
            self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit必须是1到500之间的整数")
        with self._lock:
            rows = self.db.execute(
                "SELECT id,created_at,kind,request_json,result_json,error_json "
                "FROM queries ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "id": row[0],
                "created_at": row[1],
                "kind": row[2],
                "request": json.loads(row[3]),
                "result": json.loads(row[4]) if row[4] is not None else None,
                "error": json.loads(row[5]) if row[5] is not None else None,
            }
            for row in rows
        ]

