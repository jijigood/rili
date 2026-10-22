from __future__ import annotations

import csv
import json
import re
from bisect import bisect_left, bisect_right
from datetime import date, datetime
from pathlib import Path
from typing import Any


class CalendarError(Exception):
    """Raised when calendar data cannot support a calculation."""


class TradingCalendar:
    START = date(2024, 1, 1)
    END = date(2026, 12, 31)

    def __init__(self, path: str | Path, name: str):
        self.name = name
        self.path = Path(path)
        self.days: list[date] = []
        self.trading_days: list[date] = []
        self.trading: set[date] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            raise CalendarError(f"缺少{self.name}日历文件: {self.path}")
        try:
            with self.path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream, strict=True)
                if reader.fieldnames != ["date", "is_trading_day"]:
                    raise CalendarError(f"{self.name}日历表头错误")
                rows = list(reader)
        except UnicodeDecodeError as exc:
            raise CalendarError(f"{self.name}日历文件编码错误") from exc
        except OSError as exc:
            raise CalendarError(f"{self.name}日历文件读取错误") from exc
        except csv.Error as exc:
            raise CalendarError(f"{self.name}日历CSV格式错误") from exc
        if not rows:
            raise CalendarError(f"{self.name}日历为空")
        parsed: list[tuple[date, bool]] = []
        seen: set[date] = set()
        for row in rows:
            if set(row) != {"date", "is_trading_day"} or row.get(None) is not None:
                raise CalendarError(f"{self.name}日历行字段错误")
            raw_date = (row.get("date") or "").strip()
            flag = (row.get("is_trading_day") or "").strip()
            try:
                current = date.fromisoformat(raw_date)
            except ValueError as exc:
                raise CalendarError(f"{self.name}日历日期格式错误: {raw_date}") from exc
            if current in seen:
                raise CalendarError(f"{self.name}日历存在重复日期: {current}")
            if flag not in {"0", "1"}:
                raise CalendarError(f"{self.name}日历交易标记错误: {current}")
            seen.add(current)
            parsed.append((current, flag == "1"))
        parsed.sort(key=lambda item: item[0])
        start, end = parsed[0][0], parsed[-1][0]
        if len(parsed) != (end - start).days + 1:
            raise CalendarError(f"{self.name}日历存在日期空洞")
        if start != self.START or end != self.END:
            raise CalendarError(f"{self.name}日历范围必须为2024-01-01至2026-12-31")
        self.days = [current for current, _ in parsed]
        self.trading_days = [current for current, enabled in parsed if enabled]
        self.trading = set(self.trading_days)

    def is_trading(self, current: date) -> bool:
        return current in self.trading

    def next_trading(self, current: date, include: bool = False) -> date:
        index = bisect_left(self.trading_days, current) if include else bisect_right(self.trading_days, current)
        if index >= len(self.trading_days):
            raise CalendarError(f"{self.name}日历范围不足以计算 {current}")
        return self.trading_days[index]

    def nth_after(self, current: date, n: int) -> date:
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise CalendarError("交易日偏移量必须为正整数")
        index = bisect_right(self.trading_days, current) + n - 1
        if index >= len(self.trading_days):
            raise CalendarError(f"{self.name}日历范围不足以计算 {current}之后第{n}个交易日")
        return self.trading_days[index]


class Engine:
    CUTOFF = (15, 0)
    _TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def __init__(self, base: str | Path):
        base = Path(base)
        self.a = TradingCalendar(base / "ashare_calendar_2024_2026.csv", "境内")
        self.u = TradingCalendar(base / "us_calendar_2024_2026.csv", "美股")
        config_path = base / "config.json"
        if not config_path.is_file():
            config_path = base.parent / "config.json"
        if not config_path.is_file():
            raise CalendarError(f"缺少配置文件: {config_path}")
        try:
            with config_path.open(encoding="utf-8-sig") as stream:
                self.config = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            raise CalendarError(f"配置文件格式错误: {config_path}") from exc
        if not isinstance(self.config, dict) or not self.config:
            raise CalendarError("基金配置不能为空")
        for fund_type, cfg in self.config.items():
            if not isinstance(fund_type, str) or not isinstance(cfg, dict):
                raise CalendarError("基金配置格式错误")
            offset = cfg.get("confirm_offset")
            if not isinstance(offset, int) or isinstance(offset, bool) or offset < 1:
                raise CalendarError(f"{fund_type}确认日偏移量必须为正整数")
            if cfg.get("market", "A_SHARE") not in {"A_SHARE", "A_SHARE_US"}:
                raise CalendarError(f"{fund_type}市场配置错误")
        self.common_days = [d for d in self.a.trading_days if self.u.is_trading(d)]

    def _next_common(self, current: date, include: bool = False) -> date:
        index = bisect_left(self.common_days, current) if include else bisect_right(self.common_days, current)
        if index >= len(self.common_days):
            raise CalendarError(f"共同交易日历范围不足以计算 {current}")
        return self.common_days[index]

    def _candidate_a_share_t(self, submitted: datetime) -> date:
        current = submitted.date()
        if self.a.is_trading(current) and (submitted.hour, submitted.minute) <= self.CUTOFF:
            return current
        return self.a.next_trading(current, include=not self.a.is_trading(current))

    def calculate(self, submit_time: str, fund_type: str) -> dict[str, Any]:
        if not isinstance(submit_time, str) or not self._TIME_RE.fullmatch(submit_time):
            raise ValueError("提交时间格式必须为 YYYY-MM-DD HH:MM")
        try:
            submitted = datetime.strptime(submit_time, "%Y-%m-%d %H:%M")
        except ValueError as exc:
            raise ValueError("提交时间格式必须为 YYYY-MM-DD HH:MM") from exc
        if not isinstance(fund_type, str) or fund_type not in self.config:
            supported = "、".join(self.config.keys())
            raise ValueError(f"未知基金类型，支持 {supported}")
        if not (self.a.START <= submitted.date() <= self.a.END):
            raise ValueError("提交日期超出日历覆盖范围")
        cfg = self.config[fund_type]
        candidate = self._candidate_a_share_t(submitted)
        reasons: list[str] = []
        if not self.a.is_trading(submitted.date()):
            reasons.append(f"{submitted.date()}为非交易日，顺延至下一个境内交易日")
        elif (submitted.hour, submitted.minute) > self.CUTOFF:
            reasons.append("15:00后提交，顺延至下一交易日")
        else:
            reasons.append("交易日15:00（含）前提交，当日为T日")

        if cfg.get("market", "A_SHARE") == "A_SHARE_US":
            t_date = self._next_common(candidate, include=True)
            if t_date != candidate:
                reasons.append("QDII基础T日美股休市，顺延至下一个共同交易日")
            a_confirm = self.a.nth_after(t_date, cfg["confirm_offset"])
            confirm_date = self._next_common(a_confirm, include=True)
            if confirm_date != a_confirm:
                reasons.append("QDII确认日美股休市，顺延至下一个共同交易日")
        else:
            t_date = candidate
            confirm_date = self.a.nth_after(t_date, cfg["confirm_offset"])
        if cfg.get("market", "A_SHARE") == "A_SHARE_US":
            reasons.append(f"QDII按T+{cfg['confirm_offset']}计算确认日，并要求境内与美股共同交易日")
        else:
            reasons.append(f"{fund_type}按T+{cfg['confirm_offset']}计算确认日")
        query_date = self.a.next_trading(confirm_date)
        reasons.append("查询日取确认日之后的第一个境内交易日")
        return {
            "submit_time": submit_time,
            "fund_type": fund_type,
            "T_date": t_date.isoformat(),
            "confirm_date": confirm_date.isoformat(),
            "query_date": query_date.isoformat(),
            "reasons": reasons,
        }
