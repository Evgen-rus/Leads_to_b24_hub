from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Delivery:
    project_code: str
    source_id: str
    sheet_name: str
    sheet_row: int
    assigned_by_id: str
    state: str
    bitrix_lead_id: str | None
    lead_url: str | None
    attempts: int
    last_error: str | None


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS deliveries (
                project_code TEXT NOT NULL,
                source_id TEXT NOT NULL,
                sheet_name TEXT NOT NULL,
                sheet_row INTEGER NOT NULL,
                assigned_by_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK(state IN ('pending', 'created', 'completed', 'failed')),
                bitrix_lead_id TEXT,
                lead_url TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(project_code, source_id)
            );

            CREATE TABLE IF NOT EXISTS assignment_state (
                project_code TEXT PRIMARY KEY,
                next_position INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_code TEXT NOT NULL,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                scanned INTEGER NOT NULL DEFAULT 0,
                skipped_marked INTEGER NOT NULL DEFAULT 0,
                created INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                failed INTEGER NOT NULL DEFAULT 0,
                error TEXT
            );
            """
        )
        self.connection.commit()

    @staticmethod
    def _delivery(row: sqlite3.Row | None) -> Delivery | None:
        if row is None:
            return None
        return Delivery(
            project_code=row["project_code"],
            source_id=row["source_id"],
            sheet_name=row["sheet_name"],
            sheet_row=row["sheet_row"],
            assigned_by_id=row["assigned_by_id"],
            state=row["state"],
            bitrix_lead_id=row["bitrix_lead_id"],
            lead_url=row["lead_url"],
            attempts=row["attempts"],
            last_error=row["last_error"],
        )

    def get_delivery(self, project_code: str, source_id: str) -> Delivery | None:
        row = self.connection.execute(
            "SELECT * FROM deliveries WHERE project_code = ? AND source_id = ?",
            (project_code, source_id),
        ).fetchone()
        return self._delivery(row)

    def assignment_position(self, project_code: str) -> int:
        row = self.connection.execute(
            "SELECT next_position FROM assignment_state WHERE project_code = ?",
            (project_code,),
        ).fetchone()
        return int(row[0]) if row else 0

    def get_or_create_delivery(
        self,
        project_code: str,
        source_id: str,
        sheet_name: str,
        sheet_row: int,
        assignment_cycle: tuple[str, ...],
    ) -> Delivery:
        existing = self.get_delivery(project_code, source_id)
        if existing:
            if existing.sheet_name != sheet_name or existing.sheet_row != sheet_row:
                self.connection.execute(
                    """
                    UPDATE deliveries
                    SET sheet_name = ?, sheet_row = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE project_code = ? AND source_id = ?
                    """,
                    (sheet_name, sheet_row, project_code, source_id),
                )
                self.connection.commit()
                return self.get_delivery(project_code, source_id) or existing
            return existing

        with self.connection:
            row = self.connection.execute(
                "SELECT next_position FROM assignment_state WHERE project_code = ?",
                (project_code,),
            ).fetchone()
            position = int(row[0]) if row else 0
            assigned_by_id = assignment_cycle[position % len(assignment_cycle)]
            self.connection.execute(
                """
                INSERT INTO deliveries(
                    project_code, source_id, sheet_name, sheet_row, assigned_by_id, state
                ) VALUES (?, ?, ?, ?, ?, 'pending')
                """,
                (project_code, source_id, sheet_name, sheet_row, assigned_by_id),
            )
            self.connection.execute(
                """
                INSERT INTO assignment_state(project_code, next_position)
                VALUES (?, ?)
                ON CONFLICT(project_code) DO UPDATE SET
                    next_position = excluded.next_position,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (project_code, (position + 1) % len(assignment_cycle)),
            )
        delivery = self.get_delivery(project_code, source_id)
        if delivery is None:
            raise RuntimeError("Failed to create delivery state")
        return delivery

    def mark_attempt(self, delivery: Delivery) -> None:
        self.connection.execute(
            """
            UPDATE deliveries
            SET attempts = attempts + 1, last_error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE project_code = ? AND source_id = ?
            """,
            (delivery.project_code, delivery.source_id),
        )
        self.connection.commit()

    def mark_created(self, delivery: Delivery, lead_id: str, lead_url: str) -> None:
        self.connection.execute(
            """
            UPDATE deliveries
            SET state = 'created', bitrix_lead_id = ?, lead_url = ?, last_error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE project_code = ? AND source_id = ?
            """,
            (lead_id, lead_url, delivery.project_code, delivery.source_id),
        )
        self.connection.commit()

    def mark_completed(self, delivery: Delivery) -> None:
        self.connection.execute(
            """
            UPDATE deliveries
            SET state = 'completed', last_error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE project_code = ? AND source_id = ?
            """,
            (delivery.project_code, delivery.source_id),
        )
        self.connection.commit()

    def mark_failed(self, delivery: Delivery, error: str) -> None:
        self.connection.execute(
            """
            UPDATE deliveries
            SET state = 'failed', last_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE project_code = ? AND source_id = ?
            """,
            (error[:1000], delivery.project_code, delivery.source_id),
        )
        self.connection.commit()

    def record_error(self, delivery: Delivery, error: str) -> None:
        self.connection.execute(
            """
            UPDATE deliveries
            SET last_error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE project_code = ? AND source_id = ?
            """,
            (error[:1000], delivery.project_code, delivery.source_id),
        )
        self.connection.commit()

    def start_run(self, project_code: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO runs(project_code) VALUES (?)",
            (project_code,),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_run(self, run_id: int, *, status: str, stats: dict[str, int], error: str | None = None) -> None:
        self.connection.execute(
            """
            UPDATE runs
            SET finished_at = CURRENT_TIMESTAMP, status = ?, scanned = ?, skipped_marked = ?,
                created = ?, completed = ?, failed = ?, error = ?
            WHERE id = ?
            """,
            (
                status,
                stats.get("scanned", 0),
                stats.get("skipped_marked", 0),
                stats.get("created", 0),
                stats.get("completed", 0),
                stats.get("failed", 0),
                error[:1000] if error else None,
                run_id,
            ),
        )
        self.connection.commit()
