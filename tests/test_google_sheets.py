from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from lead_hub.config import load_projects

try:
    from lead_hub.google_sheets import GoogleSheetsGateway
except ModuleNotFoundError:  # Allows core tests before optional runtime deps are installed.
    GoogleSheetsGateway = None


ROOT = Path(__file__).resolve().parents[1]


class FakeRequest:
    def __init__(self, result):
        self.result = result

    def execute(self):
        return self.result


class FakeValues:
    def __init__(self):
        self.updates = []

    def get(self, **kwargs):
        return FakeRequest(
            {
                "values": [
                    ["ID", "Дата", "Номера", "Канал", "Источник"],
                    ["1", "2026-08-10", "+70000000000", "", "Конкурент"],
                ]
            }
        )

    def update(self, **kwargs):
        self.updates.append(kwargs)
        return FakeRequest({"updatedCells": 1})


class FakeSpreadsheets:
    def __init__(self):
        self.value_api = FakeValues()

    def values(self):
        return self.value_api


class FakeService:
    def __init__(self):
        self.spreadsheet_api = FakeSpreadsheets()

    def spreadsheets(self):
        return self.spreadsheet_api


@unittest.skipIf(GoogleSheetsGateway is None, "Google runtime dependencies are not installed")
class GoogleSheetsTests(unittest.TestCase):
    def setUp(self):
        self.project = next(
            item for item in load_projects(ROOT / "config") if item.code == "praktikm"
        )

    def _gateway(self, service):
        gateway = GoogleSheetsGateway()
        gateway._service = lambda credentials_env: service
        gateway._sheet_name = lambda service_arg, spreadsheet_id, project: "Данные"
        return gateway

    def test_missing_result_header_is_created_on_the_right(self):
        service = FakeService()
        with patch.dict(os.environ, {"PRAKTIKM_GOOGLE_SHEET_ID": "sheet-id"}):
            data = self._gateway(service).read_project(
                self.project,
                create_result_column=True,
            )
        self.assertEqual(data.result_column_index, 5)
        self.assertEqual(data.headers[-1], "Статус_Б24")
        update = service.spreadsheet_api.value_api.updates[0]
        self.assertEqual(update["range"], "'Данные'!F1")
        self.assertEqual(update["body"], {"values": [["Статус_Б24"]]})

    def test_dry_run_does_not_create_missing_result_header(self):
        service = FakeService()
        with patch.dict(os.environ, {"PRAKTIKM_GOOGLE_SHEET_ID": "sheet-id"}):
            data = self._gateway(service).read_project(
                self.project,
                create_result_column=False,
            )
        self.assertEqual(data.result_column_index, 5)
        self.assertEqual(service.spreadsheet_api.value_api.updates, [])


if __name__ == "__main__":
    unittest.main()
