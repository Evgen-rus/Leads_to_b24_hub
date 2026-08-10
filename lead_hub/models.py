from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SheetData:
    spreadsheet_id: str
    sheet_name: str
    headers: list[str]
    rows: list[list[str]]
    result_column_index: int
