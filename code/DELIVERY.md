# 交付说明

运行：

```powershell
cd code
py -3 app.py
```

浏览器打开 `http://127.0.0.1:8000/`。接口和业务规则见 `README.md` 与 `spec.md`。

验证：

```powershell
py -3 tests/test_engine.py
```

已验证 50 条样例全部通过，10 万条批量计算约 2 秒。SQLite 查询记录保存在运行时生成的 `queries.sqlite3` 中。
