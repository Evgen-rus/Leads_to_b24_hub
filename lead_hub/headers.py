from __future__ import annotations

import re
from typing import Iterable


def normalize_header(value: str) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[_\s]+", " ", text)
    return text


def find_header(headers: list[str], aliases: Iterable[str]) -> int | None:
    wanted = {normalize_header(alias) for alias in aliases}
    for index, header in enumerate(headers):
        if normalize_header(header) in wanted:
            return index
    return None


def map_headers(headers: list[str], aliases: dict[str, list[str]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for key, names in aliases.items():
        index = find_header(headers, names)
        if index is not None:
            result[key] = index
    return result


def row_value(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def column_to_a1(index: int) -> str:
    if index < 0:
        raise ValueError("Column index cannot be negative")
    value = index + 1
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result
