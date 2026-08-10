from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from lead_hub.bitrix import CreatedLead
from lead_hub.config import ProjectConfig
from lead_hub.fields import build_bitrix_fields
from lead_hub.headers import map_headers, row_value
from lead_hub.models import SheetData
from lead_hub.storage import Delivery, Storage


logger = logging.getLogger(__name__)


class SheetsProtocol(Protocol):
    def read_project(self, project: ProjectConfig, *, create_result_column: bool) -> SheetData: ...
    def write_result(self, project: ProjectConfig, sheet: SheetData, row_number: int, value: str) -> None: ...


class BitrixProtocol(Protocol):
    def find_by_origin(self, project: ProjectConfig, origin_id: str) -> CreatedLead | None: ...
    def create(self, project: ProjectConfig, fields: dict[str, Any], origin_id: str) -> CreatedLead: ...


@dataclass
class RunStats:
    scanned: int = 0
    skipped_marked: int = 0
    skipped_invalid: int = 0
    planned: int = 0
    created: int = 0
    recovered: int = 0
    completed: int = 0
    failed: int = 0

    def as_storage(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "skipped_marked": self.skipped_marked,
            "created": self.created,
            "completed": self.completed,
            "failed": self.failed,
        }


class DeliveryEngine:
    def __init__(self, storage: Storage, sheets: SheetsProtocol, bitrix: BitrixProtocol):
        self.storage = storage
        self.sheets = sheets
        self.bitrix = bitrix

    @staticmethod
    def _context(row: list[str], header_map: dict[str, int]) -> dict[str, str]:
        return {key: row_value(row, index) for key, index in header_map.items()}

    def _complete_sheet_write(
        self,
        project: ProjectConfig,
        sheet: SheetData,
        delivery: Delivery,
        stats: RunStats,
    ) -> None:
        if not delivery.lead_url:
            raise RuntimeError("Created delivery has no lead URL")
        self.sheets.write_result(project, sheet, delivery.sheet_row, delivery.lead_url)
        self.storage.mark_completed(delivery)
        stats.completed += 1

    def run(self, project: ProjectConfig, *, dry_run: bool = False, limit: int | None = None) -> RunStats:
        stats = RunStats()
        sheet = self.sheets.read_project(project, create_result_column=not dry_run)
        header_map = map_headers(sheet.headers, project.google.columns)
        missing = {"source_id", "phone"} - set(header_map)
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

        assignment_cycle = project.assignment.resolved_cycle()
        dry_position = self.storage.assignment_position(project.code)
        handled = 0

        for row_number, row in enumerate(sheet.rows, start=2):
            stats.scanned += 1
            result = row_value(row, sheet.result_column_index)
            if result:
                stats.skipped_marked += 1
                continue

            context = self._context(row, header_map)
            source_id = context.get("source_id", "")
            phone = context.get("phone", "")
            if not source_id or not phone:
                stats.skipped_invalid += 1
                continue
            if limit is not None and handled >= limit:
                break
            handled += 1

            if dry_run:
                existing = self.storage.get_delivery(project.code, source_id)
                assigned = (
                    existing.assigned_by_id
                    if existing
                    else assignment_cycle[dry_position % len(assignment_cycle)]
                )
                if not existing:
                    dry_position += 1
                build_bitrix_fields(project, context, assigned)
                stats.planned += 1
                continue

            delivery = self.storage.get_or_create_delivery(
                project.code,
                source_id,
                sheet.sheet_name,
                row_number,
                assignment_cycle,
            )
            try:
                if delivery.state in {"created", "completed"} and delivery.lead_url:
                    self._complete_sheet_write(project, sheet, delivery, stats)
                    continue

                self.storage.mark_attempt(delivery)
                fields = build_bitrix_fields(project, context, delivery.assigned_by_id)
                origin_id = f"{project.code}:{source_id}"
                created = self.bitrix.find_by_origin(project, origin_id)
                if created is None:
                    created = self.bitrix.create(project, fields, origin_id)
                    stats.created += 1
                else:
                    stats.recovered += 1
                self.storage.mark_created(delivery, created.lead_id, created.lead_url)
                refreshed = self.storage.get_delivery(project.code, source_id)
                if refreshed is None:
                    raise RuntimeError("Delivery state disappeared after Bitrix create")
                self._complete_sheet_write(project, sheet, refreshed, stats)
            except Exception as error:
                current = self.storage.get_delivery(project.code, source_id) or delivery
                if current.state in {"created", "completed"}:
                    self.storage.record_error(current, str(error))
                else:
                    self.storage.mark_failed(current, str(error))
                stats.failed += 1
                logger.exception("Project %s source_id=%s failed", project.code, source_id)

        return stats
