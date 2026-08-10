from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lead_hub.storage import Storage


class StorageTests(unittest.TestCase):
    def test_assignment_cycle_persists_across_deliveries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with Storage(Path(directory) / "state.sqlite3") as storage:
                cycle = ("2751", "2751", "2751", "2767", "2767")
                assigned = [
                    storage.get_or_create_delivery("praktikm", str(index), "Данные", index + 1, cycle).assigned_by_id
                    for index in range(1, 8)
                ]
                self.assertEqual(
                    assigned,
                    ["2751", "2751", "2751", "2767", "2767", "2751", "2751"],
                )
                again = storage.get_or_create_delivery("praktikm", "1", "Данные", 2, cycle)
                self.assertEqual(again.assigned_by_id, "2751")
                self.assertEqual(storage.assignment_position("praktikm"), 2)


if __name__ == "__main__":
    unittest.main()
