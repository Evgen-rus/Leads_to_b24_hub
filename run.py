from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from lead_hub.bitrix import BitrixGateway
from lead_hub.config import ConfigError, ProjectConfig, load_projects, missing_environment
from lead_hub.engine import DeliveryEngine
from lead_hub.google_sheets import GoogleSheetsGateway
from lead_hub.logging_setup import configure_logging
from lead_hub.storage import Storage


ROOT = Path(__file__).resolve().parent
logger = logging.getLogger("lead_hub.run")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Google Sheets -> Bitrix24 lead delivery worker")
    parser.add_argument("--project", action="append", help="Project code; can be repeated")
    parser.add_argument("--dry-run", action="store_true", help="Read and validate without writes")
    parser.add_argument("--limit", type=int, help="Maximum blank rows per project")
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Allow explicitly selected disabled projects",
    )
    return parser.parse_args()


def select_projects(projects: list[ProjectConfig], args: argparse.Namespace) -> list[ProjectConfig]:
    requested = set(args.project or [])
    known = {project.code for project in projects}
    unknown = requested - known
    if unknown:
        raise ConfigError(f"Unknown projects: {', '.join(sorted(unknown))}")
    selected = [project for project in projects if not requested or project.code in requested]
    return [
        project
        for project in selected
        if project.enabled or (requested and args.include_disabled)
    ]


def main() -> int:
    args = parse_args()
    if args.limit is not None and args.limit <= 0:
        raise ConfigError("--limit must be positive")

    load_dotenv(ROOT / ".env")
    configure_logging(ROOT / "logs")
    projects = select_projects(load_projects(ROOT / "config"), args)
    if not projects:
        logger.info("No enabled projects selected")
        return 0

    db_path = Path(os.getenv("LEAD_HUB_DB", str(ROOT / "data" / "lead_hub.sqlite3")))
    failed = False
    with Storage(db_path) as storage:
        engine = DeliveryEngine(storage, GoogleSheetsGateway(), BitrixGateway())
        for project in projects:
            missing = missing_environment(project)
            if missing:
                failed = True
                logger.error("Project %s missing environment: %s", project.code, ", ".join(missing))
                continue

            run_id = None if args.dry_run else storage.start_run(project.code)
            try:
                stats = engine.run(project, dry_run=args.dry_run, limit=args.limit)
                if run_id is not None:
                    status = "partial" if stats.failed else "success"
                    storage.finish_run(run_id, status=status, stats=stats.as_storage())
                logger.info(
                    "Project %s: scanned=%s marked=%s invalid=%s planned=%s created=%s "
                    "recovered=%s completed=%s failed=%s",
                    project.code,
                    stats.scanned,
                    stats.skipped_marked,
                    stats.skipped_invalid,
                    stats.planned,
                    stats.created,
                    stats.recovered,
                    stats.completed,
                    stats.failed,
                )
                failed = failed or stats.failed > 0
            except Exception as error:
                failed = True
                if run_id is not None:
                    storage.finish_run(
                        run_id,
                        status="failed",
                        stats={"failed": 1},
                        error=str(error),
                    )
                logger.exception("Project %s run failed", project.code)
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        sys.exit(2)
