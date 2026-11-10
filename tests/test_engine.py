from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lead_hub.bitrix import CreatedLead
from lead_hub.config import load_projects
from lead_hub.engine import DeliveryEngine
from lead_hub.models import SheetData
from lead_hub.storage import Storage


ROOT = Path(__file__).resolve().parents[1]


class FakeSheets:
    def __init__(self, rows: list[list[str]], *, fail_writes: int = 0):
        self.data = SheetData(
            spreadsheet_id="sheet",
            sheet_name="Данные",
            headers=["ID", "Дата", "Номера", "Канал", "Источник", "Статус_Б24"],
            rows=rows,
            result_column_index=5,
        )
        self.fail_writes = fail_writes
        self.writes: list[tuple[int, str]] = []
        self.create_flags: list[bool] = []

    def read_project(self, project, *, create_result_column: bool):
        self.create_flags.append(create_result_column)
        return self.data

    def write_result(self, project, sheet, row_number: int, value: str):
        if self.fail_writes:
            self.fail_writes -= 1
            raise RuntimeError("temporary Google failure")
        self.writes.append((row_number, value))


class FakeBitrix:
    def __init__(self, recovered: dict[str, CreatedLead] | None = None):
        self.recovered = recovered or {}
        self.find_calls: list[str] = []
        self.create_calls: list[tuple[dict, str]] = []

    def find_by_origin(self, project, origin_id: str):
        self.find_calls.append(origin_id)
        return self.recovered.get(origin_id)

    def create(self, project, fields, origin_id: str):
        self.create_calls.append((fields, origin_id))
        lead_id = str(1000 + len(self.create_calls))
        return CreatedLead(lead_id, f"https://example.bitrix24.ru/crm/{project.bitrix.entity}/details/{lead_id}/")


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = next(
            item for item in load_projects(ROOT / "config") if item.code == "praktikm"
        )

    def test_blank_rows_are_sent_and_marked_rows_are_skipped(self) -> None:
        rows = [
            ["1", "2020-01-01", "+70000000001", "", "A", "отправили вручную"],
            ["2", "2020-01-02", "+70000000002", "", "B", ""],
            ["3", "", "", "", "C", ""],
        ]
        with tempfile.TemporaryDirectory() as directory:
            sheets = FakeSheets(rows)
            bitrix = FakeBitrix()
            with Storage(Path(directory) / "state.sqlite3") as storage:
                stats = DeliveryEngine(storage, sheets, bitrix).run(self.project)
                delivery = storage.get_delivery("praktikm", "2")
            self.assertEqual(stats.skipped_marked, 1)
            self.assertEqual(stats.skipped_invalid, 1)
            self.assertEqual(stats.created, 1)
            self.assertEqual(stats.completed, 1)
            self.assertEqual(len(bitrix.create_calls), 1)
            self.assertEqual(sheets.writes[0][0], 3)
            self.assertEqual(delivery.state, "completed")

    def test_completed_delivery_restores_deleted_sheet_link_without_duplicate(self) -> None:
        rows = [["9", "2019-01-01", "+70000000009", "", "A", ""]]
        with tempfile.TemporaryDirectory() as directory:
            sheets = FakeSheets(rows)
            bitrix = FakeBitrix()
            with Storage(Path(directory) / "state.sqlite3") as storage:
                engine = DeliveryEngine(storage, sheets, bitrix)
                engine.run(self.project)
                engine.run(self.project)
            self.assertEqual(len(bitrix.create_calls), 1)
            self.assertEqual(len(sheets.writes), 2)

    def test_bitrix_origin_recovery_avoids_duplicate(self) -> None:
        rows = [["7", "2021-01-01", "+70000000007", "", "A", ""]]
        recovered = {
            "praktikm:7": CreatedLead(
                "777", "https://example.bitrix24.ru/crm/lead/details/777/", recovered=True
            )
        }
        with tempfile.TemporaryDirectory() as directory:
            sheets = FakeSheets(rows)
            bitrix = FakeBitrix(recovered)
            with Storage(Path(directory) / "state.sqlite3") as storage:
                stats = DeliveryEngine(storage, sheets, bitrix).run(self.project)
            self.assertEqual(stats.recovered, 1)
            self.assertEqual(bitrix.create_calls, [])
            self.assertEqual(len(sheets.writes), 1)

    def test_google_failure_retries_only_sheet_write(self) -> None:
        rows = [["8", "2021-01-01", "+70000000008", "", "A", ""]]
        with tempfile.TemporaryDirectory() as directory:
            sheets = FakeSheets(rows, fail_writes=1)
            bitrix = FakeBitrix()
            with Storage(Path(directory) / "state.sqlite3") as storage:
                engine = DeliveryEngine(storage, sheets, bitrix)
                with self.assertLogs("lead_hub.engine", level="ERROR"):
                    first = engine.run(self.project)
                second = engine.run(self.project)
                delivery = storage.get_delivery("praktikm", "8")
            self.assertEqual(first.failed, 1)
            self.assertEqual(second.completed, 1)
            self.assertEqual(len(bitrix.create_calls), 1)
            self.assertEqual(delivery.state, "completed")

    def test_dry_run_has_no_external_or_delivery_writes(self) -> None:
        rows = [["10", "2021-01-01", "+70000000010", "", "A", ""]]
        with tempfile.TemporaryDirectory() as directory:
            sheets = FakeSheets(rows)
            bitrix = FakeBitrix()
            with Storage(Path(directory) / "state.sqlite3") as storage:
                stats = DeliveryEngine(storage, sheets, bitrix).run(self.project, dry_run=True)
                delivery = storage.get_delivery("praktikm", "10")
            self.assertEqual(stats.planned, 1)
            self.assertEqual(bitrix.find_calls, [])
            self.assertEqual(bitrix.create_calls, [])
            self.assertEqual(sheets.writes, [])
            self.assertEqual(sheets.create_flags, [False])
            self.assertIsNone(delivery)


if __name__ == "__main__":
    unittest.main()
