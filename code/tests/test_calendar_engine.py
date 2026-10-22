import csv
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from calendar_engine import CalendarError, Engine


ROOT = Path(__file__).parents[1]
DATA = ROOT / "data"


class CalendarEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = Engine(DATA)

    def assert_dates(self, submit_time, fund_type, expected):
        result = self.engine.calculate(submit_time, fund_type)
        self.assertEqual(
            (result["T_date"], result["confirm_date"], result["query_date"]),
            expected,
        )
        self.assertIn("reasons", result)
        self.assertGreaterEqual(len(result["reasons"]), 3)
        self.assertTrue(any("T" in reason for reason in result["reasons"]))
        self.assertTrue(any("确认" in reason for reason in result["reasons"]))
        self.assertTrue(any("查询" in reason for reason in result["reasons"]))

    def test_cutoff_1459_1500_1501(self):
        self.assert_dates("2025-01-22 14:59", "EQ", ("2025-01-22", "2025-01-23", "2025-01-24"))
        self.assert_dates("2025-01-22 15:00", "EQ", ("2025-01-22", "2025-01-23", "2025-01-24"))
        self.assert_dates("2025-01-22 15:01", "EQ", ("2025-01-23", "2025-01-24", "2025-01-27"))

    def test_weekend_and_holiday_submission(self):
        self.assert_dates("2025-01-24 15:30", "EQ", ("2025-01-27", "2025-02-05", "2025-02-06"))
        self.assert_dates("2025-01-25 09:30", "EQ", ("2025-01-27", "2025-02-05", "2025-02-06"))
        self.assert_dates("2025-01-26 15:00", "BD", ("2025-01-27", "2025-02-05", "2025-02-06"))
        self.assert_dates("2025-01-29 10:00", "EQ", ("2025-02-05", "2025-02-06", "2025-02-07"))
        self.assert_dates("2025-10-01 00:00", "EQ", ("2025-10-09", "2025-10-10", "2025-10-13"))

    def test_eq_bd_t_plus_one_excludes_t(self):
        for fund_type in ("EQ", "BD"):
            result = self.engine.calculate("2025-01-22 10:00", fund_type)
            self.assertEqual(result["T_date"], "2025-01-22")
            self.assertEqual(result["confirm_date"], "2025-01-23")

    def test_qdii_t_date_moves_to_common_day_when_us_is_closed(self):
        self.assert_dates("2025-01-20 10:00", "QDII", ("2025-01-21", "2025-01-23", "2025-01-24"))

    def test_qdii_confirmation_moves_to_common_day_when_us_is_closed(self):
        self.assert_dates("2024-02-08 15:00", "QDII", ("2024-02-08", "2024-02-20", "2024-02-21"))

    def test_year_start_and_year_end(self):
        self.assert_dates("2024-01-01 10:00", "EQ", ("2024-01-02", "2024-01-03", "2024-01-04"))
        self.assert_dates("2025-12-31 14:00", "EQ", ("2025-12-31", "2026-01-05", "2026-01-06"))
        self.assert_dates("2025-12-31 15:01", "QDII", ("2026-01-05", "2026-01-07", "2026-01-08"))

    def test_invalid_inputs(self):
        for submit_time in ("2025-01-22T15:00", "2025-01-22 15:00:01", "2025-01-22 24:00", "2025-01-22 10:60", "2025-02-30 10:00", None):
            with self.subTest(submit_time=submit_time), self.assertRaises(ValueError):
                self.engine.calculate(submit_time, "EQ")
        with self.assertRaises(ValueError):
            self.engine.calculate("2025-01-22 10:00", "FOF")
        with self.assertRaises(ValueError):
            self.engine.calculate("2023-12-31 10:00", "EQ")
        with self.assertRaises(ValueError):
            self.engine.calculate("2027-01-01 10:00", "EQ")
        with self.assertRaises(CalendarError):
            self.engine.calculate("2026-12-31 15:00", "EQ")

    def test_calendar_files_are_loaded_once_and_cached(self):
        self.assertEqual(len(self.engine.a.days), 1096)
        self.assertEqual(len(self.engine.u.days), 1096)
        self.assertEqual(len(self.engine.common_days), 703)
        original_a = self.engine.a.trading_days
        self.engine.calculate("2025-01-22 10:00", "EQ")
        self.assertIs(self.engine.a.trading_days, original_a)


class CalendarValidationTest(unittest.TestCase):
    def write_engine_files(self, mutate_a=None, mutate_u=None):
        temp = tempfile.TemporaryDirectory()
        base = Path(temp.name)
        shutil.copy2(DATA / "ashare_calendar_2024_2026.csv", base / "ashare_calendar_2024_2026.csv")
        shutil.copy2(DATA / "us_calendar_2024_2026.csv", base / "us_calendar_2024_2026.csv")
        shutil.copy2(ROOT / "config.json", base / "config.json")
        for name, mutate in (("ashare_calendar_2024_2026.csv", mutate_a), ("us_calendar_2024_2026.csv", mutate_u)):
            if mutate:
                mutate(base / name)
        return temp, base

    def assert_startup_fails(self, mutate_a=None, mutate_u=None, remove=None):
        temp, base = self.write_engine_files(mutate_a, mutate_u)
        try:
            if remove:
                (base / remove).unlink()
            with self.assertRaises(CalendarError):
                Engine(base)
        finally:
            temp.cleanup()

    def test_missing_calendar_is_rejected(self):
        self.assert_startup_fails(remove="ashare_calendar_2024_2026.csv")

    def test_bad_header_is_rejected(self):
        def mutate(path):
            path.write_text("day,flag\n2024-01-01,0\n", encoding="utf-8")
        self.assert_startup_fails(mutate_a=mutate)

    def test_duplicate_date_is_rejected(self):
        def mutate(path):
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            rows.insert(2, rows[1])
            path.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")
        self.assert_startup_fails(mutate_a=mutate)

    def test_date_gap_is_rejected(self):
        def mutate(path):
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            del rows[100]
            path.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")
        self.assert_startup_fails(mutate_a=mutate)

    def test_incomplete_range_is_rejected(self):
        def mutate(path):
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            del rows[-1]
            path.write_text("\n".join(",".join(row) for row in rows) + "\n", encoding="utf-8")
        self.assert_startup_fails(mutate_a=mutate)


if __name__ == "__main__":
    unittest.main()
