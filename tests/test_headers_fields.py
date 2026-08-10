from __future__ import annotations

import unittest
from pathlib import Path

from lead_hub.config import load_projects
from lead_hub.fields import build_bitrix_fields
from lead_hub.headers import column_to_a1, find_header, map_headers, normalize_header


ROOT = Path(__file__).resolve().parents[1]


class HeaderAndFieldTests(unittest.TestCase):
    def test_status_header_aliases_are_normalized(self) -> None:
        self.assertEqual(normalize_header("  Статус_Б24  "), "статус б24")
        self.assertEqual(find_header(["ID", "Статус Б24"], ["Статус_Б24"]), 1)

    def test_column_to_a1(self) -> None:
        self.assertEqual(column_to_a1(0), "A")
        self.assertEqual(column_to_a1(25), "Z")
        self.assertEqual(column_to_a1(26), "AA")

    def test_common_column_aliases(self) -> None:
        project = next(item for item in load_projects(ROOT / "config") if item.code == "metalwork")
        mapped = map_headers(["ID", "Дата", "Телефон", "Источник"], project.google.columns)
        self.assertEqual(mapped["source_id"], 0)
        self.assertEqual(mapped["phone"], 2)
        self.assertEqual(mapped["source_lead"], 3)

    def test_praktikm_payload(self) -> None:
        project = next(item for item in load_projects(ROOT / "config") if item.code == "praktikm")
        fields = build_bitrix_fields(
            project,
            {"source_id": "100", "phone": "+79990000000", "source_lead": "Конкурент"},
            "2751",
        )
        self.assertEqual(fields["SOURCE_ID"], "14")
        self.assertEqual(fields["STATUS_ID"], "UC_CT9E1O")
        self.assertEqual(fields["UTM_SOURCE"], "leadgenburo_dmp")
        self.assertEqual(fields["UTM_MEDIUM"], "Конкурент_+79990000000")
        self.assertEqual(fields["ASSIGNED_BY_ID"], "2751")


if __name__ == "__main__":
    unittest.main()
